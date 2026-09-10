"""Tests for the dimensional composition schema (`composition_schema.py`).

Every test proves a specific ruled property from the brief; see each
docstring for which one. Static analysis only — no pytest execution here,
just as the constraints require; these are written to be run by CI, not by
this session.
"""

from __future__ import annotations

import inspect

import pytest

from tests.architecture import composition_schema as schema
from tests.architecture.composition_schema import (
    CompositionCoverageReport,
    CompositionRecord,
    CompositionState,
    DimensionalIncoherence,
    DimensionValue,
    IncompatibleSchemaVersion,
    PackageClassification,
    RegistrationCallSite,
    RegistrationEvidence,
    RegistrationEvidenceKind,
    RuntimeExposureReport,
    build_coverage_report,
    build_runtime_exposure_report,
    classify_registration_call_site,
    composition_record_from_payload,
    derive_composition_state,
)

TRUE = DimensionValue.TRUE
FALSE = DimensionValue.FALSE
UNKNOWN = DimensionValue.UNKNOWN
NA = DimensionValue.NOT_APPLICABLE


def _optional_module_record(
    *,
    installation=TRUE,
    module_registration=TRUE,
    migration_lineage=TRUE,
    runtime_consumption=UNKNOWN,
) -> CompositionRecord:
    return CompositionRecord(
        product="erp",
        distribution="dotmac-example",
        classification=PackageClassification.OPTIONAL_MODULE,
        installation=installation,
        module_registration=module_registration,
        migration_lineage=migration_lineage,
        runtime_consumption=runtime_consumption,
    )


def _baseline_record(
    *, classification, installation=TRUE, runtime_consumption=UNKNOWN
) -> CompositionRecord:
    return CompositionRecord(
        product="sub",
        distribution="dotmac-kernel",
        classification=classification,
        installation=installation,
        module_registration=NA,
        migration_lineage=NA,
        runtime_consumption=runtime_consumption,
    )


# ---------------------------------------------------------------------------
# 1. The derivation table, exhaustively.
# ---------------------------------------------------------------------------


def test_derivation_table_for_optional_module_is_exhaustive_and_hand_checked():
    """Every (installation, module_registration, migration_lineage) combination
    for a classification where both dimensions apply (`optional-module`) maps
    to exactly one outcome — a `CompositionState` or a refused
    `DimensionalIncoherence`. The expected outcome is written by hand here —
    not derived from the implementation — so this is a real check, not a
    tautology. `runtime_consumption` is held fixed at `unknown` throughout —
    that value is itself asserted rather than exercised by this table (see
    `test_installation_absent_accepts_unknown_runtime_consumption` for that
    exact case, reasoned about on its own); the `runtime_consumption = true`
    contradiction axis is not exercised here at all and is covered by the
    dedicated contradiction tests below instead.

    Hand-derived rule, matching the pinned pipeline order exactly:

    * any of the three is `unknown` -> `evidence_incomplete` (step 2 fires
      regardless of what `installation` is, since step 2 precedes both the
      contradiction check and the installation-absent derivation).
    * else, `installation == false`:
        * `registration == true` or `lineage == true` -> refused
          (`DimensionalIncoherence`, step 3).
        * otherwise (`registration == false` and `lineage == false`) ->
          `not_composed` (step 4).
    * else (`installation == true`): the ruled registration/lineage table
      (step 6; step 5 never fires for `optional-module`).
    """
    dims = (TRUE, FALSE, UNKNOWN)
    seen = 0
    for installation in dims:
        for registration in dims:
            for lineage in dims:
                seen += 1
                record = _optional_module_record(
                    installation=installation,
                    module_registration=registration,
                    migration_lineage=lineage,
                )
                case = (
                    f"installation={installation}, "
                    f"module_registration={registration}, "
                    f"migration_lineage={lineage}"
                )

                if UNKNOWN in (installation, registration, lineage):
                    assert (
                        derive_composition_state(record)
                        == CompositionState.EVIDENCE_INCOMPLETE
                    ), case
                elif installation == FALSE:
                    if registration == TRUE or lineage == TRUE:
                        with pytest.raises(DimensionalIncoherence):
                            derive_composition_state(record)
                    else:
                        assert registration == FALSE and lineage == FALSE
                        assert (
                            derive_composition_state(record)
                            == CompositionState.NOT_COMPOSED
                        ), case
                else:
                    assert installation == TRUE
                    if registration == TRUE and lineage == TRUE:
                        expected_state = CompositionState.FULLY_COMPOSED
                    elif registration == FALSE and lineage == TRUE:
                        expected_state = CompositionState.LINEAGE_ONLY
                    elif registration == TRUE and lineage == FALSE:
                        expected_state = CompositionState.INVALID
                    else:
                        assert registration == FALSE and lineage == FALSE
                        expected_state = CompositionState.NOT_COMPOSED
                    assert derive_composition_state(record) == expected_state, case

    assert seen == 27, "hand table must cover all 3x3x3 combinations"


