# Build Backend

CPPython provides a PEP 517 build backend that wraps [scikit-build-core](https://scikit-build-core.readthedocs.io/), enabling seamless building of Python extension modules with C++ dependencies managed by CPPython.

## Overview

The `cppython.build` backend:

1. Verifies native dependencies, installs any missing dependencies, and verifies again
2. Gets the provider's generated dependency configuration
3. Passes dependency search paths or a legacy toolchain to scikit-build-core
4. Delegates the actual wheel building to scikit-build-core

The same preparation runs for source checkouts and extracted sdists. Build hooks do not configure the standalone build tree; scikit-build-core or meson-python configures its own package build. A prior `cppython install` is optional and does not configure the standalone tree by default.

On a fresh build, automatic preparation installs the base dependencies, not optional
dependency groups. If the CMake project enables tests by default, either prepare its
test group first or disable tests through that project's build settings. For Conan
identities, an automatic repair preserves the groups from the last successful install
and rechecks the local-source selection. If a previously selected group no longer
exists, select the desired groups explicitly with `cppython install`.

## Quick Start

Set `cppython.build` as your build backend in `pyproject.toml`:

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"

[project]
name = "my_extension"
version = "1.0.0"

[tool.scikit-build]
cmake.build-type = "Release"

[tool.cppython]
dependencies = ["fmt>=11.0.0", "nanobind>=2.4.0"]

[tool.cppython.generators.cmake]

[tool.cppython.providers.conan]
```

Then build with:

```bash
pip wheel .
```

## Configuration

### Build System Requirements

The `build-system.requires` should include CPPython with the appropriate extras:

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"
```

Available extras:

- `conan` - Conan package manager support
- `cmake` - CMake build system
- `meson` - Meson build system
- `git` - Git SCM support for version detection

### CPPython Configuration

Configure C++ dependencies under `[tool.cppython]`:

```toml
[tool.cppython]
install-path = "install"  # Where provider tools are cached

dependencies = [
    "fmt>=11.0.0",
]

[tool.cppython.generators.cmake]
# CMake generator options

[tool.cppython.providers.conan]
# Conan provider options
```

### scikit-build-core Configuration

All standard `[tool.scikit-build]` options are supported and passed through:

```toml
[tool.scikit-build]
cmake.build-type = "Release"
wheel.packages = ["src/my_package"]
```

For identity-based CMake + Conan builds, CPPython composes the provider's
search-path include with your existing top-level includes and leaves your
toolchain in place. Non-identity providers may supply a legacy toolchain.

## How It Works

### Build Workflow

```
pip wheel . / pdm build
        │
        ▼
┌─────────────────────────────────────┐
│         cppython.build              │
├─────────────────────────────────────┤
│ 1. Load pyproject.toml              │
│ 2. Initialize CPPython Project      │
│ 3. Verify artifacts; install       │
│    missing dependencies; reverify  │
│ 4. Read provider dependency data    │
│ 5. Compose CMake search paths       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│      scikit_build_core.build        │
├─────────────────────────────────────┤
│ 1. Configure CMake with deps        │
│ 2. Build extension module           │
│ 3. Package into wheel               │
└─────────────────────────────────────┘
```

### Dependency search paths

For an identity-based Conan build, the provider generates CMake package search
paths under `build/<identity>/conan`. CPPython adds the paths file to
`CMAKE_PROJECT_TOP_LEVEL_INCLUDES` for the package build, after any
user-supplied includes. A user-supplied `CMAKE_TOOLCHAIN_FILE` remains in
effect. Non-identity providers can continue to supply a legacy toolchain.

## Example Project

A complete example is available at `examples/conan_cmake/extension/`.

### Project Structure

```
my_extension/
├── CMakeLists.txt
├── pyproject.toml
└── src/
    └── my_extension/
        ├── __init__.py
        └── _core.cpp
```

### CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.15...3.30)
project(my_extension LANGUAGES CXX)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
find_package(nanobind REQUIRED)
find_package(fmt REQUIRED)

nanobind_add_module(_core src/my_extension/_core.cpp)
target_link_libraries(_core PRIVATE fmt::fmt)
target_compile_features(_core PRIVATE cxx_std_17)

install(TARGETS _core DESTINATION my_extension)
```

### pyproject.toml

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"

[project]
name = "my_extension"
version = "1.0.0"

[tool.scikit-build]
cmake.build-type = "Release"
wheel.packages = ["src/my_extension"]

[tool.cppython]
dependencies = ["fmt>=11.0.0", "nanobind>=2.4.0"]

[tool.cppython.generators.cmake]

[tool.cppython.providers.conan]
```

### C++ Source

```cpp
#include <nanobind/nanobind.h>
#include <fmt/core.h>

namespace nb = nanobind;

std::string greet(const std::string& name) {
    return fmt::format("Hello, {}!", name);
}

NB_MODULE(_core, m) {
    m.def("greet", &greet, nb::arg("name"));
}
```

## Comparison with Alternatives

| Approach | C++ Deps | Python Bindings | Build Backend |
|----------|----------|-----------------|---------------|
| **CPPython + scikit-build-core** | Conan/vcpkg | Any (nanobind, pybind11) | `cppython.build` |
| scikit-build-core alone | Manual/system | Any | `scikit_build_core.build` |
| meson-python | Manual/system | Any | `mesonpy` |

CPPython's advantage is integrated C++ dependency management: declare dependencies in `pyproject.toml` and package builds install them when needed. The CLI can also install dependencies and configure a standalone build tree.

## Troubleshooting

### Dependencies not found by CMake

Ensure your `CMakeLists.txt` uses `find_package()` for each dependency:

```cmake
find_package(fmt REQUIRED)
target_link_libraries(my_target PRIVATE fmt::fmt)
```

### Build isolation issues

If dependencies aren't being found in isolated builds, ensure `install-path` in `[tool.cppython]` uses an absolute path for caching:

```toml
[tool.cppython]
install-path = "~/.cppython"  # Persists across builds
```

### Viewing build logs

Set scikit-build-core to verbose mode:

```toml
[tool.scikit-build]
logging.level = "DEBUG"
```
