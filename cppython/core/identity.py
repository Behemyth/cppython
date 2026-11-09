"""Load and select build identities without depending on a provider or generator."""

import platform
import tomllib
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cppython.core.exception import ConfigException


class BuildIdentity(BaseModel, extra='forbid'):
    """The build facts and dependency configurations for one target."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    name: Annotated[str, Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')]
    os: Annotated[str, Field(min_length=1)]
    arch: Annotated[str, Field(min_length=1)]
    compiler: Annotated[str, Field(min_length=1)]
    standard_library: Annotated[str, Field(alias='standard-library', min_length=1)]
    variant: str = ''
    configurations: Annotated[list[str], Field(min_length=1)]
    dependency_build_types: Annotated[dict[str, str], Field(alias='dependency-build-types')]

    @model_validator(mode='after')
    def validate_configurations(self) -> Self:
        """Require one nonempty dependency build type for each unique configuration."""
        if any(not configuration.strip() for configuration in self.configurations):
            raise ValueError('Configuration names must not be empty')
        if len(set(self.configurations)) != len(self.configurations):
            raise ValueError('Configuration names must be unique')
        if set(self.dependency_build_types) != set(self.configurations):
            raise ValueError('Dependency build types must cover exactly the listed configurations')
        if any(not build_type.strip() for build_type in self.dependency_build_types.values()):
            raise ValueError('Dependency build types must not be empty')
        return self


class IdentityTable(BaseModel, extra='forbid'):
    """A versioned collection of build identities."""

    version: Literal[1]
    identities: Annotated[list[BuildIdentity], Field(min_length=1)]

    @model_validator(mode='after')
    def validate_names(self) -> Self:
        """Reject ambiguous names."""
        names = [identity.name for identity in self.identities]
        if len(set(names)) != len(names):
            raise ValueError('Identity names must be unique')
        return self


def load_identity_table(path: Path) -> IdentityTable:
    """Load an identity table, reporting malformed files with their path."""
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        return IdentityTable.model_validate(data)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ConfigException(f'Cannot load build identities from {path}: {error}') from error


def select_identity(path: Path, requested: str | None = None, *, required: bool = False) -> BuildIdentity | None:
    """Select an explicit identity or the unambiguous default for the current host.

    Projects without a default table retain their existing non-identity behavior.
    Explicit file and identity selections must always resolve.
    """
    if not path.is_file():
        if required or requested is not None or path.exists():
            raise ConfigException(f'Build identity table does not exist or is not a file: {path}')
        return None

    table = load_identity_table(path)
    if requested is not None:
        matches = [identity for identity in table.identities if identity.name == requested]
        if len(matches) != 1:
            raise ConfigException(
                f"Unknown build identity '{requested}' in {path}. "
                f'Available identities: {", ".join(identity.name for identity in table.identities)}'
            )
        return matches[0]

    operating_system = {'windows': 'windows', 'linux': 'linux', 'darwin': 'macos'}.get(platform.system().casefold())
    architecture = {
        'amd64': 'x64',
        'x86_64': 'x64',
        'arm64': 'arm64',
        'aarch64': 'arm64',
    }.get(platform.machine().casefold())
    matches = [
        identity
        for identity in table.identities
        if identity.os == operating_system and identity.arch == architecture and not identity.variant
    ]
    if len(matches) != 1:
        raise ConfigException(
            f'No unique default build identity for {platform.system()}/{platform.machine()} in {path}. '
            'Select one explicitly with --identity or CPPYTHON_IDENTITY.'
        )
    return matches[0]