def test_derivation_table_for_platform_baseline_classifications():
    """Both platform-baseline classifications (`universal-facility`,
    `presentation-foundation`) always carry `module_registration =
    migration_lineage = not_applicable`; state derives from `installation`
    alone: unknown -> evidence_incomplete, false -> not_composed, true ->
    not_applicable (the composition question itself does not apply)."""
    for classification in (
        PackageClassification.UNIVERSAL_FACILITY,
        PackageClassification.PRESENTATION_FOUNDATION,
    ):
        assert (
            derive_composition_state(
                _baseline_record(classification=classification, installation=UNKNOWN)
            )
            == CompositionState.EVIDENCE_INCOMPLETE
        )
        assert (
            derive_composition_state(
                _baseline_record(classification=classification, installation=FALSE)
            )
            == CompositionState.NOT_COMPOSED
        )
        assert (
            derive_composition_state(
                _baseline_record(classification=classification, installation=TRUE)
            )
            == CompositionState.NOT_APPLICABLE
        )


def test_unknown_in_any_required_dimension_yields_evidence_incomplete():
    """Direct, minimal statement of the ruled property, independent of the
    full table above."""
    assert (
        derive_composition_state(_optional_module_record(installation=UNKNOWN))
        == CompositionState.EVIDENCE_INCOMPLETE
    )
    assert (
        derive_composition_state(_optional_module_record(module_registration=UNKNOWN))
        == CompositionState.EVIDENCE_INCOMPLETE
    )
    assert (
        derive_composition_state(_optional_module_record(migration_lineage=UNKNOWN))
        == CompositionState.EVIDENCE_INCOMPLETE
    )


def test_not_composed_only_reached_when_coverage_actually_proved_absence():
    """`not_composed` (neither registration nor lineage) is only reachable
    when BOTH were actually measured false — an unknown short-circuits to
    evidence_incomplete before the "neither" branch is ever considered, so a
    record can never claim not_composed for a dimension nobody checked."""
    incomplete = _optional_module_record(
        module_registration=UNKNOWN, migration_lineage=FALSE
    )
    assert derive_composition_state(incomplete) == CompositionState.EVIDENCE_INCOMPLETE

    genuinely_absent = _optional_module_record(
        module_registration=FALSE, migration_lineage=FALSE
    )
    assert derive_composition_state(genuinely_absent) == CompositionState.NOT_COMPOSED


# ---------------------------------------------------------------------------
# 1b. The derivation ORDER is pinned by tests, not just its outcomes.
# ---------------------------------------------------------------------------


def test_missing_installation_with_runtime_evidence_is_refused_not_not_composed():
    """Consequence test (coordinator correction): a distribution reporting
    `installation = false` alongside `runtime_consumption = true` is the
    sharpest form of the contradiction — a not-installed distribution
    cannot show real runtime consumption, so this is either a measurement
    error or a genuine hazard, and the schema must say so by refusing
    rather than filing it as `not_composed`."""
    hazard = CompositionRecord(
        product="hazard-probe",
        distribution="dotmac-example",
        classification=PackageClassification.OPTIONAL_MODULE,
        installation=FALSE,
        module_registration=FALSE,
        migration_lineage=FALSE,
        runtime_consumption=TRUE,
    )
    with pytest.raises(DimensionalIncoherence, match="runtime_consumption"):
        derive_composition_state(hazard)


