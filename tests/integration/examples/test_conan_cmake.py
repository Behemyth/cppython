"""Integration tests for the conan and CMake project variation.

This module contains integration tests for projects that use conan and CMake.
The tests ensure that the projects build, configure, and execute correctly.
"""

import os
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from tomllib import loads

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from cppython.build import build_sdist, build_wheel
from cppython.core.schema import ProjectConfiguration
from cppython.project import Project

pytest_plugins = ['tests.fixtures.example', 'tests.fixtures.conan', 'tests.fixtures.cmake']

# C++20 modules require Ninja or Visual Studio generator, not Unix Makefiles
_skip_modules_test = pytest.mark.skipif(
    sys.platform != 'win32', reason='C++20 modules require Ninja or Visual Studio generator, not Unix Makefiles.'
)

# On Windows (multi-config generators), use 'default' preset
# On Linux/Mac (single-config generators), use 'default-release' because CMAKE_BUILD_TYPE is required
_cmake_preset = 'default' if sys.platform == 'win32' else 'default-release'


class TestConanCMake:
    """Test project variation of conan and CMake"""

    @staticmethod
    def _cmake_binary() -> str:
        """Select the configured executable, honoring the CI and local override."""
        with Path('pyproject.toml').open('rb') as file:
            data = tomllib.load(file)
        configured = (
            data.get('tool', {}).get('cppython', {}).get('generators', {}).get('cmake', {}).get('cmake_binary', 'cmake')
        )
        return os.environ.get('CMAKE_BINARY', configured)

    @staticmethod
    def _create_project(skip_upload: bool = True) -> Project:
        """Create a project instance with common configuration."""
        project_root = Path.cwd()
        config = ProjectConfiguration(project_root=project_root, version=None, verbosity=2, debug=True)

        pyproject_path = project_root / 'pyproject.toml'
        pyproject_data = loads(pyproject_path.read_text(encoding='utf-8'))

        if skip_upload:
            TestConanCMake._ensure_conan_config(pyproject_data)
            pyproject_data['tool']['cppython']['providers']['conan']['skip_upload'] = True

        return Project(config, pyproject_data)

    @staticmethod
    def _list_project_presets(cmake_binary: str) -> bytes:
        """Presets are available before preparation and remain owned by the project."""
        contents = Path('CMakePresets.json').read_bytes()
        assert b'conan' not in contents.lower()
        assert b'vcpkg' not in contents.lower()
        result = subprocess.run(
            [cmake_binary, '--list-presets=configure'], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f'CMake preset listing failed: {result.stderr}'
        assert f'"{_cmake_preset}"' in result.stdout
        assert '"default-debug"' in result.stdout
        return contents

    @staticmethod
    def _run_cmake_configure(cmake_binary: str, preset: str = _cmake_preset, build_type: str = 'Release') -> None:
        """Run CMake configuration and assert success.

        Args:
            cmake_binary: Path or command name for the CMake binary to use
            preset: Project-owned configure preset
            build_type: Conan dependency build type for this legacy example
        """
        toolchain = Path('build') / 'conan' / build_type / 'generators' / 'conan_toolchain.cmake'
        assert toolchain.is_file(), f'Conan toolchain not found: {toolchain}'
        result = subprocess.run(
            [cmake_binary, f'--preset={preset}', f'-DCMAKE_TOOLCHAIN_FILE={toolchain.resolve()}'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f'CMake configuration failed: {result.stderr}'

    @staticmethod
    def _run_cmake_build(cmake_binary: str) -> None:
        """Run CMake build and assert success.

        Args:
            cmake_binary: Path or command name for the CMake binary to use
        """
        result = subprocess.run(
            [cmake_binary, '--build', 'build', '--config', 'Release'], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f'CMake build failed: {result.stderr}'

    @staticmethod
    def _verify_build_artifacts() -> Path:
        """Verify basic build artifacts exist and return build path."""
        build_path = Path('build').absolute()
        assert (build_path / 'CMakeCache.txt').exists(), f'CMakeCache.txt not found in {build_path}'
        return build_path

    @staticmethod
    def _ensure_conan_config(pyproject_data: dict) -> None:
        """Helper method to ensure Conan configuration exists in pyproject data"""
        if 'tool' not in pyproject_data:
            pyproject_data['tool'] = {}
        if 'cppython' not in pyproject_data['tool']:
            pyproject_data['tool']['cppython'] = {}
        if 'providers' not in pyproject_data['tool']['cppython']:
            pyproject_data['tool']['cppython']['providers'] = {}
        if 'conan' not in pyproject_data['tool']['cppython']['providers']:
            pyproject_data['tool']['cppython']['providers']['conan'] = {}

    @staticmethod
    def _verify_conan_package_configs(package_name: str, expected_build_types: list[str]) -> None:
        """Verify that specified build types exist in the Conan local cache.

        Args:
            package_name: Name of the package to check (e.g., 'mathutils')
            expected_build_types: List of build types that should exist (e.g., ['Release', 'Debug'])
        """
        result = subprocess.run(
            ['conan', 'list', f'{package_name}/*:*'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f'Failed to list Conan packages: {result.stderr}'

        output = result.stdout
        for build_type in expected_build_types:
            assert f'build_type: {build_type}' in output, (
                f'{build_type} configuration not found in Conan cache for {package_name}. Output: {output}'
            )

    @staticmethod
    def test_simple(example_runner: CliRunner) -> None:
        """Simple project"""
        cmake_binary = TestConanCMake._cmake_binary()
        original_presets = TestConanCMake._list_project_presets(cmake_binary)

        # Create project and install dependencies
        project = TestConanCMake._create_project(skip_upload=False)
        project.install()
        assert Path('CMakePresets.json').read_bytes() == original_presets

        # Configure and verify build
        TestConanCMake._run_cmake_configure(cmake_binary)
        TestConanCMake._verify_build_artifacts()

        for preset, build_type in (('default-release', 'Release'), ('default-debug', 'Debug')):
            TestConanCMake._run_cmake_configure(cmake_binary, preset, build_type)
            build = subprocess.run(
                [cmake_binary, '--build', '--preset', preset], capture_output=True, text=True, check=False
            )
            assert build.returncode == 0, f'{preset} build failed: {build.stderr}'

        # Test publishing with skip_upload enabled
        publish_project = TestConanCMake._create_project(skip_upload=True)
        publish_project.publish()

    @staticmethod
    @_skip_modules_test
    def test_library(example_runner: CliRunner) -> None:
        """Test library creation and packaging workflow"""
        cmake_binary = TestConanCMake._cmake_binary()
        original_presets = TestConanCMake._list_project_presets(cmake_binary)

        # Create project and install dependencies
        project = TestConanCMake._create_project(skip_upload=False)
        project.install()
        assert Path('CMakePresets.json').read_bytes() == original_presets

        # Configure, build, and verify
        TestConanCMake._run_cmake_configure(cmake_binary)
        TestConanCMake._run_cmake_build(cmake_binary)
        build_path = TestConanCMake._verify_build_artifacts()

        # Verify library files exist (platform-specific)
        lib_files = list(build_path.glob('**/libmathutils.*')) + list(build_path.glob('**/mathutils.lib'))
        assert len(lib_files) > 0, f'No library files found in {build_path}'

        # Package the library to local cache
        publish_project = TestConanCMake._create_project(skip_upload=True)
        publish_project.publish()

        # Verify both Debug and Release configurations were published and consumed successfully
        # conan create already runs test_package for each build type, verifying consumption works
        TestConanCMake._verify_conan_package_configs('mathutils', ['Release', 'Debug'])

    @staticmethod
    @pytest.mark.parametrize('source_distribution', [False, True], ids=['checkout', 'sdist'])
    def test_extension(
        example_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        source_distribution: bool,
    ) -> None:
        """Build a usable extension from a clean checkout or an extracted sdist."""
        if source_distribution:
            sdist_directory = tmp_path / 'sdist'
            sdist_directory.mkdir()
            sdist_name = build_sdist(str(sdist_directory))
            extraction_directory = tmp_path / 'extracted'
            with tarfile.open(sdist_directory / sdist_name) as archive:
                archive.extractall(extraction_directory, filter='data')
            source_roots = list(extraction_directory.iterdir())
            assert len(source_roots) == 1
            monkeypatch.chdir(source_roots[0])
            assert Path('PKG-INFO').is_file()

        assert not Path('build/generators').exists()
        install_spy = mocker.spy(Project, 'install')
        configure_spy = mocker.spy(Project, 'configure')

        # Create dist directory for the wheel
        dist_path = Path('dist')
        dist_path.mkdir(exist_ok=True)

        # Build the wheel using the cppython.build backend directly
        wheel_name = build_wheel(str(dist_path))

        install_spy.assert_called_once()
        configure_spy.assert_not_called()

        # Verify wheel was created
        wheel_path = dist_path / wheel_name
        assert wheel_path.exists(), f'Wheel not created at {wheel_path}'

        # Extract and test the extension
        install_path = Path('install_target')
        install_path.mkdir(exist_ok=True)

        with zipfile.ZipFile(wheel_path, 'r') as whl:
            whl.extractall(install_path)

        # Test the installed extension by adding install_target to path
        test_code = (
            f'import sys; sys.path.insert(0, {str(install_path)!r}); '
            "import example_extension; print(example_extension.format_greeting('Test'))"
        )
        test_result = subprocess.run(
            [sys.executable, '-c', test_code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert test_result.returncode == 0, f'Extension test failed: {test_result.stderr}'
        assert 'Hello, Test!' in test_result.stdout, f'Unexpected output: {test_result.stdout}'
