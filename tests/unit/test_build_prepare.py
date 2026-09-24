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
        """Build hooks must report missing native artifacts instead of installing them."""
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
