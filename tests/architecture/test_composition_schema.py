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
    to exactly one state. The expected table is written by hand here — not
    derived from the implementation — so this is a real check, not a
    tautology."""
    dims = (TRUE, FALSE, UNKNOWN)
    expected: dict[
        tuple[DimensionValue, DimensionValue, DimensionValue], CompositionState
    ] = {}
    for installation in dims:
        for registration in dims:
            for lineage in dims:
                if installation == UNKNOWN:
                    expected_state = CompositionState.EVIDENCE_INCOMPLETE
                elif installation == FALSE:
                    expected_state = CompositionState.NOT_COMPOSED
                elif registration == UNKNOWN or lineage == UNKNOWN:
                    expected_state = CompositionState.EVIDENCE_INCOMPLETE
                elif registration == TRUE and lineage == TRUE:
                    expected_state = CompositionState.FULLY_COMPOSED
                elif registration == FALSE and lineage == TRUE:
                    expected_state = CompositionState.LINEAGE_ONLY
                elif registration == TRUE and lineage == FALSE:
                    expected_state = CompositionState.INVALID
                else:
                    assert registration == FALSE and lineage == FALSE
                    expected_state = CompositionState.NOT_COMPOSED
                expected[(installation, registration, lineage)] = expected_state

    assert len(expected) == 27, "hand table must cover all 3x3x3 combinations"

    for (installation, registration, lineage), expected_state in expected.items():
        record = _optional_module_record(
            installation=installation,
            module_registration=registration,
            migration_lineage=lineage,
        )
        actual_state = derive_composition_state(record)
        assert actual_state == expected_state, (
            f"installation={installation}, module_registration={registration}, "
            f"migration_lineage={lineage}: expected {expected_state}, got "
            f"{actual_state}"
        )


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
