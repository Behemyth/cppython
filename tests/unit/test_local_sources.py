"""Tests for user-level local-source selection"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cppython.local_sources import operations, store
from cppython.local_sources.schema import DiagnosticCode, ExitCode, Policy, Reason
from cppython.local_sources.store import LockContentionError, StateLocation, load_state, state_lock


def _checkout(root: Path, name: str, project: str | None = None, cppython: bool = True) -> Path:
    path = root / name
    path.mkdir(parents=True)
    table = '\n[tool.cppython]\ndependencies = []\n' if cppython else ''
    (path / 'pyproject.toml').write_text(f'[project]\nname = "{project or name}"\n{table}', encoding='utf-8')
    return path


def _repository(parent: Path, name: str) -> Path:
    """A committed Git checkout that can be cloned"""
    path = _checkout(parent, name)
    for command in (
        ['init', '-q'],
        ['add', '.'],
        ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x'],
    ):
        subprocess.run(['git', '-C', str(path), *command], check=True)
    return path


@pytest.fixture(name='location')
def fixture_location(tmp_path: Path) -> StateLocation:
    """A state file isolated from the user's real selection"""
    return StateLocation(tmp_path / 'state' / 'local-sources.toml', False)


@pytest.fixture(name='root')
def fixture_root(tmp_path: Path) -> Path:
    """A checkout root with three managed projects and two folders that must be ignored"""
    root = tmp_path / 'src'
    for name in ('periapsis', 'honesty', 'soul-engine'):
        _checkout(root, name)
    _checkout(root, 'renamed', project='other')
    _checkout(root, 'plain', cppython=False)
    return root


def _codes(result: operations.LocalSourceResult) -> list[DiagnosticCode]:
    return [diagnostic.code for diagnostic in result.diagnostics]


class TestStateLocation:
    """CPPYTHON_LOCAL_SOURCES selects or suspends the state"""

    @staticmethod
    def test_default() -> None:
        """An unset variable uses the user-level file"""
        location = store.locate_state({})
        assert location == StateLocation(store.default_state_path(), False)

    @staticmethod
    def test_off() -> None:
        """`off` suspends overrides without moving the file"""
        assert store.locate_state({store.ENVIRONMENT_VARIABLE: 'OFF'}).suspended_by_environment

    @staticmethod
    def test_path(tmp_path: Path) -> None:
        """Any other value is the state path"""
        location = store.locate_state({store.ENVIRONMENT_VARIABLE: str(tmp_path / 'x.toml')})
        assert location.path == tmp_path / 'x.toml'


class TestPolicy:
    """Default policies, exceptions, and idempotence"""

    @staticmethod
    def test_missing_state_is_stable(location: StateLocation) -> None:
        """No file means nothing is local"""
        result = operations.show(location)
        assert result.ok
        assert not result.effective
        assert not location.path.exists()

    @staticmethod
    def test_use_all_discovers_only_matching_projects(location: StateLocation, root: Path) -> None:
        """Discovery is opt-in and ignores folders that aren't the named CPPython project"""
        result = operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        assert result.ok
        assert sorted(result.state.packages) == ['honesty', 'periapsis', 'soul-engine']
        assert all(package.mode is Policy.LOCAL for package in result.effective)

        again = operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)
        assert not again.changed

    @staticmethod
    def test_use_without_discovery_manages_nothing(location: StateLocation, root: Path) -> None:
        """Sibling folders never become local by themselves"""
        result = operations.use(Policy.LOCAL, root=root, location=location)
        assert not result.state.packages

    @staticmethod
    def test_remove_under_local_default_is_an_exception(location: StateLocation, root: Path) -> None:
        """Removing a package from an all-local selection keeps it managed but stable"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        result = operations.remove('periapsis', location=location)
        effective = {package.name: package for package in result.effective}
        assert result.changed
        assert effective['periapsis'].mode is Policy.STABLE
        assert effective['periapsis'].reason is Reason.EXCEPTION
        assert not operations.remove('periapsis', location=location).changed

        assert operations.add('periapsis', location=location).changed
        assert not operations.add('periapsis', location=location).changed
        assert load_state(location.path).stable == []

    @staticmethod
    def test_use_clears_opposing_exceptions(location: StateLocation, root: Path) -> None:
        """Switching to all-local drops stable exceptions"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)
        operations.remove('honesty', location=location)

        result = operations.use(Policy.LOCAL, location=location)
        assert result.state.stable == []

    @staticmethod
    def test_add_under_stable_default(location: StateLocation, root: Path) -> None:
        """Adding under the stable default records a local exception"""
        operations.set_root(root, location=location)
        result = operations.add('honesty', location=location)

        assert result.state.local == ['honesty']
        assert result.effective[0].reason is Reason.EXCEPTION

        removed = operations.remove('honesty', location=location)
        assert removed.state.local == []
        assert 'honesty' in removed.state.packages

        forgotten = operations.remove('honesty', forget=True, location=location)
        assert 'honesty' not in forgotten.state.packages

    @staticmethod
    def test_remove_unmanaged(location: StateLocation) -> None:
        """Removing an unmanaged package succeeds and changes nothing"""
        result = operations.remove('ghost', location=location)
        assert result.ok
        assert not result.changed
        assert _codes(result) == [DiagnosticCode.NOT_MANAGED]

    @staticmethod
    def test_dry_run_writes_nothing(location: StateLocation, root: Path) -> None:
        """Dry runs report changes without creating the file"""
        result = operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location, dry_run=True)
        assert result.changed
        assert result.dry_run
        assert not location.path.exists()


