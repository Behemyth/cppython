"""Tests for the CPPython PEP 517 build preparation path."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cppython.build.prepare import BuildPreparation
from cppython.utility.exception import InstallationVerificationError, ProviderInstallationError


class TestBuildPreparation:
    """Tests dependency preparation without standalone build-tree configuration."""

    @staticmethod
    def test_installed_native_artifacts_are_reused(tmp_path: Path, mocker: MockerFixture) -> None:
        """Existing dependencies do not trigger installation or configuration."""
        (tmp_path / 'pyproject.toml').write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding='utf-8'
        )
        project = mocker.Mock(enabled=True)
        sync_data = project.prepare_build.return_value
        project_type = mocker.patch('cppython.build.prepare.Project', return_value=project)

        result = BuildPreparation(tmp_path).prepare()

        assert result.sync_data is sync_data
        project_type.assert_called_once()
        project.prepare_build.assert_called_once_with()
        project.install.assert_not_called()
        project.configure.assert_not_called()

    @staticmethod
    @pytest.mark.parametrize('sdist', [False, True])
    def test_build_installs_missing_native_artifacts(tmp_path: Path, mocker: MockerFixture, sdist: bool) -> None:
        """Checkout and sdist builds install dependencies but never configure."""
        (tmp_path / 'pyproject.toml').write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding='utf-8'
        )
        if sdist:
            (tmp_path / 'PKG-INFO').write_text('Metadata-Version: 2.4\n', encoding='utf-8')
        sync_data = mocker.Mock()
        project = mocker.Mock(enabled=True)
        side_effects = [InstallationVerificationError('mock', ['artifact']), sync_data]
        project.prepare_build.side_effect = side_effects
        mocker.patch('cppython.build.prepare.Project', return_value=project)

        result = BuildPreparation(tmp_path).prepare()

        assert result.sync_data is sync_data
        project.install.assert_called_once_with()
        assert project.prepare_build.call_count == len(side_effects)
        project.configure.assert_not_called()

    @staticmethod
    def test_installation_failure_is_propagated(tmp_path: Path, mocker: MockerFixture) -> None:
        """Provider failures are not retried or hidden."""
        (tmp_path / 'pyproject.toml').write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding='utf-8'
        )
        project = mocker.Mock(enabled=True)
        project.prepare_build.side_effect = InstallationVerificationError('mock', ['artifact'])
        project.install.side_effect = ProviderInstallationError('mock', 'installation failed')
        mocker.patch('cppython.build.prepare.Project', return_value=project)

        with pytest.raises(ProviderInstallationError, match='installation failed'):
            BuildPreparation(tmp_path).prepare()

        project.install.assert_called_once_with()
        project.prepare_build.assert_called_once_with()
        project.configure.assert_not_called()

    @staticmethod
    def test_missing_artifacts_after_installation_are_reported(tmp_path: Path, mocker: MockerFixture) -> None:
        """A successful install must still produce the expected artifacts."""
        (tmp_path / 'pyproject.toml').write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding='utf-8'
        )
        project = mocker.Mock(enabled=True)
        project.prepare_build.side_effect = InstallationVerificationError('mock', ['artifact'])
        mocker.patch('cppython.build.prepare.Project', return_value=project)

        with pytest.raises(InstallationVerificationError, match='mock'):
            BuildPreparation(tmp_path).prepare()

        project.install.assert_called_once_with()
        assert project.mock_calls == [mocker.call.prepare_build(), mocker.call.install(), mocker.call.prepare_build()]
        project.configure.assert_not_called()
