"""Tests for applying local sources to Conan"""

import json
from pathlib import Path

import pytest
from conan.api.conan_api import ConanAPI
from conan.api.model import RecipeReference
from conan.internal.api.local.editable import EditablePackages

from cppython.plugins.conan import local
from cppython.plugins.conan.builder import Builder
from cppython.plugins.conan.local import LocalPackage, LocalSourceError
from cppython.plugins.conan.schema import ConanfileGenerationData


def _package(root: Path, name: str = 'periapsis') -> LocalPackage:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    for file in ('pyproject.toml', 'conanfile.py', 'conanfile_base.py'):
        (path / file).write_text(f'# {file}\n', encoding='utf-8')
    return LocalPackage(name=name, path=path, reference=f'{name}/1.0')


def test_editables_point_at_checkouts_and_build_trees(tmp_path: Path) -> None:
    """Each package maps to its recipe and a per-build-type build tree under the consumer"""
    packages = [_package(tmp_path, 'periapsis'), _package(tmp_path, 'cppaste')]

    registry = local.editables(packages, tmp_path / 'build', 'Debug')

    assert registry.edited_refs == {
        RecipeReference.loads(f'{name}/1.0'): {
            'path': str(tmp_path / name / 'conanfile.py'),
            'output_folder': str(tmp_path / 'build' / 'local' / 'Debug' / name),
        }
        for name in ('periapsis', 'cppaste')
    }


def test_signature_tracks_recipe_inputs_not_sources(tmp_path: Path) -> None:
    """Recipe and manifest edits require a reinstall; source edits only need a rebuild"""
    package = _package(tmp_path)
    before = local.signature([package], ['Release'])

    (package.path / 'module.cppm').write_text('export module periapsis;\n', encoding='utf-8')
    assert local.signature([package], ['Release']) == before

    (package.path / 'pyproject.toml').write_text('# changed\n', encoding='utf-8')
    assert local.signature([package], ['Release']) != before
    assert local.signature([package], ['Release', 'Debug']) != local.signature([package], ['Release'])


def test_stamp_round_trip(tmp_path: Path) -> None:
    """The stamp records the applied signature and the installed groups"""
    package = _package(tmp_path / 'src')

    assert local.read_stamp(tmp_path) is None
    local.write_stamp(tmp_path, [package], ['Release'], ['test'])

    stamp = local.read_stamp(tmp_path)
    assert stamp is not None
    assert stamp['signature'] == local.signature([package], ['Release'])
    assert stamp['groups'] == ['test']
    assert stamp['packages'] == [{'name': 'periapsis', 'path': str(package.path), 'reference': 'periapsis/1.0'}]


def test_configure_command_uses_conan_preset(tmp_path: Path) -> None:
    """Configuration matches what Conan's CMake helper would run, for one configuration without tests"""
    package = _package(tmp_path / 'src')
    folder = tmp_path / 'out'
    (folder / 'generators').mkdir(parents=True)
    preset = {
        'configurePresets': [
            {
                'name': 'conan-default',
                'generator': 'Ninja Multi-Config',
                'cacheVariables': {'CMAKE_POLICY_DEFAULT_CMP0091': 'NEW', 'FLAG': True, 'TYPED': {'value': 'x'}},
            }
        ]
    }
    (folder / 'generators' / 'CMakePresets.json').write_text(json.dumps(preset), encoding='utf-8')

    command = local.configure_command('cmake', package, folder, 'Debug')

    assert command == [
        'cmake',
        '-S',
        str(package.path),
        '-B',
        str(folder),
        '-G',
        'Ninja Multi-Config',
        f'-DCMAKE_TOOLCHAIN_FILE={folder / "generators" / "conan_toolchain.cmake"}',
        '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW',
        '-DFLAG=ON',
        '-DTYPED=x',
        '-DCMAKE_CONFIGURATION_TYPES=Debug',
        '-DCMAKE_BUILD_TYPE=Debug',
        '-DBUILD_TESTING=OFF',
    ]


