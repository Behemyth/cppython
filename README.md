# CPPython

A transparent Python management solution for C++ dependencies and building.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![PyPI version](https://img.shields.io/pypi/v/cppython.svg)](https://pypi.org/project/cppython/)

## Goals

1. **CLI**: Provide imperative commands (`build`, `test`, `bench`, `run`, `install`) for managing C++ projects within a Python ecosystem.
2. **Plugin Architecture**: Support pluggable generators (CMake, Meson) and providers (Conan, vcpkg) so users can mix and match toolchains.
3. **PEP 517 Build Backend**: Act as a transparent build backend that delegates to scikit-build-core or meson-python after ensuring C++ dependencies are in place.
4. **Build System Consumer**: Be declared directly in `[build-system].requires`, like scikit-build-core, so package builds can consume native dependencies installed explicitly with `cppython install`. Build hooks do not install or configure native dependencies.

## Features

- Resolve and install C++ dependencies through pluggable providers (Conan, vcpkg).
- Generate and sync build-tool configuration (CMake presets, Meson native files) from a single `[tool.cppython]` table.
- Detect project versions from source control through pluggable SCM plugins (Git).
- Inspect plugin configuration and discovered build targets with `cppython info` and `cppython list`.

## Setup

See [Setup](https://synodic.github.io/cppython/setup) for setup instructions.

## Development

We use [pdm](https://pdm-project.org/en/latest/) as our build system and package manager. Scripts for development tasks are defined in `pyproject.toml` under the `[tool.pdm.scripts]` section.

See [Development](https://synodic.github.io/cppython/development) for additional build, test, and installation instructions.

For contribution guidelines, see [CONTRIBUTING.md](https://github.com/synodic/.github/blob/stable/CONTRIBUTING.md).

## Documentation

Full documentation, including the build backend reference and plugin guides, is available at [synodic.github.io/cppython](https://synodic.github.io/cppython).

## License

This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.

Copyright © 2026 Synodic Software
