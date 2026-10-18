"""Tests for Conan command output capture"""

import sys
from logging import getLogger
from types import SimpleNamespace
from typing import Any

import pytest
from cppython.plugins.conan.plugin import ConanProvider
from cppython.utility.exception import ProviderInstallationError


class _FailingCommandAPI:
    """Stands in for ``ConanAPI.command`` and fails after printing."""

    def run(self, args: list[str]) -> None:
        """Print like Conan does, then fail."""
        print("resolving graph")
        print("CMake Error: compiler not found", file=sys.stderr)
        raise RuntimeError("ERROR: conanfile.py: Error in build() method")


def test_failed_call_carries_command_and_output() -> None:
    """A failed Conan call raises with the command and everything Conan printed."""
    provider: Any = SimpleNamespace(
        _conan_api=SimpleNamespace(command=_FailingCommandAPI()),
        _logger=getLogger("test.conan"),
    )

    with pytest.raises(ProviderInstallationError) as caught:
        ConanProvider._capture_conan_call(provider, ["install", "."])

    error = caught.value
    assert error.command == ["conan", "install", "."]
    assert error.output.splitlines() == [
        "resolving graph",
        "CMake Error: compiler not found",
    ]
    assert "Error in build() method" in error.error
    assert isinstance(error.original_error, RuntimeError)
