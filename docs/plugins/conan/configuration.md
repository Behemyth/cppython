# Configuration

```toml
[tool.cppython.providers.conan]
remotes = ["remote-name"]  # Optional: remotes for publishing
skip_upload = false        # Optional: skip upload during publish
settings = { "compiler.cppstd" = "23" }
conf = { "tools.build:jobs" = 4 }
```

`settings` and `conf` are passed to both Conan host and build contexts. For an
identity-based build, CPPython derives the compiler, architecture, standard
library, and compiler executable from the selected identity and the actual
compiler. Explicit values that conflict with those facts fail. On MSVC,
runtime type follows each dependency build type and must not be overridden.

Conan installs the dependency build types required by the identity into
`build/<identity>/conan`. Debug and Release share that folder, while
Development uses Release dependencies. A Debug-only identity does not install
Release dependencies. `build-types` must include every type the identity
requires. Projects without an identity table keep the legacy per-build-type
layout. The project-owned presets in `examples/conan_cmake/simple` and
`examples/conan_cmake/library` remain provider-neutral. When using their
legacy, non-identity configuration, pass the desired generated Conan
toolchain to CMake explicitly after installing dependencies. For example:

```bash
pdm run cppython install
cmake --preset default-release \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/conan/Release/generators/conan_toolchain.cmake"
cmake --build --preset default-release
```

Identity-based projects can use the provider-neutral dependency shim instead,
without occupying `CMAKE_TOOLCHAIN_FILE`.

Conan installs a separate package for each build type. A dependency that ships
its own CMake package files, as a C++ modules library does, describes only its
own build type, so a multi-config project would import just one. For an
identity-based build, CPPython writes a small CMake package for each such
dependency under `build/<identity>/conan/packages`. It loads every installed
build type, so each configuration links its matching binary. This covers
package files that CMake's `install(EXPORT)` generated and needs CMake 3.25 or
newer. Hand-written package files keep Conan's single selection.
