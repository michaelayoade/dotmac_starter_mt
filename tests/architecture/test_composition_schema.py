"""Tests for the dimensional composition schema (`composition_schema.py`).

Every test proves a specific ruled property from the brief; see each
docstring for which one. Static analysis only — no pytest execution here,
just as the constraints require; these are written to be run by CI, not by
this session.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.architecture import composition_schema as schema
from tests.architecture.composition_schema import (
    AssemblyConsumptionKind,
    AssemblyConsumptionTrace,
    CatalogueDerivationError,
    CompositionCoverageReport,
    CompositionRecord,
    CompositionState,
    DimensionalIncoherence,
    DimensionValue,
    IncompatibleSchemaVersion,
    PackageClassification,
    PackageDossier,
    RegistrationCallSite,
    RegistrationEvidence,
    RegistrationEvidenceKind,
    RuntimeExposureReport,
    build_coverage_report,
    build_runtime_exposure_report,
    classify_registration_call_site,
    composition_record_from_payload,
    derive_composition_state,
    derive_distribution_universe,
)

TRUE = DimensionValue.TRUE
FALSE = DimensionValue.FALSE
UNKNOWN = DimensionValue.UNKNOWN
NA = DimensionValue.NOT_APPLICABLE

#: This repository's own `packages/` directory — the real tree the catalogue
#: tests measure against. Computed relative to this test file, never a
#: hard-coded absolute path.
REPO_PACKAGES_ROOT = Path(__file__).resolve().parents[2] / "packages"


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
# 3. The registration boundary — three real, paired controls (v2).
#
# v1 required `argument_kind == "ModuleManifest_tuple"` and a flat
# `consumed_by_assembly: bool`. Measured directly against both real
# repositories, that bool was identical for ERP's inert release spec and for
# a genuinely booted assembly — it never asked whether the PRODUCT'S OWN
# BOOT PATH reaches the object, only whether a `ProductAssemblySpec` was
# constructed anywhere. v2's `AssemblyConsumptionTrace` replaces it with two
# independently observable facts about the boot path itself.
# ---------------------------------------------------------------------------


def test_module_manifest_registration_positive_control_starter_own_assembly():
    """Positive control — Starter's own `app/assembly.py` + `app/main.py`
    (this repository, read directly, not a synthetic stand-in). `app/main.py`
    is the real process entry point: `from app.assembly import assembly` then
    `app = create_app(assembly)`. `dotmac_kernel.app_factory.create_app`
    builds `ModuleRegistry(spec.modules)` from it before mounting anything
    (`packages/dotmac-kernel/src/dotmac_kernel/app_factory.py`), so this
    traces `imported_by_boot_entry_point=True` and
    `consumed_by_a_real_effect=True` — the ONLY input among the three
    controls that must classify as module registration. Without this
    control, the two negative controls below would be equally consistent
    with a checker that refuses everything."""
    starter_assembly_call_site = RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=AssemblyConsumptionTrace(
            boot_entry_point="app/main.py",
            imported_by_boot_entry_point=True,
            consumed_by_a_real_effect=True,
        ),
    )
    kind = classify_registration_call_site(starter_assembly_call_site)
    assert kind is RegistrationEvidenceKind.MODULE_MANIFEST_REGISTERED
    assert (
        starter_assembly_call_site.assembly_consumption.classify()
        is AssemblyConsumptionKind.BOOT_PATH_CONSUMED
    )

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.TRUE


def test_module_manifest_tuple_not_reaching_boot_path_is_refused_erp_negative_control():
    """Negative control — ERP's `app/product_assembly.py`. Its
    `COMPOSED_MODULE_MANIFESTS` tuple passed as `modules=...` into
    `ProductAssemblySpec(...)` is a real `ModuleManifest_tuple` — the
    identical argument kind the positive control above uses. What differs,
    measured directly, is that ERP's `app/main.py` never imports
    `app.product_assembly` at all; only four architecture tests and
    `scripts/product_manifest.py` do. This is exactly the case v1's flat
    `consumed_by_assembly: bool` could not discriminate from the positive
    control — see the module docstring's "Why v2 exists"."""
    erp_assembly_call_site = RegistrationCallSite(
        callee="ProductAssemblySpec",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=AssemblyConsumptionTrace(
            boot_entry_point="app/main.py",
            imported_by_boot_entry_point=False,
            consumed_by_a_real_effect=False,
        ),
    )
    kind = classify_registration_call_site(erp_assembly_call_site)
    assert kind is RegistrationEvidenceKind.VOCABULARY_REGISTRATION
    assert (
        erp_assembly_call_site.assembly_consumption.classify()
        is AssemblyConsumptionKind.RELEASE_METADATA_ONLY
    )

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.FALSE


