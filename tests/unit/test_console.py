"""Tests the typer interface type"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import typer
from typer.testing import CliRunner

from cppython.console.entry import _parse_groups_argument, app, install, update
from cppython.utility.exception import ConfigurationRequiredError

runner = CliRunner()


class TestConsole:
    """Various that all the examples are accessible to cppython. The project should be mocked so nothing executes"""

    @staticmethod
    def test_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verifies that the entry functions with CPPython hooks"""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, [])


class TestParseGroupsArgument:
    """Tests for the _parse_groups_argument helper function"""

    @staticmethod
    def test_none_input() -> None:
        """Test that None input returns None"""
        assert _parse_groups_argument(None) is None

    @staticmethod
    def test_empty_string() -> None:
        """Test that empty string returns None"""
        assert _parse_groups_argument('') is None
        assert _parse_groups_argument('   ') is None

    @staticmethod
    def test_single_group() -> None:
        """Test parsing a single group"""
        result = _parse_groups_argument('[test]')
        assert result == ['test']

    @staticmethod
    def test_multiple_groups() -> None:
        """Test parsing multiple groups"""
        result = _parse_groups_argument('[dev,test]')
        assert result == ['dev', 'test']

    @staticmethod
    def test_groups_with_spaces() -> None:
        """Test parsing groups with whitespace"""
        result = _parse_groups_argument('[dev, test, docs]')
        assert result == ['dev', 'test', 'docs']

    @staticmethod
    def test_missing_brackets() -> None:
        """Test that missing brackets raises an error"""
        with pytest.raises(typer.BadParameter, match='Invalid groups format'):
            _parse_groups_argument('test')

    @staticmethod
    def test_missing_opening_bracket() -> None:
        """Test that missing opening bracket raises an error"""
        with pytest.raises(typer.BadParameter, match='Invalid groups format'):
            _parse_groups_argument('test]')

    @staticmethod
    def test_missing_closing_bracket() -> None:
        """Test that missing closing bracket raises an error"""
        with pytest.raises(typer.BadParameter, match='Invalid groups format'):
            _parse_groups_argument('[test')

    @staticmethod
    def test_empty_brackets() -> None:
        """Test that empty brackets raises an error"""
        with pytest.raises(typer.BadParameter, match='Empty groups specification'):
            _parse_groups_argument('[]')

    @staticmethod
    def test_empty_group_name() -> None:
        """Test that empty group names raise an error"""
        with pytest.raises(typer.BadParameter, match='Group names cannot be empty'):
            _parse_groups_argument('[test,,dev]')

    @staticmethod
    def test_whitespace_only_group() -> None:
        """Test that whitespace-only group names raise an error"""
        with pytest.raises(typer.BadParameter, match='Group names cannot be empty'):
            _parse_groups_argument('[test,  ,dev]')


class TestInstallAndUpdate:
    """Tests install and update command composition."""

    @staticmethod
    def test_install_configures_after_install(monkeypatch: pytest.MonkeyPatch) -> None:
        """The install command should pass groups and configuration through in order."""
        calls: list[tuple[str, Any]] = []

        class ProjectStub:
            def install(self, groups: list[str] | None = None) -> None:
                calls.append(('install', groups))

            def configure(self, configuration: str | None = None) -> None:
                calls.append(('configure', configuration))

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        install(context=cast(typer.Context, object()), groups='[test]', configuration='dev')

        assert calls == [('install', ['test']), ('configure', 'dev')]

    @staticmethod
    def test_update_configures_after_update(monkeypatch: pytest.MonkeyPatch) -> None:
        """The update command should pass groups and configuration through in order."""
        calls: list[tuple[str, Any]] = []

        class ProjectStub:
            def update(self, groups: list[str] | None = None) -> None:
                calls.append(('update', groups))

            def configure(self, configuration: str | None = None) -> None:
                calls.append(('configure', configuration))

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        update(context=cast(typer.Context, object()), groups='[test]', configuration='dev')

        assert calls == [('update', ['test']), ('configure', 'dev')]

    @staticmethod
    def test_install_reports_missing_configuration(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing default configuration should not turn installation into a failure."""

        class ProjectStub:
            def install(self, groups: list[str] | None = None) -> None:
                pass

            def configure(self, configuration: str | None = None) -> None:
                raise ConfigurationRequiredError('no default configuration')

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        install(context=cast(typer.Context, object()))

    @staticmethod
    @pytest.mark.parametrize('command', ['install', 'update'])
    def test_install_and_update_expose_configuration_option(command: str) -> None:
        """Both existing setup commands should accept a named configuration."""
        result = runner.invoke(app, [command, '--help'])

        assert result.exit_code == 0
        assert '--configuration' in result.stdout
