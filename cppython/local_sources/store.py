"""Locating, locking, reading, and atomically writing the user-level local-source state."""

import contextlib
import json
import os
import sys
import tempfile
import time
import tomllib
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cppython.local_sources.schema import LocalSourceState

ENVIRONMENT_VARIABLE = 'CPPYTHON_LOCAL_SOURCES'
STATE_FILE_NAME = 'local-sources.toml'
_OFF_VALUES = {'off', '0', 'false', 'no'}


class LockContentionError(Exception):
    """Another process held the state lock for longer than the timeout"""


@dataclass(frozen=True)
class StateLocation:
    """Where the state lives and whether the environment suspends it"""

    path: Path
    suspended_by_environment: bool


def default_state_path() -> Path:
    """The user-level state file, next to CPPython's global configuration"""
    return Path.home() / '.cppython' / STATE_FILE_NAME


def locate_state(environment: dict[str, str] | None = None) -> StateLocation:
    """Resolve the state file from `CPPYTHON_LOCAL_SOURCES`.

    An unset or empty variable selects the default file. `off` keeps the default file but suspends
    every override for this process. Any other value is a path to the state file.
    """
    value = (os.environ if environment is None else environment).get(ENVIRONMENT_VARIABLE, '').strip()
    if not value:
        return StateLocation(default_state_path(), False)
    if value.lower() in _OFF_VALUES:
        return StateLocation(default_state_path(), True)
    return StateLocation(Path(value).expanduser().absolute(), False)


def load_state(path: Path) -> LocalSourceState:
    """Read the state, treating a missing file as the empty stable-only selection"""
    if not path.exists():
        return LocalSourceState()
    return LocalSourceState.model_validate(tomllib.loads(path.read_text(encoding='utf-8')))


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return '[' + ', '.join(_toml_value(item) for item in value) + ']'
    raise TypeError(f'Unsupported TOML value: {value!r}')


def dump_state(state: LocalSourceState) -> str:
    """Serialize the state as TOML with a stable key order"""
    data = state.model_dump(mode='json', exclude_none=True)
    lines = ['# Written by CPPython. Change it with `cppython local`, not by hand.']
    tables: dict[str, dict[str, Any]] = {}

    for key, value in data.items():
        if key == 'packages':
            for name, package in sorted(value.items()):
                tables[f'packages.{json.dumps(name)}'] = package
        elif isinstance(value, dict):
            tables[key] = value
        else:
            lines.append(f'{key} = {_toml_value(value)}')

    for header, table in tables.items():
        lines.extend(['', f'[{header}]'])
        lines.extend(f'{key} = {_toml_value(value)}' for key, value in table.items())

    return '\n'.join(lines) + '\n'


def write_state(path: Path, state: LocalSourceState) -> None:
    """Replace the state file atomically so readers never see a partial write"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(dump_state(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _try_lock(handle: Any) -> bool:
    try:
        if sys.platform == 'win32':
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: Any) -> None:
    if sys.platform == 'win32':
        import msvcrt  # noqa: PLC0415

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def state_lock(path: Path, timeout: float = 10.0) -> Generator[None]:
    """Hold an exclusive cross-process lock for a read-modify-write of the state.

    Raises:
        LockContentionError: If the lock isn't acquired within the timeout
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    deadline = time.monotonic() + timeout

    with open(lock_path, 'a+b') as handle:
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise LockContentionError(f'Timed out waiting for {lock_path}')
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
