"""Static validation before publish: everything that needs no database.

Errors block publishing; warnings need acknowledgement. Existence and shape of
learning objects is answered by a tenant-scoped ``ReferenceCatalog`` supplied
by the caller, so a foreign-tenant id is simply "does not exist".

Depth counts nodes on the longest root-to-leaf path (a lone ``cond`` is 1).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .conditions import (
    AttemptsAtLeastParams,
    ConditionRegistry,
    EnrolledBeforeParams,
    MinimumScoreParams,
    Reference,
)
from .expression import All, Any, Cond, Expr, Not, walk
from .facts import Target

# Class B: evaluation-safety bounds, not tunable policy.
MAX_DEPTH = 6
MAX_NODES = 64
# Class B: hard floor for certificate-bearing finals (decision 2026-10-20);
# settings can never go below it.
CERTIFICATE_FINAL_MIN_PCT = 50

Severity = Literal["error", "warning"]
_Add = Callable[[Severity, str, str, str], None]


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    path: str
    message: str


class ReferenceCatalog(Protocol):
    def course_exists(self, course_id: UUID) -> bool: ...

    def activity_exists(self, activity_id: UUID) -> bool: ...

    def activity_max_attempts(self, activity_id: UUID) -> int | None: ...

    def activity_gradable(self, activity_id: UUID) -> bool: ...


def _parsed_params(
    node: Cond, registry: ConditionRegistry
) -> tuple[BaseModel | None, bool]:
    """Return ``(params, usable)``; unknown kind or bad params give ``None``."""
    spec = registry.get(node.kind)
    if spec is None:
        return None, False
    try:
        return spec.params_model.model_validate(dict(node.params)), True
    except ValidationError:
        return None, False


def dependency_edges(
    target: Target, expr: Expr, registry: ConditionRegistry
) -> Iterator[tuple[UUID, UUID]]:
    """Course -> course edges: the target course depends on referenced courses."""
    if target.kind != "course_access":
        return
    for _path, node in walk(expr):
        if not isinstance(node, Cond):
            continue
        params, usable = _parsed_params(node, registry)
        spec = registry.get(node.kind)
        if not usable or params is None or spec is None:
            continue
        for ref in spec.references(params):
            if ref.kind == "course":
                yield target.id, ref.id


def find_cycles(edges: Mapping[UUID, set[UUID]]) -> list[list[UUID]]:
    """Cycles by iterative DFS back edges, in deterministic order.

    Each cycle is rotated to start at its smallest id. Every strongly
    connected component with a cycle yields at least one entry.
    """
    nodes = sorted(set(edges) | {n for targets in edges.values() for n in targets})
    state: dict[UUID, int] = {}  # 1 = on stack, 2 = done
    cycles: list[list[UUID]] = []
    seen: set[tuple[UUID, ...]] = set()
    for root in nodes:
        if root in state:
            continue
        state[root] = 1
        path: list[UUID] = [root]
        stack: list[Iterator[UUID]] = [iter(sorted(edges.get(root, set())))]
        while stack:
            advanced = False
            for nxt in stack[-1]:
                status = state.get(nxt)
                if status is None:
                    state[nxt] = 1
                    path.append(nxt)
                    stack.append(iter(sorted(edges.get(nxt, set()))))
                    advanced = True
                    break
                if status == 1:
                    cycle = path[path.index(nxt) :]
                    pivot = cycle.index(min(cycle))
                    cycle = cycle[pivot:] + cycle[:pivot]
                    key = tuple(cycle)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(cycle)
            if not advanced:
                state[path.pop()] = 2
                stack.pop()
    return cycles


def _cycle_through(start: UUID, edges: Mapping[UUID, set[UUID]]) -> list[UUID] | None:
    """Shortest cycle through ``start`` (BFS, sorted neighbours), if any."""
    parents: dict[UUID, UUID | None] = {}
    queue: deque[UUID] = deque()
    for nxt in sorted(edges.get(start, set())):
        if nxt == start:
            return [start]
        parents[nxt] = None
        queue.append(nxt)
    while queue:
        node = queue.popleft()
        for nxt in sorted(edges.get(node, set())):
            if nxt == start:
                chain = [node]
                while (parent := parents[chain[-1]]) is not None:
                    chain.append(parent)
                return [start, *reversed(chain)]
            if nxt not in parents:
                parents[nxt] = node
                queue.append(nxt)
    return None


def _constant(expr: Expr) -> bool | None:
    """True/False when the expression cannot depend on any fact, else None."""
    if isinstance(expr, Cond):
        return None
    if isinstance(expr, Not):
        inner = _constant(expr.child)
        return None if inner is None else not inner
    values = [_constant(child) for child in expr.children]
    if isinstance(expr, All):
        if any(v is False for v in values):
            return False
        return True if all(v is True for v in values) else None
    if any(v is True for v in values):
        return True
    return False if all(v is False for v in values) else None


def validate(
    expr: Expr,
    registry: ConditionRegistry,
    target: Target,
    catalog: ReferenceCatalog,
    *,
    now: datetime,
    extra_edges: Mapping[UUID, set[UUID]] | None = None,
    certificate_bearing: bool = False,
) -> list[ValidationIssue]:
    """Validate ``expr`` for ``target``.

    ``extra_edges`` carries the course dependency edges the expression cannot
    see itself: other active and candidate course-scoped versions and track
    order edges. Cycles through the target course are errors.

    ``certificate_bearing`` is the hook for the certificate floor: the caller
    (which knows the context; not modelled until a later slice) sets it, and
    any ``minimum_score`` below ``CERTIFICATE_FINAL_MIN_PCT`` is then an error.
    """
    issues: list[ValidationIssue] = []

    def add(severity: Severity, code: str, path: str, message: str) -> None:
        issues.append(ValidationIssue(severity, code, path, message))

    nodes = list(walk(expr))
    if len(nodes) > MAX_NODES:
        add("error", "too_many_nodes", "", f"{len(nodes)} nodes exceeds {MAX_NODES}")
    for path, _node in nodes:
        depth = (path.count("/") + 1) // 2 + 1 if path else 1
        if depth > MAX_DEPTH:
            add("error", "depth_exceeded", path, f"depth {depth} exceeds {MAX_DEPTH}")
            break

    non_monotone_under_not: set[str] = set()
    for path, node in nodes:
        if isinstance(node, Any) and not node.children:
            add("error", "empty_any", path, "any() with no children can never pass")
        if isinstance(node, Not):
            for sub_path, sub in walk(node.child):
                if isinstance(sub, Cond):
                    spec = registry.get(sub.kind)
                    if spec is not None and not spec.monotone:
                        non_monotone_under_not.add(_join(path, "not", sub_path))
        if not isinstance(node, Cond):
            continue
        spec = registry.get(node.kind)
        if spec is None:
            add("error", "unknown_kind", path, f"unknown condition kind {node.kind!r}")
            continue
        if target.kind not in spec.targets:
            add(
                "error",
                "kind_not_allowed_for_target",
                path,
                f"{node.kind} cannot be used on {target.kind}",
            )
        params, usable = _parsed_params(node, registry)
        if not usable or params is None:
            add("error", "params_invalid", path, f"invalid params for {node.kind}")
            continue
        _check_references(spec.references(params), path, catalog, target, add)
        _check_thresholds(params, path, catalog, now, add)
        if (
            certificate_bearing
            and isinstance(params, MinimumScoreParams)
            and params.min_pct < CERTIFICATE_FINAL_MIN_PCT
        ):
            add(
                "error",
                "below_certificate_floor",
                path,
                f"minimum score below the {CERTIFICATE_FINAL_MIN_PCT}% floor",
            )
    for leaf_path in sorted(non_monotone_under_not):
        add(
            "error",
            "negated_non_monotone",
            leaf_path,
            "not() over a condition that can stop being satisfied is refused",
        )

    constant = _constant(expr)
    if constant is not None:
        add(
            "warning",
            "constant_true" if constant else "constant_false",
            "",
            "expression does not depend on any learner fact",
        )

    edges: dict[UUID, set[UUID]] = {k: set(v) for k, v in (extra_edges or {}).items()}
    for src, dst in dependency_edges(target, expr, registry):
        edges.setdefault(src, set()).add(dst)
    if target.kind == "course_access":
        cycle = _cycle_through(target.id, edges)
        if cycle is not None and len(cycle) > 1:
            names = " -> ".join(str(c) for c in [*cycle, cycle[0]])
            add("error", "cycle", "", f"requirement cycle: {names}")
    return issues


def _join(not_path: str, kind: str, sub_path: str) -> str:
    base = f"{not_path}/{kind}/0" if not_path else f"{kind}/0"
    if not sub_path:
        return base
    return f"{base}/{sub_path}"


def _check_references(
    refs: Iterable[Reference],
    path: str,
    catalog: ReferenceCatalog,
    target: Target,
    add: _Add,
) -> None:
    for ref in refs:
        if ref.kind == "course":
            if target.kind == "course_access" and ref.id == target.id:
                add("error", "self_reference", path, "course requires itself")
            exists = catalog.course_exists(ref.id)
        else:
            exists = catalog.activity_exists(ref.id)
        if not exists:
            add(
                "error",
                "missing_reference",
                path,
                f"{ref.kind} {ref.id} does not exist in this tenant",
            )


def _check_thresholds(
    params: BaseModel,
    path: str,
    catalog: ReferenceCatalog,
    now: datetime,
    add: _Add,
) -> None:
    if isinstance(params, AttemptsAtLeastParams):
        limit = (
            catalog.activity_max_attempts(params.activity_id)
            if catalog.activity_exists(params.activity_id)
            else None
        )
        if limit is not None and params.n > limit:
            add(
                "error",
                "impossible_threshold",
                path,
                f"needs {params.n} attempts but the activity allows {limit}",
            )
    elif isinstance(params, MinimumScoreParams):
        if (
            params.subject == "activity"
            and catalog.activity_exists(params.ref_id)
            and not catalog.activity_gradable(params.ref_id)
        ):
            add(
                "error",
                "impossible_threshold",
                path,
                "minimum score on an activity with no gradable items",
            )
    elif isinstance(params, EnrolledBeforeParams) and params.at <= now:
        add(
            "warning",
            "enrolled_before_in_past",
            path,
            "cutoff is already in the past; new enrollments can never satisfy it",
        )
