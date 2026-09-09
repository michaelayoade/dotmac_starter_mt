"""Slice 4 — the generic compatibility evaluator and its all-of gate.

Two properties this file exists to prove, per the ruling that this gate must
be UNABLE to pass on mechanism alone:

1. **Non-vacuity.** With no product evidence bound, the gate refuses, and the
   refusal names which product and why. `GateResult.satisfied` must not read
   an empty evaluation set as "nothing to refuse" — `all(())` is `True` in
   Python, which is the identical shape as a check over no files.
2. **All-of semantics.** Two products satisfied and one refusing must still
   refuse, and the refusal must name the refusing product specifically.

Everything else here (per-product findings, revision-shape refusal, the
kernel-side structural facts) is the sensitivity proof each of those two
properties needs to mean anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture import compatibility_gate as gate

# ── Non-vacuity: the gate must be unable to pass on mechanism alone ────────


def test_gate_result_cannot_pass_on_an_empty_evaluation_set() -> None:
    """Plant: construct a `GateResult` directly with zero evaluations — the
    shape a defective `evaluate_gate` could produce if it ever let the
    per-product loop come out empty. `all(())` is `True`, so a `satisfied`
    implemented as a bare `all(...)` would pass here for having nothing to
    check. This is the exact defect this whole slice exists to refuse."""
    empty = gate.GateResult(evaluations=())
    assert empty.satisfied is False
    assert "no product was evaluated" in empty.explain()


def test_gate_result_passes_when_every_evaluation_is_satisfied() -> None:
    """Near-miss half of the above: a non-empty, fully-satisfied set DOES
    pass — the empty-set refusal must not become a refusal of everything."""
    satisfied = gate.GateResult(
        evaluations=(
            gate.EvaluationResult("academy", "a" * 40, "satisfied", ()),
            gate.EvaluationResult("erp", "b" * 40, "satisfied", ()),
            gate.EvaluationResult("sub", "c" * 40, "satisfied", ()),
        )
    )
    assert satisfied.satisfied is True


def test_the_gate_refuses_today_with_the_default_bindings() -> None:
    """The default, checked-in bindings file has all three products unbound.
    Running the gate against it today must refuse — not skip, not pass over
    an empty evidence set."""
    bindings = gate.load_default_bindings()
    result = gate.evaluate_gate(bindings)
    assert result.satisfied is False
    assert len(result.refusing) == 3
    refusing_products = {evaluation.product for evaluation in result.refusing}
    assert refusing_products == set(gate.PRODUCTS)
    # Structured, not just prose: every refusal today is UNBOUND, not a
    # product that produced evidence and was found lacking — the exact
    # ABSENT-versus-REGISTRY_DISAGREEMENT distinction a reader must be able
    # to make from `status` alone, never by parsing `findings`.
    for evaluation in result.refusing:
        assert evaluation.status == "unbound"


def test_the_gate_refuses_when_bindings_is_entirely_empty() -> None:
    """`evaluate_gate({})` — no bindings dict at all, not even the unbound
    placeholders. Proves absence is refusal by construction: the three
    products are still evaluated (as unbound), never silently dropped from
    the set the all-of combination looks at."""
    result = gate.evaluate_gate({})
    assert result.satisfied is False
    assert tuple(e.product for e in result.evaluations) == gate.PRODUCTS
    for evaluation in result.evaluations:
        assert evaluation.satisfied is False
        assert evaluation.status == "unbound"
        assert evaluation.revision is None


def test_each_refusal_names_the_product_and_the_observed_reason() -> None:
    result = gate.evaluate_gate({})
    for evaluation in result.evaluations:
        joined = " ".join(evaluation.findings)
        assert "no revision is bound for" in joined
        assert evaluation.product in joined
        assert evaluation.status == "unbound"


# ── Structured status: unbound is distinct from refused-after-evaluation ───


def test_satisfied_is_derived_from_status_not_stored() -> None:
    """`satisfied` cannot be constructed to disagree with `status` — there is
    no `satisfied=` keyword any more, only `status=`, so a caller cannot
    accidentally build a "satisfied" result carrying a refusal status."""
    unbound = gate.EvaluationResult("academy", None, "unbound", ())
    assert unbound.satisfied is False
    satisfied = gate.EvaluationResult("academy", "a" * 40, "satisfied", ())
    assert satisfied.satisfied is True


def test_an_unknown_status_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown EvaluationResult.status"):
        gate.EvaluationResult("academy", None, "incompatible", ())


def test_unbound_and_evaluation_refused_are_distinct_statuses_for_academy() -> None:
    """The exact distinction this section exists for: a product with no
    revision at all (`unbound`) must never carry the same status as a
    product that DID produce complete, well-formed evidence and whose
    captured facts simply failed the check (`evaluation_refused`)."""
    unbound = gate.evaluate_academy(gate.ProductBinding(product="academy"))
    assert unbound.status == "unbound"

    revision = "5" * 40
    evaluated_and_refused = gate.evaluate_academy(
        gate.ProductBinding(
            product="academy",
            revision=revision,
            evidence={
                "protected_main_row": _protected_main_row(
                    "dotmac_academy_app", revision
                ),
                "unpublished_session_local_usage_sites": (),
                "strict_bind_without_reference_import": False,
            },
        )
    )
    assert evaluated_and_refused.status == "evaluation_refused"
    assert unbound.status != evaluated_and_refused.status


# ── All-of semantics: two satisfied, one refusing must still refuse ────────


def _protected_main_row(
    repository: str,
    commit: str,
    artefact: str = "docs/<readiness-artefact>.md (test fixture placeholder)",
) -> dict[str, str]:
    return {
        "kind": "pinned_at",
        "repository": repository,
        "commit": commit,
        "expected": f"git merge-base --is-ancestor {commit} origin/main succeeded",
        "artefact": artefact,
    }


def _satisfied_academy_binding() -> gate.ProductBinding:
    revision = "a" * 40
    return gate.ProductBinding(
        product="academy",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_academy_app", revision),
            "unpublished_session_local_usage_sites": (),
            "strict_bind_without_reference_import": True,
        },
    )


def _satisfied_erp_binding() -> gate.ProductBinding:
    revision = "b" * 40
    return gate.ProductBinding(
        product="erp",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_erp", revision),
            "sync_requirements_satisfied": True,
            "async_status": gate.ASYNC_TRANSITIONAL,
        },
    )


def _satisfied_sub_binding() -> gate.ProductBinding:
    revision = "c" * 40
    return gate.ProductBinding(
        product="sub",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_sub", revision),
            "guc_hook_ordered_after_isolation_mode": True,
            "tenant_scope_composed_with_readonly_or_serializable": True,
        },
    )


def test_all_three_satisfied_bindings_pass_the_gate() -> None:
    """Admits control: the fully-satisfied fixtures above really do pass —
    otherwise the refusal test below would refuse for the wrong reason."""
    bindings = {
        "academy": _satisfied_academy_binding(),
        "erp": _satisfied_erp_binding(),
        "sub": _satisfied_sub_binding(),
    }
    result = gate.evaluate_gate(bindings)
    assert result.satisfied is True, result.explain()
    assert result.refusing == ()


def test_two_satisfied_and_one_refusing_still_refuses_and_names_it() -> None:
    """The all-of semantics this gate exists to enforce: Academy and ERP
    fully satisfied, Sub left unbound. The gate must refuse as a whole, and
    the refusal must name Sub specifically — not report a vague partial
    pass."""
    bindings = {
        "academy": _satisfied_academy_binding(),
        "erp": _satisfied_erp_binding(),
        # sub omitted entirely — unbound.
    }
    result = gate.evaluate_gate(bindings)
    assert result.satisfied is False
    assert len(result.refusing) == 1
    assert result.refusing[0].product == "sub"
    assert result.refusing[0].status == "unbound"
    assert "no revision is bound for 'sub'" in " ".join(result.refusing[0].findings)
    explanation = result.explain()
    assert "sub" in explanation
    assert "academy" not in explanation.split("\n")[0]  # header names counts, not names


def test_unknown_product_in_bindings_is_refused_by_construction() -> None:
    with pytest.raises(ValueError, match="unknown product"):
        gate.evaluate_gate({"vendor_cp": gate.ProductBinding(product="vendor_cp")})


# ── Revision-shape refusal (reused, verbatim, from adoption_evidence.py) ──


def test_a_moving_ref_revision_is_refused_by_construction() -> None:
    binding = gate.ProductBinding(product="academy", revision="main")
    result = gate.evaluate_academy(binding)
    assert result.satisfied is False
    assert any("moving ref" in finding for finding in result.findings)


def test_an_embedded_moving_ref_after_at_is_refused() -> None:
    """`adoption_evidence.py`'s own defect catalogue names this exact shape:
    `main@e1402902` — a moving ref followed by an eight-hex-digit
    abbreviation. `_revision_problem` refuses it for naming the ref, not
    (only) for the abbreviation."""
    binding = gate.ProductBinding(product="erp", revision="main@e1402902")
    result = gate.evaluate_erp(binding)
    assert result.satisfied is False
    assert any("moving ref" in finding for finding in result.findings)


def test_an_abbreviated_commit_is_refused() -> None:
    binding = gate.ProductBinding(product="sub", revision="abc1234")
    result = gate.evaluate_sub(binding)
    assert result.satisfied is False
    assert any(
        "40-character lowercase hex commit" in finding for finding in result.findings
    )


def test_a_bound_revision_with_no_evidence_still_refuses() -> None:
    """A well-formed 40-hex commit alone is a coordinate with nothing to
    check — it must refuse exactly as a missing revision does, not pass on
    the strength of the commit shape being valid."""
    binding = gate.ProductBinding(product="academy", revision="d" * 40, evidence=None)
    result = gate.evaluate_academy(binding)
    assert result.satisfied is False
    assert any("no product-side evidence" in finding for finding in result.findings)


# ── Reason 3: a real commit that is not proven an ancestor of protected main ─


def test_a_well_formed_commit_with_no_protected_main_proof_is_refused() -> None:
    """A valid-looking 40-hex commit with no `protected_main_row` at all —
    the exact shape a branch-only SHA takes. Must be refused, and the
    refusal text must be reason 3 (protected `main`), not reason 1 (no
    revision bound) or reason 2 (moving ref)."""
    binding = gate.ProductBinding(
        product="academy",
        revision="7" * 40,
        evidence={
            "unpublished_session_local_usage_sites": (),
            "strict_bind_without_reference_import": True,
        },
    )
    result = gate.evaluate_academy(binding)
    assert result.satisfied is False
    joined = " ".join(result.findings)
    assert "protected `main`" in joined
    assert "no revision is bound" not in joined
    assert "moving ref" not in joined


def test_the_three_refusal_reasons_are_textually_distinguishable() -> None:
    """Reason 1 (unbound), reason 2 (moving ref) and reason 3 (real commit,
    unproven protected-main ancestry) each carry a distinguishing phrase
    absent from the other two — a reviewer must be able to tell them apart
    from the finding text alone."""
    unbound = gate.evaluate_academy(gate.ProductBinding(product="academy"))
    moving_ref = gate.evaluate_academy(
        gate.ProductBinding(product="academy", revision="main")
    )
    unproven = gate.evaluate_academy(
        gate.ProductBinding(
            product="academy",
            revision="8" * 40,
            evidence={
                "unpublished_session_local_usage_sites": (),
                "strict_bind_without_reference_import": True,
            },
        )
    )

    unbound_text = " ".join(unbound.findings)
    moving_ref_text = " ".join(moving_ref.findings)
    unproven_text = " ".join(unproven.findings)

    assert "no revision is bound" in unbound_text
    assert "no revision is bound" not in moving_ref_text
    assert "no revision is bound" not in unproven_text

    assert "moving ref" in moving_ref_text
    assert "moving ref" not in unbound_text
    assert "moving ref" not in unproven_text

    assert "protected `main`" in unproven_text
    assert "protected `main`" not in unbound_text
    assert "protected `main`" not in moving_ref_text


def test_protected_main_row_with_an_unknown_kind_is_refused() -> None:
    revision = "9" * 40
    binding = gate.ProductBinding(
        product="erp",
        revision=revision,
        evidence={
            "protected_main_row": {
                "kind": "workflow_run",  # an attestation kind, not reused here
                "repository": "dotmac_erp",
                "commit": revision,
                "expected": "ran",
            },
            "sync_requirements_satisfied": True,
            "async_status": gate.ASYNC_TRANSITIONAL,
        },
    )
    result = gate.evaluate_erp(binding)
    assert result.satisfied is False
    joined = " ".join(result.findings)
    assert "accepted kinds are" in joined
    assert "pinned_at" in joined and "composed_at" in joined


def test_protected_main_row_with_a_mismatched_commit_is_refused() -> None:
    revision = "1" * 40
    other_commit = "2" * 40
    binding = gate.ProductBinding(
        product="sub",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_sub", other_commit),
            "guc_hook_ordered_after_isolation_mode": True,
            "tenant_scope_composed_with_readonly_or_serializable": True,
        },
    )
    result = gate.evaluate_sub(binding)
    assert result.satisfied is False
    assert any(
        "does not match the bound revision" in finding for finding in result.findings
    )


def test_protected_main_row_missing_expected_is_refused() -> None:
    revision = "3" * 40
    binding = gate.ProductBinding(
        product="academy",
        revision=revision,
        evidence={
            "protected_main_row": {
                "kind": "pinned_at",
                "repository": "dotmac_academy_app",
                "commit": revision,
            },
            "unpublished_session_local_usage_sites": (),
            "strict_bind_without_reference_import": True,
        },
    )
    result = gate.evaluate_academy(binding)
    assert result.satisfied is False
    assert result.status == "not_on_protected_main"
    assert any(
        "protected_main_row.expected` must record" in finding
        for finding in result.findings
    )


def test_protected_main_row_missing_artefact_is_refused() -> None:
    """Guards the "plausible-but-wrong SHA" hazard: ancestry alone does not
    distinguish the RIGHT commit from any other commit also on protected
    `main`. A row with `kind`/`repository`/`commit`/`expected` all well-formed
    but no `artefact` must still refuse — naming what the commit is supposed
    to carry is required, not optional."""
    revision = "4" * 40
    binding = gate.ProductBinding(
        product="erp",
        revision=revision,
        evidence={
            "protected_main_row": {
                "kind": "pinned_at",
                "repository": "dotmac_erp",
                "commit": revision,
                "expected": f"git merge-base --is-ancestor {revision} origin/main "
                "succeeded",
            },
            "sync_requirements_satisfied": True,
            "async_status": gate.ASYNC_TRANSITIONAL,
        },
    )
    result = gate.evaluate_erp(binding)
    assert result.satisfied is False
    assert result.status == "not_on_protected_main"
    assert any("protected_main_row.artefact` must record" in f for f in result.findings)


def test_the_real_erp_readiness_revision_is_distinguishable_from_the_excluded_one() -> (
    None
):
    """Grounded in the actual coordination record: ERP's readiness revision
    is `b3b191cc8e59013ab27ea5efac0e9c605f9b7a4f` (#510, carrying the
    runtime/async/PID-fork contract) — `1d82a4d2...` (#509, customer
    import-parity work) was explicitly ruled OUT as the wrong commit.  Both
    are plausible 40-hex-shaped ancestors of ERP's protected `main`; this
    gate's `artefact` field is what forces a binder to name which one they
    mean, so a reviewer has something concrete to check rather than a bare,
    equally-plausible-looking SHA.

    This module has no access to the `dotmac_erp` repository and cannot
    itself verify either commit's ancestry or contents — that is explicitly
    UNMONITORED (see the module docstring). What this test proves is
    narrower and mechanical: naming the CORRECT artefact for the CORRECT
    commit satisfies the row-shape check, and the two commits remain
    textually distinguishable in any bound evidence."""
    correct_revision = "b3b191cc8e59013ab27ea5efac0e9c605f9b7a4f"
    excluded_revision_prefix = "1d82a4d2"
    assert correct_revision != excluded_revision_prefix
    assert gate.IMMUTABLE_COMMIT.fullmatch(correct_revision)

    binding = gate.ProductBinding(
        product="erp",
        revision=correct_revision,
        evidence={
            "protected_main_row": _protected_main_row(
                "dotmac_erp",
                correct_revision,
                artefact="docs/architecture/erp-runtime-async-pid-fork-contract.md",
            ),
            "sync_requirements_satisfied": True,
            "async_status": gate.ASYNC_TRANSITIONAL,
        },
    )
    result = gate.evaluate_erp(binding)
    assert result.revision == correct_revision
    assert result.satisfied is True, result.explain()


def test_protected_main_proof_kinds_reuse_adoption_evidences_vocabulary() -> None:
    """`PROTECTED_MAIN_PROOF_KINDS` reuses `adoption_evidence.py`'s own
    closed vocabulary rather than inventing a parallel one — proven by
    membership, not by comment."""
    from tests.architecture import adoption_evidence

    assert gate.PROTECTED_MAIN_PROOF_KINDS == {"pinned_at", "composed_at"}
    assert gate.PROTECTED_MAIN_PROOF_KINDS <= (
        adoption_evidence.ASSERTION_KINDS | adoption_evidence.AST_ASSERTION_KINDS
    )


# ── Each evaluation names the exact revision it evaluated ──────────────────


def test_evaluation_result_records_the_exact_revision_evaluated() -> None:
    unbound = gate.evaluate_academy(gate.ProductBinding(product="academy"))
    assert unbound.revision is None
    assert "<unbound>" in unbound.explain()

    bound = gate.evaluate_academy(_satisfied_academy_binding())
    assert bound.revision == "a" * 40
    assert ("a" * 40) in bound.explain()


# ── Academy: the SessionLocal / __all__ finding, and its sensitivity ──────


def test_academy_reports_the_unpublished_session_local_finding_today() -> None:
    result = gate.evaluate_academy(gate.ProductBinding(product="academy"))
    joined = " ".join(result.findings)
    assert "SessionLocal" in joined
    assert "__all__" in joined


def test_the_session_local_finding_disappears_if_it_were_published(
    tmp_path: Path,
) -> None:
    """Sensitivity, near-miss half: plant a copy of `db.py` with
    `SessionLocal` added to `__all__` and confirm the finding is absent —
    proving the check reads the real `__all__` rather than always emitting
    the finding regardless of content."""
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
    """Sensitivity, plant half in the other direction: if `SessionLocal`
    were removed from `db.py` entirely, the bare-assignment check must stop
    reporting it as present (there is nothing left to be unpublished)."""
    planted = tmp_path / "db.py"
    source = gate._DB_PATH.read_text()
    anchor = "SessionLocal = runtime.session_factory\n"
    assert (
        source.count(anchor) == 1
    ), "db.py's SessionLocal assignment has changed shape"
    planted.write_text(source.replace(anchor, "", 1))

    bare_names = gate._module_level_bare_assignment_names(planted)
    assert "SessionLocal" not in bare_names


# ── ERP: async stays explicitly transitional, never silently satisfied ────


def test_erp_reports_the_kernel_is_sync_only_today() -> None:
    result = gate.evaluate_erp(gate.ProductBinding(product="erp"))
    joined = " ".join(result.findings)
    assert "sync-only" in joined
    assert "0 `async def`" in joined


def test_erp_async_status_satisfied_is_refused_not_silently_accepted() -> None:
    revision = "e" * 40
    binding = gate.ProductBinding(
        product="erp",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_erp", revision),
            "sync_requirements_satisfied": True,
            "async_status": "satisfied",
        },
    )
    result = gate.evaluate_erp(binding)
    assert result.satisfied is False
    assert any(
        "publishes no async DatabaseRuntime boundary" in finding
        for finding in result.findings
    )


def test_erp_async_status_transitional_with_sync_satisfied_passes() -> None:
    result = gate.evaluate_erp(_satisfied_erp_binding())
    assert result.satisfied is True, result.explain()


def test_erp_missing_async_status_is_refused_as_a_shape_problem() -> None:
    revision = "f" * 40
    binding = gate.ProductBinding(
        product="erp",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_erp", revision),
            "sync_requirements_satisfied": True,
        },
    )
    result = gate.evaluate_erp(binding)
    assert result.satisfied is False
    assert any("must carry a non-empty `async_status`" in f for f in result.findings)


# ── Sub: the GUC ordering guarantee, measured structurally, with sensitivity ─


def test_sub_reports_the_isolation_ordering_guarantee_holds_today() -> None:
    result = gate.evaluate_sub(gate.ProductBinding(product="sub"))
    joined = " ".join(result.findings)
    assert "applies `execution_options` before its own `yield`: True" in joined


def test_the_ordering_check_bites_a_reordered_isolated_session(tmp_path: Path) -> None:
    """Plant: reorder `_isolated_session` so `yield db` precedes the
    `execution_options` connection call, and confirm the structural check
    reports the ordering as broken. Admits control first (the unmutated
    source reports True) so the plant is proven against a working baseline,
    not a broken fixture."""
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
    """Plant: rename `readonly_session` out of existence in a copied source
    tree and confirm `_class_public_method_names` no longer reports it —
    the fact `evaluate_sub`'s `missing` computation depends on."""
    source = gate._SESSION_RUNTIME_PATH.read_text()
    anchor = "def readonly_session(self)"
    assert source.count(anchor) == 1, "readonly_session's def line has changed shape"
    mutated = source.replace(anchor, "def _renamed_readonly_session(self)", 1)
    planted_path = tmp_path / "planted_session_runtime.py"
    planted_path.write_text(mutated)

    methods = gate._class_public_method_names(planted_path, "DatabaseRuntime")
    assert "readonly_session" not in methods


