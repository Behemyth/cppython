"""Tests for provider- and generator-neutral identity selection."""

from pathlib import Path

import pytest

from cppython.core.exception import ConfigException
from cppython.core.identity import load_identity_table, select_identity
from cppython.core.resolution import PluginCPPythonData, resolve_cppython, resolve_project_configuration
from cppython.core.schema import CPPythonGlobalConfiguration, CPPythonLocalConfiguration, ProjectConfiguration
from cppython.utility.utility import TypeName

TABLE = """
version = 1

[[identities]]
name = "linux-x64-clang-libcxx"
os = "linux"
arch = "x64"
compiler = "clang"
standard-library = "libc++"
variant = ""
configurations = ["Debug", "Development", "Release"]
dependency-build-types = { Debug = "Debug", Development = "Release", Release = "Release" }

[[identities]]
name = "linux-x64-clang-libcxx-sanitize"
os = "linux"
arch = "x64"
compiler = "clang"
standard-library = "libc++"
variant = "sanitize"
configurations = ["Debug"]
dependency-build-types = { Debug = "Debug" }
"""


@pytest.fixture
def identity_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide one regular and one opt-in identity on a Linux x64 host."""
    path = tmp_path / 'tool' / 'identities.toml'
    path.parent.mkdir()
    path.write_text(TABLE, encoding='utf-8')
    monkeypatch.setattr('cppython.core.identity.platform.system', lambda: 'Linux')
    monkeypatch.setattr('cppython.core.identity.platform.machine', lambda: 'x86_64')
    monkeypatch.delenv('CPPYTHON_IDENTITY', raising=False)
    return path


def test_host_default_and_explicit_variant(identity_file: Path) -> None:
    """The host default excludes optional variants, which remain explicitly selectable."""
    default = select_identity(identity_file)
    selected = select_identity(identity_file, 'linux-x64-clang-libcxx-sanitize')

    assert default is not None
    assert default.name == 'linux-x64-clang-libcxx'
    assert default.dependency_build_types['Development'] == 'Release'
    assert selected is not None
    assert selected.configurations == ['Debug']


def test_missing_table_preserves_legacy_projects(tmp_path: Path) -> None:
    """Only explicit selections require the optional default table to exist."""
    path = tmp_path / 'missing.toml'
    assert select_identity(path) is None
    with pytest.raises(ConfigException, match='identity table does not exist'):
        select_identity(path, 'linux-x64-clang-libcxx')
    with pytest.raises(ConfigException, match='identity table does not exist'):
        select_identity(path, required=True)


@pytest.mark.parametrize(
    'content',
    [
        TABLE + TABLE[TABLE.index('[[identities]]') : TABLE.index('[[identities]]', TABLE.index('[[identities]]') + 1)],
        TABLE.replace('Development = "Release", ', ''),
        TABLE.replace('linux-x64-clang-libcxx"', '../unsafe"', 1),
        TABLE.replace('version = 1', 'version = 2'),
    ],
)
def test_invalid_tables_fail_without_fallback(identity_file: Path, content: str) -> None:
    """Duplicate names, incomplete mappings, unsafe names, and unknown versions are errors."""
    identity_file.write_text(content, encoding='utf-8')
    with pytest.raises(ConfigException, match='Cannot load build identities'):
        load_identity_table(identity_file)


def test_unknown_and_ambiguous_defaults_fail(identity_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid explicit choices and unsupported hosts do not silently select another identity."""
    with pytest.raises(ConfigException, match='Unknown build identity'):
        select_identity(identity_file, 'missing')
    monkeypatch.setattr('cppython.core.identity.platform.machine', lambda: 'mystery')
    with pytest.raises(ConfigException, match='No unique default build identity'):
        select_identity(identity_file)


def test_resolution_precedence(identity_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI overrides the environment, which overrides project configuration."""
    root = identity_file.parent.parent
    project = ProjectConfiguration(project_root=root, version=None, identity='linux-x64-clang-libcxx-sanitize')
    local = CPPythonLocalConfiguration(identity='linux-x64-clang-libcxx')
    plugins = PluginCPPythonData(
        generator_name=TypeName('generator'), provider_name=TypeName('provider'), scm_name=TypeName('scm')
    )
    monkeypatch.setenv('CPPYTHON_IDENTITY', 'linux-x64-clang-libcxx')
    resolved = resolve_cppython(local, CPPythonGlobalConfiguration(), resolve_project_configuration(project), plugins)
    assert resolved.identity is not None
    assert resolved.identity.name == 'linux-x64-clang-libcxx-sanitize'

    project.identity = None
    monkeypatch.setenv('CPPYTHON_IDENTITY', 'linux-x64-clang-libcxx-sanitize')
    resolved = resolve_cppython(local, CPPythonGlobalConfiguration(), resolve_project_configuration(project), plugins)
    assert resolved.identity is not None
    assert resolved.identity.name == 'linux-x64-clang-libcxx-sanitize'


def test_identity_opt_out_keeps_legacy_behavior(identity_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project can disable identity preparation without removing its neutral table."""
    root = identity_file.parent.parent
    local = CPPythonLocalConfiguration(identities_enabled=False)
    project = ProjectConfiguration(project_root=root, version=None)
    plugins = PluginCPPythonData(
        generator_name=TypeName('meson'), provider_name=TypeName('vcpkg'), scm_name=TypeName('scm')
    )
    resolved = resolve_cppython(local, CPPythonGlobalConfiguration(), resolve_project_configuration(project), plugins)
    assert resolved.identity is None

    monkeypatch.setenv('CPPYTHON_IDENTITY', 'linux-x64-clang-libcxx')
    with pytest.raises(ConfigException, match='identities-enabled is false'):
        resolve_cppython(local, CPPythonGlobalConfiguration(), resolve_project_configuration(project), plugins)
