"""Unit test the provider plugin"""

import json
from pathlib import Path
from typing import Any

import pytest

from cppython.plugins.cmake.plugin import CMakeGenerator
from cppython.plugins.cmake.presets import PresetIndex
from cppython.plugins.cmake.resolution import CMakeToolResolutionError
from cppython.plugins.cmake.schema import (
    CMakeConfiguration,
    EffectiveCMakeTool,
)
from cppython.test.pytest.contracts import GeneratorUnitTestContract
from cppython.utility.exception import ConfigurationRequiredError

pytest_plugins = ["tests.fixtures.cmake"]


class TestCPPythonGenerator(GeneratorUnitTestContract[CMakeGenerator]):
    """The tests for the CMake generator"""

    @staticmethod
    @pytest.fixture(name="plugin_data", scope="session")
    def fixture_plugin_data(cmake_data: CMakeConfiguration) -> dict[str, Any]:
        """A required testing hook that allows data generation

        Args:
            cmake_data: The input data

        Returns:
            The constructed plugin data
        """
        return cmake_data.model_dump()

    @staticmethod
    @pytest.fixture(name="plugin_type", scope="session")
    def fixture_plugin_type() -> type[CMakeGenerator]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Generator
        """
        return CMakeGenerator

    @staticmethod
    def test_configure_uses_project_root_and_resolved_preset(plugin: CMakeGenerator, mocker: Any) -> None:
        """CMake presets must be invoked from the project root, not the managed preset file directory."""
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")

        plugin.configure("dev")

        run_spy.assert_called_once_with(
            [plugin._cmake_command(), "--preset", "dev"],
            cwd=plugin.core_data.project_data.project_root,
            logger=mocker.ANY,
            env=mocker.ANY,
        )

    @staticmethod
    @pytest.mark.parametrize("operation", ["configure", "build"])
    def test_cmake_operations_use_selected_executable(
        plugin: CMakeGenerator,
        mocker: Any,
        tmp_path: Any,
        operation: str,
    ) -> None:
        """CMake operations invoke the selected executable as a single path argument."""
        cmake_binary = tmp_path / "CMake Tools" / "cmake.exe"
        tool = EffectiveCMakeTool(
            executable=cmake_binary,
            version="4.5.0rc2",
            origin="environment",
            capabilities=("import-std-without-experimental-gate",),
            fingerprint="a" * 64,
        )
        plugin.data.cmake_binary = cmake_binary
        plugin.data.cmake_tool = tool
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")
        verify_spy = mocker.patch("cppython.plugins.cmake.plugin.verify_cmake_tool")

        getattr(plugin, operation)("dev")

        assert run_spy.call_args.args[0][0] == str(cmake_binary)
        verify_spy.assert_called_once_with(tool)

    @staticmethod
    def test_build_test_bench_use_project_root(plugin: CMakeGenerator, mocker: Any) -> None:
        """All preset-driven operations should run from the root CMakePresets.json directory."""
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")
        run_spy.return_value.stdout = ""
        operations = (plugin.build, plugin.test, plugin.bench)

        for operation in operations:
            operation("dev")

        assert run_spy.call_count == len(operations)
        assert all(call.kwargs["cwd"] == plugin.core_data.project_data.project_root for call in run_spy.call_args_list)

    @staticmethod
    def test_test_returns_ctest_summary(plugin: CMakeGenerator, mocker: Any) -> None:
        """The CTest summary line is returned so the CLI can show it."""
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")
        run_spy.return_value.stdout = "Test #1: a ... Passed\n\n100% tests passed out of 441\n\nTotal Test time\n"

        assert plugin.test("dev") == "100% tests passed out of 441"

    @staticmethod
    def test_test_without_summary_returns_none(plugin: CMakeGenerator, mocker: Any) -> None:
        """No summary line means nothing extra to show."""
        mocker.patch("cppython.plugins.cmake.plugin.run_subprocess").return_value.stdout = "No tests were found!!!\n"

        assert plugin.test("dev") is None

    @staticmethod
    def test_plugin_info_reports_selected_cmake_identity(plugin: CMakeGenerator, tmp_path: Any) -> None:
        """Inspection reports the selected CMake identity and minimum version."""
        cmake_binary = tmp_path / "cmake.exe"
        tool = EffectiveCMakeTool(
            executable=cmake_binary,
            version="4.5.0rc2",
            origin="environment",
            capabilities=("import-std-without-experimental-gate",),
            fingerprint="a" * 64,
        )
        plugin.data.cmake_binary = cmake_binary
        plugin.data.cmake_tool = tool
        plugin.data.minimum_version = "4.5.0rc1"

        report = plugin.plugin_info()

        assert report.configuration["cmake_binary"] == str(cmake_binary)
        assert report.configuration["minimum_version"] == "4.5.0rc1"
        assert report.configuration["cmake_tool"] == {
            "executable": str(cmake_binary),
            "version": "4.5.0rc2",
            "origin": "environment",
            "capabilities": ["import-std-without-experimental-gate"],
            "fingerprint": "a" * 64,
        }

    @staticmethod
    @pytest.mark.parametrize("command", ["_cmake_command", "_ctest_command"])
    def test_commands_never_fall_back_to_path(plugin: CMakeGenerator, command: str) -> None:
        """Without a selected CMake, commands fail instead of invoking bare PATH tools."""
        plugin.data.cmake_binary = None
        plugin.data.cmake_tool = None

        with pytest.raises(CMakeToolResolutionError, match="not resolved from PATH"):
            getattr(plugin, command)()

    @staticmethod
    def test_ctest_must_be_from_the_selected_cmake_distribution(plugin: CMakeGenerator, tmp_path: Any) -> None:
        """CTest is never resolved from PATH when a CMake distribution was selected."""
        plugin.data.cmake_binary = tmp_path / "cmake.exe"

        with pytest.raises(FileNotFoundError, match="Could not find CTest alongside"):
            plugin._ctest_command()

    @staticmethod
    @pytest.mark.parametrize("operation", ["test", "bench"])
    def test_test_and_bench_use_ctest_from_the_selected_distribution(
        plugin: CMakeGenerator,
        mocker: Any,
        tmp_path: Any,
        operation: str,
    ) -> None:
        """CTest operations use the selected CMake distribution, including paths with spaces."""
        cmake_directory = tmp_path / "CMake Tools"
        cmake_directory.mkdir()
        cmake_binary = cmake_directory / "cmake.exe"
        ctest_binary = cmake_directory / "ctest.exe"
        ctest_binary.touch()
        tool = EffectiveCMakeTool(
            executable=cmake_binary,
            version="4.5.0rc2",
            origin="environment",
            capabilities=("import-std-without-experimental-gate",),
            fingerprint="a" * 64,
        )
        plugin.data.cmake_binary = cmake_binary
        plugin.data.cmake_tool = tool
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")
        run_spy.return_value.stdout = ""
        verify_spy = mocker.patch("cppython.plugins.cmake.plugin.verify_cmake_tool")

        getattr(plugin, operation)("dev")

        assert run_spy.call_args.args[0][0] == str(ctest_binary)
        verify_spy.assert_called_once_with(tool)

    @staticmethod
    def test_configure_requires_configuration(plugin: CMakeGenerator, mocker: Any) -> None:
        """A missing CMake default is reported distinctly so install can remain successful."""
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")

        with pytest.raises(ConfigurationRequiredError):
            plugin.configure()

        run_spy.assert_not_called()

    @staticmethod
    def test_run_selects_executable_from_requested_build_configuration(
        plugin: CMakeGenerator,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """A target in another configuration cannot win by filesystem traversal order."""
        (tmp_path / "CMakePresets.json").write_text(
            json.dumps(
                {
                    "version": 9,
                    "configurePresets": [{"name": "dev", "binaryDir": "${sourceDir}/build"}],
                    "buildPresets": [
                        {
                            "name": "dev-release",
                            "configurePreset": "dev",
                            "configuration": "Release",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        reply_dir = tmp_path / "build" / ".cmake" / "api" / "v1" / "reply"
        reply_dir.mkdir(parents=True)
        (reply_dir / "index-test.json").write_text(
            json.dumps({"reply": {"codemodel-v2": {"jsonFile": "codemodel-v2-test.json"}}}),
            encoding="utf-8",
        )
        (reply_dir / "codemodel-v2-test.json").write_text(
            json.dumps(
                {
                    "configurations": [
                        {
                            "name": "Debug",
                            "targets": [{"jsonFile": "viewer-debug.json"}],
                        },
                        {
                            "name": "Release",
                            "targets": [{"jsonFile": "viewer-release.json"}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (reply_dir / "viewer-debug.json").write_text(
            json.dumps(
                {
                    "name": "viewer",
                    "type": "EXECUTABLE",
                    "artifacts": [{"path": "Debug/viewer.exe"}],
                }
            ),
            encoding="utf-8",
        )
        (reply_dir / "viewer-release.json").write_text(
            json.dumps(
                {
                    "name": "viewer",
                    "type": "EXECUTABLE",
                    "artifacts": [
                        {"path": "Release/viewer.exe"},
                        {"path": "Release/viewer.pdb"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        index = mocker.patch(
            "cppython.plugins.cmake.plugin.PresetIndex",
            return_value=PresetIndex(tmp_path),
        )
        mocker.patch.object(plugin, "_cmake_command", return_value="cmake")
        mocker.patch.object(plugin, "_run")
        run_spy = mocker.patch("cppython.plugins.cmake.plugin.run_subprocess")

        plugin.run("viewer", "dev-release")

        index.assert_called_once_with(plugin.core_data.project_data.project_root)
        assert run_spy.call_args.args[0] == [str(tmp_path / "build" / "Release" / "viewer.exe")]
