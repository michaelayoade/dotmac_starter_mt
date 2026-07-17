"""Routers must not issue direct DB queries (logic lives in service.py)."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISALLOWED = [
    re.compile(r"\bdb\.query\("),
    re.compile(r"\bdb\.execute\("),
    re.compile(r"\bselect\("),
    re.compile(r"\bdb\.get\("),
    re.compile(r"\bdb\.scalars\("),
    re.compile(r"\bdb\.scalar\("),
]


def _router_files() -> list[Path]:
    features = PROJECT_ROOT / "app" / "features"
    return sorted(
        p for p in features.rglob("*.py") if p.name in {"router.py", "web.py"}
    )


def test_routers_do_not_issue_direct_queries() -> None:
    violations: list[str] = []
    for path in _router_files():
        text = path.read_text(encoding="utf-8")
        for pattern in DISALLOWED:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{line} -> {pattern.pattern}"
                )
    assert not violations, "\n".join(violations)
