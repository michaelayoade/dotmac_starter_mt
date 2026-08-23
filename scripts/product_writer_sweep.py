#!/usr/bin/env python3
"""Which dossier/product pairs still make their writer claim in PROSE.

## The defect

Governance cites Starter dossiers across the repository boundary to justify a
roster disposition. Two such rationales were contradicted by the dossiers they
described (#354), because the claim being cited lived in `local_copy_retirement`
prose — and a sentence is not a claim a checker can compare.

`[[product_writers]]` (#354) is the typed channel that fixes it. But the
validator deliberately allows the block to be ABSENT, because absence has to
stay distinguishable from a claim of absence: a consumer that cannot find the
entry it needs must fail as UNKNOWN, and treating silence as a clean bill of
health is the original defect.

That is migration support. It is not a ratchet, and on its own it means the
prose channel stays live indefinitely for everything that has not migrated.

## What this measures

For every dossier, every product in `source_repositories` — excluding
`dotmac_starter_mt`, which is this repository rather than a product whose
writers could need retiring — that has NO typed `[[product_writers]]` row.

Each pair is one claim that can only be made in prose today. The baseline
freezes the set at 303 across 86 dossiers. It is TWO-DIRECTIONAL: the set may
not grow, and it may not shrink without the baseline being regenerated in the
same change. A silently shrinking baseline is how a ratchet stops describing
anything (ADR-0018), and a silently growing one is how migration debt becomes
permanent.

A dossier absent from the baseline must be COMPLETE. That is what stops 86
becoming 87: a new package has no excused pairs, so any untyped product it
declares fails immediately.

## What it does NOT do

It never infers a writer state from prose. Retiring a pair means somebody reads
the source product and writes down what they found, with a revision and
evidence paths; a scanner guessing `no_writer` from a sentence would
manufacture exactly the false confidence this exists to remove.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = REPO_ROOT / "packages"
BASELINE = REPO_ROOT / "docs" / "inventories" / "product-writer-baseline.json"

#: Not a product whose writers could need retiring — it is this repository.
SELF: Final = "dotmac_starter_mt"

COMMENT: Final = [
    "Dossier/product pairs whose writer claim is still PROSE ONLY.",
    "",
    "`[[product_writers]]` (#354) is the typed channel. Its validator allows the",
    "block to be absent on purpose, so that silence stays distinguishable from a",
    "claim of absence — which makes it migration support, not a ratchet. This",
    "file is the ratchet.",
    "",
    "Two-directional. The set may not GROW: a new untyped claim is new debt in a",
    "channel that has already produced one cross-repository contradiction. It may",
    "not SHRINK without being regenerated in the same change: a baseline that",
    "quietly tracks reality stops describing anything (ADR-0018).",
    "",
    "A dossier ABSENT from this file must be complete. That is what stops the",
    "count from growing as packages are added — a new package has no excused",
    "pairs at all.",
    "",
    "Retire a pair by READING the source product and writing what you found: a",
    "typed writer_state, an immutable revision, and evidence paths. Never by",
    "inferring a state from the prose already there, which would manufacture the",
    "false confidence this exists to remove.",
    "",
    "Regenerate: make product-writer-baseline",
]


def _dossiers() -> list[Path]:
    return sorted(PACKAGES_DIR.glob("*/EXTRACTION.toml"))


def untyped_pairs() -> dict[str, list[str]]:
    """dossier -> the declared products carrying no typed writer row."""
    result: dict[str, list[str]] = {}
    for path in _dossiers():
        dossier = tomllib.loads(path.read_text(encoding="utf-8"))
        declared = dossier.get("source_repositories")
        if not isinstance(declared, list):
            continue
        entries = dossier.get("product_writers") or []
        typed = {
            entry.get("product")
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("product"), str)
        }
        missing = sorted(
            product
            for product in declared
            if isinstance(product, str) and product != SELF and product not in typed
        )
        if missing:
            result[path.parent.name] = missing
    return result


def _baseline() -> dict[str, list[str]]:
    if not BASELINE.is_file():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))["prose_only"]


def compare(live: dict[str, list[str]], baseline: dict[str, list[str]]) -> list[str]:
    """Both directions, named separately — they mean different things."""
    problems: list[str] = []

    live_pairs = {(d, p) for d, products in live.items() for p in products}
    base_pairs = {(d, p) for d, products in baseline.items() for p in products}

    for dossier, product in sorted(live_pairs - base_pairs):
        if dossier in baseline:
            problems.append(
                f"{dossier}: {product} is a NEW prose-only claim — this dossier "
                "is mid-migration, so add its typed [[product_writers]] row "
                "rather than widening the exemption"
            )
        else:
            problems.append(
                f"{dossier}: {product} has no typed [[product_writers]] row, and "
                f"{dossier} is not in the baseline — a dossier added after the "
                "freeze must type every product it declares, immediately"
            )

    retired = sorted(base_pairs - live_pairs)
    if retired:
        listed = ", ".join(f"{d}/{p}" for d, p in retired[:5])
        more = f" (+{len(retired) - 5} more)" if len(retired) > 5 else ""
        problems.append(
            f"{len(retired)} pair(s) are typed but still excused here: {listed}"
            f"{more} — regenerate with `make product-writer-baseline` in the SAME "
            "change, or the ratchet stops describing anything"
        )
    return problems


def render(live: dict[str, list[str]], baseline: dict[str, list[str]]) -> str:
    live_count = sum(len(v) for v in live.values())
    base_count = sum(len(v) for v in baseline.values())
    total = len(_dossiers())
    lines = [
        f"dossiers: {total}",
        f"prose-only pairs: {live_count} across {len(live)} dossier(s)",
        f"baseline:         {base_count} across {len(baseline)} dossier(s)",
        f"fully typed:      {total - len(live)} dossier(s)",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    live = untyped_pairs()

    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(
                {"$comment": COMMENT, "prose_only": live},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(render(live, live))
        print(f"\nwrote {BASELINE.relative_to(REPO_ROOT)}")
        return 0

    baseline = _baseline()
    print(render(live, baseline))

    problems = compare(live, baseline)
    if args.check:
        if problems:
            print("\nproduct-writer ratchet FAIL:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("\nproduct-writer ratchet PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