def test_package_config(tmp_path: Path) -> None:
    """A configured build tree must export the package's CMake config"""
    package = _package(tmp_path / 'src')
    folder = tmp_path / 'out'
    assert local.package_config(package, folder) is None

    config = folder / 'lib' / 'cmake' / 'periapsis' / 'periapsisConfig.cmake'
    config.parent.mkdir(parents=True)
    config.touch()
    assert local.package_config(package, folder) == config


def test_contract_requires_editable_registry() -> None:
    """An API without the replaceable registry is reported instead of silently ignoring the selection"""

    class _API:
        pass

    with pytest.raises(LocalSourceError):
        local.check_contract(_API())  # type: ignore[arg-type]


@pytest.fixture(name='conan_api')
def fixture_conan_api(tmp_path: Path) -> ConanAPI:
    """A Conan API with its own home, so the user's cache and editables are never touched"""
    api = ConanAPI(str(tmp_path / 'home'))
    profiles = tmp_path / 'home' / 'profiles'
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / 'default').write_text(
        '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n'
        'compiler=gcc\ncompiler.version=13\ncompiler.libcxx=libstdc++11\ncompiler.cppstd=23\n',
        encoding='utf-8',
    )
    return api


def _cppython_recipe(folder: Path, name: str) -> Path:
    """A recipe generated from CPPython's own templates"""
    Builder().generate_conanfile(
        folder, ConanfileGenerationData(dependencies=[], dependency_groups={}, name=name, version='1.0')
    )
    return folder / 'conanfile.py'


def test_in_memory_editable_overrides_global_registry(conan_api: ConanAPI, tmp_path: Path) -> None:
    """The selection replaces the global registry and resolves the package from its build tree"""
    selected = _cppython_recipe(tmp_path / 'selected' / 'periapsis', 'periapsis')
    stray = _cppython_recipe(tmp_path / 'stray' / 'periapsis', 'periapsis')
    consumer = tmp_path / 'consumer'
    consumer.mkdir()
    (consumer / 'conanfile.py').write_text(
        'from conan import ConanFile\n\nclass Consumer(ConanFile):\n    requires = "periapsis/1.0"\n',
        encoding='utf-8',
    )
    conan_api.local.editable_add(str(stray.parent))
    registry_file = tmp_path / 'home' / 'editable_packages.json'
    registry = registry_file.read_text(encoding='utf-8')

    package = LocalPackage(
        name='periapsis', path=selected.parent, reference=local.recipe_reference(conan_api, selected)
    )
    local.inject(conan_api, local.editables([package], tmp_path / 'build', 'Release'))

    profile = conan_api.profiles.get_profile([conan_api.profiles.get_default_host()])
    graph = conan_api.graph.load_graph_consumer(
        str(consumer / 'conanfile.py'), None, None, None, None, profile, profile, None, None, False
    )
    graph.report_graph_error()
    conan_api.graph.analyze_binaries(graph, remotes=[])
    conan_api.install.install_binaries(graph, remotes=[])

    dependency = graph.nodes[1]
    assert Path(dependency.conanfile.recipe_folder) == selected.parent
    build_tree = tmp_path / 'build' / 'local' / 'Release' / 'periapsis'
    assert [Path(path) for path in dependency.conanfile.cpp_info.builddirs] == [
        build_tree / 'lib' / 'cmake' / 'periapsis'
    ]
    assert registry_file.read_text(encoding='utf-8') == registry


def test_empty_selection_ignores_global_editables(conan_api: ConanAPI, tmp_path: Path) -> None:
    """With nothing selected, a stray global editable is not used"""
    stray = _cppython_recipe(tmp_path / 'stray' / 'periapsis', 'periapsis')
    conan_api.local.editable_add(str(stray.parent))

    local.inject(conan_api, EditablePackages())

    assert conan_api._api_helpers.editable_packages.edited_refs == {}  # noqa: SLF001