def test_installation_absent_accepts_unknown_runtime_consumption():
    """Named on its own, not a by-product of the 27-case table (which holds
    `runtime_consumption` fixed at `unknown` throughout and so asserts this
    case rather than exercising it as a choice).

    `installation = false`, `module_registration = false`,
    `migration_lineage = false`, and `runtime_consumption = unknown` reaches
    step 4 (`_step_installation_absent`) with three MEASURED negatives and
    one UNMEASURED dimension — not four measured negatives. Step 3 only
    refuses a dimension reporting `true`; `runtime_consumption` is never a
    required dimension, so step 2 does not refuse its `unknown` either. The
    pipeline still derives `not_composed`: an unmeasured runtime signal on
    an already-confirmed-absent installation is not itself grounds to block
    the verdict, because `runtime_consumption` participates in composition-
    state derivation only as a contradiction canary (step 3), never as a
    fourth vote in the installation-absent/not-composed judgement. This is
    a deliberate choice about what step 4 requires, not an accident of the
    hand table holding the axis fixed."""
    record = _optional_module_record(
        installation=FALSE,
        module_registration=FALSE,
        migration_lineage=FALSE,
        runtime_consumption=UNKNOWN,
    )
    assert derive_composition_state(record) == CompositionState.NOT_COMPOSED


def test_optional_module_with_no_registration_mechanism_never_derives_not_applicable():
    """Consequence test: an `optional-module` distribution with no
    registration mechanism records `module_registration = false`
    ("absent"), and the pinned ordering can never reach `not_applicable`
    for it — `_step_not_applicable` itself refuses to fire for a
    classification where the dimensions apply, regardless of where it sits
    in the pipeline."""
    record = _optional_module_record(module_registration=FALSE, migration_lineage=FALSE)
    state = derive_composition_state(record)
    assert state == CompositionState.NOT_COMPOSED
    assert state != CompositionState.NOT_APPLICABLE
    # The not_applicable step itself, run in isolation, defers — it cannot
    # produce not_applicable for a classification where the dimensions apply.
    assert schema._step_not_applicable(record) is None


def test_universal_facility_with_inapplicable_dims_derives_not_applicable():
    """Consequence test: a `universal-facility` distribution — inapplicable
    `module_registration`/`migration_lineage` by classification — derives
    `not_applicable` once it is confirmed installed and nothing else
    blocks the pipeline."""
    record = _baseline_record(
        classification=PackageClassification.UNIVERSAL_FACILITY, installation=TRUE
    )
    assert derive_composition_state(record) == CompositionState.NOT_APPLICABLE


def test_ordering_pins_unknown_refusal_ahead_of_contradiction_check():
    """`installation=false` (a step-3 contradiction trigger, paired with
    `module_registration=true`) together with `migration_lineage=unknown`
    (a step-2 trigger) has exactly one correct answer under the ruled
    order (step 2 before step 3): `evidence_incomplete`. Under the REVERSE
    order this same input would instead raise `DimensionalIncoherence` —
    so this single input distinguishes the two orders directly, rather
    than merely checking an outcome multiple orders could reach."""
    record = _optional_module_record(
        installation=FALSE, module_registration=TRUE, migration_lineage=UNKNOWN
    )
    assert derive_composition_state(record) == CompositionState.EVIDENCE_INCOMPLETE


def test_ordering_pins_contradiction_check_ahead_of_installation_absent_derivation():
    """`installation=false` with `module_registration=true` and
    `migration_lineage=false` (no unknowns at all) triggers ONLY the
    step-3 contradiction condition. Under the ruled order (3 before 4)
    this raises; under the REVERSE order step 4 would fire first and
    unconditionally return `not_composed`, swallowing the contradiction —
    this is the exact defect the coordinator's correction targets."""
    record = _optional_module_record(
        installation=FALSE, module_registration=TRUE, migration_lineage=FALSE
    )
    with pytest.raises(DimensionalIncoherence):
        derive_composition_state(record)


def test_shipped_pipeline_order_matches_the_ruled_sequence():
    """Pins the ordering as an artifact, not just as a property of scattered
    outcomes: the exact sequence of step functions `derive_composition_state`
    runs, by name, in order."""
    assert [step.__name__ for step in schema._DERIVATION_PIPELINE] == [
        "_step_validate_coherence",
        "_step_refuse_required_unknown",
        "_step_refuse_contradictions",
        "_step_installation_absent",
        "_step_not_applicable",
        "_step_registration_lineage_table",
    ]


