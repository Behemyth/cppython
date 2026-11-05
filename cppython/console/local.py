"""`cppython local`: choose which managed dependencies build from local checkouts.

Commands never prompt. `--json` prints a versioned `LocalSourceResult` on stdout, and the exit code
follows `ExitCode`, so scripts, agents, Porringer, and Spurtle can drive the same contract as people.
"""

import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cppython.local_sources import operations
from cppython.local_sources.schema import EffectivePackage, ExitCode, LocalSourceResult, Policy, Severity

local_app = typer.Typer(
    no_args_is_help=True,
    help='Choose which managed dependencies build from local checkouts instead of published packages.',
)

JsonOption = Annotated[bool, typer.Option('--json', help='Print a machine-readable result on stdout.')]
DryRunOption = Annotated[bool, typer.Option('--dry-run', help='Report the planned changes without writing them.')]
DiscoverOption = Annotated[
    bool,
    typer.Option(
        '--discover', help='Also manage CPPython projects directly under the root whose folder matches their name.'
    ),
]

_console = Console(highlight=False, soft_wrap=True)
_errors = Console(stderr=True, highlight=False, soft_wrap=True)


class Selection(StrEnum):
    """Which packages develop locally by default"""

    ALL = 'all'
    NONE = 'none'


def _write_json(document: object) -> None:
    sys.stdout.write(json.dumps(document, indent=2) + '\n')


def _finish(result: LocalSourceResult, as_json: bool) -> None:
    if as_json:
        _write_json(result.model_dump(mode='json'))
    else:
        _print_result(result)
    if code := result.exit_code:
        raise typer.Exit(code=int(code))


def _checkout_label(package: EffectivePackage) -> str:
    if not package.path:
        return '-'
    if not package.exists:
        return f'{package.path} (missing)'
    revision = (package.commit or 'unknown')[:12] + (' (dirty)' if package.dirty else '')
    return f'{package.path} @ {revision}'


def _print_packages(packages: list[EffectivePackage]) -> None:
    rows = [('package', 'mode', 'reason', 'checkout')]
    rows.extend(
        (package.name, package.mode.value, package.reason.value, _checkout_label(package)) for package in packages
    )
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    for row in rows:
        line = '  '.join(cell.ljust(width) for cell, width in zip(row[:3], widths, strict=True))
        _console.print(f'{line}  {row[3]}', markup=False)


def _print_result(result: LocalSourceResult) -> None:
    if result.command != 'list':
        if result.changed:
            prefix = 'Would change' if result.dry_run else 'Changed'
            _console.print(f'{prefix}: ' + ', '.join(change.field for change in result.changes))
        elif result.ok:
            _console.print('Already up to date.')
    for clone in result.planned_clones:
        _console.print(f'{"Would clone" if result.dry_run else "Cloned"} {clone}')

    if result.effective:
        _print_packages(result.effective)
    elif result.command == 'list':
        _console.print('No managed packages. Every dependency uses its published package.')

    for diagnostic in result.diagnostics:
        stream = _errors if diagnostic.severity is Severity.ERROR else _console
        stream.print(f'{diagnostic.severity.value}: {diagnostic.message}', markup=False)
        if diagnostic.fix:
            stream.print(f'  fix: {diagnostic.fix}', markup=False)


@local_app.command('list')
def list_command(as_json: JsonOption = False) -> None:
    """Show each managed package's effective mode, the reason, and its checkout commit."""
    _finish(operations.show(), as_json)


