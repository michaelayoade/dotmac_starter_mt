"""Collections takes locks in one order: hold reference, case, subject.

The subject lock (``_serialize_identity(..., "subject", ...)``, and every call to
``_recompute_subject`` that takes it) is the innermost Collections lock. A command
that asks for a case lock (the advisory identity or ``_case/_live_case(lock=True)``)
after it could deadlock against a command holding the case and waiting for the
subject. The scan is source order within one public command, so a violation is a
static fact; planted sources prove the detector refuses it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: Acquisition order: a lock may be taken only if no later-ranked lock came before.
RANK = {"hold": 0, "case": 1, "subject": 2}

SERVICE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-collections/src/dotmac_collections/service.py"
)


def _lock_events(function: ast.AST) -> list[tuple[int, str]]:
    events: list[tuple[int, str]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        kind: str | None = None
        if name == "_serialize_identity" and len(node.args) >= 3:
            part = node.args[2]
            if isinstance(part, ast.Constant) and part.value in RANK:
                kind = str(part.value)
        elif name == "_recompute_subject":
            kind = "subject"
        elif name in {"_case", "_live_case"} and any(
            keyword.arg == "lock"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            kind = "case"
        if kind is not None:
            events.append((node.lineno * 1000 + node.col_offset, kind))
    return sorted(events)


def lock_order_violations(source: str) -> list[str]:
    problems: list[str] = []
    for node in ast.parse(source).body:
        functions = [node] if isinstance(node, ast.FunctionDef) else []
        if isinstance(node, ast.ClassDef):
            functions = [n for n in node.body if isinstance(n, ast.FunctionDef)]
        for function in functions:
            highest = -1
            for _, kind in _lock_events(function):
                if RANK[kind] < highest:
                    problems.append(f"{function.name}: {kind} lock after a later lock")
                    break
                highest = max(highest, RANK[kind])
    return problems


def test_the_real_service_takes_case_locks_before_subject_locks() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    assert lock_order_violations(source) == []
    # Non-vacuous: the service really does take both.
    assert '"subject"' in source and "_recompute_subject(" in source
    assert '"hold"' in source


@pytest.mark.parametrize(
    "source",
    [
        "def f(db, scope):\n"
        "    _serialize_identity(db, scope, 'subject', 'x')\n"
        "    _serialize_identity(db, scope, 'case', 'y')\n",
        "def f(db, scope):\n"
        "    _recompute_subject(db, scope=scope)\n"
        "    _case(db, scope, 1, lock=True)\n",
        "class C:\n    def f(db, scope):\n"
        "        _serialize_identity(db, scope, 'subject', 'x')\n"
        "        _live_case(db, scope, lock=True)\n",
        "def f(db, scope):\n"
        "    _serialize_identity(db, scope, 'case', 'y')\n"
        "    _serialize_identity(db, scope, 'hold', 'z')\n",
        "def f(db, scope):\n"
        "    _recompute_subject(db, scope=scope)\n"
        "    _serialize_identity(db, scope, 'hold', 'z')\n",
    ],
)
def test_the_detector_refuses_a_subject_lock_before_a_case_lock(source: str) -> None:
    assert lock_order_violations(source)


def test_the_detector_accepts_the_allowed_order() -> None:
    source = (
        "def f(db, scope):\n"
        "    _serialize_identity(db, scope, 'hold', 'z')\n"
        "    _serialize_identity(db, scope, 'case', 'y')\n"
        "    _case(db, scope, 1, lock=True)\n"
        "    _recompute_subject(db, scope=scope)\n"
    )
    assert lock_order_violations(source) == []
