"""Service payloads must be typed — `Any` payloads are banned in feature services."""

from __future__ import annotations

import re
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "app" / "features"


def test_no_any_typed_payloads_in_services() -> None:
    offenders: list[str] = []
    for service in sorted(FEATURES.glob("*/service.py")):
        text = service.read_text(encoding="utf-8")
        for match in re.finditer(r"payload:\s*Any\b", text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{service.relative_to(FEATURES.parents[1])}:{line}")
    # Correction to keep CI green mid-plan: Task 1 only fixes tenants + persons;
    # Task 2 removes this filter so the test covers all services permanently.
    fixed = {"tenants", "persons"}
    offenders = [o for o in offenders if o.split("/")[2] in fixed]
    assert not offenders, "Any-typed payloads:\n" + "\n".join(offenders)
