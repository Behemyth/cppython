"""Local-source operations shared by the CLI, the Python API, and build integration.

Every operation returns a `LocalSourceResult`. Mutating operations validate everything before writing,
hold the state lock for the read-modify-write, report `changed: false` when nothing differs, and write
nothing when `dry_run` is set or any error diagnostic is produced.
"""

import hashlib
import json
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import ValidationError

from cppython.local_sources.schema import (
    SCHEMA_VERSION,
    AppliedIntent,
    Change,
    Diagnostic,
    DiagnosticCode,
    EffectivePackage,
    LocalSourceIntent,
    LocalSourceResult,
    LocalSourceState,
    ManagedPackage,
    Policy,
    Reason,
    Severity,
    validate_package_name,
)
from cppython.local_sources.store import (
    LockContentionError,
    StateLocation,
    load_state,
    locate_state,
    state_lock,
    write_state,
)

_GIT_TIMEOUT = 30


class _Abort(Exception):
    """Stops a mutation after recording an error diagnostic"""


@dataclass
class _Mutation:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    planned_clones: list[str] = field(default_factory=list)

    def error(self, code: DiagnosticCode, message: str, package: str | None = None, fix: str | None = None) -> NoReturn:
        self.diagnostics.append(
            Diagnostic(code=code, severity=Severity.ERROR, message=message, package=package, fix=fix)
        )
        raise _Abort

    def note(
        self,
        code: DiagnosticCode,
        message: str,
        package: str | None = None,
        fix: str | None = None,
        severity: Severity = Severity.INFO,
    ) -> None:
        self.diagnostics.append(Diagnostic(code=code, severity=severity, message=message, package=package, fix=fix))


def canonical_name(name: str) -> str:
    """Normalize a package name for comparison"""
    return re.sub(r'[-_.]+', '-', name).lower()


def checkout_path(state: LocalSourceState, name: str) -> Path | None:
    """Where a managed package's checkout is expected, or None without a root or explicit path"""
    package = state.packages.get(name)
    if package is not None and package.path:
        return Path(package.path)
    if state.root:
        return Path(state.root) / name
    return None


def checkout_identity(path: Path) -> str | None:
    """The `[project] name` declared by a checkout's pyproject.toml, if any"""
    pyproject = path / 'pyproject.toml'
    if not pyproject.is_file():
        return None
    try:
        return tomllib.loads(pyproject.read_text(encoding='utf-8')).get('project', {}).get('name')
    except tomllib.TOMLDecodeError, OSError:
        return None


def _git(path: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ['git', '-C', str(path), *arguments],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return completed.stdout if completed.returncode == 0 else None


def checkout_revision(path: Path) -> tuple[str | None, bool | None]:
    """The checkout's HEAD commit and whether its working tree has changes"""
    head = _git(path, 'rev-parse', 'HEAD')
    status = _git(path, 'status', '--porcelain', '--untracked-files=normal')
    return (head.strip() if head else None, None if status is None else bool(status.strip()))


def _effective_mode(state: LocalSourceState, name: str) -> Policy:
    if name in state.local:
        return Policy.LOCAL
    if name in state.stable:
        return Policy.STABLE
    return state.default


def resolve(
    state: LocalSourceState, location: StateLocation, inspect_checkouts: bool = True
) -> tuple[list[EffectivePackage], list[Diagnostic]]:
    """Resolve every managed package to its effective mode and checkout details"""
    effective: list[EffectivePackage] = []
    diagnostics: list[Diagnostic] = []

    if location.suspended_by_environment:
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.SUSPENDED,
                severity=Severity.INFO,
                message='CPPYTHON_LOCAL_SOURCES=off: every package resolves to its published version.',
            )
        )
    elif not state.enabled:
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.SUSPENDED,
                severity=Severity.INFO,
                message='Local sources are suspended: every package resolves to its published version.',
                fix='cppython local on',
            )
        )

    for name in sorted(state.packages):
        selected = _effective_mode(state, name)
        if location.suspended_by_environment:
            mode, reason = Policy.STABLE, Reason.ENVIRONMENT
        elif not state.enabled:
            mode, reason = Policy.STABLE, Reason.SUSPENDED
        else:
            mode = selected
            reason = Reason.DEFAULT if selected is state.default else Reason.EXCEPTION

        path = checkout_path(state, name)
        exists = path is not None and path.is_dir()
        commit, dirty = checkout_revision(path) if exists and inspect_checkouts and path else (None, None)
        effective.append(
            EffectivePackage(
                name=name,
                mode=mode,
                reason=reason,
                path=str(path) if path else None,
                exists=exists,
                commit=commit,
                dirty=dirty,
            )
        )

        if mode is Policy.LOCAL and not exists:
            diagnostics.append(_missing_checkout(state, name, path, Severity.WARNING))

    return effective, diagnostics


