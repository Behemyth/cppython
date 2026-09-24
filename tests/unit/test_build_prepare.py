"""Tests for the CPPython PEP 517 build preparation path."""

from pathlib import Path

import pytest
from cppython.build.prepare import BuildPreparation
from cppython.utility.exception import InstallationVerificationError
from pytest_mock import MockerFixture


class TestBuildPreparation:
    """Tests that build preparation only verifies prior native installation."""

    @staticmethod
    def test_missing_native_artifacts_do_not_trigger_install(
        tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Checkout builds must report missing native artifacts instead of installing them."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        project = mocker.Mock(enabled=True)
        project.prepare_build.side_effect = InstallationVerificationError(
            "mock", ["artifact"]
        )
        project_type = mocker.patch(
            "cppython.build.prepare.Project", return_value=project
        )

        with pytest.raises(InstallationVerificationError, match="mock"):
            BuildPreparation(tmp_path).prepare()

        project_type.assert_called_once()
        project.install.assert_not_called()
        project.configure.assert_not_called()

    @staticmethod
    def test_sdist_build_installs_missing_native_artifacts(
        tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Builds from an extracted sdist install provider dependencies but never configure."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test-project"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (tmp_path / "PKG-INFO").write_text("Metadata-Version: 2.4\n", encoding="utf-8")
        sync_data = mocker.Mock()
        project = mocker.Mock(enabled=True)
        side_effects = [InstallationVerificationError("mock", ["artifact"]), sync_data]
        project.prepare_build.side_effect = side_effects
        mocker.patch("cppython.build.prepare.Project", return_value=project)

        result = BuildPreparation(tmp_path).prepare()

        assert result.sync_data is sync_data
        project.install.assert_called_once_with()
        assert project.prepare_build.call_count == len(side_effects)
        project.configure.assert_not_called()
