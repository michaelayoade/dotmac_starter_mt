"""Fixture § 7 evidence tables for tests, written into a tmp tree.

Nothing here is real evidence: IDs and digests are synthetic. The real table
lives in ``docs/inventories/lane3-admission-evidence.md`` and stays unfilled.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

EVIDENCE_PATH = "docs/inventories/lane3-admission-evidence.md"
REFERENCE = f"{EVIDENCE_PATH}#fixture-run-1"

REQUIREMENTS = (
    "Public controls",
    "Authorized scheduling",
    "PR exclusion",
    "Ref and workflow failure",
    "Credential failure",
    "No-grant refusal",
    "Isolation and disclosure",
    "Oracle reachability",
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


KINDS = {
    "Public controls": "readback",
    "Authorized scheduling": "run",
    "PR exclusion": "run",
    "Ref and workflow failure": "job",
    "Credential failure": "audit",
    "No-grant refusal": "audit",
    "Isolation and disclosure": "scan",
    "Oracle reachability": "run",
}


def coordinate(name: str, index: int) -> str:
    kind = KINDS[name]
    if kind in ("run", "job"):
        return f"{kind}:{9000001 + index}"
    return f"{kind}:fixture-record-{index}"


def rows() -> dict[str, tuple[str, str]]:
    return {
        name: (coordinate(name, i), digest(name)) for i, name in enumerate(REQUIREMENTS)
    }


def table(row_map: dict[str, tuple[str, str]], heading: str = "Fixture run 1") -> str:
    lines = [
        "# Fixture",
        "",
        f"## {heading}",
        "",
        "| Requirement | Evidence coordinate | Evidence sha256 |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {k} | `{c}` | `{d}` |" for k, (c, d) in row_map.items()]
    return "\n".join(lines) + "\n"


def write_evidence(root: pathlib.Path, text: str | None = None) -> None:
    path = root / EVIDENCE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(table(rows()) if text is None else text, encoding="utf-8")


def write_topology(
    root: pathlib.Path, document: Any, name: str = "lane3-execution.json"
) -> str:
    """Write ``document`` at ``<root>/.github/<name>`` with a valid fixture table."""
    write_evidence(root)
    path = root / ".github" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)
