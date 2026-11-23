"""Q4 (architect r4): `threading` is allowed ONLY in `lease.py`, and only for
`LeaseKeeper`. The thread is a daemon, touches no secret material, calls only
`lease/renew`, and is stopped and joined before `release`."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)


def imports_threading(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "threading" for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "threading"
        ):
            return True
    return False


def test_threading_is_imported_only_by_the_lease_module() -> None:
    offenders = [
        p.name
        for p in sorted(SRC.glob("*.py"))
        if p.name != "lease.py" and imports_threading(p)
    ]
    assert offenders == []


def test_the_lease_keeper_thread_is_a_daemon_joined_before_release() -> None:
    path = SRC / "lease.py"
    if not imports_threading(path):
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
