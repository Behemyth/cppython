"""Tests that CMake receives every installed build type of a package with its own CMake package files."""

import shutil
import subprocess
from pathlib import Path

import pytest

from cppython.plugins.conan import build_types

_TARGETS = """\
get_filename_component(_IMPORT_PREFIX "${CMAKE_CURRENT_LIST_FILE}" PATH)
get_filename_component(_IMPORT_PREFIX "${_IMPORT_PREFIX}" PATH)
get_filename_component(_IMPORT_PREFIX "${_IMPORT_PREFIX}" PATH)
get_filename_component(_IMPORT_PREFIX "${_IMPORT_PREFIX}" PATH)

add_library(synodic::probe STATIC IMPORTED)

# Load information for each installed configuration.
file(GLOB _cmake_config_files "${CMAKE_CURRENT_LIST_DIR}/probeTargets-*.cmake")
foreach(_cmake_config_file IN LISTS _cmake_config_files)
  include("${_cmake_config_file}")
endforeach()
unset(_cmake_config_file)
unset(_cmake_config_files)

include("${CMAKE_CURRENT_LIST_DIR}/cxx-modules/cxx-modules-probeTargets.cmake")

set(_IMPORT_PREFIX)
unset(_cmake_import_check_targets)
unset(_cmake_import_check_files_for_synodic::probe)
"""

_MODULES = """\
# Load information for each installed configuration.
file(GLOB _cmake_cxx_module_includes "${CMAKE_CURRENT_LIST_DIR}/cxx-modules-probeTargets-*.cmake")
foreach(_cmake_cxx_module_include IN LISTS _cmake_cxx_module_includes)
  include("${_cmake_cxx_module_include}")
endforeach()
unset(_cmake_cxx_module_include)
unset(_cmake_cxx_module_includes)
"""


def _install(cache: Path, build_type: str) -> Path:
    """Write what CMake's install(EXPORT) leaves in one Conan package folder, and return its package directory."""
    prefix = cache / build_type.lower() / 'p'
    package = prefix / 'lib' / 'cmake' / 'probe'
    (package / 'cxx-modules').mkdir(parents=True)
    (prefix / 'lib' / 'probe.lib').write_text(build_type, encoding='utf-8')
    upper = build_type.upper()
    (package / 'probeConfig.cmake').write_text(
        'include("${CMAKE_CURRENT_LIST_DIR}/probeTargets.cmake")\n', encoding='utf-8'
    )
    (package / 'probeConfigVersion.cmake').write_text(
        'set(PACKAGE_VERSION "1.0")\nset(PACKAGE_VERSION_COMPATIBLE TRUE)\n', encoding='utf-8'
    )
    (package / 'probeTargets.cmake').write_text(_TARGETS, encoding='utf-8')
    (package / f'probeTargets-{build_type.lower()}.cmake').write_text(
        f'set_property(TARGET synodic::probe APPEND PROPERTY IMPORTED_CONFIGURATIONS {upper})\n'
        f'set_target_properties(synodic::probe PROPERTIES IMPORTED_LOCATION_{upper} '
        '"${_IMPORT_PREFIX}/lib/probe.lib")\n'
        'list(APPEND _cmake_import_check_targets synodic::probe)\n'
        'list(APPEND _cmake_import_check_files_for_synodic::probe "${_IMPORT_PREFIX}/lib/probe.lib")\n',
        encoding='utf-8',
    )
    (package / 'cxx-modules' / 'cxx-modules-probeTargets.cmake').write_text(_MODULES, encoding='utf-8')
    (package / 'cxx-modules' / f'cxx-modules-probeTargets-{build_type}.cmake').write_text(
        f'include("${{CMAKE_CURRENT_LIST_DIR}}/target-probe-{build_type}.cmake")\n', encoding='utf-8'
    )
    (package / 'cxx-modules' / f'target-probe-{build_type}.cmake').write_text(
        f'set_property(TARGET synodic::probe PROPERTY IMPORTED_CXX_MODULES_{upper} '
        '"probe=${_IMPORT_PREFIX}/lib/cmake/probe/probe.ixx")\n',
        encoding='utf-8',
    )
    return package


def _conan_paths(output: Path, selected: Path, installed: list[Path]) -> Path:
    """Write the search paths Conan generates after installing every build type."""
    paths = output / 'generators' / 'conan_cmakedeps_paths.cmake'
    paths.parent.mkdir(parents=True)
    lines = [
        'set(CMAKE_FIND_PACKAGE_PREFER_CONFIG ON)',
        'set(generated_DIR "${CMAKE_CURRENT_LIST_DIR}")',
        f'set(probe_DIR "{selected.as_posix()}")',
        *(f'list(APPEND CONAN_probe_DIR_MULTI "{folder.as_posix()}")' for folder in installed),
    ]
    paths.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return paths


def test_single_build_type_needs_no_package(tmp_path: Path) -> None:
    """Conan's own selection is complete when a package has one build type or generated package files."""
    output = tmp_path / 'conan'
    release = _install(tmp_path / 'cache', 'Release')
    paths = _conan_paths(output, release, [release])

    search = build_types.write_search_paths(paths, output)

    assert search == output / build_types.SEARCH_PATHS
    assert search.read_text(encoding='utf-8') == (f'{build_types.HEADER}include([=[{paths.resolve().as_posix()}]=])\n')
    assert not (output / build_types.PACKAGES).exists()