def test_vocabulary_registration_negative_control_sub_channels():
    """Negative control — Sub's `app/services/inbox_channels.py:230` —
    `register_channels(SUB_CHANNELS)` — registers `ChannelSpec` vocabulary
    into a channel registry, never a `ModuleManifest` at all, so this is
    refused on `argument_kind` alone before any assembly-consumption trace
    is consulted. The module's own docstring independently confirms it is
    unreachable from `app/` at runtime too ("Nothing under `app/` imports
    this module at runtime yet, and that is deliberate"), so the trace is
    recorded as `RELEASE_METADATA_ONLY` for completeness even though the
    argument-kind check alone already decides this case."""
    sub_channels_call_site = RegistrationCallSite(
        callee="register_channels",
        argument_kind="ChannelSpec_tuple",
        assembly_consumption=AssemblyConsumptionTrace(
            boot_entry_point="app/main.py",
            imported_by_boot_entry_point=False,
            consumed_by_a_real_effect=False,
        ),
    )
    kind = classify_registration_call_site(sub_channels_call_site)
    assert kind is RegistrationEvidenceKind.VOCABULARY_REGISTRATION

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.FALSE


def test_a_module_manifest_tuple_reached_by_boot_but_not_consumed_still_refuses():
    """Near-miss, both facts required: even a real `ModuleManifest_tuple`
    that IS imported by the boot entry point does not count as registration
    if it is never fed into a call proven to use it for a real effect (e.g.
    a dead import, or a value only re-exported, never passed to
    `create_app`/`ModuleRegistry`). Reachability alone is not enough — this
    is the sensitivity proof that both `AssemblyConsumptionTrace` fields are
    load-bearing, not just `imported_by_boot_entry_point`."""
    imported_but_unused = RegistrationCallSite(
        callee="a_dead_import",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=AssemblyConsumptionTrace(
            boot_entry_point="app/main.py",
            imported_by_boot_entry_point=True,
            consumed_by_a_real_effect=False,
        ),
    )
    assert (
        classify_registration_call_site(imported_but_unused)
        is RegistrationEvidenceKind.VOCABULARY_REGISTRATION
    )


def test_indeterminate_assembly_consumption_is_a_refusal_not_a_guessed_false():
    """The evidence shape must be able to say "I could not establish this" —
    a `AssemblyConsumptionTrace` that cannot resolve one or both facts (e.g.
    a dynamic import) classifies as `INDETERMINATE`, and
    `classify_registration_call_site` reports that distinctly as
    `INDETERMINATE_ASSEMBLY_CONSUMPTION`, which resolves to `UNKNOWN` even
    though the call site itself WAS looked at (`measured=True`) — a second,
    independent route to `UNKNOWN` beyond `measured=False`, proving the
    refusal is never silently collapsed into a guessed `FALSE`."""
    unresolvable_import = RegistrationCallSite(
        callee="some_dynamic_indirection",
        argument_kind="ModuleManifest_tuple",
        assembly_consumption=AssemblyConsumptionTrace(
            boot_entry_point="app/main.py",
            imported_by_boot_entry_point=None,
            consumed_by_a_real_effect=True,
        ),
    )
    assert (
        unresolvable_import.assembly_consumption.classify()
        is AssemblyConsumptionKind.INDETERMINATE
    )
    kind = classify_registration_call_site(unresolvable_import)
    assert kind is RegistrationEvidenceKind.INDETERMINATE_ASSEMBLY_CONSUMPTION

    evidence = RegistrationEvidence(kind=kind, measured=True)
    assert evidence.as_dimension_value() == DimensionValue.UNKNOWN


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


def test_current_schema_version_is_v2():
    """Pins the bump itself as an artifact, not just its consequences."""
    assert schema.CURRENT_SCHEMA_VERSION == "dimensional-composition.v2"


def test_dimensional_composition_v1_payload_is_refused_no_adapter_no_migration():
    """v2's own defining case: a `dimensional-composition.v1`-tagged
    payload — the schema's OWN prior version, not an unrelated legacy tag —
    is refused exactly like every other unrecognized version. There is no
    adapter and no migration path: the payload below is otherwise perfectly
    well-formed under the v1 shape (every v1 `REQUIRED_PAYLOAD_FIELDS`
    present with valid values) and must still be refused solely because of
    its declared version, loudly naming that version."""
    v1_shaped_payload = {
        "schema_version": schema.LEGACY_SCHEMA_VERSION_V1,
        "product": "erp",
        "distribution": "dotmac-accounting",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "true",
    }
    assert schema.LEGACY_SCHEMA_VERSION_V1 != schema.CURRENT_SCHEMA_VERSION
    with pytest.raises(IncompatibleSchemaVersion, match="dimensional-composition.v1"):
        composition_record_from_payload(v1_shaped_payload)


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


