"""Tests for CMake preset relationships and stale build directory detection"""

import json
from pathlib import Path

import pytest
from cppython.plugins.cmake.presets import PresetIndex, stale_cache_hint
from cppython.utility.exception import ConfigurationRequiredError


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(name="index")
def fixture_index(tmp_path: Path) -> PresetIndex:
    """A project whose presets live in an included file and use inheritance."""
    _write(
        tmp_path / "CMakePresets.json", {"version": 9, "include": ["tool/presets.json"]}
    )
    _write(
        tmp_path / "tool" / "presets.json",
        {
            "version": 9,
            "configurePresets": [
                {
                    "name": "base",
                    "hidden": True,
                    "binaryDir": "${sourceDir}/build/${presetName}",
                },
                {
                    "name": "ninja",
                    "inherits": "base",
                    "generator": "Ninja Multi-Config",
                },
                {
                    "name": "vs",
                    "inherits": "base",
                    "generator": "Visual Studio 18 2026",
                    "architecture": "x64",
                },
            ],
            "buildPresets": [
                {"name": "ninja-base", "hidden": True, "configurePreset": "ninja"},
                {
                    "name": "ninja-debug",
                    "inherits": "ninja-base",
                    "configuration": "Debug",
                },
                {
                    "name": "ninja-release",
                    "inherits": "ninja-base",
                    "configuration": "Release",
                },
                {"name": "vs-debug", "configurePreset": "vs"},
            ],
            "testPresets": [{"name": "ninja-debug", "configurePreset": "ninja"}],
        },
    )
    return PresetIndex(tmp_path)


def test_build_preset_maps_to_its_configure_preset(index: PresetIndex) -> None:
    """Configuring with a build preset name uses that preset's (inherited) configure preset."""
    assert index.resolve("configure", "ninja-debug") == "ninja"


def test_configure_preset_maps_to_its_only_build_preset(index: PresetIndex) -> None:
    """A configure preset with exactly one build preset resolves to it."""
    assert index.resolve("build", "vs") == "vs-debug"
    assert index.resolve("test", "ninja") == "ninja-debug"


def test_configure_preset_with_several_build_presets_lists_them(
    index: PresetIndex,
) -> None:
    """Ambiguity is reported with the choices instead of guessing."""
    with pytest.raises(ConfigurationRequiredError, match="ninja-debug, ninja-release"):
        index.resolve("build", "ninja")


def test_matching_and_unknown_names_are_unchanged(index: PresetIndex) -> None:
    """Names that already match, and names CMake should report, pass through."""
    assert index.resolve("build", "ninja-release") == "ninja-release"
    assert index.resolve("configure", "missing") == "missing"
    assert index.resolve("configure", "base") == "base"


def _cache(index: PresetIndex, preset: str, **entries: str) -> None:
    binary_dir = index.source_dir / "build" / preset
    binary_dir.mkdir(parents=True)
    lines = [f"{key}:INTERNAL={value}" for key, value in entries.items()]
    (binary_dir / "CMakeCache.txt").write_text(
        "\n".join(["# comment", *lines]), encoding="utf-8"
    )


def test_stale_platform_is_explained(index: PresetIndex) -> None:
    """A platform left in the cache by an earlier configure is reported."""
    _cache(
        index,
        "ninja",
        CMAKE_GENERATOR="Ninja Multi-Config",
        CMAKE_GENERATOR_PLATFORM="x64",
    )

    hint = stale_cache_hint(index, "ninja")

    assert hint is not None
    assert "Ninja Multi-Config, platform x64" in hint
    assert "preset 'ninja' uses Ninja Multi-Config." in hint


def test_matching_cache_has_no_hint(index: PresetIndex) -> None:
    """A cache that matches the preset produces no hint."""
    _cache(
        index,
        "vs",
        CMAKE_GENERATOR="Visual Studio 18 2026",
        CMAKE_GENERATOR_PLATFORM="x64",
    )

    assert stale_cache_hint(index, "vs") is None


def test_missing_cache_has_no_hint(index: PresetIndex) -> None:
    """Without a previous configure there is nothing to explain."""
    assert stale_cache_hint(index, "ninja") is None
