"""Tests for Conan command output capture"""

import sys
from logging import getLogger
from types import SimpleNamespace
from typing import Any

import pytest
from conan.internal.api.local.editable import EditablePackages

from cppython.plugins.conan.plugin import ConanProvider
from cppython.utility.exception import ProviderInstallationError


class _FailingCommandAPI:
    """Stands in for ``ConanAPI.command`` and fails after printing."""

    def run(self, args: list[str]) -> None:
        """Print like Conan does, then fail."""
        print("resolving graph")
        print("CMake Error: compiler not found", file=sys.stderr)
        raise RuntimeError("ERROR: conanfile.py: Error in build() method")


def _provider(command: object) -> Any:
    return SimpleNamespace(
        _conan_api=SimpleNamespace(command=command, _api_helpers=SimpleNamespace(_editable_packages=None)),
        _editables=EditablePackages(),
        _logger=getLogger("test.conan"),
    )


def test_failed_call_carries_command_and_output() -> None:
    """A failed Conan call raises with the command and everything Conan printed."""
    provider = _provider(_FailingCommandAPI())
    provider._conan_api._api_helpers._editable_packages = EditablePackages()

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


def test_call_sees_only_the_provider_editables() -> None:
    """Each Conan call runs with the provider's in-memory editables in place of the global registry."""
    seen: list[object] = []

    class _RecordingCommandAPI:
        def run(self, args: list[str]) -> None:
            seen.append(provider._conan_api._api_helpers._editable_packages)

    provider = _provider(_RecordingCommandAPI())
    provider._conan_api._api_helpers._editable_packages = EditablePackages()

    ConanProvider._capture_conan_call(provider, ["install", "."])

    assert seen == [provider._editables]


def test_unsupported_conan_fails_clearly() -> None:
    """A Conan release without the expected editable registry fails before running anything."""
    provider = _provider(_FailingCommandAPI())

    with pytest.raises(ProviderInstallationError, match="editable registry"):
        ConanProvider._capture_conan_call(provider, ["install", "."])
