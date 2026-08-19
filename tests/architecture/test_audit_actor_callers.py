"""Every shipped tenant-audit caller supplies the canonical actor identity."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "packages",
)


def _writer_calls() -> list[tuple[Path, ast.Call]]:
    calls: list[tuple[Path, ast.Call]] = []
    for root in PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            calls.extend(
                (path, node)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "write_audit_event"
            )
    return calls


def _actor_problem(call: ast.Call) -> str | None:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    actor_type = keywords.get("actor_type")
    actor_id = keywords.get("actor_id")
    party_id = keywords.get("actor_party_id")
    if actor_type is None:
        return "actor_type must be explicit; actor_party_id is only enrichment"
    if not isinstance(actor_type, ast.Constant) or not isinstance(
        actor_type.value, str
    ):
        return "actor_type must be an auditable literal at each shipped call site"
    if actor_type.value != "system" and actor_id is None:
        return "every non-system actor needs an explicit actor_id"
    if actor_type.value == "user" and party_id is not None:
        if actor_id is None or ast.unparse(actor_id) != f"str({ast.unparse(party_id)})":
            return "an authenticated Party user must identify that same principal"
    return None


def test_every_shipped_audit_writer_names_the_canonical_actor() -> None:
    calls = _writer_calls()
    assert len(calls) == 24, "the ratchet must change when the caller set changes"

    problems = [
        f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: {problem}"
        for path, call in calls
        if (problem := _actor_problem(call))
    ]
    assert not problems, f"non-canonical audit actor callers: {problems}"


def test_actor_guard_ignores_prose_that_only_names_the_writer() -> None:
    """A source-text grep would flag the explanation of the invariant itself."""
    tree = ast.parse("# write_audit_event must name the actor pair\nvalue = 1\n")
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_audit_event"
        for node in ast.walk(tree)
    )


def test_actor_guard_rejects_the_retired_party_only_shape() -> None:
    """Sensitivity: deleting the pair recreates the exact removed fallback."""
    tree = ast.parse("write_audit_event(db, actor_party_id=party.id)")
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert _actor_problem(call) == (
        "actor_type must be explicit; actor_party_id is only enrichment"
    )


def test_actor_guard_rejects_a_user_whose_identifier_is_still_derived() -> None:
    """Naming only the kind still relies on the other half of the old shim."""
    tree = ast.parse(
        'write_audit_event(db, actor_party_id=party.id, actor_type="user")'
    )
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert _actor_problem(call) == "every non-system actor needs an explicit actor_id"