def _missing_checkout(state: LocalSourceState, name: str, path: Path | None, severity: Severity) -> Diagnostic:
    package = state.packages.get(name)
    clone = f'cppython local add {name} --clone' + ('' if package and package.url else ' --url <git-url>')
    where = str(path) if path else '<no root or path configured>'
    return Diagnostic(
        code=DiagnosticCode.MISSING_CHECKOUT if path else DiagnosticCode.NO_ROOT,
        severity=severity,
        message=f"'{name}' is selected for local development, but its checkout is missing: {where}",
        package=name,
        fix=f'{clone}  (or `cppython local remove {name}` to use the published package)',
    )


def _check_identity(mutation: _Mutation, name: str, path: Path) -> None:
    identity = checkout_identity(path)
    if identity is None:
        mutation.error(
            DiagnosticCode.IDENTITY_MISMATCH,
            f"{path} has no pyproject.toml with a [project] name, so it can't be verified as '{name}'.",
            package=name,
            fix=f'cppython local add {name} --path <checkout of {name}>',
        )
    elif canonical_name(identity) != canonical_name(name):
        mutation.error(
            DiagnosticCode.IDENTITY_MISMATCH,
            f"{path} declares project '{identity}', not '{name}'.",
            package=name,
            fix=f'cppython local add {name} --path <checkout of {name}>',
        )


def _state_view(state: LocalSourceState) -> dict[str, Any]:
    data = state.model_dump(mode='json', exclude={'applied'})
    packages = data.pop('packages')
    for name, package in packages.items():
        data[f'packages.{name}'] = package
    return data


def _changes(before: LocalSourceState, after: LocalSourceState) -> list[Change]:
    old, new = _state_view(before), _state_view(after)
    return [
        Change(field=key, before=old.get(key), after=new.get(key))
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]


def _result(  # noqa: PLR0913
    command: str,
    location: StateLocation,
    state: LocalSourceState,
    mutation: _Mutation,
    *,
    changes: list[Change] | None = None,
    dry_run: bool = False,
    inspect_checkouts: bool = True,
) -> LocalSourceResult:
    effective, resolved = resolve(state, location, inspect_checkouts)
    diagnostics = mutation.diagnostics + resolved
    return LocalSourceResult(
        command=command,
        ok=not any(diagnostic.severity is Severity.ERROR for diagnostic in diagnostics),
        changed=bool(changes),
        dry_run=dry_run,
        state_path=str(location.path),
        changes=changes or [],
        planned_clones=mutation.planned_clones,
        state=state,
        effective=effective,
        diagnostics=diagnostics,
    )


def _mutate(  # noqa: PLR0913
    command: str,
    mutate: Callable[[LocalSourceState, _Mutation], None],
    *,
    location: StateLocation | None,
    dry_run: bool,
    mutation: _Mutation | None = None,
    provenance: AppliedIntent | None = None,
) -> LocalSourceResult:
    location = location or locate_state()
    mutation = mutation or _Mutation()

    try:
        with state_lock(location.path):
            before = load_state(location.path)
            after = before.model_copy(deep=True)
            try:
                mutate(after, mutation)
                after = LocalSourceState.model_validate(after.model_dump())
            except _Abort:
                return _result(command, location, before, mutation, dry_run=dry_run)
            except ValidationError as error:
                mutation.diagnostics.append(
                    Diagnostic(code=DiagnosticCode.INVALID_INPUT, severity=Severity.ERROR, message=str(error))
                )
                return _result(command, location, before, mutation, dry_run=dry_run)

            changes = _changes(before, after)
            if changes and provenance is not None:
                after.applied = provenance
            if changes and not dry_run:
                write_state(location.path, after)
            return _result(command, location, after, mutation, changes=changes, dry_run=dry_run)
    except LockContentionError as error:
        mutation.diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.LOCK_CONTENTION,
                severity=Severity.ERROR,
                message=f'Another CPPython process is changing local sources: {error}',
                fix='Retry the command.',
            )
        )
        return _result(command, location, load_state(location.path), mutation, dry_run=dry_run)