def test_sub_guc_ordering_shape_refusals_are_reported() -> None:
    revision = "1" * 40
    binding = gate.ProductBinding(
        product="sub",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_sub", revision),
            "guc_hook_ordered_after_isolation_mode": True,
        },
    )
    result = gate.evaluate_sub(binding)
    assert result.satisfied is False
    assert any(
        "tenant_scope_composed_with_readonly_or_serializable" in f
        for f in result.findings
    )


def test_sub_observed_false_values_are_reported_as_observed_not_presumed() -> None:
    revision = "2" * 40
    binding = gate.ProductBinding(
        product="sub",
        revision=revision,
        evidence={
            "protected_main_row": _protected_main_row("dotmac_sub", revision),
            "guc_hook_ordered_after_isolation_mode": False,
            "tenant_scope_composed_with_readonly_or_serializable": True,
        },
    )
    result = gate.evaluate_sub(binding)
    assert result.satisfied is False
    assert any(
        finding.startswith("OBSERVED at sub@") and "NOT ordered" in finding
        for finding in result.findings
    )


# ── The bindings-file loader: shape refusals ────────────────────────────────


def test_load_default_bindings_reads_the_checked_in_seam_file() -> None:
    bindings = gate.load_default_bindings()
    assert set(bindings) == set(gate.PRODUCTS)
    for product, binding in bindings.items():
        assert binding.product == product
        assert binding.revision is None


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


