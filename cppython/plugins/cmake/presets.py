"""Relate CMake configure, build and test presets so one name can drive every step."""

import json
from pathlib import Path
from typing import Any, Literal

from cppython.utility.exception import ConfigurationRequiredError

type PresetKind = Literal["configure", "build", "test"]

_SECTIONS: dict[PresetKind, str] = {
    "configure": "configurePresets",
    "build": "buildPresets",
    "test": "testPresets",
}


class PresetIndex:
    """The presets CMake would see from a source directory, keyed by kind and name."""

    def __init__(self, source_dir: Path) -> None:
        """Read ``CMakePresets.json`` and ``CMakeUserPresets.json`` with their includes."""
        self.source_dir = source_dir
        self._presets: dict[PresetKind, dict[str, dict[str, Any]]] = {
            kind: {} for kind in _SECTIONS
        }
        self._read: set[Path] = set()
        self._load(source_dir / "CMakePresets.json")
        self._load(source_dir / "CMakeUserPresets.json")

    def _load(self, path: Path) -> None:
        path = path.resolve()
        if path in self._read or not path.is_file():
            return
        self._read.add(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        for include in data.get("include", []):
            self._load(
                path.parent / include.replace("${sourceDir}", str(self.source_dir))
            )
        for kind, section in _SECTIONS.items():
            for preset in data.get(section, []):
                self._presets[kind].setdefault(preset["name"], preset)

    def field(self, kind: PresetKind, name: str, key: str) -> Any:
        """Return *key* for a preset, following ``inherits`` like CMake does."""
        preset = self._presets[kind].get(name)
        if preset is None:
            return None
        if key in preset:
            return preset[key]
        parents = preset.get("inherits", [])
        for parent in [parents] if isinstance(parents, str) else parents:
            value = self.field(kind, parent, key)
            if value is not None:
                return value
        return None

    def _usable(self, kind: PresetKind, name: str) -> bool:
        preset = self._presets[kind].get(name)
        return preset is not None and not preset.get("hidden", False)

    def resolve(self, kind: PresetKind, name: str) -> str:
        """Map *name* to a preset of *kind*.

        A build or test preset name maps to its configure preset; a configure preset name maps
        to its only build or test preset. Unknown names are returned unchanged so CMake reports them.

        Raises:
            ConfigurationRequiredError: If a configure preset has several presets of *kind*.
        """
        if self._usable(kind, name):
            return name

        if kind == "configure":
            others: tuple[PresetKind, ...] = ("build", "test")
            for other in others:
                if self._usable(other, name) and (
                    configure := self.field(other, name, "configurePreset")
                ):
                    return configure
            return name

        if not self._usable("configure", name):
            return name

        matches = [
            candidate
            for candidate in self._presets[kind]
            if self._usable(kind, candidate)
            and self.field(kind, candidate, "configurePreset") == name
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ConfigurationRequiredError(
                f"Configure preset '{name}' has no {kind} preset."
            )
        raise ConfigurationRequiredError(
            f"Configure preset '{name}' has several {kind} presets; choose one with --configuration: "
            + ", ".join(matches)
        )

    def binary_dir(self, name: str) -> Path | None:
        """Return the build directory of configure preset *name*, when it can be expanded."""
        value = self.field("configure", name, "binaryDir")
        if not isinstance(value, str):
            return None
        macros = {
            "${sourceDir}": str(self.source_dir),
            "${sourceParentDir}": str(self.source_dir.parent),
            "${sourceDirName}": self.source_dir.name,
            "${presetName}": name,
        }
        for macro, replacement in macros.items():
            value = value.replace(macro, replacement)
        value = value.replace("${dollar}", "\0")
        if "${" in value or "env{" in value:
            return None
        return self.source_dir / value.replace("\0", "$")


def _cache_entries(cache_file: Path) -> dict[str, str]:
    """Parse ``KEY:TYPE=VALUE`` lines from a CMakeCache.txt."""
    entries: dict[str, str] = {}
    for line in cache_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("#", "//")) or "=" not in line:
            continue
        key_type, _, value = line.partition("=")
        key = key_type.partition(":")[0]
        # A key can appear as both a user and an INTERNAL entry; whichever is set is the one in effect.
        if value or key not in entries:
            entries[key] = value
    return entries


def _expected(index: PresetIndex, preset: str, key: str) -> str:
    """The platform/toolset value CMake will pass for *preset*; empty when CMake leaves it alone."""
    value = index.field("configure", preset, key)
    if isinstance(value, dict):
        return (
            "" if value.get("strategy") == "external" else str(value.get("value", ""))
        )
    return value or ""


def _describe(generator: str, platform: str, toolset: str) -> str:
    parts = [generator or "no generator"]
    parts.extend(
        f"{label} {value}"
        for label, value in (("platform", platform), ("toolset", toolset))
        if value
    )
    return ", ".join(parts)


def stale_cache_hint(index: PresetIndex, preset: str) -> str | None:
    """Explain when the build directory was configured with a different generator, platform or toolset."""
    binary_dir = index.binary_dir(preset)
    if (
        binary_dir is None
        or not (cache_file := binary_dir / "CMakeCache.txt").is_file()
    ):
        return None

    cache = _cache_entries(cache_file)
    cached = (
        cache.get("CMAKE_GENERATOR", ""),
        cache.get("CMAKE_GENERATOR_PLATFORM", ""),
        cache.get("CMAKE_GENERATOR_TOOLSET", ""),
    )
    wanted = (
        index.field("configure", preset, "generator") or cached[0],
        _expected(index, preset, "architecture"),
        _expected(index, preset, "toolset"),
    )
    if cached == wanted:
        return None
    return (
        f"{binary_dir} was configured with {_describe(*cached)}, but preset '{preset}' uses {_describe(*wanted)}. "
        "Delete that directory and configure again."
    )
