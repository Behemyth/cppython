"""Unit tests for Conan builder functionality."""

from pathlib import Path
from textwrap import dedent

import pytest

from cppython.plugins.conan.builder import Builder
from cppython.plugins.conan.schema import ConanDependency, ConanfileGenerationData, ConanVersion
from cppython.utility.generated import GeneratedFileError, GeneratedFilePolicy, GeneratedFileState


class TestBuilder:
    """Test the Conan Builder class."""

    @pytest.fixture
    def builder(self) -> Builder:
        """Create a Builder instance for testing."""
        return Builder()

    def test_mixed_dependencies(self, builder: Builder) -> None:
        """Test base conanfile with both regular and test dependencies."""
        dependencies = [
            ConanDependency(name='boost', version=ConanVersion.from_string('1.80.0')),
        ]
        dependency_groups = {
            'test': [
                ConanDependency(name='gtest', version=ConanVersion.from_string('1.14.0')),
            ]
        }

        content = builder._base_conanfile_content(dependencies, dependency_groups)

        assert content.startswith('# cppython-generated: schema=1 sha256=')
        assert 'self.requires("boost/1.80.0")' in content
        assert 'self.test_requires("gtest/1.14.0")' in content

    def test_creates_both_files(self, builder: Builder, tmp_path: Path) -> None:
        """Test generate_conanfile creates both base and user files."""
        dependencies = [
            ConanDependency(name='boost', version=ConanVersion.from_string('1.80.0')),
        ]
        dependency_groups = {}

        data = ConanfileGenerationData(
            dependencies=dependencies,
            dependency_groups=dependency_groups,
            name='test-project',
            version='1.0.0',
        )
        builder.generate_conanfile(
            directory=tmp_path,
            data=data,
        )

        base_file = tmp_path / 'conanfile_base.py'
        conan_file = tmp_path / 'conanfile.py'

        assert base_file.exists()
        assert conan_file.exists()

    def test_regenerates_base_file(self, builder: Builder, tmp_path: Path) -> None:
        """Test base file is always regenerated with new dependencies."""
        initial_dependencies = [
            ConanDependency(name='boost', version=ConanVersion.from_string('1.80.0')),
        ]

        initial_data = ConanfileGenerationData(
            dependencies=initial_dependencies,
            dependency_groups={},
            name='test-project',
            version='1.0.0',
        )
        builder.generate_conanfile(
            directory=tmp_path,
            data=initial_data,
        )

        base_file = tmp_path / 'conanfile_base.py'
        initial_content = base_file.read_text(encoding='utf-8')
        assert 'boost/1.80.0' in initial_content

        updated_dependencies = [
            ConanDependency(name='zlib', version=ConanVersion.from_string('1.2.13')),
        ]

        updated_data = ConanfileGenerationData(
            dependencies=updated_dependencies,
            dependency_groups={},
            name='test-project',
            version='1.0.0',
        )
        builder.generate_conanfile(
            directory=tmp_path,
            data=updated_data,
        )

        updated_content = base_file.read_text(encoding='utf-8')
        assert 'zlib/1.2.13' in updated_content
        assert 'boost/1.80.0' not in updated_content

    def test_preserves_user_file(self, builder: Builder, tmp_path: Path) -> None:
        """Test user conanfile is never modified once created."""
        conan_file = tmp_path / 'conanfile.py'
        custom_content = dedent("""
            from conanfile_base import CPPythonBase
            
            class CustomPackage(CPPythonBase):
                name = "custom"
                version = "1.0.0"
                
                def requirements(self):
                    super().requirements()
                    self.requires("custom-lib/1.0.0")
        """)
        conan_file.write_text(custom_content)

        dependencies = [
            ConanDependency(name='boost', version=ConanVersion.from_string('1.80.0')),
        ]

        data = ConanfileGenerationData(
            dependencies=dependencies,
            dependency_groups={},
            name='new-name',
            version='2.0.0',
        )
        builder.generate_conanfile(
            directory=tmp_path,
            data=data,
        )

        final_content = conan_file.read_text()
        assert final_content == custom_content
        assert 'CustomPackage' in final_content
        assert 'custom-lib/1.0.0' in final_content

    def test_inheritance_chain(self, builder: Builder, tmp_path: Path) -> None:
        """Test complete inheritance chain from base to user file."""
        dependencies = [
            ConanDependency(name='boost', version=ConanVersion.from_string('1.80.0')),
            ConanDependency(name='zlib', version=ConanVersion.from_string('1.2.13')),
        ]
        dependency_groups = {
            'test': [
                ConanDependency(name='gtest', version=ConanVersion.from_string('1.14.0')),
            ]
        }

        data = ConanfileGenerationData(
            dependencies=dependencies,
            dependency_groups=dependency_groups,
            name='test-project',
            version='1.0.0',
        )
        builder.generate_conanfile(
            directory=tmp_path,
            data=data,
        )

        base_content = (tmp_path / 'conanfile_base.py').read_text(encoding='utf-8')
        user_content = (tmp_path / 'conanfile.py').read_text(encoding='utf-8')

        assert 'self.requires("boost/1.80.0")' in base_content
        assert 'self.requires("zlib/1.2.13")' in base_content
        assert 'self.test_requires("gtest/1.14.0")' in base_content

        assert 'from conanfile_base import CPPythonBase' in user_content
        assert 'class TestProjectPackage(CPPythonBase):' in user_content
        assert 'super().requirements()' in user_content
        assert 'super().build_requirements()' in user_content