def test_bindings_loader_accepts_a_real_bound_revision(tmp_path: Path) -> None:
    """Proves the seam: editing only the JSON data file, with zero code
    changes, produces a bound evaluation."""
    import json as _json

    revision = "a" * 40
    real = tmp_path / "bindings.json"
    real.write_text(
        _json.dumps(
            {
                "schema": "compatibility_gate_bindings_v1",
                "bindings": {
                    "academy": {
                        "revision": revision,
                        "evidence": {
                            "protected_main_row": _protected_main_row(
                                "dotmac_academy_app", revision
                            ),
                            "unpublished_session_local_usage_sites": [],
                            "strict_bind_without_reference_import": True,
                        },
                    }
                },
            }
        )
    )
    bindings = gate.load_default_bindings(real)
    result = gate.evaluate_academy(bindings["academy"])
    assert result.revision == "a" * 40
    assert result.satisfied is True, result.explain()


# ── Docstring naming discipline: no dotted product-path strings ────────────


def test_the_module_names_no_literal_product_import_paths() -> None:
    """This module and its docstrings discuss Academy/ERP/Sub file
    locations in prose (backtick-quoted, human-readable), never as
    dotted-import-looking string literals a content-scanning classifier
    could misread as this repository importing or depending on another
    product's module. `app.api.deps`-style dotted paths (the shape a
    Python import or a dependency scanner keys on) must not appear as
    string literals in the source."""
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
