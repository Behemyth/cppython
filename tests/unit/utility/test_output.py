"""Tests for session failure output"""

import io
import logging
import subprocess

import pytest
from rich.console import Console

from cppython.utility.exception import ProviderInstallationError
from cppython.utility.output import OutputSession


def _render(error: Exception) -> str:
    """Raise *error* inside a session and return what the session printed."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=200)
    with pytest.raises(type(error)), OutputSession(console, verbose=False):
        raise error
    return buffer.getvalue()


def test_provider_failure_shows_command_and_output_tail() -> None:
    """A provider failure prints its command and the end of its output, like a failed process."""
    output = "\n".join(f"line {index}" for index in range(30))
    error = ProviderInstallationError(
        "conan", "boom", command=["conan", "install", "."], output=output
    )

    printed = _render(error)

    assert "Provider 'conan' installation failed: boom" in printed
    assert "conan install ." in printed
    assert "| line 29" in printed
    assert "| line 10" in printed
    assert "| line 9\n" not in printed
    assert "Full log:" in printed


def test_provider_failure_without_command_prints_message() -> None:
    """Errors without a command keep the one-line message."""
    printed = _render(ProviderInstallationError("conan", "boom"))

    assert "Error: Provider 'conan' installation failed: boom" in printed
    assert "|" not in printed


def test_process_failure_shows_status_and_output() -> None:
    """A failed process prints its status, command and output."""
    error = subprocess.CalledProcessError(
        2, ["cmake", "--build"], output="compiling", stderr="error C1234"
    )

    printed = _render(error)

    assert "command exited with status 2" in printed
    assert "cmake --build" in printed
    assert "| compiling" in printed
    assert "| error C1234" in printed


def test_notes_are_printed_as_hints() -> None:
    """Notes added to a failure are shown after it."""
    error = subprocess.CalledProcessError(1, ["cmake", "--preset", "x"])
    error.add_note("Delete build/[x] and configure again.")

    printed = _render(error)

    assert "Hint: Delete build/[x] and configure again." in printed


def test_error_message_is_not_markup() -> None:
    """Brackets in a generic error, such as a diff of '[test]', are printed literally."""
    printed = _render(ValueError("-    groups = [test]\n+    groups = [bold]"))

    assert "groups = [test]" in printed
    assert "groups = [bold]" in printed


def test_quiet_session_replays_warnings_but_not_errors() -> None:
    """Warnings reach the user after the spinner; per-line stderr errors stay in the log file."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=200)
    logger = logging.getLogger("cppython.test")

    with OutputSession(console, verbose=False):
        logger.warning("Regenerated [x].py; commit the change")
        logger.error("stderr line")
        logger.info("progress")

    printed = buffer.getvalue()
    assert "Warning: Regenerated [x].py; commit the change" in printed
    assert "stderr line" not in printed
    assert "progress" not in printed
    assert printed.index("Warning:") < printed.index("Done")
