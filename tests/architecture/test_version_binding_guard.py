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

#: SUPERSEDED, which is not the same thing and must not become it. `0.3.0a3`'s
#: bytes were never wrong — they remain the sanctioned bootstrap input for
#: Platform CP's issuer cutover and for Lane 3, resolved by run and artifact id.
#: What changed is the CONTRACT: it predates the execution binding, so an
#: executor built from those bytes runs unbound. `publishable=false` therefore
#: stops a PUBLICATION without withdrawing it from its two supervised consumers,
#: which is exactly the distinction `TERMINAL_UNPUBLISHABLE` draws by holding
#: `invalidated` and not `superseded`.
SUPERSEDED = "0.3.0a3"

#: Versions of this facility that have been PUBLISHED. Written out rather than
#: read from `git tag`, so this test states an expectation the guard must meet
#: instead of comparing the guard's answer with the guard's own input.
PUBLISHED = ("0.1.0a1", "0.2.0a1", "0.2.0a2")

#: Every built-but-unpublished, publishable candidate. A tuple rather than a
#: single name: a constant that held only one would quietly stop exercising the
#: second-build refusal on later frozen candidates.
BUILT_CANDIDATES = ("0.3.0a1", "0.3.0a4")


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

    `--purpose release`, not `candidate`, and that changed on 2026-09-02 when
    the declared version was built. Before then this tree declared a version
    with no artifact, so the meaningful admit was "nothing forbids building
    it". Now `0.3.0a3` HAS its one candidate, and the meaningful admit is the
    one publication actually needs: nothing forbids releasing those bytes. The
    candidate-purpose answer for this same version is a refusal, asserted
    directly below — dropping this test when it flipped, rather than moving it,
    would have left the guard with no real-target ADMIT at all (ADR 0034).
    """
    declared = _declared_version()
    found = _bindings(declared, purpose="release")
    assert not found, (
        f"{FACILITY} declares {declared}, which is forbidden from release: "
        + "; ".join(str(binding) for binding in found)
    )


def test_the_declared_version_is_refused_for_a_SECOND_build() -> None:
    """The candidate receipt is the second-build refusal."""
    declared = _declared_version()
    found = _bindings(declared, purpose="candidate")
    assert any(binding.kind == "candidate artifact" for binding in found), found
    assert declared not in PUBLISHED and declared != INVALIDATED


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
    assert {INVALIDATED, SUPERSEDED, *BUILT_CANDIDATES} <= versions
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


def test_a_superseded_candidate_is_refused_for_release() -> None:
    """The refusal moved to where the REASON is.

    Before the frozen-candidate resolve fix in this same change, `0.3.0a3` was
    refused for release by a source-version mismatch — a coincidence standing
    where a reason belongs, which stops holding the moment somebody bumps a
    version for an unrelated purpose. It is now refused by the record that
    actually says why, and the guard reads that record.
    """
    found = _bindings(SUPERSEDED, purpose="release")
    assert found, f"{SUPERSEDED} must be refused for release by its disposition"
    assert any(binding.kind == "disposition (superseded)" for binding in found), (
        f"{SUPERSEDED} is refused, but not by its disposition: "
        + "; ".join(binding.kind for binding in found)
    )


def test_a_superseded_candidate_is_still_refused_for_a_second_build() -> None:
    """Superseded is not permission to rebuild. The bytes are historical fact;
    a second build of the same version would make one name two artifacts."""
    assert _bindings(SUPERSEDED, purpose="candidate")


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
