"""Tests for the dimensional composition schema (`composition_schema.py`).

Every test proves a specific ruled property from the brief; see each
docstring for which one. Static analysis only — no pytest execution here,
just as the constraints require; these are written to be run by CI, not by
this session.

Non-authoritative fixtures — read this before citing anything from the ERP
and Sub sections below as evidence about those products. The ERP call site
(``dotmac_erp/app/product_assembly.py``) and the Sub call site
(``dotmac_sub/app/services/inbox_channels.py:230``) that the registration-
boundary tests below encode as literal ``RegistrationCallSite``/
``AssemblyConsumptionTrace`` field values are a ONE-TIME, DATED manual
reading of those two separate repositories' trees, recorded here as
hand-typed literals. They are NOT re-derived by execution — this repository's
own test run has no access to either ``dotmac_erp`` or ``dotmac_sub`` — and
they are NOT evidence about ERP's or Sub's current state; they are regression
fixtures for THIS module's own classifier logic (`classify_registration_
call_site`), catching a regression in how this module discriminates
"release metadata" from "boot-path consumption," nothing more. A change in
either upstream repository can silently make these literals stale without
this test suite ever knowing, and nothing here or in `composition_schema.py`
may be read as a current claim about ERP's or Sub's real registration state.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import replace
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
    EnvelopeIncoherence,
    IncompatibleSchemaVersion,
    InstallRecipe,
    ManifestDeclarationError,
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
    composition_records_from_envelope,
    derive_composition_state,
    derive_distribution_universe,
    derive_group_optionality,
    derive_installation_dimension,
    derive_installation_group_universe,
    derive_lock_group_membership,
    derive_migration_lineage_applicability_from_manifest,
    measure_starter_boot_assembly_consumption,
)

TRUE = DimensionValue.TRUE
FALSE = DimensionValue.FALSE
UNKNOWN = DimensionValue.UNKNOWN
NA = DimensionValue.NOT_APPLICABLE

#: This repository's own `packages/` directory — the real tree the catalogue
#: tests measure against. Computed relative to this test file, never a
#: hard-coded absolute path.
REPO_PACKAGES_ROOT = Path(__file__).resolve().parents[2] / "packages"

#: This repository's own root (one level up from `REPO_PACKAGES_ROOT`) — the
#: real tree `measure_starter_boot_assembly_consumption` reads `app/main.py`,
#: `app/assembly.py`, and the kernel's `app_factory.py` against.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _derived_record(
    *,
    product: str,
    distribution: str,
    classification: PackageClassification,
    installation: DimensionValue,
    module_registration: DimensionValue,
    migration_lineage: DimensionValue,
    runtime_consumption: DimensionValue,
    manifest_applies: bool = True,
) -> CompositionRecord:
    """Build a synthetic already-derived record for pure pipeline tests.

    Product records never use this test helper; they travel through
    ``composition_record_from_payload`` and derive the same fact from the
    authoritative dossier and manifest. Keeping the exceptional builder
    visibly private prevents pure state-table tests from becoming evidence
    for the ingestion boundary.
    """
    record = object.__new__(CompositionRecord)
    for name, value in (
        ("product", product),
        ("distribution", distribution),
        ("classification", classification),
        ("installation", installation),
        ("module_registration", module_registration),
        ("migration_lineage", migration_lineage),
        ("runtime_consumption", runtime_consumption),
        ("_CompositionRecord__migration_lineage_manifest_applies", manifest_applies),
    ):
        object.__setattr__(record, name, value)
    record.__post_init__()
    return record


def _optional_module_record(
    *,
    installation=TRUE,
    module_registration=TRUE,
    migration_lineage=TRUE,
    runtime_consumption=UNKNOWN,
) -> CompositionRecord:
    return _derived_record(
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
    return _derived_record(
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
    hazard = _derived_record(
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
    object.__setattr__(
        broken, "_CompositionRecord__migration_lineage_manifest_applies", False
    )

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
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


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


def test_measure_starter_boot_assembly_consumption_against_this_repository():
    """The positive control above (`starter_assembly_call_site`) is a
    hand-typed `AssemblyConsumptionTrace` — the module docstring's own
    section on the registration boundary is explicit that this is exactly
    as unverifiable, by construction, as the hand-typed ERP/Sub negative
    controls: nothing stops an author from setting either boolean field to
    whatever answer they want. What makes the STARTER control different, and
    what the module docstring repeatedly claims (`composition_schema.py`'s
    "The registration boundary" section: "the one input that proves it can
    also say yes," "re-derived by executing
    `measure_starter_boot_assembly_consumption` against this repository's
    own tree"), is that this repository's own tree is available to re-derive
    it FROM, by execution, rather than trust an assertion. This test is that
    re-derivation: it actually calls
    `measure_starter_boot_assembly_consumption` against `REPO_ROOT` — this
    repository's real `app/main.py` importing `app.assembly`, `app/main.py`
    calling `create_app(assembly)`, and the kernel's `app_factory.py`
    containing `ModuleRegistry(spec.modules)` — and asserts the SAME
    `BOOT_PATH_CONSUMED` classification the hand-typed control above claims.
    Without this test, the module docstring's claim of re-derivation was
    prose citing an execution that never happened."""
    trace = measure_starter_boot_assembly_consumption(REPO_ROOT)
    assert trace.imported_by_boot_entry_point is True
    assert trace.consumed_by_a_real_effect is True
    assert trace.classify() is AssemblyConsumptionKind.BOOT_PATH_CONSUMED


def test_deleting_the_boot_entry_point_makes_the_trace_indeterminate(tmp_path: Path):
    """Named exactly as the module docstring cites it (`composition_schema.py`
    at the `AssemblyConsumptionTrace` docstring and at
    `measure_starter_boot_assembly_consumption`'s own docstring) — until this
    test, that name appeared only inside those two docstrings and nowhere
    executable. Proves the function is a real read, not a fixed answer: a
    tree with no `app/main.py` at all (an empty `tmp_path`, standing in for
    "the boot entry point was deleted") must classify as `INDETERMINATE`,
    never guessing `BOOT_PATH_CONSUMED` or `RELEASE_METADATA_ONLY` from an
    absent file."""
    trace = measure_starter_boot_assembly_consumption(tmp_path)
    assert trace.imported_by_boot_entry_point is None
    assert trace.consumed_by_a_real_effect is None
    assert trace.classify() is AssemblyConsumptionKind.INDETERMINATE


def test_an_empty_assembly_file_cannot_satisfy_the_positive_control(tmp_path: Path):
    """Existence is not evidence that ``assembly`` is actually declared."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        "from app.assembly import assembly\ncreate_app(assembly)\n"
    )
    (app_dir / "assembly.py").write_text("# no ProductAssemblySpec here\n")
    factory_dir = tmp_path / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel"
    factory_dir.mkdir(parents=True)
    (factory_dir / "app_factory.py").write_text("ModuleRegistry(spec.modules)\n")

    trace = measure_starter_boot_assembly_consumption(tmp_path)
    assert trace.imported_by_boot_entry_point is False
    assert trace.classify() is AssemblyConsumptionKind.INDETERMINATE


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
        _derived_record(
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
    academy_absent = _derived_record(
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
        _derived_record(
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
        _derived_record(
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
        composition_record_from_payload(old_shaped_payload, REPO_PACKAGES_ROOT)


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
        composition_record_from_payload(v1_shaped_payload, REPO_PACKAGES_ROOT)


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
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


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
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


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
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    assert record.product == "erp"
    assert derive_composition_state(record) == CompositionState.FULLY_COMPOSED


# ---------------------------------------------------------------------------
# 5b. Ruling 1 through the real ingestion path — `composition_record_from_
#     payload` must derive migration-lineage applicability itself. A prior
#     commit (03c2629b) fixed this only for direct `CompositionRecord(...)`
#     construction with the flag supplied by hand — a seam no product
#     payload ever uses. Every test below goes through
#     `composition_record_from_payload` itself, never a hand-built record,
#     because that is precisely the gap the coordinator's correction closed.
# ---------------------------------------------------------------------------


def test_stateless_module_payload_with_not_applicable_lineage_is_accepted():
    """The live case, through the real ingestion path. A
    `dotmac-document-rendering` payload recording `migration_lineage:
    "not_applicable"` must be ACCEPTED (not refused) and, once registered,
    must derive `fully_composed` — the two outcomes the ruling exists to
    restore, reached this time by `composition_record_from_payload`, not by
    a hand-built `CompositionRecord`. Before the fix in this commit, this
    exact payload was refused at construction (`migration_lineage applies
    ... cannot be recorded not_applicable`), because
    `composition_record_from_payload` never derived or passed the manifest
    flag — it always fell back to the classification-only default."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": "dotmac-document-rendering",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
    }
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    assert record.migration_lineage is DimensionValue.NOT_APPLICABLE
    assert derive_composition_state(record) == CompositionState.FULLY_COMPOSED


def test_stateless_module_payload_with_false_lineage_no_longer_derives_invalid():
    """Companion to the test above, pinning the OTHER outcome the ruling
    eliminates: a `dotmac-document-rendering` payload that (incorrectly, by
    the old classification-only rule) recorded `migration_lineage: "false"`
    used to derive `invalid` once registered. Since lineage does not apply
    to this distribution at all, `false` is now refused the same way `true`
    would be — not_applicable is the only honest value once the manifest is
    consulted — so this asserts the refusal, not a lingering `invalid`."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": "dotmac-document-rendering",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "false",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(ValueError, match="not_applicable"):
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


def test_stateful_module_payload_with_not_applicable_lineage_still_refused():
    """The guard must not have been weakened into accepting anything: a
    `dotmac-billing` payload (a real, coherent, stateful manifest —
    `short_code`/`migration_prefix` both declared) recording
    `migration_lineage: "not_applicable"` must still be refused. If this
    passed, the fix would have made `not_applicable` universally legal for
    `optional-module` instead of manifest-conditional."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-billing",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(ValueError, match="not_applicable"):
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


def test_payload_cannot_reclassify_a_real_stateful_module_as_a_baseline():
    """The dossier, not the product payload, owns package classification.

    Without this join, calling a stateful optional module a universal
    facility skips manifest parsing and makes both module dimensions appear
    legitimately inapplicable.
    """
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-billing",
        "classification": "universal-facility",
        "installation": "true",
        "module_registration": "not_applicable",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(CatalogueDerivationError, match="optional-module"):
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


def test_payload_classification_matching_the_dossier_is_accepted():
    """Near-miss: agreement with the authoritative dossier is not refused."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-billing",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
    }
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    assert record.classification is PackageClassification.OPTIONAL_MODULE
    assert derive_composition_state(record) is CompositionState.FULLY_COMPOSED


def test_payload_cannot_supply_migration_lineage_manifest_applies():
    """Applicability is Starter-derived and is not a payload input.

    Supplying even the value opposite to the real manifest is refused at
    ingestion. Ignoring the field would prevent it from changing today's
    result, but would still accept an authority-shaped input and leave the
    producer believing it participated in the decision.
    """
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": "dotmac-document-rendering",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
        "migration_lineage_manifest_applies": True,  # opposite of derived False
    }
    with pytest.raises(
        IncompatibleSchemaVersion, match="migration_lineage_manifest_applies"
    ):
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


# ---------------------------------------------------------------------------
# 5c. Closed shape — an unrecognized payload key is refused, not silently
#     dropped. Michael planted an `api_key` field alongside a legitimate
#     payload and it was accepted, with the key simply never read; a
#     product could put arbitrary content, including a credential, into a
#     composition record and ingestion would not refuse it.
# ---------------------------------------------------------------------------

#: The exact legitimate payload every closed-shape test below either reuses
#: unmodified (the near-miss) or extends with an offending key. Kept as one
#: literal so the near-miss and the plants are provably the same shape apart
#: from the extra key(s).
_LEGITIMATE_PAYLOAD = {
    "schema_version": schema.CURRENT_SCHEMA_VERSION,
    "product": "erp",
    "distribution": "dotmac-accounting",
    "classification": "optional-module",
    "installation": "true",
    "module_registration": "true",
    "migration_lineage": "true",
    "runtime_consumption": "true",
}


def test_payload_with_an_unknown_field_is_refused_naming_it():
    """The measured defect: a payload carrying every legitimate field plus
    one unrecognized key (`api_key`, the planted credential-shaped field)
    must be refused, and the refusal must name `api_key` specifically —
    not just refuse the payload for some other reason."""
    payload = {**_LEGITIMATE_PAYLOAD, "api_key": "AKIAIOSFODNN7EXAMPLE"}
    with pytest.raises(IncompatibleSchemaVersion, match="api_key"):
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)


def test_payload_with_several_unknown_fields_names_all_of_them():
    """A checker that refuses on the first offending key and stops would
    hide every other stray key from the caller trying to fix the payload.
    Both `api_key` and `note` must appear in the one raised message."""
    payload = {
        **_LEGITIMATE_PAYLOAD,
        "api_key": "AKIAIOSFODNN7EXAMPLE",
        "note": "arbitrary prose",
    }
    with pytest.raises(IncompatibleSchemaVersion) as excinfo:
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    message = str(excinfo.value)
    assert "api_key" in message
    assert "note" in message


def test_payload_with_only_the_known_fields_is_still_accepted():
    """Near-miss: the exact legitimate payload — every known field, nothing
    else — is not refused by the closed-shape check. Without this, a
    checker that refuses every payload would equally satisfy the two plants
    above."""
    record = composition_record_from_payload(
        dict(_LEGITIMATE_PAYLOAD), REPO_PACKAGES_ROOT
    )
    assert record.product == "erp"
    assert record.distribution == "dotmac-accounting"


def test_derived_only_field_refusal_stays_a_distinct_message_from_unknown_key():
    """The existing derived-only refusal
    (`migration_lineage_manifest_applies`, a real field name that simply may
    never appear on an input payload) must keep producing ITS OWN message —
    naming the derived/legacy-field defect — rather than being swallowed by
    the generic unknown-key path this change adds. Two different defects
    (authoring a derivation vs. carrying undeclared content) deserve two
    different diagnoses."""
    payload = {**_LEGITIMATE_PAYLOAD, "migration_lineage_manifest_applies": True}
    with pytest.raises(IncompatibleSchemaVersion) as excinfo:
        composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    message = str(excinfo.value)
    assert "migration_lineage_manifest_applies" in message
    assert "derived/legacy field" in message
    assert "closed shape" not in message


# ---------------------------------------------------------------------------
# 5d. The envelope reader — the shared document shape Academy, ERP, and Sub
#     each publish (`{schema_version, product, starter_catalogue_revision,
#     records}`), closed the same way the record shape above is closed.
# ---------------------------------------------------------------------------

#: A valid 40-lowercase-hex `starter_catalogue_revision`. Not a real commit
#: — `composition_records_from_envelope` only validates shape, never
#: ancestry, so a fixture SHA that never existed is exactly the right
#: fixture for exercising that boundary.
_VALID_REVISION = "a" * 40

#: Two real, coherent `optional-module` rows for product "erp" — reusing
#: `_LEGITIMATE_PAYLOAD` for the first row keeps it provably identical to
#: the record-level near-miss above; the second row is the same shape
#: against a different real distribution, so the envelope tests exercise
#: more than one row.
_ENVELOPE_ROW_ACCOUNTING = dict(_LEGITIMATE_PAYLOAD)
_ENVELOPE_ROW_BILLING = {**_LEGITIMATE_PAYLOAD, "distribution": "dotmac-billing"}

#: The exact legitimate envelope every closed-shape test below either
#: reuses unmodified (the near-miss / acceptance tests) or mutates with one
#: offending change — same discipline as `_LEGITIMATE_PAYLOAD` above.
_LEGITIMATE_ENVELOPE = {
    "schema_version": schema.CURRENT_SCHEMA_VERSION,
    "product": "erp",
    "starter_catalogue_revision": _VALID_REVISION,
    "records": [_ENVELOPE_ROW_ACCOUNTING, _ENVELOPE_ROW_BILLING],
}


def test_a_real_three_product_shaped_envelope_is_accepted_and_records_match():
    """The base case: a document shaped exactly like what Academy, ERP, and
    Sub publish, with more than one row, is accepted, and the records
    returned correspond to the input rows (right product, right
    distributions, in order)."""
    records = composition_records_from_envelope(
        dict(_LEGITIMATE_ENVELOPE), REPO_PACKAGES_ROOT
    )
    assert [r.distribution for r in records] == [
        "dotmac-accounting",
        "dotmac-billing",
    ]
    assert all(r.product == "erp" for r in records)


def test_envelope_with_only_known_fields_and_valid_rows_is_accepted():
    """Near-miss: every top-level field and every row field legitimate,
    nothing extra. Without this, a reader that refuses every envelope would
    equally satisfy every refusal test below."""
    records = composition_records_from_envelope(
        dict(_LEGITIMATE_ENVELOPE), REPO_PACKAGES_ROOT
    )
    assert len(records) == 2


def test_envelope_with_an_unknown_top_level_field_is_refused_naming_it():
    """The three-repository defect in miniature: a document carrying an
    extra top-level key (`signing_key`, standing in for the kind of stray
    field the record-level fix already refuses) must be refused, naming it,
    the same way an unrecognized record field is."""
    envelope = {**_LEGITIMATE_ENVELOPE, "signing_key": "AKIAIOSFODNN7EXAMPLE"}
    with pytest.raises(IncompatibleSchemaVersion, match="signing_key"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_missing_a_required_top_level_field_is_refused_naming_it():
    envelope = dict(_LEGITIMATE_ENVELOPE)
    del envelope["starter_catalogue_revision"]
    with pytest.raises(IncompatibleSchemaVersion, match="starter_catalogue_revision"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_a_stale_schema_version_is_refused_naming_it():
    envelope = {
        **_LEGITIMATE_ENVELOPE,
        "schema_version": schema.LEGACY_SCHEMA_VERSION_V1,
    }
    with pytest.raises(IncompatibleSchemaVersion, match="dimensional-composition.v1"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_an_empty_product_is_refused():
    envelope = {**_LEGITIMATE_ENVELOPE, "product": ""}
    with pytest.raises(IncompatibleSchemaVersion, match="product"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_a_malformed_starter_catalogue_revision_is_refused():
    """Shape only: not 40 lowercase hex characters. This test says nothing
    about, and must never be read as testing, whether a well-shaped
    revision names a real or protected-main-ancestor commit — that is
    explicitly out of scope for this function (see its docstring)."""
    envelope = {**_LEGITIMATE_ENVELOPE, "starter_catalogue_revision": "not-a-sha"}
    with pytest.raises(IncompatibleSchemaVersion, match="starter_catalogue_revision"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_records_not_a_list_is_refused():
    envelope = {**_LEGITIMATE_ENVELOPE, "records": "dotmac-accounting"}
    with pytest.raises(IncompatibleSchemaVersion, match="records"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_empty_records_is_refused_not_read_as_a_vacuous_pass():
    """The specific control the coordinator asked to be distinguished: an
    empty `records` list is refused outright, never returned as an empty
    tuple. A caller catching only `IncompatibleSchemaVersion` and treating
    a successful-but-empty return as "nothing to report" would be exactly
    the vacuous-pass hazard this refusal exists to prevent."""
    envelope = {**_LEGITIMATE_ENVELOPE, "records": []}
    with pytest.raises(IncompatibleSchemaVersion, match="empty"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_with_a_duplicate_distribution_is_refused_naming_it():
    envelope = {
        **_LEGITIMATE_ENVELOPE,
        "records": [_ENVELOPE_ROW_ACCOUNTING, dict(_ENVELOPE_ROW_ACCOUNTING)],
    }
    with pytest.raises(EnvelopeIncoherence, match="dotmac-accounting"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_row_disagreeing_with_the_envelope_product_is_refused():
    mismatched_row = {**_ENVELOPE_ROW_BILLING, "product": "sub"}
    envelope = {
        **_LEGITIMATE_ENVELOPE,
        "records": [_ENVELOPE_ROW_ACCOUNTING, mismatched_row],
    }
    with pytest.raises(EnvelopeIncoherence, match="dotmac-billing"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_envelope_row_still_enforces_the_closed_record_shape():
    """Every row travels through `composition_record_from_payload`, so the
    closed record shape (this same commit series' earlier fix) applies to
    every row in an envelope, not only to a record ingested on its own."""
    tainted_row = {**_ENVELOPE_ROW_BILLING, "api_key": "AKIAIOSFODNN7EXAMPLE"}
    envelope = {
        **_LEGITIMATE_ENVELOPE,
        "records": [_ENVELOPE_ROW_ACCOUNTING, tainted_row],
    }
    with pytest.raises(IncompatibleSchemaVersion, match="api_key"):
        composition_records_from_envelope(envelope, REPO_PACKAGES_ROOT)


def test_payload_ingestion_propagates_a_contradictory_manifest_refusal(
    tmp_path: Path,
):
    """The refusal propagates, rather than being swallowed and defaulted: a
    payload naming a distribution whose real manifest is contradictory
    (Ruling 1 outcome 3) must raise `ManifestDeclarationError` out of
    `composition_record_from_payload` itself — ingestion never catches this
    and falls back to a guessed applicability."""
    package_dir = tmp_path / "dotmac-contradictory-ingest"
    src_dir = package_dir / "src" / "dotmac_contradictory_ingest"
    src_dir.mkdir(parents=True)
    (package_dir / "EXTRACTION.toml").write_text(
        'package = "dotmac-contradictory-ingest"\n'
        'classification = "optional-module"\n'
    )
    (src_dir / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        "module = ModuleManifest(\n"
        '    code="contradictory_ingest",\n'
        '    version="0.1.0a1",\n'
        "    core=False,\n"
        '    short_code="ci",\n'
        ")\n"
    )
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": "dotmac-contradictory-ingest",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(ManifestDeclarationError, match="dotmac-contradictory-ingest"):
        composition_record_from_payload(payload, tmp_path)


def test_universal_facility_payload_is_accepted_and_not_applicable_through_ingestion():
    """Zero ingestion coverage for any non-`optional-module` classification
    let a real defect through unnoticed: every payload elsewhere in this
    file declares `"classification": "optional-module"`, so the guard in
    `composition_record_from_payload` that SKIPS the manifest read for
    platform-baseline distributions (`if classification is
    PackageClassification.OPTIONAL_MODULE`) was never exercised — invert or
    delete it and every Academy record (`dotmac-kernel` as
    `universal-facility`, `dotmac-ui` as `presentation-foundation`, which
    together are Academy's entire composed set) would raise
    `ManifestDeclarationError` (there is no `manifest.py` under
    `packages/dotmac-kernel/` for a universal-facility distribution to
    read), and no test would fail. This ingests a real `dotmac-kernel`
    payload with both module dimensions `not_applicable` and asserts it is
    ACCEPTED and derives `not_applicable`, never touching the manifest
    reader at all."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "academy",
        "distribution": "dotmac-kernel",
        "classification": "universal-facility",
        "installation": "true",
        "module_registration": "not_applicable",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
    }
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    assert derive_composition_state(record) == CompositionState.NOT_APPLICABLE


def test_presentation_foundation_payload_is_accepted_and_not_applicable():
    """Companion to the `universal-facility` ingestion test above —
    together `dotmac-kernel` and `dotmac-ui` are the whole of one product's
    (Academy's) composed-distribution record, so both platform-baseline
    classifications need direct ingestion coverage, not just one."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "academy",
        "distribution": "dotmac-ui",
        "classification": "presentation-foundation",
        "installation": "true",
        "module_registration": "not_applicable",
        "migration_lineage": "not_applicable",
        "runtime_consumption": "unknown",
    }
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    assert derive_composition_state(record) == CompositionState.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# 6. runtime_consumption is measured, never inferred.
# ---------------------------------------------------------------------------


def _step_reads_runtime_consumption_as_code(step) -> bool:
    """True iff `step`'s AST contains a real `record.runtime_consumption`
    attribute access — never a substring match, which would also fire on
    the several step docstrings that legitimately EXPLAIN runtime_consumption
    (e.g. `_step_installation_absent`'s docstring, which discusses it at
    length without the executable code ever touching it)."""
    tree = ast.parse(inspect.getsource(step))
    return any(
        isinstance(node, ast.Attribute) and node.attr == "runtime_consumption"
        for node in ast.walk(tree)
    )


def _is_real_state_return_value(value: ast.expr | None) -> bool:
    """A `return` with no value, or a bare `return None`, is not a returned
    `CompositionState` — only a real value (e.g. `CompositionState.FULLY_COMPOSED`)
    counts as "deciding a state"."""
    if value is None:
        return False
    return not (isinstance(value, ast.Constant) and value.value is None)


def _link_ast_parents(root: ast.AST) -> None:
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]


def _references_runtime_consumption(node: ast.AST, tainted_names: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "runtime_consumption":
            return True
        if isinstance(sub, ast.Name) and sub.id in tainted_names:
            return True
    return False


def _collect_runtime_consumption_tainted_names(func_node: ast.FunctionDef) -> set[str]:
    """One level of variable-assignment taint tracking, fixed-point over
    chained assignments: a local name assigned from an expression that
    itself references `runtime_consumption` (directly or via an
    already-tainted name) becomes tainted too. Enough to catch the realistic
    near-miss where a step reads the dimension into an intermediate
    variable (e.g. `exposed = record.runtime_consumption is TRUE`) before
    branching on it — a plain "is the attribute inside this exact node"
    check would miss that indirection entirely."""
    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign) and _references_runtime_consumption(
                node.value, tainted
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in tainted:
                        tainted.add(target.id)
                        changed = True
    return tainted


def _step_returns_a_state_derived_from_runtime_consumption(step) -> bool:
    """True iff ANY `return <real state>` in `step` is influenced by
    `runtime_consumption` — either directly in the returned expression, or
    indirectly because it is nested inside an `if` (at any enclosing depth)
    whose test reads the dimension, directly or through a tainted local
    variable. This is the actual forbidden shape the module docstring
    names: "forbidden to let it decide a *returned* `CompositionState`."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(step)))
    func_node = tree.body[0]
    assert isinstance(func_node, ast.FunctionDef)
    _link_ast_parents(func_node)
    tainted = _collect_runtime_consumption_tainted_names(func_node)

    for node in ast.walk(func_node):
        if not (
            isinstance(node, ast.Return) and _is_real_state_return_value(node.value)
        ):
            continue
        if _references_runtime_consumption(node.value, tainted):
            return True
        ancestor = getattr(node, "parent", None)
        while ancestor is not None and ancestor is not func_node:
            if isinstance(ancestor, ast.If) and _references_runtime_consumption(
                ancestor.test, tainted
            ):
                return True
            ancestor = getattr(ancestor, "parent", None)
    return False


def test_runtime_consumption_participates_only_as_a_contradiction_raise():
    """Structural proof, over the pipeline STEPS that actually decide
    states, not over `derive_composition_state` itself — that function is a
    twelve-line loop over `_DERIVATION_PIPELINE` and so cannot contain the
    string `"runtime_consumption"` under any implementation, correct or
    broken; asserting its absence there (the previous form of this test) was
    a tautology.

    Two real properties, checked directly against the step functions' real
    code (AST attribute access, never a docstring substring — several other
    steps' DOCSTRINGS legitimately discuss `runtime_consumption` without
    their code ever reading it):

    1. Exactly one step (`_step_refuse_contradictions`) reads
       `record.runtime_consumption` in code at all — a second step reading
       it would be a NEW leak this test must catch, which asserting only
       the dispatch loop's source could never do.
    2. NO step returns a real `CompositionState` that is influenced by
       `runtime_consumption` — directly in the return expression, or
       indirectly via an enclosing `if` (through any depth of nesting, and
       through one level of variable-assignment taint tracking). It is
       legitimate to RAISE `DimensionalIncoherence` from it (the
       contradiction canary in `_step_refuse_contradictions`); it is
       forbidden to let it decide a returned state — exactly the
       distinction the module docstring and
       `test_runtime_consumption_varies_independently_of_the_other_three`
       both promise. Verified against two planted near-misses this
       function-level check catches (both direct and indirect-via-variable)
       and, separately, that the real shipped pipeline is clean.
    """
    steps_referencing_it = [
        step
        for step in schema._DERIVATION_PIPELINE
        if _step_reads_runtime_consumption_as_code(step)
    ]
    assert [step.__name__ for step in steps_referencing_it] == [
        "_step_refuse_contradictions"
    ], "runtime_consumption must be read in code by exactly the contradiction step"

    for step in schema._DERIVATION_PIPELINE:
        assert not _step_returns_a_state_derived_from_runtime_consumption(step), (
            f"{step.__name__} returns a CompositionState influenced by "
            "runtime_consumption — forbidden even via an enclosing `if` or "
            "an intermediate variable"
        )

    # Sensitivity, PLANT half — direct: runtime_consumption drives the
    # returned value's own if-condition.
    class _FakeStepDirect:
        __name__ = "_fake_step_direct"

    def _fake_step_direct(record):  # synthetic probe
        if record.runtime_consumption is DimensionValue.TRUE:
            return CompositionState.FULLY_COMPOSED
        return None

    assert _step_returns_a_state_derived_from_runtime_consumption(_fake_step_direct)

    # Sensitivity, PLANT half — indirect: the dimension is read into a local
    # variable first, then that variable's `if` gates a real-state return.
    def _fake_step_indirect(record):  # synthetic probe
        exposed = record.runtime_consumption is DimensionValue.TRUE
        if exposed:
            return CompositionState.FULLY_COMPOSED
        return None

    assert _step_returns_a_state_derived_from_runtime_consumption(_fake_step_indirect)

    # Sensitivity, NEAR-MISS half — a step that reads runtime_consumption
    # but only ever raises from it (the real, legitimate shape) is NOT
    # flagged.
    assert not _step_returns_a_state_derived_from_runtime_consumption(
        schema._step_refuse_contradictions
    )


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


def test_dossier_package_name_must_match_its_directory(tmp_path: Path):
    """A dossier cannot redirect one package directory to another identity."""
    package_dir = tmp_path / "dotmac-directory-name"
    package_dir.mkdir()
    (package_dir / "EXTRACTION.toml").write_text(
        'package = "dotmac-different-name"\nclassification = "optional-module"\n'
    )

    with pytest.raises(
        CatalogueDerivationError, match="dotmac-different-name.*dotmac-directory-name"
    ):
        derive_distribution_universe(tmp_path)


def test_dossier_package_name_matching_its_directory_is_accepted(tmp_path: Path):
    """Near-miss: the directory/name binding rejects only disagreement."""
    package_dir = tmp_path / "dotmac-same-name"
    package_dir.mkdir()
    (package_dir / "EXTRACTION.toml").write_text(
        'package = "dotmac-same-name"\nclassification = "optional-module"\n'
    )

    assert derive_distribution_universe(tmp_path) == (
        PackageDossier(
            distribution="dotmac-same-name",
            classification=PackageClassification.OPTIONAL_MODULE,
        ),
    )


def test_distribution_universe_refuses_a_symlinked_package_directory(tmp_path: Path):
    """The catalogue cannot be redirected to a dossier outside its root."""
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    outside = tmp_path / "outside-package"
    outside.mkdir()
    (outside / "EXTRACTION.toml").write_text(
        'package = "dotmac-linked"\nclassification = "optional-module"\n'
    )
    (packages_root / "dotmac-linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CatalogueDerivationError, match="symlink"):
        derive_distribution_universe(packages_root)


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
    """`PackageClassification.STATELESS_CONTRACT_CATALOGUE` has no dossier in
    THIS test's fixed real-tree snapshot as of this commit (see the module
    docstring) — an unexercised branch proves nothing about its own
    correctness, so this builds a synthetic dossier declaring it and proves
    the derivation both accepts it and preserves the exact enum member.

    Deliberately does NOT also assert the classification is absent from the
    real `packages/` tree: `.github/release-contracts.json` documents this
    as an ACTIVE, deliberately-empty lane — seven candidate catalogues await
    a kernel grammar before any of them can adopt this classification — so a
    legitimate real dossier landing tomorrow would fail an absence assertion
    for a reason unrelated to what this test is actually proving."""
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
    universe appears anywhere in `composition_schema.py`'s source. The
    count is re-derived HERE, from `REPO_PACKAGES_ROOT.iterdir()`, at test
    time — never asserted as a fixed number in this test or in the module
    under test — precisely because that count is not stable: it was 95 at
    the time this test was last corrected, up from an earlier count that
    predated the later addition of `dotmac-runner-transport` and
    `dotmac-runner-transport-github-actions`, and a hard-coded number here
    would itself become exactly the kind of check that answers without
    being able to refuse a real change to the tree."""
    source = inspect.getsource(schema)
    real_count = len([p for p in REPO_PACKAGES_ROOT.iterdir() if p.is_dir()])
    assert str(real_count) not in source, (
        f"composition_schema.py's source contains the literal "
        f"{real_count!r} — the catalogue universe's size must never be a "
        "hard-coded constant"
    )


def test_package_dossier_holds_only_distribution_and_classification():
    """`PackageDossier` is deliberately narrow — this module needs nothing
    else from an `EXTRACTION.toml` dossier, and `adoption_evidence.py`
    (a different owner) is where the rest of that file's meaning lives."""
    fields = set(PackageDossier.__dataclass_fields__)
    assert fields == {"distribution", "classification"}


# ---------------------------------------------------------------------------
# 10. Ruling 1 — migration-lineage applicability consults the validated
#     manifest contract, alongside classification. Read by AST, never by
#     name/comment/count.
# ---------------------------------------------------------------------------


def test_document_rendering_real_manifest_declares_no_lineage_by_ast():
    """The live shape driving this ruling: `dotmac-document-rendering`
    (classification `optional-module`) declares only `code`, `version`,
    `core` in its real `ModuleManifest(...)` call — no `short_code`, no
    `migration_prefix`, no `tables`, no `platform_tables`, no
    `migration_branch`. Its own source even carries the comment
    "Deliberately no short_code, migration prefix, tables or plane
    declaration" — a substring search for `short_code` would match that
    COMMENT and wrongly conclude the keyword is present; this asserts the
    AST-derived answer is the honest one: lineage does not apply."""
    result = derive_migration_lineage_applicability_from_manifest(
        REPO_PACKAGES_ROOT, "dotmac-document-rendering"
    )
    assert result is False


def test_a_real_stateful_manifest_declares_lineage_applies_by_ast():
    """Near-miss/positive companion to the test above, against a real,
    ordinary stateful `optional-module` manifest (`dotmac-billing`): both
    `short_code` and `migration_prefix` are declared, so lineage applies."""
    result = derive_migration_lineage_applicability_from_manifest(
        REPO_PACKAGES_ROOT, "dotmac-billing"
    )
    assert result is True


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """Identity set of every string-literal AST node that IS a docstring
    (the first statement of a module/function/class body). Used to exempt
    legitimate documentation/prose — e.g. this module's own explanatory
    mentions of `dotmac-document-rendering` as an EXAMPLE — from the
    forbidden-name sweep below, while still catching a real hard-coded
    lookup anywhere else in the module's executable code, including inside
    a helper the narrow, single-function version of this check never read
    (e.g. `_is_declared_empty`)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        is_scope_node = isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        )
        if is_scope_node:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _source_names_any_forbidden_string(source: str, forbidden_names) -> str | None:
    """Returns the first forbidden name found in a non-docstring string
    literal anywhere in `source`'s AST, or `None` if none is found. AST-
    based, not a raw substring scan of the file text, so legitimate prose
    is exempt (see `_docstring_constant_ids`) — only executable string
    literals (e.g. a hard-coded lookup tuple/dict key/membership test) can
    trip it."""
    tree = ast.parse(textwrap.dedent(source))
    exempt = _docstring_constant_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in exempt
        ):
            for name in forbidden_names:
                if name in node.value:
                    return name
    return None


def test_no_distribution_name_or_hard_coded_count_governs_lineage_applicability():
    """Structural proof that the derivation is keyword-structure-driven, not
    name- or count-driven.

    The name check is widened to the WHOLE module (`inspect.getsource
    (schema)`) — matching `test_universe_size_is_never_hard_coded_anywhere_
    in_this_module`'s scope for the catalogue universe elsewhere in this
    module — because the earlier, single-function version
    (`inspect.getsource(derive_migration_lineage_applicability_from_
    manifest)` alone) was satisfiable by a module-level
    `_STATELESS = ("dotmac-document-rendering",)` tuple, a count constant, a
    dict lookup, or by moving any of those into `_is_declared_empty`, which
    the narrow scan never read.

    Carries a sensitivity PLANT — the only structural test in this file that
    previously had none: a synthetic name-driven implementation of exactly
    that shape IS shown to be rejected by the same detector this test uses
    on the real module, proving the detector can name its own violation
    rather than merely never having encountered one. A near-miss (the
    identical shape, minus the hard-coded name) is shown NOT flagged,
    proving the detector isn't simply refusing everything."""
    forbidden_names = (
        "document-rendering",
        "document_rendering",
        "dotmac-billing",
        "dotmac_billing",
    )

    hit = _source_names_any_forbidden_string(inspect.getsource(schema), forbidden_names)
    assert hit is None, (
        f"composition_schema.py's executable code contains forbidden name "
        f"{hit!r} outside a docstring — applicability must be derived from "
        "manifest structure, never a hard-coded distribution name"
    )

    # Numeric-literal count check stays scoped to the one function whose job
    # is deciding applicability — a whole-module numeric-literal ban is not
    # meaningful (the module legitimately contains many numbers, e.g.
    # `_is_declared_empty`'s own `len(value.elts) == 0`).
    source = inspect.getsource(derive_migration_lineage_applicability_from_manifest)
    tree = ast.parse(textwrap.dedent(source))
    func_node = tree.body[0]
    assert isinstance(func_node, ast.FunctionDef)
    body_without_docstring = (
        func_node.body[1:] if ast.get_docstring(func_node) else (func_node.body)
    )
    for statement in body_without_docstring:
        for node in ast.walk(statement):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int | float)
                and not isinstance(node.value, bool)
            ):
                raise AssertionError(
                    "derive_migration_lineage_applicability_from_manifest's "
                    f"executable body contains numeric literal {node.value!r} "
                    "— no count of stateless modules may be encoded"
                )

    # Sensitivity PLANT: a synthetic name-driven implementation of exactly
    # the shape the widened scan above exists to catch — a module-level
    # lookup tuple naming the real distribution — is shown to be rejected.
    planted_violation_source = (
        "def _fake_lineage_applicability(distribution):\n"
        '    STATELESS_DISTRIBUTIONS = ("dotmac-document-rendering",)\n'
        "    return distribution not in STATELESS_DISTRIBUTIONS\n"
    )
    planted_hit = _source_names_any_forbidden_string(
        planted_violation_source, forbidden_names
    )
    assert planted_hit == "document-rendering", (
        "the forbidden-name detector failed to catch its own named "
        "violation — a name-driven implementation must be rejected"
    )

    # NEAR MISS half: the identical shape, minus the hard-coded name (a
    # keyword-structure-driven check, the real shape this module ships), is
    # not flagged — proving the detector distinguishes the two rather than
    # refusing everything indiscriminately.
    near_miss_source = (
        "def _fake_lineage_applicability(has_identity):\n    return has_identity\n"
    )
    assert _source_names_any_forbidden_string(near_miss_source, forbidden_names) is None


def test_manifest_with_short_code_but_no_migration_prefix_is_refused_by_name(
    tmp_path: Path,
):
    """Outcome 3, sensitivity PLANT: a manifest that parses cleanly and is
    genuinely internally contradictory — `short_code` declared with no
    `migration_prefix` — must be refused BY NAME
    (`ManifestDeclarationError`), not silently resolved to either
    applicability. This is the harder, more realistic case the brief calls
    out: an absent/unparseable file is the easy path and the less likely
    one to occur in practice."""
    package_dir = tmp_path / "dotmac-half-declared"
    src_dir = package_dir / "src" / "dotmac_half_declared"
    src_dir.mkdir(parents=True)
    (src_dir / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        "module = ModuleManifest(\n"
        '    code="half_declared",\n'
        '    version="0.1.0a1",\n'
        "    core=False,\n"
        '    short_code="half",\n'
        ")\n"
    )
    with pytest.raises(ManifestDeclarationError, match="dotmac-half-declared"):
        derive_migration_lineage_applicability_from_manifest(
            tmp_path, "dotmac-half-declared"
        )


def test_manifest_declaring_tables_but_no_identity_is_refused_by_name(
    tmp_path: Path,
):
    """Outcome 3, second PLANT: a "stateless" pair (no `short_code`, no
    `migration_prefix`) contradicted by a declared `tables` keyword — the
    manifest parses fine, but claims to own lineage-bearing state while
    naming no migration identity for it. Refused by name, not defaulted."""
    package_dir = tmp_path / "dotmac-contradictory"
    src_dir = package_dir / "src" / "dotmac_contradictory"
    src_dir.mkdir(parents=True)
    (src_dir / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        "module = ModuleManifest(\n"
        '    code="contradictory",\n'
        '    version="0.1.0a1",\n'
        "    core=False,\n"
        '    tables=("some_table",),\n'
        ")\n"
    )
    with pytest.raises(ManifestDeclarationError, match="dotmac-contradictory"):
        derive_migration_lineage_applicability_from_manifest(
            tmp_path, "dotmac-contradictory"
        )


def test_coherent_stateful_and_stateless_manifests_are_not_refused_near_miss(
    tmp_path: Path,
):
    """Sensitivity NEAR-MISS half for both plants above: the identical
    shapes, minus the contradiction, are accepted — proving the refusals
    above are triggered by the contradiction specifically, not by some
    unrelated property of the fixture (e.g. the temp directory itself, or
    the presence of any keyword at all)."""
    coherent_stateful = tmp_path / "dotmac-coherent-stateful"
    src_a = coherent_stateful / "src" / "dotmac_coherent_stateful"
    src_a.mkdir(parents=True)
    (src_a / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        "module = ModuleManifest(\n"
        '    code="coherent_stateful",\n'
        '    version="0.1.0a1",\n'
        "    core=False,\n"
        '    short_code="coh",\n'
        '    migration_prefix="co",\n'
        '    tables=("some_table",),\n'
        ")\n"
    )
    assert (
        derive_migration_lineage_applicability_from_manifest(
            tmp_path, "dotmac-coherent-stateful"
        )
        is True
    )

    coherent_stateless = tmp_path / "dotmac-coherent-stateless"
    src_b = coherent_stateless / "src" / "dotmac_coherent_stateless"
    src_b.mkdir(parents=True)
    (src_b / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        "module = ModuleManifest(\n"
        '    code="coherent_stateless",\n'
        '    version="0.1.0a1",\n'
        "    core=False,\n"
        ")\n"
    )
    assert (
        derive_migration_lineage_applicability_from_manifest(
            tmp_path, "dotmac-coherent-stateless"
        )
        is False
    )


def _write_optional_module_fixture(
    packages_root: Path, distribution: str, manifest_source: str
) -> None:
    package_dir = packages_root / distribution
    source_dir = package_dir / "src" / distribution.replace("-", "_")
    source_dir.mkdir(parents=True)
    (package_dir / "EXTRACTION.toml").write_text(
        f'package = "{distribution}"\nclassification = "optional-module"\n'
    )
    (source_dir / "manifest.py").write_text(manifest_source)


def test_ingestion_refuses_a_decoy_manifest_call_before_the_real_export(
    tmp_path: Path,
):
    """One canonical export plus any second call is ambiguous, not harmless."""
    distribution = "dotmac-ambiguous"
    _write_optional_module_fixture(
        tmp_path,
        distribution,
        "from dotmac_kernel.modules import ModuleManifest\n"
        "def decoy():\n"
        '    return ModuleManifest(code="decoy", version="0.1.0a1", core=False)\n'
        "module = ModuleManifest(\n"
        '    code="ambiguous", version="0.1.0a1", core=False,\n'
        '    short_code="amb", migration_prefix="am",\n'
        ")\n",
    )
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": distribution,
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(ManifestDeclarationError, match="exactly one module-level"):
        composition_record_from_payload(payload, tmp_path)


def test_ingestion_refuses_a_function_local_manifest_without_a_module_export(
    tmp_path: Path,
):
    """A lone call in executable scope is not the exported manifest."""
    distribution = "dotmac-function-local"
    _write_optional_module_fixture(
        tmp_path,
        distribution,
        "from dotmac_kernel.modules import ModuleManifest\n"
        "def build():\n"
        "    return ModuleManifest(\n"
        '        code="function_local", version="0.1.0a1", core=False,\n'
        '        short_code="fnl", migration_prefix="fl",\n'
        "    )\n",
    )
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "starter",
        "distribution": distribution,
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
    }
    with pytest.raises(ManifestDeclarationError, match="exactly one module-level"):
        composition_record_from_payload(payload, tmp_path)


def test_manifest_distribution_traversal_is_refused(tmp_path: Path):
    with pytest.raises(ManifestDeclarationError, match="safe single"):
        derive_migration_lineage_applicability_from_manifest(tmp_path, "../escape")


def test_manifest_symlink_escape_is_refused(tmp_path: Path):
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    outside = tmp_path / "outside"
    source_dir = outside / "src" / "dotmac_escape"
    source_dir.mkdir(parents=True)
    (source_dir / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        'module = ModuleManifest(code="escape", version="0.1.0a1", core=False)\n'
    )
    (packages_root / "dotmac-escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ManifestDeclarationError, match="symlink"):
        derive_migration_lineage_applicability_from_manifest(
            packages_root, "dotmac-escape"
        )


def test_manifest_intermediate_src_symlink_is_refused_inside_root(tmp_path: Path):
    """Containment alone cannot detect an in-root intermediate redirect."""
    packages_root = tmp_path / "packages"
    package_dir = packages_root / "dotmac-intermediate"
    package_dir.mkdir(parents=True)
    redirected_src = packages_root / "redirected-src"
    manifest_dir = redirected_src / "dotmac_intermediate"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.py").write_text(
        "from dotmac_kernel.modules import ModuleManifest\n"
        'module = ModuleManifest(code="redirected", version="0.1.0a1", core=False)\n'
    )
    (package_dir / "src").symlink_to(redirected_src, target_is_directory=True)

    with pytest.raises(ManifestDeclarationError, match="src.*symlink"):
        derive_migration_lineage_applicability_from_manifest(
            packages_root, "dotmac-intermediate"
        )


def test_absent_manifest_file_is_refused_by_name(tmp_path: Path):
    """The easy outcome-3 case — no manifest.py at all — still refused by
    name rather than defaulted. Named explicitly per the brief: this is the
    LEAST likely real-world case, not the only one exercised."""
    with pytest.raises(ManifestDeclarationError, match="dotmac-missing"):
        derive_migration_lineage_applicability_from_manifest(tmp_path, "dotmac-missing")


def test_a_registered_stateless_optional_module_derives_fully_composed():
    """Ruling 1's load-bearing consequence: `_step_registration_lineage_
    table` must READ applicability rather than infer "lineage absent" from
    a `False` dimension value. `dotmac-document-rendering`, registered
    against a consumed assembly, with `migration_lineage` correctly recorded
    `not_applicable` (its manifest declares no lineage at all — see the AST
    test above) must derive `FULLY_COMPOSED`, never `INVALID` — `INVALID` is
    reserved for a module that genuinely owns lineage and does not have it,
    not for a module that never had a lineage question to begin with."""
    registered_stateless = _derived_record(
        product="starter",
        distribution="dotmac-document-rendering",
        classification=PackageClassification.OPTIONAL_MODULE,
        installation=TRUE,
        module_registration=TRUE,
        migration_lineage=NA,
        runtime_consumption=UNKNOWN,
        manifest_applies=False,
    )
    state = derive_composition_state(registered_stateless)
    assert state == CompositionState.FULLY_COMPOSED


def test_an_unregistered_stateless_optional_module_derives_not_composed():
    """Companion near-miss: the identical stateless-lineage shape, but NOT
    registered, must still derive `NOT_COMPOSED` — inapplicable lineage
    does not manufacture composition on its own; registration still has to
    hold."""
    unregistered_stateless = _derived_record(
        product="starter",
        distribution="dotmac-document-rendering",
        classification=PackageClassification.OPTIONAL_MODULE,
        installation=TRUE,
        module_registration=FALSE,
        migration_lineage=NA,
        runtime_consumption=UNKNOWN,
        manifest_applies=False,
    )
    state = derive_composition_state(unregistered_stateless)
    assert state == CompositionState.NOT_COMPOSED


def test_stateless_optional_module_cannot_record_lineage_true_or_false():
    """`_check_applicability` must refuse the OTHER direction too once
    `migration_lineage_manifest_applies=False`: recording `migration_lineage
    = true` (or `false`) for a distribution whose manifest says lineage does
    not apply is exactly as wrong as the platform-baseline case — the
    dimension isn't a real question for this distribution, so any value
    other than `not_applicable` is refused."""
    with pytest.raises(ValueError, match="not_applicable"):
        _derived_record(
            product="starter",
            distribution="dotmac-document-rendering",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=TRUE,
            module_registration=TRUE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
            manifest_applies=False,
        )


def test_direct_optional_module_construction_has_no_applicability_fallback():
    """A direct constructor has no authoritative manifest fact to use.

    It therefore refuses every optional-module record instead of preserving
    the former ``None -> lineage applies`` compatibility fallback.
    """
    with pytest.raises(TypeError, match="composition_record_from_payload"):
        CompositionRecord(
            product="erp",
            distribution="dotmac-example",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=TRUE,
            module_registration=TRUE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
        )


def test_direct_constructor_cannot_accept_manifest_applicability():
    """The generated constructor has no slot for a derived authority fact."""
    with pytest.raises(TypeError):
        CompositionRecord(  # type: ignore[call-arg]
            product="erp",
            distribution="dotmac-example",
            classification=PackageClassification.OPTIONAL_MODULE,
            installation=TRUE,
            module_registration=TRUE,
            migration_lineage=TRUE,
            runtime_consumption=UNKNOWN,
            migration_lineage_manifest_applies=True,  # type: ignore[call-arg]
        )


def test_dataclasses_replace_cannot_reopen_the_constructor_seam():
    """A valid parsed record cannot be cloned around the refusing constructor."""
    payload = {
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "product": "erp",
        "distribution": "dotmac-billing",
        "classification": "optional-module",
        "installation": "true",
        "module_registration": "true",
        "migration_lineage": "true",
        "runtime_consumption": "unknown",
    }
    record = composition_record_from_payload(payload, REPO_PACKAGES_ROOT)
    with pytest.raises(TypeError, match="no public constructor"):
        replace(record, product="attacker-authored")


def test_record_validation_refuses_non_boolean_applicability_after_a_bypass():
    """Even ``object.__new__`` cannot make a truthy value authoritative."""
    record = object.__new__(CompositionRecord)
    for name, value in (
        ("product", "erp"),
        ("distribution", "dotmac-example"),
        ("classification", PackageClassification.OPTIONAL_MODULE),
        ("installation", TRUE),
        ("module_registration", TRUE),
        ("migration_lineage", TRUE),
        ("runtime_consumption", UNKNOWN),
        ("_CompositionRecord__migration_lineage_manifest_applies", 1),
    ):
        object.__setattr__(record, name, value)
    with pytest.raises(TypeError, match="derived bool"):
        record.__post_init__()


# ---------------------------------------------------------------------------
# 11. Ruling 2 — `installation = FALSE` may not clear on an unknown. Already
#     the shipped behaviour (verified directly above by execution against
#     the pinned pipeline); what this section adds is the PIN — a named test
#     asserting it is a deliberate choice, with a real sensitivity plant
#     showing the wrong answer a naive reordering would have produced.
# ---------------------------------------------------------------------------


def test_installation_false_with_applicable_unknown_is_evidence_incomplete():
    """The ruled property, stated directly: `installation = false` together
    with an APPLICABLE `module_registration` (or `migration_lineage`) left
    `unknown` derives `evidence_incomplete`, never `not_composed`. This is
    already guaranteed by the shipped pipeline order (step 2, the unknown
    refusal, runs strictly before step 4, the installation-absent
    derivation) — this test pins it as a named, deliberate property rather
    than a by-product nobody asserted directly."""
    record = _optional_module_record(
        installation=FALSE, module_registration=UNKNOWN, migration_lineage=FALSE
    )
    assert derive_composition_state(record) == CompositionState.EVIDENCE_INCOMPLETE

    record_lineage_unknown = _optional_module_record(
        installation=FALSE, module_registration=FALSE, migration_lineage=UNKNOWN
    )
    assert (
        derive_composition_state(record_lineage_unknown)
        == CompositionState.EVIDENCE_INCOMPLETE
    )


def test_reordered_pipeline_clearing_installation_false_first_gives_wrong_answer():
    """Sensitivity proof for the Ruling 2 pin above, in the same style as
    `test_pipeline_ordering_is_pinned_not_incidental`: build a pipeline
    variant that moves `_step_installation_absent` (step 4) AHEAD of
    `_step_refuse_required_unknown` (step 2) — exactly the future mistake
    the pin exists to prevent, a reader short-circuiting on
    `installation = false` before checking for unmeasured applicable
    dimensions. Run the SAME record used above through it and show it
    reaches `not_composed` — the WRONG answer, since `module_registration`
    was never actually measured. An assertion that the shipped pipeline
    returns `evidence_incomplete` would not be this proof; this asserts the
    REORDERED pipeline's own wrong output."""
    record = _optional_module_record(
        installation=FALSE, module_registration=UNKNOWN, migration_lineage=FALSE
    )

    # Confirm the shipped pipeline's correct answer first, by execution.
    assert derive_composition_state(record) == CompositionState.EVIDENCE_INCOMPLETE

    reordered_steps = (
        schema._step_validate_coherence,
        schema._step_installation_absent,  # moved ahead of the unknown refusal
        schema._step_refuse_required_unknown,
        schema._step_refuse_contradictions,
        schema._step_not_applicable,
        schema._step_registration_lineage_table,
    )
    assert set(reordered_steps) == set(schema._DERIVATION_PIPELINE)
    assert reordered_steps != schema._DERIVATION_PIPELINE

    reordered_result = None
    for step in reordered_steps:
        reordered_result = step(record)
        if reordered_result is not None:
            break

    assert reordered_result == CompositionState.NOT_COMPOSED, (
        "the reordered pipeline wrongly clears installation=false on an "
        "unmeasured applicable dimension instead of refusing it as "
        "evidence_incomplete — proving the shipped order, not just its "
        "outcome, is what the Ruling 2 pin protects"
    )


# ---------------------------------------------------------------------------
# The installation boundary: `derive_installation_dimension`, the structured
# lock read (`derive_lock_group_membership`), the structured `pyproject.toml`
# group-optionality read (`derive_group_optionality`), and the recipe parse
# (`derive_installation_group_universe` / `_parse_single_recipe_group_
# selection`). Every test below is a real execution of these functions, not
# an assertion of an outcome the code cannot produce differently — see the
# module docstring's "What `installation` means" section for the ruling
# these prove.
# ---------------------------------------------------------------------------

#: A minimal, already-parsed `poetry.lock` document. `dotmac-deployment-
#: foundation` is shaped like ERP's real lock at the time of Michael's
#: ruling: the sole entry in an OPTIONAL `dev` group. `ops` is a
#: NON-OPTIONAL custom group (`dotmac-ops-tool` lives only there,
#: `dotmac-ui` lives in both `main` and `ops`), and `docs` is a second
#: OPTIONAL custom group (`dotmac-docs-tool`) — the two custom-group shapes
#: needed to prove Poetry's real default-set semantics below.
_LOCK_DOCUMENT = {
    "package": [
        {
            "name": "dotmac-deployment-foundation",
            "version": "0.4.0a1",
            "groups": ["dev"],
        },
        {"name": "dotmac-kernel", "version": "1.0.0", "groups": ["main"]},
        {"name": "dotmac-ui", "version": "1.0.0", "groups": ["main", "ops"]},
        {"name": "dotmac-ops-tool", "version": "1.0.0", "groups": ["ops"]},
        {"name": "dotmac-docs-tool", "version": "1.0.0", "groups": ["docs"]},
    ]
}

#: The matching, already-parsed `pyproject.toml` document: `dev` and `docs`
#: are declared OPTIONAL, `ops` is declared explicitly NON-optional.
_PYPROJECT_DOCUMENT = {
    "tool": {
        "poetry": {
            "group": {
                "dev": {"optional": True},
                "ops": {"optional": False},
                "docs": {"optional": True},
            }
        }
    }
}

#: Poetry's computed default install set for `_LOCK_DOCUMENT` +
#: `_PYPROJECT_DOCUMENT`: `main` plus every NON-optional custom group
#: (`ops`) the lock actually resolves packages into. `dev` and `docs` are
#: both optional, so neither is in the default.
_EXPECTED_DEFAULT_GROUPS = frozenset({"main", "ops"})


def _recipe(*flags: tuple[str, str], tool: str = "poetry", subcommand: str = "install"):
    return InstallRecipe(tool=tool, subcommand=subcommand, flags=flags, source="test")


def _universe(recipes, *, group_optionality=None, lock_document=_LOCK_DOCUMENT):
    membership = derive_lock_group_membership(lock_document)
    assert membership is not None
    return derive_installation_group_universe(
        recipes, lock_membership=membership, group_optionality=group_optionality
    )


def _dimension(
    distribution, recipes, *, group_optionality=None, lock_document=_LOCK_DOCUMENT
):
    membership = derive_lock_group_membership(lock_document)
    return derive_installation_dimension(
        distribution=distribution,
        lock_membership=membership,
        recipes=recipes,
        group_optionality=group_optionality,
    )


# --- The structured lock read -----------------------------------------------


def test_lock_group_membership_reads_structured_groups_fields():
    """`derive_lock_group_membership` is a plain structured read — no
    parsing, no heuristic. Proves the shape directly against `_LOCK_DOCUMENT`."""
    membership = derive_lock_group_membership(_LOCK_DOCUMENT)
    assert membership is not None
    assert membership.groups_by_distribution[
        "dotmac-deployment-foundation"
    ] == frozenset({"dev"})
    assert membership.groups_by_distribution["dotmac-kernel"] == frozenset({"main"})
    assert membership.all_declared_groups == frozenset({"main", "dev", "ops", "docs"})


def test_lock_missing_groups_field_on_any_package_is_refused_not_defaulted():
    """The older-lock-format caveat, named explicitly in the docstring: a
    lock with even one entry carrying no `groups` field at all cannot
    honestly answer which groups install where. Refused (`None`), never
    read as 'the packages without groups just don't matter'."""
    old_style = {"package": [{"name": "dotmac-kernel", "version": "1.0.0"}]}
    assert derive_lock_group_membership(old_style) is None


def test_lock_with_no_package_list_is_refused():
    assert derive_lock_group_membership({}) is None
    assert derive_lock_group_membership({"package": []}) is None


# --- The structured pyproject.toml group-optionality read -------------------


def test_group_optionality_reads_declared_optional_flags():
    optionality = derive_group_optionality(_PYPROJECT_DOCUMENT)
    assert optionality == {"dev": True, "ops": False, "docs": True}


def test_group_optionality_omitted_key_reads_as_poetry_default_non_optional():
    """A group table present but with no `optional` key at all is read as
    Poetry's own documented default, `False` — this is Poetry's stated
    semantics, not a guess this module makes."""
    document = {"tool": {"poetry": {"group": {"ops": {}}}}}
    assert derive_group_optionality(document) == {"ops": False}


def test_group_optionality_with_no_group_table_is_the_empty_mapping():
    """No `[tool.poetry.group]` table at all is a complete, honest answer —
    the product declares zero custom groups — not a refusal."""
    document = {"tool": {"poetry": {}}}
    assert derive_group_optionality(document) == {}


def test_group_optionality_refused_when_tool_poetry_is_absent():
    assert derive_group_optionality({}) is None
    assert derive_group_optionality({"tool": {}}) is None


def test_group_optionality_refused_when_a_group_entry_is_not_a_table():
    document = {"tool": {"poetry": {"group": {"ops": "not-a-table"}}}}
    assert derive_group_optionality(document) is None


def test_group_optionality_refused_when_optional_value_is_not_boolean():
    document = {"tool": {"poetry": {"group": {"ops": {"optional": "yes"}}}}}
    assert derive_group_optionality(document) is None


# --- `--only`: exempt from optionality entirely ------------------------------


def test_only_flag_selects_exactly_the_named_groups_needing_no_optionality():
    """`--only` never consults `group_optionality` — proven here by passing
    `None` and still getting a confident answer."""
    recipe = _recipe(("--only", "main"))
    assert _universe((recipe,), group_optionality=None) == frozenset({"main"})


def test_only_flag_accepts_a_comma_separated_group_list():
    recipe = _recipe(("--only", "main,ops"))
    assert _universe((recipe,)) == frozenset({"main", "ops"})


def test_plant_only_with_optional_and_nonoptional_groups_present_still_yields_main():
    """Plant — the regression guard for clause 1: `--only main` must still
    resolve to exactly `{"main"}` even though the lock/pyproject fixture
    used throughout this section declares both an optional custom group
    (`dev`) and a non-optional one (`ops`). `--only` REPLACES the default
    entirely; it never falls back to Poetry's computed default, so neither
    `dev` nor `ops` leaks in regardless of their declared optionality."""
    recipe = _recipe(("--only", "main"))
    assert _universe((recipe,), group_optionality=None) == frozenset({"main"})
    # Confirmed again with real optionality data supplied, proving the
    # branch ignores it rather than merely never needing it in this call:
    assert _universe(
        (recipe,), group_optionality={"dev": True, "ops": False, "docs": True}
    ) == frozenset({"main"})


# --- Bare `poetry install`: Poetry's computed default ------------------------


def test_bare_install_resolves_the_default_set_with_known_optionality():
    recipe = _recipe()
    optionality = {"dev": True, "ops": False, "docs": True}
    universe = _universe((recipe,), group_optionality=optionality)
    assert universe == _EXPECTED_DEFAULT_GROUPS


def test_plant_bare_install_with_nonoptional_custom_group_is_true_not_false():
    """Plant — the case the OLD (pre-fix) model got wrong in the confident
    direction: a bare `poetry install`, with `ops` declared non-optional,
    installs `main` PLUS `ops` by Poetry's own default. `dotmac-ops-tool`
    (which lives only in `ops`) must derive `true`, not `false` — the old
    model treated every bare install as unparseable and would have said
    `unknown` at best, or (if naively 'fixed' to assume `main`-only) would
    have wrongly said `false`, which is worse than an `unknown` because it
    looks like a confident, checked answer."""
    result = _dimension(
        "dotmac-ops-tool",
        (_recipe(),),
        group_optionality={"dev": True, "ops": False, "docs": True},
    )
    assert result is DimensionValue.TRUE


def test_plant_bare_install_with_unknown_optionality_is_unknown():
    """Plant — clause 3 arriving through the third input. No
    `group_optionality` supplied at all: the default set cannot be
    computed, so this must be `unknown`, never `false` and never a silent
    `main`-only guess."""
    result = _dimension("dotmac-kernel", (_recipe(),), group_optionality=None)
    assert result is DimensionValue.UNKNOWN
    assert result is not DimensionValue.FALSE
    assert result is not DimensionValue.TRUE


def test_bare_install_with_optionality_incomplete_for_a_relevant_group_is_unknown():
    """`group_optionality` supplied, but missing an entry for `ops` — a
    group the lock actually resolves packages into. Refused, not defaulted
    either way."""
    incomplete = {"dev": True, "docs": True}  # "ops" missing
    result = _dimension("dotmac-ops-tool", (_recipe(),), group_optionality=incomplete)
    assert result is DimensionValue.UNKNOWN


# --- `--with`: adds to the default, and is a no-op for an already-default
# --- (non-optional) group -----------------------------------------------


def test_plant_with_optional_group_adds_it_to_the_default():
    recipe = _recipe(("--with", "docs"))
    universe = _universe(
        (recipe,), group_optionality={"dev": True, "ops": False, "docs": True}
    )
    assert universe == frozenset({"main", "ops", "docs"})
    result = _dimension(
        "dotmac-docs-tool",
        (recipe,),
        group_optionality={"dev": True, "ops": False, "docs": True},
    )
    assert result is DimensionValue.TRUE


def test_plant_with_nonoptional_group_is_a_documented_noop():
    """Plant — the direction a naive model gets wrong: naming an already-
    non-optional group on `--with` does not duplicate or otherwise change
    the default; the resulting set is identical to the bare-install
    default."""
    recipe = _recipe(("--with", "ops"))
    universe = _universe(
        (recipe,), group_optionality={"dev": True, "ops": False, "docs": True}
    )
    assert universe == _EXPECTED_DEFAULT_GROUPS


# --- `--without`: subtracts from the default ---------------------------------


def test_plant_without_subtracts_from_the_default():
    recipe = _recipe(("--without", "ops"))
    universe = _universe(
        (recipe,), group_optionality={"dev": True, "ops": False, "docs": True}
    )
    assert universe == frozenset({"main"})
    result = _dimension(
        "dotmac-ops-tool",
        (recipe,),
        group_optionality={"dev": True, "ops": False, "docs": True},
    )
    assert result is DimensionValue.FALSE, (
        "ops is excluded by --without, so a distribution living only in "
        "ops must no longer derive true even though a bare install (see "
        "test_plant_bare_install_with_nonoptional_custom_group_is_true_not_false) "
        "would have included it"
    )


# --- `poetry sync`: recognised alongside `poetry install` -------------------


def test_poetry_sync_subcommand_is_recognized_alongside_install():
    recipe = _recipe(("--only", "main"), subcommand="sync")
    assert _universe((recipe,)) == frozenset({"main"})


def test_unsupported_subcommand_is_refused():
    recipe = _recipe(("--only", "main"), subcommand="add")
    assert _universe((recipe,)) is None


# --- Refusals that must keep working ----------------------------------------


def test_only_and_with_together_is_refused_as_incoherent():
    recipe = _recipe(("--only", "main"), ("--with", "ops"))
    assert _universe((recipe,)) is None


def test_with_and_without_together_is_refused_as_a_shape_this_parser_does_not_resolve():
    recipe = _recipe(("--with", "docs"), ("--without", "ops"))
    optionality = {"dev": True, "ops": False, "docs": True}
    assert _universe((recipe,), group_optionality=optionality) is None


def test_unrecognized_flag_is_refused():
    recipe = _recipe(("--only", "main"), ("--extra-index-url", "https://example.test"))
    assert _universe((recipe,)) is None


def test_blank_flag_argument_is_refused_not_read_as_empty_selection():
    """Models an unresolved build-arg substitution collapsing to an empty
    string, e.g. `--only ${GROUPS}` rendered blank. Refused, not read as
    'selects nothing'."""
    recipe = _recipe(("--only", ""))
    assert _universe((recipe,)) is None


def test_non_poetry_tool_is_refused():
    recipe = _recipe(("--only", "main"), tool="pip")
    assert _universe((recipe,)) is None


def test_empty_recipe_tuple_is_refused_the_academy_case():
    assert _universe(()) is None


def test_one_unparseable_recipe_refuses_the_whole_union_not_just_itself():
    """A product with several deployed recipes where only one fails to
    parse must not silently fall back to the ones that did — that would
    hide a real production installation behind an unread recipe."""
    good = _recipe(("--only", "main"))
    bad = _recipe(("--only", ""))  # blank argument: unparseable
    assert _universe((good, bad)) is None
    assert _universe((bad, good)) is None


# --- The four plants from Michael's original ruling, plus the near-miss ----


def test_plant_1_dev_group_only_distribution_is_false():
    """Plant 1 — the real ERP `dotmac-deployment-foundation` case: resolved
    in the lock, but only into `dev`, against a recipe selecting only
    `main`. Before this module's `derive_installation_dimension` existed,
    there was no shared way to express this distinction at all — the
    undefined 'resolved and installed' reading let ERP record `true` here.
    After: a real execution derives `false`."""
    result = _dimension("dotmac-deployment-foundation", (_recipe(("--only", "main")),))
    assert result is DimensionValue.FALSE


def test_plant_2_main_group_distribution_is_true():
    """Plant 2 — the positive control. Without this, the refusal plants
    would be equally consistent with a checker that refuses everything;
    this proves the derivation can also say yes."""
    result = _dimension("dotmac-kernel", (_recipe(("--only", "main")),))
    assert result is DimensionValue.TRUE


def test_plant_3_no_measurable_recipe_yields_unknown_not_false_or_full_lock():
    """Plant 3 — clause 3, the load-bearing one. The Academy case: no
    authoritative checked-in recipe at all. Asserted explicitly as its own
    case, because 'default to false' and 'default to the full lock set'
    are exactly the two wrong answers a future reader will be tempted to
    write instead."""
    result = _dimension("dotmac-kernel", ())
    assert result is DimensionValue.UNKNOWN
    assert result is not DimensionValue.FALSE
    assert result is not DimensionValue.TRUE


def test_plant_4_unparseable_recipe_yields_unknown_never_widens_to_all_groups():
    """Plant 4 — an unrecognised recipe shape must never widen the
    installed set. Plants a deliberately malformed recipe (an unresolved
    build-arg substitution collapsing to a blank `--only` argument) and
    observes the refusal reaches `unknown`, never `true` for every
    distribution in the lock."""
    malformed = (_recipe(("--only", "")),)
    result_kernel = _dimension("dotmac-kernel", malformed)
    result_foundation = _dimension("dotmac-deployment-foundation", malformed)
    assert result_kernel is DimensionValue.UNKNOWN
    assert result_foundation is DimensionValue.UNKNOWN


def test_near_miss_legitimate_multi_group_recipe_resolves_to_the_union():
    """The Sub shape: a product selecting `main` plus one operator group in
    a single recipe (or, equivalently, across several deployed recipes —
    see `test_multi_recipe_union_across_deployed_profiles_sub_shape` below)
    must NOT be refused merely for naming more than one group."""
    result = _dimension("dotmac-ui", (_recipe(("--only", "main,ops")),))
    assert result is DimensionValue.TRUE


def test_multi_recipe_union_across_deployed_profiles_sub_shape():
    """Sub's real shape: separate application and operator recipes, neither
    of which alone selects `ops`, but whose UNION does. A dependency
    reaching only one deployed profile is still installed."""
    app_recipe = _recipe(("--only", "main"))
    operator_recipe = _recipe(("--with", "ops"))
    result = _dimension(
        "dotmac-ui",
        (app_recipe, operator_recipe),
        group_optionality={"dev": True, "ops": False, "docs": True},
    )
    assert result is DimensionValue.TRUE


def test_old_format_lock_with_no_groups_field_anywhere_yields_unknown():
    """The file-format caveat, planted on its own: a lock predating the
    `groups` field is `unknown` for the whole product, never an assumption
    that everything unresolved-by-group is `main`."""
    old_lock = {"package": [{"name": "dotmac-kernel", "version": "1.0.0"}]}
    result = _dimension(
        "dotmac-kernel",
        (_recipe(("--only", "main")),),
        lock_document=old_lock,
    )
    assert result is DimensionValue.UNKNOWN


def test_recipe_selecting_a_group_absent_from_the_lock_is_refused_as_stale():
    """The cross-check clause from the ERP-lane finding: a recipe naming a
    group the lock has no entries for at all is a stale or wrong recipe
    reference, not a product with zero production dependencies. Refused
    (`unknown`), never read as 'nothing is installed' — which would make
    every distribution in that product `false` while looking like a clean
    answer."""
    stale = (_recipe(("--only", "nonexistent-group")),)
    result = _dimension("dotmac-kernel", stale)
    assert result is DimensionValue.UNKNOWN


def test_distribution_absent_from_the_lock_entirely_is_false_not_unknown():
    """A distribution that never resolves in the lock at all cannot be
    installed into any group it was never resolved into — this is a
    confirmed negative, not an absence of evidence, and stays distinct from
    the `unknown` cases above."""
    result = _dimension("dotmac-never-resolved", (_recipe(("--only", "main")),))
    assert result is DimensionValue.FALSE


def test_derive_installation_dimension_never_returns_not_applicable():
    """`installation` is never `not_applicable` for any classification (see
    the module docstring's invariant, and `CompositionRecord.__post_init__`,
    which would refuse a record carrying it there). Sweep every reachable
    branch of `derive_installation_dimension` and confirm none of them can
    produce it."""
    optionality = {"dev": True, "ops": False, "docs": True}
    branches = [
        _dimension("dotmac-deployment-foundation", (_recipe(("--only", "main")),)),
        _dimension("dotmac-kernel", ()),
        _dimension("dotmac-kernel", (), lock_document={"package": []}),
        _dimension("dotmac-kernel", (_recipe(("--only", "main")),)),
        _dimension("dotmac-ops-tool", (_recipe(),), group_optionality=optionality),
        _dimension(
            "dotmac-ops-tool",
            (_recipe(("--without", "ops")),),
            group_optionality=optionality,
        ),
    ]
    assert DimensionValue.NOT_APPLICABLE not in branches
