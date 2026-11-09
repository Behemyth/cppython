"""Helpers for generating CMake input files."""

from pathlib import Path


def bracket_path(path: Path) -> str:
    """Return an absolute path as a CMake bracket argument."""
    value = path.resolve().as_posix()
    delimiter = '='
    while f']{delimiter}]' in value:
        delimiter += '='
    return f'[{delimiter}[{value}]{delimiter}]'
