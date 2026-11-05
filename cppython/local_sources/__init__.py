"""User-level selection of local checkouts for managed native dependencies.

The selection lives outside every repository (see `store.locate_state`) and is changed only through
these operations or the `cppython local` commands built on them.
"""

from cppython.local_sources.operations import (
    ProjectSelection,
    add,
    apply,
    dependency_names,
    discover,
    export,
    parse_intent,
    project_selection,
    remove,
    schema,
    set_enabled,
    set_root,
    show,
    use,
)
from cppython.local_sources.schema import (
    Diagnostic,
    DiagnosticCode,
    EffectivePackage,
    ExitCode,
    LocalSourceIntent,
    LocalSourceResult,
    LocalSourceState,
    Policy,
)
from cppython.local_sources.store import ENVIRONMENT_VARIABLE, StateLocation, locate_state

__all__ = [
    'ENVIRONMENT_VARIABLE',
    'Diagnostic',
    'DiagnosticCode',
    'EffectivePackage',
    'ExitCode',
    'LocalSourceIntent',
    'LocalSourceResult',
    'LocalSourceState',
    'Policy',
    'ProjectSelection',
    'StateLocation',
    'add',
    'apply',
    'dependency_names',
    'discover',
    'export',
    'locate_state',
    'parse_intent',
    'project_selection',
    'remove',
    'schema',
    'set_enabled',
    'set_root',
    'show',
    'use',
]
