"""Tests for identity-based Conan compiler and platform settings."""

import json
import subprocess
from pathlib import Path

import pytest

from cppython.core.identity import BuildIdentity
from cppython.plugins.conan.identity import resolve_identity_profile
from cppython.utility.exception import ProviderConfigurationError


def _identity(operating_system: str, architecture: str, compiler: str, library: str) -> BuildIdentity:
    return BuildIdentity(
        name=f'{operating_system}-{architecture}-{compiler}',
        os=operating_system,
        arch=architecture,
        compiler=compiler,
        standard_library=library,
        configurations=['Debug', 'Development', 'Release'],
        dependency_build_types={'Debug': 'Debug', 'Development': 'Release', 'Release': 'Release'},
    )


def test_msvc_uses_actual_version_target_and_release_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MSVC's compiler version and target must match the identity's dynamic CRT."""
    compiler = tmp_path / 'cl.exe'
    monkeypatch.setattr('cppython.plugins.conan.identity.platform.system', lambda: 'Windows')
    monkeypatch.setattr('cppython.plugins.conan.identity._environment', lambda: {})
    monkeypatch.setattr('cppython.plugins.conan.identity._find_compiler', lambda _name, _env: compiler)
    monkeypatch.setattr(
        'cppython.plugins.conan.identity._run_compiler',
        lambda *_args: subprocess.CompletedProcess([], 2, '', 'Microsoft C/C++ Version 19.52.37025 for x64'),
    )

    profile = resolve_identity_profile(_identity('windows', 'x64', 'msvc', 'msvc'), {'compiler.cppstd': '23'})

    assert profile.settings == {
        'os': 'Windows',
        'arch': 'x86_64',
        'compiler': 'msvc',
        'compiler.version': '195',
        'compiler.runtime': 'dynamic',
    }
    assert profile.conf['tools.build:compiler_executables'] == {'c': str(compiler), 'cpp': str(compiler)}
    assert 'compiler.version=195' in profile.arguments()


@pytest.mark.parametrize(
    'case',
    [('Linux', 'linux', 'x64', 'x86_64-unknown-linux-gnu'), ('Darwin', 'macos', 'arm64', 'arm64-apple-darwin')],
)
def test_clang_settings_and_macos_link_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, str, str],
) -> None:
    """Linux and macOS use the actual Clang major and matching libc++ settings."""
    system, operating_system, architecture, triple = case
    root = tmp_path / 'llvm'
    (root / 'lib' / 'c++').mkdir(parents=True)
    (root / 'lib' / 'unwind').mkdir(parents=True)
    manifest = root / 'lib' / 'c++' / 'libc++.modules.json'
    manifest.write_text('{}', encoding='utf-8')
    monkeypatch.setattr('cppython.plugins.conan.identity.platform.system', lambda: system)
    monkeypatch.setattr('cppython.plugins.conan.identity._environment', lambda: {})
    monkeypatch.setattr('cppython.plugins.conan.identity._find_compiler', lambda name, _env: root / 'bin' / name)
    monkeypatch.setattr(
        'cppython.plugins.conan.identity._run_compiler',
        lambda _compiler, args, _env: subprocess.CompletedProcess(
            [],
            0,
            'clang version 23.0.0' if args == ['--version'] else ('23.0.0' if args == ['-dumpversion'] else triple),
            '',
        ),
    )

    profile = resolve_identity_profile(_identity(operating_system, architecture, 'clang', 'libc++'), {})

    assert profile.settings['compiler.version'] == '23'
    assert profile.settings['compiler.libcxx'] == 'libc++'
    assert profile.settings['arch'] == ('x86_64' if architecture == 'x64' else 'armv8')
    if system == 'Darwin':
        flags = [
            f'-L{root / "lib" / "c++"}',
            f'-Wl,-rpath,{root / "lib" / "c++"}',
            f'-L{root / "lib" / "unwind"}',
            '-lunwind',
        ]
        assert profile.conf['tools.build:exelinkflags'] == flags
        assert profile.conf['tools.build:sharedlinkflags'] == flags
        # Dependencies built from source need the manifest that Clang cannot locate in lib/c++
        variables = {'CMAKE_CXX_STDLIB_MODULES_JSON': str(manifest)}
        assert profile.conf['tools.cmake.cmaketoolchain:extra_variables'] == variables
        assert f'tools.cmake.cmaketoolchain:extra_variables={json.dumps(variables, separators=(",", ":"))}' in (
            profile.arguments()
        )
        manifest.unlink()
        without = resolve_identity_profile(_identity(operating_system, architecture, 'clang', 'libc++'), {})
        assert 'tools.cmake.cmaketoolchain:extra_variables' not in without.conf
    else:
        assert 'tools.build:exelinkflags' not in profile.conf
        assert 'tools.cmake.cmaketoolchain:extra_variables' not in profile.conf


def test_compiler_override_conflict_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit provider settings may not contradict the compiler used by the preset."""
    compiler = tmp_path / 'cl.exe'
    monkeypatch.setattr('cppython.plugins.conan.identity.platform.system', lambda: 'Windows')
    monkeypatch.setattr('cppython.plugins.conan.identity._environment', lambda: {})
    monkeypatch.setattr('cppython.plugins.conan.identity._find_compiler', lambda _name, _env: compiler)
    monkeypatch.setattr(
        'cppython.plugins.conan.identity._run_compiler',
        lambda *_args: subprocess.CompletedProcess([], 2, '', 'Version 19.52.37025 for x64'),
    )
    with pytest.raises(ProviderConfigurationError, match='conflicts'):
        resolve_identity_profile(_identity('windows', 'x64', 'msvc', 'msvc'), {'compiler.version': '194'})

    with pytest.raises(ProviderConfigurationError, match='compiler.runtime_type must follow'):
        resolve_identity_profile(_identity('windows', 'x64', 'msvc', 'msvc'), {'compiler.runtime_type': 'Debug'})

    jobs = 4
    profile = resolve_identity_profile(
        _identity('windows', 'x64', 'msvc', 'msvc'),
        {},
        {'tools.build:jobs': jobs},
    )
    assert profile.conf['tools.build:jobs'] == jobs
    with pytest.raises(ProviderConfigurationError, match='conflicts with the compiler'):
        resolve_identity_profile(
            _identity('windows', 'x64', 'msvc', 'msvc'),
            {},
            {'tools.build:compiler_executables': {'cpp': 'other.exe'}},
        )


def test_compiler_target_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ARM64 identity cannot silently prepare dependencies with an x64 compiler."""
    compiler = tmp_path / 'cl.exe'
    monkeypatch.setattr('cppython.plugins.conan.identity.platform.system', lambda: 'Windows')
    monkeypatch.setattr('cppython.plugins.conan.identity._environment', lambda: {})
    monkeypatch.setattr('cppython.plugins.conan.identity._find_compiler', lambda _name, _env: compiler)
    monkeypatch.setattr(
        'cppython.plugins.conan.identity._run_compiler',
        lambda *_args: subprocess.CompletedProcess([], 2, '', 'Version 19.52.37025 for x64'),
    )
    with pytest.raises(ProviderConfigurationError, match="requires architecture 'arm64'"):
        resolve_identity_profile(_identity('windows', 'arm64', 'msvc', 'msvc'), {})