class TestAddValidation:
    """Selected checkouts must exist and be the named package"""

    @staticmethod
    def test_no_root(location: StateLocation) -> None:
        """Without a root or path, add explains how to set one"""
        result = operations.add('honesty', location=location)
        assert result.exit_code is ExitCode.INVALID_INPUT
        assert _codes(result) == [DiagnosticCode.NO_ROOT]
        assert not location.path.exists()

    @staticmethod
    def test_missing_checkout(location: StateLocation, root: Path) -> None:
        """A missing checkout is an error, never a silent fallback"""
        operations.set_root(root, location=location)
        result = operations.add('cppaste', location=location)

        assert result.exit_code is ExitCode.MISSING_CHECKOUT
        assert result.diagnostics[0].fix is not None
        assert '--clone' in result.diagnostics[0].fix
        assert 'cppaste' not in load_state(location.path).packages

    @staticmethod
    def test_identity_mismatch(location: StateLocation, root: Path) -> None:
        """A checkout declaring another project is rejected"""
        result = operations.add('honesty', path=root / 'renamed', location=location)
        assert result.exit_code is ExitCode.IDENTITY_MISMATCH
        assert not location.path.exists()

    @staticmethod
    def test_invalid_name(location: StateLocation, root: Path) -> None:
        """Package names are validated before anything happens"""
        operations.set_root(root, location=location)
        assert operations.add('Bad Name', location=location).exit_code is ExitCode.INVALID_INPUT

    @staticmethod
    def test_clone_dry_run_and_clone(location: StateLocation, root: Path, tmp_path: Path) -> None:
        """Clones are planned under --dry-run and performed otherwise"""
        upstream = _repository(tmp_path / 'upstream', 'cppaste')
        operations.set_root(root, location=location)

        planned = operations.add('cppaste', url=str(upstream), clone=True, location=location, dry_run=True)
        assert planned.ok
        assert planned.planned_clones
        assert not (root / 'cppaste').exists()

        cloned = operations.add('cppaste', url=str(upstream), clone=True, location=location)
        assert cloned.ok, cloned.diagnostics
        effective = {package.name: package for package in cloned.effective}
        assert effective['cppaste'].commit is not None
        assert effective['cppaste'].dirty is False
        assert cloned.state.packages['cppaste'].url == str(upstream)


class TestSuspension:
    """Overrides can be suspended without losing the selection"""

    @staticmethod
    def test_off_and_on(location: StateLocation, root: Path) -> None:
        """`off` makes everything stable and `on` restores it"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        off = operations.set_enabled(False, location=location)
        assert all(package.reason is Reason.SUSPENDED for package in off.effective)
        assert DiagnosticCode.SUSPENDED in _codes(off)

        on = operations.set_enabled(True, location=location)
        assert all(package.mode is Policy.LOCAL for package in on.effective)

    @staticmethod
    def test_environment_off(location: StateLocation, root: Path) -> None:
        """CPPYTHON_LOCAL_SOURCES=off wins over the file"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)
        suspended = StateLocation(location.path, True)

        result = operations.show(suspended)
        assert all(package.reason is Reason.ENVIRONMENT for package in result.effective)
        assert operations.project_selection('soul-engine', ['honesty'], suspended).local == []