def test_every_build_type_is_imported_and_stale_packages_are_removed(tmp_path: Path) -> None:
    """The package loads Conan's selected folder, then each other folder's files under that folder's prefix."""
    output = tmp_path / 'conan'
    release = _install(tmp_path / 'cache', 'Release')
    debug = _install(tmp_path / 'cache', 'Debug')
    paths = _conan_paths(output, debug, [release, debug])
    stale = output / build_types.PACKAGES / 'removed' / 'removedConfig.cmake'
    stale.parent.mkdir(parents=True)
    stale.write_text('stale', encoding='utf-8')

    search = build_types.write_search_paths(paths, output)

    package = output / build_types.PACKAGES / 'probe'
    assert search.read_text(encoding='utf-8').splitlines()[-1] == f'set(probe_DIR [=[{package.resolve().as_posix()}]=])'
    assert not stale.exists()
    config = (package / 'probeConfig.cmake').read_text(encoding='utf-8')
    assert config.startswith(build_types.HEADER)
    prefix = (tmp_path / 'cache' / 'release' / 'p').resolve().as_posix()
    expected = [
        f'include([=[{debug.resolve().as_posix()}/probeConfig.cmake]=])',
        f'set(_IMPORT_PREFIX [=[{prefix}]=])',
        f'include([=[{prefix}/lib/cmake/probe/probeTargets-release.cmake]=])',
        f'include([=[{prefix}/lib/cmake/probe/cxx-modules/cxx-modules-probeTargets-Release.cmake]=])',
    ]
    positions = [config.index(line) for line in expected]
    assert positions == sorted(positions)
    assert 'debug.cmake' not in config
    assert (package / 'probeConfigVersion.cmake').read_text(encoding='utf-8') == (
        f'{build_types.HEADER}include([=[{debug.resolve().as_posix()}/probeConfigVersion.cmake]=])\n'
    )

    build_types.clear(output)
    assert not search.exists()
    assert not (output / build_types.PACKAGES).exists()


def test_package_without_cmake_exports_is_left_to_conan(tmp_path: Path) -> None:
    """Hand-written package files cannot be completed, so Conan's selection stands."""
    output = tmp_path / 'conan'
    folders = []
    for build_type in ('release', 'debug'):
        folder = tmp_path / 'cache' / build_type / 'p' / 'cmake'
        folder.mkdir(parents=True)
        (folder / 'probeConfig.cmake').write_text('add_library(probe INTERFACE IMPORTED)\n', encoding='utf-8')
        folders.append(folder)
    paths = _conan_paths(output, folders[1], folders)

    search = build_types.write_search_paths(paths, output)

    assert 'probe_DIR' not in search.read_text(encoding='utf-8')
    assert not (output / build_types.PACKAGES).exists()


@pytest.mark.skipif(shutil.which('cmake') is None, reason='CMake is required')
def test_cmake_consumer_sees_every_build_type(tmp_path: Path) -> None:
    """A real find_package() gets both build types, a satisfied version check, and no leaked variables."""
    output = tmp_path / 'conan'
    release = _install(tmp_path / 'cache', 'Release')
    debug = _install(tmp_path / 'cache', 'Debug')
    search = build_types.write_search_paths(_conan_paths(output, debug, [release, debug]), output)
    cache = (tmp_path / 'cache').resolve().as_posix()
    source = tmp_path / 'consumer'
    source.mkdir()
    (source / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.25)\n'
        'project(consumer LANGUAGES NONE)\n'
        'find_package(probe 1.0 REQUIRED)\n'
        'get_target_property(configurations synodic::probe IMPORTED_CONFIGURATIONS)\n'
        'get_target_property(debug synodic::probe IMPORTED_LOCATION_DEBUG)\n'
        'get_target_property(release synodic::probe IMPORTED_LOCATION_RELEASE)\n'
        'get_target_property(modules synodic::probe IMPORTED_CXX_MODULES_RELEASE)\n'
        'list(SORT configurations)\n'
        'if(NOT configurations STREQUAL "DEBUG;RELEASE"\n'
        f'    OR NOT debug STREQUAL "{cache}/debug/p/lib/probe.lib"\n'
        f'    OR NOT release STREQUAL "{cache}/release/p/lib/probe.lib"\n'
        f'    OR NOT modules STREQUAL "probe={cache}/release/p/lib/cmake/probe/probe.ixx")\n'
        '  message(FATAL_ERROR "Incomplete import: ${configurations} ${debug} ${release} ${modules}")\n'
        'endif()\n'
        'if(DEFINED _IMPORT_PREFIX OR DEFINED _cmake_import_check_targets)\n'
        '  message(FATAL_ERROR "The generated package leaked import variables")\n'
        'endif()\n',
        encoding='utf-8',
    )

    result = subprocess.run(
        [
            'cmake',
            '-S',
            str(source),
            '-B',
            str(tmp_path / 'build'),
            f'-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={search.as_posix()}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
