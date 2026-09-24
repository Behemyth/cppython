"""Unit test the Meson generator plugin"""

from typing import Any

import pytest
from cppython.plugins.meson.plugin import MesonGenerator
from cppython.plugins.meson.schema import (
    MesonConfiguration,
)
from cppython.test.pytest.contracts import GeneratorUnitTestContract

pytest_plugins = ["tests.fixtures.meson"]


class TestMesonGenerator(GeneratorUnitTestContract[MesonGenerator]):
    """The tests for the Meson generator"""

    @staticmethod
    @pytest.fixture(name="plugin_data", scope="session")
    def fixture_plugin_data(meson_data: MesonConfiguration) -> dict[str, Any]:
        """A required testing hook that allows data generation.

        Args:
            meson_data: The input data

        Returns:
            The constructed plugin data
        """
        return meson_data.model_dump()

    @staticmethod
    @pytest.fixture(name="plugin_type", scope="session")
    def fixture_plugin_type() -> type[MesonGenerator]:
        """A required testing hook that allows type generation.

        Returns:
            The type of the Generator
        """
        return MesonGenerator

    @staticmethod
    def test_configure_uses_named_build_directory(
        plugin: MesonGenerator, mocker: Any
    ) -> None:
        """Meson configure should set up the selected build directory."""
        setup_spy = mocker.patch.object(plugin, "_ensure_setup")

        plugin.configure("development")

        setup_spy.assert_called_once_with("development")

    @staticmethod
    def test_build_sets_up_the_selected_directory(
        plugin: MesonGenerator, mocker: Any
    ) -> None:
        """Meson build should configure the same directory it compiles."""
        mocker.patch("cppython.plugins.meson.plugin.run_subprocess")
        setup_spy = mocker.patch.object(plugin, "_ensure_setup")

        plugin.build("development")

        setup_spy.assert_called_once_with("development")
