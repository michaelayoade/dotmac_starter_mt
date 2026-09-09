"""Slice 4 — the generic compatibility evaluator and its all-of gate.

Two properties this file exists to prove, per the ruling that this gate must
be UNABLE to pass on mechanism alone:

1. **Non-vacuity.** With no product evidence bound, the gate refuses, and the
   refusal names which product and why.
   `GateResult.compatibility_satisfied` must not read an empty evaluation
   set as "nothing to refuse".
2. **All-of semantics.** Two products satisfied and one refusing must still
   refuse, and the refusal must name the refusing product specifically.

A third property, added by the second ruling: **the evaluator must be
CAPABLE of refusing a wrong commit, not merely make it visible.** Every
"satisfied" path below is built through `fetch_readiness_record` against a
REAL local git repository this file constructs (the same technique
`test_allocation_serialized_gate.py` already uses) — never through a
caller-supplied claim. There is no code path left in `compatibility_gate.py`
that accepts a boolean, a digest, or an ancestry claim as INPUT.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.architecture import compatibility_gate as gate

# ── Git fixture helpers: real local repositories, no network ───────────────


def _git(repo: Path, args: list[str]) -> None:
    command = ["git", *args]
    result = subprocess.run(  # noqa: S603
        command, cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _git_output(repo: Path, args: list[str]) -> str:
    command = ["git", *args]
    result = subprocess.run(  # noqa: S603
        command, cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _init_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "test@example.com"])
    _git(repo, ["config", "user.name", "Test"])
    _git(repo, ["config", "commit.gpgsign", "false"])
    (repo / "README.md").write_text("placeholder\n")
    _git(repo, ["add", "."])
    _git(repo, ["commit", "-q", "-m", "init"])
    return repo


def _commit_readiness_record(repo: Path, record: dict) -> str:
    record_path = repo / "docs" / "kernel-runtime-readiness.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record))
    _git(repo, ["add", "."])
    _git(repo, ["commit", "-q", "-m", "add readiness record"])
    return _git_output(repo, ["rev-parse", "HEAD"]).strip()


def _mark_as_protected_main(repo: Path, revision: str) -> None:
    _git(repo, ["update-ref", "refs/remotes/origin/main", revision])


def _requirement(
    requirement_id: str, *, satisfied: bool, source_reference: str = "app/x.py:1"
) -> dict:
    return {
        "id": requirement_id,
        "statement": f"{requirement_id} holds",
        "satisfied": satisfied,
        "source_reference": source_reference,
    }


def _record(*, repository: str, subject: str, requirements: list[dict]) -> dict:
    return {
        "schema": gate.READINESS_SCHEMA_MARKER,
        "product": repository,
        "subject": subject,
        "requirements": requirements,
        "composition": [],
        "source_references": ["README.md"],
    }


def _satisfied_requirements_for(product: str) -> list[dict]:
    """A record satisfying every id `PRODUCT_SPECS[product]` selects —
    generated from the fixed spec itself, so this fixture can never drift
    from the ids the module actually reads."""
    return [
        _requirement(requirement.requirement_id, satisfied=True)
        for requirement in gate.PRODUCT_SPECS[product].requirements
    ]


_ACADEMY_SATISFIED_REQUIREMENTS = _satisfied_requirements_for("academy")
_ERP_SATISFIED_REQUIREMENTS = _satisfied_requirements_for("erp")
_SUB_SATISFIED_REQUIREMENTS = _satisfied_requirements_for("sub")
_SATISFIED_REQUIREMENTS = {
    "academy": _ACADEMY_SATISFIED_REQUIREMENTS,
    "erp": _ERP_SATISFIED_REQUIREMENTS,
    "sub": _SUB_SATISFIED_REQUIREMENTS,
}


def _bind_satisfied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, product: str
) -> gate.ProductBinding:
    spec = gate.PRODUCT_SPECS[product]
    repo = _init_repo(tmp_path, f"{spec.repository}-clone")
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        requirements=_SATISFIED_REQUIREMENTS[product],
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))
    return gate.ProductBinding(product=product, revision=revision)


# ── Non-vacuity: the gate must be unable to pass on mechanism alone ────────


def test_gate_result_cannot_pass_on_an_empty_evaluation_set() -> None:
    """Plant: construct a `GateResult` directly with zero evaluations —
    `all(())` is `True`, so a `compatibility_satisfied` implemented as a bare
    `all(...)` would pass here for having nothing to check."""
    empty = gate.GateResult(evaluations=())
    assert empty.compatibility_satisfied is False
    assert "no product was evaluated" in empty.explain()
    assert empty.adoption_status == "not_evaluated"
    assert "ADOPTION NOT EVALUATED" in empty.explain()


def test_gate_result_passes_when_every_evaluation_is_satisfied() -> None:
    satisfied = gate.GateResult(
        evaluations=(
            gate.EvaluationResult("academy", "a" * 40, "satisfied", ()),
            gate.EvaluationResult("erp", "b" * 40, "satisfied", ()),
            gate.EvaluationResult("sub", "c" * 40, "satisfied", ()),
        )
    )
    assert satisfied.compatibility_satisfied is True


def test_the_gate_refuses_without_a_configured_clone_even_with_a_real_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checked-in bindings file now binds the three real protected-`main`
    coordinates (Academy #134, ERP #523, Sub #3041) — every product's
    `revision` is a well-formed 40-hex commit, not `None`. Without a
    configured local clone (the ordinary state on a workstation, as opposed
    to the CI job that exports `COMPAT_GATE_CLONE_*`), the runner cannot
    fetch a record and every evaluation refuses at `evidence_incomplete` —
    NOT `unbound` (a revision IS named) and NOT `evaluation_refused` (no
    record was ever read to disagree with)."""
    for spec in gate.PRODUCT_SPECS.values():
        monkeypatch.delenv(spec.clone_env_var, raising=False)

    bindings = gate.load_default_bindings()
    for product in gate.PRODUCTS:
        assert bindings[product].revision is not None
        assert gate.IMMUTABLE_COMMIT.fullmatch(bindings[product].revision)

    result = gate.evaluate_gate(bindings)
    assert result.compatibility_satisfied is False
    assert len(result.refusing) == 3
    for evaluation in result.evaluations:
        assert evaluation.compatibility_status == "evidence_incomplete"
        assert evaluation.revision is not None


