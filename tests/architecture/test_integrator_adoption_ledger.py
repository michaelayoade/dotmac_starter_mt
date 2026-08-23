"""The Integrator may cut over capabilities one at a time, never its plane by accident.

ADR-0024 moves provider machinery out of every product, but a callback cutover
is smaller than control-plane retirement.  The executable ledger makes that
difference reviewable: every observed live binding needs a disposition, every
continuing capability needs a complete migration packet, and the seven shared
Sub tables remain until the entire live population is migrated or retired.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "integrator_adoption_ledger.py"
INVENTORIES = PROJECT_ROOT / "docs" / "inventories"
LEDGER = INVENTORIES / "integrator-adoption-ledger.json"
CATALOGUE = INVENTORIES / "integrator-sub-connector-catalogue.json"
SOURCE_CATALOGUES = (
    INVENTORIES / "integrator-academy-source-catalogue.json",
    INVENTORIES / "integrator-erp-source-catalogue.json",
    INVENTORIES / "integrator-crm-source-catalogue.json",
)
FLEET_BASELINE = INVENTORIES / "external-connector-baseline.json"
STAGING = (
    INVENTORIES / "evidence" / "integrator-adoption-seabone-staging-2026-08-18.json"
)
ERP_STAGING = (
    INVENTORIES / "evidence" / "integrator-adoption-seabone-erp-staging-2026-08-19.json"
)
ACADEMY_STAGING = (
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-academy-staging-2026-08-19.json"
)
ERP_MIGRATION_REHEARSAL = (
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-erp-migration-rehearsal-2026-08-19.json"
)
ACADEMY_MIGRATION_REHEARSAL = (
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-academy-migration-rehearsal-2026-08-19.json"
)
CRM_STAGING = (
    INVENTORIES / "evidence" / "integrator-adoption-seabone-crm-staging-2026-08-18.json"
)
ADR = PROJECT_ROOT / "docs" / "adr" / "0024-apps-compose-by-synchronizing-data.md"


def _module():
    spec = importlib.util.spec_from_file_location("integrator_adoption_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_catalogues() -> list[dict]:
    return [_read(path) for path in SOURCE_CATALOGUES]


def test_the_ledger_and_its_evidence_are_checked_in_and_linked() -> None:
    assert SCRIPT.is_file()
    assert LEDGER.is_file()
    assert CATALOGUE.is_file()
    assert all(path.is_file() for path in SOURCE_CATALOGUES)
    assert STAGING.is_file()
    assert ERP_STAGING.is_file()
    assert ACADEMY_STAGING.is_file()
    assert ERP_MIGRATION_REHEARSAL.is_file()
    assert ACADEMY_MIGRATION_REHEARSAL.is_file()
    assert CRM_STAGING.is_file()
    index = (INVENTORIES / "README.md").read_text(encoding="utf-8")
    assert "integrator-adoption-ledger" in index


def test_source_catalogues_are_complete_structural_maps_not_capability_guesses() -> (
    None
):
    gate = _module()
    catalogues = [_read(path) for path in SOURCE_CATALOGUES]

    assert {row["source"]["application"] for row in catalogues} == {
        "dotmac_academy_app",
        "dotmac_erp",
        "dotmac_crm",
    }
    assert gate.source_catalogue_problems(catalogues, _read(LEDGER)) == []
    assert all(row["ratchet_classified_paths"] for row in catalogues)
    assert all(row["surfaces"] for row in catalogues)


def test_staging_legacy_observations_name_only_mapped_source_surfaces() -> None:
    gate = _module()
    catalogues = [_read(path) for path in SOURCE_CATALOGUES]
    snapshots = [_read(ERP_STAGING), _read(ACADEMY_STAGING), _read(CRM_STAGING)]

    assert all(gate.snapshot_problems(snapshot) == [] for snapshot in snapshots)
    assert gate.snapshot_catalogue_problems(snapshots, catalogues) == []


def test_an_unknown_runtime_surface_is_refused() -> None:
    """Sensitivity: an aggregate count cannot bypass source classification."""
    gate = _module()
    snapshots = [
        copy.deepcopy(_read(ERP_STAGING)),
        _read(ACADEMY_STAGING),
        _read(CRM_STAGING),
    ]
    snapshots[0]["legacy_surfaces"][0]["surface_id"] = "erp-unknown-provider"

    assert any(
        "runtime observation names no source surface" in problem
        for problem in gate.snapshot_catalogue_problems(
            snapshots, [_read(path) for path in SOURCE_CATALOGUES]
        )
    )


def test_erp_staging_census_is_complete_read_only_and_financially_held() -> None:
    catalogue = _read(INVENTORIES / "integrator-erp-source-catalogue.json")
    erp = _read(ERP_STAGING)

    assert erp["source"]["source_revision"] == catalogue["source"]["revision"]
    assert set(erp["coverage"].values()) == {True}
    assert {row["surface_id"] for row in erp["legacy_surfaces"]} == {
        row["id"] for row in catalogue["surfaces"]
    }
    safety = erp["safety_observations"]
    assert safety["database_read_only_proven"] is True
    assert safety["database_isolation_level"] == "repeatable read"
    assert safety["database_session_count"] == safety["organization_count"] + 1
    assert safety["financial_cutover_authorized"] is False
    assert safety["billing_non_interference_proven"] is False


def test_academy_staging_census_is_complete_read_only_and_truthfully_empty() -> None:
    catalogue = _read(INVENTORIES / "integrator-academy-source-catalogue.json")
    academy = _read(ACADEMY_STAGING)

    assert academy["source"]["source_revision"] == catalogue["source"]["revision"]
    assert set(academy["coverage"].values()) == {True}
    assert {row["surface_id"] for row in academy["legacy_surfaces"]} == {
        row["id"] for row in catalogue["surfaces"]
    }
    safety = academy["safety_observations"]
    assert safety["database_read_only_proven"] is True
    assert safety["database_isolation_level"] == "repeatable read"
    assert safety["database_session_count"] == 1
    assert safety["tenant_count"] == 0
    assert safety["financial_cutover_authorized"] is False
    assert safety["billing_non_interference_proven"] is False
    assert any(
        row["surface_id"] == "academy-smtp-delivery"
        and row["observation"] == "configured"
        for row in academy["legacy_surfaces"]
    )


def test_staging_disproves_a_big_bang_crm_retirement_and_exposes_erp_money() -> None:
    crm = _read(CRM_STAGING)
    erp = _read(ERP_STAGING)

    assert sum(row["count"] for row in crm["legacy_surfaces"]) > 0
    assert crm["safety_observations"]["crm_retirement_zero_proven"] is False
    assert any(
        row["surface_id"] == "erp-paystack-payments"
        and row["observation"] == "active"
        and row["count"] == 825
        for row in erp["legacy_surfaces"]
    )
    assert erp["safety_observations"]["financial_cutover_authorized"] is False


def test_erp_migration_rehearsal_proves_billing_and_survivor_preservation() -> None:
    gate = _module()
    rehearsal = _read(ERP_MIGRATION_REHEARSAL)

    assert gate.migration_rehearsal_problems(rehearsal) == []


def test_academy_migration_rehearsal_proves_exact_artifact_and_row_preservation() -> None:
    gate = _module()
    rehearsal = _read(ACADEMY_MIGRATION_REHEARSAL)

    assert gate.migration_rehearsal_problems(rehearsal) == []
    assert rehearsal["migration"]["before_heads"] == ["0049_enrollment_audience"]
    assert rehearsal["migration"]["after_heads"] == ["0053_entrance_defaults"]
    assert rehearsal["migration"]["repaired_preconditions"] == []


def test_erp_migration_rehearsal_detector_has_bite() -> None:
    gate = _module()
    rehearsal = copy.deepcopy(_read(ERP_MIGRATION_REHEARSAL))
    rehearsal["survivor_counts"]["financial_after"]["erp-paystack-payments"] += 1
    rehearsal["survivor_counts"]["legacy_marker_counts_sha256_after"] = "0" * 64
    rehearsal["safety_observations"]["shared_staging_database_modified"] = True

    problems = gate.migration_rehearsal_problems(rehearsal)

    assert "migration rehearsal changed financial counts" in problems
    assert "migration rehearsal changed legacy marker counts" in problems
    assert "migration rehearsal modified the shared staging database" in problems


def test_source_mapped_is_distinct_from_an_owned_capability_catalogue() -> None:
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)
    baseline = _read(FLEET_BASELINE)
    sources = [_read(path) for path in SOURCE_CATALOGUES]

    assert gate.fleet_baseline_problems(ledger, catalogue, baseline, sources) == []
    by_app = {
        row["application"]: row
        for row in ledger["programme"]["application_inventories"]
    }
    assert by_app["dotmac_erp"]["surface_status"] == "source-mapped"
    assert by_app["dotmac_crm"]["surface_status"] == "source-mapped"
    assert by_app["dotmac_academy_app"]["surface_status"] == "source-mapped"


def test_a_source_map_cannot_promote_an_unassigned_capability_to_migrate() -> None:
    """Sensitivity: source adjacency never creates provider-neutral vocabulary."""
    gate = _module()
    catalogues = [copy.deepcopy(_read(path)) for path in SOURCE_CATALOGUES]
    academy = next(
        row
        for row in catalogues
        if row["source"]["application"] == "dotmac_academy_app"
    )
    smtp = next(
        row for row in academy["surfaces"] if row["id"] == "academy-smtp-delivery"
    )
    smtp["disposition"] = "migrate"

    assert any(
        "migrate requires an owned capability contract" in problem
        for problem in gate.source_catalogue_problems(catalogues, _read(LEDGER))
    )


def test_product_local_infrastructure_cannot_be_relabelled_as_a_connector() -> None:
    """Sensitivity: an HTTP request is evidence to classify, not ownership."""
    gate = _module()
    catalogues = [copy.deepcopy(_read(path)) for path in SOURCE_CATALOGUES]
    academy = next(
        row
        for row in catalogues
        if row["source"]["application"] == "dotmac_academy_app"
    )
    lab = next(
        row for row in academy["surfaces"] if row["id"] == "academy-lab-console-proxy"
    )
    lab["disposition"] = "needs-production-evidence"

    assert any(
        "product-local authority requires retain-product-local" in problem
        for problem in gate.source_catalogue_problems(catalogues, _read(LEDGER))
    )


def test_only_internal_application_transport_may_be_retained_as_a_product_api() -> None:
    gate = _module()
    catalogues = [copy.deepcopy(_read(path)) for path in SOURCE_CATALOGUES]
    academy = next(
        row
        for row in catalogues
        if row["source"]["application"] == "dotmac_academy_app"
    )
    smtp = next(
        row for row in academy["surfaces"] if row["id"] == "academy-smtp-delivery"
    )
    smtp["disposition"] = "retain-product-api"

    assert any(
        "only internal application transport" in problem
        for problem in gate.source_catalogue_problems(catalogues, _read(LEDGER))
    )


def test_financial_source_work_cannot_enter_migration_behind_the_closed_gate() -> None:
    gate = _module()
    catalogues = [copy.deepcopy(_read(path)) for path in SOURCE_CATALOGUES]
    erp = next(
        row for row in catalogues if row["source"]["application"] == "dotmac_erp"
    )
    paystack = next(
        row for row in erp["surfaces"] if row["id"] == "erp-paystack-payments"
    )
    paystack["disposition"] = "migrate"
    paystack["capability"] = {
        "status": "declared",
        "id": "payments.intent.v1",
        "owner": "Billing",
    }

    assert any(
        "financial cutover is not authorized" in problem
        for problem in gate.source_catalogue_problems(catalogues, _read(LEDGER))
    )


def test_every_declared_runtime_capability_has_exactly_one_disposition() -> None:
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)

    expected = gate.catalogue_capability_keys(catalogue)
    declared = gate.ledger_capability_keys(ledger)
    assert expected
    assert declared == expected
    assert gate.catalogue_problems(ledger, catalogue) == []


def test_every_required_application_has_one_fleet_inventory_disposition() -> None:
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)
    baseline = _read(FLEET_BASELINE)

    assert gate.fleet_baseline_problems(ledger, catalogue, baseline) == []
    required = set(ledger["programme"]["required_applications"])
    inventoried = {
        row["application"] for row in ledger["programme"]["application_inventories"]
    }
    assert required == inventoried == set(baseline["repos_measured"])


def test_a_nonzero_application_cannot_be_declared_measured_zero() -> None:
    """Sensitivity: Vendor's genuine zero must not become a reusable escape."""
    gate = _module()
    ledger = copy.deepcopy(_read(LEDGER))
    catalogue = _read(CATALOGUE)
    baseline = _read(FLEET_BASELINE)
    erp = next(
        row
        for row in ledger["programme"]["application_inventories"]
        if row["application"] == "dotmac_erp"
    )
    erp["surface_status"] = "measured-zero"

    assert any(
        "nonzero fleet surface cannot be measured-zero" in problem
        for problem in gate.fleet_baseline_problems(ledger, catalogue, baseline)
    )


