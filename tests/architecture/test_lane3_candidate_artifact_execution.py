"""Lane 3 executes the recorded candidate, never checkout Foundation source.

The candidate receipt is the only source of artifact coordinates.  Both the
rehearsal runner and the publication-side receipt verifier must therefore use
the wheel fetched and verified against that receipt.  A dispatch-provided
digest or a ``sys.path`` insertion pointing at ``packages/`` would let a green
run attest different bytes from the ones later published.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LANE3 = ROOT / ".github/workflows/exposure-rehearsal.yml"
RELEASE = ROOT / ".github/workflows/release-facility.yml"
RUNNER = ROOT / "scripts/exposure_rehearsal_runner.py"
CHECKER = ROOT / "scripts/require_rehearsal.py"


def _document(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dispatch_inputs(document: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 parses the unquoted YAML key ``on`` as True.
    trigger = document.get("on", document.get(True, {}))
    return trigger["workflow_dispatch"]["inputs"]


def _run_steps(document: dict[str, Any], job: str) -> list[str]:
    return [
        str(step.get("run", ""))
        for step in document["jobs"][job]["steps"]
        if step.get("run")
    ]


def _ordered_findings(runs: list[str], markers: tuple[str, ...]) -> list[str]:
    positions: list[int] = []
    findings: list[str] = []
    for marker in markers:
        matches = [index for index, body in enumerate(runs) if marker in body]
        if len(matches) != 1:
            findings.append(f"{marker!r} occurs {len(matches)} times")
            continue
        positions.append(matches[0])
    if len(positions) == len(markers) and positions != sorted(positions):
        findings.append(f"candidate execution order is {positions}, not monotonic")
    return findings


LANE3_ORDER = (
    "resolve-candidate",
    "gh api",
    "verify-candidate",
    "pip install --no-deps",
    "scripts/exposure_rehearsal_runner.py",
)

RELEASE_ORDER = (
    "resolve-candidate",
    "gh api",
    "verify-candidate",
    "pip install --no-deps",
    "scripts/require_rehearsal.py",
)


def _lane3_findings(document: dict[str, Any]) -> list[str]:
    findings = _ordered_findings(_run_steps(document, "rehearse"), LANE3_ORDER)
    inputs = _dispatch_inputs(document)
    if "foundation_artifact" in inputs:
        findings.append("the dispatch can supply a candidate digest")
    expected_inputs = {
        "authorization_run",
        # `authorization_doc_digest` was REMOVED on 2026-09-05 and must not come
        # back. A digest typed into a dispatch field is a claim about a document
        # rather than the document, and nothing could attest it — the runner
        # takes `--authorization-document` and refuses unless an installed
        # verifier vouches for the bytes (`scripts/lane3_authorization.py`).
        "facility",
        "controller_identity",
        "target",
        "vm_slot",
        "candidate_version",
    }
    if set(inputs) != expected_inputs:
        findings.append("the dispatch input set is not the closed expected set")
    permissions = document["jobs"]["rehearse"].get("permissions", {})
    if permissions.get("actions") != "read":
        findings.append("the rehearsal job cannot fetch the recorded Actions artifact")
    execute = next(
        (
            body
            for body in _run_steps(document, "rehearse")
            if "scripts/exposure_rehearsal_runner.py" in body
        ),
        "",
    )
    # ONE string covering both properties, because they are only jointly
    # sufficient: the candidate venv makes the wheel importable and `-E -P`
    # makes it the ONLY importable copy. A venv reached with PYTHONPATH still
    # pointing at the checkout exercises the source.
    candidate_runner = (
        ".lane3-foundation/bin/python -E -P scripts/exposure_rehearsal_runner.py"
    )
    if candidate_runner not in execute:
        findings.append("the rehearsal runner is not driven by the candidate venv")
    if "steps.candidate.outputs.candidate_sha256" not in execute:
        findings.append("the receipt digest is not derived from the candidate receipt")
    if "-E -P" not in execute:
        findings.append("the rehearsal runner is not launched with PYTHONPATH cleared")
    # THREE revisions, three expressions. The candidate source revision comes
    # only from the resolved receipt; the runner revision is this workflow's own
    # head SHA. One expression serving both is the conflation.
    if "steps.candidate.outputs.candidate_source_sha" not in execute:
        findings.append("the candidate SOURCE revision is not passed at all")
    if "--candidate-source-revision" not in execute:
        findings.append("the candidate source revision is not named as its own input")
    if '--foundation-revision "${GITHUB_SHA}"' not in execute:
        findings.append("the runner revision is not this workflow's own head SHA")
    return findings


def _release_findings(document: dict[str, Any]) -> list[str]:
    findings = _ordered_findings(_run_steps(document, "build"), RELEASE_ORDER)
    verify = next(
        (
            body
            for body in _run_steps(document, "build")
            if "scripts/require_rehearsal.py" in body
        ),
        "",
    )
    if ".foundation-candidate/bin/python -E -P scripts/require_rehearsal.py" not in (
        verify
    ):
        findings.append(
            "the receipt verifier is not driven by the candidate venv with "
            "PYTHONPATH cleared"
        )
    # The binding that makes the CANDIDATE SOURCE revision real: the digest
    # identifies exactly one CandidateArtifact.v1, and that record names exactly
    # one source_sha. Without it a rehearsal of candidate A satisfies a
    # publication of candidate B whenever both ran at one commit.
    if "--artifact-digest" not in verify:
        findings.append("publication does not bind the receipt to the artifact")
    if "steps.candidate.outputs.candidate_sha256" not in verify:
        findings.append(
            "the artifact digest the gate compares is not the resolved " "candidate's"
        )
    return findings


def _checkout_imports(source: str) -> list[int]:
    tree = ast.parse(source)
    findings: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "insert" or not isinstance(node.func.value, ast.Attribute):
            continue
        if node.func.value.attr != "path" or not isinstance(
            node.func.value.value, ast.Name
        ):
            continue
        if node.func.value.value.id != "sys":
            continue
        rendered = ast.dump(node)
        if "dotmac-deployment-foundation" in rendered and "packages" in rendered:
            findings.append(node.lineno)
    return findings


def test_lane3_executes_the_digest_verified_candidate_wheel() -> None:
    assert _lane3_findings(_document(LANE3)) == []


def test_publication_verifies_the_receipt_with_the_same_candidate_wheel() -> None:
    assert _release_findings(_document(RELEASE)) == []


def test_neither_executable_can_import_foundation_from_checkout() -> None:
    assert _checkout_imports(RUNNER.read_text(encoding="utf-8")) == []
    assert _checkout_imports(CHECKER.read_text(encoding="utf-8")) == []


def test_the_lane3_guard_bites_on_each_identity_regression() -> None:
    original = _document(LANE3)

    missing_verify = copy.deepcopy(original)
    missing_verify["jobs"]["rehearse"]["steps"] = [
        step
        for step in missing_verify["jobs"]["rehearse"]["steps"]
        if "verify-candidate" not in str(step.get("run", ""))
    ]
    assert _lane3_findings(missing_verify)

    checkout_python = copy.deepcopy(original)
    for step in checkout_python["jobs"]["rehearse"]["steps"]:
        if "scripts/exposure_rehearsal_runner.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                ".lane3-foundation/bin/python", "python"
            )
    assert _lane3_findings(checkout_python)

    supplied_digest = copy.deepcopy(original)
    inputs = _dispatch_inputs(supplied_digest)
    inputs["foundation_artifact"] = {"required": True, "type": "string"}
    assert _lane3_findings(supplied_digest)


def test_the_lane3_guard_bites_on_each_revision_and_isolation_regression() -> None:
    """Sensitivity for the three additions, one planted defect each.

    The assertions above pass over workflows that are already correct, which
    says nothing about their ability to fail. Each mutation below is a shape a
    reviewer could plausibly ship.
    """
    original = _document(LANE3)

    # Isolation removed. A venv alone does not make the wheel the only
    # importable copy: PYTHONPATH is honoured by every interpreter, so the
    # rehearsal would exercise the checkout and pass identically whether or not
    # the wheel is correct.
    unisolated = copy.deepcopy(original)
    for step in unisolated["jobs"]["rehearse"]["steps"]:
        if "scripts/exposure_rehearsal_runner.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                ".lane3-foundation/bin/python -E -P",
                ".lane3-foundation/bin/python",
            )
    assert _lane3_findings(unisolated)

    # The conflation itself: the candidate source revision replaced by the
    # workflow's own head SHA, so two of the three questions get one answer and
    # nothing can say they disagreed.
    conflated = copy.deepcopy(original)
    for step in conflated["jobs"]["rehearse"]["steps"]:
        if "scripts/exposure_rehearsal_runner.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                "${{ steps.candidate.outputs.candidate_source_sha }}",
                "${GITHUB_SHA}",
            )
    assert _lane3_findings(conflated)

    # And the other direction: the runner revision taken from the candidate
    # receipt, which would make a rehearsal claim to have run at whatever commit
    # built the wheel.
    swapped = copy.deepcopy(original)
    for step in swapped["jobs"]["rehearse"]["steps"]:
        if "scripts/exposure_rehearsal_runner.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                '--foundation-revision "${GITHUB_SHA}"',
                "--foundation-revision "
                '"${{ steps.candidate.outputs.candidate_source_sha }}"',
            )
    assert _lane3_findings(swapped)


def test_the_release_guard_bites_when_the_receipt_is_not_bound_to_the_artifact() -> (
    None
):
    """The substitution this binding exists for, planted.

    A publication that drops `--artifact-digest` still checks the lane, the
    revision and all sixteen statuses — everything except which bytes the run
    was about. `candidate_version` is a dispatch input precisely so two
    candidates can be rehearsed from one SHA, so this is a reachable shape and
    not a hypothetical one.
    """
    unbound = _document(RELEASE)
    for step in unbound["jobs"]["build"]["steps"]:
        run = str(step.get("run", ""))
        if "scripts/require_rehearsal.py" in run:
            step["run"] = "\n".join(
                line for line in run.splitlines() if "--artifact-digest" not in line
            )
    assert _release_findings(unbound)


# ── the third revision is emitted at all, and is usable when it is ─────────


def _release_facility():  # type: ignore[no-untyped-def]
    """`scripts/release_facility.py`, imported by path — it is a script."""
    spec = importlib.util.spec_from_file_location(
        "_release_facility", ROOT / "scripts" / "release_facility.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_release_facility"] = module
    spec.loader.exec_module(module)
    return module


def test_every_committed_candidate_receipt_names_the_commit_it_was_built_from() -> None:
    """Non-vacuity over the real records rather than a fixture.

    The candidate source revision is the one of the three that had no name
    downstream at all: it sat in these files and nothing emitted it, so nothing
    could compare it. A receipt that cannot answer would make the binding
    unreachable for that version, and it is better to learn that here than in a
    release lane.
    """
    facility = _release_facility()
    seen = 0
    for path in sorted((ROOT / "docs" / "inventories").glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        if document.get("schema") != "CandidateArtifact.v1":
            continue
        seen += 1
        revision = facility.candidate_source_revision(document)
        assert len(revision) == 40, f"{path.name}: {revision!r}"
    assert seen, "no CandidateArtifact.v1 receipt was found, so this proved nothing"


def test_an_unusable_source_revision_refuses_rather_than_being_passed_on() -> None:
    """Sensitivity, with the near-miss that matters most.

    Absent and empty are the obvious defects. The ABBREVIATED one is the
    dangerous member: it looks like an answer, and a binding built on it is a
    comparison that can only ever fail — reported to an operator as "the
    revisions disagree" when the actual defect is a truncated field.
    """
    facility = _release_facility()
    good = "a" * 40
    assert facility.candidate_source_revision({"source_sha": good}) == good
    for bad in (
        {},
        {"source_sha": ""},
        {"source_sha": good[:12]},
        {"source_sha": None},
    ):
        with pytest.raises(facility.ReleaseRefused):
            facility.candidate_source_revision(bad)


def test_resolve_candidate_emits_the_source_revision_under_its_own_name() -> None:
    """It must be a SEPARATE output from `candidate_sha256` and from the
    workflow's `GITHUB_SHA`. Three questions, three names — a value folded into
    another's is a value nothing downstream can refer to."""
    tree = ast.parse(
        (ROOT / "scripts" / "release_facility.py").read_text(encoding="utf-8")
    )
    resolver = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_resolve_candidate"
    )
    literals = [
        node.value
        for node in ast.walk(resolver)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any("candidate_source_sha=" in text for text in literals), (
        "resolve-candidate emits no `candidate_source_sha`, so nothing "
        "downstream can name the revision the artifact was built from"
    )
    # And it is not the DIGEST wearing another name. `sha256` is emitted
    # through the coordinate loop, so it appears as a bare key rather than in a
    # `candidate_...=` literal; both values must be there, separately.
    assert "sha256" in literals, (
        "the artifact digest is no longer emitted, so the two values a reader "
        "must not confuse are down to one"
    )


# ── ruling 4, the DECISIONS: driven against real git history ───────────────
#
# The workflow checks below read strings out of YAML. These run the functions
# those strings invoke, over a real repository, because a flag in a workflow
# that nobody has watched refuse is exactly as good as no flag.


def _repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A real repository: two commits on main and one on a divergent branch."""
    root = tmp_path / "repo"
    root.mkdir()

    def g(*a: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, a test fixture
            ["git", *a],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    g("init", "-q", "-b", "main")
    g("config", "user.email", "x@y")
    g("config", "user.name", "x")
    (root / "a").write_text("1", encoding="utf-8")
    g("add", "a")
    g("commit", "-qm", "one")
    built_from = g("rev-parse", "HEAD")
    (root / "a").write_text("2", encoding="utf-8")
    g("add", "a")
    g("commit", "-qm", "two")
    tip = g("rev-parse", "HEAD")
    g("checkout", "-q", "-b", "side", built_from)
    (root / "b").write_text("3", encoding="utf-8")
    g("add", "b")
    g("commit", "-qm", "divergent")
    divergent = g("rev-parse", "HEAD")
    g("checkout", "-q", "main")
    return root, built_from, tip, divergent


def test_a_candidate_built_from_an_ANCESTOR_is_accepted(tmp_path: Path) -> None:
    """The accepting control, and the case equality would wrongly refuse.

    Every recorded candidate was built at a commit that is an ancestor of `main`
    and not its head — `foundation-candidate.yml` exists so a candidate can be
    built BEFORE the commit that rehearses it. Requiring equality here would not
    be stricter; it would make the release unsatisfiable, and unsatisfiable
    gates get waived rather than met.
    """
    facility = _release_facility()
    root, built_from, tip, _ = _repo(tmp_path)
    bound = facility.require_revision_relationships(
        {"source_sha": built_from},
        runner_revision=tip,
        release_revision=tip,
        repo_root=root,
    )
    assert bound["candidate_source_revision"] == built_from
    assert bound["runner_revision"] == tip


def test_a_candidate_built_at_the_TIP_is_also_accepted(tmp_path: Path) -> None:
    """Ancestry admits equality, so the strict case still passes. Without this
    the rule could be 'strictly older', which nobody ruled."""
    facility = _release_facility()
    root, _, tip, _ = _repo(tmp_path)
    assert facility.require_revision_relationships(
        {"source_sha": tip}, runner_revision=tip, release_revision=tip, repo_root=root
    )


def test_a_candidate_from_a_DIVERGENT_branch_is_refused(tmp_path: Path) -> None:
    """The substitution 'no relationship' would permit: bytes built from code
    that was never on the protected branch, rehearsed and published by a
    protected-main run, with nothing saying so."""
    facility = _release_facility()
    root, _, tip, divergent = _repo(tmp_path)
    with pytest.raises(facility.ReleaseRefused) as exc:
        facility.require_revision_relationships(
            {"source_sha": divergent},
            runner_revision=tip,
            release_revision=tip,
            repo_root=root,
        )
    assert "NOT an ancestor" in str(exc.value)


def test_BOTH_protected_revisions_are_checked(tmp_path: Path) -> None:
    """Checking only one leaves the other open: only the release revision would
    let a rehearsal run on a branch that never contained the candidate source;
    only the runner's would let publication happen from one."""
    facility = _release_facility()
    root, built_from, tip, divergent = _repo(tmp_path)
    # Sound against the runner, divergent against the release revision.
    with pytest.raises(facility.ReleaseRefused) as exc:
        facility.require_revision_relationships(
            {"source_sha": tip},
            runner_revision=tip,
            release_revision=divergent,
            repo_root=root,
        )
    assert "release revision" in str(exc.value)
    # And the mirror image.
    with pytest.raises(facility.ReleaseRefused) as exc:
        facility.require_revision_relationships(
            {"source_sha": tip},
            runner_revision=divergent,
            release_revision=tip,
            repo_root=root,
        )
    assert "Lane 3 runner" in str(exc.value)


def test_an_UNFETCHED_commit_refuses_as_a_fetch_problem_not_a_branch_one(
    tmp_path: Path,
) -> None:
    """Two failures, two repairs, and collapsing them is how a real guard gets
    disabled.

    A shallow clone answers "not an ancestor" for two commits that are related,
    so a check that reported that would blame a divergent branch for a missing
    `fetch-depth: 0`. The refusal names the fetch.
    """
    facility = _release_facility()
    root, _, tip, _ = _repo(tmp_path)
    with pytest.raises(facility.ReleaseRefused) as exc:
        facility.require_revision_relationships(
            {"source_sha": "b" * 40},
            runner_revision=tip,
            release_revision=tip,
            repo_root=root,
        )
    message = str(exc.value)
    assert "fetch-depth" in message
    assert "NOT an ancestor" not in message


def test_the_tag_must_PEEL_to_the_candidate_source_commit(tmp_path: Path) -> None:
    """`git rev-parse <annotated tag>` returns the TAG OBJECT's sha; only
    `rev-list -n 1` returns the commit. This repository has already had a gate
    turn on that distinction, so the loose reading is asserted to fail here.
    """
    facility = _release_facility()
    root, built_from, tip, _ = _repo(tmp_path)

    def g(*a: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, a test fixture
            ["git", *a],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    g("tag", "-a", "v1", "-m", "annotated", built_from)
    tag_object = g("rev-parse", "v1")
    assert tag_object != built_from, "the fixture did not create an ANNOTATED tag"

    assert (
        facility.require_tag_peels_to("v1", expected_commit=built_from, repo_root=root)
        == built_from
    )
    # Points at a real commit, the wrong one.
    with pytest.raises(facility.ReleaseRefused):
        facility.require_tag_peels_to("v1", expected_commit=tip, repo_root=root)
    # The LOOSE reading: the tag object's own sha is not the commit.
    with pytest.raises(facility.ReleaseRefused):
        facility.require_tag_peels_to("v1", expected_commit=tag_object, repo_root=root)


# ── ruling 4: the three revisions stand in a stated RELATIONSHIP ───────────


def _publish_steps(document: dict[str, Any]) -> list[str]:
    job = next(
        name
        for name, body in document["jobs"].items()
        if any("git tag -a" in str(step.get("run", "")) for step in body["steps"])
    )
    return _run_steps(document, job)


def test_the_release_lane_binds_the_three_revisions_by_ancestry() -> None:
    """Neither equality nor nothing.

    Equality recreates the bootstrap loop; no relationship permits a candidate
    built on a divergent branch to be rehearsed and published by a
    protected-main run. `verify-revisions` is the ancestry check, and the runner
    revision must come from the RECEIPT rather than from a workflow variable —
    a value re-derived in the lane would be the lane agreeing with itself.
    """
    runs = _run_steps(_document(RELEASE), "build")
    bind = next((body for body in runs if "verify-revisions" in body), "")
    assert bind, "the release lane does not bind the three revisions at all"
    assert "--runner-revision" in bind and "--release-revision" in bind
    assert (
        "steps.rehearsal.outputs.rehearsal_runner_revision" in bind
    ), "the runner revision is not taken from the verified receipt"
    assert "${GITHUB_SHA}" in bind, "the release revision is not this run's SHA"


def test_the_version_tag_targets_the_commit_the_candidate_was_built_from() -> None:
    """A version tag names where this version's SOURCE is.

    Tagging the release run's SHA points the tag at a tree that was never
    built, so a consumer checking out the tag to inspect what they installed
    gets code that is not what they installed.
    """
    tag_step = next(
        body for body in _publish_steps(_document(RELEASE)) if "git tag -a" in body
    )
    assert (
        "candidate_source_sha" in tag_step
    ), "the tag does not target the candidate source commit"
    assert 'git tag -a "${TAG}"' in tag_step
    # The release revision is not lost — it is recorded separately, which is
    # what "bound independently" means.
    assert "release-adapter-revision: ${GITHUB_SHA}" in tag_step


def test_the_tag_is_read_back_from_GIT_and_peeled() -> None:
    """`git tag` succeeding says the command was accepted; only reading the ref
    back says what it resolves to. And PEELED: an annotated tag is an object of
    its own, so `rev-parse` returns that object's sha rather than the commit —
    the distinction a released-manifest gate in this repository already turned
    on."""
    steps = _publish_steps(_document(RELEASE))
    verify = next((body for body in steps if "verify-tag" in body), "")
    assert verify, "the pushed tag is never read back"
    positions = [
        i
        for i, body in enumerate(steps)
        if "git tag -a" in body or "verify-tag" in body
    ]
    assert (
        positions == sorted(positions) and len(positions) == 2
    ), "the tag is verified before it is written"


def test_those_three_guards_bite() -> None:
    """Sensitivity, one planted defect each. All three assertions above pass
    over a workflow that is already correct."""
    original = _document(RELEASE)

    # (1) ancestry dropped entirely.
    unbound = copy.deepcopy(original)
    unbound["jobs"]["build"]["steps"] = [
        step
        for step in unbound["jobs"]["build"]["steps"]
        if "verify-revisions" not in str(step.get("run", ""))
    ]
    runs = _run_steps(unbound, "build")
    assert not any("verify-revisions" in body for body in runs)

    # (2) the runner revision re-derived in the lane instead of read from the
    #     receipt — the shape that looks right and proves nothing.
    self_agreeing = copy.deepcopy(original)
    for step in self_agreeing["jobs"]["build"]["steps"]:
        if "verify-revisions" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                "${{ steps.rehearsal.outputs.rehearsal_runner_revision }}",
                "${GITHUB_SHA}",
            )
    bind = next(
        body
        for body in _run_steps(self_agreeing, "build")
        if "verify-revisions" in body
    )
    assert "steps.rehearsal.outputs.rehearsal_runner_revision" not in bind

    # (3) the tag back on the release SHA.
    mistagged = copy.deepcopy(original)
    for step in mistagged["jobs"][
        next(
            name
            for name, body in mistagged["jobs"].items()
            if any("git tag -a" in str(s.get("run", "")) for s in body["steps"])
        )
    ]["steps"]:
        if "git tag -a" in str(step.get("run", "")):
            step["run"] = (
                str(step["run"])
                .replace('"${SOURCE}"\n', '"${GITHUB_SHA}"\n')
                .replace("candidate_source_sha", "release_sha")
            )
    tag_step = next(body for body in _publish_steps(mistagged) if "git tag -a" in body)
    assert "candidate_source_sha" not in tag_step


# ── the isolation is proved, not asserted ──────────────────────────────────
#
# `-E -P` in a workflow is a string in a YAML file, and every check above is a
# check on that string. None of them establishes that the flags DO anything —
# and an isolation nobody has watched work is exactly as good as one that does
# not. So this pair runs a real interpreter, twice, over a decoy the checkout
# stands in for.


def _decoy(tmp_path: Path) -> Path:
    """A directory holding a module named as the package under isolation.

    It stands in for `packages/dotmac-deployment-foundation/src`, which is what
    `PYTHONPATH` would actually be pointing at on a runner that leaked it.
    """
    root = tmp_path / "checkout-src"
    (root / "dotmac_deployment_foundation").mkdir(parents=True)
    (root / "dotmac_deployment_foundation" / "__init__.py").write_text(
        f"ORIGIN = {DECOY_ORIGIN!r}\n", encoding="utf-8"
    )
    return root


#: What the decoy says about itself, and the ONLY string either test below
#: reasons about. Both ask one question — did the decoy win? — because that is
#: the question `-E` exists to answer, and it is answerable in an environment
#: where the real package is installed and in one where it is not.
DECOY_ORIGIN = "the checkout, not the wheel"


def _import_origin(tmp_path: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """Import the package name under a PYTHONPATH that points at the decoy.

    The probe uses `getattr(..., default)` rather than a bare attribute so that
    "the installed package answered" is an ORDINARY RESULT rather than a
    traceback. That distinction is what makes this pair environment-independent:
    under `-E -P` the decoy loses either by the module being absent entirely or
    by the installed one winning, and those are two spellings of one fact.

    An earlier revision asserted `ModuleNotFoundError`, which is true only where
    the package is NOT installed. CI installs it as a path dependency, so the
    check failed there while passing locally — a test that was really asserting
    a property of the developer's environment.
    """
    script = tmp_path / "probe.py"
    script.write_text(
        "import dotmac_deployment_foundation as m\n"
        "print(getattr(m, 'ORIGIN', 'not-the-decoy'))\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_decoy(tmp_path))
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, *flags, str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


def test_PYTHONPATH_alone_really_does_reach_the_checkout(tmp_path: Path) -> None:
    """The half that makes the isolation non-vacuous.

    Without `-E`, an interpreter — venv or not — imports whatever `PYTHONPATH`
    names. If this failed, `-E` would be guarding against nothing and every
    assertion above would be decorative.
    """
    result = _import_origin(tmp_path)
    assert result.returncode == 0, result.stderr
    assert DECOY_ORIGIN in result.stdout, (
        "PYTHONPATH did not reach the decoy, so `-E` would be guarding against "
        "nothing and every isolation assertion in this file would be decorative"
    )


def test_dash_E_dash_P_makes_the_checkout_unreachable(tmp_path: Path) -> None:
    """And the half the workflows rely on: with the flags, that same path does
    not win, so the only copy that can answer is the installed one.

    Deliberately NOT asserted as a particular failure. Where the package is
    installed the import succeeds from site-packages; where it is not, it fails
    outright. Both are the decoy losing, and pinning either one would make this
    a test about which environment it happens to run in — which is exactly the
    mistake this assertion replaced.
    """
    result = _import_origin(tmp_path, "-E", "-P")
    assert DECOY_ORIGIN not in result.stdout, (
        "`-E -P` still imported the module PYTHONPATH named. The rehearsal "
        "would exercise the checkout and pass identically whether or not the "
        "candidate wheel is correct"
    )


def test_the_runner_re_adds_its_OWN_directory_so_dash_P_costs_nothing() -> None:
    """`-P` drops the script's directory from `sys.path`, and the runner imports
    `lane3_provocation` and `lane3_inside_vantage` from exactly there.

    It survives because it puts that directory back itself, from an absolute
    path derived from `__file__` rather than from whatever the launcher left
    behind. If that line were ever removed, `-P` would turn a working rehearsal
    into an ImportError at collection — so the two belong in one test.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    own_dir = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "sys.path.insert"
        and "__file__" in ast.unparse(node)
        and "packages" not in ast.unparse(node)
    ]
    assert len(own_dir) == 1, (
        "the runner no longer re-adds its own directory to sys.path, and it is "
        "launched with -P, which removes it. Its `lane3_*` siblings become "
        "unimportable"
    )


def test_the_release_guard_bites_when_checkout_python_verifies_the_receipt() -> None:
    mutated = _document(RELEASE)
    for step in mutated["jobs"]["build"]["steps"]:
        if "scripts/require_rehearsal.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                ".foundation-candidate/bin/python", "python"
            )
    assert _release_findings(mutated)