def test_real_coordinates_report_compatibility_with_no_adoption_fraction() -> None:
    """Runs the checked-in bindings file's three REAL protected-`main`
    coordinates (Academy #134, ERP #523, Sub #3041) end to end, exactly as
    CI's checkout step configures `COMPAT_GATE_CLONE_*` (see the module
    docstring, "CI access this runner requires"). Skips outright, rather
    than silently asserting nothing, when no clone is configured — the
    ordinary state on a workstation.

    Proves the four things the ruled result contract requires of this exact
    run: (1) compatibility is 3-of-3 satisfied; (2) every product AND the
    aggregate report the fixed, structured `adoption_status ==
    "not_evaluated"`; (3) Academy's five real adoption-debt observations —
    all `satisfied=False` in its own record — remain visible; and (4) no
    adoption numerator or denominator (an "N/3"-shaped fraction) is emitted
    anywhere in the rendered output — asserted as an explicit absence, not
    merely left unchecked."""
    if not all(
        os.environ.get(spec.clone_env_var) for spec in gate.PRODUCT_SPECS.values()
    ):
        pytest.skip(
            "no COMPAT_GATE_CLONE_* configured in this environment — this "
            "assertion runs against CI's real product checkouts"
        )

    bindings = gate.load_default_bindings()
    result = gate.evaluate_gate(bindings)

    # (1) Compatibility is 3-of-3 satisfied.
    assert result.compatibility_satisfied is True, result.explain()
    assert all(e.compatibility_satisfied for e in result.evaluations)

    # (2) Every product AND the aggregate report the structured, fixed
    # adoption_status -- never a computed fraction.
    assert result.adoption_status == "not_evaluated"
    for evaluation in result.evaluations:
        assert evaluation.adoption_status == "not_evaluated"

    # (3) Academy's five real adoption-debt observations remain visible.
    academy = next(e for e in result.evaluations if e.product == "academy")
    adoption_findings = [f for f in academy.findings if f.startswith("ADOPTION STATE")]
    assert len(adoption_findings) == 5
    assert all("satisfied=False" in f for f in adoption_findings)

    # (4) No adoption numerator or denominator is emitted anywhere in the
    # rendered output -- assert the ABSENCE explicitly.
    rendered = (
        result.explain()
        + "\n"
        + "\n".join(finding for e in result.evaluations for finding in e.findings)
    )
    fraction_near_adoption = re.search(
        r"adoption[^\n]{0,60}\d+\s*/\s*\d+", rendered, re.IGNORECASE
    )
    assert fraction_near_adoption is None, fraction_near_adoption
    assert "0/3" not in rendered
    assert "3/3" not in rendered
    assert "0 of 3" not in rendered
    assert "3 of 3" not in rendered


def test_the_gate_refuses_when_bindings_is_entirely_empty() -> None:
    result = gate.evaluate_gate({})
    assert result.compatibility_satisfied is False
    assert tuple(e.product for e in result.evaluations) == gate.PRODUCTS
    for evaluation in result.evaluations:
        assert evaluation.compatibility_satisfied is False
        assert evaluation.compatibility_status == "unbound"
        assert evaluation.revision is None


