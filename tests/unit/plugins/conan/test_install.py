"""Unit tests for the conan plugin install functionality"""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from cppython.plugins.cmake.schema import EffectiveCMakeTool
from cppython.plugins.conan.plugin import ConanProvider
from cppython.test.pytest.mixins import ProviderPluginTestMixin
from cppython.utility.generated import (
    GeneratedFileError,
    GeneratedFilePolicy,
    GeneratedFileState,
)

# Use shared fixtures
pytest_plugins = ["tests.fixtures.conan"]

# Constants for test verification
EXPECTED_DEPENDENCY_COUNT = 2


class TestConanInstall(ProviderPluginTestMixin[ConanProvider]):
    """Tests for the Conan provider install functionality"""

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