def test_pipeline_ordering_is_pinned_not_incidental():
    """The strongest form of the ordering proof: build the SAME step
    functions the module ships, but with `_step_installation_absent` moved
    ahead of `_step_refuse_contradictions` (exactly the bug the coordinator
    named). For the genuinely contradictory record above, the shipped
    pipeline refuses it while this reordered pipeline silently derives
    `not_composed` — proving the ORDER itself, not merely the step set,
    decides the answer, so a test that only checked final states under the
    shipped order could not have distinguished a correctly- from an
    incorrectly-ordered implementation."""
    contradiction = _optional_module_record(
        installation=FALSE, module_registration=TRUE, migration_lineage=FALSE
    )

    with pytest.raises(DimensionalIncoherence):
        derive_composition_state(contradiction)

    reordered_steps = (
        schema._step_validate_coherence,
        schema._step_refuse_required_unknown,
        schema._step_installation_absent,  # moved ahead of the contradiction refusal
        schema._step_refuse_contradictions,
        schema._step_not_applicable,
        schema._step_registration_lineage_table,
    )
    assert set(reordered_steps) == set(schema._DERIVATION_PIPELINE)
    assert reordered_steps != schema._DERIVATION_PIPELINE

    reordered_result = None
    for step in reordered_steps:
        reordered_result = step(contradiction)
        if reordered_result is not None:
            break
    assert reordered_result == CompositionState.NOT_COMPOSED, (
        "the reordered pipeline swallows the contradiction instead of "
        "refusing it — proving the shipped order is load-bearing"
    )


def test_validate_coherence_step_refuses_a_record_that_bypassed_construction():
    """Defence in depth for pipeline step 1: even a `CompositionRecord`
    instance that bypassed `__post_init__` entirely (constructed via
    `object.__new__`, simulating a hypothetical future construction-path
    defect) is still refused by the pipeline's own first step, rather than
    silently deriving a state from incoherent dimensions."""
    broken = object.__new__(CompositionRecord)
    object.__setattr__(broken, "product", "erp")
    object.__setattr__(broken, "distribution", "dotmac-example")
    object.__setattr__(
        broken, "classification", PackageClassification.UNIVERSAL_FACILITY
    )
    object.__setattr__(broken, "installation", TRUE)
    # Incoherent: universal-facility never applies to module_registration.
    object.__setattr__(broken, "module_registration", TRUE)
    object.__setattr__(broken, "migration_lineage", NA)
    object.__setattr__(broken, "runtime_consumption", UNKNOWN)

    with pytest.raises(DimensionalIncoherence):
        derive_composition_state(broken)


# ---------------------------------------------------------------------------
# 2. State is derived, never authored.
# ---------------------------------------------------------------------------


def test_composition_record_refuses_a_supplied_state_field():
    """`CompositionRecord` has no `state` parameter at all; supplying one
    raises `TypeError` rather than being silently accepted or overriding the
    derivation."""
    with pytest.raises(TypeError):
        CompositionRecord(  # type: ignore[call-arg]
            product="erp",
            distribution="dotmac-example",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=TRUE,
            module_registration=TRUE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
            state=CompositionState.FULLY_COMPOSED,  # type: ignore[call-arg]
        )


