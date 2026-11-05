"""Versioned models for local-source intent, state, and command results.

Intent is a declarative document that a profile, agent, or user hands to CPPython. State is the
user-level selection CPPython derives from intent and commands. Results describe what a command did
(or would do with ``--dry-run``) in a stable, machine-readable shape.
"""

import re
from enum import IntEnum, StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

_PACKAGE_NAME = re.compile(r'^[a-z0-9][a-z0-9._-]*$')


def validate_package_name(name: str) -> str:
    """Validate and return a managed package name.

    Raises:
        ValueError: If the name isn't a lowercase package name
    """
    if not _PACKAGE_NAME.fullmatch(name):
        raise ValueError(
            f"Invalid package name '{name}'. "
            "Use lowercase letters, digits, '.', '_' and '-', starting with a letter or digit."
        )
    return name


PackageName = Annotated[str, Field(pattern=_PACKAGE_NAME.pattern)]


class Policy(StrEnum):
    """Default resolution for managed packages without an exception"""

    LOCAL = 'local'
    STABLE = 'stable'


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ManagedPackage(_Strict):
    """A Synodic package that local-source selection may route to a checkout"""

    url: Annotated[str | None, Field(description='Git URL used to clone a missing checkout.')] = None
    path: Annotated[
        str | None,
        Field(description='Explicit checkout path. When omitted, the checkout is <root>/<name>.'),
    ] = None


class LocalSourceIntent(_Strict):
    """A declarative local-source document accepted by `cppython local apply`.

    Every field except `schema_version` is optional. Omitted fields leave the current state unchanged
    unless the document is applied with `--replace`.
    """

    model_config = ConfigDict(extra='forbid', title='CPPython local-source intent')

    schema_version: Literal[1] = Field(description='Document format version.')
    root: Annotated[
        str | None,
        Field(description='Folder containing checkouts. Relative paths resolve against the document location.'),
    ] = None
    default: Annotated[Policy | None, Field(description='Resolution for packages without an exception.')] = None
    enabled: Annotated[bool | None, Field(description='Set to false to suspend every override.')] = None
    packages: Annotated[
        dict[PackageName, ManagedPackage] | None,
        Field(description='Managed packages, keyed by package name.'),
    ] = None
    local: Annotated[
        list[PackageName] | None,
        Field(description='Packages that resolve to their checkout regardless of the default.'),
    ] = None
    stable: Annotated[
        list[PackageName] | None,
        Field(description='Packages that resolve to published packages regardless of the default.'),
    ] = None

    @field_validator('local', 'stable')
    @classmethod
    def _unique_sorted(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else sorted(set(value))


class AppliedIntent(_Strict):
    """Provenance of the last applied intent document"""

    source: str
    digest: str


class LocalSourceState(_Strict):
    """User-level local-source selection, written only by CPPython"""

    model_config = ConfigDict(extra='forbid', title='CPPython local-source state')

    schema_version: Literal[1] = SCHEMA_VERSION
    enabled: bool = True
    root: str | None = None
    default: Policy = Policy.STABLE
    packages: dict[PackageName, ManagedPackage] = Field(default_factory=dict)
    local: list[PackageName] = Field(default_factory=list)
    stable: list[PackageName] = Field(default_factory=list)
    applied: AppliedIntent | None = None

    @field_validator('local', 'stable')
    @classmethod
    def _unique_sorted(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    def to_intent(self) -> LocalSourceIntent:
        """Export the selection as an intent document that reproduces it"""
        return LocalSourceIntent(
            schema_version=SCHEMA_VERSION,
            root=self.root,
            default=self.default,
            enabled=self.enabled,
            packages={name: package.model_copy() for name, package in sorted(self.packages.items())},
            local=list(self.local),
            stable=list(self.stable),
        )


class Reason(StrEnum):
    """Why a package has its effective mode"""

    DEFAULT = 'default'
    EXCEPTION = 'exception'
    SUSPENDED = 'suspended'
    ENVIRONMENT = 'environment'


class EffectivePackage(_Strict):
    """The resolved state of one managed package"""

    name: str
    mode: Policy
    reason: Reason
    path: str | None
    exists: bool
    commit: str | None = None
    dirty: bool | None = None


class Severity(StrEnum):
    """Diagnostic severity"""

    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


class DiagnosticCode(StrEnum):
    """Stable diagnostic identifiers"""

    INVALID_INPUT = 'invalid-input'
    NO_ROOT = 'no-root'
    MISSING_CHECKOUT = 'missing-checkout'
    IDENTITY_MISMATCH = 'identity-mismatch'
    NOT_MANAGED = 'not-managed'
    CLONE_FAILED = 'clone-failed'
    LOCK_CONTENTION = 'lock-contention'
    PRESERVED_EXCEPTION = 'preserved-exception'
    SUSPENDED = 'suspended'
    DEPENDENCY_CYCLE = 'dependency-cycle'


class ExitCode(IntEnum):
    """Documented process exit codes for `cppython local` commands"""

    OK = 0
    FAILURE = 1
    INVALID_INPUT = 2
    MISSING_CHECKOUT = 3
    IDENTITY_MISMATCH = 4
    LOCK_CONTENTION = 5


EXIT_CODES: dict[DiagnosticCode, ExitCode] = {
    DiagnosticCode.INVALID_INPUT: ExitCode.INVALID_INPUT,
    DiagnosticCode.NO_ROOT: ExitCode.INVALID_INPUT,
    DiagnosticCode.MISSING_CHECKOUT: ExitCode.MISSING_CHECKOUT,
    DiagnosticCode.IDENTITY_MISMATCH: ExitCode.IDENTITY_MISMATCH,
    DiagnosticCode.CLONE_FAILED: ExitCode.MISSING_CHECKOUT,
    DiagnosticCode.LOCK_CONTENTION: ExitCode.LOCK_CONTENTION,
    DiagnosticCode.DEPENDENCY_CYCLE: ExitCode.INVALID_INPUT,
}


class Diagnostic(_Strict):
    """A coded message with an optional fix command"""

    code: DiagnosticCode
    severity: Severity
    message: str
    package: str | None = None
    fix: str | None = None


class Change(_Strict):
    """One field-level change between the previous and resulting state"""

    field: str
    before: object = None
    after: object = None


class LocalSourceResult(_Strict):
    """The machine-readable outcome of a `cppython local` command"""

    model_config = ConfigDict(extra='forbid', title='CPPython local-source result')

    schema_version: Literal[1] = SCHEMA_VERSION
    command: str
    ok: bool
    changed: bool
    dry_run: bool
    state_path: str
    changes: list[Change] = Field(default_factory=list)
    planned_clones: list[str] = Field(default_factory=list)
    state: LocalSourceState
    effective: list[EffectivePackage] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @property
    def exit_code(self) -> ExitCode:
        """The process exit code implied by the first error diagnostic"""
        for diagnostic in self.diagnostics:
            if diagnostic.severity is Severity.ERROR:
                return EXIT_CODES.get(diagnostic.code, ExitCode.FAILURE)
        return ExitCode.OK