class TestConanfileContent:
    """Tests for conanfile.py template content generation."""

    @pytest.fixture
    def builder(self) -> Builder:
        """Create a Builder instance for testing."""
        return Builder()

    def test_conanfile_content_is_valid_python(self, builder: Builder, tmp_path: Path) -> None:
        """_conanfile_content returns valid Python without version markers."""
        content = Builder._conanfile_content('my-project', '0.1.0')
        assert 'cppython-template-version' not in content
        assert 'from conanfile_base import CPPythonBase' in content
        assert 'class MyProjectPackage(CPPythonBase):' in content


def _data(*names: str) -> ConanfileGenerationData:
    return ConanfileGenerationData(
        dependencies=[ConanDependency(name=name, version=ConanVersion.from_string('1.0.0')) for name in names],
        dependency_groups={},
        name='test-project',
        version='1.0.0',
    )


class TestBaseFilePolicy:
    """Tests for how generate_conanfile treats an existing conanfile_base.py."""

    def test_untouched_base_is_regenerated(self, tmp_path: Path) -> None:
        """A base file nobody edited follows pyproject.toml changes."""
        Builder().generate_conanfile(tmp_path, _data('boost'))
        Builder().generate_conanfile(tmp_path, _data('zlib'))

        content = (tmp_path / 'conanfile_base.py').read_text(encoding='utf-8')
        assert 'zlib/1.0.0' in content
        assert 'boost/1.0.0' not in content

    def test_hand_edited_base_is_preserved(self, tmp_path: Path) -> None:
        """A hand-edited base file is left alone and the error shows the diff."""
        Builder().generate_conanfile(tmp_path, _data('boost'))
        base_file = tmp_path / 'conanfile_base.py'
        edited = base_file.read_text(encoding='utf-8') + '# local tweak\n'
        base_file.write_text(edited, encoding='utf-8')

        with pytest.raises(GeneratedFileError) as error:
            Builder().generate_conanfile(tmp_path, _data('zlib'))

        assert error.value.state is GeneratedFileState.MODIFIED
        assert '-# local tweak' in error.value.diff
        assert base_file.read_text(encoding='utf-8') == edited

    def test_force_overwrites_hand_edits(self, tmp_path: Path) -> None:
        """FORCE replaces a hand-edited base file."""
        Builder().generate_conanfile(tmp_path, _data('boost'))
        base_file = tmp_path / 'conanfile_base.py'
        base_file.write_text(base_file.read_text(encoding='utf-8') + '# local tweak\n', encoding='utf-8')

        Builder().generate_conanfile(tmp_path, _data('zlib'), GeneratedFilePolicy.FORCE)

        content = base_file.read_text(encoding='utf-8')
        assert '# local tweak' not in content
        assert 'zlib/1.0.0' in content

    def test_locked_never_writes(self, tmp_path: Path) -> None:
        """LOCKED fails on a stale base file and leaves both files untouched."""
        Builder().generate_conanfile(tmp_path, _data('boost'))
        base_file = tmp_path / 'conanfile_base.py'
        before = base_file.read_text(encoding='utf-8')

        with pytest.raises(GeneratedFileError) as error:
            Builder().generate_conanfile(tmp_path, _data('zlib'), GeneratedFilePolicy.LOCKED)

        assert error.value.state is GeneratedFileState.STALE
        assert base_file.read_text(encoding='utf-8') == before

    def test_locked_passes_when_current(self, tmp_path: Path) -> None:
        """LOCKED succeeds when the base file already matches."""
        Builder().generate_conanfile(tmp_path, _data('boost'))

        Builder().generate_conanfile(tmp_path, _data('boost'), GeneratedFilePolicy.LOCKED)

    def test_locked_does_not_create_files(self, tmp_path: Path) -> None:
        """LOCKED reports a missing base file instead of creating anything."""
        # Autouse fixtures may populate tmp_path, so observe a dedicated project directory.
        project = tmp_path / 'project'
        project.mkdir()
        with pytest.raises(GeneratedFileError) as error:
            Builder().generate_conanfile(project, _data('boost'), GeneratedFilePolicy.LOCKED)

        assert error.value.state is GeneratedFileState.MISSING
        assert not any(project.iterdir())

    def test_legacy_base_is_migrated(self, tmp_path: Path) -> None:
        """A base file from a generator without headers counts as untouched."""
        base_file = tmp_path / 'conanfile_base.py'
        base_file.write_text(
            '"""CPPython managed base ConanFile.\n\n'
            'This file is auto-generated by CPPython. Do not edit manually.\n"""\n',
            encoding='utf-8',
        )

        Builder().generate_conanfile(tmp_path, _data('boost'))

        assert base_file.read_text(encoding='utf-8').startswith('# cppython-generated:')
