#!/usr/bin/env python3
"""Which released distributions ship source that is not their published source.

A distribution declares a version. If a release tag exists for that exact
version, a consumer installing it receives the bytes at that tag. This measures
whether the `src/` tree on HEAD is still those bytes.

Only `src/` is compared, deliberately. `CHANGELOG.md`, `EXTRACTION.toml` and the
`pyproject.toml` version line drift on most released packages as governance
records are updated, and that is bookkeeping, not a lie to a consumer. What
matters is the IMPORTABLE code: `dotmac-ui` 0.1.0a7 was published, verified and
tagged, and then `map_frame` merged with no version bump, so the wheel a
consumer installed exposed only `EMPTY_STATE` while every gate stayed green.
That is a `src/` divergence, and comparing whole package directories would bury
it in 60-odd harmless metadata diffs.

Comparison is by git TREE OBJECT hash: one comparison covering every path, every
byte, modes, additions and deletions, which no file walk can accidentally miss.

Usage:
    python scripts/published_source_drift.py            # report
    python scripts/published_source_drift.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "tests/architecture/published_source_drift_baseline.json"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def tags() -> frozenset[str]:
    return frozenset(t for t in git("tag", "--list").splitlines() if t)


def released_distributions() -> list[tuple[str, str, str]]:
    """(distribution, version, `src` path) for every package with a release tag."""

    known = tags()
    found: list[tuple[str, str, str]] = []
    for pyproject in sorted(REPO_ROOT.glob("packages/*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project") or data.get("tool", {}).get("poetry", {})
        name, version = project.get("name"), project.get("version")
        if not (name and version):
            continue
        if f"{name}-v{version}" not in known:
            continue
        rel = pyproject.parent.relative_to(REPO_ROOT).as_posix()
        found.append((name, version, f"{rel}/src"))
    return found


def drifted() -> list[dict[str, str]]:
    """Every released distribution whose `src/` differs from its tag."""

    out: list[dict[str, str]] = []
    for name, version, src in released_distributions():
        tag = f"{name}-v{version}"
        published = git("rev-parse", f"{tag}:{src}")
        current = git("rev-parse", f"HEAD:{src}")
        if not published or not current or published == current:
            continue
        out.append(
            {
                "distribution": name,
                "version": version,
                "tag": tag,
                "published_tree": published,
                "head_tree": current,
            }
        )
    return sorted(out, key=lambda row: row["distribution"])


def baseline_document() -> dict:
    """Render the complete current census while preserving dispositions.

    This is deliberately a pure return value.  The post-release recorder needs
    to validate every output before writing any of them, so a helper that writes
    as a side effect would make an atomic record impossible.
    """

    rows = drifted()
    total = len(released_distributions())

    # Carry forward the DISPOSITION a human wrote for a row that is still
    # drifting. Regeneration must not silently discard the characterisation --
    # a debt list whose reasons evaporate the first time somebody runs the make
    # target is a list that decays into bare names nobody can act on. A row that
    # stopped drifting simply disappears, reason and all.
    if BASELINE_PATH.exists():
        previous = json.loads(BASELINE_PATH.read_text())
        carried = {
            entry["distribution"]: entry
            for entry in previous.get("drifted", [])
            if "disposition" in entry
        }
        for row in rows:
            prior = carried.get(row["distribution"])
            if prior is None:
                continue
            row["disposition"] = prior["disposition"]
            row["reason"] = prior.get("reason", "")

    return {
        "$comment": [
            "Released distributions whose src/ tree no longer matches",
            "the tag of the version they declare. Each row is one",
            "version naming two different sets of importable bytes.",
            "",
            "This is FROZEN DEBT, ratcheted in BOTH directions by",
            "tests/architecture/",
            "test_declared_version_matches_published_tree.py: a new",
            "drifted distribution fails, and a repaired one fails too",
            "until this file is regenerated, so the count cannot drift",
            "silently in either direction.",
            "",
            "Repair a row by allocating a NEW version, or by moving the",
            "declared version to a PEP 440 local development marker --",
            "never by editing a published version's contents, which",
            "would make one version name two contracts.",
            "",
            "",
            "Every row carries a DISPOSITION and a retirement",
            "condition, because a debt list without one is a list",
            "nobody can ever close. A row is not evidence a",
            "re-release is owed: `accepted-debt` rows are",
            "docstring-only and clear at that distribution's next",
            "release, and `not-actionable-here` is repaired by",
            "removing a retired copy, never by publishing.",
            "",
            "A row also says NOTHING about a released MIGRATION.",
            "That is a different gate",
            "(tests/architecture/test_released_migrations.py) and a",
            "different failure: a migration edited after release",
            "changes what future installations build, while",
            "alembic_version records only that the revision ran.",
            "Do not read a row here as covering it.",
            "",
            "Dispositions are carried forward on regeneration; a row",
            "that stops drifting disappears, reason and all.",
            "Regenerate: make published-source-drift-baseline",
        ],
        "released_total": total,
        "drifted_total": len(rows),
        "drifted": rows,
    }


def render_baseline() -> str:
    """Canonical bytes used by both the manual and post-tag writers."""

    return json.dumps(baseline_document(), indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    if not tags():
        print("REFUSED: this checkout has no tags; every result would be vacuous")
        return 2

    document = baseline_document()
    rows = document["drifted"]
    total = document["released_total"]
    if args.write_baseline:
        BASELINE_PATH.write_text(render_baseline())
        print(f"wrote {BASELINE_PATH.relative_to(REPO_ROOT)} ({len(rows)} drifted)")
        return 0

    print(f"released distributions with a matching tag: {total}")
    print(f"src/ identical to the published tag:        {total - len(rows)}")
    print(f"src/ DRIFTED from the published tag:        {len(rows)}")
    for row in rows:
        print(f"  {row['distribution']} {row['version']} ({row['tag']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
