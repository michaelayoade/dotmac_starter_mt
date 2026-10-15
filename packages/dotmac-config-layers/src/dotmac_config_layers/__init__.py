"""Generic, dependency-free layered-configuration composition engine.

Weighted, tiered layers are matched to a subject, merged deterministically
(NetBox config-context semantics plus tested deviations), validated against
module schemas and compared with observed state. The package is pure: it imports
only the standard library, pydantic and itself, owns no state and does no I/O.
Provider plug-ins implement :class:`DomainPlugin`; this package names no provider.
"""

from __future__ import annotations

from typing import Final

from .compose import (
    SCHEMA_VERSION,
    AppliedLayer,
    Effective,
    ExceptionStatus,
    LapsedException,
    ModuleResult,
    compose,
    provenance_gaps,
    validate_exception,
    validate_layer_document,
)
from .derive import (
    NOT_DERIVED,
    DerivationCycleError,
    DerivedRule,
    Ref,
)
from .document import (
    DocumentError,
    canonical_json,
    hash_of,
    pointer,
    sha256_hex,
)
from .drift import (
    NON_DRIFT,
    DriftItem,
    DriftStatus,
    ModuleEvaluation,
    ModuleStatus,
    NotEvaluated,
    Proposal,
    Suppression,
    Unknown,
)
from .evaluate import (
    evaluate_module,
)
from .exceptions import (
    LAPSE_REPORT_DAYS,
    MAX_EXCEPTION_DAYS,
    MIN_REASON_CHARS,
    ExceptionKind,
    ExceptionSpec,
    ExceptionState,
    exception_state,
)
from .fields import (
    OPTIONAL_UNMANAGED,
    REQUIRED,
    AccessKind,
    FieldSpec,
    Kind,
    Strategy,
    Tie,
    lst,
    mapping,
    obj,
    scalar,
)
from .layers import (
    Layer,
    LayerError,
    LayerRevision,
    Subject,
    TierSpec,
)
from .manifest import module
from .merge import (
    Conflict,
    MergeError,
    Origin,
    Provenance,
    Shadow,
    Tombstone,
)
from .plugin import (
    MODES,
    DomainPlugin,
    Invalid,
    Module,
    ModuleBase,
    PluginError,
    SurfaceClaim,
    plugin_problems,
)
from .widening import (
    WideningItem,
    mode_widening,
    params_widening,
    widening,
)

__version__: Final[str] = "0.1.0a1"

__all__ = [
    "AccessKind",
    "AppliedLayer",
    "Conflict",
    "DerivationCycleError",
    "DerivedRule",
    "DocumentError",
    "DomainPlugin",
    "DriftItem",
    "DriftStatus",
    "Effective",
    "ExceptionKind",
    "ExceptionSpec",
    "ExceptionState",
    "ExceptionStatus",
    "FieldSpec",
    "Invalid",
    "Kind",
    "LAPSE_REPORT_DAYS",
    "LapsedException",
    "Layer",
    "LayerError",
    "LayerRevision",
    "MAX_EXCEPTION_DAYS",
    "MIN_REASON_CHARS",
    "MODES",
    "MergeError",
    "Module",
    "ModuleBase",
    "ModuleEvaluation",
    "ModuleResult",
    "ModuleStatus",
    "NON_DRIFT",
    "NOT_DERIVED",
    "NotEvaluated",
    "OPTIONAL_UNMANAGED",
    "Origin",
    "PluginError",
    "Proposal",
    "Provenance",
    "REQUIRED",
    "Ref",
    "SCHEMA_VERSION",
    "Shadow",
    "Strategy",
    "Subject",
    "Suppression",
    "SurfaceClaim",
    "Tie",
    "TierSpec",
    "Tombstone",
    "Unknown",
    "WideningItem",
    "__version__",
    "canonical_json",
    "compose",
    "evaluate_module",
    "exception_state",
    "hash_of",
    "lst",
    "mapping",
    "mode_widening",
    "module",
    "obj",
    "params_widening",
    "plugin_problems",
    "pointer",
    "provenance_gaps",
    "scalar",
    "sha256_hex",
    "validate_exception",
    "validate_layer_document",
    "widening",
]
