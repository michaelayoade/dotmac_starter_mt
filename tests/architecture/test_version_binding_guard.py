"""A version name binds to one source and one artifact, and the guard says so.

`0.3.0a2` is the live case. It was built once as candidate artifact 9740182233
from ``e930f878…``; commit ``0f390a9a…`` (#551) then changed the facility source
under the same declared version, and the repository offered one version name
over two contracts. Every fact needed to prevent that was already checked in —
a tag for a published version, a `CandidateArtifact.v1` receipt for a built one,
and now a `CandidateDisposition.v1` for a consumed one. Nothing read them.

`scripts/version_binding_guard.py` reads them. This module is its gate, and it
is written against the REAL records rather than a fixture, because Governance
**ADR 0034** is explicit that a gate enumerating real targets must demonstrate a
real-target ADMIT. Synthetic acceptance plus a planted refusal proves only that
the function has two branches; it does not prove the guard is pointed at
anything.

So both halves here are real:

* ``0.3.0a4`` — the version this tree actually declares — is ADMITTED for its
  own RELEASE and REFUSED for a second CANDIDATE build, because its one build is
  now bound by `docs/inventories/foundation-candidate-0.3.0a4.json`;
* ``0.3.0a3`` is ADMITTED for its own RELEASE and REFUSED for a second CANDIDATE
  build, because it has been built once
  (`docs/inventories/foundation-candidate-0.3.0a3.json`). Those two answers for
  one version are the whole point of ``--purpose``: a candidate receipt is the
  release's INPUT and a second build's REFUSAL. It is also the case this file
  most needs to keep asserting — those bytes are the Platform CP cutover's
  bootstrap input, and the bump away from the version must not have loosened
  anything about them;
* ``0.3.0a2`` is REFUSED for both, citing its candidate receipt and its
  invalidating disposition;
* every published tag is refused too, from the same record set, so the admit is
  not an artefact of the guard finding nothing.

## The fourth source, closed 2026-09-06

`ABANDONED_UNBUILT` used to live here, documenting a real hole: `0.3.0a6` was
declared, never built, retired unbuilt, and the guard had nothing on record to
refuse it with — its own comment said so at length, and a dedicated test
(`test_an_abandoned_unbuilt_name_has_no_record_to_refuse_it`) asserted the gap
directly rather than merely describing it. `0.4.0a1` then reproduced the same
shape from the built side: run 33920058598 built it once and the receipt never
reached this repository, so the guard ADMITTED a coordinate that was already
spent — on both purposes, for the version this tree currently declares.

`scripts/version_binding_guard.py:spent_identity_bindings` closes both. It is a
FOURTH typed binding source, read by schema (`SpentIdentity.v1`) from
`docs/inventories/spent-version-identities.json` exactly the way
`disposition_bindings` reads `CandidateDisposition.v1` — not a
`if version == "0.4.0a1"` special case, and not a fabricated candidate receipt
or disposition for either coordinate; Michael has explicitly reserved those
decisions. `SPENT_IDENTITY` below replaces `ABANDONED_UNBUILT`: the same two
tests that once proved the gap now prove it is closed, in the same direction of
difficulty (a real coordinate, not a fixture) that the module docstring above
already requires of every other assertion here.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404 -- argv list, shell=False; git only
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "version_binding_guard.py"
FACILITY = "dotmac-deployment-foundation"
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-deployment-foundation"

#: The consumed version. Never to be rebuilt, republished, tagged or
#: re-declared — see `docs/inventories/foundation-candidate-dispositions.json`.
INVALIDATED = "0.3.0a2"

#: SUPERSEDED, which is not the same thing as invalidated and must not become
#: it. Neither version's bytes were ever wrong as bytes:
#:
#: - `0.3.0a3` remains the sanctioned bootstrap input for Platform CP's issuer
#:   cutover and for Lane 3, resolved by run and artifact id. What changed is
#:   the CONTRACT: it predates the execution binding, so an executor built from
#:   those bytes runs unbound.
#: - `0.3.0a4` was ruled not cutover-admissible (2026-09-03): its installed CLI
#:   cannot load an assembly's effects or verifiers, and its release-evidence
#:   reader stringified signed envelopes at the `Mapping[str, str]` seam.
#:
#: `publishable=false` stops a PUBLICATION without invalidating the preserved
#: receipts, which is exactly the distinction `TERMINAL_UNPUBLISHABLE` draws by
#: holding `invalidated` and not `superseded`.
SUPERSEDED = ("0.3.0a3", "0.3.0a4", "0.3.0a5")

#: Versions of this facility that have been PUBLISHED. Written out rather than
#: read from `git tag`, so this test states an expectation the guard must meet
#: instead of comparing the guard's answer with the guard's own input.
PUBLISHED = ("0.1.0a1", "0.2.0a1", "0.2.0a2")

#: Every built-but-unpublished candidate still ADMITTED for its own release.
#: A tuple rather than a single name: a constant that held only one would
#: quietly stop exercising the second-build refusal on later frozen candidates.
#: `0.3.0a4` left this set on 2026-09-03 when its disposition landed, and
#: `0.3.0a5` left it the same day for the same reason — both are still refused
#: for a second build, exercised via `SUPERSEDED` below.
#:
#: It is down to ONE member, which is exactly the state the paragraph above
#: warns about, so it is recorded rather than left to be noticed: the
#: second-build refusal is still exercised on all four frozen candidates
#: through `SUPERSEDED`, and this constant now covers only the narrower
#: property that a live candidate is ADMITTED for its own release. The next
#: candidate build restores it to two.
BUILT_CANDIDATES = ("0.3.0a1",)

#: Spent with NO ordinary lifecycle record at all — the fourth binding source,
#: read from `docs/inventories/spent-version-identities.json` by
#: `spent_identity_bindings`. Written out longhand rather than derived, for the
#: same reason `PUBLISHED` is: a stated expectation can be wrong and get
#: caught, and a derived one agrees with the guard for every input including
#: the ones where the guard is broken.
#:
#: The three sets above are all spent by ARTIFACTS: `PUBLISHED` has a tag,
#: `INVALIDATED` and `SUPERSEDED` each have a `CandidateArtifact.v1` receipt and
#: a disposition. Neither member here has any of those:
#:
#: * `0.3.0a6` — declared 2026-09-03, never built, retired unbuilt 2026-09-04
#:   when the declared identity moved to `0.4.0a1`. Spent by DOCUMENTS: while
#:   declared, `main` advertised it in the package CHANGELOG, in
#:   `docs/MODULE_CATALOG.md`, in the `poetry.lock` path-package line, in
#:   `docs/inventories/declared-publication-baseline.json`, and inside the
#:   rendered `deploy/rendered/docker-compose.yml` labels by way of
#:   `io.dotmac.deployment.configuration.digest`. Re-declaring it would recreate
#:   `0.3.0a2`'s two-contracts defect with the documents instead of the bytes.
#: * `0.4.0a1` — **the version this tree currently declares.** Built ONCE (run
#:   33920058598, artifact 9954731961, from protected-main source
#:   `753a004e…`); the `CandidateArtifact.v1` receipt never reached this
#:   repository, and the facility's importable source then drifted under the
#:   unchanged name (`#628`, `#631`) before anyone committed it. Its ordinary
#:   record set is empty for the identical reason `0.3.0a6`'s is — nothing to
#:   point at — which is why `test_the_declared_version_has_no_candidate_
#:   artifact_yet` still passes even though this version is now refused for
#:   both purposes: that test only speaks to the ABSENCE of a `candidate
#:   artifact` binding, and `spent identity` is a different kind.
#:
#: **This was `ABANDONED_UNBUILT`, and it was an EXPECTATION rather than an
#: enforcement** — `bindings_for(version, ...)` returned EMPTY for every member
#: because the guard had only three record sets and none of them had anything
#: to refuse `0.3.0a6` with; a dispatched build would not have been stopped.
#: `spent_identity_bindings` closed that gap (2026-09-06), so the constant now
#: names a population the guard DOES refuse, and the tests below assert the
#: refusal directly rather than the absence.
#:
#: It is deliberately NOT added to `SUPERSEDED`, and the reason is mechanical
#: rather than stylistic: `SUPERSEDED` members are refused for `--purpose
#: candidate` by a `candidate artifact` binding — a second build would produce
#: a SECOND set of bytes for a name that already has one. Neither member here
#: has a first set of bytes to make a second build meaningful against; they are
#: refused by a `spent identity` binding instead, which says so.
SPENT_IDENTITY = ("0.3.0a6", "0.4.0a1")

#: The exact rows `docs/inventories/spent-version-identities.json` must carry —
#: not merely their count. A two-directional ratchet (ADR-0018): a row added
#: without updating this set fails `test_the_spent_identity_ledger_matches_
#: its_own_test_expectation` for growing silently, and a row removed without a
#: real lifecycle record landing in the same change fails
#: `test_every_known_spent_identity_is_refused_for_both_purposes` because the
#: coordinate it named would quietly reopen. See the record file's own
#: `$comment` for the full transition discipline.
EXPECTED_SPENT_IDENTITIES = frozenset(SPENT_IDENTITY)


def _module():
    """Load the guard, REGISTERING it before executing it.

    `@dataclass(slots=True)` re-reads `sys.modules[cls.__module__]` while
    processing the class, so a module executed without being registered raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` — a failure
    that looks like a bug in the guard and is a bug in this loader. The same
    correction was already made once for the publication sweep.
    """
    spec = importlib.util.spec_from_file_location("version_binding_guard", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _module()


def _declared_version() -> str:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["tool"]["poetry"]["version"])


def _bindings(version: str, purpose: str = "candidate"):
    return GUARD.bindings_for(
        FACILITY, version, repo_root=PROJECT_ROOT, purpose=purpose
    )


# ── the real-target ADMIT (ADR 0034) ────────────────────────────────────────


def test_the_version_this_tree_declares_is_refused_for_release() -> None:
    """The half a planted refusal cannot substitute for — and the assertion
    this test used to make (`assert not found`) was WRONG the day it was
    measured against `main`: the declared version is `0.4.0a1`, which is
    exactly the coordinate `spent_identity_bindings` now refuses.

    Read from `pyproject.toml` rather than hard-coded: this is the assertion
    that keeps the guard and the tree from drifting. If someone re-declares a
    version that is published or CONSUMED — the exact mistake that produced
    `0.3.0a2`'s two contracts — this fails here rather than at a dispatch six
    weeks later.

    Before 2026-09-06 this test asserted the OPPOSITE — that the declared
    version was ADMITTED for release — and said so honestly: "the record-only
    guard still sees it as unbuilt and admits both purposes because its
    candidate receipt never entered the tree... it must not be cited as
    authority to build or release 0.4.0a1". That was a correct description of
    a guard with a real hole in it, not a passing test to be proud of. Closing
    the hole means this assertion FLIPS along with it — a guard that still
    admitted the declared version after `spent_identity_bindings` existed would
    mean the new source was never actually wired into `all_bindings`.

    `0.3.0a5` walked the whole cycle inside a single day and is worth reading as
    the complete state machine for the ORDINARY case (a real candidate build
    followed by a real disposition). Declared and unbuilt: both purposes
    admitted. BUILT on 2026-09-03: the candidate purpose began refusing a
    SECOND build while release still admitted, because publishing the exact
    bytes a candidate lane built is the correct release path. SUPERSEDED the
    same day: release refuses too, by the disposition record that says why.
    `0.4.0a1` never had that ordinary path available — its receipt never
    reached this repository — which is exactly why it needed a FOURTH source
    rather than a disposition that has no receipt to anchor to.
    """
    declared = _declared_version()
    assert declared == "0.4.0a1", (
        "this test's docstring and its sibling below both narrate the "
        "0.4.0a1 spent-identity coordinate by name; a version bump changes "
        "which coordinate is under test and must be reflected here, not "
        "silently inherited"
    )
    found = _bindings(declared, purpose="release")
    assert found, (
        f"{FACILITY} declares {declared}, which is a SPENT identity with no "
        "ordinary lifecycle record (built once, unrecorded, then drifted — "
        "see docs/inventories/spent-version-identities.json) and must be "
        "refused for release"
    )
    assert any(binding.kind.startswith("spent identity") for binding in found), (
        "refused, but not by the spent-identity record that actually says "
        "why: " + "; ".join(str(binding) for binding in found)
    )


def test_the_version_this_tree_declares_is_refused_for_a_candidate_build() -> None:
    """The candidate-purpose sibling of the test above. Before 2026-09-06 this
    test (`test_the_declared_version_has_no_candidate_artifact_yet`) asserted
    only the absence of a `candidate artifact` binding — true then and still
    true now, since `0.4.0a1`'s receipt never entered this repository. That
    narrower assertion is preserved below as
    `test_the_declared_version_still_has_no_candidate_artifact_receipt`,
    because it is a different and still-true fact: the guard refuses this
    version NOT because a receipt exists, but because a `spent identity`
    record does. Conflating the two kinds would make the reason
    unfalsifiable — a hand-written `0.4.0a1` receipt would then look identical
    to today's actual state, which is precisely the shape
    `test_the_declared_version_still_has_no_candidate_artifact_receipt`'s
    "READ THIS BEFORE" paragraph existed to prevent.
    """
    declared = _declared_version()
    found = _bindings(declared, purpose="candidate")
    assert found, (
        f"{FACILITY} declares {declared}, a spent identity, and must be "
        "refused for a first candidate build too"
    )
    assert any(binding.kind.startswith("spent identity") for binding in found), (
        "refused, but not by the spent-identity record: "
        + "; ".join(str(binding) for binding in found)
    )


def test_the_declared_version_still_has_no_candidate_artifact_receipt() -> None:
    """The narrower, still-true half of the old
    `test_the_declared_version_has_no_candidate_artifact_yet`.

    This is deliberately a record-stage assertion rather than a lifecycle
    invariant, and it is a DIFFERENT fact from "the guard refuses this
    version" above: the build oracle proves `0.4.0a1` was built; this reader
    proves only that no `CandidateArtifact.v1` reached the repository. Both
    are true at once, for different reasons — the refusal comes from
    `spent_identity_bindings`, not from a phantom candidate receipt.

    **READ THIS BEFORE TAKING IT AS EVIDENCE `0.4.0a1` IS UNBUILT. It is not.**
    Run 33920058598 built the candidate from `753a004e` on 2026-09-04 and the
    receipt was never committed, so this assertion is true of the LEDGER and
    false of the artifact — and its passing was one of the four green signals
    that let `#628` and `#631` move the facility source underneath the name.
    The population a record-reader cannot see on its own is `AGENTS.md` rule
    50's, answered from the build oracle by `candidate_source_binding.py
    --window` and frozen in `tests/architecture/candidate_window_baseline.json`.
    Do not "repair" this by hand-writing the receipt — the line above says
    why, and the repair is a decision recorded in the window baseline, not in
    this test.
    """
    declared = _declared_version()
    found = _bindings(declared, purpose="candidate")
    assert not any(binding.kind == "candidate artifact" for binding in found), (
        f"{FACILITY} declares {declared}, which is now recorded as already "
        "BUILT via a real CandidateArtifact.v1 receipt: "
        + "; ".join(str(binding) for binding in found)
        + ". If the 0.4.0a1 candidate has now been committed as a receipt, "
        "the spent-identity row for it must be REMOVED in the same change "
        "(see docs/inventories/spent-version-identities.json's transition "
        "discipline) — do not leave both records refusing the same coordinate"
    )


def test_the_declared_version_is_not_a_SPENT_BY_BYTES_name() -> None:
    """The drift-catcher for the three ARTIFACT-spent sets: a re-declared spent
    name fails HERE, not at a dispatch six weeks later. Each spent set was the
    live mistake once — a2 re-declared would be the two-contracts defect,
    a3/a4 re-declared would put a superseded candidate back in play.

    `BUILT_CANDIDATES` is deliberately NOT one of them, and this is the whole
    distinction the test turns on. A version having its OWN one candidate is
    not a spent name — it is the normal built state, which every release
    passes through between its candidate build and its publication, and which
    `0.3.0a3` and `0.3.0a4` each occupied in turn. Spent means CONSUMED BY
    SOMETHING ELSE: published under a tag, invalidated, or superseded.

    **`SPENT_IDENTITY` is deliberately NOT checked here, and that is not an
    oversight — it is the fact this whole module change turns on.** The
    version this tree currently declares, `0.4.0a1`, IS a member of
    `SPENT_IDENTITY`: it is spent (built once, never recorded, then drifted)
    while simultaneously being the label `pyproject.toml` still carries,
    because allocating a successor is Michael's decision and has not been
    made. That is a real, current, and intentional state — a coordinate
    can be spent-by-drift and still be the resting declared name pending an
    authorized successor, in a way `0.3.0a2`/`SUPERSEDED` never could be
    (those are spent by DIFFERENT bytes existing under the same name, which
    re-declaring would immediately double). Asserting `declared not in
    SPENT_IDENTITY` here would therefore fail against the tree's actual,
    correct state — the failure this test exists to catch is a version going
    BACKWARD to reuse an artifact-spent name, not a version resting at its own
    already-spent-by-drift identity. `test_the_version_this_tree_declares_is_
    refused_for_release` and its candidate sibling are where that second fact
    is asserted, directly, by name.
    """
    declared = _declared_version()
    assert declared not in PUBLISHED
    assert declared != INVALIDATED
    assert declared not in SUPERSEDED


def test_every_known_spent_identity_is_refused_for_both_purposes() -> None:
    """`SPENT_IDENTITY` replaces `ABANDONED_UNBUILT`, and this test replaces
    `test_an_abandoned_unbuilt_name_has_no_record_to_refuse_it` — same shape,
    opposite assertion, because the gap it named is now closed.

    This is HALF of the two-directional ratchet the record file's `$comment`
    describes: a row silently removed from
    `docs/inventories/spent-version-identities.json` with no real lifecycle
    record replacing it reopens the coordinate, and this test — which names
    every member BY HAND rather than reading the file's own row count — is
    what catches that, because it does not agree with the guard's answer by
    construction.

    Sensitivity comes from the control at the end: the same call, over the
    same repository, returns real bindings for an artifact-spent version too,
    so an emptiness assertion elsewhere is not mistaken for this one having
    stopped checking.
    """
    for version in SPENT_IDENTITY:
        for purpose in ("candidate", "release"):
            found = _bindings(version, purpose=purpose)
            assert found, (
                f"{version} is a known spent identity and must be refused "
                f"for --purpose {purpose}. If it has since acquired an "
                "ordinary lifecycle record (tag/receipt/disposition) AND its "
                "spent-identity row was removed in the same change, this is "
                "not the file to silence — update SPENT_IDENTITY instead"
            )
            assert any(
                binding.kind.startswith("spent identity") for binding in found
            ), (
                f"{version} is refused, but not by its spent-identity record: "
                + "; ".join(str(binding) for binding in found)
            )
    # The control. A built version, through the identical call.
    assert _bindings(BUILT_CANDIDATES[0], purpose="candidate")


def test_the_spent_identity_ledger_matches_its_own_test_expectation() -> None:
    """The other half of the ratchet: the record file's row set must equal
    `SPENT_IDENTITY` exactly, so a THIRD row appended without updating this
    test — or a row deleted without updating it — fails here rather than
    silently changing what the guard refuses.
    """
    document = json.loads(
        (
            PROJECT_ROOT / "docs" / "inventories" / "spent-version-identities.json"
        ).read_text(encoding="utf-8")
    )
    on_disk = {
        entry["version"]
        for entry in document["entries"]
        if entry.get("facility") == FACILITY
    }
    assert on_disk == EXPECTED_SPENT_IDENTITIES


def test_no_spent_identity_row_duplicates_an_ordinary_lifecycle_record() -> None:
    """The other direction of the transition discipline (ADR-0018): a row
    whose `(facility, version)` ALREADY has a tag, a candidate receipt or a
    disposition is stale and must be removed in the same change that added
    the real record — a ledger that only grows stops describing anything.

    Checked against the guard's own three ORDINARY sources directly, not
    against `all_bindings` (which would trivially include the spent-identity
    binding itself and always pass).
    """
    ordinary = (
        GUARD.tag_bindings(FACILITY, repo_root=PROJECT_ROOT)
        + GUARD.candidate_bindings(FACILITY, repo_root=PROJECT_ROOT)
        + GUARD.disposition_bindings(FACILITY, repo_root=PROJECT_ROOT)
    )
    ordinary_versions = {binding.version for binding in ordinary}
    overlap = set(SPENT_IDENTITY) & ordinary_versions
    assert not overlap, (
        f"{overlap} now has an ordinary lifecycle record AND a spent-identity "
        "row — remove the row(s) for these versions from "
        "docs/inventories/spent-version-identities.json in this same change"
    )


def test_the_admit_is_not_an_empty_record_set() -> None:
    """An admit against nothing is not an admit.

    The guard would return "free" for every version if it were reading no
    records at all, and the test above would pass. This is what makes the admit
    mean something: the same call, over the same repository, finds real
    bindings for other versions.
    """
    every = GUARD.all_bindings(FACILITY, repo_root=PROJECT_ROOT)
    versions = {binding.version for binding in every}
    assert set(PUBLISHED) <= versions
    assert {INVALIDATED, *SUPERSEDED, *BUILT_CANDIDATES} <= versions
    kinds = {binding.kind for binding in every}
    assert "published tag" in kinds
    assert "candidate artifact" in kinds
    assert any(kind.startswith("disposition") for kind in kinds)


# ── the real-target REFUSALS ────────────────────────────────────────────────


def test_the_invalidated_candidate_is_refused_for_a_new_build() -> None:
    found = _bindings(INVALIDATED)
    kinds = {binding.kind for binding in found}
    assert "candidate artifact" in kinds, found
    assert "disposition (invalidated)" in kinds, found


def test_the_invalidated_candidate_is_refused_for_a_RELEASE_too() -> None:
    """`--purpose release` permits a version's own candidate receipt, because
    publishing those exact bytes is the designed path. The disposition must
    survive that exemption, or an invalidated candidate reaches the index."""
    found = _bindings(INVALIDATED, purpose="release")
    assert found, "an invalidated candidate must never be publishable"
    assert all(binding.kind.startswith("disposition") for binding in found), found


@pytest.mark.parametrize("version", PUBLISHED)
def test_a_published_version_is_refused_for_both_purposes(version: str) -> None:
    for purpose in ("candidate", "release"):
        found = _bindings(version, purpose=purpose)
        assert any(
            binding.kind == "published tag" for binding in found
        ), f"{version} is published and must be refused for {purpose}"


@pytest.mark.parametrize("version", BUILT_CANDIDATES)
def test_a_built_candidate_is_refused_for_a_SECOND_build(version: str) -> None:
    found = _bindings(version)
    assert any(binding.kind == "candidate artifact" for binding in found), found


@pytest.mark.parametrize("version", SUPERSEDED)
def test_a_superseded_candidate_is_refused_for_release(version: str) -> None:
    """The refusal moved to where the REASON is.

    Before the frozen-candidate resolve fix in this same change, `0.3.0a3` was
    refused for release by a source-version mismatch — a coincidence standing
    where a reason belongs, which stops holding the moment somebody bumps a
    version for an unrelated purpose. It is now refused by the record that
    actually says why, and the guard reads that record.
    """
    found = _bindings(version, purpose="release")
    assert found, f"{version} must be refused for release by its disposition"
    assert any(binding.kind == "disposition (superseded)" for binding in found), (
        f"{version} is refused, but not by its disposition: "
        + "; ".join(binding.kind for binding in found)
    )


@pytest.mark.parametrize("version", SUPERSEDED)
def test_a_superseded_candidate_is_still_refused_for_a_second_build(
    version: str,
) -> None:
    """Superseded is not permission to rebuild. The bytes are historical fact;
    a second build of the same version would make one name two artifacts."""
    assert _bindings(version, purpose="candidate")


@pytest.mark.parametrize("version", BUILT_CANDIDATES)
def test_a_built_candidate_is_admitted_for_its_own_release(version: str) -> None:
    """The exception, stated as a test so it cannot quietly widen. A candidate
    receipt is the release lane's INPUT — `foundation-candidate.yml` builds once
    so publication reuses those bytes rather than rebuilding them.

    `0.3.0a3` is the live case: its wheel is the Platform CP cutover's bootstrap
    input, resolved by run and artifact id out of the committed receipt rather
    than by the version this tree declares. Moving the declared identity to
    `0.3.0a4` must leave this admit exactly where it was."""
    assert not _bindings(version, purpose="release")


# ── the guard's own failure modes ───────────────────────────────────────────


def test_a_checkout_with_no_tags_refuses_to_answer(tmp_path: Path) -> None:
    """Without tags the guard cannot see publications, so it would admit an
    already-released version — the exact failure it exists to stop. An
    unavailable oracle is a refusal to answer, never a pass."""
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / "docs" / "inventories").mkdir(parents=True)
    (repo / ".github" / "release-facilities.json").write_text(
        json.dumps(
            {"facilities": {FACILITY: {"tag_prefix": f"{FACILITY}-v"}}}, indent=2
        )
    )
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "init", "-q"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    with pytest.raises(GUARD.CannotAnswer):
        GUARD.tag_bindings(FACILITY, repo_root=repo)


def test_an_unallowlisted_facility_refuses_to_answer() -> None:
    """ "No tag found" and "this guard does not know which tags to look for" are
    the same empty list and must not be the same answer."""
    with pytest.raises(GUARD.CannotAnswer):
        GUARD.tag_prefix("dotmac-not-a-facility", PROJECT_ROOT)


def test_an_unknown_purpose_refuses_to_answer() -> None:
    with pytest.raises(GUARD.CannotAnswer):
        _bindings("0.9.9", purpose="whatever")


# ── sensitivity: the refusal is derived, not hard-coded ─────────────────────


def test_a_receipt_for_the_declared_version_would_refuse_it(tmp_path: Path) -> None:
    """Planted, because the live answers are for fixed version strings and a
    guard with `if version == "0.3.0a2"` in it would pass them all. Here the
    declared version is given a candidate receipt in a SCRATCH tree — one whose
    only record is the planted file — and must be reported from it. That the
    real tree now also refuses the declared version for a second build is not a
    substitute: this proves the refusal is derived from whatever records are
    present, not from the repository's particular ones."""
    declared = _declared_version()
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / "docs" / "inventories").mkdir(parents=True)
    (repo / ".github" / "release-facilities.json").write_text(
        json.dumps(
            {"facilities": {FACILITY: {"tag_prefix": f"{FACILITY}-v"}}}, indent=2
        )
    )
    (repo / "docs" / "inventories" / "planted.json").write_text(
        json.dumps(
            {
                "schema": "CandidateArtifact.v1",
                "facility": FACILITY,
                "version": declared,
                "source_sha": "0" * 40,
                "sha256": "1" * 64,
                "artifact_id": "1",
            },
            indent=2,
        )
    )
    found = GUARD.candidate_bindings(FACILITY, repo_root=repo)
    assert [binding.version for binding in found] == [declared]


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / "docs" / "inventories").mkdir(parents=True)
    (repo / ".github" / "release-facilities.json").write_text(
        json.dumps(
            {
                "facilities": {
                    FACILITY: {"tag_prefix": f"{FACILITY}-v"},
                    "some-other-facility": {"tag_prefix": "some-other-facility-v"},
                }
            },
            indent=2,
        )
    )
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "init", "-q"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    # `tag_bindings` refuses to answer on a checkout with NO tags at all (see
    # `test_a_checkout_with_no_tags_refuses_to_answer`). A scratch tree that
    # wants a real ADMIT/REFUSE answer through `bindings_for` — rather than
    # deliberately exercising that failure mode — needs at least one
    # unrelated tag so the tag oracle can answer "no PUBLISHED tag for this
    # facility", not "I cannot see any tags".
    for name, email in (("user.email", "test@example.invalid"), ("user.name", "Test")):
        subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(repo), "config", name, email],  # noqa: S607
            check=True,
            capture_output=True,
        )
    (repo / "README.md").write_text("scratch\n")
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "add", "."],  # noqa: S607
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "commit", "-q", "-m", "scratch"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "tag", "unrelated-marker"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return repo


def _scratch_repo_no_tags(tmp_path: Path) -> Path:
    """Like `_scratch_repo`, but WITHOUT the marker tag — deliberately used
    only where the missing-tag failure mode itself is under test."""
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / "docs" / "inventories").mkdir(parents=True)
    (repo / ".github" / "release-facilities.json").write_text(
        json.dumps(
            {"facilities": {FACILITY: {"tag_prefix": f"{FACILITY}-v"}}}, indent=2
        )
    )
    subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo), "init", "-q"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return repo


def _write_spent_identity(
    repo: Path, *, facility: str = FACILITY, version: str, kind: str = "test-kind"
) -> None:
    (repo / "docs" / "inventories" / "planted-spent.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "schema": "SpentIdentity.v1",
                        "facility": facility,
                        "version": version,
                        "kind": kind,
                        "reason": "planted for a test; never a real coordinate",
                    }
                ]
            },
            indent=2,
        )
    )


def test_a_planted_spent_identity_row_would_refuse_it(tmp_path: Path) -> None:
    """The fourth-source analogue of
    `test_a_receipt_for_the_declared_version_would_refuse_it`: planted in a
    SCRATCH tree whose only record is this one file, so a guard with
    `if version == "0.4.0a1"` hard-coded somewhere would pass every live
    assertion above while failing this one — this proves the refusal is
    derived from whatever `SpentIdentity.v1` rows are present, not from the
    repository's own two coordinates."""
    repo = _scratch_repo(tmp_path)
    _write_spent_identity(repo, version="1.2.3-planted")
    found = GUARD.spent_identity_bindings(FACILITY, repo_root=repo)
    assert [binding.version for binding in found] == ["1.2.3-planted"]
    assert found[0].kind == "spent identity (test-kind)"


def test_a_different_facility_declaring_the_same_version_string_is_unaffected(
    tmp_path: Path,
) -> None:
    """Non-vacuity in the direction that stops this repair becoming a global
    string ban. The record is keyed on `(facility, version)`; a row for
    `dotmac-deployment-foundation 0.4.0a1` must not leak into a refusal for
    `some-other-facility 0.4.0a1` in the same scratch tree, over the identical
    file."""
    repo = _scratch_repo(tmp_path)
    _write_spent_identity(repo, facility=FACILITY, version="0.4.0a1")
    # The coordinate this row actually names is refused...
    assert GUARD.bindings_for(FACILITY, "0.4.0a1", repo_root=repo, purpose="candidate")
    # ...but a DIFFERENT facility declaring the identical version string is not.
    other = GUARD.bindings_for(
        "some-other-facility", "0.4.0a1", repo_root=repo, purpose="candidate"
    )
    assert (
        not other
    ), f"a different facility's identical version string was refused: {other}"


def test_a_genuinely_free_version_is_admitted_in_a_scratch_tree(
    tmp_path: Path,
) -> None:
    """The admit control this whole module needs and did not previously carry
    in scratch form: without it, a guard rewritten to refuse EVERYTHING would
    pass every refusal test above. A scratch tree with a spent-identity row
    for one version must still ADMIT an unrelated, unbound one."""
    repo = _scratch_repo(tmp_path)
    _write_spent_identity(repo, version="0.4.0a1")
    found = GUARD.bindings_for(
        FACILITY, "9.9.9-free", repo_root=repo, purpose="candidate"
    )
    assert not found, f"an unbound version was refused: {found}"


def test_missing_history_cannot_answer_even_with_a_spent_identity_row_present(
    tmp_path: Path,
) -> None:
    """Distinguishing CANNOT ANSWER from ADMIT is the whole point of
    `EXIT_CANNOT_ANSWER`, and this is the case where a naive implementation
    could blur them: a repository with a spent-identity row but NO tags at
    all. `all_bindings` calls `tag_bindings` first, so the missing-tag
    `CannotAnswer` must propagate before `spent_identity_bindings` is ever
    reached — a bug that caught the exception "because the spent-identity
    source already refuses this coordinate anyway" would let a checkout with
    no tags silently ADMIT every OTHER, unrelated version, which is the exact
    failure `test_a_checkout_with_no_tags_refuses_to_answer` exists to stop."""
    repo = _scratch_repo_no_tags(tmp_path)
    _write_spent_identity(repo, version="0.4.0a1")
    with pytest.raises(GUARD.CannotAnswer):
        GUARD.bindings_for(FACILITY, "0.4.0a1", repo_root=repo, purpose="candidate")
    with pytest.raises(GUARD.CannotAnswer):
        GUARD.bindings_for(
            FACILITY, "9.9.9-unrelated", repo_root=repo, purpose="candidate"
        )


def test_a_row_for_a_different_facility_is_a_negative_control(
    tmp_path: Path,
) -> None:
    """PERMANENT NEGATIVE CONTROL. A row present in the inventory, on disk,
    parsed successfully, and naming the EXACT version under test — but for a
    different facility — must produce SILENCE for the facility under test.
    This is the near-miss `test_a_different_facility_declaring_the_same_
    version_string_is_unaffected` already proves from the admit side; this
    version proves it from `spent_identity_bindings` directly, and confirms
    the row was actually exercised (not skipped for an unrelated reason) by
    asserting it DOES surface for the facility it actually names."""
    repo = _scratch_repo(tmp_path)
    _write_spent_identity(repo, facility="some-other-facility", version="0.4.0a1")
    # Silence for the facility under test — the near-miss.
    assert GUARD.spent_identity_bindings(FACILITY, repo_root=repo) == []
    # The row was read and matched something — proof the silence above is a
    # real filter result, not the row failing to parse or load at all.
    assert GUARD.spent_identity_bindings("some-other-facility", repo_root=repo)


def test_removing_the_fourth_source_from_all_bindings_reopens_a_known_coordinate(
    tmp_path: Path,
) -> None:
    """PLANTED DEFECT, named by file/line/symbol. Loads a MUTATED copy of
    `scripts/version_binding_guard.py` with the
    `+ spent_identity_bindings(facility, repo_root=repo_root)` line removed
    from `all_bindings`, and proves that mutant ADMITS a coordinate the real
    module refuses — i.e. the wiring in `all_bindings`, not merely the
    existence of `spent_identity_bindings` as a free function, is what this
    whole change depends on."""
    source = SCRIPT.read_text(encoding="utf-8")
    needle = "        + spent_identity_bindings(facility, repo_root=repo_root)\n"
    assert source.count(needle) == 1, "the wiring line moved; update this test"
    mutated_source = source.replace(needle, "")
    assert mutated_source != source

    mutated_path = tmp_path / "mutated_version_binding_guard.py"
    mutated_path.write_text(mutated_source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "mutated_version_binding_guard", mutated_path
    )
    assert spec is not None and spec.loader is not None
    mutant = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mutant
    spec.loader.exec_module(mutant)

    repo = _scratch_repo(tmp_path)
    _write_spent_identity(repo, version="0.4.0a1")

    # The real module still refuses it...
    assert GUARD.bindings_for(FACILITY, "0.4.0a1", repo_root=repo, purpose="candidate")
    # ...but the mutant, missing the wiring line, ADMITS it — the defect is
    # named (all_bindings' missing `spent_identity_bindings` call), not merely
    # asserted.
    reopened = mutant.bindings_for(
        FACILITY, "0.4.0a1", repo_root=repo, purpose="candidate"
    )
    assert not reopened, (
        "removing the spent_identity_bindings() call from all_bindings should "
        f"have reopened this coordinate; it did not: {reopened}"
    )


# ── the workflows actually gate ON this script, by POSITION ────────────────
#
# `version.py`'s note that "version_binding_guard.py only runs when a build is
# requested" is a statement about the trigger (workflow_dispatch), not about
# absence of ordering. Measured here rather than assumed: both lanes already
# called the guard before the section 6 of this task's brief was written, and
# these tests exist so that fact is PROVEN by step position rather than by
# reading the YAML once and trusting the reading.

CANDIDATE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "foundation-candidate.yml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-facility.yml"


def _step_index(steps: list[dict], needle: str) -> int:
    for index, step in enumerate(steps):
        blob = json.dumps(step)
        if needle in blob:
            return index
    raise AssertionError(f"no step contains {needle!r} among {len(steps)} steps")


def test_the_candidate_lane_calls_the_guard_before_building() -> None:
    workflow = yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["candidate"]["steps"]
    guard_index = _step_index(steps, "version_binding_guard.py")
    build_index = _step_index(steps, "poetry build")
    assert guard_index < build_index, (
        f"the guard runs at step {guard_index} but the build is at step "
        f"{build_index} — the guard must run BEFORE the build, not merely "
        "somewhere in the same job"
    )


def test_the_release_lane_calls_the_guard_before_fetching_and_before_publishing() -> (
    None
):
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    build_steps = workflow["jobs"]["build"]["steps"]
    build_guard_index = _step_index(build_steps, "version_binding_guard.py")
    fetch_index = _step_index(build_steps, "Fetch the candidate artifact")
    assert build_guard_index < fetch_index, (
        "the build job must refuse a bound version before it ever fetches the "
        "candidate bytes"
    )

    publish_steps = workflow["jobs"]["publish"]["steps"]
    publish_guard_index = _step_index(publish_steps, "version_binding_guard.py")
    publish_index = _step_index(publish_steps, "twine upload")
    assert publish_guard_index < publish_index, (
        "the publish job must re-assert the version is not bound before "
        "twine ever uploads anything — this is the credentialed side, and a "
        "gate that runs after the upload is not a gate"
    )


def test_a_guard_step_moved_after_the_build_would_be_caught(tmp_path: Path) -> None:
    """Sensitivity proof for the position check above, planted rather than
    argued: reorder the candidate lane's real steps so the guard runs AFTER
    the build, and confirm `_step_index` reports the guard at a HIGHER index —
    the exact condition `test_the_candidate_lane_calls_the_guard_before_
    building` would fail on."""
    workflow = yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))
    steps = list(workflow["jobs"]["candidate"]["steps"])
    guard_index = _step_index(steps, "version_binding_guard.py")
    guard_step = steps.pop(guard_index)
    # Re-insert it just after the (now shifted) build step.
    build_index_after_pop = _step_index(steps, "poetry build")
    steps.insert(build_index_after_pop + 1, guard_step)

    new_guard_index = _step_index(steps, "version_binding_guard.py")
    new_build_index = _step_index(steps, "poetry build")
    assert new_guard_index > new_build_index, (
        "the reordering fixture did not actually move the guard after the "
        "build; this test would then prove nothing"
    )
