# Local Sources

Local sources let you develop several CPPython packages together. Each dependency is either **stable** (its published package) or **local** (a checkout on disk). You choose this per package, without editing any repository.

The Conan provider applies the selection. See [How builds use the selection](#how-builds-use-the-selection).

## Intent and state

- **Intent** is a declarative document that describes the selection you want. It can come from a setup profile, an agent, or you.
- **State** is one user-level file that records the current selection. Only `cppython local` commands and the `cppython.local_sources` API write it.

The state file is outside every repository. Its location:

| `CPPYTHON_LOCAL_SOURCES` | State file |
| --- | --- |
| unset | `~/.cppython/local-sources.toml` |
| a path | that path |
| `off`, `0`, `false`, or `no` | none: every override is suspended for this process |

Writes are atomic, and a cross-process lock serializes them. Two concurrent commands can't lose each other's updates.

## Selection model

The selection is a **policy** plus **exceptions**:

- `default` is the policy for every managed package: `stable` or `local`.
- `local` and `stable` list packages that are exceptions to the policy.
- `packages` lists the managed packages. A package can have a `url` to clone from and a `path` when its checkout isn't `<root>/<name>`.
- `root` is the folder that holds checkouts named after their packages.
- `enabled = false` suspends every override without losing the selection.

A package is managed only when you declare it. A checkout that happens to sit in the root folder is never selected on its own. To register existing checkouts, opt in with `--discover`.

A local package must have a checkout, and the checkout's `[project] name` must match the package. A missing checkout or a mismatched identity is an error. CPPython never silently falls back to the published package.

## Commands

| Command | Effect |
| --- | --- |
| `cppython local list` | Show each managed package, its effective mode, why, and its checkout commit. |
| `cppython local use all\|none [--root DIR] [--discover]` | Set the policy to local (`all`) or stable (`none`), and clear the exceptions. |
| `cppython local root DIR [--discover]` | Set the checkout folder. |
| `cppython local add NAME [--path DIR] [--url URL] [--clone]` | Develop one package locally. `--clone` clones a missing checkout from `--url`. |
| `cppython local remove NAME [--forget]` | Return one package to stable. `--forget` also stops managing it. |
| `cppython local off` / `on` | Suspend or resume every override. |
| `cppython local apply FILE\|- \| --inline JSON [--root DIR] [--replace] [--clone]` | Apply an intent document. `--clone` clones missing local checkouts. |
| `cppython local export` | Print the current selection as an intent document. |
| `cppython local schema [intent\|result\|state]` | Print a JSON Schema. |

`--discover` registers each CPPython project in the root whose folder name matches its project name, and records its `origin` remote as the clone URL.

Every command that changes the state:

- is idempotent: running it twice gives the same state, and the second run reports no change;
- accepts `--dry-run` to report the planned changes without writing them;
- accepts `--json` to print a machine-readable result on stdout;
- never prompts.

These commands work outside a project. They don't need a `pyproject.toml`.

## How builds use the selection

### Which packages apply

A local package applies to a project when the project depends on it, directly or through another local checkout. CPPython follows each local checkout's base dependencies (not its dependency groups) and orders the packages so that dependencies come first. A cycle between the project and its checkouts is an error.

A local package that is reachable only through a *stable* package isn't found. Make the packages in between local too, or use `cppython local use all`. A stable package can also reuse a binary that was built against the published version of a local dependency, so prefer `use all` when you change an API.

### Install

`cppython install` prepares each local package before it installs the project:

1. It regenerates the checkout's `conanfile_base.py`, like an install in that checkout would.
2. It runs `conan install` for the checkout, with the project's settings and without the checkout's tests.
3. It configures the checkout's CMake build tree in `build/local/<build type>/<package>` in the **consuming** project. Each project and build type gets its own tree, and the checkout itself stays clean.

The project then resolves the package from that build tree instead of the Conan cache.

### Build, test, bench, and run

`build`, `test`, `bench`, and `run` keep the selection current without a separate install:

- The last install writes a stamp to `build/local/selection.json`. It records the local packages and a hash of each checkout's `pyproject.toml`, `conanfile.py`, and `conanfile_base.py`.
- When the selection or one of those files changed, the command reinstalls first, with the dependency groups of the last install.
- It then builds each local package's tree for the configuration's dependency build type, before it builds the project. A source change in a checkout rebuilds that package and relinks the project in the same command.
- It prints one line per local package with its path, commit, and whether the checkout is modified. When you remove every local package, it reinstalls the published packages and says so.

### Requirements for a local package

A local package must export a CMake package from its build tree. Add this after the package's `install()` rules, so the build tree matches the installed `lib/cmake/<name>` layout:

```cmake
set(mylib_BUILD_PACKAGE_DIR ${CMAKE_CURRENT_BINARY_DIR}/${CMAKE_INSTALL_LIBDIR}/cmake/mylib)

export(EXPORT mylibTargets
	FILE ${mylib_BUILD_PACKAGE_DIR}/mylibTargets.cmake
	NAMESPACE synodic::
	CXX_MODULES_DIRECTORY cxx-modules
)

configure_file(${CMAKE_CURRENT_BINARY_DIR}/mylibConfig.cmake ${mylib_BUILD_PACKAGE_DIR}/mylibConfig.cmake COPYONLY)
configure_file(${CMAKE_CURRENT_BINARY_DIR}/mylibConfigVersion.cmake ${mylib_BUILD_PACKAGE_DIR}/mylibConfigVersion.cmake COPYONLY)
```

Install fails with this fix when the configured tree has no `<name>Config.cmake`. The package should also honor `BUILD_TESTING`, which CPPython sets to `OFF`.

### Conan

- CPPython gives Conan its selection in memory, for its own process only. It doesn't run `conan editable add`, and it doesn't write a Conan workspace.
- Conan editables that you registered yourself (`conan editable add`) and editables from a `conanws` file are **ignored** by CPPython commands, even when nothing is selected.
- This relies on Conan internals, so CPPython requires `conan>=2.30,<2.34`. An unsupported Conan fails with a clear error instead of ignoring the selection.

### Publishing

`cppython publish` refuses to run while a local package applies, because the result couldn't be reproduced. Publish with `CPPYTHON_LOCAL_SOURCES=off` or after `cppython local off`.

## Intent documents

`apply` reads JSON or TOML. Every field except `schema_version` is optional:

```toml
schema_version = 1
root = ".."
default = "local"
local = []
stable = ["cppaste"]

[packages.honesty]
url = "https://github.com/Behemyth/honesty.git"

[packages.cppaste]
url = "https://github.com/Behemyth/cppaste.git"
```

- Give the document as a file, as `-` for stdin, or as a string with `--inline`. Give exactly one.
- A relative `root` resolves against the document's folder. When the document comes from stdin or `--inline`, it resolves against the current directory. `--root` overrides it.
- With `--clone`, `apply` clones each local package whose checkout is missing, from the package's `url`. If a missing checkout has no `url`, `apply` fails before it changes anything. `--dry-run` lists the clones in `planned_clones` without cloning.
- By default, `apply` **merges**. Fields in the document replace the same fields in the state. Exceptions that the document doesn't mention are kept, and each one is reported as a `preserved-exception` diagnostic.
- `--replace` makes the selection exactly the document.
- A package can't be in both `local` and `stable`. Exceptions must name managed packages.

`cppython local export` prints a document that `apply --replace` reproduces exactly.

## Automation

Agents and tools should use `--json` and the exit code.

1. Read the contract with `cppython local schema intent` and `cppython local schema result`.
2. Preview with `cppython local apply - --dry-run --json`.
3. Apply with `cppython local apply - --json`.

To pass a document without a file or a pipe, use `--inline`. Add `--clone` to fetch missing checkouts in the same step:

```shell
cppython local apply --inline '{"schema_version":1,"default":"local"}' --clone --json
```

[Porringer](https://github.com/Synodic-Software/porringer) uses this form for the `native` section of a setup manifest.

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Other failure, for example a failed clone |
| 2 | Invalid input |
| 3 | Missing checkout |
| 4 | Checkout identity mismatch |
| 5 | Another process held the lock for too long |

Each diagnostic in the result has a stable `code`, a `severity`, a `message`, and, where possible, a `fix` command. The state is written only when the command makes changes and reports no errors.

Python callers can use the same operations directly:

```python
from cppython.local_sources import apply, show

result = apply(document_text, source='profile:develop', root='~/projects')
if not result.ok:
    ...
```
