"""Builder to help resolve cmake state"""

import hashlib
import importlib
import os
import re
import shutil
import subprocess
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from cppython.core.schema import CorePluginData
from cppython.plugins.cmake.schema import CMakeConfiguration, CMakeData, CMakeToolOrigin, EffectiveCMakeTool

_CMAKE_VERSION = re.compile(r"^cmake version (\S+)", re.MULTILINE)
_VERSION_TIMEOUT_SECONDS = 15
MISSING_CMAKE_GUIDANCE = (
    "Install or sync CPPython's 'cmake' extra, or set CMAKE_BINARY to a compatible CMake executable. "
    "CMake is not resolved from PATH."
)


class CMakeToolResolutionError(ValueError):
    """Raised when the selected CMake executable is invalid or incompatible."""


def _packaged_cmake() -> Path | None:
    """Return the CMake shipped by the ``cmake`` PyPI package, if installed."""
    if find_spec("cmake") is None:
        return None
    cmake = importlib.import_module("cmake")
    binary = shutil.which("cmake", path=cmake.CMAKE_BIN_DIR)
    return Path(binary) if binary else None


def _inspect_cmake_binary(binary: Path, origin: CMakeToolOrigin) -> EffectiveCMakeTool:
    """Validate a CMake executable and capture its identity."""
    if not binary.is_file():
        raise CMakeToolResolutionError(f"Selected CMake executable does not exist or is not a file: {binary}")

    binary = binary.resolve()
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CMakeToolResolutionError(f"Could not query selected CMake executable {binary}: {error}") from error

    if result.returncode != 0:
        raise CMakeToolResolutionError(
            f"Selected CMake executable failed with exit code {result.returncode}: {binary}\n{result.stderr}"
        )

    match = _CMAKE_VERSION.search(result.stdout)
    if match is None:
        raise CMakeToolResolutionError(f"Selected executable did not report a CMake version: {binary}")

    try:
        version = Version(match.group(1))
    except InvalidVersion as error:
        raise CMakeToolResolutionError(f"Selected CMake executable reported an invalid version: {binary}") from error

    try:
        fingerprint = hashlib.sha256(binary.read_bytes()).hexdigest()
    except OSError as error:
        raise CMakeToolResolutionError(f"Could not fingerprint selected CMake executable {binary}: {error}") from error

    return EffectiveCMakeTool(
        executable=binary,
        version=str(version),
        origin=origin,
        capabilities=("import-std-without-experimental-gate",) if version >= Version("4.5.0rc1") else (),
        fingerprint=fingerprint,
    )


def verify_cmake_tool(tool: EffectiveCMakeTool) -> None:
    """Ensure the selected executable has not changed since it was inspected."""
    current = _inspect_cmake_binary(tool.executable, tool.origin)
    if current != tool:
        raise CMakeToolResolutionError(
            f"Selected CMake executable changed after inspection: {tool.executable}. Re-run inspection before using it."
        )


def resolve_cmake_tool(
    configured_path: Path | None,
    project_root: Path | None = None,
    minimum_version: str | None = None,
) -> EffectiveCMakeTool | None:
    """Resolve, validate, and fingerprint the effective CMake executable.

    Resolution order:
    1. CMAKE_BINARY environment variable, if it agrees with configured_path
    2. Configured path from cmake_binary setting
    3. cmake installed with CPPython's ``cmake`` extra

    ``PATH`` is never consulted. Every selection fails closed when invalid or
    below the project minimum.

    Args:
        configured_path: The cmake_binary path from configuration, if any
        project_root: Root used to resolve relative explicit selections.
        minimum_version: Minimum supported version required by the project.

    Returns:
        The validated CMake identity, or None when no executable is available.
    """
    required_version = _parse_minimum_version(minimum_version)
    env_binary = os.environ.get("CMAKE_BINARY")
    if tool := _resolve_explicit_cmake_tool(
        env_binary,
        configured_path,
        project_root,
        required_version,
    ):
        return tool

    return _resolve_packaged_cmake_tool(required_version)