def test_category_counts_do_not_pretend_to_be_capability_inventory() -> None:
    gate = _module()
    blockers = gate.retirement_blockers(_read(LEDGER), [_read(STAGING)])

    assert "capability inventory incomplete for dotmac_erp" in blockers
    assert "capability inventory incomplete for dotmac_crm" in blockers
    assert "capability inventory incomplete for dotmac_academy_app" in blockers
    assert "application disposition is unclassified: dotmac_erp" in blockers
    assert "application disposition is unclassified: dotmac_academy_app" in blockers


def test_catalogue_completeness_gate_detects_a_dropped_capability() -> None:
    """Sensitivity: the CLI must enforce the comparison, not only this test."""
    gate = _module()
    ledger = copy.deepcopy(_read(LEDGER))
    catalogue = _read(CATALOGUE)
    row = next(item for item in ledger["cohorts"] if item["id"] == "sub-erp")
    row["capabilities"].remove("erp.status.read.v1")

    assert any(
        "declared capability lacks a disposition" in problem
        for problem in gate.catalogue_problems(ledger, catalogue)
    )


def test_catalogue_only_entries_are_explicitly_disposed_without_fake_capabilities() -> (
    None
):
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)

    expected = gate.catalogue_only_keys(catalogue)
    declared = gate.ledger_catalogue_only_keys(ledger)
    assert expected == {"3cx", "freepbx"}
    assert declared == expected


