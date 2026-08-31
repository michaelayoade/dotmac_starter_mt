#!/usr/bin/env python3
"""Regenerate the authored-facet-prefix debt baseline.

A composed template must not decide which facet it is mounted under.  The
declaration-driven forms — ``surface.landing_path``, ``surface.logout_path``,
``surface_url()`` — carry that answer from the assembly, and a literal ``/admin``
inside a shell that a second facet also composes renders a cross-facet link.

The legacy contract-v1 screens still author their prefixes, so the gate freezes
that debt exactly rather than pretending it is absent.  It is two-directional
(ADR-0018 § 3): it fails when debt rises AND when it falls, so a slice that
genuinely retires an authored prefix runs this and commits the lowered baseline
in the SAME change, where the reduction is reviewable as a diff.

Run via ``make facet-nav-baseline``.  The detector itself lives in
``tests/architecture/test_facet_template_conventions.py``; this script only
serialises it, so the two can never disagree about what counts as debt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.architecture.test_facet_template_conventions import (  # noqa: E402
    BASELINE_PATH,
    facet_prefixes,
    scan_repository,
    total_of,
)

_COMMENT = (
    "Frozen authored-facet-prefix debt for composed templates. A URL-bearing "
    "attribute must not author a declared facet's own prefix; use "
    "surface.landing_path / surface.logout_path / surface_url(). "
    "Two-directional ratchet: the gate fails if this rises or falls without "
    "being regenerated in the same change. Run `make facet-nav-baseline`. "
    "Do not hand-edit."
)


def main() -> int:
    inventory = scan_repository()
    total = total_of(inventory)

    previous: int | None = None
    if BASELINE_PATH.is_file():
        previous = int(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["total"])

    payload = {
        "_comment": _COMMENT,
        "declared_facet_prefixes": list(facet_prefixes()),
        "total": total,
        "files": dict(sorted(inventory.items())),
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    if previous is None:
        print(f"wrote baseline: {total} authored prefixes in {len(inventory)} files")
    else:
        print(
            f"baseline {previous} -> {total} "
            f"({len(inventory)} files); commit this diff in the same change"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