def test_payload_carrying_a_state_field_is_refused():
    """The ingestion boundary independently refuses a payload that supplies
    `state` or `fully_composed` directly, even though the dimension fields
    it also carries are otherwise well-formed."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-example",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
        "state": "fully_composed",
    }
    with pytest.raises(IncompatibleSchemaVersion):
        composition_record_from_payload(payload)


# ---------------------------------------------------------------------------
# 3. The registration boundary — paired controls.
# ---------------------------------------------------------------------------


def test_vocabulary_registration_negative_control_sub_channels():
    """Sub's `app/services/inbox_channels.py:230` —
    `register_channels(SUB_CHANNELS)` — registers `ChannelSpec` vocabulary
    into a channel registry, never a `ModuleManifest`, and is never consumed
    by an assembly. It must NOT classify as module registration."""
    sub_channels_call_site = RegistrationCallSite(
        callee="register_channels",
        argument_kind="ChannelSpec_tuple",
        consumed_by_assembly=False,
    )
    kind = classify_registration_call_site(sub_channels_call_site)
    assert kind is RegistrationEvidenceKind.VOCABULARY_REGISTRATION

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.FALSE


def test_module_manifest_registration_positive_control_erp_product_assembly():
    """ERP's `app/product_assembly.py` — `COMPOSED_MODULE_MANIFESTS` (a tuple
    of real `ModuleManifest` objects: accounting_module, files_module, ...)
    passed as `modules=COMPOSED_MODULE_MANIFESTS` into `ProductAssemblySpec`
    (`dotmac_kernel.assembly.ProductAssemblySpec`, whose `modules:
    Sequence[AnyManifest]` field is exactly "registered through the consumed
    assembly"). This MUST classify as module registration — the paired
    positive control that proves the checker recognizes the real shape, not
    only that it refuses the wrong one."""
    erp_assembly_call_site = RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        consumed_by_assembly=True,
    )
    kind = classify_registration_call_site(erp_assembly_call_site)
    assert kind is RegistrationEvidenceKind.MODULE_MANIFEST_REGISTERED

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.TRUE


def test_a_module_manifest_tuple_not_consumed_by_an_assembly_still_refuses():
    """Near-miss: even real `ModuleManifest` objects do not count as
    registration if they are never bound into a consumed assembly (e.g. a
    tuple built for a test fixture and never passed anywhere) — both facts
    must hold, not just the argument kind."""
    unused_manifests = RegistrationCallSite(
        callee="a_test_fixture",
        argument_kind="ModuleManifest_tuple",
        consumed_by_assembly=False,
    )
    assert (
        classify_registration_call_site(unused_manifests)
        is RegistrationEvidenceKind.VOCABULARY_REGISTRATION
    )


def test_unmeasured_registration_is_unknown_regardless_of_kind():
    """`measured=False` always yields `unknown`, even for the kind that
    would otherwise classify as true — a claim about a call site that was
    never actually looked at must never resolve to a positive answer."""
    evidence = RegistrationEvidence(
        kind=RegistrationEvidenceKind.MODULE_MANIFEST_REGISTERED, measured=False
    )
    assert evidence.as_dimension_value() == DimensionValue.UNKNOWN


# ---------------------------------------------------------------------------
# 4. not_applicable vs absent (false) vs unknown — three distinct values.
# ---------------------------------------------------------------------------


def test_not_applicable_false_and_unknown_are_three_distinct_values():
    assert DimensionValue.NOT_APPLICABLE != DimensionValue.FALSE
    assert DimensionValue.NOT_APPLICABLE != DimensionValue.UNKNOWN
    assert DimensionValue.FALSE != DimensionValue.UNKNOWN
    assert (
        len(
            {
                DimensionValue.NOT_APPLICABLE,
                DimensionValue.FALSE,
                DimensionValue.UNKNOWN,
            }
        )
        == 3
    )


def test_not_applicable_is_derived_from_classification_only_never_from_a_product():
    """`not_applicable` requires an owning package classification. An
    `optional-module` distribution can never be recorded `not_applicable`
    for `module_registration` — not even for a product like Academy that has
    no registration mechanism whatsoever. That product's failure to
    register is the `false` ("absent") case; the constructor structurally
    refuses the `not_applicable` shortcut."""
    with pytest.raises(ValueError, match="not_applicable"):
        CompositionRecord(
            product="academy",
            distribution="dotmac-example",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=TRUE,
            module_registration=DimensionValue.NOT_APPLICABLE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
        )

    # The honest value for "product has no mechanism, distribution requires
    # one" is FALSE (absent) — and that construction succeeds.
    academy_absent = CompositionRecord(
        product="academy",
        distribution="dotmac-example",
        classification=PackageClassification.OPTIONAL_MODULE,
        installation=TRUE,
        module_registration=DimensionValue.FALSE,
        migration_lineage=TRUE,
        runtime_consumption=UNKNOWN,
    )
    assert academy_absent.module_registration is DimensionValue.FALSE
    assert academy_absent.module_registration is not DimensionValue.NOT_APPLICABLE


def test_platform_baseline_cannot_record_false_where_not_applicable_is_required():
    """The inverse near-miss: a platform-baseline distribution cannot record
    `module_registration=false` either — that dimension does not apply to
    it at all, so `false` (a claim that it applies and was not satisfied) is
    just as wrong as `true` would be."""
    with pytest.raises(ValueError, match="not_applicable"):
        CompositionRecord(
            product="sub",
            distribution="dotmac-kernel",
            classification=PackageClassification.UNIVERSAL_FACILITY,
            installation=TRUE,
            module_registration=DimensionValue.FALSE,
            migration_lineage=DimensionValue.NOT_APPLICABLE,
            runtime_consumption=UNKNOWN,
        )


def test_installation_can_never_be_not_applicable():
    with pytest.raises(ValueError, match="not_applicable"):
        CompositionRecord(
            product="erp",
            distribution="dotmac-example",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=DimensionValue.NOT_APPLICABLE,
            module_registration=TRUE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
        )


# ---------------------------------------------------------------------------
# 5. Schema version — refuse the old shape outright, never translate it.
# ---------------------------------------------------------------------------


def test_old_kernel_runtime_composition_v1_shaped_record_is_refused():
    """A real old-shape payload: Academy's `composed_distributions` rollup
    tagged with the shared legacy version. It must be refused outright —
    not partially read, not upgraded, not defaulted — even though it names
    every distribution the new schema also cares about."""
    old_shaped_payload = {
        "schema_version": "kernel-runtime-composition.v1",
        "product": "academy",
        "composed_distributions": [
            "dotmac-kernel",
            "dotmac-ui",
            "dotmac-people",
        ],
    }
    with pytest.raises(
        IncompatibleSchemaVersion, match="kernel-runtime-composition.v1"
    ):
        composition_record_from_payload(old_shaped_payload)


def test_payload_with_no_schema_version_at_all_is_refused():
    payload = {
        "product": "sub",
        "distribution": "dotmac-inbox",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "false",
    }
    with pytest.raises(IncompatibleSchemaVersion):
        composition_record_from_payload(payload)


def test_current_schema_payload_missing_a_dimension_is_refused_not_defaulted():
    """A payload declaring the CURRENT schema version but missing one
    dimension (`runtime_consumption`) must be refused, never treated as
    `unknown` by default — defaulting is exactly the mechanism that would
    let an old record through wearing new clothes."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-example",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        # runtime_consumption deliberately omitted
    }
    with pytest.raises(IncompatibleSchemaVersion, match="runtime_consumption"):
        composition_record_from_payload(payload)