def test_current_record_is_truthfully_blocked_not_pretending_to_be_complete() -> None:
    gate = _module()
    ledger = _read(LEDGER)
    snapshots = [_read(STAGING)]

    assert gate.structural_problems(ledger, snapshots) == []
    blockers = gate.retirement_blockers(ledger, snapshots)
    assert ledger["programme"]["platform_retirement_claimed"] is False
    assert "production inventory is unmeasured for dotmac_sub" in blockers
    assert any("migration packet incomplete" in item for item in blockers)


def test_seabone_staging_evidence_cannot_satisfy_the_production_inventory_gate() -> (
    None
):
    gate = _module()
    ledger = _read(LEDGER)
    staging = _read(STAGING)

    assert staging["source"]["environment"] == "staging"
    assert staging["source"]["server_name"] == "dotmac-sub-staging"
    assert gate.production_snapshots([staging]) == []
    assert (
        "production inventory is unmeasured for dotmac_sub"
        in gate.retirement_blockers(ledger, [staging])
    )


def test_production_source_map_must_observe_every_surface_without_unmeasured() -> None:
    """Sensitivity: a partial query is not a production-derived inventory."""
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)
    source_catalogues = _source_catalogues()
    erp_catalogue = next(
        row for row in source_catalogues if row["source"]["application"] == "dotmac_erp"
    )
    production = copy.deepcopy(_read(ERP_STAGING))
    production["source"]["environment"] = "production"
    production["source"]["source_revision"] = erp_catalogue["source"]["revision"]
    production["coverage"] = dict.fromkeys(gate.REQUIRED_COVERAGE, True)
    production["safety_observations"]["database_read_only_proven"] = True
    missing_surface = production["legacy_surfaces"].pop()

    problems = gate.production_snapshot_coverage_problems(
        production,
        ledger,
        catalogue,
        source_catalogues,
    )
    assert any("source surfaces are unobserved" in problem for problem in problems)

    production["legacy_surfaces"].append(missing_surface)
    assert (
        gate.production_snapshot_coverage_problems(
            production,
            ledger,
            catalogue,
            source_catalogues,
        )
        == []
    )

    production["safety_observations"]["database_read_only_proven"] = False
    assert any(
        "database read-only proof is missing" in problem
        for problem in gate.production_snapshot_coverage_problems(
            production,
            ledger,
            catalogue,
            source_catalogues,
        )
    )
    production["safety_observations"]["database_read_only_proven"] = True
    production["legacy_surfaces"][0]["observation"] = "unmeasured"
    assert any(
        "production observation is unmeasured" in problem
        for problem in gate.production_snapshot_coverage_problems(
            production,
            ledger,
            catalogue,
            source_catalogues,
        )
    )


