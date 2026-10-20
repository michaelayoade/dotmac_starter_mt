"""Pure requirements engine: conditions, composition, validation, evaluation.

Not wired into any existing behaviour. Consumers import this package directly.
The legacy compiler needs the ORM-backed entitlements, so it is imported from
``.legacy`` explicitly and is not re-exported here.
"""

from .conditions import (
    BUILTIN_CONDITIONS,
    ConditionRegistry,
    ConditionSpec,
    DuplicateConditionKindError,
    EvidenceRef,
    LeafContext,
    LeafResult,
    Reference,
    UnknownConditionKindError,
    default_registry,
)
from .evaluate import EvaluationResult, NodeResult, Reason, evaluate
from .expression import (
    All,
    Any,
    Cond,
    Expr,
    ExpressionError,
    Not,
    combine_additive,
    content_hash,
    parse,
    to_data,
    to_json,
    walk,
)
from .facts import LearnerFacts, Route, Target
from .validate import (
    CERTIFICATE_FINAL_MIN_PCT,
    MAX_DEPTH,
    MAX_NODES,
    ReferenceCatalog,
    ValidationIssue,
    dependency_edges,
    find_cycles,
    validate,
)

__all__ = [
    "BUILTIN_CONDITIONS",
    "CERTIFICATE_FINAL_MIN_PCT",
    "MAX_DEPTH",
    "MAX_NODES",
    "All",
    "Any",
    "Cond",
    "ConditionRegistry",
    "ConditionSpec",
    "DuplicateConditionKindError",
    "EvaluationResult",
    "EvidenceRef",
    "Expr",
    "ExpressionError",
    "LeafContext",
    "LeafResult",
    "LearnerFacts",
    "NodeResult",
    "Not",
    "Reason",
    "Reference",
    "ReferenceCatalog",
    "Route",
    "Target",
    "UnknownConditionKindError",
    "ValidationIssue",
    "combine_additive",
    "content_hash",
    "default_registry",
    "dependency_edges",
    "evaluate",
    "find_cycles",
    "parse",
    "to_data",
    "to_json",
    "validate",
    "walk",
]
