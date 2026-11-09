# Configuration Reference

Complete configuration options for the `cppython.build` backend.

## Build System

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"
```

### Extras

| Extra | Description |
|-------|-------------|
| `conan` | Conan package manager support |
| `cmake` | CMake generator support |
| `meson` | Meson generator support |
| `git` | Git SCM for dynamic versioning |

## CPPython Options

These options are configured under `[tool.cppython]`.

### install-path

Path where provider tools and cached dependencies are stored.

```toml
[tool.cppython]
install-path = "~/.cppython"  # Default
```

- **Type**: Path
- **Default**: `~/.cppython`
- Relative paths are resolved from the project root
- Use absolute path for build isolation compatibility

### tool-path

Local directory for CPPython's project tooling files.

```toml
[tool.cppython]
tool-path = "tool"  # Default
```

- **Type**: Path
- **Default**: `tool`

### build-path

Directory for build artifacts.

```toml
[tool.cppython]
build-path = "build"  # Default
```

- **Type**: Path
- **Default**: `build`

For an identity-based CMake project with a custom build path, set the absolute
`SYNODIC_DEPENDENCIES_ROOT` CMake cache variable to the same directory.

### identities-file and identity

CPPython reads the neutral identity table at `tool/identities.toml` by default.
Set `identities-file` to use another project-relative or absolute path. Choose
an identity with `cppython --identity <name>`, `CPPYTHON_IDENTITY`, or
`identity` in project configuration, in that order. Otherwise, CPPython selects
the unambiguous default for the current host. Explicit unknown identities,
missing explicitly configured tables, and ambiguous host defaults fail.
Projects without the default table retain their existing non-identity behavior.

```toml
[tool.cppython]
identities-file = "tool/identities.toml"
# identity = "windows-x64-msvc"
```

The selected identity must match the identity in the CMake preset. For a
non-default preset, select its identity explicitly before preparing it.
The identity-based handoff currently supports CMake with Conan. Meson and
vcpkg use their legacy non-identity integrations until their identity handoffs
are validated; they are not silently treated as CMake search-path providers.
To use those legacy integrations with a project that contains an identity
table, set `identities-enabled = false` under `[tool.cppython]`. An explicit
identity selection then fails rather than being ignored.

### dependencies

List of C++ dependencies in PEP 508-style format.

```toml
[tool.cppython]
dependencies = [
    "fmt>=11.0.0",
    "boost>=1.84.0",
]
```

- **Type**: List of strings
- Syntax follows provider conventions (Conan uses `name>=version`)

### dependency-groups

Named groups of dependencies for optional features.

```toml
[tool.cppython.dependency-groups]
test = ["gtest>=1.14.0"]
dev = ["benchmark>=1.8.0"]
```

- **Type**: Dictionary of string lists

## Provider Configuration

### Conan

```toml
[tool.cppython.providers.conan]
# Conan-specific options
```

See [Conan Plugin Configuration](../plugins/conan/configuration.md) for details.

## Generator Configuration

### CMake

```toml
[tool.cppython.generators.cmake]
cmake_binary = "C:/Tools/CMake/bin/cmake.exe"  # Optional explicit executable
minimum-version = "4.5.0rc1"        # Optional project requirement
```

The project owns `CMakePresets.json` and any included preset files. CPPython
reads them but does not create or rewrite them. The old `preset_file` and
`configuration_name` options remain accepted for compatibility; they no
longer control preset generation.

CPPython validates the selected executable by querying its version and recording a SHA-256 fingerprint. Selection order is `CMAKE_BINARY`, then `cmake_binary`, then the CMake installed by CPPython's `cmake` extra (which Porringer/PDM installs). `PATH` is never consulted. If both explicit selections are set, they must resolve to the same executable. Invalid, conflicting, changed, or under-versioned selections fail instead of silently falling back. If no CMake is selected, CMake and CTest commands fail and ask you to sync the `cmake` extra or set `CMAKE_BINARY`. CTest is resolved beside the selected CMake binary so that the tools come from the same distribution.

`minimum-version` is the runtime check. Keep it aligned with the project's `cmake_minimum_required` and its `cmake` dependency constraint.

Use `CMAKE_BINARY` for a machine-local, per-process override rather than checking a machine-specific path into `pyproject.toml`:

```powershell
$env:CMAKE_BINARY = 'C:\Program Files\CMake\bin\cmake.exe'
cppython configure
Remove-Item Env:CMAKE_BINARY
```

Until a CMake 4.5 wheel is published, use this override to bridge to a locally installed 4.5 release candidate. Keep it local: it does not update `PATH` or change the project's tracked configuration.

For a persistent machine-local selection, put the executable in the ignored
`.cppython.toml` at the project root:

```toml
[generators.cmake]
cmake_binary = "C:/Tools/CMake/bin/cmake.exe"
```

This setting affects CPPython operations. Select the same executable separately
for direct CMake commands and IDE integrations.

## scikit-build-core Passthrough

All `[tool.scikit-build]` options are passed directly to scikit-build-core:

```toml
[tool.scikit-build]
cmake.build-type = "Release"
cmake.args = ["-DSOME_OPTION=ON"]
wheel.packages = ["src/my_package"]
logging.level = "WARNING"
```

For identity-based CMake + Conan builds, CPPython adds the provider's
search-path file to `CMAKE_PROJECT_TOP_LEVEL_INCLUDES` while preserving the
effective user includes and toolchain. Legacy non-identity providers may still
supply a toolchain file. The selected CMake is also used by scikit-build-core;
an incompatible `CMAKE_EXECUTABLE` override fails.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `CPPYTHON_IDENTITY` | Selects a build identity when no CLI identity is given |
| `CMAKE_BINARY` | Selects the CMake executable used by CPPython and the CMake build backend |
| `CMAKE_EXECUTABLE` | scikit-build-core override; must match CPPython's selected CMake |
| `CONAN_HOME` | Conan cache location |

## Example: Full Configuration

```toml
[build-system]
requires = ["cppython[conan, cmake, git]"]
build-backend = "cppython.build"

[project]
name = "my_extension"
version = "1.0.0"
requires-python = ">=3.10"

[tool.scikit-build]
cmake.build-type = "Release"
cmake.args = ["-DBUILD_TESTING=OFF"]
wheel.packages = ["src/my_extension"]
wheel.install-dir = "my_extension"

[tool.cppython]
install-path = "~/.cppython"
tool-path = "tool"
build-path = "build"

dependencies = [
    "fmt>=11.0.0",
    "spdlog>=1.14.0",
]

[tool.cppython.dependency-groups]
test = ["gtest>=1.14.0", "benchmark>=1.8.0"]

[tool.cppython.generators.cmake]

[tool.cppython.providers.conan]
```
