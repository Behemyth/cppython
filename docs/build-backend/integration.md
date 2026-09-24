# Integration Guide

How to integrate the `cppython.build` backend into your Python extension project.

## Migrating from scikit-build-core

If you have an existing scikit-build-core project, migration is straightforward.

### Before (scikit-build-core only)

```toml
[build-system]
requires = ["scikit-build-core"]
build-backend = "scikit_build_core.build"

[tool.scikit-build]
cmake.build-type = "Release"
```

With manual C++ dependency management (system packages, git submodules, etc.).

### After (CPPython + scikit-build-core)

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"

[tool.scikit-build]
cmake.build-type = "Release"

[tool.cppython]
dependencies = ["fmt>=11.0.0", "nanobind>=2.4.0"]

[tool.cppython.generators.cmake]

[tool.cppython.providers.conan]
```

### CMakeLists.txt Changes

Remove manual dependency fetching:

```cmake
# Before: FetchContent, git submodules, find_package with hints
FetchContent_Declare(fmt GIT_REPOSITORY ...)
FetchContent_MakeAvailable(fmt)

# After: Just find_package (Conan toolchain provides paths)
find_package(fmt REQUIRED)
```

## Using with PDM

CPPython integrates with PDM for development workflow.

### Development Setup

```toml
[tool.pdm]
distribution = true

[dependency-groups]
native = ["cppython[conan, cmake]"]

[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"
```

### Commands

```bash
# Bootstrap the CPPython CLI and its native plugins without building this project
pdm install --no-self -G native

# Install native dependencies and configure the build tree
pdm run cppython install [test]

# Install the Python project and build its wheel
pdm install
pdm build
```

## Build Isolation

### Default Behavior

`pip wheel .` and `pdm build` use isolated build environments. CPPython handles this by:

1. Verifying C++ dependencies were installed beforehand with `cppython install`
   (or installing them when building from an sdist, see below)
2. Reading provider artifacts from `install-path` (outside isolation)
3. Passing absolute toolchain paths to scikit-build-core

### Building from an sdist

A wheel built from a source distribution runs in a freshly extracted directory, where no manual
`cppython install` is possible. When the source root contains `PKG-INFO` (the marker of an
extracted sdist), missing native dependencies are installed through the provider during the build.
The build tree is still never configured. Builds from a source checkout always require an explicit
`cppython install`.

### Caching Dependencies

For faster builds, use a persistent `install-path`:

```toml
[tool.cppython]
install-path = "~/.cppython"  # Shared across projects
```

### Disabling Isolation (Development)

For faster iteration during development:

```bash
pip wheel . --no-build-isolation
```

This uses your current environment's CPPython installation.

## CI/CD Integration

### GitHub Actions

```yaml
name: Build

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.14'

      - name: Install build tools
        run: pip install build "cppython[conan,cmake]"

      - name: Install native dependencies
        run: cppython install

      - name: Build sdist and wheel
        run: python -m build

      - name: Upload wheel
        uses: actions/upload-artifact@v4
        with:
          name: wheel
          path: dist/*.whl
```

`python -m build` builds the wheel from the generated sdist, which installs native dependencies
inside the extracted tree. The explicit `cppython install` step keeps the checkout usable for
`python -m build --wheel` and populates the shared `install-path` cache.

### Caching Conan Packages

```yaml
      - name: Cache Conan packages
        uses: actions/cache@v4
        with:
          path: ~/.conan2
          key: conan-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
```

## Multi-Platform Builds

### cibuildwheel

CPPython works with cibuildwheel for building wheels across platforms. cibuildwheel builds from the
project checkout, so install native dependencies before each build:

```toml
# pyproject.toml
[tool.cibuildwheel]
build-verbosity = 1
before-build = "pip install \"cppython[conan,cmake]\" && cppython install"
```

## Editable Installs

scikit-build-core's editable mode works with CPPython:

```bash
pip install -e . --no-build-isolation
```

For automatic rebuilds on import:

```toml
[tool.scikit-build]
editable.rebuild = true
editable.verbose = true
```

## Combining with Non-Python Projects

If your repository contains both a Python extension and a standalone C++ project:

```
my_project/
├── CMakeLists.txt          # Standalone C++ build
├── CMakePresets.json       # CPPython manages presets
├── pyproject.toml          # Python extension config
├── src/
│   ├── lib/                # C++ library
│   └── python/             # Python bindings
└── tool/                   # CPPython generated files
```

### Dual Workflow

**Python extension** (uses `cppython.build`):

```bash
pdm run cppython install
pip wheel .
```

**Standalone C++ build** (uses CMakePresets):

```bash
cmake --preset=default
cmake --build build
```

Both workflows share the same Conan-managed dependencies through CPPython's CMake preset integration.
