"""Tests for the Conan provider's local-source lifecycle"""

from logging import getLogger
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cppython.plugins.conan import local
from cppython.plugins.conan.local import LocalPackage
from cppython.plugins.conan.plugin import ConanProvider
from cppython.utility.exception import ProviderInstallationError


def _provider(tmp_path: Path, packages: list[LocalPackage], managed: bool = True) -> Any:
    installs: list[list[str] | None] = []

    def install(groups: list[str] | None = None) -> None:
        installs.append(groups)
        local.write_stamp(tmp_path / 'build', packages, ['Release'], groups)

    return SimpleNamespace(
        core_data=SimpleNamespace(
            cppython_data=SimpleNamespace(build_path=tmp_path / 'build', dependency_groups={'test': []}),
            project_data=SimpleNamespace(project_root=tmp_path),
        ),
        data=SimpleNamespace(build_types=['Release']),
        installs=installs,
        _install_dependencies=install,
        _local_selection=lambda: (packages, managed),
        _local_packages=lambda: packages,
        _logger=getLogger('test.conan'),
    )


def _package(tmp_path: Path) -> LocalPackage:
    path = tmp_path / 'periapsis'
    path.mkdir(exist_ok=True)
    (path / 'conanfile.py').write_text('# recipe\n', encoding='utf-8')
    return LocalPackage(name='periapsis', path=path, reference='periapsis/1.0')


def test_unmanaged_project_never_reinstalls(tmp_path: Path) -> None:
    """Without a selection or a previous local install there is nothing to sync or report"""
    provider = _provider(tmp_path, [], managed=False)

    assert ConanProvider.sync_local_sources(provider) == []
    assert provider.installs == []


def test_new_selection_reinstalls_with_all_groups(tmp_path: Path) -> None:
    """A selection never installed reinstalls once, then stays in sync"""
    provider = _provider(tmp_path, [_package(tmp_path)])

    lines = ConanProvider.sync_local_sources(provider)
    assert provider.installs == [['test']]
    assert lines[0].startswith(f'local periapsis -> {tmp_path / "periapsis"}')

    ConanProvider.sync_local_sources(provider)
    assert provider.installs == [['test']]


def test_recipe_change_reinstalls_with_recorded_groups(tmp_path: Path) -> None:
    """Changing a local recipe reinstalls with the groups of the last install"""
    package = _package(tmp_path)
    provider = _provider(tmp_path, [package])
    local.write_stamp(tmp_path / 'build', [package], ['Release'], None)

    ConanProvider.sync_local_sources(provider)
    assert provider.installs == []

    (package.path / 'conanfile.py').write_text('# edited recipe\n', encoding='utf-8')
    ConanProvider.sync_local_sources(provider)
    assert provider.installs == [None]


def test_cleared_selection_returns_to_published_packages(tmp_path: Path) -> None:
    """Dropping every local checkout reinstalls the published packages and says so"""
    local.write_stamp(tmp_path / 'build', [_package(tmp_path)], ['Release'], None)
    provider = _provider(tmp_path, [])

    lines = ConanProvider.sync_local_sources(provider)

    assert provider.installs == [None]
    assert lines == ['local sources: none apply to this project; using published packages']


def test_publish_refuses_local_sources(tmp_path: Path) -> None:
    """A package built against unpublished checkouts is never published"""
    (tmp_path / 'conanfile.py').touch()
    provider = _provider(tmp_path, [_package(tmp_path)])

    with pytest.raises(ProviderInstallationError, match='Refusing to publish'):
        ConanProvider.publish(provider)
