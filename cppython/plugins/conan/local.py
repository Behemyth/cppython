"""Apply the user's local-source selection to Conan without touching any repository or the Conan home.

Each selected checkout becomes a Conan editable that exists only in this process: the registry is
replaced in memory before every Conan command, so the global `conan editable` registry is ignored
and nothing is written to it. A checkout is installed and configured into its own build tree under
the consumer's build directory, and its CMake package files are exported from that build tree, so the
consumer finds the dependency by its usual `find_package` call and rebuilds it incrementally.
"""

import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conan.api.conan_api import ConanAPI
from conan.api.model import RecipeReference
from conan.internal.api.local.editable import EditablePackages

STAMP_VERSION = 1
_HASHED_FILES = ('pyproject.toml', 'conanfile.py', 'conanfile_base.py')


class LocalSourceError(Exception):
    """A selected local checkout can't be applied"""


@dataclass(frozen=True)
class LocalPackage:
    """A selected local checkout, resolved to the Conan reference it provides"""

    name: str
    path: Path
    reference: str
    requires: tuple[str, ...] = ()

    @property
    def conanfile(self) -> Path:
        """The checkout's Conan recipe"""
        return self.path / 'conanfile.py'


def check_contract(api: ConanAPI) -> None:
    """Fail clearly if this Conan release no longer exposes the editable registry CPPython replaces.

    Raises:
        LocalSourceError: If the registry isn't where CPPython expects it
    """
    helpers = getattr(api, '_api_helpers', None)
    if not isinstance(getattr(helpers, '_editable_packages', None), EditablePackages):
        raise LocalSourceError(
            'This Conan release does not expose the editable registry CPPython uses for local sources. '
            'Install a Conan version within the range CPPython declares.'
        )


def inject(api: ConanAPI, editables: EditablePackages) -> None:
    """Replace the process's editable registry with *editables*.

    Conan rebuilds the registry from the Conan home whenever the API is reinitialized, so this
    is called before every command.
    """
    check_contract(api)
    api._api_helpers._editable_packages = editables  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def output_folder(build_path: Path, build_type: str, name: str) -> Path:
    """The build tree for a local checkout in one build type"""
    return build_path / 'local' / build_type / name


def editables(packages: Sequence[LocalPackage], build_path: Path, build_type: str) -> EditablePackages:
    """An in-memory registry that maps each local package to its checkout and build tree"""
    registry = EditablePackages()
    for package in packages:
        registry.edited_refs[RecipeReference.loads(package.reference)] = {
            'path': str(package.conanfile),
            'output_folder': str(output_folder(build_path, build_type, package.name)),
        }
    return registry


def recipe_reference(api: ConanAPI, conanfile: Path) -> str:
    """The `name/version` a checkout's recipe declares

    Raises:
        LocalSourceError: If the recipe doesn't declare both
    """
    recipe = api.local.inspect(str(conanfile), remotes=None, lockfile=None)
    if not recipe.name or not recipe.version:
        raise LocalSourceError(f'{conanfile} must declare a name and version to be used as a local source.')
    return f'{recipe.name}/{recipe.version}'


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def signature(packages: Sequence[LocalPackage], build_types: Sequence[str]) -> str:
    """A digest of everything that requires reinstalling when it changes.

    Source edits are not included: `cmake --build` picks them up incrementally.
    """
    data = {
        'version': STAMP_VERSION,
        'build_types': list(build_types),
        'packages': [
            {
                'name': package.name,
                'path': str(package.path),
                'reference': package.reference,
                'files': {name: _file_digest(package.path / name) for name in _HASHED_FILES},
            }
            for package in packages
        ],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def stamp_path(build_path: Path) -> Path:
    """Where the last applied selection is recorded"""
    return build_path / 'local' / 'selection.json'


def read_stamp(build_path: Path) -> dict[str, Any] | None:
    """The last applied selection, or None if there isn't a readable one"""
    try:
        data = json.loads(stamp_path(build_path).read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) and data.get('version') == STAMP_VERSION else None


def write_stamp(
    build_path: Path, packages: Sequence[LocalPackage], build_types: Sequence[str], groups: list[str] | None
) -> None:
    """Record the selection an install applied"""
    path = stamp_path(build_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'version': STAMP_VERSION,
        'signature': signature(packages, build_types),
        'groups': groups,
        'packages': [
            {'name': package.name, 'path': str(package.path), 'reference': package.reference} for package in packages
        ],
    }
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def package_config(package: LocalPackage, folder: Path) -> Path | None:
    """The CMake package file a configured checkout exports from its build tree, if it does"""
    found = sorted(folder.glob(f'lib*/cmake/{package.name}/{package.name}Config.cmake'))
    return found[0] if found else None


def configure_command(cmake: str, package: LocalPackage, folder: Path, build_type: str) -> list[str]:
    """The CMake configure command `conan build` would run, limited to one configuration and without tests"""
    generators = folder / 'generators'
    preset: dict[str, Any] = {}
    try:
        presets = json.loads((generators / 'CMakePresets.json').read_text(encoding='utf-8'))
        preset = next(iter(presets.get('configurePresets', [])), {})
    except OSError, ValueError:
        pass

    command = [cmake, '-S', str(package.path), '-B', str(folder)]
    if generator := preset.get('generator'):
        command.extend(['-G', generator])
    command.append(f'-DCMAKE_TOOLCHAIN_FILE={generators / "conan_toolchain.cmake"}')
    for key, raw in preset.get('cacheVariables', {}).items():
        value = raw.get('value') if isinstance(raw, dict) else raw
        if isinstance(value, bool):
            value = 'ON' if value else 'OFF'
        if value is not None:
            command.append(f'-D{key}={value}')
    command.extend([f'-DCMAKE_CONFIGURATION_TYPES={build_type}', f'-DCMAKE_BUILD_TYPE={build_type}'])
    command.append('-DBUILD_TESTING=OFF')
    return command


def checkout_summary(package: LocalPackage) -> str:
    """One line describing a local checkout, for the build summary"""
    commit = _git(package.path, 'rev-parse', '--short', 'HEAD')
    dirty = _git(package.path, 'status', '--porcelain', '--untracked-files=no')
    detail = f' @ {commit}' if commit else ''
    if dirty:
        detail += ' (modified)'
    return f'local {package.name} -> {package.path}{detail}'


def _git(path: Path, *args: str) -> str | None:
    creationflags = 0
    if sys.platform == 'win32':
        if not hasattr(subprocess, 'CREATE_NO_WINDOW'):
            raise RuntimeError('Python does not provide the Windows CREATE_NO_WINDOW subprocess flag.')
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            ['git', '-C', str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=creationflags,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None
