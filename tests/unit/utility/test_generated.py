"""Unit tests for generated file ownership tracking."""

import logging
from pathlib import Path

import pytest

from cppython.utility.generated import (
    GeneratedFileError,
    GeneratedFilePolicy,
    GeneratedFileState,
    classify,
    render,
    synchronize,
)

_LOGGER = logging.getLogger('test')


class TestClassify:
    """Tests for classify."""

    def test_missing(self, tmp_path: Path) -> None:
        """A file that doesn't exist is missing."""
        assert classify(tmp_path / 'file.py', render('body\n')) is GeneratedFileState.MISSING

    def test_current(self, tmp_path: Path) -> None:
        """A file with the expected content is current."""
        path = tmp_path / 'file.py'
        path.write_text(render('body\n'), encoding='utf-8')

        assert classify(path, render('body\n')) is GeneratedFileState.CURRENT

    def test_stale(self, tmp_path: Path) -> None:
        """An untouched file from older inputs is stale."""
        path = tmp_path / 'file.py'
        path.write_text(render('old\n'), encoding='utf-8')

        assert classify(path, render('new\n')) is GeneratedFileState.STALE

    def test_modified(self, tmp_path: Path) -> None:
        """A file whose body no longer matches its header hash is modified."""
        path = tmp_path / 'file.py'
        path.write_text(render('old\n') + 'edit\n', encoding='utf-8')

        assert classify(path, render('new\n')) is GeneratedFileState.MODIFIED

    def test_headerless_is_modified(self, tmp_path: Path) -> None:
        """A file without a header or legacy marker is treated as hand-written."""
        path = tmp_path / 'file.py'
        path.write_text('hand written\n', encoding='utf-8')

        assert classify(path, render('new\n')) is GeneratedFileState.MODIFIED

    def test_legacy_marker_is_stale(self, tmp_path: Path) -> None:
        """A file from a generator without headers counts as untouched."""
        path = tmp_path / 'file.py'
        path.write_text('# auto-generated\nbody\n', encoding='utf-8')

        assert classify(path, render('new\n'), ('auto-generated',)) is GeneratedFileState.STALE

    def test_crlf_is_current(self, tmp_path: Path) -> None:
        """Line ending conversion by git doesn't count as a change."""
        path = tmp_path / 'file.py'
        path.write_bytes(render('a\nb\n').replace('\n', '\r\n').encode('utf-8'))

        assert classify(path, render('a\nb\n')) is GeneratedFileState.CURRENT

    def test_crlf_stale_is_not_modified(self, tmp_path: Path) -> None:
        """The header hash ignores line endings, so a CRLF checkout of an old file stays stale."""
        path = tmp_path / 'file.py'
        path.write_bytes(render('a\nb\n').replace('\n', '\r\n').encode('utf-8'))

        assert classify(path, render('c\n')) is GeneratedFileState.STALE


class TestSynchronize:
    """Tests for synchronize under each policy."""

    @pytest.mark.parametrize('policy', [GeneratedFilePolicy.UPDATE, GeneratedFilePolicy.FORCE])
    def test_writes_missing(self, tmp_path: Path, policy: GeneratedFilePolicy) -> None:
        """Writable policies create a missing file."""
        path = tmp_path / 'file.py'

        assert synchronize(path, render('body\n'), policy, _LOGGER) is GeneratedFileState.MISSING
        assert path.read_text(encoding='utf-8') == render('body\n')

    def test_update_regenerates_stale(self, tmp_path: Path) -> None:
        """UPDATE rewrites an untouched file."""
        path = tmp_path / 'file.py'
        path.write_text(render('old\n'), encoding='utf-8')

        assert synchronize(path, render('new\n'), GeneratedFilePolicy.UPDATE, _LOGGER) is GeneratedFileState.STALE
        assert path.read_text(encoding='utf-8') == render('new\n')

    def test_update_refuses_modified(self, tmp_path: Path) -> None:
        """UPDATE refuses to overwrite hand edits and suggests --force-regenerate."""
        path = tmp_path / 'file.py'
        edited = render('old\n') + 'edit\n'
        path.write_text(edited, encoding='utf-8')

        with pytest.raises(GeneratedFileError) as error:
            synchronize(path, render('new\n'), GeneratedFilePolicy.UPDATE, _LOGGER)

        assert error.value.state is GeneratedFileState.MODIFIED
        assert '-edit' in error.value.diff
        assert any('--force-regenerate' in note for note in error.value.__notes__)
        assert path.read_text(encoding='utf-8') == edited

    def test_force_overwrites_modified(self, tmp_path: Path) -> None:
        """FORCE overwrites hand edits and warns."""
        path = tmp_path / 'file.py'
        path.write_text(render('old\n') + 'edit\n', encoding='utf-8')

        synchronize(path, render('new\n'), GeneratedFilePolicy.FORCE, _LOGGER)

        assert path.read_text(encoding='utf-8') == render('new\n')

    @pytest.mark.parametrize(
        ('initial', 'state'),
        [
            (None, GeneratedFileState.MISSING),
            (render('old\n'), GeneratedFileState.STALE),
            (render('old\n') + 'edit\n', GeneratedFileState.MODIFIED),
        ],
        ids=['missing', 'stale', 'modified'],
    )
    def test_locked_never_writes(self, tmp_path: Path, initial: str | None, state: GeneratedFileState) -> None:
        """LOCKED reports every difference and changes nothing."""
        path = tmp_path / 'file.py'
        if initial is not None:
            path.write_text(initial, encoding='utf-8')

        with pytest.raises(GeneratedFileError) as error:
            synchronize(path, render('new\n'), GeneratedFilePolicy.LOCKED, _LOGGER)

        assert error.value.state is state
        assert any("'cppython install" in note for note in error.value.__notes__)
        if initial is None:
            assert not path.exists()
        else:
            assert path.read_text(encoding='utf-8') == initial

    def test_locked_passes_when_current(self, tmp_path: Path) -> None:
        """LOCKED succeeds when nothing differs."""
        path = tmp_path / 'file.py'
        path.write_text(render('body\n'), encoding='utf-8')

        assert synchronize(path, render('body\n'), GeneratedFilePolicy.LOCKED, _LOGGER) is GeneratedFileState.CURRENT