class TestApply:
    """Intent documents from profiles and agents"""

    @staticmethod
    def _develop(root: Path) -> str:
        return json.dumps(
            {
                'schema_version': 1,
                'root': str(root),
                'default': 'local',
                'packages': {'periapsis': {'url': 'https://example.invalid/periapsis.git'}, 'honesty': {}},
            }
        )

    def test_merge_preserves_user_exceptions(self, location: StateLocation, root: Path) -> None:
        """Re-applying a profile keeps exceptions the user made and reports them"""
        operations.apply(self._develop(root), location=location)
        operations.remove('periapsis', location=location)

        result = operations.apply(self._develop(root), location=location)
        assert not result.changed
        assert result.state.stable == ['periapsis']
        assert DiagnosticCode.PRESERVED_EXCEPTION in _codes(result)

    def test_replace(self, location: StateLocation, root: Path) -> None:
        """--replace makes the state exactly the document"""
        operations.apply(self._develop(root), location=location)
        operations.remove('periapsis', location=location)

        result = operations.apply(self._develop(root), replace=True, location=location)
        assert result.changed
        assert result.state.stable == []

    def test_provenance_only_on_change(self, location: StateLocation, root: Path) -> None:
        """The applied digest is recorded with real changes and kept otherwise"""
        first = operations.apply(self._develop(root), source='profile-a', location=location)
        assert first.state.applied is not None
        assert first.state.applied.source == 'profile-a'

        operations.apply(self._develop(root), source='profile-b', location=location)
        applied = load_state(location.path).applied
        assert applied is not None
        assert applied.source == 'profile-a'

    def test_export_round_trip(self, location: StateLocation, root: Path) -> None:
        """`export` output applies with no changes in either mode"""
        operations.apply(self._develop(root), location=location)
        operations.remove('honesty', location=location)
        exported = operations.export(location).model_dump_json(exclude_none=True)

        assert not operations.apply(exported, location=location).changed
        assert not operations.apply(exported, replace=True, location=location).changed

    @staticmethod
    def test_toml_and_relative_root(location: StateLocation, root: Path) -> None:
        """TOML documents work and a relative root resolves against the document folder"""
        text = 'schema_version = 1\nroot = "src"\ndefault = "local"\n[packages.honesty]\n'
        result = operations.apply(text, base_directory=root.parent, location=location)
        assert result.ok
        assert result.state.root == str(root.resolve())

    def test_root_override(self, location: StateLocation, root: Path, tmp_path: Path) -> None:
        """Porringer passes the clone destination with --root"""
        result = operations.apply(self._develop(tmp_path / 'elsewhere'), root=root, location=location)
        assert result.state.root == str(root.resolve())

    @staticmethod
    @pytest.mark.parametrize(
        'document',
        [
            '{"default": "local"}',
            '{"schema_version": 2}',
            '{"schema_version": 1, "unknown": true}',
            '{"schema_version": 1, "packages": {"a": {}}, "local": ["a"], "stable": ["a"]}',
            '{"schema_version": 1, "local": ["ghost"]}',
            '{"schema_version": 1, "packages": {"Bad Name": {}}}',
            'not = [valid',
        ],
    )
    def test_invalid_documents_write_nothing(location: StateLocation, document: str) -> None:
        """Rejected documents leave the state untouched"""
        result = operations.apply(document, location=location)
        assert result.exit_code is ExitCode.INVALID_INPUT
        assert not location.path.exists()

    @staticmethod
    def test_identity_checked(location: StateLocation, root: Path) -> None:
        """A document can't select a checkout that is another project"""
        document = json.dumps(
            {
                'schema_version': 1,
                'default': 'local',
                'packages': {'honesty': {'path': str(root / 'renamed')}},
            }
        )
        assert operations.apply(document, location=location).exit_code is ExitCode.IDENTITY_MISMATCH

    @staticmethod
    def test_clone_missing_checkouts(location: StateLocation, root: Path, tmp_path: Path) -> None:
        """--clone fetches local packages that are missing and leaves existing checkouts alone"""
        upstream = _repository(tmp_path / 'upstream', 'cppaste')
        document = json.dumps(
            {
                'schema_version': 1,
                'root': str(root),
                'default': 'local',
                'packages': {'cppaste': {'url': str(upstream)}, 'honesty': {}},
            }
        )

        planned = operations.apply(document, clone=True, dry_run=True, location=location)
        assert planned.ok, planned.diagnostics
        assert planned.planned_clones == [f'{upstream} -> {root / "cppaste"}']
        assert not (root / 'cppaste').exists()
        assert not location.path.exists()

        applied = operations.apply(document, clone=True, location=location)
        assert applied.ok, applied.diagnostics
        assert {package.name: package.commit is not None for package in applied.effective}['cppaste']

        again = operations.apply(document, clone=True, location=location)
        assert not again.changed
        assert not again.planned_clones

    @staticmethod
    def test_clone_needs_a_url(location: StateLocation, root: Path) -> None:
        """A missing local checkout without a URL is rejected before anything is written"""
        document = json.dumps({'schema_version': 1, 'root': str(root), 'default': 'local', 'packages': {'ghost': {}}})

        result = operations.apply(document, clone=True, location=location)
        assert result.exit_code is ExitCode.INVALID_INPUT
        assert result.diagnostics[0].fix is not None
        assert not location.path.exists()