@local_app.command()
def use(
    selection: Annotated[Selection, typer.Argument(help='`all` develops every managed package locally.')],
    root: Annotated[Path | None, typer.Option(help='Folder containing checkouts named after packages.')] = None,
    discover: DiscoverOption = False,
    as_json: JsonOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Set the default for every managed package and clear exceptions to the old default."""
    policy = Policy.LOCAL if selection is Selection.ALL else Policy.STABLE
    _finish(operations.use(policy, root=root, discover_packages=discover, dry_run=dry_run), as_json)


@local_app.command()
def root(
    folder: Annotated[Path, typer.Argument(help='Folder containing checkouts named after packages.')],
    discover: DiscoverOption = False,
    as_json: JsonOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Set the folder where checkouts are found as <folder>/<package>."""
    _finish(operations.set_root(folder, discover_packages=discover, dry_run=dry_run), as_json)


@local_app.command()
def add(
    package: Annotated[str, typer.Argument(help='Package name, as declared in its pyproject.toml.')],
    path: Annotated[Path | None, typer.Option(help="Checkout path, when it isn't <root>/<package>.")] = None,
    url: Annotated[str | None, typer.Option(help='Git URL to clone from and remember.')] = None,
    clone: Annotated[bool, typer.Option('--clone', help="Clone the checkout if it's missing.")] = False,
    as_json: JsonOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Build a package from its local checkout. Adding it again changes nothing."""
    _finish(operations.add(package, path=path, url=url, clone=clone, dry_run=dry_run), as_json)


@local_app.command()
def remove(
    package: Annotated[str, typer.Argument(help='Package name.')],
    forget: Annotated[bool, typer.Option('--forget', help='Also stop managing the package.')] = False,
    as_json: JsonOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Use a package's published version again. Removing it again changes nothing."""
    _finish(operations.remove(package, forget=forget, dry_run=dry_run), as_json)


@local_app.command()
def off(as_json: JsonOption = False, dry_run: DryRunOption = False) -> None:
    """Suspend every override and keep the selection for `on`."""
    _finish(operations.set_enabled(False, dry_run=dry_run), as_json)


@local_app.command()
def on(as_json: JsonOption = False, dry_run: DryRunOption = False) -> None:
    """Resume the suspended selection."""
    _finish(operations.set_enabled(True, dry_run=dry_run), as_json)


@local_app.command('apply')
def apply_command(
    document: Annotated[
        str | None, typer.Argument(help='Intent document path (JSON or TOML), or `-` for stdin.')
    ] = None,
    inline: Annotated[
        str | None,
        typer.Option(help='Intent document text instead of a path; a relative root uses the current folder.'),
    ] = None,
    root: Annotated[Path | None, typer.Option(help="Checkout folder; overrides the document's root.")] = None,
    replace: Annotated[
        bool, typer.Option('--replace', help='Make the selection exactly the document instead of merging.')
    ] = False,
    clone: Annotated[
        bool, typer.Option('--clone', help='Clone missing checkouts of local packages from their `url` first.')
    ] = False,
    as_json: JsonOption = False,
    dry_run: DryRunOption = False,
) -> None:
    """Apply an intent document from a profile, agent, or `export`. The whole document is validated first."""
    if inline is not None and document is None:
        text, source, base = inline, '<inline>', Path.cwd()
    elif document is None or inline is not None:
        _errors.print('error: give either a document path or --inline', markup=False)
        raise typer.Exit(code=ExitCode.INVALID_INPUT)
    elif document == '-':
        text, source, base = sys.stdin.read(), '<stdin>', Path.cwd()
    else:
        location = Path(document).expanduser().absolute()
        try:
            text = location.read_text(encoding='utf-8')
        except OSError as error:
            _errors.print(f'error: cannot read {location}: {error}', markup=False)
            raise typer.Exit(code=ExitCode.INVALID_INPUT) from None
        source, base = str(location), location.parent
    result = operations.apply(
        text, source=source, base_directory=base, root=root, replace=replace, dry_run=dry_run, clone=clone
    )
    _finish(result, as_json)


@local_app.command()
def export() -> None:
    """Print the selection as a JSON intent document that `apply` reproduces."""
    _write_json(operations.export().model_dump(mode='json', exclude_none=True))


@local_app.command()
def schema(
    kind: Annotated[
        str, typer.Argument(help='`intent` (the apply document), `result` (--json output), or `state`.')
    ] = 'intent',
) -> None:
    """Print the JSON Schema for intent documents, command results, or stored state."""
    if kind not in operations.SCHEMAS:
        raise typer.BadParameter(f'Choose one of: {", ".join(operations.SCHEMAS)}', param_hint='KIND')
    _write_json(operations.schema(kind))
