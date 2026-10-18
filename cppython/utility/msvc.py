"""Locate Visual Studio and load its developer environment on Windows.

CMake's Ninja generators with MSVC need ``cl`` on ``PATH``, which normally
means running from a Developer Command Prompt.  This finds the newest
Visual Studio with the C++ tools (including Preview/Insiders builds) and
captures the environment ``VsDevCmd.bat`` would set.
"""

import functools
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("cppython.msvc")

_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"


def _vswhere() -> Path:
    program_files = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    return Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def installation_path() -> Path | None:
    """Return the newest Visual Studio installation with the C++ tools, if any."""
    vswhere = _vswhere()
    if not vswhere.is_file():
        return None
    result = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-prerelease",
            "-products",
            "*",
            "-requires",
            _VC_TOOLS_COMPONENT,
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    paths = result.stdout.strip().splitlines()
    return Path(paths[0]) if paths else None


def parse_environment(output: str) -> dict[str, str]:
    """Parse ``set`` output into an environment mapping."""
    return dict(
        line.split("=", 1)
        for line in output.splitlines()
        if "=" in line and not line.startswith("=")
    )


@functools.cache
def developer_environment() -> dict[str, str] | None:
    """Return a Visual Studio developer environment, or ``None`` when not needed or unavailable.

    Not needed off Windows or when ``cl`` is already on ``PATH``.
    """
    if sys.platform != "win32" or shutil.which("cl"):
        return None

    install = installation_path()
    if install is None:
        logger.debug("No Visual Studio installation with C++ tools found")
        return None

    devcmd = install / "Common7" / "Tools" / "VsDevCmd.bat"
    if not devcmd.is_file():
        return None

    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
    result = subprocess.run(
        f'"{devcmd}" -arch={arch} -host_arch={arch} -no_logo && set',
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "Loading the Visual Studio developer environment from %s failed", install
        )
        return None

    logger.info("Using Visual Studio developer environment from %s", install)
    return parse_environment(result.stdout)
