"""Unit tests for Visual Studio developer environment discovery."""

import subprocess
from pathlib import Path

import pytest

from cppython.utility import msvc


def test_parse_environment_skips_drive_variables() -> None:
    """``set`` output becomes a mapping; ``=C:`` style entries are dropped."""
    output = "=C:=C:\\work\nPATH=C:\\VS\\bin;C:\\Windows\nINCLUDE=C:\\VS\\include\nnot a variable\n"

    assert msvc.parse_environment(output) == {
        "PATH": "C:\\VS\\bin;C:\\Windows",
        "INCLUDE": "C:\\VS\\include",
    }


def test_not_needed_when_compiler_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """No environment is loaded when ``cl`` is already available."""
    msvc.developer_environment.cache_clear()
    monkeypatch.setattr(msvc.sys, "platform", "win32")
    monkeypatch.setattr(msvc.shutil, "which", lambda _name: "C:\\VS\\cl.exe")
    monkeypatch.setattr(
        msvc, "installation_path", lambda: pytest.fail("vswhere should not run")
    )

    assert msvc.developer_environment() is None
    msvc.developer_environment.cache_clear()


@pytest.mark.parametrize(("machine", "arch"), [("AMD64", "amd64"), ("ARM64", "arm64")])
def test_developer_environment_loads_visual_studio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, machine: str, arch: str
) -> None:
    """Load the selected target architecture and return the captured environment."""
    msvc.developer_environment.cache_clear()
    devcmd = tmp_path / "Common7" / "Tools" / "VsDevCmd.bat"
    devcmd.parent.mkdir(parents=True)
    devcmd.touch()
    monkeypatch.setattr(msvc.sys, "platform", "win32")
    monkeypatch.setattr(msvc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(msvc, "installation_path", lambda: tmp_path)
    monkeypatch.setattr(msvc.platform, "machine", lambda: machine)
    commands: list[str] = []

    def run(command: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="PATH=compiler\nINCLUDE=headers\n", stderr="")

    monkeypatch.setattr(msvc.subprocess, "run", run)

    assert msvc.developer_environment() == {"PATH": "compiler", "INCLUDE": "headers"}
    assert commands == [f'"{devcmd}" -arch={arch} -host_arch={arch} -no_logo && set']
    msvc.developer_environment.cache_clear()


def test_query_includes_prerelease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preview and Insiders installations are considered."""
    vswhere = tmp_path / "vswhere.exe"
    vswhere.touch()
    monkeypatch.setattr(msvc, "_vswhere", lambda: vswhere)
    captured: list[list[str]] = []

    def _run(args: list[str], **_kwargs: object) -> object:
        captured.append(args)
        return type(
            "Result",
            (),
            {"stdout": "C:\\Program Files\\Microsoft Visual Studio\\18\\Insiders\n"},
        )()

    monkeypatch.setattr(msvc.subprocess, "run", _run)

    assert msvc.installation_path() == Path(
        "C:\\Program Files\\Microsoft Visual Studio\\18\\Insiders"
    )
    assert "-prerelease" in captured[0]
