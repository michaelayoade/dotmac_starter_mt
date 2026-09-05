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
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404 -- argv list, shell=False; git only
import sys
import tomllib
from pathlib import Path

import pytest

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

#: DECLARED ON `main` AND NEVER BUILT, then retired. A fourth kind of spent
#: name, and the first one this facility has produced.
#:
#: The three sets above are all spent by ARTIFACTS: `PUBLISHED` has a tag,
#: `INVALIDATED` and `SUPERSEDED` each have a `CandidateArtifact.v1` receipt and
#: a disposition. `0.3.0a6` has none of those. It was the declared identity from
#: 2026-09-03 to 2026-09-04 and no wheel was ever built for it.
#:
#: It is spent anyway, by DOCUMENTS rather than by bytes. While it was declared,
#: `main` advertised it in the package CHANGELOG, in `docs/MODULE_CATALOG.md`,
#: in the `poetry.lock` path-package line, in
#: `docs/inventories/declared-publication-baseline.json`, and inside the
#: rendered `deploy/rendered/docker-compose.yml` labels by way of
#: `io.dotmac.deployment.configuration.digest`. Re-declaring it would recreate
#: `0.3.0a2`'s two-contracts defect with the documents instead of the bytes.
#:
#: **This constant is an EXPECTATION and NOT AN ENFORCEMENT, and the difference
#: is the reason it is documented here rather than merely listed.** The guard
#: has three record sets — tags, candidate receipts, dispositions — and this
#: name is in none of them, so `bindings_for("0.3.0a6", ...)` returns EMPTY for
#: both purposes and a dispatched build of `0.3.0a6` would not be refused. That
#: is an unmonitored population, recorded as one rather than described as
#: covered (ADR-0018: a guard exemption states an enforceable premise, or the
#: region is unmonitored rather than exempt).
#:
#: It is deliberately NOT added to `SUPERSEDED`, and the reason is mechanical
#: rather than stylistic: `test_a_superseded_candidate_is_still_refused_for_a_
#: second_build` asserts `_bindings(version, purpose="candidate")` is non-empty,
#: and for a version that was never built the guard has nothing to return. A
#: symmetrical-looking entry there would FAIL, which is the clearest available
#: proof that an unbuilt name is a different population and not a tidier
#: instance of the same one. `test_an_abandoned_unbuilt_name_has_no_record_to_
#: refuse_it` asserts that emptiness directly, so the gap is a checked fact
#: instead of a claim in a comment.
ABANDONED_UNBUILT = ("0.3.0a6",)


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


def test_the_version_this_tree_declares_is_admitted_for_its_own_release() -> None:
    """The half a planted refusal cannot substitute for.

    Read from `pyproject.toml` rather than hard-coded: this is the assertion
    that keeps the guard and the tree from drifting. If someone re-declares a
    version that is published or CONSUMED — the exact mistake that produced
    `0.3.0a2`'s two contracts — this fails here rather than at a dispatch six
    weeks later.

    WHICH purposes are admitted flips with the declared version's lifecycle
    stage, and the flip is part of the record rather than a loosening. The
    declared version is now `0.4.0a1`. The record-only guard still sees it as
    unbuilt and admits both purposes because its candidate receipt never entered
    the tree. That answer is intentionally NOT the lifecycle answer: run
    `33920058598` built it once, and the external-oracle candidate-window gate
    refuses both the missing receipt and the later source drift. This test
    documents the narrower record-reader result; it must not be cited as
    authority to build or release `0.4.0a1`.

    `0.3.0a6` reached this same stage and left it without ever being built —
    the first name in this facility's ledger to do so. It acquired no receipt
    and no disposition, so it is absent from `SUPERSEDED` and present instead in
    `ABANDONED_UNBUILT`, which that constant explains at length. Nothing about
    this test changed for it, which is the point worth noticing: an unbuilt
    predecessor leaves no trace in the guard's records, and that absence is a
    gap rather than a clean handover.

    `0.3.0a5` walked the whole cycle inside a single day and is worth reading as
    the complete state machine. Declared and unbuilt: both purposes admitted.
    BUILT on 2026-09-03 (run 33780438726, artifact 9903418260): the candidate
    purpose began refusing a SECOND build while release still admitted, because
    publishing the exact bytes a candidate lane built is the correct release
    path. SUPERSEDED the same day, once PR #600 changed the facility source
    under that declared name: release refuses too, by the disposition record
    that says why.

    That last transition is the one this guard could not have prompted, and the
    distinction matters for anyone reading this as coverage. This test asks
    whether a DECLARED version may be built or released. It does not ask whether
    the tree still ships the source an already-built candidate was built from —
    a different question, over a population with no tag, answered by
    `test_candidate_source_binding.py`.

    This is the same transition `0.3.0a3` and `0.3.0a4` each made. Reading the
    tests together is what shows it as a state machine advancing rather than as
    a constraint being relaxed.
    """
    declared = _declared_version()
    found = _bindings(declared, purpose="release")
    assert not found, (
        f"{FACILITY} declares {declared}, which is forbidden from release: "
        + "; ".join(str(binding) for binding in found)
    )


