"""A migration that shipped in a published tag is history, and history is bytes.

## The enforceable premise (ADR-0018)

`dotmac-integration` has been published four times and
`dotmac-entitlement-allocation` four. Every migration file present at any of
those eight tags is inside a wheel on the registry, and has therefore RUN,
unmodified, in at least one database this repository does not own and cannot
inspect.

Allocation is the sharper case, and the reason the guard grew a second
distribution rather than staying a one-module special. Its four releases hold
ONE migration with ONE digest: a2 exposed `versions_dir()`, a3 made the ORM
relationships class-bound, a4 moved a manifest declaration between plane slots.
Four tags and nothing to show for them in the versions directory reads, to the
next author, as "the migration is still ours to edit". It is not.

The integration a3 entry was added while #204 was open: a3 was tagged from `b14f66e`
after the branch was cut, which is exactly the event the map has to absorb
rather than be surprised by. The six files did not move — a3 is a Python fix —
so the entry repeats a2's digests, and `test_two_tags_agree_about_a_file_they_
both_shipped` is what proves that repetition is a fact rather than a paste.

Editing such a file does not migrate anything. It changes what a future
installation builds while every existing installation keeps whatever the old
bytes built — and `alembic_version` records only that the revision ran, never
which version of it. So the divergence is permanent, silent, and invisible to
every other gate here: the composed gate reads the CURRENT tree, the
live-catalog gate reads a database built from the CURRENT tree, and both agree
with each other while disagreeing with the field.

That premise is enforceable, which is what makes this a guard rather than a
convention. "Released" is not a judgement call: it is a tag on `origin`, and
every digest below is reproducible with

    git show <tag>:<path> | shasum -a 256

## Two halves, because either alone is defeatable

The **checked-in map** is what a reviewer reads: an edit to a released
migration shows up as a digest change in the diff, at the moment somebody could
still object. On its own it is defeatable in one commit — edit the file, update
the digest beside it, and the comparison agrees with itself.

So the map is also **cross-checked against the tags**: every recorded digest
must equal the SHA-256 of the blob git holds at that tag. Doctoring the map now
requires moving a tag on `origin`, which is a different and far more visible
act. Neither half is redundant; the first catches the honest mistake, the
second catches the map being brought into line with it.

The tag half follows the fail-closed oracle discipline #202 established for
`test_declared_publication.py`: a shallow or tagless checkout is a FAILURE, not
a skip, because "the oracle was unavailable" is never evidence that nothing is
wrong. It is runnable because that change gave the `unit` job `fetch-depth: 0`
— before it, this half would have been permanently skipped, which is why the
checked-in map is the primary and not merely a convenience.

## What this does NOT claim

It does not stop a released migration from being WRONG. A defect in shipped DDL
is repaired by a new revision that alters the result — the same discipline as
`ig_0007`, which verifies a prerequisite `ig_0001` should arguably have verified
and does not touch `ig_0001` to do it. This guard only insists the repair be
additive.

## Scope

Two distributions: `dotmac-integration` and `dotmac-entitlement-allocation`.
Each was added by the change that was tempted to edit its released bytes —
integration's by `ig_0007`, allocation's by `ea_0002` — and each entry's
digests were read out of the tags in that same change. That is the enrolment
rule, and it is the reason this is still not generalised to "every allowlisted
module": a distribution enters when somebody has actually verified its tags,
because a guard populated by guesswork is worse than an absent one.

Enrolment is therefore a data edit — a row in `DISTRIBUTIONS`, its tags in
`RELEASED_TAGS`, and its still-editable files in `UNRELEASED`. The
`test_every_migration_is_either_released_or_declared_unreleased` ratchet then
holds that distribution's whole versions directory, in both directions.

An unenrolled distribution is UNMONITORED here, not exempt (ADR-0018). The
difference is visible in `test_the_unmonitored_distributions_are_named`, which
lists exactly which allowlisted modules this file says nothing about.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The distributions this file monitors, and where each keeps its lineage.
#:
#: A distribution is here because somebody read its tags. Everything else is
#: unmonitored and named as such by
#: `test_the_unmonitored_distributions_are_named`.
DISTRIBUTIONS: dict[str, Path] = {
    "dotmac-integration": (
        REPO_ROOT
        / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
    ),
    "dotmac-entitlement-allocation": (
        REPO_ROOT
        / "packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation"
        / "migrations/versions"
    ),
}

#: The glob that enumerates one distribution's lineage on disk. Derived from
#: the module's migration prefix, so the ratchet cannot be defeated by a file
#: the pattern happens not to match.
LINEAGE_GLOBS: dict[str, str] = {
    "dotmac-integration": "ig_*.py",
    "dotmac-entitlement-allocation": "ea_*.py",
}

#: Kept for the many call sites that only need integration's directory.
VERSIONS = DISTRIBUTIONS["dotmac-integration"]

#: `tag -> (distribution, tagged commit, {filename: sha256 at that tag})`.
#:
#: Every one is an annotated tag created by `release-module.yml` and present on
#: `origin`; the commit is recorded so a reviewer can locate the release without
#: resolving the tag object.
RELEASED_TAGS: dict[str, tuple[str, str, dict[str, str]]] = {
    "dotmac-integration-v0.1.0a1": (
        "dotmac-integration",
        "1b1d62b",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a2": (
        "dotmac-integration",
        "aaa3b54",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
        },
    ),
    # a3 is a Python-only fix (`ModeContractError` sanitisation, #201) shipped
    # with the same six migrations. Recorded anyway: "the lineage did not
    # change" is a claim, and an entry per tag is what checks it.
    "dotmac-integration-v0.1.0a3": (
        "dotmac-integration",
        "b14f66e",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
        },
    ),
    # a4 adds the DDL-free prerequisite verification revision. Its bytes are
    # now published history and may not remain in the editable set below.
    "dotmac-integration-v0.1.0a4": (
        "dotmac-integration",
        "306a40e",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
        },
    ),
    # ── dotmac-entitlement-allocation ───────────────────────────────────────
    #
    # Four tags, one migration, one digest. `ea_0001` has not moved a byte
    # since `0.1.0a1`: a2 exposed `versions_dir()`, a3 made the ORM
    # relationships class-bound, and a4 moved the tables from the `tables=`
    # slot to `platform_tables=` — all three are Python-only, and the fourth
    # changed a manifest declaration rather than any DDL.
    #
    # That is exactly the history that makes an in-place edit tempting: four
    # releases with nothing to show for them in this directory reads as "the
    # migration is still ours". It is not — those bytes have run in databases
    # this repository does not own, which is why `ea_0002` exists as its own
    # head instead of a `require_prerequisites` line appended to `ea_0001`.
    "dotmac-entitlement-allocation-v0.1.0a1": (
        "dotmac-entitlement-allocation",
        "847ce0b",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a2": (
        "dotmac-entitlement-allocation",
        "5ded880",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a3": (
        "dotmac-entitlement-allocation",
        "c371b0f",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a4": (
        "dotmac-entitlement-allocation",
        "67bdfb8",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
}

#: Migration files that exist in the tree and have NOT shipped in any tag, so
#: are still editable. This is the second direction of the ratchet: a new
#: migration must be named here, and a file may only move from here into
#: `RELEASED_TAGS` — never the other way, and never out of both.
#:
#: `ig_0008` and `ig_0009` move when integration's next version is tagged, and
#: `ea_0002` plus
#: `ea_0003` when allocation's `0.1.0a5` is. Each move is the same commit that
#: removes its
#: distribution's row from `docs/inventories/declared-publication-baseline
#: .json`. The release lane does not wait for an open branch, which is the
#: whole reason "released" is read from tags and not from a version number
#: somebody intended.
UNRELEASED: dict[str, frozenset[str]] = {
    "dotmac-integration": frozenset(
        {
            "ig_0008_platform_audit_log.py",
            "ig_0009_product_port_descriptors.py",
        }
    ),
    "dotmac-entitlement-allocation": frozenset(
        {"ea_0002_idempotency_ledger.py", "ea_0003_platform_audit_log.py"}
    ),
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drift(versions: Path, distribution: str = "dotmac-integration") -> list[str]:
    """Every released file of `distribution` in `versions` whose bytes moved.

    Takes the directory as an argument rather than reading the module constant,
    which is the whole reason the sensitivity proofs below can run: they point
    it at a deliberately damaged copy of the tree. `distribution` selects which
    tags apply, because one directory holds one lineage and the map now holds
    several.
    """
    problems: list[str] = []
    for tag, (owner, commit, files) in sorted(RELEASED_TAGS.items()):
        if owner != distribution:
            continue
        for name, expected in sorted(files.items()):
            path = versions / name
            if not path.is_file():
                problems.append(
                    f"{name} shipped in {tag} ({commit}) and is now MISSING — a "
                    "released revision cannot be withdrawn; adopters have "
                    "already run it"
                )
                continue
            actual = _digest(path)
            if actual != expected:
                problems.append(
                    f"{name} shipped in {tag} ({commit}) as sha256 {expected} "
                    f"and is now {actual}"
                )
    return problems


def _shipping_tags(name: str) -> list[str]:
    """Every tag whose entry records `name`.

    Derived, never counted by hand: `ig_0001` was in two tags when this file
    was written and three by the time it merged, and a hardcoded expectation
    turns a released migration appearing in one more release into a red suite
    for no reason.
    """
    return sorted(tag for tag, (_, _, files) in RELEASED_TAGS.items() if name in files)


# ── The guard ───────────────────────────────────────────────────────────────


def test_the_map_records_something_to_check() -> None:
    """A digest map that emptied would make every test below pass having
    compared nothing — the exact failure ADR-0018 calls a guard with no
    sensitivity."""
    assert RELEASED_TAGS, "no released tags recorded; this file proves nothing"
    for tag, (owner, _, files) in RELEASED_TAGS.items():
        assert files, f"{tag} records no files"
        assert owner in DISTRIBUTIONS, (
            f"{tag} names distribution {owner!r}, which has no versions "
            "directory in DISTRIBUTIONS — the map would then check nothing"
        )
    # Every monitored distribution must actually be monitored. A row in
    # DISTRIBUTIONS with no tag would look enrolled and check nothing.
    covered = {owner for owner, _, _ in RELEASED_TAGS.values()}
    assert covered == set(DISTRIBUTIONS), sorted(set(DISTRIBUTIONS) - covered)


@pytest.mark.parametrize("distribution", sorted(DISTRIBUTIONS))
def test_no_released_migration_has_been_edited(distribution: str) -> None:
    """The guard itself. Adopters ran these bytes; the tree must still hold
    them, or a repair was made in place instead of in a new revision."""
    problems = _drift(DISTRIBUTIONS[distribution], distribution)
    assert not problems, (
        f"{distribution}: released migrations were modified:\n" + "\n".join(problems)
    )


def test_two_tags_agree_about_a_file_they_both_shipped() -> None:
    """The map's own honesty check.

    `ig_0001` and `ig_0002` appear under three tags each and
    `ea_0001_allocations.py` under four. If the recorded digests ever
    disagreed, the map would be asserting that one file had two sets of released
    bytes — which is either a transcription error here or the very edit this
    file exists to forbid, already committed and then papered over by updating
    only one entry.
    """
    seen: dict[str, tuple[str, str]] = {}
    for tag, (_, _, files) in sorted(RELEASED_TAGS.items()):
        for name, digest in sorted(files.items()):
            if name in seen:
                first_tag, first_digest = seen[name]
                assert digest == first_digest, (
                    f"{name} is recorded as {first_digest} in {first_tag} and "
                    f"{digest} in {tag} — one file, two release histories"
                )
            else:
                seen[name] = (tag, digest)
    assert seen, "no shared files to compare"


@pytest.mark.parametrize("distribution", sorted(DISTRIBUTIONS))
def test_every_migration_is_either_released_or_declared_unreleased(
    distribution: str,
) -> None:
    """The two-directional ratchet.

    Without this, the guard is trivially defeated in both directions: a new
    migration simply never enters the map, and a released one can be dropped
    from it in the same commit that edits it. Neither is possible if the union
    must equal the directory exactly.
    """
    versions = DISTRIBUTIONS[distribution]
    on_disk = {path.name for path in versions.glob(LINEAGE_GLOBS[distribution])}
    assert on_disk, f"{distribution}: no migration matched its lineage glob"
    released = {
        name
        for owner, _, files in RELEASED_TAGS.values()
        if owner == distribution
        for name in files
    }
    unreleased = UNRELEASED[distribution]

    assert not (released & unreleased), (
        f"{distribution}: {sorted(released & unreleased)} claimed as both "
        "released and unreleased — a file is one or the other"
    )
    missing = sorted(on_disk - released - unreleased)
    assert not missing, (
        f"{distribution}: migration(s) {missing} are in neither map. A new "
        "migration goes in UNRELEASED; record its digest under its tag when it "
        "ships"
    )
    stale = sorted((released | unreleased) - on_disk)
    assert not stale, (
        f"{distribution}: {stale} are recorded but not on disk — see the "
        "deletion message in `_drift` before removing anything"
    )


def test_the_unmonitored_distributions_are_named() -> None:
    """ADR-0018: unmonitored and exempt are different labels, and the
    difference has to be visible.

    A releasable module absent from `DISTRIBUTIONS` is not excused from the
    rule that released bytes are history — nothing here checks it, which is a
    weaker statement and a different one. Naming the set turns "this file
    covers two modules" from a fact a reader has to reconstruct into one the
    suite reports, and makes enrolling the next module a visible diff rather
    than a silent absence.

    Deliberately NOT an assertion that the set is empty. It will not be for a
    long time, and a failing gate nobody can fix is one somebody deletes.
    """
    allowlist = set(
        json.loads(
            (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
        )["modules"]
    )
    monitored = set(DISTRIBUTIONS)
    assert monitored <= allowlist, (
        f"{sorted(monitored - allowlist)} is monitored here but is not in the "
        "release allowlist — an unreleasable distribution has no released bytes"
    )
    unmonitored = sorted(allowlist - monitored)
    print(
        "released-migration guard — UNMONITORED (not exempt): "
        + (", ".join(unmonitored) or "none")
    )
    assert monitored, "no distribution is monitored; this file proves nothing"


# ── Sensitivity proofs ──────────────────────────────────────────────────────
#
# A guard nobody has watched fail is a guard nobody has tested. Both proofs
# damage a COPY of the tree, so they establish that `_drift` reports the change
# without any chance of leaving the real one modified.


def test_the_guard_catches_a_one_byte_edit_to_a_released_migration(
    tmp_path: Path,
) -> None:
    """The proof that matters: a whitespace-only change is still a change.

    The realistic way a released migration gets edited is not a rewrite — it is
    a formatter run, a typo fix in a docstring, or a comment added while reading
    it. If the guard only noticed semantic edits it would miss every one of
    those, and they are equally capable of making the checked-in lineage
    disagree with what shipped.
    """
    copy = tmp_path / "versions"
    shutil.copytree(VERSIONS, copy)
    assert not _drift(copy), "the copy must start clean or this proves nothing"

    victim = copy / "ig_0001_connector_control_plane.py"
    victim.write_bytes(victim.read_bytes() + b"\n")

    shipped_in = _shipping_tags("ig_0001_connector_control_plane.py")
    assert len(shipped_in) >= 2, "this proof wants a file that shipped more than once"
    problems = _drift(copy)
    assert len(problems) == len(shipped_in), problems
    for problem, tag in zip(problems, shipped_in, strict=True):
        assert tag in problem, "each release that shipped the file must be named"
        assert "ig_0001_connector_control_plane.py" in problem
        assert (
            "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            in problem
        ), "the message must name the digest that shipped, not just 'differs'"


def test_the_guard_catches_a_deleted_released_migration(tmp_path: Path) -> None:
    """Deletion is the other half, and `_digest` alone would raise
    `FileNotFoundError` here — an error, not a finding, and one a reader would
    take for a broken test rather than a withdrawn revision."""
    copy = tmp_path / "versions"
    shutil.copytree(VERSIONS, copy)
    (copy / "ig_0006_retention.py").unlink()

    problems = _drift(copy)
    assert len(problems) == len(_shipping_tags("ig_0006_retention.py")), problems
    for problem in problems:
        assert "ig_0006_retention.py" in problem
        assert "MISSING" in problem


@pytest.mark.parametrize("distribution", sorted(DISTRIBUTIONS))
def test_an_unreleased_migration_is_free_to_change(
    tmp_path: Path, distribution: str
) -> None:
    """Specificity for the two above: `_drift` must fire on RELEASED bytes, not
    on any change at all. A guard that refused every edit to the directory would
    pass both proofs above and block all future work."""
    copy = tmp_path / "versions"
    shutil.copytree(DISTRIBUTIONS[distribution], copy)
    victim = copy / next(iter(UNRELEASED[distribution]))
    victim.write_bytes(victim.read_bytes() + b"\n# still being written\n")
    assert not _drift(copy, distribution)


def test_the_guard_catches_an_edit_to_the_second_distributions_bytes(
    tmp_path: Path,
) -> None:
    """Enrolment is only real if the new rows are actually compared.

    `ea_0001` shipped in four tags with one digest, and every proof above walks
    integration's directory — so all of them would pass with the allocation
    rows present and never read. This damages the file `ea_0002` exists to
    avoid editing, and requires all four releases to be named.
    """
    distribution = "dotmac-entitlement-allocation"
    copy = tmp_path / "versions"
    shutil.copytree(DISTRIBUTIONS[distribution], copy)
    assert not _drift(copy, distribution), "the copy must start clean"

    victim = copy / "ea_0001_allocations.py"
    victim.write_bytes(victim.read_bytes() + b"\n# a formatter ran\n")

    shipped_in = _shipping_tags("ea_0001_allocations.py")
    assert len(shipped_in) == 4, shipped_in
    problems = _drift(copy, distribution)
    assert len(problems) == len(shipped_in), problems
    for problem, tag in zip(problems, shipped_in, strict=True):
        assert tag in problem
        assert "ea_0001_allocations.py" in problem
        assert (
            "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            in problem
        ), "the message must name the digest that shipped, not just 'differs'"


def test_one_distributions_damage_is_not_attributed_to_the_other(
    tmp_path: Path,
) -> None:
    """The scoping proof the second distribution makes necessary.

    `_drift` filters by owner. Without that filter it would hunt every recorded
    filename in whichever directory it was handed — so an intact allocation
    lineage would be reported as four MISSING files every time integration's
    directory was checked, and a guard that fails loudly for the wrong module
    is one whose next real failure gets waved through.

    Damage integration; require the allocation lineage, on disk and untouched,
    to stay silent.
    """
    damaged = tmp_path / "integration"
    shutil.copytree(DISTRIBUTIONS["dotmac-integration"], damaged)
    victim = damaged / "ig_0002_execution.py"
    victim.write_bytes(victim.read_bytes() + b"\n")
    assert _drift(damaged, "dotmac-integration"), "the damage must be reported"

    intact = tmp_path / "allocation"
    shutil.copytree(DISTRIBUTIONS["dotmac-entitlement-allocation"], intact)
    assert not _drift(intact, "dotmac-entitlement-allocation")


# ── The map is cross-checked against the tags it claims to quote ────────────


def _tag_oracle() -> object:
    """The repository's ONE definition of a usable tag oracle.

    Reusing `declared_publication_sweep` rather than re-implementing `git tag`
    here: two answers to "can this checkout be trusted about tags?" is how one
    of them ends up lenient. Its refusals are `SweepRefused`, and they are
    propagated, never converted to a skip.
    """
    import importlib.util

    path = REPO_ROOT / "scripts/declared_publication_sweep.py"
    spec = importlib.util.spec_from_file_location("declared_publication_sweep", path)
    assert spec is not None and spec.loader is not None
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    return sweep


def _blob_digest(tag: str, name: str) -> str:
    """SHA-256 of the migration file as git holds it at `tag`.

    The directory comes from the tag's own recorded distribution, not from a
    module constant — with two lineages in the map, a fixed path would compare
    one distribution's tags against the other's files and fail on every one.
    """
    distribution, _, _ = RELEASED_TAGS[tag]
    relative = DISTRIBUTIONS[distribution].relative_to(REPO_ROOT) / name
    # Fixed argv, no shell. The only interpolated values are this file's own
    # literals — a tag and a path from `RELEASED_TAGS`. `git` from PATH is the
    # same trust assumption every other subprocess guard here makes.
    argv = ["git", "show", f"{tag}:{relative.as_posix()}"]
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        argv, cwd=REPO_ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0, (
        f"`git show {tag}:{relative}` failed: {result.stderr.decode().strip()} "
        "— the map names a tag or a path this checkout does not have"
    )
    return hashlib.sha256(result.stdout).hexdigest()


def test_the_tag_oracle_is_usable_rather_than_absent() -> None:
    """Fail closed, exactly as `test_declared_publication.py` now does.

    A shallow or tagless checkout cannot answer what a release contained. The
    previous version of that module treated an unusable oracle as a skip and
    ran green while checking nothing on every PR for weeks (#202). This asserts
    the oracle up front so the cross-check below cannot inherit that shape.
    """
    sweep = _tag_oracle()
    assert not sweep.is_shallow(REPO_ROOT), (  # type: ignore[attr-defined]
        "shallow checkout: the released-migration cross-check needs full "
        "history and tags — the `unit` job sets fetch-depth: 0 for this reason"
    )
    tags = set(sweep.git_tags(REPO_ROOT))  # type: ignore[attr-defined]
    missing = sorted(set(RELEASED_TAGS) - tags)
    assert not missing, (
        f"tag(s) {missing} are recorded here but absent from this checkout — "
        "either the tags were not fetched, or the map names a release that "
        "does not exist"
    )


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_the_recorded_digests_are_what_the_tag_actually_holds(tag: str) -> None:
    """The half that makes the map hard to doctor.

    Editing a released migration and updating its digest here would satisfy
    every test above — the tree and the map would simply agree with each other
    about the wrong bytes. This compares the map to git, so the two can only be
    reconciled by moving a tag.

    It also proves the file SET: a released file quietly dropped from the map
    would go unnoticed by a digest comparison that only walks the map, so the
    tag's own file list is the expected set.
    """
    distribution, commit, files = RELEASED_TAGS[tag]
    relative = DISTRIBUTIONS[distribution].relative_to(REPO_ROOT).as_posix()
    argv = ["git", "ls-tree", "-r", "--name-only", tag, "--", relative]
    listing = subprocess.run(  # noqa: S603 # nosec B603 B607
        argv, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert listing.returncode == 0, listing.stderr
    at_tag = {
        line.rsplit("/", 1)[-1] for line in listing.stdout.splitlines() if line.strip()
    }
    assert at_tag == set(files), (
        f"{tag} ({commit}) contains {sorted(at_tag)} but the map records "
        f"{sorted(files)} — a released file is missing from, or invented in, "
        "this entry"
    )
    for name, expected in sorted(files.items()):
        assert _blob_digest(tag, name) == expected, (
            f"{tag}/{name}: the map records {expected} but git holds "
            f"{_blob_digest(tag, name)} at that tag — the map was edited to "
            "match a changed file instead of the change being reverted"
        )


def test_the_cross_check_would_catch_a_doctored_map() -> None:
    """Sensitivity for the two above, without touching a real tag.

    `_blob_digest` reads git; the assertion compares it to the map. Point it at
    a file whose recorded digest is deliberately wrong and it must disagree —
    otherwise the comparison is not reading git at all, which is exactly how a
    cross-check degrades into a second copy of the thing it checks.
    """
    for tag, name in (
        ("dotmac-integration-v0.1.0a1", "ig_0001_connector_control_plane.py"),
        # Both distributions, because `_blob_digest` now resolves the directory
        # from the tag's owner. Reading only integration's would leave the
        # allocation rows compared against a path nothing checks.
        ("dotmac-entitlement-allocation-v0.1.0a1", "ea_0001_allocations.py"),
    ):
        actual = _blob_digest(tag, name)
        assert actual == RELEASED_TAGS[tag][2][name]
        assert actual != "0" * 64


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_each_recorded_digest_is_a_sha256(tag: str) -> None:
    """A truncated or mistyped digest would compare unequal to everything and
    turn the guard into a permanent failure — or, pasted from the wrong column,
    into a permanent pass against a value nothing produces."""
    _, _, files = RELEASED_TAGS[tag]
    for name, digest in files.items():
        assert len(digest) == 64, f"{tag}/{name}: {digest!r} is not a sha256"
        assert set(digest) <= set("0123456789abcdef"), f"{tag}/{name}: {digest!r}"