def test_current_schema_payload_with_all_dimensions_present_is_accepted():
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-accounting",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "true",
    }
    record = composition_record_from_payload(payload)
    assert record.product == "erp"
    assert derive_composition_state(record) == CompositionState.FULLY_COMPOSED


# ---------------------------------------------------------------------------
# 6. runtime_consumption is measured, never inferred.
# ---------------------------------------------------------------------------


def test_runtime_consumption_is_not_inferred_by_derive_composition_state():
    """Structural proof, not just a behavioural one: the derivation
    function's own source never even references `runtime_consumption`, so
    it is impossible for that dimension's value to leak into the
    composition state through any code path in this function."""
    source = inspect.getsource(derive_composition_state)
    assert "runtime_consumption" not in source


def test_runtime_consumption_varies_independently_of_the_other_three():
    """Two records identical in every other dimension but differing only in
    `runtime_consumption` must reach the SAME composition state — proving
    the state derivation cannot see that dimension at all."""
    exposed = _optional_module_record(runtime_consumption=TRUE)
    unexposed = _optional_module_record(runtime_consumption=FALSE)
    unmeasured = _optional_module_record(runtime_consumption=UNKNOWN)
    states = {
        derive_composition_state(exposed),
        derive_composition_state(unexposed),
        derive_composition_state(unmeasured),
    }
    assert states == {CompositionState.FULLY_COMPOSED}