def test_production_source_map_must_match_the_deployed_revision() -> None:
    gate = _module()
    production = copy.deepcopy(_read(ERP_STAGING))
    production["source"]["environment"] = "production"
    production["source"]["source_revision"] = "0" * 40
    production["coverage"] = dict.fromkeys(gate.REQUIRED_COVERAGE, True)

    assert any(
        "source catalogue revision differs from deployed revision" in problem
        for problem in gate.production_snapshot_coverage_problems(
            production,
            _read(LEDGER),
            _read(CATALOGUE),
            _source_catalogues(),
        )
    )


def test_production_sub_inventory_accounts_for_every_catalogued_capability() -> None:
    """Sensitivity: no rows cannot mean both absent and never queried."""
    gate = _module()
    ledger = _read(LEDGER)
    catalogue = _read(CATALOGUE)
    production = copy.deepcopy(_read(STAGING))
    production["source"]["environment"] = "production"
    production["source"]["source_revision"] = catalogue["source"]["revision"]
    production["coverage"] = dict.fromkeys(gate.REQUIRED_COVERAGE, True)
    production["safety_observations"]["database_read_only_proven"] = True

    assert any(
        "catalogued capabilities are unobserved" in problem
        for problem in gate.production_snapshot_coverage_problems(
            production,
            ledger,
            catalogue,
            _source_catalogues(),
        )
    )

    production["bindings"] = [
        {
            "connector_key": connector["key"],
            "connector_version": connector["version"],
            "installation_state": "absent",
            "capability_id": capability["id"],
            "binding_state": "absent",
            "count": 0,
        }
        for connector in catalogue["connectors"]
        if connector["runtime"] != "catalogue_only"
        for capability in connector["capabilities"]
    ]
    assert (
        gate.production_snapshot_coverage_problems(
            production,
            ledger,
            catalogue,
            _source_catalogues(),
        )
        == []
    )


