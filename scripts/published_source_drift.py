#!/usr/bin/env python3
"""Which released distributions ship source that is not their published source.

A distribution declares a version. If a release tag exists for that exact
version, a consumer installing it receives the bytes at that tag. This measures
whether the `src/` tree on HEAD is still those bytes.

For legacy Python distributions only `src/` is compared, deliberately.
`CHANGELOG.md`, `EXTRACTION.toml` and the
`pyproject.toml` version line drift on most released packages as governance
records are updated, and that is bookkeeping, not a lie to a consumer. What
matters is the IMPORTABLE code: `dotmac-ui` 0.1.0a7 was published, verified and
tagged, and then `map_frame` merged with no version bump, so the wheel a
consumer installed exposed only `EMPTY_STATE` while every gate stayed green.
That is a `src/` divergence, and comparing whole package directories would bury
it in 60-odd harmless metadata diffs.

Native inputs present at either the publication tag or HEAD enroll a
NativePackageSource.v1 identity over src/, native/, build.py and pyproject.toml
Git object IDs, modes and kinds with explicit absence markers. This covers
Rust, C, vendored inputs, dependency locks, build hooks and packaging selection
without hashing unrelated governance documents. Removing native inputs cannot
unenroll a tag.
Unresolved revisions or missing required native paths refuse the comparison.
Native fingerprints are separate fields; legacy tree IDs retain their meaning.

Comparison is by git TREE OBJECT hash: one comparison covering every path, every
byte, modes, additions and deletions, which no file walk can accidentally miss.

Usage:
    python scripts/published_source_drift.py            # report
    python scripts/published_source_drift.py --write-baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import TypedDict

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


NATIVE_SOURCE_SCHEMA = "NativePackageSource.v1"
_NATIVE_COMPONENTS = ("src", "native", "build.py", "pyproject.toml")
_ABSENT = "ABSENT"


class _GitObject(TypedDict):
    mode: str
    kind: str
    oid: str


_SourceObjects = dict[str, _GitObject]


def _source_objects(ref: str, package: str) -> _SourceObjects:
    """Read labeled Git objects, distinguishing absence from unresolved refs."""
    paths = [f"{package}/{component}" for component in _NATIVE_COMPONENTS]
    result = subprocess.run(
        ["git", "ls-tree", ref, "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"cannot resolve native source revision {ref!r}")
    objects: _SourceObjects = {}
    for line in result.stdout.splitlines():
        metadata, path = line.split("\t", 1)
        mode, kind, identity = metadata.split()
        objects[path.removeprefix(f"{package}/")] = {
            "mode": mode,
            "kind": kind,
            "oid": identity,
        }
    return objects


def _native_enrolled(objects: _SourceObjects, ref: str, package: str) -> bool:
    if "native" in objects or "build.py" in objects:
        return True
    if "pyproject.toml" not in objects:
        return False
    result = subprocess.run(
        ["git", "cat-file", "blob", objects["pyproject.toml"]["oid"]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(
            f"cannot resolve native source configuration {package} at {ref}"
        )
    try:
        metadata = tomllib.loads(result.stdout)
    except tomllib.TOMLDecodeError as failure:
        raise SystemExit(
            f"cannot resolve native source configuration {package} at {ref}"
        ) from failure
    return "native" in metadata.get("tool", {}).get("dotmac", {})


def native_fingerprint(ref: str, package: str) -> str:
    return _native_fingerprint(_source_objects(ref, package), ref, package)


def _native_fingerprint(objects: _SourceObjects, ref: str, package: str) -> str:
    for required in ("src", "pyproject.toml"):
        if required not in objects:
            raise SystemExit(
                f"required native source input {package}/{required} is missing at {ref}"
            )
    payload = {
        "schema": NATIVE_SOURCE_SCHEMA,
        "objects": [
            [
                label + "/" if label in {"src", "native"} else label,
                objects.get(label, _ABSENT),
            ]
            for label in _NATIVE_COMPONENTS
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def drifted() -> list[dict[str, str]]:
    """Compare Python source and, when enrolled at either ref, native inputs."""
    out: list[dict[str, str]] = []
    for name, version, src in released_distributions():
        tag = f"{name}-v{version}"
        package = src.removesuffix("/src")
        published_objects = _source_objects(tag, package)
        current_objects = _source_objects("HEAD", package)
        native = _native_enrolled(published_objects, tag, package) or _native_enrolled(
            current_objects, "HEAD", package
        )
        published = (
            published_objects["src"]["oid"] if "src" in published_objects else ""
        )
        current = current_objects["src"]["oid"] if "src" in current_objects else ""
        extra = {}
        if native:
            published_fingerprint = _native_fingerprint(published_objects, tag, package)
            current_fingerprint = _native_fingerprint(current_objects, "HEAD", package)
            if published_fingerprint == current_fingerprint:
                continue
            extra = {
                "native_source_schema": NATIVE_SOURCE_SCHEMA,
                "published_native_fingerprint": published_fingerprint,
                "head_native_fingerprint": current_fingerprint,
            }
        elif not published or not current or published == current:
            continue
        out.append(
            {
                "distribution": name,
                "version": version,
                "tag": tag,
                "published_tree": published,
                "head_tree": current,
                **extra,
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
