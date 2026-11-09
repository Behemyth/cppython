"""Tests the typer interface type"""

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import typer
from typer.testing import CliRunner

from cppython.console.entry import _parse_groups_argument, app, configure, install
from cppython.console.schema import ConsoleConfiguration
from cppython.utility.exception import ConfigurationRequiredError
from cppython.utility.generated import GeneratedFilePolicy

runner = CliRunner()


class TestConsole:
    """Various that all the examples are accessible to cppython. The project should be mocked so nothing executes"""

    @staticmethod
    def test_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verifies that the entry functions with CPPython hooks"""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, [])

    @staticmethod
    def test_global_identity_reaches_project_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
        """One explicit identity applies to the selected command."""
        selected: list[str | None] = []

        @contextmanager
        def session_project(context: typer.Context) -> Any:
            configuration = context.find_object(ConsoleConfiguration)
            assert configuration is not None
            selected.append(configuration.project_configuration.identity)
            yield type('ProjectStub', (), {'install': lambda self, groups=None: None})()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)
        result = runner.invoke(app, ['--identity', 'linux-x64-clang-libcxx', 'install'])

        assert result.exit_code == 0
        assert selected == ['linux-x64-clang-libcxx']


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

        result = runner.invoke(app, ['install', '[test]', '--configure', '--configuration', 'dev'])

        assert result.exit_code == 0, result.output
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

        result = runner.invoke(app, ['update', '[test]', '--configure', '--configuration', 'dev'])

        assert result.exit_code == 0, result.output
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

        install(context=cast(typer.Context, object()), configure=True)

    @staticmethod
    @pytest.mark.parametrize('command', ['install', 'update'])
    def test_install_and_update_expose_configuration_option(command: str) -> None:
        """Both existing setup commands should accept a named configuration."""
        result = runner.invoke(app, [command, '--help'])
        # CI terminals enable Rich styling, which splits option names with ANSI escapes.
        output = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout)

        assert result.exit_code == 0
        assert '--configuration' in output
        assert '--configure' in output
        assert '--no-configure' in output

    @staticmethod
    @pytest.mark.parametrize('command', ['install', 'update'])
    @pytest.mark.parametrize('options', [[], ['--no-configure']])
    def test_dependency_only_command(command: str, options: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Commands leave configuration alone by default and with no-configure."""
        calls: list[tuple[str, Any]] = []

        class ProjectStub:
            def install(self, groups: list[str] | None = None) -> None:
                calls.append(('install', groups))

            def update(self, groups: list[str] | None = None) -> None:
                calls.append(('update', groups))

            def configure(self, configuration: str | None = None) -> None:
                pytest.fail('Dependency-only commands must not configure the build tree')

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        result = runner.invoke(app, [command, '[test]', *options])

        assert result.exit_code == 0, result.output
        assert calls == [(command, ['test'])]

    @staticmethod
    def test_configure_uses_named_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit configuration does not install or update dependencies."""
        calls: list[str | None] = []

        class ProjectStub:
            def configure(self, configuration: str | None = None) -> None:
                calls.append(configuration)

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        result = runner.invoke(app, ['configure', '--configuration', 'dev'])

        assert result.exit_code == 0, result.output
        assert calls == ['dev']

    @staticmethod
    def test_configure_reports_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit configuration must not suppress missing configuration errors."""

        class ProjectStub:
            def configure(self, configuration: str | None = None) -> None:
                raise ConfigurationRequiredError('no default configuration')

        @contextmanager
        def session_project(_context: Any) -> Any:
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)

        with pytest.raises(ConfigurationRequiredError, match='no default configuration'):
            configure(context=cast(typer.Context, object()))


class TestGeneratedFilePolicy:
    """Tests the generated file policy flags and the check command."""

    @staticmethod
    def _capture_policy(monkeypatch: pytest.MonkeyPatch) -> list[GeneratedFilePolicy]:
        policies: list[GeneratedFilePolicy] = []

        class ProjectStub:
            def install(self, groups: list[str] | None = None) -> None:
                pass

            def update(self, groups: list[str] | None = None) -> None:
                pass

            def check(self) -> None:
                policies.append(GeneratedFilePolicy.LOCKED)

        @contextmanager
        def session_project(context: typer.Context) -> Any:
            configuration = context.find_object(ConsoleConfiguration)
            assert configuration is not None
            policies.append(configuration.project_configuration.generated_files)
            yield ProjectStub()

        monkeypatch.setattr('cppython.console.entry._session_project', session_project)
        return policies

    @staticmethod
    @pytest.mark.parametrize('command', ['install', 'update'])
    @pytest.mark.parametrize(
        ('flags', 'policy'),
        [
            ([], GeneratedFilePolicy.UPDATE),
            (['--locked'], GeneratedFilePolicy.LOCKED),
            (['--force-regenerate'], GeneratedFilePolicy.FORCE),
        ],
    )
    def test_flags_set_policy(
        monkeypatch: pytest.MonkeyPatch, command: str, flags: list[str], policy: GeneratedFilePolicy
    ) -> None:
        """--locked and --force-regenerate select the policy the project runs under."""
        policies = TestGeneratedFilePolicy._capture_policy(monkeypatch)

        result = runner.invoke(app, [command, *flags])

        assert result.exit_code == 0, result.output
        assert policies == [policy]

    @staticmethod
    def test_flags_are_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
        """--locked and --force-regenerate contradict each other."""
        policies = TestGeneratedFilePolicy._capture_policy(monkeypatch)

        result = runner.invoke(app, ['install', '--locked', '--force-regenerate'])

        assert result.exit_code != 0
        assert policies == []

    @staticmethod
    def test_check_command(monkeypatch: pytest.MonkeyPatch) -> None:
        """The check command runs the project check."""
        policies = TestGeneratedFilePolicy._capture_policy(monkeypatch)

        result = runner.invoke(app, ['check'])

        assert result.exit_code == 0, result.output
        assert policies == [GeneratedFilePolicy.UPDATE, GeneratedFilePolicy.LOCKED]