def _parse_minimum_version(minimum_version: str | None) -> Version | None:
    """Parse the configured minimum CMake version."""
    if minimum_version is None:
        return None
    try:
        return Version(minimum_version)
    except InvalidVersion as error:
        raise CMakeToolResolutionError(
            f"Invalid minimum CMake version: {minimum_version}"
        ) from error


def _resolve_explicit_cmake_tool(
    env_binary: str | None,
    configured_path: Path | None,
    project_root: Path | None,
    required_version: Version | None,
) -> EffectiveCMakeTool | None:
    """Resolve explicit environment/configuration selections without fallback."""
    if env_binary and configured_path is not None:
        env_path = _resolved_selection_path(Path(env_binary), project_root)
        configured_selection = _resolved_selection_path(configured_path, project_root)
        if env_path != configured_selection:
            raise CMakeToolResolutionError(
                f"CMAKE_BINARY selects {env_path}, but cmake_binary config selects {configured_selection}. "
                "Make both selections agree or remove one."
            )

    if env_binary:
        selected = Path(env_binary)
        origin: CMakeToolOrigin = "environment"
    elif configured_path is not None:
        selected = configured_path
        origin = "configuration"
    else:
        return None

    if not selected.is_absolute() and project_root is not None:
        selected = project_root / selected
    tool = _inspect_cmake_binary(selected.expanduser(), origin)
    _require_minimum_version(tool, required_version)
    return tool


def _resolve_packaged_cmake_tool(
    required_version: Version | None,
) -> EffectiveCMakeTool | None:
    """Use the CMake installed with CPPython's ``cmake`` extra."""
    packaged = _packaged_cmake()
    if packaged is None:
        if required_version is None:
            return None
        raise CMakeToolResolutionError(
            f"CMake {required_version} or newer is required, but the packaged CMake is not installed. "
            f"{MISSING_CMAKE_GUIDANCE}"
        )

    try:
        tool = _inspect_cmake_binary(packaged.expanduser().resolve(), "packaged")
        _require_minimum_version(tool, required_version)
    except CMakeToolResolutionError as error:
        raise CMakeToolResolutionError(f"{error}\n{MISSING_CMAKE_GUIDANCE}") from error
    return tool


def _require_minimum_version(
    tool: EffectiveCMakeTool,
    required_version: Version | None,
) -> None:
    """Reject a selected CMake executable that does not meet the project minimum."""
    if required_version is not None and Version(tool.version) < required_version:
        raise CMakeToolResolutionError(
            f"CMake {required_version} or newer is required, but {tool.version} was selected from "
            f"{tool.origin}: {tool.executable}"
        )


def _resolved_selection_path(path: Path, project_root: Path | None) -> str:
    """Normalize an explicit selection for conflict checks."""
    path = path.expanduser()
    if not path.is_absolute() and project_root is not None:
        path = project_root / path
    return os.path.normcase(str(path.resolve(strict=False)))


def resolve_cmake_binary(
    configured_path: Path | None,
    project_root: Path | None = None,
    minimum_version: str | None = None,
) -> Path | None:
    """Resolve the effective CMake executable path."""
    tool = resolve_cmake_tool(configured_path, project_root, minimum_version)
    return tool.executable if tool else None


def resolve_cmake_data(data: dict[str, Any], core_data: CorePluginData) -> CMakeData:
    """Resolves the input data table from defaults to requirements

    Args:
        data: The input table
        core_data: The core data to help with the resolve

    Returns:
        The resolved data
    """
    parsed_data = CMakeConfiguration(**data)

    root_directory = core_data.project_data.project_root.absolute()

    modified_preset_file = parsed_data.preset_file
    if not modified_preset_file.is_absolute():
        modified_preset_file = root_directory / modified_preset_file

    cmake_tool = resolve_cmake_tool(
        parsed_data.cmake_binary,
        root_directory,
        parsed_data.minimum_version,
    )

    return CMakeData(
        preset_file=modified_preset_file,
        configuration_name=parsed_data.configuration_name,
        cmake_binary=cmake_tool.executable if cmake_tool else None,
        cmake_tool=cmake_tool,
        minimum_version=parsed_data.minimum_version,
        default_configuration=parsed_data.default_configuration,
    )
