"""Tests for the PEP 517 CMake dependency handoff."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from cppython.build import backend
from cppython.build.prepare import BuildPreparationResult
from cppython.plugins.cmake.resolution import CMakeToolResolutionError
from cppython.plugins.cmake.schema import CMakeSyncData
from cppython.utility.utility import TypeName


@pytest.mark.parametrize('source', ['define', 'args', 'environment'])
def test_dependency_include_composes_with_user_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """The provider include augments the effective user include and toolchain."""
    project = tmp_path / 'source with spaces'
    project.mkdir()
    user_include = project / 'user-include.cmake'
    user_toolchain = project / 'user-toolchain.cmake'
    dependency = project / 'dependency.cmake'
    ready = project / 'ready'
    user_include.write_text('set(USER_INCLUDE_LOADED TRUE)\n', encoding='utf-8')
    user_toolchain.write_text('set(USER_TOOLCHAIN_LOADED TRUE)\n', encoding='utf-8')
    dependency.write_text('set(PROVIDER_INCLUDE_LOADED TRUE)\n', encoding='utf-8')
    ready.touch()
    (project / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.20)\n'
        'project(probe LANGUAGES NONE)\n'
        'if(NOT USER_INCLUDE_LOADED OR NOT USER_TOOLCHAIN_LOADED OR NOT PROVIDER_INCLUDE_LOADED)\n'
        '  message(FATAL_ERROR "A required include or toolchain was lost")\n'
        'endif()\n',
        encoding='utf-8',
    )
    cmake = shutil.which('cmake')
    assert cmake is not None
    sync = CMakeSyncData(
        provider_name=TypeName('test'),
        cmake_binary=Path(cmake),
        dependency_file=dependency,
        dependency_ready_file=ready,
    )
    monkeypatch.setattr(backend, 'developer_environment', lambda: None)
    monkeypatch.delenv('CMAKE_EXECUTABLE', raising=False)
    include_arg = f'-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={user_include.as_posix()}'
    if source == 'environment':
        monkeypatch.setenv('CMAKE_ARGS', shlex.join([include_arg]))
        command_include: list[str] = []
    else:
        monkeypatch.delenv('CMAKE_ARGS', raising=False)
        command_include = [include_arg]
    original_args = os.environ.get('CMAKE_ARGS')

    with backend._cmake_backend_environment(sync):
        assert os.environ['CMAKE_EXECUTABLE'] == cmake
        args = shlex.split(os.environ['CMAKE_ARGS'])
        command = [
            cmake,
            '-S',
            str(project),
            '-B',
            str(tmp_path / 'build'),
            f'-DCMAKE_TOOLCHAIN_FILE={user_toolchain.as_posix()}',
            *command_include,
            *args,
        ]
        subprocess.run(command, capture_output=True, text=True, check=True)
        subprocess.run(command, capture_output=True, text=True, check=True)

    cache = (tmp_path / 'build' / 'CMakeCache.txt').read_text(encoding='utf-8')
    entry = next(line for line in cache.splitlines() if line.startswith('CMAKE_PROJECT_TOP_LEVEL_INCLUDES:'))
    assert entry.count(dependency.as_posix()) == 1
    assert user_include.as_posix() in entry
    assert os.environ.get('CMAKE_ARGS') == original_args
    assert 'CMAKE_EXECUTABLE' not in os.environ


def test_missing_dependency_or_ready_marker_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing or interrupted provider output never becomes a successful wheel build."""
    dependency = tmp_path / 'dependency.cmake'
    ready = tmp_path / 'ready'
    sync = CMakeSyncData(provider_name=TypeName('test'), dependency_file=dependency, dependency_ready_file=ready)
    monkeypatch.setattr(backend, 'developer_environment', lambda: None)

    with pytest.raises(FileNotFoundError, match='search file'), backend._cmake_backend_environment(sync):
        pytest.fail('the backend must not run')

    dependency.touch()
    with pytest.raises(FileNotFoundError, match='not ready'), backend._cmake_backend_environment(sync):
        pytest.fail('the backend must not run')


def test_conflicting_cmake_executable_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different explicit backend CMake does not silently bypass the selected tool."""
    sync = CMakeSyncData(provider_name=TypeName('test'), cmake_binary=tmp_path / 'selected-cmake')
    monkeypatch.setattr(backend, 'developer_environment', lambda: None)
    monkeypatch.setenv('CMAKE_EXECUTABLE', str(tmp_path / 'other-cmake'))
    with (
        pytest.raises(CMakeToolResolutionError, match='CMAKE_EXECUTABLE conflicts'),
        backend._cmake_backend_environment(sync),
    ):
        pytest.fail('the backend must not run')


@pytest.mark.parametrize('hook', ['build_wheel', 'build_editable'])
def test_backend_preserves_settings_and_restores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, hook: str
) -> None:
    """Both build hooks keep caller settings and restore temporary environment state."""
    dependency = tmp_path / 'dependency.cmake'
    dependency.touch()
    cmake = tmp_path / 'cmake'
    sync = CMakeSyncData(provider_name=TypeName('test'), cmake_binary=cmake, dependency_file=dependency)
    monkeypatch.setattr(backend, 'prepare_build', lambda _source: BuildPreparationResult(sync_data=sync))
    monkeypatch.setattr(
        backend,
        'developer_environment',
        lambda: {'CMAKE_ARGS': '-DSTALE=ON', 'CMAKE_EXECUTABLE': 'stale-cmake'},
    )
    monkeypatch.setenv('CMAKE_ARGS', '-DUSER_FLAG=ON')
    monkeypatch.delenv('CMAKE_EXECUTABLE', raising=False)
    delegated = mocker.Mock()
    initial_files: list[Path] = []

    def run_backend(_directory: str, settings: dict[str, Any], _metadata: str | None) -> str:
        assert settings == {'cmake.args': ['-DPROJECT_FLAG=ON']}
        assert os.environ['CMAKE_EXECUTABLE'] == str(cmake)
        args = shlex.split(os.environ['CMAKE_ARGS'])
        assert args[0] == '-DUSER_FLAG=ON'
        assert args[-2] == '-C'
        initial_file = Path(args[-1])
        assert initial_file.is_file()
        initial_files.append(initial_file)
        return 'example.whl'

    getattr(delegated, hook).side_effect = run_backend
    monkeypatch.setattr(backend, '_get_backend', lambda _is_meson: delegated)
    settings = {'cmake.args': ['-DPROJECT_FLAG=ON']}
    assert getattr(backend, hook)(str(tmp_path), settings) == 'example.whl'
    assert settings == {'cmake.args': ['-DPROJECT_FLAG=ON']}
    assert initial_files
    assert not initial_files[0].exists()
    assert os.environ['CMAKE_ARGS'] == '-DUSER_FLAG=ON'
    assert 'CMAKE_EXECUTABLE' not in os.environ