def _absolute(path: str | Path, base: Path | None = None) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    return str(candidate.resolve())


def _origin_url(path: Path) -> str | None:
    url = _git(path, 'remote', 'get-url', 'origin')
    return url.strip() if url else None


def discover(root: Path) -> dict[str, ManagedPackage]:
    """CPPython projects directly under `root` whose folder name matches their project name"""
    found: dict[str, ManagedPackage] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        identity = checkout_identity(child) if child.is_dir() else None
        if identity is None or canonical_name(identity) != canonical_name(child.name):
            continue
        try:
            data = tomllib.loads((child / 'pyproject.toml').read_text(encoding='utf-8'))
            validate_package_name(child.name)
        except tomllib.TOMLDecodeError, OSError, ValueError:
            continue
        if 'cppython' in data.get('tool', {}):
            found[child.name] = ManagedPackage(url=_origin_url(child))
    return found


def _register_discovered(state: LocalSourceState, mutation: _Mutation) -> None:
    if not state.root:
        mutation.error(
            DiagnosticCode.NO_ROOT,
            'Discovery needs a checkout root.',
            fix='Pass --root <folder>.',
        )
    for name, package in discover(Path(state.root)).items():
        existing = state.packages.get(name)
        if existing is None:
            state.packages[name] = package
        elif existing.url is None:
            existing.url = package.url


def show(location: StateLocation | None = None, inspect_checkouts: bool = True) -> LocalSourceResult:
    """Report the current selection and each package's effective state"""
    location = location or locate_state()
    return _result('list', location, load_state(location.path), _Mutation(), inspect_checkouts=inspect_checkouts)


def use(
    policy: Policy,
    root: str | Path | None = None,
    discover_packages: bool = False,
    location: StateLocation | None = None,
    dry_run: bool = False,
) -> LocalSourceResult:
    """Set the default policy and clear exceptions that only restated the opposite default.

    With `discover_packages`, CPPython projects found directly under the root become managed.
    """

    def mutate(state: LocalSourceState, mutation: _Mutation) -> None:
        state.default = policy
        if policy is Policy.LOCAL:
            state.stable = []
        else:
            state.local = []
        if root is not None:
            state.root = _absolute(root)
        if discover_packages:
            _register_discovered(state, mutation)

    return _mutate('use', mutate, location=location, dry_run=dry_run)


def set_root(
    root: str | Path, discover_packages: bool = False, location: StateLocation | None = None, dry_run: bool = False
) -> LocalSourceResult:
    """Set the folder that contains checkouts named after their packages"""

    def mutate(state: LocalSourceState, mutation: _Mutation) -> None:
        state.root = _absolute(root)
        if discover_packages:
            _register_discovered(state, mutation)

    return _mutate('root', mutate, location=location, dry_run=dry_run)


def set_enabled(enabled: bool, location: StateLocation | None = None, dry_run: bool = False) -> LocalSourceResult:
    """Suspend or resume every override while keeping the selection"""

    def mutate(state: LocalSourceState, _: _Mutation) -> None:
        state.enabled = enabled

    return _mutate('on' if enabled else 'off', mutate, location=location, dry_run=dry_run)


def _clone(url: str, path: Path) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(['git', 'clone', url, str(path)], capture_output=True, text=True, check=False)
    except OSError as error:
        return str(error)
    return None if completed.returncode == 0 else (completed.stderr.strip() or f'git exited {completed.returncode}')