def test_runtime_exposure_is_reported_as_its_own_separate_report():
    """`RuntimeExposureReport` is a distinct type from
    `CompositionCoverageReport`, produced by a distinct function, over the
    same records — never merged into the composition-state counts."""
    records = [
        _optional_module_record(runtime_consumption=TRUE),
        _optional_module_record(runtime_consumption=FALSE),
        _optional_module_record(runtime_consumption=UNKNOWN),
        _optional_module_record(runtime_consumption=UNKNOWN),
    ]
    report = build_runtime_exposure_report(records)
    assert report == RuntimeExposureReport(exposed=1, not_exposed=1, unknown=2)
    assert not isinstance(report, CompositionCoverageReport)


# ---------------------------------------------------------------------------
# 7. No aggregate, anywhere.
# ---------------------------------------------------------------------------


def test_no_scalar_total_exists_anywhere():
    """Structural sweep of the whole module: no report dataclass field is
    named like a total, no report type defines `__add__`/`__radd__`/
    `__int__`, and no top-level callable's name suggests it would produce a
    single aggregate figure. This is the proof that "no code path produces
    a single aggregate count" — not just that today's two report types
    happen not to have one."""
    forbidden_substrings = ("total", "aggregate", "grand", "sum_of", "count_all")

    for report_type in (CompositionCoverageReport, RuntimeExposureReport):
        for field_name in report_type.__dataclass_fields__:
            for forbidden in forbidden_substrings:
                assert forbidden not in field_name.lower(), (
                    f"{report_type.__name__}.{field_name} looks like an "
                    "aggregate field"
                )
        assert "__add__" not in report_type.__dict__
        assert "__radd__" not in report_type.__dict__
        assert "__int__" not in report_type.__dict__
        assert not hasattr(report_type, "total")

    for name in dir(schema):
        if name.startswith("_"):
            continue
        obj = getattr(schema, name)
        if callable(obj) and not isinstance(obj, type):
            for forbidden in forbidden_substrings:
                assert forbidden not in name.lower(), (
                    f"module-level callable {name!r} looks like an " "aggregate helper"
                )


def test_coverage_report_counts_stay_separate_buckets_no_single_number():
    """Concrete instance check: a mixed set of records produces per-state
    counts that a caller must read individually — there is no attribute or
    method on the report that hands back one number summarizing all of
    them."""
    records = [
        _optional_module_record(module_registration=TRUE, migration_lineage=TRUE),
        _optional_module_record(module_registration=FALSE, migration_lineage=TRUE),
        _optional_module_record(module_registration=TRUE, migration_lineage=FALSE),
        _optional_module_record(module_registration=FALSE, migration_lineage=FALSE),
        _optional_module_record(module_registration=UNKNOWN, migration_lineage=TRUE),
        _baseline_record(classification=PackageClassification.UNIVERSAL_FACILITY),
    ]
    report = build_coverage_report(records)
    assert report == CompositionCoverageReport(
        fully_composed=1,
        lineage_only=1,
        invalid=1,
        not_composed=1,
        evidence_incomplete=1,
        not_applicable=1,
    )
    # No summing attribute exists to collapse this back into one figure.
    assert not hasattr(report, "total")
    assert not hasattr(report, "__add__")


# ---------------------------------------------------------------------------
# 8. NOT_COMPOSED is a derivation, not a second copy of the evidence
#    (Michael's ruling: no new enum member; installation retains the
#    distinction; the collapse is intentional and must not be "fixed").
# ---------------------------------------------------------------------------


def test_not_composed_collapses_installation_absent_and_installed_but_unregistered():
    """Control 1. Two records that both derive `NOT_COMPOSED` — one
    `installation=false`, one `installation=true` with applicable
    `module_registration`/`migration_lineage` both `false` — produce the
    IDENTICAL state, while their `installation` values remain distinct and
    readable on the records themselves. The state collapses what the
    record keeps; that is the point, not a bug."""
    confirmed_not_installed = _optional_module_record(
        installation=FALSE, module_registration=FALSE, migration_lineage=FALSE
    )
    installed_but_unregistered = _optional_module_record(
        installation=TRUE, module_registration=FALSE, migration_lineage=FALSE
    )

    state_a = derive_composition_state(confirmed_not_installed)
    state_b = derive_composition_state(installed_but_unregistered)
    assert state_a == state_b == CompositionState.NOT_COMPOSED

    # The record's own installation dimension still distinguishes them,
    # even though the derived state does not.
    assert confirmed_not_installed.installation is DimensionValue.FALSE
    assert installed_but_unregistered.installation is DimensionValue.TRUE
    assert (
        confirmed_not_installed.installation != installed_but_unregistered.installation
    )