def test_each_refusal_names_the_product_and_the_observed_reason() -> None:
    result = gate.evaluate_gate({})
    for evaluation in result.evaluations:
        joined = " ".join(evaluation.findings)
        assert "no revision is bound for" in joined
        assert evaluation.product in joined
        assert evaluation.compatibility_status == "unbound"


# ── Structured status: unbound is distinct from refused-after-evaluation ───


def test_compatibility_satisfied_is_derived_from_status_not_stored() -> None:
    unbound = gate.EvaluationResult("academy", None, "unbound", ())
    assert unbound.compatibility_satisfied is False
    satisfied = gate.EvaluationResult("academy", "a" * 40, "satisfied", ())
    assert satisfied.compatibility_satisfied is True


def test_an_unknown_status_is_refused_at_construction() -> None:
    with pytest.raises(
        ValueError, match="unknown EvaluationResult.compatibility_status"
    ):
        gate.EvaluationResult("academy", None, "incompatible", ())


def test_evaluation_result_has_no_digest_input_seam() -> None:
    """Structural proof of "the digest is output, never input": neither
    `ProductBinding` nor any accepted input carries a `digest` field a
    caller could set. Constructing one with an unexpected `digest` kwarg
    raises `TypeError` — the dataclass itself has no such slot."""
    with pytest.raises(TypeError):
        gate.ProductBinding(  # type: ignore[call-arg]
            product="erp", revision="a" * 40, digest="x" * 64
        )


def test_evaluation_result_has_no_adoption_status_input_seam() -> None:
    """Structural proof, the identical discipline as the digest seam above:
    `adoption_status` is a property, not a dataclass field, so there is no
    `__init__` parameter for it at all — a caller cannot supply or override
    it, and the fixed literal `"not_evaluated"` is the only value it can
    ever report."""
    with pytest.raises(TypeError):
        gate.EvaluationResult(  # type: ignore[call-arg]
            "academy", "a" * 40, "satisfied", (), adoption_status="satisfied"
        )
    result = gate.EvaluationResult("academy", "a" * 40, "satisfied", ())
    assert result.adoption_status == "not_evaluated"


def test_gate_result_has_no_adoption_status_input_seam() -> None:
    with pytest.raises(TypeError):
        gate.GateResult(evaluations=(), adoption_status="satisfied")  # type: ignore[call-arg]
    result = gate.GateResult(evaluations=())
    assert result.adoption_status == "not_evaluated"


# ── All-of semantics: two satisfied, one refusing must still refuse ────────


def test_all_three_satisfied_bindings_pass_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Admits control: three REAL local repositories, each carrying a fully
    satisfied readiness record, verified end to end through
    `fetch_readiness_record` (real `git merge-base --is-ancestor`, real
    `git show`, real JSON parse) — never through a caller-supplied claim."""
    bindings = {
        product: _bind_satisfied(monkeypatch, tmp_path / product, product)
        for product in gate.PRODUCTS
    }
    result = gate.evaluate_gate(bindings)
    assert result.compatibility_satisfied is True, result.explain()
    assert result.refusing == ()
    for evaluation in result.evaluations:
        assert evaluation.artefact_digest is not None


def test_two_satisfied_and_one_refusing_still_refuses_and_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bindings = {
        "academy": _bind_satisfied(monkeypatch, tmp_path / "academy", "academy"),
        "erp": _bind_satisfied(monkeypatch, tmp_path / "erp", "erp"),
        # sub omitted entirely — unbound.
    }
    result = gate.evaluate_gate(bindings)
    assert result.compatibility_satisfied is False
    assert len(result.refusing) == 1
    assert result.refusing[0].product == "sub"
    assert result.refusing[0].compatibility_status == "unbound"
    explanation = result.explain()
    assert "sub" in explanation


def test_unknown_product_in_bindings_is_refused_by_construction() -> None:
    with pytest.raises(ValueError, match="unknown product"):
        gate.evaluate_gate({"vendor_cp": gate.ProductBinding(product="vendor_cp")})


# ── Revision-shape refusal (reused, verbatim, from adoption_evidence.py) ──


def test_a_moving_ref_revision_is_refused_by_construction() -> None:
    binding = gate.ProductBinding(product="academy", revision="main")
    result = gate.evaluate_academy(binding)
    assert result.compatibility_satisfied is False
    assert result.compatibility_status == "moving_ref"


def test_an_embedded_moving_ref_after_at_is_refused() -> None:
    binding = gate.ProductBinding(product="erp", revision="main@e1402902")
    result = gate.evaluate_erp(binding)
    assert result.compatibility_satisfied is False
    assert result.compatibility_status == "moving_ref"


def test_an_abbreviated_commit_is_refused() -> None:
    binding = gate.ProductBinding(product="sub", revision="abc1234")
    result = gate.evaluate_sub(binding)
    assert result.compatibility_satisfied is False
    assert result.compatibility_status == "invalid_revision"


# ── The runner: ancestry, blob, parse — each a real refusal point ─────────


def test_ancestry_failure_refuses_as_not_on_protected_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plant: a commit that is never merged into `origin/main` — the real
    `git merge-base --is-ancestor` genuinely refuses it, not a claimed row."""
    spec = gate.PRODUCT_SPECS["erp"]
    repo = _init_repo(tmp_path, "erp-clone")
    base = _git_output(repo, ["rev-parse", "HEAD"]).strip()
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        requirements=_ERP_SATISFIED_REQUIREMENTS,
    )
    revision = _commit_readiness_record(repo, record)
    # origin/main stays at the FIRST commit — `revision` is never merged.
    _mark_as_protected_main(repo, base)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "not_on_protected_main"
    assert outcome.record is None
    assert outcome.problem is not None
    assert "NOT to be an ancestor" in outcome.problem