def add(  # noqa: PLR0913
    name: str,
    path: str | Path | None = None,
    url: str | None = None,
    clone: bool = False,
    location: StateLocation | None = None,
    dry_run: bool = False,
) -> LocalSourceResult:
    """Select a package's checkout for local development, cloning it first when asked"""
    location = location or locate_state()
    mutation = _Mutation()
    explicit_path = _absolute(path) if path is not None else None

    def plan(state: LocalSourceState) -> Path:
        try:
            validate_package_name(name)
        except ValueError as error:
            mutation.error(DiagnosticCode.INVALID_INPUT, str(error), package=name)

        package = state.packages.get(name, ManagedPackage()).model_copy()
        package.path = explicit_path or package.path
        package.url = url or package.url
        state.packages[name] = package

        target = checkout_path(state, name)
        if target is None:
            mutation.error(
                DiagnosticCode.NO_ROOT,
                f"There is no checkout root and no --path for '{name}'.",
                package=name,
                fix=f'cppython local root <folder>  (or `cppython local add {name} --path <checkout>`)',
            )
        return target

    # Clone outside the lock so a slow network doesn't block other selection changes.
    try:
        target = plan(load_state(location.path).model_copy(deep=True))
        if not target.is_dir():
            known_url = url or load_state(location.path).packages.get(name, ManagedPackage()).url
            if not clone:
                fix = f'cppython local add {name} --clone' + ('' if known_url else ' --url <git-url>')
                mutation.error(
                    DiagnosticCode.MISSING_CHECKOUT, f"The checkout for '{name}' is missing: {target}", name, fix
                )
            if not known_url:
                mutation.error(
                    DiagnosticCode.INVALID_INPUT,
                    f"Can't clone '{name}' without a URL.",
                    package=name,
                    fix=f'cppython local add {name} --clone --url <git-url>',
                )
            mutation.planned_clones.append(f'{known_url} -> {target}')
            if not dry_run and (failure := _clone(str(known_url), target)):
                mutation.error(DiagnosticCode.CLONE_FAILED, f"Cloning '{name}' failed: {failure}", name)
        if target.is_dir():
            _check_identity(mutation, name, target)
    except _Abort:
        return _result('add', location, load_state(location.path), mutation, dry_run=dry_run)

    def mutate(state: LocalSourceState, _: _Mutation) -> None:
        plan(state)
        if state.default is Policy.LOCAL:
            state.stable = [item for item in state.stable if item != name]
            state.local = [item for item in state.local if item != name]
        else:
            state.local = [*state.local, name]

    return _mutate('add', mutate, location=location, dry_run=dry_run, mutation=mutation)


def remove(
    name: str, forget: bool = False, location: StateLocation | None = None, dry_run: bool = False
) -> LocalSourceResult:
    """Return a package to its published version, or stop managing it with `forget`"""

    def mutate(state: LocalSourceState, mutation: _Mutation) -> None:
        if name not in state.packages:
            mutation.note(DiagnosticCode.NOT_MANAGED, f"'{name}' isn't managed; nothing to change.", package=name)
            return
        state.local = [item for item in state.local if item != name]
        state.stable = [item for item in state.stable if item != name]
        if forget:
            del state.packages[name]
        elif state.default is Policy.LOCAL:
            state.stable = [*state.stable, name]

    return _mutate('remove', mutate, location=location, dry_run=dry_run)


def parse_intent(text: str) -> LocalSourceIntent:
    """Parse a JSON or TOML intent document.

    Raises:
        ValueError: If the text isn't valid JSON/TOML or doesn't match the intent schema
    """
    try:
        data = json.loads(text) if text.lstrip().startswith('{') else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f'The document is neither valid JSON nor valid TOML: {error}') from error
    try:
        return LocalSourceIntent.model_validate(data)
    except ValidationError as error:
        raise ValueError(str(error)) from error