# ---------------------------------------------------------------------------
# 9. The catalogue universe — product-independent, derived from every
#    packages/*/EXTRACTION.toml (Ruling 2).
# ---------------------------------------------------------------------------


def test_derive_distribution_universe_matches_the_real_packages_tree():
    """Runs against THIS repository's real `packages/` directory, not a
    synthetic stand-in. The expected count and name set are re-derived from
    `packages_root.iterdir()` directly in this test — never a literal
    number — so the assertion tracks a real change to the tree instead of
    going stale the moment a package is added or removed."""
    universe = derive_distribution_universe(REPO_PACKAGES_ROOT)

    real_directories = [p for p in REPO_PACKAGES_ROOT.iterdir() if p.is_dir()]
    assert len(universe) == len(real_directories)

    derived_names = {dossier.distribution for dossier in universe}
    real_names = {p.name for p in real_directories}
    assert derived_names == real_names

    # Every real distribution directory has a real EXTRACTION.toml — the
    # "no directory silently skipped" property, checked positively here
    # rather than only via the refusal test below.
    for directory in real_directories:
        assert (directory / "EXTRACTION.toml").is_file()

    # No duplicate distribution names in the derived universe.
    assert len(derived_names) == len(universe)


def test_derive_distribution_universe_takes_no_product_name():
    """Structural proof of product-independence (Ruling 2): the function's
    own signature accepts only a filesystem path, never a product
    identifier — so there is nowhere in its call contract for a product
    name to enter and no way for it to govern a product-scoped subset."""
    parameters = list(inspect.signature(derive_distribution_universe).parameters)
    assert parameters == ["packages_root"]
    # No named product may appear in the function's body — the docstring's
    # generic word "product" (as in "product-independent") is fine and is
    # deliberately not checked here; a literal product NAME would be the
    # actual violation of Ruling 2.
    source = inspect.getsource(derive_distribution_universe)
    for forbidden_product_name in ("starter", "dotmac_erp", "dotmac_sub", "academy"):
        assert forbidden_product_name not in source.lower(), (
            f"derive_distribution_universe's body references "
            f"{forbidden_product_name!r} — it must not branch on which "
            "product is asking"
        )


def test_a_packages_directory_with_no_extraction_toml_is_refused_not_skipped(
    tmp_path: Path,
):
    """Sensitivity proof, PLANT half: a real package directory silently
    missing its dossier must be refused, not quietly excluded from the
    universe (which would understate it without anyone noticing)."""
    (tmp_path / "dotmac-has-dossier").mkdir()
    (tmp_path / "dotmac-has-dossier" / "EXTRACTION.toml").write_text(
        'package = "dotmac-has-dossier"\nclassification = "optional-module"\n'
    )
    (tmp_path / "dotmac-missing-dossier").mkdir()  # no EXTRACTION.toml

    with pytest.raises(CatalogueDerivationError, match="dotmac-missing-dossier"):
        derive_distribution_universe(tmp_path)


def test_a_packages_directory_with_extraction_toml_present_is_not_refused(
    tmp_path: Path,
):
    """Sensitivity proof, NEAR-MISS half: the identical tree, minus the
    missing dossier, is accepted — proving the refusal above is triggered by
    the missing file specifically, not by some unrelated property of the
    fixture."""
    (tmp_path / "dotmac-has-dossier").mkdir()
    (tmp_path / "dotmac-has-dossier" / "EXTRACTION.toml").write_text(
        'package = "dotmac-has-dossier"\nclassification = "optional-module"\n'
    )

    universe = derive_distribution_universe(tmp_path)
    assert len(universe) == 1
    assert universe[0].distribution == "dotmac-has-dossier"


def test_duplicate_distribution_name_across_two_dossiers_is_refused(tmp_path: Path):
    (tmp_path / "dir-one").mkdir()
    (tmp_path / "dir-one" / "EXTRACTION.toml").write_text(
        'package = "dotmac-dup"\nclassification = "optional-module"\n'
    )
    (tmp_path / "dir-two").mkdir()
    (tmp_path / "dir-two" / "EXTRACTION.toml").write_text(
        'package = "dotmac-dup"\nclassification = "optional-module"\n'
    )

    with pytest.raises(CatalogueDerivationError, match="dotmac-dup"):
        derive_distribution_universe(tmp_path)


