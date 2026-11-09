"""Unit test the provider plugin"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cppython.core.identity import BuildIdentity
from cppython.plugins.cmake.plugin import CMakeGenerator
from cppython.plugins.cmake.presets import PresetIndex
from cppython.plugins.cmake.resolution import CMakeToolResolutionError
from cppython.plugins.cmake.schema import (
    CMakeConfiguration,
    CMakeSyncData,
    EffectiveCMakeTool,
)
from cppython.test.pytest.contracts import GeneratorUnitTestContract
from cppython.utility.exception import ConfigurationRequiredError, NotSupportedError
from cppython.utility.utility import TypeName

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
        assert report.managed_files == []

    @staticmethod
    def test_sync_preserves_project_owned_presets(plugin: CMakeGenerator) -> None:
        """CMake sync stores toolchains without writing or rewriting preset files."""
        preset_file = plugin.data.preset_file
        preset_file.parent.mkdir(parents=True, exist_ok=True)
        original = '{"version": 10, "configurePresets": []}\n'
        preset_file.write_text(original, encoding="utf-8")
        toolchain_file = plugin.core_data.project_data.project_root / "build" / "toolchain.cmake"

        plugin.sync(CMakeSyncData(provider_name=TypeName("test-provider"), toolchain_files={"Debug": toolchain_file}))

        assert preset_file.read_text(encoding="utf-8") == original
        assert plugin._toolchain_files == {"Debug": toolchain_file}
        assert not (
            plugin.core_data.cppython_data.tool_path / "cppython" / "CPPython.json"
        ).exists()

    @staticmethod
    def test_sync_writes_provider_neutral_identity_glue(plugin: CMakeGenerator) -> None:
        """Only generated build output contains the provider-specific search file."""
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
        build_path = plugin.core_data.cppython_data.build_path
        search_file = build_path / identity.name / "conan" / "generators" / "conan_cmakedeps_paths.cmake"
        glue = build_path / identity.name / "deps" / "dependencies.cmake"

        plugin.sync(CMakeSyncData(provider_name=TypeName("conan"), dependency_file=search_file))

        assert glue.is_file()
        assert search_file.as_posix() in glue.read_text(encoding="utf-8")
        assert not plugin.data.preset_file.exists()

        plugin.sync(CMakeSyncData(provider_name=TypeName("other")))
        assert not glue.exists()

    @staticmethod
    def test_sync_refuses_to_overwrite_unmanaged_dependency_glue(plugin: CMakeGenerator) -> None:
        """An existing user-owned file in the build tree is not silently replaced."""
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
        glue = plugin.core_data.cppython_data.build_path / identity.name / "deps" / "dependencies.cmake"
        glue.parent.mkdir(parents=True, exist_ok=True)
        glue.write_text("# user-owned\n", encoding="utf-8")

        with pytest.raises(FileExistsError, match="not generated by CPPython"):
            plugin.sync(CMakeSyncData(provider_name=TypeName("test-provider"), dependency_file=glue.parent / "other"))
        assert glue.read_text(encoding="utf-8") == "# user-owned\n"

    @staticmethod
    def test_identity_rejects_unvalidated_generated_toolchain(plugin: CMakeGenerator) -> None:
        """A toolchain cannot be silently treated as a dependency search-path include."""
        plugin.core_data.cppython_data.identity = BuildIdentity(
            name="windows-x64-msvc",
            os="windows",
            arch="x64",
            compiler="msvc",
            standard_library="msvc",
            configurations=["Debug"],
            dependency_build_types={"Debug": "Debug"},
        )
        with pytest.raises(NotSupportedError, match="not yet supported"):
            plugin.sync(CMakeSyncData(provider_name=TypeName("vcpkg"), toolchain_file=Path("vcpkg.cmake")))

    @staticmethod
    def test_dependency_glue_rejects_an_incomplete_install(plugin: CMakeGenerator) -> None:
        """CMake cannot use stale output after a failed dependency installation."""
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
        directory = plugin.core_data.cppython_data.build_path / identity.name
        dependency = directory / "conan" / "paths.cmake"
        ready = directory / "conan" / "ready.json"
        dependency.parent.mkdir(parents=True, exist_ok=True)
        dependency.write_text("set(LOADED TRUE)\n", encoding="utf-8")
        plugin.sync(
            CMakeSyncData(
                provider_name=TypeName("test-provider"),
                dependency_file=dependency,
                dependency_ready_file=ready,
            )
        )
        glue = directory / "deps" / "dependencies.cmake"
        failed = subprocess.run(
            [plugin._cmake_command(), "-P", str(glue)], capture_output=True, text=True, check=False
        )
        assert failed.returncode != 0
        assert "dependencies" in failed.stderr.lower()
        assert "incomplete" in failed.stderr.lower()

        ready.touch()
        subprocess.run([plugin._cmake_command(), "-P", str(glue)], capture_output=True, text=True, check=True)

    @staticmethod
    def test_identity_default_and_dependency_build_type(plugin: CMakeGenerator) -> None:
        """Default build/test and local-source builds follow the selected identity's ABI mapping."""
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
        root = plugin.core_data.project_data.project_root
        (root / "CMakePresets.json").write_text(
            json.dumps(
                {
                    "version": 10,
                    "configurePresets": [
                        {
                            "name": "identity-base",
                            "hidden": True,
                            "cacheVariables": {"SYNODIC_BUILD_IDENTITY": identity.name},
                        },
                        {"name": identity.name, "inherits": "identity-base"},
                        {
                            "name": f"{identity.name}-debug",
                            "inherits": "identity-base",
                            "cacheVariables": {"CMAKE_BUILD_TYPE": "Debug"},
                        },
                    ],
                    "buildPresets": [
                        {
                            "name": f"{identity.name}-multi-development",
                            "configurePreset": identity.name,
                            "configuration": "Development",
                        },
                        {"name": f"{identity.name}-debug", "configurePreset": f"{identity.name}-debug"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        assert plugin._preset("build", None) == f"{identity.name}-multi-development"
        assert plugin.dependency_build_type() == "Release"
        assert plugin.dependency_build_type(f"{identity.name}-debug") == "Debug"

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