def _digest(intent: LocalSourceIntent) -> str:
    canonical = json.dumps(intent.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest()


def _clone_missing(state: LocalSourceState, mutation: _Mutation, dry_run: bool) -> None:
    """Clone every local package whose checkout is missing, using its recorded URL"""
    for name in sorted(state.packages):
        target = checkout_path(state, name)
        if _effective_mode(state, name) is not Policy.LOCAL or target is None or target.is_dir():
            continue
        url = state.packages[name].url
        if not url:
            mutation.error(
                DiagnosticCode.INVALID_INPUT,
                f"Can't clone '{name}' without a URL.",
                package=name,
                fix=f'Give `{name}` a `url` in the document, or clone it to {target} yourself.',
            )
        mutation.planned_clones.append(f'{url} -> {target}')
        if not dry_run and (failure := _clone(url, target)):
            mutation.error(DiagnosticCode.CLONE_FAILED, f"Cloning '{name}' failed: {failure}", name)


def apply(  # noqa: PLR0913
    text: str,
    source: str = '<stdin>',
    base_directory: Path | None = None,
    root: str | Path | None = None,
    replace: bool = False,
    location: StateLocation | None = None,
    dry_run: bool = False,
    clone: bool = False,
) -> LocalSourceResult:
    """Apply an intent document.

    By default the document is merged: fields it sets win, and exceptions it doesn't mention are kept
    and reported. With `replace`, the selection becomes exactly the document. With `clone`, local
    packages whose checkouts are missing are cloned from their URLs before the selection is saved.
    """
    location = location or locate_state()
    mutation = _Mutation()

    try:
        intent = parse_intent(text)
    except ValueError as error:
        mutation.diagnostics.append(
            Diagnostic(code=DiagnosticCode.INVALID_INPUT, severity=Severity.ERROR, message=str(error))
        )
        return _result('apply', location, load_state(location.path), mutation, dry_run=dry_run)

    base = base_directory or Path.cwd()
    resolved_root = (
        _absolute(root) if root is not None else (_absolute(intent.root, base) if intent.root is not None else None)
    )

    def mutate(state: LocalSourceState, current: _Mutation) -> None:
        local, stable = set(intent.local or []), set(intent.stable or [])
        if overlap := local & stable:
            current.error(
                DiagnosticCode.INVALID_INPUT, f'Packages listed as both local and stable: {", ".join(sorted(overlap))}'
            )

        previous = set(state.local) | set(state.stable)
        if replace:
            state.root = resolved_root
            state.default = intent.default or Policy.STABLE
            state.enabled = True if intent.enabled is None else intent.enabled
            state.packages = {}
            state.local, state.stable = [], []
        else:
            state.root = resolved_root or state.root
            state.default = intent.default or state.default
            state.enabled = state.enabled if intent.enabled is None else intent.enabled

        for name, package in (intent.packages or {}).items():
            merged = state.packages.get(name, ManagedPackage()).model_copy()
            merged.url = package.url or merged.url
            merged.path = _absolute(package.path, base) if package.path else merged.path
            state.packages[name] = merged

        state.local = sorted((set(state.local) - stable) | local)
        state.stable = sorted((set(state.stable) - local) | stable)

        if unmanaged := (set(state.local) | set(state.stable)) - state.packages.keys():
            current.error(
                DiagnosticCode.INVALID_INPUT,
                f"Exceptions name packages that aren't managed: {', '.join(sorted(unmanaged))}",
                fix='Add them under `packages` in the document.',
            )

        for name in sorted(state.packages):
            path = checkout_path(state, name)
            if _effective_mode(state, name) is Policy.LOCAL and path is not None and path.is_dir():
                _check_identity(current, name, path)

        if not replace:
            for name in sorted(previous - local - stable):
                current.note(
                    DiagnosticCode.PRESERVED_EXCEPTION,
                    f"Kept the existing exception for '{name}' because the document doesn't mention it.",
                    package=name,
                    fix=f'cppython local remove {name}  (or apply with --replace)',
                )

    provenance = AppliedIntent(source=source, digest=_digest(intent))
    if clone:
        # Plan on a copy and clone outside the lock, like `add`; the real pass reports any planning error.
        planned = load_state(location.path).model_copy(deep=True)
        try:
            mutate(planned, _Mutation())
        except _Abort:
            pass
        else:
            try:
                _clone_missing(planned, mutation, dry_run)
            except _Abort:
                return _result('apply', location, load_state(location.path), mutation, dry_run=dry_run)
    return _mutate('apply', mutate, location=location, dry_run=dry_run, mutation=mutation, provenance=provenance)


def export(location: StateLocation | None = None) -> LocalSourceIntent:
    """The current selection as an intent document that `apply` reproduces"""
    location = location or locate_state()
    return load_state(location.path).to_intent()


SCHEMAS: dict[str, type[LocalSourceIntent | LocalSourceState | LocalSourceResult]] = {
    'intent': LocalSourceIntent,
    'state': LocalSourceState,
    'result': LocalSourceResult,
}


def schema(kind: str = 'intent') -> dict[str, Any]:
    """The JSON Schema for an intent document, the stored state, or a command result"""
    model = SCHEMAS[kind]
    document = model.model_json_schema()
    document['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    document['$id'] = f'https://synodic.software/cppython/local-sources/{kind}/v{SCHEMA_VERSION}.json'
    return document


def dependency_names(pyproject_data: dict[str, Any], include_groups: bool = True) -> set[str]:
    """Canonical names of the native dependencies a project declares, optionally with dependency groups"""
    table = pyproject_data.get('tool', {}).get('cppython', {})
    requirements: list[str] = list(table.get('dependencies', []))
    if include_groups:
        for group in table.get('dependency-groups', {}).values():
            requirements.extend(group)

    names: set[str] = set()
    for requirement in requirements:
        try:
            names.add(canonical_name(Requirement(requirement).name))
        except InvalidRequirement:
            continue
    return names


@dataclass
class ProjectSelection:
    """The local checkouts that apply to one project's dependency graph"""

    local: list[EffectivePackage]
    """Usable local checkouts, dependencies before the packages that require them"""
    diagnostics: list[Diagnostic]
    requires: dict[str, list[str]] = field(default_factory=dict)
    """For each local checkout, the other local checkouts its own declared dependencies name"""
    managed: bool = False
    """Whether the user's selection manages any package, used to report an all-published graph"""

    @property
    def ok(self) -> bool:
        """Whether every selected checkout is usable"""
        return not any(diagnostic.severity is Severity.ERROR for diagnostic in self.diagnostics)


def _checkout_dependencies(path: Path) -> set[str]:
    """The base (non-group) native dependencies a checkout declares; its tests are never built for consumers"""
    try:
        data = tomllib.loads((path / 'pyproject.toml').read_text(encoding='utf-8'))
    except OSError, tomllib.TOMLDecodeError:
        return set()
    return dependency_names(data, include_groups=False)


def _dependency_order(names: Iterable[str], requires: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Order *names* so dependencies come first; also return the names that form a cycle"""
    order: list[str] = []
    done: set[str] = set()
    active: list[str] = []
    cycle: list[str] = []

    def visit(name: str) -> None:
        if name in done or cycle:
            return
        if name in active:
            cycle.extend([*active[active.index(name) :], name])
            return
        active.append(name)
        for dependency in requires.get(name, []):
            visit(dependency)
        active.pop()
        done.add(name)
        order.append(name)

    for name in sorted(names):
        visit(name)
    return order, cycle


def project_selection(
    project_name: str, dependencies: Iterable[str], location: StateLocation | None = None
) -> ProjectSelection:
    """Select the local checkouts relevant to a project, in dependency order.

    Selection starts from the project's declared dependencies and follows each selected checkout's own
    declared dependencies, so a local package required only through another local package applies too.
    The project itself is never overridden. A selected checkout that is missing or isn't the named
    package is an error rather than a silent fallback to the published package. A published package
    between two local ones is not inspected, so local packages reachable only through it don't apply.
    """
    location = location or locate_state()
    state = load_state(location.path)
    effective, _ = resolve(state, location)
    selected = {canonical_name(package.name): package for package in effective if package.mode is Policy.LOCAL}
    project = canonical_name(project_name)

    usable: dict[str, EffectivePackage] = {}
    requires: dict[str, list[str]] = {}
    diagnostics: list[Diagnostic] = []
    pending = sorted({canonical_name(name) for name in dependencies} - {project}, reverse=True)
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in selected:
            continue
        seen.add(name)
        package = selected[name]
        if not package.exists:
            diagnostics.append(
                _missing_checkout(state, package.name, checkout_path(state, package.name), Severity.ERROR)
            )
            continue
        mutation = _Mutation()
        try:
            _check_identity(mutation, package.name, Path(str(package.path)))
        except _Abort:
            diagnostics.extend(mutation.diagnostics)
            continue
        declared = _checkout_dependencies(Path(str(package.path)))
        if project in declared:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.DEPENDENCY_CYCLE,
                    severity=Severity.ERROR,
                    message=f"Local '{package.name}' requires '{project_name}', the project being built.",
                    package=package.name,
                    fix=f'cppython local remove {package.name}',
                )
            )
            continue
        usable[name] = package
        requires[name] = sorted(dependency for dependency in declared if dependency in selected)
        pending.extend(sorted(requires[name], reverse=True))

    for name, names in requires.items():
        requires[name] = [dependency for dependency in names if dependency in usable]

    order, cycle = _dependency_order(usable, requires)
    if cycle:
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.DEPENDENCY_CYCLE,
                severity=Severity.ERROR,
                message='Local checkouts require each other in a cycle: ' + ' -> '.join(cycle),
                package=cycle[0],
                fix=f'cppython local remove {cycle[0]}',
            )
        )
        order = []

    return ProjectSelection(
        local=[usable[name] for name in order],
        diagnostics=diagnostics,
        requires={name: requires[name] for name in order},
        managed=bool(state.packages),
    )