def test_no_clone_configured_refuses_as_evidence_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = gate.PRODUCT_SPECS["sub"]
    monkeypatch.delenv(spec.clone_env_var, raising=False)
    outcome = gate.fetch_readiness_record(spec, "a" * 40)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "no local clone is configured" in outcome.problem


def test_a_configured_but_missing_clone_is_an_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The corrected rule: a checkout failure must FAIL the CI job, never
    read as a gate refusal. A configured-but-nonexistent clone path raises,
    it does not return a soft `FetchOutcome`."""
    spec = gate.PRODUCT_SPECS["academy"]
    monkeypatch.setenv(spec.clone_env_var, str(tmp_path / "does-not-exist"))
    with pytest.raises(RuntimeError, match="infrastructure failure"):
        gate.fetch_readiness_record(spec, "a" * 40)


def test_blob_missing_at_path_refuses_as_evidence_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = gate.PRODUCT_SPECS["sub"]
    repo = _init_repo(tmp_path, "sub-clone")
    revision = _git_output(repo, ["rev-parse", "HEAD"]).strip()
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "no blob found" in outcome.problem
    assert outcome.digest is None


def test_invalid_json_refuses_but_still_reports_a_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = gate.PRODUCT_SPECS["erp"]
    repo = _init_repo(tmp_path, "erp-clone")
    record_path = repo / "docs" / "kernel-runtime-readiness.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("{not valid json")
    _git(repo, ["add", "."])
    _git(repo, ["commit", "-q", "-m", "bad record"])
    revision = _git_output(repo, ["rev-parse", "HEAD"]).strip()
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "not valid JSON" in outcome.problem
    assert outcome.digest is not None  # the blob WAS read and hashed


def test_schema_mismatch_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = gate.PRODUCT_SPECS["sub"]
    repo = _init_repo(tmp_path, "sub-clone")
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        requirements=_SUB_SATISFIED_REQUIREMENTS,
    )
    record["schema"] = "some-other-schema.v1"
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "`schema`" in outcome.problem


def test_product_mismatch_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record whose `product` names a DIFFERENT repository (e.g. copied
    from another product) is refused — `product` must equal the fixed
    repository name, not the short internal product name."""
    spec = gate.PRODUCT_SPECS["erp"]
    repo = _init_repo(tmp_path, "erp-clone")
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        requirements=_ERP_SATISFIED_REQUIREMENTS,
    )
    record["product"] = "dotmac_sub"
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "`product`" in outcome.problem


