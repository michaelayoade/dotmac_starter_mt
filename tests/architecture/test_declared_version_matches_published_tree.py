"""A tree claiming a published version must SHIP that version's source.

`test_module_version_sync.py` proves a distribution's three version surfaces
agree with each other. `test_declared_publication.py` proves a declared version
is either installable or recorded with a reason. Neither compares the SOURCE to
the artifact that version names, so both stayed green while a package's
importable code drifted away from the bytes a consumer installing that exact
version receives.

That gap is not hypothetical. `dotmac-ui` 0.1.0a7 was published, verified and
tagged; `map_frame` then merged to `main` with no version bump, so the wheel a
consumer installed exposed only `EMPTY_STATE` while every gate passed. An ERP
install-back is what finally caught it. The repair was a NEW version -- never
editing a published one -- but nothing prevented the next occurrence.

## Why `src/` and not the package directory

Comparing whole package directories reports 63 of 79 released distributions as
drifted, and nearly all of it is `CHANGELOG.md`, `EXTRACTION.toml` and the
`pyproject.toml` version line: governance records updated after a release. That
is bookkeeping, not a lie to a consumer, and burying four real defects under
sixty harmless ones is how a gate gets ignored. Comparing `src/` isolates the
property that actually bit: the IMPORTABLE code a consumer receives.

Measured on `src/`, 75 of 79 released distributions are byte-identical to their
tag and four are not. Those four are frozen in
`published_source_drift_baseline.json` as debt.

## The ratchet runs in both directions

A new drifted distribution fails. A repaired one ALSO fails, until the baseline
is regenerated -- otherwise the count silently falls and the file stops
describing reality, which is how a "known debt" list becomes a place defects go
to be forgotten.

Byte-identity is checked by comparing git TREE OBJECT hashes, not a file walk: a
tree hash covers every path, every byte, modes, additions and deletions, in one
comparison that cannot be fooled by a file the walk forgot to visit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).parent / "published_source_drift_baseline.json"

_SCRIPT = REPO_ROOT / "scripts/published_source_drift.py"


def _load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_published_source_drift", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_drift = _load_script()


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


def _require_tags() -> None:
    """A tagless checkout would make every result vacuous, so refuse instead.

    Without this the whole file passes by finding nothing to compare, which is
    indistinguishable from finding nothing wrong.
    """

    if not _drift.tags():
        pytest.fail(
            "this checkout has no git tags, so no declared version can be "
            "compared against its published source. Fetch tags rather than "
            "letting this check pass vacuously"
        )


def test_no_new_distribution_ships_source_its_published_version_does_not():
    """The invariant. A new row here is one version naming two sets of bytes."""

    _require_tags()
    known = {row["distribution"] for row in _baseline()["drifted"]}
    current = {row["distribution"] for row in _drift.drifted()}
    new = sorted(current - known)
    assert not new, (
        "these released distributions now ship src/ that differs from the tag of "
        f"the version they declare: {new}. One version cannot name two different "
        "sets of importable bytes -- allocate a NEW version, or move the declared "
        "version to a PEP 440 local development marker. Never edit a published "
        "version's contents."
    )


def test_repaired_drift_is_removed_from_the_baseline():
    """The other direction: frozen debt may only fall by regeneration.

    A silently shrinking baseline stops describing reality, and a list nobody
    maintains is where defects go to be forgotten.
    """

    _require_tags()
    known = {row["distribution"] for row in _baseline()["drifted"]}
    current = {row["distribution"] for row in _drift.drifted()}
    repaired = sorted(known - current)
    assert not repaired, (
        f"{repaired} no longer drift from their published source. Regenerate the "
        "baseline in this same change: make published-source-drift-baseline"
    )


def test_the_recorded_totals_still_describe_this_tree():
    _require_tags()
    baseline = _baseline()
    assert baseline["drifted_total"] == len(baseline["drifted"])
    assert len(_drift.released_distributions()) == baseline["released_total"], (
        "the number of released distributions changed; regenerate the baseline "
        "so its counts still describe this tree"
    )


def test_the_comparison_actually_reaches_released_distributions():
    """Non-vacuity: the invariant must be exercised by something real.

    With no matched distribution every assertion above passes over an empty set,
    which is indistinguishable from a broken matcher.
    """

    _require_tags()
    released = _drift.released_distributions()
    assert len(released) > 50, (
        f"only {len(released)} distributions matched a release tag; the tag "
        "naming convention or the matcher is probably broken"
    )


def test_the_drift_detector_would_actually_fire():
    """Sensitivity: the tree-hash comparison must distinguish two known trees.

    A comparison that always reported equality would pass every assertion above
    in silence. Compare two revisions KNOWN to differ and require inequality.
    """

    changed = [
        line
        for line in _drift.git("diff", "--name-only", "HEAD~1", "HEAD").splitlines()
        if "/" in line
    ]
    if not changed:
        pytest.skip("HEAD changes no nested path to compare")
    directory = changed[0].rsplit("/", 1)[0]
    head = _drift.git("rev-parse", f"HEAD:{directory}")
    parent = _drift.git("rev-parse", f"HEAD~1:{directory}")
    if not head or not parent:
        pytest.skip("that path does not exist in both revisions")
    assert head != parent, (
        "the tree-hash comparison reported two demonstrably different trees as "
        "identical; nothing else in this file can be trusted"
    )


def test_the_kernel_no_longer_claims_a_version_it_is_not():
    """The marker half of the contract, asserted by name.

    `dotmac-kernel` 0.1.0a99 is published and tagged, and main has since
    diverged. The declared version therefore carries a PEP 440 local segment,
    which no index accepts: it allocates nothing, cannot be published, and needs
    no release authorization. Michael authorizes release versions; nothing
    authorizes main to claim a version it is not.

    If someone removes the marker without allocating a new version, this states
    why at the point the change is made.
    """

    import tomllib

    data = tomllib.loads(
        (REPO_ROOT / "packages/dotmac-kernel/pyproject.toml").read_text()
    )
    project = data.get("project") or data.get("tool", {}).get("poetry", {})
    version = project["version"]
    if f"dotmac-kernel-v{version}" in _drift.tags():
        published = _drift.git(
            "rev-parse", f"dotmac-kernel-v{version}:packages/dotmac-kernel/src"
        )
        current = _drift.git("rev-parse", "HEAD:packages/dotmac-kernel/src")
        assert published == current, (
            f"dotmac-kernel declares {version}, a released version, but its src/ "
            "has diverged from that release"
        )
    else:
        assert "+" in version, (
            f"dotmac-kernel declares {version}, which has no release tag and no "
            "local development segment. A declared version with neither is a "
            "claim nobody can check: allocate and release it, or mark it as "
            "development with a PEP 440 local segment"
        )
