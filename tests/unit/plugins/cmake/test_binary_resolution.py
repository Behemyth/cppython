"""Unit tests for CMake binary resolution."""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cppython.core.schema import CorePluginData
from cppython.plugins.cmake import resolution
from cppython.plugins.cmake.resolution import (
    CMakeToolResolutionError,
    resolve_cmake_binary,
    resolve_cmake_data,
    resolve_cmake_tool,
)
from cppython.plugins.cmake.schema import CMakeToolOrigin, EffectiveCMakeTool

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(autouse=True)
def _no_cmake_binary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMAKE_BINARY", raising=False)


def test_packaged_cmake_ignores_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The CMake installed with the ``cmake`` extra is used without consulting PATH."""
    packaged = tmp_path / "cmake.exe"
    expected = EffectiveCMakeTool(
        executable=packaged,
        version="4.5.0",
        origin="packaged",
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: packaged)
    monkeypatch.setattr(resolution.shutil, "which", lambda *_args, **_kwargs: pytest.fail("PATH consulted"))
    monkeypatch.setattr(resolution, "_inspect_cmake_binary", lambda *_args: expected)

    assert resolve_cmake_binary(None) == packaged


def test_configured_binary_preferred_over_packaged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicitly configured binary still takes precedence."""
    configured = tmp_path / "configured-cmake"
    configured.touch()
    expected = EffectiveCMakeTool(
        executable=configured,
        version="4.5.0",
        origin="configuration",
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: tmp_path / "cmake.exe")
    monkeypatch.setattr(resolution, "_inspect_cmake_binary", lambda *_args: expected)

    assert resolve_cmake_binary(configured) == configured


def test_missing_packaged_cmake_does_not_use_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the extra, nothing is selected even if PATH has a CMake."""
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: None)
    monkeypatch.setattr(resolution.shutil, "which", lambda *_args, **_kwargs: pytest.fail("PATH consulted"))

    assert resolve_cmake_tool(None) is None


def test_missing_packaged_cmake_with_minimum_explains_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A required minimum without the packaged tool points to sync or CMAKE_BINARY."""
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: None)

    with pytest.raises(CMakeToolResolutionError, match="CMAKE_BINARY") as error:
        resolve_cmake_tool(None, minimum_version="4.5.0rc1")
    assert "not resolved from PATH" in str(error.value)


def test_invalid_environment_selection_fails_without_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An invalid environment override must not silently select another CMake."""
    missing = tmp_path / "missing-cmake.exe"
    monkeypatch.setenv("CMAKE_BINARY", str(missing))
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: pytest.fail("must not fall back"))

    with pytest.raises(CMakeToolResolutionError, match="does not exist"):
        resolve_cmake_tool(None)


def test_invalid_configured_selection_fails_without_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An invalid configured executable must not silently select another CMake."""
    missing = tmp_path / "missing-cmake.exe"
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: pytest.fail("must not fall back"))

    with pytest.raises(CMakeToolResolutionError, match="does not exist"):
        resolve_cmake_tool(missing)


def test_selected_cmake_records_version_origin_and_fingerprint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The selected binary's version and fingerprint are captured."""
    binary = tmp_path / "cmake.exe"
    binary.write_bytes(b"validated executable")
    monkeypatch.setattr(
        resolution.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["cmake", "--version"],
            returncode=0,
            stdout="cmake version 4.5.0-rc2\n",
            stderr="",
        ),
    )

    tool = resolve_cmake_tool(binary)

    assert tool is not None
    assert tool.executable == binary.resolve()
    assert tool.version == "4.5.0rc2"
    assert tool.origin == "configuration"
    assert tool.capabilities == ("import-std-without-experimental-gate",)
    assert _SHA256_HEX_PATTERN.fullmatch(tool.fingerprint)


def test_relative_configured_path_resolves_from_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Relative configured paths resolve from the project root."""
    root = tmp_path / "project"
    binary = root / "tools" / "cmake.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    expected = EffectiveCMakeTool(
        executable=binary.resolve(),
        version="4.5.0",
        origin="configuration",
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(resolution, "_inspect_cmake_binary", lambda *_args: expected)

    assert resolve_cmake_tool(Path("tools/cmake.exe"), root) == expected


def test_conflicting_environment_and_configured_selections_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Conflicting explicit selections are reported instead of silently overriding."""
    monkeypatch.setenv("CMAKE_BINARY", str(tmp_path / "environment-cmake.exe"))

    with pytest.raises(CMakeToolResolutionError, match="Make both selections agree"):
        resolve_cmake_tool(tmp_path / "configured-cmake.exe")


def test_selected_cmake_must_meet_project_minimum(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A CMake binary below the project's declared minimum is rejected."""
    binary = tmp_path / "cmake.exe"
    binary.touch()
    monkeypatch.setattr(
        resolution.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["cmake", "--version"],
            returncode=0,
            stdout="cmake version 4.4.5\n",
            stderr="",
        ),
    )

    with pytest.raises(CMakeToolResolutionError, match="CMake 4.5.0rc1 or newer is required"):
        resolve_cmake_tool(binary, minimum_version="4.5.0rc1")


def test_older_packaged_cmake_fails_without_path_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An under-versioned packaged CMake fails instead of trying PATH."""
    packaged = tmp_path / "packaged" / "cmake.exe"
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: packaged)
    monkeypatch.setattr(resolution.shutil, "which", lambda *_args, **_kwargs: pytest.fail("PATH consulted"))

    def inspect(binary: Path, origin: CMakeToolOrigin) -> EffectiveCMakeTool:
        return EffectiveCMakeTool(
            executable=binary,
            version="4.4.5",
            origin=origin,
            fingerprint="0" * 64,
        )

    monkeypatch.setattr(resolution, "_inspect_cmake_binary", inspect)

    with pytest.raises(CMakeToolResolutionError, match="4.4.5") as error:
        resolve_cmake_tool(None, minimum_version="4.5.0rc1")
    assert "CMAKE_BINARY" in str(error.value)


def test_cmake_configuration_minimum_is_forwarded_to_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generator configuration enforces its declared minimum during resolution."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    tool = EffectiveCMakeTool(
        executable=tmp_path / "cmake.exe",
        version="4.5.0rc2",
        origin="environment",
        capabilities=("import-std-without-experimental-gate",),
        fingerprint="0" * 64,
    )
    resolve_mock = Mock(return_value=tool)
    monkeypatch.setattr(resolution, "resolve_cmake_tool", resolve_mock)

    data = resolve_cmake_data(
        {"minimum-version": "4.5.0rc1"},
        CorePluginData.model_construct(project_data=SimpleNamespace(project_root=project_root)),
    )

    assert data.minimum_version == "4.5.0rc1"
    assert data.cmake_tool == tool
    resolve_mock.assert_called_once_with(None, project_root, "4.5.0rc1")


def test_changed_cmake_fingerprint_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A changed executable is rejected after inspection."""
    binary = tmp_path / "cmake.exe"
    tool = EffectiveCMakeTool(
        executable=binary,
        version="4.5.0",
        origin="configuration",
        fingerprint="0" * 64,
    )
    changed = tool.model_copy(update={"fingerprint": "1" * 64})
    monkeypatch.setattr(resolution, "_inspect_cmake_binary", lambda *_args: changed)

    with pytest.raises(CMakeToolResolutionError, match="changed after inspection"):
        resolution.verify_cmake_tool(tool)
