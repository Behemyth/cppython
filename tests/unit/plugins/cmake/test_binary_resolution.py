"""Unit tests for CMake binary resolution."""

from pathlib import Path

import pytest
from cppython.plugins.cmake import resolution
from cppython.plugins.cmake.experimental import (
    FALLBACK_IMPORT_STD_UUID,
    import_std_uuid,
)
from cppython.plugins.cmake.resolution import (
    resolve_cmake_binary as _resolve_cmake_binary,
)


@pytest.fixture(autouse=True)
def _no_cmake_binary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMAKE_BINARY", raising=False)


def test_import_std_uuid_read_from_binary(tmp_path: Path) -> None:
    """The UUID stored just before the variable name in the binary is used."""
    binary = tmp_path / "cmake.exe"
    binary.write_bytes(
        b"\x00ExportSbom\x00ababa1b5-7099-495f-a9cd-e22d38f274f2\x00CMAKE_EXPERIMENTAL_OTHER\x00"
        b"CxxImportStd\x00f35a9ac6-8463-4d38-8eec-5d6008153e7d\x00CMAKE_EXPERIMENTAL_CXX_IMPORT_STD\x00"
    )

    assert import_std_uuid(binary) == "f35a9ac6-8463-4d38-8eec-5d6008153e7d"


def test_import_std_uuid_falls_back(tmp_path: Path) -> None:
    """Unknown binaries use the bundled fallback UUID."""
    binary = tmp_path / "not-cmake.exe"
    binary.write_bytes(b"nothing here")

    assert import_std_uuid(binary) == FALLBACK_IMPORT_STD_UUID
    assert import_std_uuid(None) == FALLBACK_IMPORT_STD_UUID


def test_packaged_cmake_preferred_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CMake installed with the ``cmake`` extra wins over an older one on PATH."""
    packaged = tmp_path / "cmake.exe"
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: packaged)
    monkeypatch.setattr(
        resolution.shutil,
        "which",
        lambda *_args, **_kwargs: str(tmp_path / "old" / "cmake.exe"),
    )

    assert _resolve_cmake_binary(None) == packaged


def test_configured_binary_preferred_over_packaged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicitly configured binary still takes precedence."""
    configured = tmp_path / "configured-cmake"
    configured.touch()
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: tmp_path / "cmake.exe")

    assert _resolve_cmake_binary(configured) == configured


def test_path_used_without_packaged_cmake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the extra, PATH lookup is the fallback."""
    on_path = tmp_path / "cmake.exe"
    monkeypatch.setattr(resolution, "_packaged_cmake", lambda: None)
    monkeypatch.setattr(
        resolution.shutil, "which", lambda *_args, **_kwargs: str(on_path)
    )

    assert _resolve_cmake_binary(None) == on_path