def test_state_only_reports_cannot_answer_a_cross_dimensional_composition_question():
    """Control 2. A real check, not a comment: builds the SAME categorized-
    set machinery this module already ships (`build_coverage_report`,
    `build_runtime_exposure_report`) over a small record set and shows that
    no combination of the two REPORT OBJECTS answers "how many
    `not_composed` distributions are also `runtime_consumption = true`" —
    exactly the question Michael's drawn-out consequence puts at stake: an
    installed, unregistered, RUNNING distribution reports `NOT_COMPOSED`
    while visibly carrying `runtime_consumption = TRUE`.

    Sensitivity proof (both halves required, per the standing rule that a
    control must be shown able to refuse, not just to answer):

    * PLANT — the defect this control targets is "answer the question from
      the reports alone." Every fixed answer a report-only consumer could
      reach from `(coverage.not_composed, runtime.exposed)` is checked
      against the true, record-level count and shown WRONG. Removing this
      assertion would let the test pass over a report-only path that was
      never actually exercised for correctness — the failure mode this
      control exists to avoid.
    * NEAR MISS — reading the identical question directly off the original
      records (never through a report) is shown CORRECT, proving nothing
      is wrong with a record-level check, only a report/state-only one; a
      control that only showed the report-only path failing, with no
      correct alternative demonstrated, would not have proven the reports
      insufficient so much as proven nothing was tried.
    """
    installed_unregistered_and_running = _optional_module_record(
        installation=TRUE,
        module_registration=FALSE,
        migration_lineage=FALSE,
        runtime_consumption=TRUE,
    )
    genuinely_not_installed = _optional_module_record(
        installation=FALSE,
        module_registration=FALSE,
        migration_lineage=FALSE,
        runtime_consumption=UNKNOWN,
    )
    fully_composed_and_running = _optional_module_record(
        installation=TRUE,
        module_registration=TRUE,
        migration_lineage=TRUE,
        runtime_consumption=TRUE,
    )
    records = [
        installed_unregistered_and_running,
        genuinely_not_installed,
        fully_composed_and_running,
    ]

    assert (
        derive_composition_state(installed_unregistered_and_running)
        == CompositionState.NOT_COMPOSED
    )
    assert (
        derive_composition_state(genuinely_not_installed)
        == CompositionState.NOT_COMPOSED
    )
    assert (
        derive_composition_state(fully_composed_and_running)
        == CompositionState.FULLY_COMPOSED
    )

    coverage = build_coverage_report(records)
    runtime = build_runtime_exposure_report(records)
    assert coverage.not_composed == 2
    assert coverage.fully_composed == 1
    assert runtime.exposed == 2  # the two records genuinely running

    # The true, record-level answer: only ONE of the two not_composed
    # records is also runtime_consumption=true.
    true_not_composed_and_running = sum(
        1
        for record in records
        if derive_composition_state(record) == CompositionState.NOT_COMPOSED
        and record.runtime_consumption is DimensionValue.TRUE
    )
    assert true_not_composed_and_running == 1

    # PLANT: every fixed answer a report-only consumer could reach is wrong.
    naive_min_of_the_two_counts = min(coverage.not_composed, runtime.exposed)
    naive_runtime_exposed_total = runtime.exposed
    assert naive_min_of_the_two_counts != true_not_composed_and_running
    assert naive_runtime_exposed_total != true_not_composed_and_running

    # Structural half of the same proof: neither report even HAS a field
    # that could hold this answer — consistent with "no scalar total,
    # ever" and with runtime_consumption never folding into a state.
    assert not hasattr(coverage, "not_composed_and_runtime_exposed")
    assert not hasattr(runtime, "not_composed_and_runtime_exposed")

    # NEAR MISS: the identical question, read directly off the records
    # (never through a report), is correct.
    record_level_answer = sum(
        1
        for record in records
        if derive_composition_state(record) == CompositionState.NOT_COMPOSED
        and record.runtime_consumption is DimensionValue.TRUE
    )
    assert record_level_answer == true_not_composed_and_running == 1
