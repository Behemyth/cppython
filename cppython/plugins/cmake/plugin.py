"""The CMake generator implementation"""

import json
import re
import subprocess
from logging import getLogger
from pathlib import Path
from typing import Any, Literal

from cppython.core.plugin_schema.generator import (
    Generator,
    GeneratorPluginGroupData,
    SupportedGeneratorFeatures,
)
from cppython.core.schema import (
    CorePluginData,
    Information,
    PluginReport,
    SupportedFeatures,
    SyncData,
)
from cppython.plugins.cmake.builder import Builder
from cppython.plugins.cmake.presets import PresetIndex, PresetKind, stale_cache_hint
from cppython.plugins.cmake.resolution import (
    MISSING_CMAKE_GUIDANCE,
    CMakeToolResolutionError,
    resolve_cmake_data,
    verify_cmake_tool,
)
from cppython.plugins.cmake.schema import CMakeSyncData
from cppython.utility.exception import ConfigurationRequiredError
from cppython.utility.msvc import developer_environment
from cppython.utility.subprocess import run_subprocess

logger = getLogger("cppython.cmake")

_CTEST_SUMMARY = re.compile(r"^\d+% tests passed.*$", re.MULTILINE)


def ctest_summary(output: str | None) -> str | None:
    """Return CTest's ``N% tests passed ...`` line from its output."""
    match = _CTEST_SUMMARY.search(output or "")
    return match.group(0).strip() if match else None


