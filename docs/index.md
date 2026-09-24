# CPPython

A transparent Python management solution for C++ dependencies and building.

CPPython lets you declare C++ dependencies in `pyproject.toml`, install and configure them with `cppython install`, and consume them from your build system. It works two ways:

- **As a PEP 517 build backend**: declare `cppython.build` in `[build-system]`; CPPython verifies that native dependencies were installed explicitly before delegating to scikit-build-core or meson-python. See [Build Backend](build-backend/index.md).
- **As a CLI**: run `cppython install`, `cppython build`, and related commands directly against a CMake or Meson project, without a Python packaging step.

## Plugin architecture

CPPython resolves dependencies and generates build configuration through three kinds of plugins:

| Kind | Purpose | Built-in plugins |
| --- | --- | --- |
| Provider | Installs and publishes C++ dependencies | [Conan](plugins/conan/index.md), vcpkg |
| Generator | Produces build-tool configuration (presets, native files) | CMake, Meson |
| SCM | Detects project version from source control | Git |

## Quick start

```toml
[build-system]
requires = ["cppython[conan, cmake]"]
build-backend = "cppython.build"

[tool.cppython]
dependencies = ["fmt>=11.0.0"]

[tool.cppython.providers.conan]
[tool.cppython.generators.cmake]
```

```bash
pip install "cppython[conan,cmake]"
cppython install
pip wheel .
```

See [Build Backend](build-backend/index.md) for the full workflow, or [Configuration](build-backend/configuration.md) for every available option.
