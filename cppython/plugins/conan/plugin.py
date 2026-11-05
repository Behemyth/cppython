"""Conan Provider Plugin

This module implements the Conan provider plugin for CPPython. It handles
integration with the Conan package manager, including dependency resolution,
installation, and synchronization with other tools.
"""

import contextlib
import io
import os
import tomllib
from logging import Logger, getLogger
from pathlib import Path
from typing import Any

from conan.api.conan_api import ConanAPI
from conan.cli.cli import Cli
from conan.internal.api.local.editable import EditablePackages
from packaging.requirements import Requirement

from cppython.core.plugin_schema.generator import SyncConsumer
from cppython.core.plugin_schema.provider import (
    Provider,
    ProviderPluginGroupData,
    SupportedProviderFeatures,
)
from cppython.core.schema import (
    CorePluginData,
    Information,
    PluginReport,
    SupportedFeatures,
    SyncData,
)
from cppython.local_sources import project_selection
from cppython.local_sources.operations import canonical_name
from cppython.local_sources.schema import Severity
from cppython.plugins.cmake.plugin import CMakeGenerator
from cppython.plugins.cmake.resolution import verify_cmake_tool
from cppython.plugins.cmake.schema import CMakeSyncData, EffectiveCMakeTool
from cppython.plugins.conan import local
from cppython.plugins.conan.builder import Builder
from cppython.plugins.conan.local import LocalPackage, LocalSourceError
from cppython.plugins.conan.resolution import (
    resolve_conan_data,
    resolve_conan_dependency,
)
from cppython.plugins.conan.schema import ConanData, ConanfileGenerationData
from cppython.plugins.meson.plugin import MesonGenerator
from cppython.plugins.meson.schema import MesonSyncData
from cppython.utility.exception import (
    InstallationVerificationError,
    NotSupportedError,
    ProviderInstallationError,
)
from cppython.utility.generated import GeneratedFileError, GeneratedFilePolicy
from cppython.utility.msvc import developer_environment
from cppython.utility.subprocess import run_subprocess
from cppython.utility.utility import TypeName