def test_subject_mismatch_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = gate.PRODUCT_SPECS["academy"]
    repo = _init_repo(tmp_path, "academy-clone")
    record = _record(
        repository=spec.repository,
        subject="a-different-subject",
        requirements=_ACADEMY_SATISFIED_REQUIREMENTS,
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    outcome = gate.fetch_readiness_record(spec, revision)
    assert outcome.problem_status == "evidence_incomplete"
    assert outcome.problem is not None
    assert "`subject`" in outcome.problem


def test_missing_requirement_id_refuses_as_evidence_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 2: a record that omits one of `PRODUCT_SPECS["sub"]`'s SELECTED
    ids refuses at `evidence_incomplete`, naming the absent id — never a
    silent pass and never `evaluation_refused` (that status is reserved for
    an id that IS present with `satisfied: false`)."""
    spec = gate.PRODUCT_SPECS["sub"]
    first_id = spec.requirements[0].requirement_id
    repo = _init_repo(tmp_path, "sub-clone")
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        # Only the first selected id — every other one Starter reads is
        # simply absent from this record.
        requirements=[_requirement(first_id, satisfied=True)],
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    result = gate.evaluate_sub(gate.ProductBinding(product="sub", revision=revision))
    assert result.compatibility_status == "evidence_incomplete"
    assert any("no requirement with id" in f for f in result.findings)
    missing_ids = {r.requirement_id for r in spec.requirements[1:]}
    joined = " ".join(result.findings)
    assert all(missing_id in joined for missing_id in missing_ids)


def test_a_requirement_present_but_not_satisfied_refuses_as_evaluation_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 6: a false ERP compatibility-role fact still refuses. The
    product-authored `satisfied: false` is read and trusted — this is
    exactly what "legitimate here, and only here" means: ERP's own CI wrote
    this false, and the gate reports it, never overrides it."""
    spec = gate.PRODUCT_SPECS["erp"]
    failing_id = next(
        r.requirement_id for r in spec.requirements if r.role == "compatibility"
    )
    requirements = [
        _requirement(r.requirement_id, satisfied=(r.requirement_id != failing_id))
        for r in spec.requirements
    ]
    repo = _init_repo(tmp_path, "erp-clone")
    record = _record(
        repository=spec.repository, subject=spec.subject, requirements=requirements
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    result = gate.evaluate_erp(gate.ProductBinding(product="erp", revision=revision))
    assert result.compatibility_status == "evaluation_refused"
    assert any("OBSERVED" in f and failing_id in f for f in result.findings)


def test_a_false_sub_compatibility_fact_also_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 6, Sub half: all eight of Sub's selected ids are
    `role="compatibility"`, so any one of them being `false` refuses —
    there is no adoption-state escape hatch for Sub."""
    spec = gate.PRODUCT_SPECS["sub"]
    failing_id = spec.requirements[-1].requirement_id
    requirements = [
        _requirement(r.requirement_id, satisfied=(r.requirement_id != failing_id))
        for r in spec.requirements
    ]
    repo = _init_repo(tmp_path, "sub-clone")
    record = _record(
        repository=spec.repository, subject=spec.subject, requirements=requirements
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    result = gate.evaluate_sub(gate.ProductBinding(product="sub", revision=revision))
    assert result.compatibility_status == "evaluation_refused"
    assert any("OBSERVED" in f and failing_id in f for f in result.findings)


def test_academy_adoption_debt_is_reported_but_never_refuses_compatibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 5, and the ruling's substantive verdict rule: Academy's five
    selected ids are ALL `role="adoption_state"`. A record where every one
    of them is `false` — exactly today's real Academy record shape — must
    still evaluate `satisfied` (never `evaluation_refused`), and every false
    value must be visible, labelled as adoption state, in `findings`."""
    spec = gate.PRODUCT_SPECS["academy"]
    requirements = [
        _requirement(r.requirement_id, satisfied=False) for r in spec.requirements
    ]
    repo = _init_repo(tmp_path, "academy-clone")
    record = _record(
        repository=spec.repository, subject=spec.subject, requirements=requirements
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    result = gate.evaluate_academy(
        gate.ProductBinding(product="academy", revision=revision)
    )
    assert result.compatibility_status == "satisfied", result.explain()
    joined = " ".join(result.findings)
    for r in spec.requirements:
        assert r.requirement_id in joined
    assert "ADOPTION STATE" in joined
    assert "OBSERVED" not in joined  # never reported as a compatibility refusal


def test_extra_product_owned_requirements_are_permitted_and_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 7: a record may carry requirement ids Starter does not select
    at all — those extra product-owned facts are simply never looked up,
    satisfied or not, and never affect the verdict."""
    spec = gate.PRODUCT_SPECS["sub"]
    requirements = [
        *_SUB_SATISFIED_REQUIREMENTS,
        _requirement(
            "an-extra-product-owned-fact-starter-never-reads", satisfied=False
        ),
    ]
    repo = _init_repo(tmp_path, "sub-clone")
    record = _record(
        repository=spec.repository, subject=spec.subject, requirements=requirements
    )
    revision = _commit_readiness_record(repo, record)
    _mark_as_protected_main(repo, revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    result = gate.evaluate_sub(gate.ProductBinding(product="sub", revision=revision))
    assert result.compatibility_status == "satisfied", result.explain()


def test_duplicate_selected_requirement_ids_refuse() -> None:
    """Guard 1: constructing a `ProductSpec` mapping that selects the same
    requirement id twice for one product is refused by
    `_duplicate_requirement_ids` — the exact function `PRODUCT_SPECS` itself
    is validated through at import time."""
    duplicated = {
        "academy": gate.ProductSpec(
            product="academy",
            repository="dotmac_academy_app",
            subject="academy-kernel-successor-readiness",
            clone_env_var="COMPAT_GATE_CLONE_DOTMAC_ACADEMY_APP",
            requirements=(
                gate.RequirementSpec("some-id", "adoption_state"),
                gate.RequirementSpec("some-id", "adoption_state"),
            ),
        ),
    }
    problems = gate._duplicate_requirement_ids(duplicated)
    assert problems == {"academy": ["some-id"]}


def test_the_real_product_specs_table_has_no_duplicate_selected_ids() -> None:
    """Admits control for guard 1: the real, checked-in `PRODUCT_SPECS` is
    duplicate-free (proven by successfully importing the module at all —
    the same check raises `RuntimeError` at import time otherwise)."""
    assert gate._duplicate_requirement_ids(gate.PRODUCT_SPECS) == {}


def test_requirement_spec_rejects_an_unknown_role() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        gate.RequirementSpec("some-id", "not-a-real-role")


def test_bindings_loader_refuses_a_requirements_field(tmp_path: Path) -> None:
    """Guard 3: a binding row cannot select or rename requirement ids —
    `requirements` is not among `ALLOWED_BINDING_ROW_KEYS`, so a row that
    tries to carry one is refused the same way an `evidence` row is."""
    bad = tmp_path / "bindings.json"
    bad.write_text(
        json.dumps(
            {
                "schema": "compatibility_gate_bindings_v1",
                "bindings": {
                    "erp": {
                        "revision": "a" * 40,
                        "requirements": ["single-sync-engine-construction-site"],
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError, match="unrecognised"):
        gate.load_default_bindings(bad)


def test_bindings_loader_refuses_a_role_or_disposition_field(tmp_path: Path) -> None:
    """Guard 5: a binding row cannot reclassify a requirement's disposition
    either — `role`/`disposition` are not among `ALLOWED_BINDING_ROW_KEYS`,
    so a row attempting to turn a compatibility requirement into an
    adoption-state observation (or the reverse) is refused the same way."""
    for bad_key in ("role", "disposition"):
        bad = tmp_path / f"bindings-{bad_key}.json"
        bad.write_text(
            json.dumps(
                {
                    "schema": "compatibility_gate_bindings_v1",
                    "bindings": {
                        "academy": {
                            "revision": "a" * 40,
                            bad_key: "compatibility",
                        }
                    },
                }
            )
        )
        with pytest.raises(ValueError, match="unrecognised"):
            gate.load_default_bindings(bad)


# ── Guard 4: no evaluator function may spell a requirement-id literal ──────

_REQUIREMENT_ID_SHAPE = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}\b")


def _requirement_id_shaped_literals_in_evaluators(source: str) -> list[tuple[str, str]]:
    """Parses `source`, walks every module-level function named
    `evaluate_*`, and returns `(function_name, literal)` for every string
    constant anywhere in that function's body (including nested f-string
    literal segments) that contains a hyphen-joined, 3+-token lowercase slug
    — the shape every real product requirement id uses. `PRODUCT_SPECS`
    itself lives outside any `evaluate_*` function, so a legitimate id
    declared there is never scanned; only a literal an evaluator spells
    itself is caught."""
    tree = ast.parse(source)
    hits: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("evaluate_"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    for match in _REQUIREMENT_ID_SHAPE.finditer(sub.value):
                        hits.append((node.name, match.group(0)))
    return hits


def test_no_evaluator_local_requirement_id_literals() -> None:
    """The real, checked-in `compatibility_gate.py`: zero requirement-id-
    shaped literals inside any `evaluate_*` function body. Every lookup goes
    through `_evaluate_readiness_requirements`, which reads
    `PRODUCT_SPECS[...].requirements` — a table declared OUTSIDE every
    evaluator function."""
    source = (Path(__file__).parent / "compatibility_gate.py").read_text()
    hits = _requirement_id_shaped_literals_in_evaluators(source)
    assert hits == [], (
        f"found requirement-id-shaped literal(s) inside an evaluate_* "
        f"function body, outside PRODUCT_SPECS: {hits!r}"
    )


def test_the_scan_bites_a_planted_hardcoded_requirement_id() -> None:
    """Sensitivity, defect half: plant a fresh `evaluate_*` function whose
    body hardcodes a requirement-id-shaped literal (exactly the third-layer
    regression guard 4 exists to catch) and confirm the scan names it."""
    planted = (
        "def evaluate_planted(binding):\n"
        "    entry = record.requirement('a-planted-hardcoded-requirement-id')\n"
        "    return entry\n"
    )
    hits = _requirement_id_shaped_literals_in_evaluators(planted)
    assert hits == [("evaluate_planted", "a-planted-hardcoded-requirement-id")]


def test_the_scan_does_not_bite_an_unrelated_short_or_underscored_literal() -> None:
    """Sensitivity, near-miss half: a two-token hyphenated phrase (below the
    3-token minimum any real requirement id has), a single status word, and
    an underscore-joined Python-symbol-shaped docstring mention must NOT be
    flagged — the scan targets the hyphenated, 3+-token requirement-id
    shape specifically, not every multi-word string in an evaluator."""
    near_miss = (
        "def evaluate_near_miss(binding):\n"
        "    '''Mentions bind_database_runtime and resolve_database_runtime.'''\n"
        "    status = 'evidence_incomplete'\n"
        "    note = 'kernel-side'\n"
        "    return status, note\n"
    )
    assert _requirement_id_shaped_literals_in_evaluators(near_miss) == []


# ── The digest: reproducible, and never authoritative as input ─────────────


def test_the_digest_is_reproducible_for_identical_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mirrors the real measurement that motivated this shape: two DIFFERENT
    commits carrying the IDENTICAL record bytes hash to the IDENTICAL
    digest. This is not a bug to guard against — it is exactly why the
    digest is reported as OUTPUT and never compared as authoritative input;
    see the module docstring."""
    spec = gate.PRODUCT_SPECS["sub"]
    repo = _init_repo(tmp_path, "sub-clone")
    record = _record(
        repository=spec.repository,
        subject=spec.subject,
        requirements=_SUB_SATISFIED_REQUIREMENTS,
    )
    first_revision = _commit_readiness_record(repo, record)
    # A second, unrelated commit that touches nothing in the record path.
    (repo / "unrelated.txt").write_text("noise\n")
    _git(repo, ["add", "."])
    _git(repo, ["commit", "-q", "-m", "unrelated change"])
    second_revision = _git_output(repo, ["rev-parse", "HEAD"]).strip()
    _mark_as_protected_main(repo, second_revision)
    monkeypatch.setenv(spec.clone_env_var, str(repo))

    first_outcome = gate.fetch_readiness_record(spec, first_revision)
    second_outcome = gate.fetch_readiness_record(spec, second_revision)
    assert first_revision != second_revision
    assert first_outcome.digest == second_outcome.digest
    assert first_outcome.digest is not None


# ── Kernel-side structural facts (unchanged by either ruling) ──────────────


def test_academy_reports_the_unpublished_session_local_finding_today() -> None:
    result = gate.evaluate_academy(gate.ProductBinding(product="academy"))
    joined = " ".join(result.findings)
    assert "SessionLocal" in joined
    assert "__all__" in joined


def test_the_session_local_finding_disappears_if_it_were_published(
    tmp_path: Path,
) -> None:
    """Sensitivity, near-miss half: plant a copy of `db.py` with
    `SessionLocal` added to `__all__` and confirm the finding is absent."""
    planted = tmp_path / "db.py"
    source = gate._DB_PATH.read_text()
    anchor = '    "tenant_session_by_slug",\n]'
    assert source.count(anchor) == 1, "db.py's __all__ list has changed shape"
    replacement = '    "tenant_session_by_slug",\n    "SessionLocal",\n]'
    planted.write_text(source.replace(anchor, replacement, 1))

    all_names = gate._module_all(planted)
    assert "SessionLocal" in all_names


def test_the_bare_assignment_check_stops_reporting_a_removed_attribute(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "db.py"
    source = gate._DB_PATH.read_text()
    anchor = "SessionLocal = runtime.session_factory\n"
    assert (
        source.count(anchor) == 1
    ), "db.py's SessionLocal assignment has changed shape"
    planted.write_text(source.replace(anchor, "", 1))

    bare_names = gate._module_level_bare_assignment_names(planted)
    assert "SessionLocal" not in bare_names


def test_erp_reports_the_kernel_is_sync_only_today() -> None:
    result = gate.evaluate_erp(gate.ProductBinding(product="erp"))
    joined = " ".join(result.findings)
    assert "sync-only" in joined
    assert "0 `async def`" in joined


def test_sub_reports_the_isolation_ordering_guarantee_holds_today() -> None:
    result = gate.evaluate_sub(gate.ProductBinding(product="sub"))
    joined = " ".join(result.findings)
    assert "applies `execution_options` before its own `yield`: True" in joined


def test_the_ordering_check_bites_a_reordered_isolated_session(tmp_path: Path) -> None:
    """Plant: reorder `_isolated_session` so `yield db` precedes the
    `execution_options` connection call. Admits control first."""
    source = gate._SESSION_RUNTIME_PATH.read_text()
    control_path = tmp_path / "control_session_runtime.py"
    control_path.write_text(source)
    assert gate._isolated_session_orders_execution_options_before_yield(
        control_path
    ), "control (unmutated) source did not report the ordering as holding"

    anchor = (
        "            db.connection(execution_options=_EXECUTION_OPTIONS[mode])\n"
        "            yield db\n"
    )
    assert source.count(anchor) == 1, "_isolated_session's body has changed shape"
    reordered = source.replace(
        anchor,
        "            yield db\n"
        "            db.connection(execution_options=_EXECUTION_OPTIONS[mode])\n",
        1,
    )
    planted_path = tmp_path / "planted_session_runtime.py"
    planted_path.write_text(reordered)

    assert not gate._isolated_session_orders_execution_options_before_yield(
        planted_path
    ), "the ordering check did not bite a reordered _isolated_session"


def test_sub_missing_boundary_method_is_reported(tmp_path: Path) -> None:
    source = gate._SESSION_RUNTIME_PATH.read_text()
    anchor = "def readonly_session(self)"
    assert source.count(anchor) == 1, "readonly_session's def line has changed shape"
    mutated = source.replace(anchor, "def _renamed_readonly_session(self)", 1)
    planted_path = tmp_path / "planted_session_runtime.py"
    planted_path.write_text(mutated)

    methods = gate._class_public_method_names(planted_path, "DatabaseRuntime")
    assert "readonly_session" not in methods


# ── The bindings-file loader: shape refusals ────────────────────────────────


def test_load_default_bindings_reads_the_checked_in_seam_file() -> None:
    """The checked-in file now binds the three real protected-`main`
    coordinates — every product's `revision` is a well-formed 40-hex commit."""
    bindings = gate.load_default_bindings()
    assert set(bindings) == set(gate.PRODUCTS)
    for product, binding in bindings.items():
        assert binding.product == product
        assert binding.revision is not None
        assert gate.IMMUTABLE_COMMIT.fullmatch(binding.revision)


def test_bindings_loader_refuses_an_undeclared_schema(tmp_path: Path) -> None:
    bad = tmp_path / "bindings.json"
    bad.write_text('{"schema": "v0", "bindings": {}}')
    with pytest.raises(ValueError, match="schema"):
        gate.load_default_bindings(bad)


def test_bindings_loader_refuses_an_unknown_product_key(tmp_path: Path) -> None:
    bad = tmp_path / "bindings.json"
    bad.write_text(
        '{"schema": "compatibility_gate_bindings_v1", '
        '"bindings": {"vendor_cp": {"revision": null}}}'
    )
    with pytest.raises(ValueError, match="unknown product"):
        gate.load_default_bindings(bad)


def test_bindings_loader_refuses_an_evidence_field() -> None:
    """The defect this replaces: a binding row could once carry an
    `evidence` object with caller-authored booleans that evaluated as
    satisfied. That field no longer exists in the schema; the loader refuses
    it outright rather than silently ignoring it."""
    payload = {
        "schema": "compatibility_gate_bindings_v1",
        "bindings": {
            "erp": {
                "revision": "a" * 40,
                "evidence": {"sync_requirements_satisfied": True},
            }
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bindings.json"
        bad.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="unrecognised"):
            gate.load_default_bindings(bad)


def test_bindings_loader_accepts_a_revision_only_row(tmp_path: Path) -> None:
    """The seam: editing only the JSON data file's `revision` field, with
    zero code changes, produces a bound (though not necessarily satisfied)
    evaluation — a real revision alone is not enough without a fetched
    record, and that is the point."""
    real = tmp_path / "bindings.json"
    real.write_text(
        json.dumps(
            {
                "schema": "compatibility_gate_bindings_v1",
                "bindings": {"academy": {"revision": "a" * 40}},
            }
        )
    )
    bindings = gate.load_default_bindings(real)
    assert bindings["academy"].revision == "a" * 40
    assert bindings["erp"].revision is None
    assert bindings["sub"].revision is None


# ── Docstring naming discipline: no dotted product-path strings ────────────


def test_the_module_names_no_literal_product_import_paths() -> None:
    """This module discusses Academy/ERP/Sub file locations in prose, never
    as dotted-import-looking string literals a content-scanning classifier
    could misread as this repository importing or depending on another
    product's module."""
    source = (Path(__file__).parent / "compatibility_gate.py").read_text()
    tree = ast.parse(source)
    suspicious = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("app.") and value.count(".") >= 2:
                suspicious.append(value)
    assert suspicious == [], (
        f"found dotted-import-looking string literal(s) that a "
        f"content-scanning classifier could misread as a dependency: "
        f"{suspicious!r}"
    )
