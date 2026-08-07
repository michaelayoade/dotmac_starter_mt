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


# Installed MODULE packages are held to the same rule as the assembly's own
# features: an adapter never queries. A module that escaped this check would be
# held to a WEAKER standard than the assembly installing it, which is backwards
# — it is the less-reviewed code.
MODULE_PACKAGE_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "packages/dotmac-template-studio/src/dotmac_template_studio",
)


def _router_files() -> list[Path]:
    roots = (PROJECT_ROOT / "app" / "features", *MODULE_PACKAGE_ROOTS)
    return sorted(
        p
        for root in roots
        for p in root.rglob("*.py")
        if p.name in {"router.py", "web.py"}
    )


def test_router_scan_is_not_vacuous() -> None:
    """Assert on the set walked, not just on the violations found."""
    found = {p.parent.name for p in _router_files()}
    assert "dotmac_template_studio" in found, (
        "the installed module's adapters are not being scanned — "
        f"walked: {sorted(found)}"
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
