#!/usr/bin/env python3
"""Require every inventoried product writer claim to be typed and pinned.

The migration ratchet reached zero on 2026-08-23. There is therefore no debt
baseline and no legitimate prose-only state left to grandfather. Every product
named by a dossier's ``source_repositories`` must have exactly one
``[[product_writers]]`` row, except this repository itself, and that row's
immutable revision must equal the product's one effective audit pin:
``revalidation_revisions`` when the dossier has re-audited that product,
otherwise ``source_revisions``.

The dossier schema still treats an absent row as UNKNOWN rather than silently
claiming ``no_writer``. This repository-level check makes UNKNOWN inadmissible
for checked-in dossiers now that the migration is complete.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Any, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = REPO_ROOT / "packages"
SELF: Final = "dotmac_starter_mt"


def dossiers() -> list[Path]:
    return sorted(PACKAGES_DIR.glob("*/EXTRACTION.toml"))


def dossier_problems(name: str, dossier: dict[str, Any]) -> list[str]:
    """Return completeness and exact-pin findings for one parsed dossier."""
    problems: list[str] = []
    declared = dossier.get("source_repositories")
    if not isinstance(declared, list):
        return problems

    rows = dossier.get("product_writers") or []
    counts: dict[str, int] = {}
    by_product: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("product"), str):
            continue
        product = row["product"]
        counts[product] = counts.get(product, 0) + 1
        by_product[product] = row

    expected_products = {
        product for product in declared if isinstance(product, str) and product != SELF
    }
    for product in sorted(expected_products):
        count = counts.get(product, 0)
        if count == 0:
            problems.append(
                f"{name}: {product} has no typed [[product_writers]] row; "
                "prose-only writer claims are no longer admissible"
            )
        elif count > 1:
            problems.append(
                f"{name}: {product} has {count} product_writers rows; exactly "
                "one claim is required"
            )

    for product in sorted(set(counts) - expected_products):
        problems.append(
            f"{name}: {product} has a typed [[product_writers]] row but is not "
            "a declared external source product"
        )

    pin_sets: dict[str, dict[str, list[str]]] = {}
    for field in ("source_revisions", "revalidation_revisions"):
        pins: dict[str, list[str]] = {}
        for item in dossier.get(field) or []:
            if not isinstance(item, str) or ":" not in item:
                continue
            product, revision = item.split(":", 1)
            pins.setdefault(product, []).append(revision)
        pin_sets[field] = pins

    for product, row in by_product.items():
        revision = row.get("revision")
        product_pins = pin_sets["revalidation_revisions"].get(
            product, pin_sets["source_revisions"].get(product, [])
        )
        if product_pins != [revision]:
            problems.append(
                f"{name}: {product} writer revision {revision!r} must equal "
                f"its one effective audit pin; found {product_pins!r}"
            )
    return problems


def repository_problems() -> list[str]:
    problems: list[str] = []
    for path in dossiers():
        dossier = tomllib.loads(path.read_text(encoding="utf-8"))
        problems.extend(dossier_problems(path.parent.name, dossier))
    return problems


def render() -> str:
    typed = 0
    declared = 0
    for path in dossiers():
        dossier = tomllib.loads(path.read_text(encoding="utf-8"))
        declared += sum(
            isinstance(product, str) and product != SELF
            for product in dossier.get("source_repositories") or []
        )
        typed += sum(
            isinstance(row, dict) and isinstance(row.get("product"), str)
            for row in dossier.get("product_writers") or []
        )
    return (
        f"dossiers: {len(dossiers())}\n"
        f"declared product claims: {declared}\n"
        f"typed rows: {typed}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    print(render())
    problems = repository_problems()
    if problems:
        print("\nproduct-writer completeness FAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1 if args.check else 0
    print("\nproduct-writer completeness PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
