# CPPython

A transparent Python management solution for C++ dependencies and building.

CPPython lets you declare C++ dependencies in `pyproject.toml` and consume them from your build system. It works two ways:

- **As a PEP 517 build backend**: declare `cppython.build` in `[build-system]`; CPPython installs missing native dependencies before delegating to scikit-build-core or meson-python. See [Build Backend](build-backend/index.md).
- **As a CLI**: run `cppython install` to install dependencies, then `cppython configure` to configure a standalone build tree. Use `install --configure` to combine those steps. Build and test through the CLI without a Python packaging step.

To develop several packages together, choose per dependency between its published package and a local checkout with `cppython local`. See [Local Sources](local-sources.md).

### Preparation metadata for front ends

Front ends that run a project's existing `cppython install` preparation step
can set `CPPYTHON_PREPARATION_REPORT` to an absolute, front-end-owned JSON
report filepath for that subprocess. After a successful dependency installation,
CPPython exclusively creates that file containing a versioned
`PreparationMetadata` object. Normal stdout/stderr logs and install/configure
operations are unchanged; there is no log sentinel or metadata sniffing.

The version `"1"` report includes `identity` (the resolved identity name, or
null), `backend` (the selected native build-system/generator, such as `cmake`,
not an inferred Python packaging backend), `provider` (the selected provider
name, such as `conan`), and `dependency_outputs` (provider-owned installation
output directories, or null if unsupported).
Conan reuses the exact output-folder resolution used by installation.
Provider configuration, environment variables, credentials, and captured
subprocess output are not included. Other providers can implement the optional
`PreparationOutputs` capability without changing the provider contract.

The report describes dependency preparation only. It does not certify native
configuration, build, or test success. Failed installation does not emit a
success report, and requesting metadata does not run extra install, configure,
build, or test operations.

Relative report paths, duplicate writers, and report-write errors fail explicitly
rather than overwriting a report or claiming a successful handoff. The front end
owns report cleanup and should consume the report only after the subprocess
succeeds. Porringer supplies a unique path in the project directory and removes
the report on completion or interruption. Older CPPython versions ignore this
optional request, allowing the front end to report metadata as unavailable.

## Plugin architecture

CPPython resolves dependencies and generates build configuration through three kinds of plugins:

| Kind | Purpose | Built-in plugins |
| --- | --- | --- |
| Provider | Installs and publishes C++ dependencies | [Conan](plugins/conan/index.md), vcpkg |
| Generator | Reads build configuration and synchronizes provider-generated files | CMake, Meson |
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
pip wheel .
```

See [Build Backend](build-backend/index.md) for the full workflow, or [Configuration](build-backend/configuration.md) for every available option.