def test_the_declared_version_has_no_candidate_artifact_yet() -> None:
    """The other half of the flip, in its NO-COMMITTED-RECORD position.

    This is deliberately a record-stage assertion rather than a lifecycle
    invariant. The build oracle proves `0.4.0a1` was built; this reader proves
    only that no `CandidateArtifact.v1` reached the repository.

    It has exactly two forms and it flips between them at a build:

    - **declared with no committed candidate record** (this one): no
      `candidate artifact` binding exists, so this narrower guard admits a
      first build even when the external build oracle says one already ran;
    - **built** (`..._is_refused_for_a_SECOND_build`): the one candidate receipt
      IS the refusal.

    `0.3.0a3`, `0.3.0a4` and `0.3.0a5` each walked both positions, and the flip
    is performed by hand at each transition ON PURPOSE. Deriving it — asking
    whether a receipt exists and expecting the guard to agree — would compare
    the guard's answer with the guard's own input, which is precisely what
    `PUBLISHED` is written out longhand to avoid. A stated expectation can be
    wrong and get caught; a derived one agrees with the guard for every input,
    including the inputs where the guard is broken.

    **What this is NOT.** It is not the name-freshness invariant — that is
    `test_the_declared_version_is_not_a_SPENT_name`, which holds in every stage
    and must stay separate. #597 briefly merged the two by adding
    `declared not in BUILT_CANDIDATES` to the freshness test, and #599 removed
    it again with the reason recorded in that test's docstring: it was
    "asserting a lifecycle STAGE while claiming to assert name freshness". Do
    not re-merge them. The stage belongs here, where the name says so.

    It still bites in this position: a hand-written `0.4.0a1` receipt for a
    build that never happened, or a guard reporting a candidate that does not
    exist, fails here.

    **READ THIS BEFORE TAKING IT AS EVIDENCE `0.4.0a1` IS UNBUILT. It is not.**
    Run 33920058598 built the candidate from `753a004e` on 2026-09-04 and the
    receipt was never committed, so this assertion is true of the LEDGER and
    false of the artifact — and its passing was one of the four green signals
    that let `#628` and `#631` move the facility source underneath the name.
    Nothing here is wrong: `bindings_for` reads records and there is no record.
    The population a record-reader cannot see is `AGENTS.md` rule 50's, answered
    from the build oracle by `candidate_source_binding.py --window` and frozen in
    `tests/architecture/candidate_window_baseline.json`. Do not "repair" this by
    hand-writing the receipt — the line above says why, and the repair is a
    decision recorded in the window baseline.
    """
    declared = _declared_version()
    found = _bindings(declared, purpose="candidate")
    assert not any(binding.kind == "candidate artifact" for binding in found), (
        f"{FACILITY} declares {declared}, which is recorded as already BUILT: "
        + "; ".join(str(binding) for binding in found)
        + ". If the 0.4.0a1 candidate has now been built, this test flips "
        "back to "
        "test_the_declared_version_is_refused_for_a_SECOND_build — see its "
        "docstring; do not delete the assertion"
    )


def test_the_declared_version_is_not_a_SPENT_name() -> None:
    """The drift-catcher: a re-declared spent name fails HERE, not at a
    dispatch six weeks later. Each spent set was the live mistake once — a2
    re-declared would be the two-contracts defect, a3/a4 re-declared would put
    a superseded candidate back in play.

    `BUILT_CANDIDATES` is deliberately NOT one of them, and this is the whole
    distinction the test turns on. A version having its OWN one candidate is
    not a spent name — it is the normal built state, which every release
    passes through between its candidate build and its publication, and which
    `0.3.0a3` and `0.3.0a4` each occupied in turn. Spent means CONSUMED BY
    SOMETHING ELSE: published under a tag, invalidated, or superseded. An
    earlier revision of this test asserted `declared not in BUILT_CANDIDATES`,
    which was true only while the declared version was unbuilt and became
    false the moment its candidate existed — it was asserting a lifecycle
    STAGE while claiming to assert name freshness.
    """
    declared = _declared_version()
    assert declared not in PUBLISHED
    assert declared != INVALIDATED
    assert declared not in SUPERSEDED
    # The fourth kind, added 2026-09-04 with `0.4.0a1`. A name can be spent by
    # DOCUMENTS as well as by bytes, and this half of the assertion is the only
    # thing standing between `0.3.0a6` and a re-declaration — the guard cannot
    # see it. See the constant's own comment.
    assert declared not in ABANDONED_UNBUILT


def test_an_abandoned_unbuilt_name_has_no_record_to_refuse_it() -> None:
    """The gap `ABANDONED_UNBUILT` exists to record, asserted rather than
    described — and asserted in the direction that hurts.

    This test PASSES on emptiness, which is normally the shape of a test that
    has stopped checking. It is written that way on purpose: the fact worth
    holding is that the guard cannot see this population at all, and a comment
    saying so decays while an assertion does not. If someone later gives
    `0.3.0a6` a hand-written receipt or a disposition to make the ledger look
    symmetrical, this fails and sends them to the constant, where the reason a
    version with no artifact gets no disposition is written out.

    Its sensitivity comes from the line below it: the same call, over the same
    repository, returns real bindings for a version that WAS built. Without
    that, an emptiness assertion would also pass against a guard that had
    stopped reading records.
    """
    for version in ABANDONED_UNBUILT:
        for purpose in ("candidate", "release"):
            assert not _bindings(version, purpose=purpose), (
                f"{version} was never built and has no record, yet the guard "
                f"reports a binding for --purpose {purpose}. If it has since "
                "acquired one, this is not the file to silence: see "
                "ABANDONED_UNBUILT"
            )
    # The control. A built version, through the identical call.
    assert _bindings(BUILT_CANDIDATES[0], purpose="candidate")


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
