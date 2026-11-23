"""Q4 (architect r4, L3 r5): `threading` is allowed ONLY in `lease.py`, and only for
`LeaseKeeper`. The thread is a daemon, touches no secret material, calls only
`lease/renew`, and is stopped and joined before `release`. The check covers
subpackages and the dynamic spellings (`__import__`, `importlib.import_module`)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)
BANNED = {"threading", "_thread"}


def names_threading(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] in BANNED for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] in BANNED
        ):
            return True
        if isinstance(node, ast.Call):
            callee = node.func
            dynamic = (isinstance(callee, ast.Name) and callee.id == "__import__") or (
                isinstance(callee, ast.Attribute) and callee.attr == "import_module"
            )
            if (
                dynamic
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).split(".")[0] in BANNED
            ):
                return True
    return False


def test_threading_is_imported_only_by_the_lease_module() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in sorted(SRC.rglob("*.py"))  # subpackages included
        if p.relative_to(SRC).as_posix() != "lease.py"
        and names_threading(p.read_text())
    ]
    assert offenders == []


def test_the_guard_catches_every_spelling() -> None:
    for source in (
        "import threading",
        "import threading as t",
        "from threading import Thread",
        "import _thread",
        "__import__('threading')",
        "import importlib\nimportlib.import_module('threading')",
        "from importlib import import_module\nimport_module('threading.local')",
    ):
        assert names_threading(source), source
    for source in ("import json", "__import__('json')", "x = 'threading'"):
        assert not names_threading(source), source


def test_the_lease_keeper_thread_is_a_daemon_joined_before_release() -> None:
    path = SRC / "lease.py"
    if not names_threading(path.read_text()):
        return  # the renew loop lands with #1146; this test then bites
    tree = ast.parse(path.read_text())
    keeper = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "LeaseKeeper"
    )
    text = ast.get_source_segment(path.read_text(), keeper) or ""
    assert "daemon=True" in text and ".join(" in text
    for forbidden in ("credential", "secret", "bao", "openbao", "pop_key", "environ"):
        assert forbidden not in text.lower(), forbidden
    assert text.count('"lease/') + text.count("'lease/") <= 1 and "renew" in text
