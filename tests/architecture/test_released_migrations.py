"""A migration that shipped in a published tag is history, and history is bytes.

## The enforceable premise (ADR-0018)

`dotmac-integration` has been published three times. Every migration file
present at `dotmac-integration-v0.1.0a1`, `-v0.1.0a2` and `-v0.1.0a3` is inside
a wheel on the registry, and has therefore RUN, unmodified, in at least one
database this repository does not own and cannot inspect.

The a3 entry was added while this change was open: a3 was tagged from `b14f66e`
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

`dotmac-integration` only, because it is the module whose released bytes this
change was tempted to edit. Extending it to another distribution is a data
edit: add its tags below. It is deliberately NOT generalised to "every
allowlisted module" today — that would need digests for tags nobody has
verified in this change, and a guard populated by guesswork is worse than an
absent one.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = (
    REPO_ROOT / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
)

#: `tag -> (tagged commit, {filename: sha256 of the file at that tag})`.
#:
#: All three are annotated tags created by `release-module.yml` and present on
#: `origin`; the commit is recorded so a reviewer can locate the release without
#: resolving the tag object.
RELEASED_TAGS: dict[str, tuple[str, dict[str, str]]] = {
    "dotmac-integration-v0.1.0a1": (
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
}

#: Migration files that exist in the tree and have NOT shipped in any tag, so
#: are still editable. This is the second direction of the ratchet: a new
#: migration must be named here, and a file may only move from here into
#: `RELEASED_TAGS` — never the other way, and never out of both.
#:
#: `ig_0007` moves when `0.1.0a4` is tagged. That is the same commit in which
#: this set becomes empty again. It was written as `0.1.0a3` until a3 was
#: released without it — the release lane does not wait for an open branch,
#: which is the whole reason "released" is read from tags and not from a
#: version number somebody intended.
UNRELEASED = frozenset({"ig_0007_idempotency_ledger.py"})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drift(versions: Path) -> list[str]:
    """Every released file in `versions` whose bytes are not what shipped.

    Takes the directory as an argument rather than reading the module constant,
    which is the whole reason the sensitivity proofs below can run: they point
    it at a deliberately damaged copy of the tree.
    """
    problems: list[str] = []
    for tag, (commit, files) in sorted(RELEASED_TAGS.items()):
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
    return sorted(tag for tag, (_, files) in RELEASED_TAGS.items() if name in files)


# ── The guard ───────────────────────────────────────────────────────────────


def test_the_map_records_something_to_check() -> None:
    """A digest map that emptied would make every test below pass having
    compared nothing — the exact failure ADR-0018 calls a guard with no
    sensitivity."""
    assert RELEASED_TAGS, "no released tags recorded; this file proves nothing"
    for tag, (_, files) in RELEASED_TAGS.items():
        assert files, f"{tag} records no files"


def test_no_released_migration_has_been_edited() -> None:
    """The guard itself. Adopters ran these bytes; the tree must still hold
    them, or a repair was made in place instead of in a new revision."""
    problems = _drift(VERSIONS)
    assert not problems, "released migrations were modified:\n" + "\n".join(problems)


def test_two_tags_agree_about_a_file_they_both_shipped() -> None:
    """The map's own honesty check.

    `ig_0001` and `ig_0002` appear under both tags. If the recorded digests ever
    disagreed, the map would be asserting that one file had two sets of released
    bytes — which is either a transcription error here or the very edit this
    file exists to forbid, already committed and then papered over by updating
    only one entry.
    """
    seen: dict[str, tuple[str, str]] = {}
    for tag, (_, files) in sorted(RELEASED_TAGS.items()):
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


def test_every_migration_is_either_released_or_declared_unreleased() -> None:
    """The two-directional ratchet.

    Without this, the guard is trivially defeated in both directions: a new
    migration simply never enters the map, and a released one can be dropped
    from it in the same commit that edits it. Neither is possible if the union
    must equal the directory exactly.
    """
    on_disk = {path.name for path in VERSIONS.glob("ig_*.py")}
    released = {name for _, files in RELEASED_TAGS.values() for name in files}

    assert not (released & UNRELEASED), (
        f"{sorted(released & UNRELEASED)} claimed as both released and "
        "unreleased — a file is one or the other"
    )
    missing = sorted(on_disk - released - UNRELEASED)
    assert not missing, (
        f"migration(s) {missing} are in neither map. A new migration goes in "
        "UNRELEASED; record its digest under its tag when it ships"
    )
    stale = sorted((released | UNRELEASED) - on_disk)
    assert not stale, (
        f"{stale} are recorded but not on disk — see the deletion message in "
        "`_drift` before removing anything"
    )


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


def test_an_unreleased_migration_is_free_to_change(tmp_path: Path) -> None:
    """Specificity for the two above: `_drift` must fire on RELEASED bytes, not
    on any change at all. A guard that refused every edit to the directory would
    pass both proofs above and block all future work."""
    copy = tmp_path / "versions"
    shutil.copytree(VERSIONS, copy)
    victim = copy / next(iter(UNRELEASED))
    victim.write_bytes(victim.read_bytes() + b"\n# still being written\n")
    assert not _drift(copy)


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
    """SHA-256 of the migration file as git holds it at `tag`."""
    relative = VERSIONS.relative_to(REPO_ROOT) / name
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
    commit, files = RELEASED_TAGS[tag]
    relative = VERSIONS.relative_to(REPO_ROOT).as_posix()
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
    tag = "dotmac-integration-v0.1.0a1"
    actual = _blob_digest(tag, "ig_0001_connector_control_plane.py")
    assert actual == RELEASED_TAGS[tag][1]["ig_0001_connector_control_plane.py"]
    assert actual != "0" * 64


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_each_recorded_digest_is_a_sha256(tag: str) -> None:
    """A truncated or mistyped digest would compare unequal to everything and
    turn the guard into a permanent failure — or, pasted from the wrong column,
    into a permanent pass against a value nothing produces."""
    _, files = RELEASED_TAGS[tag]
    for name, digest in files.items():
        assert len(digest) == 64, f"{tag}/{name}: {digest!r} is not a sha256"
        assert set(digest) <= set("0123456789abcdef"), f"{tag}/{name}: {digest!r}"