def test_unrecognized_classification_fails_loudly_as_a_named_refusal(
    tmp_path: Path,
):
    """A dossier's `classification` that `PackageClassification(...)` does
    not recognize must surface as a named `CatalogueDerivationError` (this
    module's own refusal type), not an unhandled `ValueError` from deep
    inside the enum constructor."""
    (tmp_path / "dotmac-bad").mkdir()
    (tmp_path / "dotmac-bad" / "EXTRACTION.toml").write_text(
        'package = "dotmac-bad"\nclassification = "not-a-real-classification"\n'
    )

    with pytest.raises(CatalogueDerivationError, match="not-a-real-classification"):
        derive_distribution_universe(tmp_path)


def test_a_dossier_with_a_recognized_classification_is_not_refused(
    tmp_path: Path,
):
    """Sensitivity near-miss for the classification refusal above: the
    identical shape with a REAL classification value succeeds."""
    (tmp_path / "dotmac-good").mkdir()
    (tmp_path / "dotmac-good" / "EXTRACTION.toml").write_text(
        'package = "dotmac-good"\nclassification = "optional-module"\n'
    )

    universe = derive_distribution_universe(tmp_path)
    assert universe[0].classification is PackageClassification.OPTIONAL_MODULE


def test_stateless_contract_catalogue_is_exercised_by_a_synthetic_dossier(
    tmp_path: Path,
):
    """`PackageClassification.STATELESS_CONTRACT_CATALOGUE` is declared but
    used by zero real dossiers in this repository today (see the module
    docstring) — an unexercised branch proves nothing about its own
    correctness, so this builds a synthetic dossier declaring it and proves
    the derivation both accepts it and preserves the exact enum member."""
    (tmp_path / "dotmac-schemas").mkdir()
    (tmp_path / "dotmac-schemas" / "EXTRACTION.toml").write_text(
        'package = "dotmac-schemas"\n'
        'classification = "stateless-contract-catalogue"\n'
    )

    universe = derive_distribution_universe(tmp_path)
    assert len(universe) == 1
    assert (
        universe[0].classification is PackageClassification.STATELESS_CONTRACT_CATALOGUE
    )
    # Confirms this classification is genuinely absent from the real tree
    # today, which is why the branch needed a synthetic dossier at all.
    real_universe = derive_distribution_universe(REPO_PACKAGES_ROOT)
    real_classifications = {d.classification for d in real_universe}
    assert (
        PackageClassification.STATELESS_CONTRACT_CATALOGUE not in real_classifications
    )


def test_a_dossier_with_no_package_name_is_refused(tmp_path: Path):
    (tmp_path / "dotmac-nameless").mkdir()
    (tmp_path / "dotmac-nameless" / "EXTRACTION.toml").write_text(
        'classification = "optional-module"\n'
    )

    with pytest.raises(CatalogueDerivationError):
        derive_distribution_universe(tmp_path)


def test_packages_root_that_is_not_a_directory_is_refused(tmp_path: Path):
    not_a_directory = tmp_path / "does-not-exist"

    with pytest.raises(CatalogueDerivationError):
        derive_distribution_universe(not_a_directory)


def test_universe_size_is_never_hard_coded_anywhere_in_this_module():
    """Structural sweep: no literal count of today's real distribution
    universe appears anywhere in `composition_schema.py`'s source — the
    number the brief measured (93) is explicitly checked absent, and so is
    this module's own directly-measured count, so neither can silently
    become a governing constant."""
    source = inspect.getsource(schema)
    real_count = len([p for p in REPO_PACKAGES_ROOT.iterdir() if p.is_dir()])
    for forbidden_literal in ("93", str(real_count)):
        assert forbidden_literal not in source, (
            f"composition_schema.py's source contains the literal "
            f"{forbidden_literal!r} — the catalogue universe's size must "
            "never be a hard-coded constant"
        )


def test_package_dossier_holds_only_distribution_and_classification():
    """`PackageDossier` is deliberately narrow — this module needs nothing
    else from an `EXTRACTION.toml` dossier, and `adoption_evidence.py`
    (a different owner) is where the rest of that file's meaning lives."""
    fields = set(PackageDossier.__dataclass_fields__)
    assert fields == {"distribution", "classification"}
