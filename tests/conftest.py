"""Shared test configuration"""

import pytest

from cppython.local_sources import ENVIRONMENT_VARIABLE


@pytest.fixture(autouse=True)
def _isolated_local_sources(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own local-source selection out of every test"""
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, str(tmp_path_factory.mktemp('local-sources') / 'local-sources.toml'))
