#!/usr/bin/env python3
"""Regenerate the ADR-0018 three-leg retrofit backlog.

The backlog is the frozen inventory of this repository's OWN architecture
guards that do not yet carry all three legs ADR-0018's 2026-08-15 amendment
requires. It is two-directional: the gate fails when a population grows AND
when it shrinks, so a retrofit runs this and commits the lowered baseline in
the SAME change. That is the point — the reduction lands as a reviewable diff
naming the arm that was fixed, instead of a count that quietly went down.

Run via ``make guard-proof-baseline``. The measurement itself lives in
``tests/architecture/test_guard_proof_ratchet.py``; this script only serialises
it, so the two can never disagree about what counts as a missing leg.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.architecture.test_guard_proof_ratchet import (  # noqa: E402
    BASELINE_PATH,
    POPULATIONS,
    guard_sources,
    scan,
    totals,
)

_COMMENT = (
    "Frozen ADR-0018 three-leg retrofit backlog for this repository's own "
    "architecture guards. Two-directional ratchet: the gate fails if a "
    "population rises or falls without this file being regenerated in the "
    "same change. Retire an entry by giving the named arm the leg it lacks, "
    "then run `make guard-proof-baseline`. Do not hand-edit."
)

_LEGEND = {
    "fixture_only_arms": (
        "<guard>::<detector> -> the fixture proofs that exist. Missing LEG 3: "
        "nothing drives this detector over mutated real corpus bytes."
    ),
    "guards_without_a_firing_proof": (
        "<guard> -> its discovery handles. Missing ALL THREE legs: the guard "
        "scans the real tree and nothing anywhere in it plants a violation."
    ),
    "vacuous_discovery_scopes": (
        "<guard>::<scope>::<call> -> unasserted real-tree discovery. An empty "
        "match set is a green run: add one non-emptiness assertion."
    ),
}


def main() -> int:
    live = scan(guard_sources())
    previous: dict[str, int] = {}
    existed = BASELINE_PATH.is_file()
    if existed:
        stored = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        previous = dict(stored.get("totals", {}))

    payload = {
        "_comment": _COMMENT,
        "_legend": _LEGEND,
        "totals": totals(live),
        "populations": {population: live[population] for population in POPULATIONS},
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    raised = []
    for population in POPULATIONS:
        now = len(live[population])
        was = previous.get(population)
        if not existed or was is None:
            print(f"{population}: {now} (initial baseline)")
            continue
        delta = now - was
        movement = "unchanged" if delta == 0 else f"{was} -> {now}"
        print(f"{population}: {now} ({movement})")
        if delta > 0:
            raised.append(population)

    if raised:
        print(
            "WARNING: this run RAISED " + ", ".join(raised) + ". A new arm that "
            "lands without its three legs is precisely what the ratchet exists "
            "to prevent — add the leg rather than the baseline entry.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
