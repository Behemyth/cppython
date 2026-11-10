"""Map a selected build identity and the actual compiler to Conan settings."""

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cppython.core.identity import BuildIdentity
from cppython.utility.exception import ProviderConfigurationError
from cppython.utility.msvc import developer_environment

_SYSTEMS = {'windows': 'Windows', 'linux': 'Linux', 'macos': 'Macos'}
_ARCHITECTURES = {'x64': 'x86_64', 'arm64': 'armv8'}
_TARGET_ARCHITECTURES = {
    'x64': 'x64',
    'amd64': 'x64',
    'x86_64': 'x64',
    'arm64': 'arm64',
    'aarch64': 'arm64',
}
_MSVC_VERSION = re.compile(r'\bVersion\s+(\d+)\.(\d+)(?:\.\d+)*\s+for\s+(\S+)', re.IGNORECASE)


@dataclass(frozen=True)
class ConanIdentityProfile:
    """Conan settings and configuration derived from one verified compiler."""

    settings: dict[str, str]
    conf: dict[str, object]

    def arguments(self) -> list[str]:
        """Return command-line settings for both host and build contexts."""
        settings = [argument for key, value in self.settings.items() for argument in ('-s:a', f'{key}={value}')]
        conf = [
            argument
            for key, value in self.conf.items()
            for argument in ('-c:a', f'{key}={json.dumps(value, separators=(",", ":"))}')
        ]
        return [*settings, *conf]


def _environment() -> dict[str, str]:
    return developer_environment() or dict(os.environ)


def _find_compiler(name: str, environment: dict[str, str]) -> Path:
    path = next((value for key, value in environment.items() if key.casefold() == 'path'), '')
    executable = shutil.which(name, path=path)
    if executable is None:
        raise ProviderConfigurationError('conan', f"Compiler '{name}' was not found on PATH for the selected identity.")
    return Path(executable).absolute()


