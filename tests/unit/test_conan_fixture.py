"""Tests for isolation of Conan test configuration."""

from pathlib import Path

import pytest

from tests.fixtures.conan import _seed_conan_home


def test_seed_uses_selected_conan_home_without_copying_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The isolated home keeps CI compiler settings, not packages or an unrelated user profile."""
    selected = tmp_path / 'selected'
    fallback = tmp_path / 'fallback'
    destination = tmp_path / 'isolated'
    for root, content in ((selected, 'selected'), (fallback, 'fallback')):
        (root / 'profiles').mkdir(parents=True)
        (root / 'profiles' / 'default').write_text(content, encoding='utf-8')
    (selected / 'profiles' / 'build').write_text('build profile', encoding='utf-8')
    (selected / 'settings_user.yml').write_text('custom settings', encoding='utf-8')
    (selected / 'p').mkdir()
    destination.mkdir()
    monkeypatch.setenv('CONAN_HOME', str(selected))
    monkeypatch.setenv('CONAN_USER_HOME', str(fallback))

    _seed_conan_home(destination)

    assert (destination / 'profiles' / 'default').read_text(encoding='utf-8') == 'selected'
    assert (destination / 'profiles' / 'build').read_text(encoding='utf-8') == 'build profile'
    assert (destination / 'settings_user.yml').read_text(encoding='utf-8') == 'custom settings'
    assert not (destination / 'p').exists()


def test_seed_falls_back_to_configured_user_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicitly selected user home remains supported when CONAN_HOME is absent."""
    source = tmp_path / 'user'
    destination = tmp_path / 'isolated'
    (source / 'profiles').mkdir(parents=True)
    (source / 'profiles' / 'default_build').write_text('fallback', encoding='utf-8')
    destination.mkdir()
    monkeypatch.delenv('CONAN_HOME', raising=False)
    monkeypatch.setenv('CONAN_USER_HOME', str(source))

    _seed_conan_home(destination)

    assert (destination / 'profiles' / 'default_build').read_text(encoding='utf-8') == 'fallback'
