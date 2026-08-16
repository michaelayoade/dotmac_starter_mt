"""Template Studio never relies on the temporary party-only actor fallback."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("packages/dotmac-template-studio/src/dotmac_template_studio")


def _actor_problem(call: ast.Call) -> str | None:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    actor_type = keywords.get("actor_type")
    actor_id = keywords.get("actor_id")
    party_id = keywords.get("actor_party_id")
    if actor_type is None or actor_id is None or party_id is None:
        return "actor_party_id, actor_type and actor_id must all be explicit"
    if not isinstance(actor_type, ast.Constant) or actor_type.value != "user":
        return "Template Studio's authenticated Party actor must be a user"
    if ast.unparse(actor_id) != f"str({ast.unparse(party_id)})":
        return "actor_id must identify the same principal as actor_party_id"
    return None


def test_every_template_studio_audit_writer_names_the_actor_pair() -> None:
    calls: list[tuple[Path, ast.Call]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        calls.extend(
            (path, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "write_audit_event"
        )

    assert len(calls) == 9, "the ratchet must be updated when the caller set changes"
    missing: list[str] = []
    for path, call in calls:
        if problem := _actor_problem(call):
            missing.append(f"{path}:{call.lineno}: {problem}")
    assert not missing, f"non-canonical audit actor callers: {missing}"


def test_the_actor_guard_rejects_the_retired_party_only_shape() -> None:
    """Sensitivity: the detector fails on the exact compatibility shape."""
    tree = ast.parse("write_audit_event(db, actor_party_id=actor.id)")
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert _actor_problem(call) == (
        "actor_party_id, actor_type and actor_id must all be explicit"
    )
