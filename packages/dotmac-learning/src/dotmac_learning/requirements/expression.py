"""Requirement expression tree: strict parse, canonical JSON, content hash, walk.

Pure data. Nodes are ``all`` / ``any`` / ``not`` compositions over ``cond``
leaves. Child order is significant (evaluation and explanation follow declared
order), so it is part of the canonical form and of the content hash.

Node paths are slash-joined ``<parent kind>/<child index>`` pairs from the
root, which itself has the empty path: ``""``, ``"all/1"``, ``"all/1/any/0"``.
A path is stable for a given expression and is what ``manual_approval``
approvals bind to.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

# Parser safety bounds (class B: stop adversarial input before recursion
# limits). They are deliberately far above the publishable limits in
# ``validate`` (MAX_DEPTH / MAX_NODES), which are reported as issues.
HARD_MAX_DEPTH = 64
HARD_MAX_NODES = 10_000


class ExpressionError(ValueError):
    """Raised when a document is not a well-formed requirement expression."""


@dataclass(frozen=True)
class Cond:
    kind: str
    params: Mapping[str, object]


@dataclass(frozen=True)
class All:
    children: tuple[Expr, ...]


@dataclass(frozen=True)
class Any:
    children: tuple[Expr, ...]


@dataclass(frozen=True)
class Not:
    child: Expr


Expr = All | Any | Not | Cond


def child_path(parent_path: str, parent_kind: str, index: int) -> str:
    segment = f"{parent_kind}/{index}"
    return f"{parent_path}/{segment}" if parent_path else segment


def node_kind(node: Expr) -> str:
    if isinstance(node, All):
        return "all"
    if isinstance(node, Any):
        return "any"
    if isinstance(node, Not):
        return "not"
    return "cond"


def node_children(node: Expr) -> tuple[Expr, ...]:
    if isinstance(node, (All, Any)):
        return node.children
    if isinstance(node, Not):
        return (node.child,)
    return ()


def walk(expr: Expr) -> Iterator[tuple[str, Expr]]:
    """Yield ``(path, node)`` in preorder, children in declared order."""
    stack: list[tuple[str, Expr]] = [("", expr)]
    while stack:
        path, node = stack.pop()
        yield path, node
        kind = node_kind(node)
        children = node_children(node)
        for index in range(len(children) - 1, -1, -1):
            stack.append((child_path(path, kind, index), children[index]))


def _check_json_value(value: object, where: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExpressionError(f"{where}: non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _check_json_value(item, where)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExpressionError(f"{where}: non-string key")
            _check_json_value(item, where)
        return
    raise ExpressionError(f"{where}: value is not JSON-compatible")


def _parse_node(node: object, path: str, depth: int, budget: list[int]) -> Expr:
    if depth > HARD_MAX_DEPTH:
        raise ExpressionError(f"{path or 'root'}: nesting too deep")
    budget[0] -= 1
    if budget[0] < 0:
        raise ExpressionError("expression has too many nodes")
    if not isinstance(node, dict):
        raise ExpressionError(f"{path or 'root'}: node must be an object")
    keys = set(node)
    if "cond" in keys:
        extra = keys - {"cond", "params"}
        if extra:
            raise ExpressionError(f"{path or 'root'}: unknown keys {sorted(extra)}")
        kind = node["cond"]
        if not isinstance(kind, str) or not kind:
            raise ExpressionError(f"{path or 'root'}: cond kind must be a string")
        if "params" not in node:
            raise ExpressionError(f"{path or 'root'}: cond requires params")
        params = node["params"]
        if not isinstance(params, dict):
            raise ExpressionError(f"{path or 'root'}: params must be an object")
        _check_json_value(params, path or "root")
        return Cond(kind=kind, params=dict(params))
    if len(keys) != 1:
        raise ExpressionError(
            f"{path or 'root'}: node must have exactly one of all/any/not/cond"
        )
    (key,) = keys
    if key in ("all", "any"):
        raw = node[key]
        if not isinstance(raw, list):
            raise ExpressionError(f"{path or 'root'}: {key} must be a list")
        children = tuple(
            _parse_node(item, child_path(path, key, index), depth + 1, budget)
            for index, item in enumerate(raw)
        )
        return All(children) if key == "all" else Any(children)
    if key == "not":
        return Not(
            _parse_node(node["not"], child_path(path, "not", 0), depth + 1, budget)
        )
    raise ExpressionError(f"{path or 'root'}: unknown key {key!r}")


def parse(document: object) -> Expr:
    """Strictly parse a decoded JSON value (or a JSON string/bytes)."""
    if isinstance(document, (str, bytes)):
        try:
            document = json.loads(document)
        except ValueError as exc:
            raise ExpressionError(f"malformed JSON: {exc}") from exc
    return _parse_node(document, "", 1, [HARD_MAX_NODES])


def to_data(expr: Expr) -> dict[str, object]:
    """Plain-JSON form of ``expr`` (inverse of ``parse``)."""
    if isinstance(expr, Cond):
        return {"cond": expr.kind, "params": dict(expr.params)}
    if isinstance(expr, Not):
        return {"not": to_data(expr.child)}
    key = "all" if isinstance(expr, All) else "any"
    return {key: [to_data(child) for child in expr.children]}


def to_json(expr: Expr) -> str:
    """Canonical JSON: sorted keys, no whitespace, ASCII-only."""
    return json.dumps(to_data(expr), sort_keys=True, separators=(",", ":"))


def content_hash(expr: Expr) -> str:
    return hashlib.sha256(to_json(expr).encode("utf-8")).hexdigest()


def combine_additive(parent: Expr, own: Expr) -> Expr:
    """Effective expression of a narrower scope: parent AND own.

    Course and activity requirements only ever add to the parent's; there is
    deliberately no "replace" (decision 2026-10-20), so a narrower scope can
    never drop a tenant gate.
    """
    return All((parent, own))
