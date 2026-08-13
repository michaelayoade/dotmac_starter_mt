#!/usr/bin/env python3
"""Regenerate the hardcoded-palette debt baseline.

The baseline is the ratchet's frozen inventory of literal Tailwind palette
utilities in starter-owned templates.  It is two-directional: the gate fails
when debt rises AND when it falls, so a slice that genuinely retires palette
usage runs this and commits the lowered baseline in the SAME change.  That is
the point — the reduction shows up as a reviewable diff instead of being
silently absorbed.

Run via ``make palette-baseline``.  The detector itself lives in
``tests/architecture/test_palette_ratchet.py``; this script only serialises it,
so the two can never disagree about what counts as debt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.architecture.test_palette_ratchet import (  # noqa: E402
    BASELINE_PATH,
    scan_repository,
    total_of,
)


def main() -> int:
    inventory = scan_repository()
    existed = BASELINE_PATH.is_file()
    previous = 0
    if existed:
        previous = int(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["total"])

    total = total_of(inventory)
    payload = {
        "_comment": (
            "Frozen hardcoded-palette debt for starter-owned templates. "
            "Two-directional ratchet: the gate fails if this rises or falls "
            "without being regenerated in the same change. Retire entries by "
            "authoring against var(--dmui-*), then run `make palette-baseline`. "
            "Do not hand-edit."
        ),
        "total": total,
        "files": inventory,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    delta = total - previous
    if not existed:
        summary = "initial baseline"
    elif delta == 0:
        summary = "unchanged"
    else:
        summary = f"{'RETIRED' if delta < 0 else 'ADDED'} {abs(delta)}"
    print(f"palette debt: {total} across {len(inventory)} files ({summary})")
    if existed and delta > 0:
        print(
            "WARNING: this run RAISED the baseline. Adding hardcoded palette "
            "utilities is what the ratchet exists to prevent — confirm this is "
            "deliberate before committing.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
