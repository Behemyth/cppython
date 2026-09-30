"""Unit test the provider plugin"""

from typing import Any

import pytest

from cppython.plugins.cmake.plugin import CMakeGenerator
from cppython.plugins.cmake.schema import (
    CMakeConfiguration,
)
from cppython.test.pytest.contracts import GeneratorUnitTestContract
from cppython.utility.exception import ConfigurationRequiredError

pytest_plugins = ['tests.fixtures.cmake']


class TestCPPythonGenerator(GeneratorUnitTestContract[CMakeGenerator]):
    """The tests for the CMake generator"""

    @staticmethod
    @pytest.fixture(name='plugin_data', scope='session')
    def fixture_plugin_data(cmake_data: CMakeConfiguration) -> dict[str, Any]:
        """A required testing hook that allows data generation

        Args:
            cmake_data: The input data

        Returns:
            The constructed plugin data
        """
        return cmake_data.model_dump()

    @staticmethod
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type() -> type[CMakeGenerator]:
        """A required testing hook that allows type generation

        Returns:
            The type of the Generator
        """
        return CMakeGenerator

    @staticmethod
    def test_configure_uses_project_root_and_resolved_preset(plugin: CMakeGenerator, mocker: Any) -> None:
        """CMake presets must be invoked from the project root, not the managed preset file directory."""
        run_spy = mocker.patch('cppython.plugins.cmake.plugin.run_subprocess')

        plugin.configure('dev')

        run_spy.assert_called_once_with(
            [plugin._cmake_command(), '--preset', 'dev'],  # noqa: SLF001
            cwd=plugin.core_data.project_data.project_root,  # noqa: SLF001
            logger=mocker.ANY,
        )

    @staticmethod
    def test_build_test_bench_use_project_root(plugin: CMakeGenerator, mocker: Any) -> None:
        """All preset-driven operations should run from the root CMakePresets.json directory."""
        run_spy = mocker.patch('cppython.plugins.cmake.plugin.run_subprocess')
        operations = (plugin.build, plugin.test, plugin.bench)

        for operation in operations:
            operation('dev')

        assert run_spy.call_count == len(operations)
        assert all(
            call.kwargs['cwd'] == plugin.core_data.project_data.project_root  # noqa: SLF001
            for call in run_spy.call_args_list
        )

    @staticmethod
    def test_configure_requires_configuration(plugin: CMakeGenerator, mocker: Any) -> None:
        """A missing CMake default is reported distinctly so install can remain successful."""
        run_spy = mocker.patch('cppython.plugins.cmake.plugin.run_subprocess')

        with pytest.raises(ConfigurationRequiredError):
            plugin.configure()

        run_spy.assert_not_called()
