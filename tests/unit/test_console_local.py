"""Tests for the `cppython local` command group"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cppython.console.entry import app
from cppython.local_sources.schema import ExitCode
from cppython.local_sources.store import ENVIRONMENT_VARIABLE

runner = CliRunner()


@pytest.fixture(autouse=True)
def fixture_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run outside any project with a throwaway state file"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, str(tmp_path / 'state.toml'))
    checkout = tmp_path / 'src' / 'honesty'
    checkout.mkdir(parents=True)
    (checkout / 'pyproject.toml').write_text('[project]\nname = "honesty"\n[tool.cppython]\n', encoding='utf-8')


def test_works_outside_a_project() -> None:
    """Local commands don't need a pyproject.toml in the working directory"""
    result = runner.invoke(app, ['local', 'list'])
    assert result.exit_code == 0, result.output
    assert 'No managed packages' in result.output


def test_json_contract(tmp_path: Path) -> None:
    """--json prints a versioned result and repeated commands report no change"""
    first = runner.invoke(app, ['local', 'use', 'all', '--root', str(tmp_path / 'src'), '--discover', '--json'])
    second = runner.invoke(app, ['local', 'use', 'all', '--root', str(tmp_path / 'src'), '--discover', '--json'])

    assert first.exit_code == 0
    first_result, second_result = json.loads(first.stdout), json.loads(second.stdout)
    assert first_result['schema_version'] == 1
    assert first_result['changed']
    assert not second_result['changed']
    assert second_result['effective'][0]['name'] == 'honesty'


def test_exit_code_and_fix() -> None:
    """Errors use documented exit codes and carry a fix"""
    result = runner.invoke(app, ['local', 'add', 'honesty', '--json'])
    assert result.exit_code == ExitCode.INVALID_INPUT
    assert json.loads(result.stdout)['diagnostics'][0]['fix']


def test_apply_from_stdin_and_export(tmp_path: Path) -> None:
    """An agent can apply a document from stdin and round-trip it through export"""
    document = json.dumps({'schema_version': 1, 'root': 'src', 'default': 'local', 'packages': {'honesty': {}}})

    planned = runner.invoke(app, ['local', 'apply', '-', '--dry-run', '--json'], input=document)
    assert json.loads(planned.stdout)['changed']
    assert not (tmp_path / 'state.toml').exists()

    applied = runner.invoke(app, ['local', 'apply', '-', '--json'], input=document)
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)['state']['root'] == str((tmp_path / 'src').resolve())

    exported = runner.invoke(app, ['local', 'export'])
    again = runner.invoke(app, ['local', 'apply', '-', '--replace', '--json'], input=exported.stdout)
    assert not json.loads(again.stdout)['changed']


def test_invalid_document_exit_code() -> None:
    """Invalid documents exit with the invalid-input code"""
    result = runner.invoke(app, ['local', 'apply', '-'], input='{"schema_version": 9}')
    assert result.exit_code == ExitCode.INVALID_INPUT


def test_apply_inline(tmp_path: Path) -> None:
    """--inline takes the document as an argument, and exactly one source is required"""
    document = json.dumps({'schema_version': 1, 'root': str(tmp_path / 'src'), 'packages': {'honesty': {}}})

    applied = runner.invoke(app, ['local', 'apply', '--inline', document, '--json'])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)['changed']

    assert runner.invoke(app, ['local', 'apply']).exit_code == ExitCode.INVALID_INPUT
    both = runner.invoke(app, ['local', 'apply', '-', '--inline', document], input=document)
    assert both.exit_code == ExitCode.INVALID_INPUT


def test_schema_command() -> None:
    """The schema command prints JSON Schema and rejects unknown kinds"""
    result = runner.invoke(app, ['local', 'schema', 'result'])
    assert json.loads(result.stdout)['title'] == 'CPPython local-source result'
    assert runner.invoke(app, ['local', 'schema', 'nope']).exit_code == ExitCode.INVALID_INPUT
