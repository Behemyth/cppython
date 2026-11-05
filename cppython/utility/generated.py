"""Ownership tracking for files that CPPython generates into a project.

A generated file starts with a header that records the generator schema and a hash of the
body CPPython wrote. The hash tells an untouched file, which CPPython may regenerate, apart
from a hand-edited one, which it must not overwrite without an explicit force.
"""

import difflib
import hashlib
import re
from enum import StrEnum
from logging import Logger
from pathlib import Path

SCHEMA = 1

_HEADER_PATTERN = re.compile(r'^# cppython-generated: schema=(?P<schema>\d+) sha256=(?P<digest>[0-9a-f]{64})$')


class GeneratedFilePolicy(StrEnum):
    """How CPPython may change a generated file that differs from what it would generate now"""

    UPDATE = 'update'
    """Write missing or untouched files; refuse to overwrite hand edits"""

    LOCKED = 'locked'
    """Never write; any difference is an error"""

    FORCE = 'force'
    """Overwrite the file even if it was edited by hand"""


class GeneratedFileState(StrEnum):
    """The relationship between a generated file on disk and its expected content"""

    MISSING = 'missing'
    CURRENT = 'current'
    STALE = 'stale'
    """Untouched since CPPython wrote it, but generated from older inputs"""

    MODIFIED = 'modified'
    """Edited by hand since CPPython wrote it"""


class GeneratedFileError(Exception):
    """Raised when a generated file can't be brought up to date under the active policy."""

    def __init__(self, path: Path, state: GeneratedFileState, policy: GeneratedFilePolicy, diff: str) -> None:
        """Initializes the error.

        Args:
            path: The generated file
            state: The file's state on disk
            policy: The policy that refused the change
            diff: Unified diff from the file on disk to the expected content
        """
        self.path = path
        self.state = state
        self.policy = policy
        self.diff = diff

        if state is GeneratedFileState.MODIFIED:
            headline = f'{path.name} was edited by hand'
            if policy is not GeneratedFilePolicy.LOCKED:
                headline += ", so CPPython won't overwrite it"
            hint = (
                f"Move custom changes out of {path.name}, then run 'cppython install --force-regenerate'"
                ' and commit the regenerated file.'
            )
        elif state is GeneratedFileState.MISSING:
            headline = f'{path.name} is missing'
            hint = "Run 'cppython install' and commit the generated file."
        else:
            headline = f'{path.name} is out of date with pyproject.toml'
            hint = "Run 'cppython install' and commit the regenerated file."

        message = f'{headline}\n{diff}' if diff else headline
        super().__init__(message)
        self.add_note(hint)


def _normalize(text: str) -> str:
    return text.replace('\r\n', '\n')


def _digest(body: str) -> str:
    return hashlib.sha256(_normalize(body).encode('utf-8')).hexdigest()


def render(body: str) -> str:
    """Prefix a generated body with its ownership header.

    Args:
        body: The generated content

    Returns:
        The full file content
    """
    return f'# cppython-generated: schema={SCHEMA} sha256={_digest(body)}\n{body}'


def classify(path: Path, expected: str, legacy_markers: tuple[str, ...] = ()) -> GeneratedFileState:
    """Determine how a generated file on disk relates to its expected content.

    Args:
        path: The generated file
        expected: The full content CPPython would write, including the header
        legacy_markers: Text that identifies a file from a generator version without headers.
            Those versions always overwrote the file, so a match counts as untouched.

    Returns:
        The file's state
    """
    if not path.exists():
        return GeneratedFileState.MISSING

    existing = _normalize(path.read_text(encoding='utf-8'))
    if existing == _normalize(expected):
        return GeneratedFileState.CURRENT

    first_line, _, body = existing.partition('\n')
    if match := _HEADER_PATTERN.match(first_line):
        untouched = match['digest'] == _digest(body)
    else:
        untouched = any(marker in existing for marker in legacy_markers)

    return GeneratedFileState.STALE if untouched else GeneratedFileState.MODIFIED


def _diff(path: Path, expected: str) -> str:
    existing = _normalize(path.read_text(encoding='utf-8')) if path.exists() else ''
    lines = difflib.unified_diff(
        existing.splitlines(keepends=True),
        _normalize(expected).splitlines(keepends=True),
        fromfile=f'{path.name} (on disk)',
        tofile=f'{path.name} (expected)',
    )
    return ''.join(lines)


def synchronize(
    path: Path,
    expected: str,
    policy: GeneratedFilePolicy,
    logger: Logger,
    legacy_markers: tuple[str, ...] = (),
) -> GeneratedFileState:
    """Bring a generated file up to date, as far as the policy allows.

    Args:
        path: The generated file
        expected: The full content to write, including the header from `render`
        policy: What CPPython is allowed to change
        logger: Logger for reporting writes
        legacy_markers: See `classify`

    Returns:
        The state the file was in before synchronization

    Raises:
        GeneratedFileError: If the file differs and the policy forbids writing it
    """
    state = classify(path, expected, legacy_markers)
    if state is GeneratedFileState.CURRENT:
        return state

    forbidden = policy is GeneratedFilePolicy.LOCKED or (
        state is GeneratedFileState.MODIFIED and policy is not GeneratedFilePolicy.FORCE
    )
    if forbidden:
        raise GeneratedFileError(path, state, policy, _diff(path, expected))

    path.write_text(expected, encoding='utf-8')

    # Local sources regenerate files in other checkouts, so name the project too
    name = f'{path.parent.name}/{path.name}'
    match state:
        case GeneratedFileState.MISSING:
            logger.info('Created %s', name)
        case GeneratedFileState.STALE:
            logger.warning('Regenerated %s from pyproject.toml; commit the change', name)
        case GeneratedFileState.MODIFIED:
            logger.warning('Overwrote hand edits in %s', name)

    return state
