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
layout.