class ConanProvider(Provider):
    """Conan Provider"""

    def __init__(
        self,
        group_data: ProviderPluginGroupData,
        core_data: CorePluginData,
        configuration_data: dict[str, Any],
    ) -> None:
        """Initializes the provider"""
        self.group_data: ProviderPluginGroupData = group_data
        self.core_data: CorePluginData = core_data
        self.data: ConanData = resolve_conan_data(configuration_data, core_data)

        self.builder = Builder()
        # Initialize ConanAPI once and reuse it
        self._conan_api = ConanAPI()
        # Initialize CLI for command API to work properly
        self._cli = Cli(self._conan_api)
        self._cli.add_commands()

        self._ensure_default_profiles()

        self._cmake_binary: str | None = None
        self._cmake_tool: EffectiveCMakeTool | None = None
        self._logger = getLogger("cppython.conan")
        # Every Conan command sees only these editables, never the global `conan editable` registry
        self._editables = EditablePackages()
        self._local: tuple[list[LocalPackage], bool] | None = None

    def _capture_conan_call(self, args: list[str]) -> None:
        """Run a Conan CLI command while capturing stdout/stderr to the logger.

        Args:
            args: Command arguments to pass to ``conan_api.command.run``.

        Raises:
            ProviderInstallationError: If the command fails; carries the command and its output.
        """
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            local.inject(self._conan_api, self._editables)
        except LocalSourceError as error:
            raise ProviderInstallationError("conan", str(error), error) from error

        try:
            with (
                contextlib.redirect_stdout(stdout_capture),
                contextlib.redirect_stderr(stderr_capture),
            ):
                self._conan_api.command.run(args)
        except Exception as error:
            captured = (stdout_capture.getvalue(), stderr_capture.getvalue())
            output = "\n".join(text.rstrip("\n") for text in captured if text)
            for line in output.splitlines():
                self._logger.error("%s", line)
            raise ProviderInstallationError(
                "conan", str(error), error, command=["conan", *args], output=output
            ) from error
        else:
            captured_out = stdout_capture.getvalue()
            captured_err = stderr_capture.getvalue()
            if captured_out:
                for line in captured_out.splitlines():
                    self._logger.debug("%s", line)
            if captured_err:
                for line in captured_err.splitlines():
                    self._logger.debug("%s", line)

    @staticmethod
    def features(directory: Path) -> SupportedFeatures:
        """Queries conan support

        Args:
            directory: The directory to query

        Returns:
            Supported features - `SupportedProviderFeatures`. Cast to this type to help us avoid generic typing
        """
        return SupportedProviderFeatures()

    @staticmethod
    def information() -> Information:
        """Returns plugin information

        Returns:
            Plugin information
        """
        return Information()

    def _install_dependencies(self, *, update: bool = False, groups: list[str] | None = None) -> None:
        """Install/update dependencies using Conan CLI.

        Args:
            update: If True, check remotes for newer versions/revisions and install those.
                   If False, use cached versions when available.
            groups: Optional list of dependency group names to include
        """
        operation = "update" if update else "install"
        logger = getLogger("cppython.conan")

        try:
            # Setup environment and generate conanfile
            conanfile_path = self._prepare_installation()
        except GeneratedFileError:
            raise
        except Exception as e:
            raise ProviderInstallationError("conan", f"Failed to prepare {operation} environment: {e}", e) from e

        # Every group is declared in the recipe, so unrequested test requirements are skipped at install time
        include_test = bool(groups) and "test" in groups

        packages = self._local_packages()
        build_path = self.core_data.cppython_data.build_path

        try:
            build_types = self.data.build_types
            for build_type in build_types:
                self._editables = local.editables(packages, build_path, build_type)
                for package in packages:
                    logger.info("Preparing local %s for build type: %s", package.name, build_type)
                    self._prepare_local_package(package, update, build_type)
                logger.info("Installing dependencies for build type: %s", build_type)
                self._run_conan_install(conanfile_path, update, build_type, logger, include_test=include_test)
        except ProviderInstallationError, GeneratedFileError:
            raise
        except Exception as e:
            raise ProviderInstallationError("conan", f"Failed to install dependencies: {e}", e) from e
        finally:
            self._editables = EditablePackages()

        local.write_stamp(build_path, packages, self.data.build_types, groups)

    def _local_packages(self) -> list[LocalPackage]:
        """The user's selected local checkouts that apply to this project, dependencies first.

        Raises:
            ProviderInstallationError: If a selected checkout can't be used
        """
        return self._local_selection()[0]

    def _local_selection(self) -> tuple[list[LocalPackage], bool]:
        """The applicable local checkouts and whether the user's selection manages any package."""
        if self._local is not None:
            return self._local

        cppython_data = self.core_data.cppython_data
        requirements = [
            *cppython_data.dependencies,
            *(requirement for group in cppython_data.dependency_groups.values() for requirement in group),
        ]
        try:
            selection = project_selection(
                self.core_data.pep621_data.name, [requirement.name for requirement in requirements]
            )
        except Exception as error:
            message = f"Can't read the local-source selection: {error}"
            raise ProviderInstallationError("conan", message, error) from error

        if not selection.ok:
            problems = [
                f"{diagnostic.message}" + (f" Fix: {diagnostic.fix}" if diagnostic.fix else "")
                for diagnostic in selection.diagnostics
                if diagnostic.severity is Severity.ERROR
            ]
            raise ProviderInstallationError("conan", "Local sources can't be applied: " + " ".join(problems))

        packages: list[LocalPackage] = []
        names: dict[str, str] = {}
        for selected in selection.local:
            path = Path(str(selected.path))
            if not (path / "conanfile.py").is_file():
                raise ProviderInstallationError(
                    "conan",
                    f"Local '{selected.name}' at {path} has no conanfile.py. Run 'cppython install' there once.",
                )
            try:
                reference = local.recipe_reference(self._conan_api, path / "conanfile.py")
            except Exception as error:
                raise ProviderInstallationError(
                    "conan", f"Can't read the recipe of local '{selected.name}' at {path}: {error}", error
                ) from error
            names[canonical_name(selected.name)] = reference.split("/")[0]
            packages.append(LocalPackage(name=reference.split("/")[0], path=path, reference=reference))

        packages = [
            LocalPackage(
                name=package.name,
                path=package.path,
                reference=package.reference,
                requires=tuple(names[name] for name in requires if name in names),
            )
            for package, requires in zip(packages, selection.requires.values(), strict=True)
        ]
        self._local = (packages, selection.managed)
        return self._local

    def _local_generation_data(self, package: LocalPackage) -> ConanfileGenerationData:
        """The conanfile_base.py inputs a local checkout's own pyproject.toml declares."""
        data = tomllib.loads((package.path / "pyproject.toml").read_text(encoding="utf-8"))
        table = data.get("tool", {}).get("cppython", {})
        name, version = package.reference.split("/", 1)
        return ConanfileGenerationData(
            dependencies=[resolve_conan_dependency(Requirement(text)) for text in table.get("dependencies", [])],
            dependency_groups={
                group: [resolve_conan_dependency(Requirement(text)) for text in requirements]
                for group, requirements in table.get("dependency-groups", {}).items()
            },
            name=name,
            version=version,
        )

    def _prepare_local_package(self, package: LocalPackage, update: bool, build_type: str) -> None:
        """Install a local checkout's dependencies into its build tree and configure it.

        The checkout's CPPython-managed conanfile_base.py is regenerated first, exactly as
        'cppython install' in that checkout would, so its requirements match its pyproject.toml.
        """
        self.builder.generate_conanfile(package.path, self._local_generation_data(package))

        folder = local.output_folder(self.core_data.cppython_data.build_path, build_type, package.name)
        command_args = ["install", str(package.conanfile), "--output-folder", str(folder), "--build", "missing"]
        if update:
            command_args.append("--update")
        command_args.extend(["-c", "tools.graph:skip_test=True"])
        command_args.extend(self._settings_args(build_type))
        command_args.extend(["-c", "&:tools.cmake.cmakedeps:new=will_break_next"])
        command_args.extend(["-c", "tools.cmake.cmaketoolchain:generator=Ninja Multi-Config"])
        command_args.extend(self._cmake_program_args())

        original_cwd = os.getcwd()
        try:
            os.chdir(str(package.path))
            self._capture_conan_call(command_args)
        finally:
            os.chdir(original_cwd)

        run_subprocess(
            local.configure_command(self._cmake_binary or "cmake", package, folder, build_type),
            cwd=package.path,
            logger=self._logger,
            env=developer_environment(),
        )
        if local.package_config(package, folder) is None:
            raise ProviderInstallationError(
                "conan",
                f"Local '{package.name}' at {package.path} doesn't export {package.name}Config.cmake from its build "
                f"tree, so consumers can't find it. Add export(EXPORT ...) and copy the package config files to "
                f"${{CMAKE_CURRENT_BINARY_DIR}}/${{CMAKE_INSTALL_LIBDIR}}/cmake/{package.name} in its CMakeLists.txt.",
            )

    def sync_local_sources(self) -> list[str]:
        """Reinstall when the local-source selection changed since the last install, then summarize it.

        Returns:
            One line per local checkout in use, or a note that published packages are used
        """
        build_path = self.core_data.cppython_data.build_path
        packages, managed = self._local_selection()
        stamp = local.read_stamp(build_path)
        current = local.signature(packages, self.data.build_types)

        if (stamp is not None or packages) and (stamp is None or stamp.get("signature") != current):
            groups = stamp.get("groups") if stamp else list(self.core_data.cppython_data.dependency_groups) or None
            self._logger.info("The local-source selection changed since the last install; reinstalling")
            self._install_dependencies(groups=groups)

        if packages:
            return [local.checkout_summary(package) for package in packages]
        if managed:
            return ["local sources: none apply to this project; using published packages"]
        return []

    def build_local_sources(self, build_type: str | None = None) -> None:
        """Incrementally build each local checkout's build tree, dependencies first.

        Args:
            build_type: The dependency build type the consumer uses, or None for every configured one
        """
        packages = self._local_packages()
        build_types = [build_type] if build_type else self.data.build_types
        for selected_type in build_types:
            for package in packages:
                folder = local.output_folder(self.core_data.cppython_data.build_path, selected_type, package.name)
                if not (folder / "CMakeCache.txt").is_file():
                    raise InstallationVerificationError("conan", [f"local {package.name} build tree ({folder})"])
                self._logger.info("Building local %s (%s)", package.name, selected_type)
                run_subprocess(
                    [self._cmake_binary or "cmake", "--build", str(folder), "--config", selected_type],
                    cwd=package.path,
                    logger=self._logger,
                    env=developer_environment(),
                )

    def _generation_data(self) -> ConanfileGenerationData:
        """Resolve the data conanfile_base.py is generated from.

        Every declared dependency group is included, so the generated file does not depend
        on which groups a particular install selects.

        Returns:
            The generation data
        """
        cppython_data = self.core_data.cppython_data
        return ConanfileGenerationData(
            dependencies=[resolve_conan_dependency(req) for req in cppython_data.dependencies],
            dependency_groups={
                name: [resolve_conan_dependency(req) for req in requirements]
                for name, requirements in cppython_data.dependency_groups.items()
            },
            name=self.core_data.pep621_data.name,
            version=self.core_data.pep621_data.version,
        )

    def _prepare_installation(self) -> Path:
        """Prepare the installation environment and generate conanfile.

        Returns:
            Path to conanfile.py
        """
        self.builder.generate_conanfile(
            self.core_data.project_data.project_root,
            self._generation_data(),
            self.core_data.project_data.generated_files,
        )

        # Ensure build directory exists
        self.core_data.cppython_data.build_path.mkdir(parents=True, exist_ok=True)

        # Setup paths
        project_root = self.core_data.project_data.project_root
        conanfile_path = project_root / "conanfile.py"

        if not conanfile_path.exists():
            raise FileNotFoundError("Generated conanfile.py not found")

        return conanfile_path

    def _ensure_default_profiles(self) -> None:
        """Ensure default Conan profiles exist, creating them if necessary."""
        try:
            self._conan_api.profiles.get_default_host()
            self._conan_api.profiles.get_default_build()
        except Exception:
            # If profiles don't exist, create them using profile detect
            self._conan_api.command.run(["profile", "detect"])

    def _settings_args(self, build_type: str | None) -> list[str]:
        """Return ``-s`` arguments for the configured settings and *build_type*.

        Configured settings apply to both the host and build contexts (``-s:a``) so
        tools built from source, such as ``protoc``, use the project's language
        standard instead of the detected profile's default. The build type only
        applies to the host context.
        """
        args = [arg for key, value in self.data.settings.items() for arg in ("-s:a", f"{key}={value}")]
        if build_type:
            args.extend(["-s", f"build_type={build_type}"])
        return args

    def _cmake_program_args(self) -> list[str]:
        """Return Conan configuration for the inspected CMake executable."""
        if self._cmake_tool:
            verify_cmake_tool(self._cmake_tool)
        if not self._cmake_binary:
            return []

        cmake_path = f'"{self._cmake_binary}"' if " " in self._cmake_binary else self._cmake_binary
        return ["-c", f"tools.cmake:cmake_program={cmake_path}"]

    def _run_conan_install(
        self,
        conanfile_path: Path,
        update: bool,
        build_type: str,
        logger: Logger,
        *,
        include_test: bool = True,
    ) -> None:
        """Run conan install command using Conan API with optional build type.

        Args:
            conanfile_path: Path to the conanfile.py
            update: Whether to check for updates
            build_type: Build type (Release, Debug, etc.) or None for default
            logger: Logger instance
            include_test: Whether to install the recipe's test requirements
        """
        # Build conan install command arguments
        command_args = ["install", str(conanfile_path)]

        # Keep each build type's CMake package paths isolated for multi-config consumers.
        output_folder = self.core_data.cppython_data.build_path / "conan" / build_type
        command_args.extend(["--output-folder", str(output_folder)])

        # Add build missing flag
        command_args.extend(["--build", "missing"])

        # Add update flag if needed
        if update:
            command_args.append("--update")

        if not include_test:
            command_args.extend(["-c", "tools.graph:skip_test=True"])

        command_args.extend(self._settings_args(build_type))
        # Enable CMakeConfigDeps (the modern CMake config-mode generator) for the consumer only;
        # dependencies built from source keep the CMakeDeps their recipes are tested with
        command_args.extend(["-c", "&:tools.cmake.cmakedeps:new=will_break_next"])

        # Match create(): only Ninja Multi-Config supports C++ modules and 'import std;'
        command_args.extend(["-c", "tools.cmake.cmaketoolchain:generator=Ninja Multi-Config"])

        command_args.extend(self._cmake_program_args())

        try:
            # Use reusable Conan API instance instead of subprocess
            # Change to project directory since Conan API might not handle cwd like subprocess
            original_cwd = os.getcwd()
            try:
                os.chdir(str(self.core_data.project_data.project_root))
                self._capture_conan_call(command_args)
            finally:
                os.chdir(original_cwd)
        except ProviderInstallationError:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error("Conan install failed: %s", error_msg, exc_info=True)
            raise ProviderInstallationError("conan", error_msg, e) from e

    def verify_installed(self) -> None:
        """Verify that Conan-generated artifacts exist on disk.

        Checks for the toolchain/native files that ``conan install`` produces
        in the generators output directory.

        Raises:
            InstallationVerificationError: If expected artifacts are missing
        """
        missing: list[str] = []
        for build_type in self.data.build_types:
            generators_path = self.core_data.cppython_data.build_path / "conan" / build_type / "generators"
            cmake_toolchain = generators_path / "conan_toolchain.cmake"
            meson_native = generators_path / "conan_meson_native.ini"

            if not generators_path.is_dir():
                missing.append(f"generators directory ({generators_path})")
            elif not cmake_toolchain.exists() and not meson_native.exists():
                missing.append(
                    f"toolchain files in {generators_path} (expected conan_toolchain.cmake or conan_meson_native.ini)"
                )

        if missing:
            raise InstallationVerificationError("conan", missing)

    def install(self, groups: list[str] | None = None) -> None:
        """Installs the provider

        Args:
            groups: Optional list of dependency group names to install
        """
        self._install_dependencies(update=False, groups=groups)

    def check(self) -> None:
        """Verify conanfile_base.py matches pyproject.toml without writing anything.

        Raises:
            GeneratedFileError: If conanfile_base.py is missing or differs
        """
        self.builder.generate_conanfile(
            self.core_data.project_data.project_root,
            self._generation_data(),
            GeneratedFilePolicy.LOCKED,
        )

    def update(self, groups: list[str] | None = None) -> None:
        """Updates the provider

        Args:
            groups: Optional list of dependency group names to update
        """
        self._install_dependencies(update=True, groups=groups)

    @staticmethod
    def supported_sync_type(sync_type: type[SyncData]) -> bool:
        """Checks if the given sync type is supported by the Conan provider.

        Args:
            sync_type: The type of synchronization data to check.

        Returns:
            True if the sync type is supported, False otherwise.
        """
        return sync_type in CMakeGenerator.sync_types() or sync_type in MesonGenerator.sync_types()

    def sync_data(self, consumer: SyncConsumer) -> SyncData:
        """Generates synchronization data for the given consumer.

        Args:
            consumer: The input consumer for which synchronization data is generated.

        Returns:
            The synchronization data object.

        Raises:
            NotSupportedError: If the consumer's sync type is not supported.
        """
        for sync_type in consumer.sync_types():
            if sync_type == CMakeSyncData:
                return self._sync_with_cmake(consumer)
            if sync_type == MesonSyncData:
                return self._create_meson_sync_data()

        raise NotSupportedError(f"Unsupported sync types: {consumer.sync_types()}")

    def _sync_with_cmake(self, consumer: SyncConsumer) -> CMakeSyncData:
        """Synchronize with CMake generator and create sync data.

        Args:
            consumer: The CMake generator consumer

        Returns:
            CMakeSyncData configured for Conan integration
        """
        # Extract cmake_binary from CMakeGenerator if available
        # The cmake_binary is already validated and resolved during CMake data resolution
        if isinstance(consumer, CMakeGenerator) and consumer.data.cmake_binary:
            self._cmake_binary = str(consumer.data.cmake_binary.resolve())
            self._cmake_tool = consumer.data.cmake_tool

        return self._create_cmake_sync_data()

    def _create_cmake_sync_data(self) -> CMakeSyncData:
        """Creates CMake synchronization data with Conan toolchain configuration.

        Returns:
            CMakeSyncData configured for Conan integration
        """
        build_path = self.core_data.cppython_data.build_path
        toolchain_files = {
            build_type: build_path / "conan" / build_type / "generators" / "conan_toolchain.cmake"
            for build_type in self.data.build_types
        }

        return CMakeSyncData(
            provider_name=TypeName("conan"),
            toolchain_file=toolchain_files.get("Release") or next(iter(toolchain_files.values()), None),
            toolchain_files=toolchain_files,
        )

    def _create_meson_sync_data(self) -> MesonSyncData:
        """Creates Meson synchronization data with Conan toolchain configuration.

        Conan's MesonToolchain generator produces ``conan_meson_native.ini``
        and ``conan_meson_cross.ini`` files in the generators directory.

        Returns:
            MesonSyncData configured for Conan integration
        """
        build_type = self.data.build_types[0]
        generators_path = self.core_data.cppython_data.build_path / "conan" / build_type / "generators"

        native_file = generators_path / "conan_meson_native.ini"
        cross_file = generators_path / "conan_meson_cross.ini"

        return MesonSyncData(
            provider_name=TypeName("conan"),
            native_file=native_file if native_file.exists() else None,
            cross_file=cross_file if cross_file.exists() else None,
        )

    @classmethod
    async def download_tooling(cls, directory: Path) -> None:
        """Download external tooling required by the Conan provider.

        Since we're using CMakeToolchain generator instead of cmake-conan provider,
        no external tooling needs to be downloaded.
        """
        # No external tooling required when using CMakeToolchain

    def publish(self) -> None:
        """Publishes the package using conan create workflow.

        Creates packages for all configured build types (e.g., Release, Debug)
        to support both single-config and multi-config generators.
        """
        project_root = self.core_data.project_data.project_root
        conanfile_path = project_root / "conanfile.py"
        logger = getLogger("cppython.conan")

        if not conanfile_path.exists():
            raise FileNotFoundError(f"conanfile.py not found at {conanfile_path}")

        if packages := self._local_packages():
            names = ", ".join(package.name for package in packages)
            raise ProviderInstallationError(
                "conan",
                f"Refusing to publish while local sources apply ({names}); a package built against unpublished "
                "checkouts can't be reproduced. Publish with CPPYTHON_LOCAL_SOURCES=off or 'cppython local off'.",
            )

        try:
            # Create packages for each configured build type
            build_types = self.data.build_types
            for build_type in build_types:
                logger.info("Creating package for build type: %s", build_type)
                self._run_conan_create(conanfile_path, build_type, logger)

            # Upload once after all configurations are built
            if not self.data.skip_upload:
                self._upload_package(logger)

        except ProviderInstallationError:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error("Conan create failed: %s", error_msg, exc_info=True)
            raise ProviderInstallationError("conan", error_msg, e) from e

    def _run_conan_create(self, conanfile_path: Path, build_type: str, logger: Logger) -> None:
        """Run conan create command for a specific build type.

        Args:
            conanfile_path: Path to the conanfile.py
            build_type: Build type (Release, Debug, etc.)
            logger: Logger instance
        """
        # Build conan create command arguments
        command_args = ["create", str(conanfile_path)]

        # Add build mode (build everything for publishing)
        command_args.extend(["--build", "missing"])

        # Skip test dependencies during publishing
        command_args.extend(["-c", "tools.graph:skip_test=True"])
        command_args.extend(["-c", "tools.build:skip_test=True"])

        # Enable CMakeConfigDeps (the modern CMake config-mode generator) for the package being
        # created only; dependencies built from source keep the CMakeDeps their recipes are tested with
        command_args.extend(["-c", "&:tools.cmake.cmakedeps:new=will_break_next"])

        # Force Ninja Multi-Config generator for C++ module support
        # The Visual Studio generator does not support BMI-only compilation
        # needed for consuming C++ modules across package boundaries
        command_args.extend(["-c", "tools.cmake.cmaketoolchain:generator=Ninja Multi-Config"])

        command_args.extend(self._settings_args(build_type))

        command_args.extend(self._cmake_program_args())

        # Run conan create using reusable Conan API instance
        # Change to project directory since Conan API might not handle cwd like subprocess
        original_cwd = os.getcwd()
        try:
            os.chdir(str(self.core_data.project_data.project_root))
            self._capture_conan_call(command_args)
        finally:
            os.chdir(original_cwd)

    def _upload_package(self, logger) -> None:
        """Upload the package to configured remotes using Conan API."""
        # If no remotes configured, upload to all remotes
        if not self.data.remotes:
            # Upload to all available remotes
            command_args = ["upload", "*", "--all", "--confirm"]
        else:
            # Upload only to specified remotes
            for remote in self.data.remotes:
                command_args = ["upload", "*", "--remote", remote, "--all", "--confirm"]

                # Log the command being executed
                logger.info("Executing conan upload command: conan %s", " ".join(command_args))

                try:
                    self._capture_conan_call(command_args)
                except ProviderInstallationError as e:
                    raise ProviderInstallationError(
                        "conan",
                        f"Upload to {remote} failed: {e.error}",
                        e,
                        command=e.command,
                        output=e.output,
                    ) from e
            return

        # Log the command for uploading to all remotes
        logger.info("Executing conan upload command: conan %s", " ".join(command_args))

        try:
            self._capture_conan_call(command_args)
        except Exception as e:
            error_msg = str(e)
            logger.error("Conan upload failed: %s", error_msg, exc_info=True)
            raise ProviderInstallationError("conan", error_msg, e) from e

    def plugin_info(self) -> PluginReport:
        """Return a report describing the Conan provider's configuration, managed files, and templates.

        Returns:
            A :class:`PluginReport` with Conan-specific details.
        """
        project_root = self.core_data.project_data.project_root

        template_content = Builder._conanfile_content(
            self.core_data.pep621_data.name,
            self.core_data.pep621_data.version,
        )

        return PluginReport(
            configuration={
                "build_types": self.data.build_types,
                "remotes": self.data.remotes,
                "profile_dir": str(self.data.profile_dir),
                "skip_upload": self.data.skip_upload,
            },
            managed_files=[project_root / "conanfile_base.py"],
            template_files={"conanfile.py": template_content},
        )