class CMakeGenerator(Generator):
    """CMake generator"""

    def __init__(
        self,
        group_data: GeneratorPluginGroupData,
        core_data: CorePluginData,
        data: dict[str, Any],
    ) -> None:
        """Initializes the generator"""
        self.group_data = group_data
        self.core_data = core_data
        self.data = resolve_cmake_data(data, core_data)
        self.builder = Builder()

        self._cppython_preset_directory = self.core_data.cppython_data.tool_path / "cppython"
        self._provider_directory = self._cppython_preset_directory / "providers"
        self._toolchain_files: dict[str, Path] = {}

    @staticmethod
    def features(directory: Path) -> SupportedFeatures:
        """Queries if CMake is supported

        Returns:
            The supported features - `SupportedGeneratorFeatures`. Cast to this type to help us avoid generic typing
        """
        return SupportedGeneratorFeatures()

    @staticmethod
    def information() -> Information:
        """Queries plugin info

        Returns:
            Plugin information
        """
        return Information()

    @staticmethod
    def sync_types() -> list[type[SyncData]]:
        """Returns types in order of preference

        Returns:
            The available types
        """
        return [CMakeSyncData]

    def sync(self, sync_data: SyncData) -> None:
        """Disk sync point

        Args:
            sync_data: The input data
        """
        match sync_data:
            case CMakeSyncData():
                self._toolchain_files = dict(sync_data.toolchain_files)
                self._cppython_preset_directory.mkdir(parents=True, exist_ok=True)

                cppython_preset_file = self._cppython_preset_directory / "CPPython.json"

                project_root = self.core_data.project_data.project_root

                cppython_preset_file = self.builder.write_cppython_preset(
                    self._cppython_preset_directory,
                    cppython_preset_file,
                    sync_data,
                    project_root,
                )

                self.builder.write_root_presets(
                    self.data.preset_file,
                    cppython_preset_file,
                    self.data,
                    self.core_data.cppython_data.build_path,
                )
            case _:
                raise ValueError("Unsupported sync data type")

    def _cmake_command(self) -> str:
        """Returns the cmake command to use.

        Returns:
            The cmake binary path as a string
        """
        if self.data.cmake_tool:
            verify_cmake_tool(self.data.cmake_tool)
        if self.data.cmake_binary:
            return str(self.data.cmake_binary)
        raise CMakeToolResolutionError(f"No CMake executable was selected. {MISSING_CMAKE_GUIDANCE}")

    def _ctest_command(self) -> str:
        """Returns the ctest command to use.

        Derives the ctest path from the cmake binary path when available.

        Returns:
            The ctest binary path as a string
        """
        if self.data.cmake_tool:
            verify_cmake_tool(self.data.cmake_tool)
        if self.data.cmake_binary:
            ctest_path = self.data.cmake_binary.parent / "ctest"
            if ctest_path.exists():
                return str(ctest_path)
            ctest_exe = self.data.cmake_binary.parent / "ctest.exe"
            if ctest_exe.exists():
                return str(ctest_exe)
            raise FileNotFoundError(
                f"Could not find CTest alongside the selected CMake executable: {self.data.cmake_binary}"
            )
        raise CMakeToolResolutionError(f"No CMake executable was selected for CTest. {MISSING_CMAKE_GUIDANCE}")

    def _resolve_configuration(self, configuration: str | None) -> str:
        """Resolves the effective CMake preset from CLI argument or default config.

        Args:
            configuration: The configuration value passed from the CLI, or None

        Returns:
            The resolved CMake preset name

        Raises:
            ValueError: If no configuration is available from either CLI or default-configuration config
        """
        effective = configuration or self.data.default_configuration
        if effective is None:
            raise ConfigurationRequiredError(
                "CMake generator requires a configuration. "
                "Provide --configuration on the CLI or set 'default-configuration' in [tool.cppython.generators.cmake]."
            )
        return effective

    def _preset(
        self,
        kind: PresetKind,
        configuration: str | None,
        index: PresetIndex | None = None,
    ) -> str:
        """Resolve the configuration to the preset of *kind* that CMake should run."""
        name = self._resolve_configuration(configuration)
        index = index or PresetIndex(self.core_data.project_data.project_root)
        preset = index.resolve(kind, name)
        if preset != name:
            logger.info("Using %s preset '%s' for '%s'", kind, preset, name)
        return preset

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a CMake tool from the project root, in the compiler environment when one is needed."""
        return run_subprocess(
            cmd,
            cwd=self.core_data.project_data.project_root,
            logger=logger,
            env=developer_environment(),
        )

    def build(self, configuration: str | None = None) -> None:
        """Builds the project using cmake --build with the resolved preset.

        Args:
            configuration: Optional CMake preset name. Overrides default-configuration from config.
        """
        index = PresetIndex(self.core_data.project_data.project_root)
        preset = self._preset("build", configuration, index)
        self._configure_operation("build", preset, index)
        self._run([self._cmake_command(), "--build", "--preset", preset])

    def _configure_operation(self, kind: Literal["build", "test"], preset: str, index: PresetIndex) -> None:
        """Configure the build tree selected by a build or test preset."""
        configure_preset = index.field(kind, preset, "configurePreset")
        if configure_preset:
            self._request_codemodel(index, configure_preset)
            self._run([self._cmake_command(), "--preset", configure_preset])

    def _request_codemodel(self, index: PresetIndex, preset: str) -> Path | None:
        """Request CMake's target metadata for the selected configure preset."""
        binary_dir = index.binary_dir(preset)
        if binary_dir is None:
            return None
        query = binary_dir / ".cmake" / "api" / "v1" / "query"
        query.mkdir(parents=True, exist_ok=True)
        (query / "codemodel-v2").touch()
        return binary_dir

    def _codemodel_targets(self, index: PresetIndex, preset: str, configuration: str | None) -> list[dict[str, Any]]:
        """Read target metadata for the selected build configuration."""
        binary_dir = index.binary_dir(preset)
        if binary_dir is None:
            raise ConfigurationRequiredError(
                f"Cannot resolve the binaryDir for configure preset '{preset}' to inspect build targets."
            )
        reply_dir = binary_dir / ".cmake" / "api" / "v1" / "reply"
        indexes = sorted(reply_dir.glob("index-*.json"), key=lambda path: path.stat().st_mtime)
        if not indexes:
            raise FileNotFoundError(
                f"CMake has no file API reply in '{reply_dir}'. Configure the selected preset first."
            )
        reply = json.loads(indexes[-1].read_text(encoding="utf-8"))
        codemodel_file = reply.get("reply", {}).get("codemodel-v2", {}).get("jsonFile")
        if not codemodel_file:
            raise FileNotFoundError(f"CMake's file API reply in '{reply_dir}' does not contain codemodel-v2 data.")
        codemodel = json.loads((reply_dir / codemodel_file).read_text(encoding="utf-8"))
        configurations = codemodel.get("configurations", [])
        if configuration is None:
            if len(configurations) != 1:
                raise ConfigurationRequiredError(
                    f"Build preset for '{preset}' does not select one of {len(configurations)} CMake configurations."
                )
            selected = configurations[0]
        else:
            matches = [item for item in configurations if item.get("name", "").casefold() == configuration.casefold()]
            if len(matches) != 1:
                raise ConfigurationRequiredError(
                    f"CMake configuration '{configuration}' is not uniquely available for preset '{preset}'."
                )
            selected = matches[0]
        targets = []
        for target in selected.get("targets", []):
            target_data = json.loads((reply_dir / target["jsonFile"]).read_text(encoding="utf-8"))
            targets.append(target_data)
        return targets

    def dependency_build_type(self, configuration: str | None = None) -> str | None:
        """The provider build type whose toolchain the configuration's configure preset uses.

        Args:
            configuration: Optional CMake configure, build or test preset name.

        Returns:
            The build type, or None if the preset's toolchain isn't one the provider generated
        """
        if not self._toolchain_files:
            return None
        project_root = self.core_data.project_data.project_root
        index = PresetIndex(project_root)
        try:
            preset = self._preset("configure", configuration, index)
        except ConfigurationRequiredError:
            return None
        toolchain = index.field("configure", preset, "toolchainFile")
        if not isinstance(toolchain, str):
            return None
        selected = (project_root / toolchain.replace("${sourceDir}", str(project_root))).resolve()
        for build_type, path in self._toolchain_files.items():
            if (project_root / path).resolve() == selected:
                return build_type
        return None

    def configure(self, configuration: str | None = None) -> None:
        """Configures the project using the resolved CMake configure preset.

        Args:
            configuration: Optional CMake configure, build or test preset name.
        """
        index = PresetIndex(self.core_data.project_data.project_root)
        preset = self._preset("configure", configuration, index)
        self._request_codemodel(index, preset)
        try:
            self._run([self._cmake_command(), "--preset", preset])
        except subprocess.CalledProcessError as error:
            if hint := stale_cache_hint(index, preset):
                error.add_note(hint)
            raise

    def test(self, configuration: str | None = None) -> str | None:
        """Runs tests using ctest with the resolved preset.

        Args:
            configuration: Optional CMake preset name. Overrides default-configuration from config.

        Returns:
            CTest's summary line, when it printed one.
        """
        index = PresetIndex(self.core_data.project_data.project_root)
        preset = self._preset("test", configuration, index)
        self._configure_operation("test", preset, index)
        return ctest_summary(self._run([self._ctest_command(), "--preset", preset]).stdout)

    def bench(self, configuration: str | None = None) -> str | None:
        """Runs benchmarks using ctest with the resolved preset.

        Args:
            configuration: Optional CMake preset name. Overrides default-configuration from config.

        Returns:
            CTest's summary line, when it printed one.
        """
        index = PresetIndex(self.core_data.project_data.project_root)
        preset = self._preset("test", configuration, index)
        self._configure_operation("test", preset, index)
        return ctest_summary(self._run([self._ctest_command(), "--preset", preset]).stdout)

    def run(self, target: str, configuration: str | None = None) -> None:
        """Runs a built executable by target name.

        Searches the build directory for the executable matching the target name.

        Args:
            target: The name of the build target/executable to run
            configuration: Optional CMake preset name. Overrides default-configuration from config.

        Raises:
            FileNotFoundError: If the target executable cannot be found
        """
        index = PresetIndex(self.core_data.project_data.project_root)
        build_preset = self._preset("build", configuration, index)
        configure_preset = index.field("build", build_preset, "configurePreset")
        if configure_preset is None:
            raise ConfigurationRequiredError(f"Build preset '{build_preset}' does not identify a configure preset.")
        self._request_codemodel(index, configure_preset)
        self._run([self._cmake_command(), "--preset", configure_preset])
        build_configuration = index.field("build", build_preset, "configuration")
        targets = self._codemodel_targets(index, configure_preset, build_configuration)
        target_name = target[:-4] if target.casefold().endswith(".exe") else target
        matches = [
            artifact
            for item in targets
            if item.get("name") == target_name and item.get("type") == "EXECUTABLE"
            for artifact in item.get("artifacts", [])
            if Path(artifact["path"]).stem.casefold() == target_name.casefold()
            and Path(artifact["path"]).suffix.casefold() in {".exe", ""}
        ]
        if len(matches) != 1:
            if not matches:
                raise FileNotFoundError(
                    f"Could not find executable target '{target}' for build preset '{build_preset}'."
                )
            raise ConfigurationRequiredError(
                f"Executable target '{target}' resolves to multiple artifacts for build preset '{build_preset}'."
            )
        binary_dir = index.binary_dir(configure_preset)
        if binary_dir is None:
            raise ConfigurationRequiredError(
                f"Cannot resolve the binaryDir for configure preset '{configure_preset}' to run build targets."
            )
        executable = binary_dir / matches[0]["path"]
        run_subprocess([str(executable)], cwd=self.data.preset_file.parent, logger=logger)

    def list_targets(self) -> list[str]:
        """Lists discovered build targets/executables in the CMake build directory.

        Searches the build directory for executable files, excluding common
        non-target files.

        Returns:
            A sorted list of unique target names found.
        """
        build_path = self.core_data.cppython_data.build_path

        if not build_path.exists():
            return []

        # Collect executable files from the build directory
        targets: set[str] = set()
        for candidate in build_path.rglob("*"):
            if candidate.is_file() and (candidate.stat().st_mode & 0o111 or candidate.suffix == ".exe"):
                # Use the stem (name without extension) as the target name
                targets.add(candidate.stem)

        return sorted(targets)

    def plugin_info(self) -> PluginReport:
        """Return a report describing the CMake generator's configuration and managed files.

        Returns:
            A :class:`PluginReport` with CMake-specific details.
        """
        managed = [self._cppython_preset_directory / "CPPython.json"]

        config: dict[str, object] = {
            "preset_file": str(self.data.preset_file),
            "configuration_name": self.data.configuration_name,
        }
        if self.data.cmake_binary is not None:
            config["cmake_binary"] = str(self.data.cmake_binary)
        if self.data.cmake_tool is not None:
            config["cmake_tool"] = self.data.cmake_tool.model_dump(mode="json")
        if self.data.minimum_version is not None:
            config["minimum_version"] = self.data.minimum_version
        if self.data.default_configuration is not None:
            config["default_configuration"] = self.data.default_configuration

        return PluginReport(
            configuration=config,
            managed_files=managed,
        )
