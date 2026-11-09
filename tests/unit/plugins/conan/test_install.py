"""Unit tests for the conan plugin install functionality"""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from packaging.requirements import Requirement

from cppython.core.identity import BuildIdentity
from cppython.plugins.cmake.schema import EffectiveCMakeTool
from cppython.plugins.conan import local
from cppython.plugins.conan.identity import ConanIdentityProfile
from cppython.plugins.conan.plugin import ConanProvider
from cppython.test.pytest.mixins import ProviderPluginTestMixin
from cppython.utility.exception import (
    InstallationVerificationError,
    ProviderConfigurationError,
    ProviderInstallationError,
)
from cppython.utility.generated import (
    GeneratedFileError,
    GeneratedFilePolicy,
    GeneratedFileState,
)
from cppython.utility.utility import TypeName

# Use shared fixtures
pytest_plugins = ["tests.fixtures.conan"]

# Constants for test verification
EXPECTED_DEPENDENCY_COUNT = 2


def _write_identity_paths(plugin: ConanProvider) -> None:
    paths = plugin._conan_output_folder("Debug") / "generators" / "conan_cmakedeps_paths.cmake"
    paths.parent.mkdir(parents=True, exist_ok=True)
    paths.touch()


class TestConanInstall(ProviderPluginTestMixin[ConanProvider]):
    """Tests for the Conan provider install functionality"""

    @staticmethod
    @pytest.mark.parametrize('identity_mode', [False, True])
    def test_preparation_outputs_use_install_locations(plugin: ConanProvider, identity_mode: bool) -> None:
        """Reporting reuses Conan's own output-folder resolution and deduplicates identity folders."""
        if identity_mode:
            plugin.core_data.cppython_data.identity = BuildIdentity(
                name='selected-native',
                os='windows',
                arch='x64',
                compiler='msvc',
                standard_library='msvc',
                configurations=['Debug', 'Release'],
                dependency_build_types={'Debug': 'Debug', 'Release': 'Release'},
            )
        else:
            plugin.core_data.cppython_data.identity = None
        plugin.data.build_types = ['Debug', 'Release']
        outputs = plugin.preparation_outputs()
        assert outputs == tuple(dict.fromkeys(plugin._conan_output_folder(kind) for kind in plugin.data.build_types))
        if identity_mode:
            assert outputs == (plugin.core_data.cppython_data.build_path / 'selected-native' / 'conan',)
        else:
            assert outputs == (
                plugin.core_data.cppython_data.build_path / 'conan' / 'Debug',
                plugin.core_data.cppython_data.build_path / 'conan' / 'Release',
            )

    @staticmethod
    @pytest.fixture(name="plugin_data", scope="session")
    def fixture_plugin_data(conan_plugin_data: dict[str, Any]) -> dict[str, Any]:
        """A required testing hook that allows data generation

        Returns:
            The constructed plugin data
        """
        return conan_plugin_data

    @staticmethod
    @pytest.fixture(name="plugin_type", scope="session")
    def fixture_plugin_type() -> type[ConanProvider]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Provider
        """
        return ConanProvider

    @pytest.mark.parametrize("operation", ["install", "create"])
    def test_selected_cmake_path_is_passed_to_conan(
        self,
        plugin: ConanProvider,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        operation: str,
    ) -> None:
        """Conan install and create receive the same explicitly selected CMake executable."""
        cmake_binary = tmp_path / "CMake Tools" / "cmake.exe"
        cmake_tool = EffectiveCMakeTool(
            executable=cmake_binary,
            version="4.5.0rc2",
            origin="environment",
            capabilities=("import-std-without-experimental-gate",),
            fingerprint="0" * 64,
        )
        command: list[str] = []
        verify_tool = Mock()
        plugin._cmake_binary = str(cmake_binary)
        plugin._cmake_tool = cmake_tool
        monkeypatch.setattr(plugin, "_capture_conan_call", command.extend)
        monkeypatch.setattr("cppython.plugins.conan.plugin.verify_cmake_tool", verify_tool)

        if operation == "install":
            plugin._run_conan_install(
                tmp_path / "conanfile.py",
                update=False,
                build_type="Release",
                logger=logging.getLogger("test.conan"),
            )
        else:
            plugin._run_conan_create(
                tmp_path / "conanfile.py",
                build_type="Release",
                logger=logging.getLogger("test.conan"),
            )

        assert f'tools.cmake:cmake_program="{cmake_binary}"' in command
        verify_tool.assert_called_once_with(cmake_tool)

    @pytest.mark.parametrize(
        ("groups", "skipped"),
        [(None, True), (["dev"], True), (["test"], False), (["dev", "test"], False)],
    )
    def test_test_requirements_follow_groups(
        self,
        plugin: ConanProvider,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        groups: list[str] | None,
        skipped: bool,
    ) -> None:
        """The recipe declares every group, so unrequested test requirements are skipped at install."""
        command: list[str] = []
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_capture_conan_call", command.extend)
        monkeypatch.setattr(plugin, "_cmake_program_args", list)

        plugin._install_dependencies(groups=groups)

        assert ("tools.graph:skip_test=True" in command) is skipped

    def test_build_type_install_uses_isolated_output_folder(
        self,
        plugin: ConanProvider,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Each build type gets independent Conan-generated CMake package paths."""
        command: list[str] = []
        monkeypatch.setattr(plugin, "_capture_conan_call", command.extend)
        monkeypatch.setattr(plugin, "_cmake_program_args", list)

        plugin._run_conan_install(
            tmp_path / "conanfile.py",
            update=False,
            build_type="Debug",
            logger=logging.getLogger("test.conan"),
        )

        output_folder = Path(command[command.index("--output-folder") + 1])
        assert output_folder == plugin.core_data.cppython_data.build_path / "conan" / "Debug"

    def test_identity_installs_share_output_and_generate_paths_only_sync(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Debug and Release share one identity-scoped output, not the legacy build-type folders."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug", "Development", "Release"],
            dependency_build_types={"Debug": "Debug", "Development": "Release", "Release": "Release"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        commands: list[list[str]] = []
        monkeypatch.setattr(plugin, "_capture_conan_call", lambda command: commands.append(command.copy()))
        monkeypatch.setattr(plugin, "_cmake_program_args", list)

        for build_type in ("Debug", "Release"):
            plugin._run_conan_install(tmp_path / "conanfile.py", False, build_type, logging.getLogger("test.conan"))

        expected = plugin.core_data.cppython_data.build_path / identity.name / "conan"
        assert all(Path(command[command.index("--output-folder") + 1]) == expected for command in commands)
        assert plugin._local_build_path() == plugin.core_data.cppython_data.build_path / identity.name
        sync = plugin._create_cmake_sync_data()
        assert sync.toolchain_file is None
        assert sync.dependency_file == expected / "generators" / "conan_cmakedeps_paths.cmake"
        assert sync.dependency_ready_file == expected / ".cppython-installed.json"

    def test_identity_verification_requires_current_successful_install(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A missing or stale success record is never mistaken for a prepared identity."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug", "Release"],
            dependency_build_types={"Debug": "Debug", "Release": "Release"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin.core_data.cppython_data.generator_name = TypeName("cmake")
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)
        monkeypatch.setattr(plugin, "_run_conan_install", lambda *_args, **_kwargs: _write_identity_paths(plugin))
        paths = plugin._conan_output_folder("Debug") / "generators" / "conan_cmakedeps_paths.cmake"
        paths.parent.mkdir(parents=True)
        paths.touch()

        with pytest.raises(InstallationVerificationError, match="installation record"):
            plugin.verify_installed()
        plugin._install_dependencies(groups=["test"])
        plugin.verify_installed()
        local.write_stamp(plugin._local_build_path(), [], plugin.data.build_types, ["different"])
        with pytest.raises(InstallationVerificationError, match="current identity and compiler"):
            plugin.verify_installed()
        local.write_stamp(plugin._local_build_path(), [], plugin.data.build_types, ["test"])
        plugin.data.settings["compiler.cppstd"] = "23"
        with pytest.raises(InstallationVerificationError, match="current identity and compiler"):
            plugin.verify_installed()

    def test_identity_install_replaces_stale_generators_once(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A new selection cannot retain old package metadata, but both build types survive."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug", "Release"],
            dependency_build_types={"Debug": "Debug", "Release": "Release"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin.data.build_types = ["Debug", "Release"]
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)
        generators = plugin._conan_output_folder("Debug") / "generators"
        generators.mkdir(parents=True)
        (generators / "OldPackageConfig.cmake").write_text("stale", encoding="utf-8")

        def generate(_recipe: Path, _update: bool, build_type: str, _logger: Any, **_kwargs: Any) -> None:
            generators.mkdir(parents=True, exist_ok=True)
            (generators / f"{build_type}.cmake").write_text(build_type, encoding="utf-8")

        monkeypatch.setattr(plugin, "_run_conan_install", generate)
        plugin._install_dependencies(groups=["test"])

        assert not (generators / "OldPackageConfig.cmake").exists()
        assert (generators / "Debug.cmake").exists()
        assert (generators / "Release.cmake").exists()

    def test_identity_install_refuses_linked_generators(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cleanup must not traverse a linked generators directory."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug"],
            dependency_build_types={"Debug": "Debug"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)
        generators = plugin._conan_output_folder("Debug") / "generators"
        generators.mkdir(parents=True)
        sentinel = generators / "preserve.txt"
        sentinel.touch()
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == generators or original_is_symlink(path))

        with pytest.raises(ProviderInstallationError, match="linked generators"):
            plugin._install_dependencies()
        assert sentinel.exists()

    def test_previously_selected_groups_are_validated(self, plugin: ConanProvider) -> None:
        """Automatic repairs use the last group selection and reject removed groups."""
        plugin.core_data.cppython_data.dependency_groups["test"] = [Requirement("example==1.0")]
        local.write_stamp(plugin._local_build_path(), [], plugin.data.build_types, ["test"])
        assert plugin.previous_install_groups() == ["test"]

        del plugin.core_data.cppython_data.dependency_groups["test"]
        with pytest.raises(ProviderConfigurationError, match="no longer exist"):
            plugin.previous_install_groups()
        local.stamp_path(plugin._local_build_path()).write_text("{invalid", encoding="utf-8")
        with pytest.raises(ProviderConfigurationError, match="missing or invalid"):
            plugin.previous_install_groups()

    def test_local_source_changes_invalidate_identity_installation(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PEP 517 verification rejects dependency glue for an old local-source selection."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug"],
            dependency_build_types={"Debug": "Debug"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin.core_data.cppython_data.generator_name = TypeName("cmake")
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)
        monkeypatch.setattr(plugin, "_run_conan_install", lambda *_args, **_kwargs: _write_identity_paths(plugin))
        paths = plugin._conan_output_folder("Debug") / "generators" / "conan_cmakedeps_paths.cmake"
        paths.parent.mkdir(parents=True)
        paths.touch()
        monkeypatch.setattr("cppython.plugins.conan.local.signature", lambda *_args: "before")

        plugin._install_dependencies(groups=["test"])
        plugin.verify_installed()
        monkeypatch.setattr("cppython.plugins.conan.local.signature", lambda *_args: "after")
        with pytest.raises(InstallationVerificationError, match="current identity"):
            plugin.verify_installed()

    def test_dependency_changes_invalidate_identity_installation(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A changed dependency declaration or Conan recipe requires a new install."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug", "Release"],
            dependency_build_types={"Debug": "Debug", "Release": "Release"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin.core_data.cppython_data.generator_name = TypeName("cmake")
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)
        monkeypatch.setattr(plugin, "_run_conan_install", lambda *_args, **_kwargs: _write_identity_paths(plugin))
        root = plugin.core_data.project_data.project_root
        recipe = root / "conanfile.py"
        recipe.write_text("# original recipe\n", encoding="utf-8")
        paths = plugin._conan_output_folder("Debug") / "generators" / "conan_cmakedeps_paths.cmake"
        paths.parent.mkdir(parents=True)
        paths.touch()

        plugin._install_dependencies()
        plugin.verify_installed()
        plugin.core_data.cppython_data.dependencies.append(Requirement("example==2.0"))
        with pytest.raises(InstallationVerificationError, match="current identity and compiler"):
            plugin.verify_installed()

        plugin._install_dependencies()
        plugin.verify_installed()
        recipe.write_text("# modified recipe\n", encoding="utf-8")
        with pytest.raises(InstallationVerificationError, match="current identity and compiler"):
            plugin.verify_installed()

    def test_failed_install_removes_identity_ready_marker(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An interrupted update cannot leave stale dependencies marked as ready."""
        identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug"],
            dependency_build_types={"Debug": "Debug"},
        )
        plugin.core_data.cppython_data.identity = identity
        plugin._identity_profile = ConanIdentityProfile(settings={"compiler": "msvc"}, conf={})
        ready = plugin._identity_ready_file()
        assert ready is not None
        ready.parent.mkdir(parents=True)
        ready.write_text("stale\n", encoding="utf-8")
        monkeypatch.setattr(plugin, "_prepare_installation", lambda: tmp_path / "conanfile.py")
        monkeypatch.setattr(plugin, "_local_packages", list)

        def fail(*_args: object, **_kwargs: object) -> None:
            raise ProviderInstallationError("conan", "install failed")

        monkeypatch.setattr(plugin, "_run_conan_install", fail)
        with pytest.raises(ProviderInstallationError, match="install failed"):
            plugin._install_dependencies()
        assert not ready.exists()

    def test_sanitizer_identity_only_prepares_required_build_type(
        self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Debug-only identities never install an unused Release dependency graph."""
        identity = BuildIdentity(
            name="linux-x64-clang-libcxx-sanitize",
            os="linux",
            arch="x64",
            compiler="clang",
            standard_library="libc++",
            variant="sanitize",
            configurations=["Debug"],
            dependency_build_types={"Debug": "Debug"},
        )
        plugin.core_data.cppython_data.identity = identity
        monkeypatch.setattr(ConanProvider, "_ensure_default_profiles", lambda _self: None)
        sanitized = ConanProvider(plugin.group_data, plugin.core_data, {})
        assert sanitized.data.build_types == ["Debug"]

        with pytest.raises(ProviderConfigurationError, match="absent from the configured build-types"):
            ConanProvider(plugin.group_data, plugin.core_data, {"build-types": ["Release"]})

    def test_generated_file_error_is_not_wrapped(self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refused generated file surfaces with its diff instead of a generic install failure."""
        error = GeneratedFileError(
            Path("conanfile_base.py"),
            GeneratedFileState.STALE,
            GeneratedFilePolicy.LOCKED,
            "",
        )

        def refuse() -> Path:
            raise error

        monkeypatch.setattr(plugin, "_prepare_installation", refuse)

        with pytest.raises(GeneratedFileError) as raised:
            plugin._install_dependencies()

        assert raised.value is error

    def test_check_never_writes(self, plugin: ConanProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """check() generates under the LOCKED policy."""
        generate = Mock()
        monkeypatch.setattr(plugin.builder, "generate_conanfile", generate)

        plugin.check()

        assert generate.call_args.args[2] is GeneratedFilePolicy.LOCKED
