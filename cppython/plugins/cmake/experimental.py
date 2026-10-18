"""CMake experimental-feature gates.

Experimental CMake features are enabled by setting a variable to a UUID that
changes with each CMake release.  CMake stores each UUID directly before the
feature's variable name, so the value is read from the binary in use.
"""

import functools
import re
from pathlib import Path

IMPORT_STD_VARIABLE = "CMAKE_EXPERIMENTAL_CXX_IMPORT_STD"
FALLBACK_IMPORT_STD_UUID = "d0edc3af-4c50-42ea-a356-e2862fe7a444"

_UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SEARCH_WINDOW = 64


@functools.cache
def import_std_uuid(cmake_binary: Path | None) -> str:
    """Return the ``import std`` gate UUID for *cmake_binary*, or the fallback when unknown."""
    if cmake_binary is None or not cmake_binary.is_file():
        return FALLBACK_IMPORT_STD_UUID
    data = cmake_binary.read_bytes()
    index = data.find(IMPORT_STD_VARIABLE.encode())
    if index < 0:
        return FALLBACK_IMPORT_STD_UUID
    candidates = _UUID.findall(data, max(0, index - _SEARCH_WINDOW), index)
    return candidates[-1].decode() if candidates else FALLBACK_IMPORT_STD_UUID