def test_staging_selection_is_bound_to_the_target_application() -> None:
    """Sensitivity: evidence file order cannot select another product's DB."""
    gate = _module()
    snapshots = [_read(ERP_STAGING), _read(CRM_STAGING), _read(STAGING)]

    selected = gate.staging_snapshot_for_target(
        _read(LEDGER),
        snapshots,
        target_cohort_id="sub-whatsapp-receive",
    )

    assert selected is not None
    assert selected["source"]["application"] == "dotmac_sub"


def test_staging_rehearsal_refuses_evidence_from_another_application() -> None:
    gate = _module()
    blockers = gate.staging_rehearsal_blockers(
        _read(LEDGER),
        _read(ERP_STAGING),
        target_cohort_id="sub-whatsapp-receive",
    )

    assert any("does not match target application" in item for item in blockers)


def test_staging_cli_ignores_another_app_even_when_it_comes_first(
    capsys,
) -> None:
    gate = _module()

    result = gate.main(
        [
            "--snapshot",
            str(ERP_STAGING),
            "--snapshot",
            str(STAGING),
            "--require-staging-ready",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "survivor canary is missing: sub-nextcloud-talk" in captured.err
    assert "dotmac_erp does not match target application" not in captured.err


def test_cli_reports_partial_production_evidence_instead_of_measured(
    tmp_path: Path,
    capsys,
) -> None:
    gate = _module()
    partial = copy.deepcopy(_read(ERP_STAGING))
    partial["source"]["environment"] = "production"
    partial["coverage"] = dict.fromkeys(gate.REQUIRED_COVERAGE, True)
    partial["legacy_surfaces"].pop()
    path = tmp_path / "partial-production.json"
    path.write_text(json.dumps(partial), encoding="utf-8")

    result = gate.main(["--snapshot", str(path)])

    captured = capsys.readouterr()
    assert result == 0
    assert "production inventory incomplete for dotmac_erp" in captured.out
    assert "source surfaces are unobserved" in captured.out


def test_seabone_rehearsal_names_survivor_and_billing_blockers() -> None:
    gate = _module()
    blockers = gate.staging_rehearsal_blockers(
        _read(LEDGER),
        _read(STAGING),
        target_cohort_id="sub-whatsapp-receive",
    )

    assert any("billing non-interference is unproven" in item for item in blockers)
    assert any(
        "survivor canary is missing: sub-nextcloud-talk" in item for item in blockers
    )
    assert any("staging coverage is incomplete" in item for item in blockers)


def test_a_survivor_canary_changes_the_staging_verdict() -> None:
    """Sensitivity: recording a real survivor proof removes its exact blocker."""
    gate = _module()
    ledger = copy.deepcopy(_read(LEDGER))
    nextcloud = next(
        row for row in ledger["cohorts"] if row["id"] == "sub-nextcloud-talk"
    )
    nextcloud["survivor_canary"] = {
        "status": "ready",
        "evidence": ["synthetic:test"],
    }

    blockers = gate.staging_rehearsal_blockers(
        ledger,
        _read(STAGING),
        target_cohort_id="sub-whatsapp-receive",
    )
    assert "survivor canary is missing: sub-nextcloud-talk" not in blockers


def test_financial_capabilities_stay_retained_while_the_gate_is_closed() -> None:
    ledger = _read(LEDGER)
    assert ledger["programme"]["financial_cutover_authorized"] is False
    financial = [row for row in ledger["cohorts"] if row["financial"]]
    assert financial
    assert {row["connector_key"] for row in financial} == {"paystack", "flutterwave"}
    assert {row["disposition"] for row in financial} == {"retain-temporarily"}
    assert all(row["retain_until_gate"] for row in financial)


def test_first_cutover_does_not_claim_whatsapp_send_or_shared_tables() -> None:
    ledger = _read(LEDGER)
    by_id = {row["id"]: row for row in ledger["cohorts"]}

    assert by_id["sub-whatsapp-receive"]["cutover_wave"] == 1
    assert by_id["sub-whatsapp-send-templates"]["cutover_wave"] > 1
    assert set(ledger["programme"]["shared_sub_control_plane_tables"]) == {
        "integration_installations",
        "integration_config_revisions",
        "integration_capability_bindings",
        "integration_checkpoints",
        "integration_event_subscriptions",
        "integration_deliveries",
        "integration_inbox",
    }


def test_live_binding_without_a_disposition_is_a_retirement_blocker() -> None:
    """Sensitivity: an unclassified live integration can never read as zero debt."""
    gate = _module()
    ledger = _read(LEDGER)
    production = gate.synthetic_production_snapshot(
        connector_key="unknown.provider",
        capability_id="unknown.observe.v1",
    )

    blockers = gate.retirement_blockers(ledger, [production])
    assert any("live capability has no disposition" in item for item in blockers)


def test_an_incomplete_packet_refuses_a_platform_retirement_claim() -> None:
    """Sensitivity: changing the headline cannot make unfinished rows disappear."""
    gate = _module()
    ledger = copy.deepcopy(_read(LEDGER))
    ledger["programme"]["platform_retirement_claimed"] = True
    production = gate.synthetic_production_snapshot(
        connector_key="whatsapp",
        capability_id="messaging.receive.v1",
    )

    problems = gate.structural_problems(ledger, [production])
    assert any(
        "platform retirement is claimed while blockers remain" in p for p in problems
    )


def test_a_financial_migration_is_refused_while_its_gate_is_closed() -> None:
    """Sensitivity: a tidy-up cannot silently absorb the payment cutover."""
    gate = _module()
    ledger = copy.deepcopy(_read(LEDGER))
    paystack = next(row for row in ledger["cohorts"] if row["id"] == "sub-paystack")
    paystack["disposition"] = "migrate"
    paystack["packet"] = dict.fromkeys(gate.PACKET_FIELDS, "missing")

    problems = gate.structural_problems(ledger, [_read(STAGING)])
    assert any("financial cutover is not authorized" in p for p in problems)


def test_snapshot_schema_cannot_carry_configuration_or_secret_material() -> None:
    """The evidence is counts and identities, never configuration payloads."""
    gate = _module()
    snapshot = _read(STAGING)
    assert gate.snapshot_problems(snapshot) == []

    leaked = copy.deepcopy(snapshot)
    leaked["bindings"][0]["secret_refs"] = {"token": "forbidden"}
    assert any("forbidden snapshot field" in p for p in gate.snapshot_problems(leaked))


def test_adr_names_capability_cutover_and_fleet_zero_as_different_gates() -> None:
    source = " ".join(ADR.read_text(encoding="utf-8").lower().split())
    assert "capability cutover is not control-plane retirement" in source
    assert "cut over sequentially" in source
    assert "fleet-zero" in source
    assert "survivor" in source
