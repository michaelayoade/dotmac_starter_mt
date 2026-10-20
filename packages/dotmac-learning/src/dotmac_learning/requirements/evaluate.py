"""Deterministic evaluation with structured, explainable results.

Children are always evaluated in declared order with no short-circuit, so every
failing leaf is explained. ``reasons`` on a node are why it failed (failing
leaves, in declared order); ``satisfied`` are why it passed. ``not`` swaps the
two and prefixes the codes with ``not.``.

Three-valued logic keeps unknowns fail-closed: a leaf that cannot be decided
(missing fact, invalid params, kind not allowed for the target) is *failed and
indeterminate*. ``not`` never inverts it (it stays failed and indeterminate);
``all`` is false if any child is definitely false, else indeterminate if any
child is, else true; ``any`` is true if any child is true, else indeterminate
if any child is, else false. An indeterminate root fails.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from .conditions import (
    ConditionRegistry,
    EvidenceRef,
    LeafContext,
)
from .expression import All, Cond, Expr, Not, child_path
from .facts import LearnerFacts, Target

__all__ = [
    "EvaluationResult",
    "EvidenceRef",
    "NodeResult",
    "Reason",
    "evaluate",
]


@dataclass(frozen=True)
class Reason:
    code: str
    path: str
    params: Mapping[str, object]


@dataclass(frozen=True)
class NodeResult:
    path: str
    kind: str
    passed: bool
    reasons: tuple[Reason, ...]
    evidence: tuple[EvidenceRef, ...]
    children: tuple[NodeResult, ...]
    satisfied: tuple[Reason, ...] = ()
    # Failed because unknown, not because definitely false (see module doc).
    indeterminate: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    root: NodeResult
    reasons: tuple[Reason, ...]
    evidence: tuple[EvidenceRef, ...]
    indeterminate: bool = False


def _dedupe(evidence: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    seen: set[EvidenceRef] = set()
    ordered: list[EvidenceRef] = []
    for item in evidence:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


def _prefixed(reasons: tuple[Reason, ...]) -> tuple[Reason, ...]:
    return tuple(Reason(f"not.{r.code}", r.path, r.params) for r in reasons)


def _concat(nodes: tuple[NodeResult, ...], *, failed: bool) -> tuple[Reason, ...]:
    out: list[Reason] = []
    for node in nodes:
        out.extend(node.reasons if failed else node.satisfied)
    return tuple(out)


def _eval_cond(
    node: Cond,
    path: str,
    facts: LearnerFacts,
    registry: ConditionRegistry,
    target: Target,
) -> NodeResult:
    spec = registry.require(node.kind)
    if target.kind not in spec.targets:
        reason = Reason("kind_not_allowed_for_target", path, {"kind": node.kind})
        return NodeResult(path, node.kind, False, (reason,), (), (), indeterminate=True)
    try:
        params = spec.params_model.model_validate(dict(node.params))
    except ValidationError:
        reason = Reason("invalid_params", path, {"kind": node.kind})
        return NodeResult(path, node.kind, False, (reason,), (), (), indeterminate=True)
    leaf = spec.evaluate(params, LeafContext(facts, target, path))
    reason = Reason(leaf.code, path, leaf.params)
    if leaf.passed:
        return NodeResult(path, node.kind, True, (), leaf.evidence, (), (reason,))
    return NodeResult(
        path,
        node.kind,
        False,
        (reason,),
        leaf.evidence,
        (),
        indeterminate=leaf.indeterminate,
    )


def _eval(
    node: Expr,
    path: str,
    facts: LearnerFacts,
    registry: ConditionRegistry,
    target: Target,
) -> NodeResult:
    if isinstance(node, Cond):
        return _eval_cond(node, path, facts, registry, target)
    if isinstance(node, Not):
        child = _eval(node.child, child_path(path, "not", 0), facts, registry, target)
        if child.indeterminate:
            return NodeResult(
                path,
                "not",
                False,
                child.reasons,
                child.evidence,
                (child,),
                indeterminate=True,
            )
        if child.passed:
            return NodeResult(
                path, "not", False, _prefixed(child.satisfied), child.evidence, (child,)
            )
        return NodeResult(
            path, "not", True, (), child.evidence, (child,), _prefixed(child.reasons)
        )
    kind = "all" if isinstance(node, All) else "any"
    children = tuple(
        _eval(child, child_path(path, kind, index), facts, registry, target)
        for index, child in enumerate(node.children)
    )
    evidence = _dedupe(tuple(e for child in children for e in child.evidence))
    unknown = any(child.indeterminate for child in children)
    if kind == "all":
        definitely_false = any(
            not child.passed and not child.indeterminate for child in children
        )
        if not definitely_false and not unknown:
            return NodeResult(
                path,
                kind,
                True,
                (),
                evidence,
                children,
                _concat(children, failed=False),
            )
        return NodeResult(
            path,
            kind,
            False,
            _concat(children, failed=True),
            evidence,
            children,
            indeterminate=unknown and not definitely_false,
        )
    if not children:
        reason = Reason("empty_any", path, {})
        return NodeResult(path, kind, False, (reason,), (), ())
    if any(child.passed for child in children):
        satisfied = tuple(
            r for child in children if child.passed for r in child.satisfied
        )
        return NodeResult(path, kind, True, (), evidence, children, satisfied)
    return NodeResult(
        path,
        kind,
        False,
        _concat(children, failed=True),
        evidence,
        children,
        indeterminate=unknown,
    )


def evaluate(
    expr: Expr, facts: LearnerFacts, registry: ConditionRegistry, target: Target
) -> EvaluationResult:
    """Evaluate ``expr`` for one learner and target.

    Unknown condition kinds raise ``UnknownConditionKindError``: an expression
    that cannot be understood must not silently pass or fail.
    """
    root = _eval(expr, "", facts, registry, target)
    return EvaluationResult(
        passed=root.passed,
        root=root,
        reasons=root.reasons if not root.passed else (),
        evidence=_dedupe(root.evidence),
        indeterminate=root.indeterminate,
    )
