#!/usr/bin/env python3
"""Which released migration lineages the released-migrations gate does not check.

`tests/architecture/test_released_migrations.py` freezes the bytes of every
migration that shipped in a published tag. It is a strong guard over a
HAND-MAINTAINED list of what to check, which makes its blind spot the list
itself: a distribution absent from `DISTRIBUTIONS` is not reported as
unprotected, it is simply never looked at.

That is how `dotmac-numbering` 0.1.0a2 had a released migration edited in place
with nothing failing. The gate did not miss the edit; it was never pointed at
the file.

A distribution needs covering when BOTH are true:

* it has a migration lineage (`src/*/migrations/versions/*.py`), and
* it has at least one release tag, so those bytes are inside a wheel on the
  registry and have run in a database this repository cannot inspect.

Usage:
    python scripts/released_lineage_coverage.py            # report
    python scripts/released_lineage_coverage.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "tests/architecture/test_released_migrations.py"
BASELINE_PATH = REPO_ROOT / "tests/architecture/released_lineage_coverage_baseline.json"


def tags() -> frozenset[str]:
    result = subprocess.run(
        ["git", "tag", "--list"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return frozenset(t for t in result.stdout.split() if t)


def covered() -> frozenset[str]:
    """The distributions the gate's `DISTRIBUTIONS` map actually names."""

    source = GATE.read_text()
    start = source.index("DISTRIBUTIONS: dict[str, Path] = {")
    end = source.index("LINEAGE_GLOBS", start)
    return frozenset(re.findall(r'"(dotmac-[a-z0-9-]+)"\s*:', source[start:end]))


def released_lineages() -> list[str]:
    """Distributions that have a migration lineage AND at least one release tag."""

    known = tags()
    found: list[str] = []
    for pyproject in sorted(REPO_ROOT.glob("packages/*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project") or data.get("tool", {}).get("poetry", {})
        name = project.get("name")
        if not name:
            continue
        versions = [
            path
            for path in pyproject.parent.glob("src/*/migrations/versions/*.py")
            if path.name != "__init__.py"
        ]
        if not versions:
            continue
        if any(tag.startswith(f"{name}-v") for tag in known):
            found.append(name)
    return sorted(found)


def uncovered() -> list[str]:
    return sorted(set(released_lineages()) - covered())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    if not tags():
        print("REFUSED: this checkout has no tags; every result would be vacuous")
        return 2

    missing = uncovered()
    if args.write_baseline:
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "$comment": [
                        "Released migration lineages the released-migrations gate",
                        "does not check. Each is a lineage whose bytes are inside a",
                        "published wheel and have run in databases this repository",
                        "cannot inspect, with nothing freezing them.",
                        "",
                        "FROZEN DEBT, ratcheted in BOTH directions: a newly",
                        "uncovered released lineage fails, and a newly covered one",
                        "fails too until this file is regenerated -- so the hole",
                        "cannot grow quietly, and closing it is a visible edit.",
                        "",
                        "Shrink a row by ENROLLING the distribution in",
                        "tests/architecture/test_released_migrations.py, never by",
                        "deleting the row.",
                        "",
                        "Regenerate: make released-lineage-coverage-baseline",
                    ],
                    "released_lineages_total": len(released_lineages()),
                    "covered_total": len(covered() & set(released_lineages())),
                    "uncovered_total": len(missing),
                    "uncovered": missing,
                },
                indent=2,
            )
            + "\n"
        )
        print(
            f"wrote {BASELINE_PATH.relative_to(REPO_ROOT)} ({len(missing)} uncovered)"
        )
        return 0

    print(f"released lineages: {len(released_lineages())}")
    print(f"covered by the gate: {len(covered() & set(released_lineages()))}")
    print(f"UNCOVERED: {len(missing)}")
    for name in missing:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
