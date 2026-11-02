# Conanfile Integration

CPPython uses a dual-file approach.

## `conanfile_base.py` - Auto-generated

**Auto-generated** from your `pyproject.toml` dependencies. Commit it, but **don't edit it by hand**; put custom recipe logic in `conanfile.py`.

Contains a `CPPythonBase` class with dependencies from `[tool.cppython.dependencies]` and test dependencies from `[tool.cppython.dependency-groups.test]`. Every group is declared regardless of which groups an install selects, so the file is the same on every machine. Test requirements are only installed when you request them, as in `cppython install [test]`.

The first line records a hash of the generated content. CPPython uses it to tell whether you edited the file:

| File state | `install` / `update` | `--locked` or `cppython check` |
| --- | --- | --- |
| Missing | Created | Fails |
| Matches `pyproject.toml` | Left alone | Passes |
| Untouched, but `pyproject.toml` changed | Regenerated, with a warning to commit the change | Fails with a diff |
| Edited by hand | Fails with a diff; `--force-regenerate` overwrites it | Fails with a diff |

Files written by CPPython versions without the header count as untouched and are regenerated once.

In CI, run `cppython install --locked` (or `cppython check`) so a stale committed file fails the build instead of being silently rewritten.

## `conanfile.py` - User-customizable

**Created once** if it doesn't exist, then **never modified** by CPPython. Inherits from `CPPythonBase`.

You can customize this file for package metadata like name, version, and settings, additional dependencies beyond `pyproject.toml`, build configuration and CMake integration, and package and export logic.

**Key requirement** - Always call `super().requirements()` and `super().build_requirements()` to inherit managed dependencies.

## Workflow

- First run - Both files are generated
- Subsequent runs - `conanfile_base.py` is regenerated if `pyproject.toml` changed; `conanfile.py` is preserved
- Adding dependencies - Update `pyproject.toml`, run `cppython install`, and commit both changes

## Migrating Existing Projects

If you have an existing `conanfile.py`, back it up and run `cppython install` to generate both files. Then update your conanfile to inherit from `CPPythonBase`.

```python
from conan.tools.cmake import CMake, CMakeConfigDeps, CMakeToolchain, cmake_layout
from conanfile_base import CPPythonBase  # Import the base class


class YourPackage(CPPythonBase):  # Inherit from CPPythonBase instead of ConanFile
    name = "your-package"
    version = "1.0.0"
    settings = "os", "compiler", "build_type", "arch"
    exports = "conanfile_base.py"  # Export the base file

    def requirements(self):
        super().requirements()  # Get CPPython managed dependencies
        # Add your custom requirements here
        # self.requires("custom-lib/1.0.0")

    def build_requirements(self):
        super().build_requirements()  # Get CPPython managed test dependencies
        # Add your custom build requirements here

    # ... rest of your configuration
```
