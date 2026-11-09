"""Versioned, allow-listed metadata from native dependency preparation."""

from pathlib import Path
from typing import Literal

from cppython.core.schema import CPPythonModel

PREPARATION_REPORT_ENV = 'CPPYTHON_PREPARATION_REPORT'


class PreparationMetadata(CPPythonModel, extra='forbid'):
    """Facts from the selected project, not evidence of a native build or test."""

    schema_version: Literal['1'] = '1'
    identity: str | None = None
    backend: str
    provider: str
    dependency_outputs: tuple[Path, ...] | None = None


def write_preparation_report(path: Path, metadata: PreparationMetadata) -> None:
    """Write one report to an explicit front-end-owned path without overwriting another writer."""
    if not path.is_absolute():
        raise ValueError('CPPYTHON_PREPARATION_REPORT must be an absolute report file path')
    try:
        with path.open('x', encoding='utf-8') as report:
            report.write(metadata.model_dump_json() + '\n')
    except OSError as error:
        raise OSError(f'Cannot write CPPython preparation report: {path}') from error