class TestStore:
    """Writes are atomic and serialized across processes"""

    @staticmethod
    def test_interrupted_write_keeps_previous_state(
        location: StateLocation, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure before replacement leaves the old file valid and no temporary files"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)
        before = location.path.read_bytes()

        def interrupted(*_: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(store.os, 'replace', interrupted)
        with pytest.raises(KeyboardInterrupt):
            operations.remove('honesty', location=location)

        assert location.path.read_bytes() == before
        assert sorted(path.name for path in location.path.parent.iterdir()) == [
            'local-sources.toml',
            'local-sources.toml.lock',
        ]

    @staticmethod
    def test_lock_contention(location: StateLocation, monkeypatch: pytest.MonkeyPatch) -> None:
        """A held lock produces a coded, retryable error"""
        acquired, release = threading.Event(), threading.Event()

        def hold() -> None:
            with state_lock(location.path):
                acquired.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        acquired.wait(10)
        try:
            with pytest.raises(LockContentionError), state_lock(location.path, timeout=0.1):
                pass

            monkeypatch.setattr(operations, 'state_lock', lambda path: state_lock(path, timeout=0.1))
            result = operations.set_enabled(False, location=location)
            assert result.exit_code is ExitCode.LOCK_CONTENTION
        finally:
            release.set()
            holder.join()

    @staticmethod
    def test_concurrent_processes_keep_every_update(location: StateLocation, root: Path) -> None:
        """Parallel `add` processes never lose each other's selections"""
        names = [f'pkg{index}' for index in range(8)]
        for name in names:
            _checkout(root, name)
        operations.set_root(root, location=location)

        environment = {**os.environ, store.ENVIRONMENT_VARIABLE: str(location.path)}
        # Widen the read-modify-write window so an unlocked implementation reliably loses updates.
        script = (
            'import sys, time\n'
            'from cppython.local_sources import operations\n'
            'read = operations.load_state\n'
            'def slow(path):\n'
            '    state = read(path)\n'
            '    time.sleep(0.2)\n'
            '    return state\n'
            'operations.load_state = slow\n'
            'sys.exit(int(operations.add(sys.argv[1]).exit_code))\n'
        )
        processes = [subprocess.Popen([sys.executable, '-c', script, name], env=environment) for name in names]
        assert [process.wait(120) for process in processes] == [0] * len(names)

        assert load_state(location.path).local == sorted(names)


class TestProjectSelection:
    """Builds only see selected checkouts in their own graph"""

    @staticmethod
    def test_filters_to_dependencies(location: StateLocation, root: Path) -> None:
        """Only dependencies apply, and the project itself is never overridden"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        selection = operations.project_selection('soul-engine', ['honesty', 'soul-engine', 'sdl'], location)
        assert selection.ok
        assert [package.name for package in selection.local] == ['honesty']

    @staticmethod
    def test_missing_checkout_is_error(location: StateLocation, root: Path) -> None:
        """A selected checkout that disappeared fails the build instead of using the package"""
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)
        (root / 'honesty' / 'pyproject.toml').unlink()
        (root / 'honesty').rmdir()

        selection = operations.project_selection('soul-engine', ['honesty'], location)
        assert not selection.ok
        assert selection.diagnostics[0].code is DiagnosticCode.MISSING_CHECKOUT

    @staticmethod
    def test_dependency_names() -> None:
        """Runtime and group requirements are both part of the graph"""
        data = {
            'tool': {
                'cppython': {
                    'dependencies': ['Periapsis>=0.0.0.dev1', 'vulkan-headers==1.4'],
                    'dependency-groups': {'test': ['honesty>=0.0.0.dev1']},
                }
            }
        }
        assert operations.dependency_names(data) == {'periapsis', 'vulkan-headers', 'honesty'}
        assert operations.dependency_names(data, include_groups=False) == {'periapsis', 'vulkan-headers'}

    @staticmethod
    def _requires(root: Path, name: str, *dependencies: str, groups: dict[str, list[str]] | None = None) -> None:
        text = f'[project]\nname = "{name}"\n\n[tool.cppython]\ndependencies = {json.dumps(list(dependencies))}\n'
        if groups:
            text += '\n[tool.cppython.dependency-groups]\n'
            text += ''.join(f'{group} = {json.dumps(members)}\n' for group, members in groups.items())
        (root / name / 'pyproject.toml').write_text(text, encoding='utf-8')

    def test_follows_local_dependencies_in_order(self, location: StateLocation, root: Path) -> None:
        """A local checkout's own local dependencies apply too, dependencies first"""
        _checkout(root, 'cppaste')
        self._requires(root, 'honesty', 'cppaste>=1', groups={'test': ['soul-engine']})
        self._requires(root, 'cppaste', 'periapsis>=1')
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        selection = operations.project_selection('soul-engine', ['honesty'], location)

        assert selection.ok
        assert [package.name for package in selection.local] == ['periapsis', 'cppaste', 'honesty']
        assert selection.requires == {'periapsis': [], 'cppaste': ['periapsis'], 'honesty': ['cppaste']}
        assert selection.managed

    def test_cycle_is_error(self, location: StateLocation, root: Path) -> None:
        """Checkouts that require each other can't be ordered"""
        self._requires(root, 'honesty', 'periapsis')
        self._requires(root, 'periapsis', 'honesty')
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        selection = operations.project_selection('soul-engine', ['honesty'], location)

        assert not selection.ok
        assert selection.local == []
        assert selection.diagnostics[0].code is DiagnosticCode.DEPENDENCY_CYCLE

    def test_checkout_requiring_the_project_is_error(self, location: StateLocation, root: Path) -> None:
        """A local dependency that requires the project being built would be a cycle"""
        self._requires(root, 'honesty', 'soul-engine')
        operations.use(Policy.LOCAL, root=root, discover_packages=True, location=location)

        selection = operations.project_selection('soul-engine', ['honesty'], location)

        assert not selection.ok
        assert selection.diagnostics[0].code is DiagnosticCode.DEPENDENCY_CYCLE

    @staticmethod
    def test_unmanaged_selection(location: StateLocation) -> None:
        """An empty selection applies nothing and manages nothing"""
        selection = operations.project_selection('soul-engine', ['honesty'], location)
        assert selection.ok
        assert selection.local == []
        assert not selection.managed


class TestSchema:
    """Agents can discover the document and result formats"""

    @staticmethod
    @pytest.mark.parametrize('kind', ['intent', 'state', 'result'])
    def test_schema(kind: str) -> None:
        """Every schema is a versioned JSON Schema document"""
        document = operations.schema(kind)
        assert document['$id'].endswith(f'/{kind}/v1.json')
        assert 'properties' in document
