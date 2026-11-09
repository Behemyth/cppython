"""Tests the Data type"""

import logging

import pytest

from cppython.builder import Builder
from cppython.core.identity import BuildIdentity
from cppython.core.resolution import PluginBuildData
from cppython.core.schema import (
    CPPythonLocalConfiguration,
    GeneratorData,
    PEP621Configuration,
    ProjectConfiguration,
    ProviderData,
)
from cppython.data import Data
from cppython.test.mock.generator import MockGenerator
from cppython.test.mock.provider import MockProvider
from cppython.test.mock.scm import MockSCM
from cppython.utility.utility import TypeName


class TestData:
    """Various tests for the Data type"""

    @staticmethod
    @pytest.fixture(
        name='data',
    )
    def fixture_data(
        project_configuration: ProjectConfiguration,
        pep621_configuration: PEP621Configuration,
        cppython_local_configuration: CPPythonLocalConfiguration,
    ) -> Data:
        """Creates a mock plugins fixture.

        We want all the plugins to use the same data variants at the same time, so we
        have to resolve data inside the fixture instead of using other data fixtures

        Args:
            project_configuration: Variant fixture for the project configuration
            pep621_configuration: Variant fixture for PEP 621 configuration
            cppython_local_configuration: Variant fixture for cppython configuration

        Returns:
            The mock plugins fixture

        """
        logger = logging.getLogger()
        builder = Builder(project_configuration, logger)

        plugin_build_data = PluginBuildData(generator_type=MockGenerator, provider_type=MockProvider, scm_type=MockSCM)

        return builder.build(pep621_configuration, cppython_local_configuration, plugin_build_data)

    @staticmethod
    def test_sync(data: Data) -> None:
        """Verifies that the sync method executes without error

        Args:
            data: Fixture for the mocked data class
        """
        data.sync()

    @staticmethod
    @pytest.mark.parametrize('has_outputs', [False, True])
    def test_preparation_metadata(data: Data, monkeypatch: pytest.MonkeyPatch, has_outputs: bool) -> None:
        """Metadata uses the already-resolved selection and provider-owned locations."""
        cppython = data._core_data.cppython_data
        cppython.identity = BuildIdentity(
            name='explicit-selection',
            os='windows',
            arch='x64',
            compiler='msvc',
            standard_library='msvc',
            configurations=['Debug'],
            dependency_build_types={'Debug': 'Debug'},
        )
        cppython.provider_data['password'] = 'never-emit-this'
        outputs = (cppython.build_path / 'provider-chosen-output',)
        if has_outputs:
            monkeypatch.setattr(data.plugins.provider, 'preparation_outputs', lambda: outputs, raising=False)
        metadata = data.preparation_metadata()
        assert metadata.identity == 'explicit-selection'
        assert metadata.backend == data.plugins.generator.name()
        assert metadata.provider == data.plugins.provider.name()
        assert metadata.dependency_outputs == (outputs if has_outputs else None)
        assert 'never-emit-this' not in metadata.model_dump_json()

    @staticmethod
    def test_named_plugin_configuration() -> None:
        """Test that named plugin configuration is properly validated"""
        # Test valid named configuration
        config = CPPythonLocalConfiguration(
            providers={TypeName('conan'): ProviderData({'some_setting': 'value'})},
            generators={TypeName('cmake'): GeneratorData({'another_setting': True})},
        )
        assert config.providers == {TypeName('conan'): {'some_setting': 'value'}}
        assert config.generators == {TypeName('cmake'): {'another_setting': True}}

        # Test empty configuration is valid
        config_empty = CPPythonLocalConfiguration()
        assert config_empty.providers == {}
        assert config_empty.generators == {}