def _run_compiler(
    executable: Path, arguments: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(executable), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProviderConfigurationError('conan', f'Cannot inspect compiler {executable}: {error}') from error


def _require_target(identity: BuildIdentity, target: str) -> None:
    actual = _TARGET_ARCHITECTURES.get(target.casefold())
    if actual != identity.arch:
        raise ProviderConfigurationError(
            'conan',
            f"Identity '{identity.name}' requires architecture '{identity.arch}', "
            f"but the selected compiler targets '{target}'.",
        )


def _msvc_profile(identity: BuildIdentity, environment: dict[str, str]) -> ConanIdentityProfile:
    if identity.os != 'windows' or identity.standard_library != 'msvc':
        raise ProviderConfigurationError('conan', f"Unsupported MSVC identity '{identity.name}'.")

    compiler = _find_compiler('cl', environment)
    result = _run_compiler(compiler, ['/Bv'], environment)
    match = _MSVC_VERSION.search(result.stdout + '\n' + result.stderr)
    if match is None:
        raise ProviderConfigurationError('conan', f'Cannot determine MSVC version and target from {compiler} /Bv.')
    major, minor = int(match.group(1)), int(match.group(2))
    _require_target(identity, match.group(3))

    return ConanIdentityProfile(
        settings={
            'compiler': 'msvc',
            'compiler.version': str(major * 10 + minor // 10),
            'compiler.runtime': 'dynamic',
        },
        conf={'tools.build:compiler_executables': {'c': str(compiler), 'cpp': str(compiler)}},
    )


def _clang_profile(identity: BuildIdentity, environment: dict[str, str]) -> ConanIdentityProfile:
    if identity.os not in {'linux', 'macos'} or identity.standard_library != 'libc++':
        raise ProviderConfigurationError('conan', f"Unsupported Clang identity '{identity.name}'.")

    c_compiler = _find_compiler('clang', environment)
    cpp_compiler = _find_compiler('clang++', environment)
    versions: set[str] = set()
    for compiler in (c_compiler, cpp_compiler):
        banner = _run_compiler(compiler, ['--version'], environment)
        version = _run_compiler(compiler, ['-dumpversion'], environment)
        target = _run_compiler(compiler, ['-dumpmachine'], environment)
        if (
            banner.returncode != 0
            or not re.search(r'\bclang version \d', banner.stdout, re.IGNORECASE)
            or 'Apple clang' in banner.stdout
            or version.returncode != 0
            or target.returncode != 0
            or not re.fullmatch(r'\d+(?:\.\d+)*', version.stdout.strip())
        ):
            raise ProviderConfigurationError('conan', f'Cannot determine Clang version and target from {compiler}.')
        versions.add(version.stdout.strip().split('.')[0])
        _require_target(identity, target.stdout.strip().split('-', 1)[0])
    if len(versions) != 1:
        raise ProviderConfigurationError('conan', 'The selected clang and clang++ have different major versions.')

    conf: dict[str, object] = {'tools.build:compiler_executables': {'c': str(c_compiler), 'cpp': str(cpp_compiler)}}
    if identity.os == 'macos':
        root = c_compiler.resolve().parent.parent
        libcxx = root / 'lib' / 'c++'
        unwind = root / 'lib' / 'unwind'
        if not libcxx.is_dir() or not unwind.is_dir():
            raise ProviderConfigurationError(
                'conan',
                f'Upstream LLVM at {root} must provide lib/c++ and lib/unwind for the macOS identity.',
            )
        link_flags = [f'-L{libcxx}', f'-Wl,-rpath,{libcxx}', f'-L{unwind}', '-lunwind']
        conf['tools.build:exelinkflags'] = link_flags
        conf['tools.build:sharedlinkflags'] = link_flags
        # Clang's -print-file-name does not search lib/c++, so CMake cannot find the 'import std' manifest
        # when it builds a dependency from source.
        manifest = libcxx / 'libc++.modules.json'
        if manifest.is_file():
            conf['tools.cmake.cmaketoolchain:extra_variables'] = {'CMAKE_CXX_STDLIB_MODULES_JSON': str(manifest)}

    return ConanIdentityProfile(
        settings={'compiler': 'clang', 'compiler.version': versions.pop(), 'compiler.libcxx': 'libc++'},
        conf=conf,
    )


def resolve_identity_profile(
    identity: BuildIdentity, overrides: dict[str, str], conf_overrides: dict[str, Any] | None = None
) -> ConanIdentityProfile:
    """Resolve settings and reject explicit overrides that disagree with the actual target."""
    host = {'Windows': 'windows', 'Linux': 'linux', 'Darwin': 'macos'}.get(platform.system())
    if identity.os != host or identity.os not in _SYSTEMS or identity.arch not in _ARCHITECTURES:
        raise ProviderConfigurationError(
            'conan',
            f"Identity '{identity.name}' cannot be prepared on this host ({platform.system()}/{platform.machine()}).",
        )

    environment = _environment()
    if identity.compiler == 'msvc':
        compiler = _msvc_profile(identity, environment)
    elif identity.compiler == 'clang':
        compiler = _clang_profile(identity, environment)
    else:
        raise ProviderConfigurationError('conan', f"Unsupported compiler '{identity.compiler}' for '{identity.name}'.")

    settings = {'os': _SYSTEMS[identity.os], 'arch': _ARCHITECTURES[identity.arch], **compiler.settings}
    for key, value in overrides.items():
        if key in settings and settings[key] != value:
            raise ProviderConfigurationError(
                'conan',
                f"Setting '{key}={value}' conflicts with identity '{identity.name}' and the selected "
                f'compiler ({key}={settings[key]}).',
                key,
            )
    if identity.compiler == 'msvc' and 'compiler.runtime_type' in overrides:
        raise ProviderConfigurationError(
            'conan',
            'compiler.runtime_type must follow each dependency build type for an identity-based MSVC build.',
            'compiler.runtime_type',
        )

    conf = dict(compiler.conf)
    for key, value in (conf_overrides or {}).items():
        if key == 'tools.build:compiler_executables' and value != conf[key]:
            raise ProviderConfigurationError(
                'conan',
                f"Configuration '{key}' conflicts with the compiler selected for identity '{identity.name}'.",
                key,
            )
        conf[key] = value
    return ConanIdentityProfile(settings=settings, conf=conf)
