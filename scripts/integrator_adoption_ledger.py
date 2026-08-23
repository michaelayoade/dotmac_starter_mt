#!/usr/bin/env python3
"""Validate the fleet Integrator adoption ledger and its runtime evidence.

The normal check accepts a truthful incomplete programme and prints its
blockers.  ``--require-retirement-ready`` is the destructive-boundary gate: it
fails until production coverage is complete, every live capability has one
disposition, every migration/retirement packet is proven, and no temporary
retention remains.

Snapshots deliberately contain only deployment identity, aggregate table
counts and connector/capability identities.  Configuration, payloads and secret
references are forbidden evidence fields.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from typing import Any

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
INVENTORIES = PROJECT_ROOT / "docs" / "inventories"
DEFAULT_LEDGER = INVENTORIES / "integrator-adoption-ledger.json"
DEFAULT_CATALOGUE = INVENTORIES / "integrator-sub-connector-catalogue.json"
DEFAULT_SOURCE_CATALOGUES = (
    INVENTORIES / "integrator-academy-source-catalogue.json",
    INVENTORIES / "integrator-erp-source-catalogue.json",
    INVENTORIES / "integrator-crm-source-catalogue.json",
)
DEFAULT_FLEET_BASELINE = INVENTORIES / "external-connector-baseline.json"
DEFAULT_SNAPSHOTS = (
    INVENTORIES / "evidence" / "integrator-adoption-seabone-staging-2026-08-18.json",
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-erp-staging-2026-08-19.json",
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-academy-staging-2026-08-19.json",
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-crm-staging-2026-08-18.json",
)
DEFAULT_REHEARSALS = (
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-erp-migration-rehearsal-2026-08-19.json",
    INVENTORIES
    / "evidence"
    / "integrator-adoption-seabone-academy-migration-rehearsal-2026-08-19.json",
)

DISPOSITIONS = frozenset({"migrate", "retire", "retain-temporarily"})
APPLICATION_DISPOSITIONS = frozenset(
    {"migrate", "retire", "mixed", "unclassified", "not-applicable"}
)
SURFACE_STATUSES = frozenset(
    {"capability-catalogued", "source-mapped", "category-counted", "measured-zero"}
)
SOURCE_DISPOSITIONS = frozenset(
    {
        "migrate",
        "retire",
        "retain-temporarily",
        "retain-product-local",
        "retain-product-api",
        "needs-production-evidence",
    }
)
AUTHORITY_CLASSES = frozenset(
    {
        "external-transport",
        "internal-app-transport",
        "product-local-infrastructure",
        "product-local-operational",
    }
)
PRODUCT_LOCAL_AUTHORITY_CLASSES = frozenset(
    {"product-local-infrastructure", "product-local-operational"}
)
CAPABILITY_STATUSES = frozenset({"declared", "unassigned", "not-applicable"})
STEP_STATUSES = frozenset({"missing", "ready", "not-applicable"})
PACKET_FIELDS = (
    "connector_distribution",
    "product_port",
    "descriptor",
    "reconciler",
    "secret_mapping",
    "mirror_evidence",
    "rollback_plan",
    "retirement_gate",
)
LIVE_STATES = frozenset({"enabled", "configured", "quarantined"})
LEGACY_OBSERVATIONS = frozenset(
    {"active", "configured", "historical", "absent", "unmeasured"}
)
REQUIRED_COVERAGE = frozenset(
    {
        "integration_tables",
        "provider_clients",
        "provider_credentials",
        "webhook_verification",
        "connector_scheduling",
        "checkpoints",
        "delivery_retries",
    }
)
FORBIDDEN_SNAPSHOT_FIELDS = frozenset(
    {
        "api_key",
        "config_json",
        "configuration",
        "consequence_json",
        "headers_json",
        "password",
        "payload_json",
        "secret_refs",
        "secret_values",
        "token",
    }
)


def _read(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return value


def _capability_key(application: str, connector: str, capability: str) -> str:
    return "|".join((application.strip(), connector.strip(), capability.strip()))


def catalogue_capability_keys(catalogue: Mapping[str, Any]) -> set[str]:
    application = str(catalogue.get("source", {}).get("application", ""))
    keys: set[str] = set()
    for connector in catalogue.get("connectors", ()):
        if connector.get("runtime") == "catalogue_only":
            continue
        for capability in connector.get("capabilities", ()):
            keys.add(
                _capability_key(
                    application,
                    str(connector.get("key", "")),
                    str(capability.get("id", "")),
                )
            )
    return keys


def catalogue_only_keys(catalogue: Mapping[str, Any]) -> set[str]:
    return {
        str(connector.get("key", ""))
        for connector in catalogue.get("connectors", ())
        if connector.get("runtime") == "catalogue_only"
    }


def ledger_capability_keys(ledger: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for row in ledger.get("cohorts", ()):
        for capability in row.get("capabilities", ()):
            keys.add(
                _capability_key(
                    str(row.get("application", "")),
                    str(row.get("connector_key", "")),
                    str(capability),
                )
            )
    return keys


def ledger_catalogue_only_keys(ledger: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("connector_key", "")) for row in ledger.get("catalogue_only", ())
    }


def catalogue_problems(
    ledger: Mapping[str, Any], catalogue: Mapping[str, Any]
) -> list[str]:
    """Compare the source catalogue with its explicit programme dispositions."""

    expected = catalogue_capability_keys(catalogue)
    declared = ledger_capability_keys(ledger)
    problems = [
        f"declared capability lacks a disposition: {key}"
        for key in sorted(expected - declared)
    ]
    problems.extend(
        f"disposition names no declared capability: {key}"
        for key in sorted(declared - expected)
    )

    expected_catalogue_only = catalogue_only_keys(catalogue)
    declared_catalogue_only = ledger_catalogue_only_keys(ledger)
    problems.extend(
        f"catalogue-only connector lacks a disposition: {key}"
        for key in sorted(expected_catalogue_only - declared_catalogue_only)
    )
    problems.extend(
        f"catalogue-only disposition names no declaration: {key}"
        for key in sorted(declared_catalogue_only - expected_catalogue_only)
    )
    return problems


def source_catalogue_problems(
    catalogues: Iterable[Mapping[str, Any]], ledger: Mapping[str, Any]
) -> list[str]:
    """Validate legacy source maps without turning adjacent code into a contract.

    These catalogues answer which committed runtime surfaces exist.  They do
    not grant a provider-neutral capability name or prove production use.  A
    surface may move only after its owning product supplies that contract;
    direct HTTP usage alone is discovery evidence.
    """

    problems: list[str] = []
    rows = list(catalogues)
    seen_applications: set[str] = set()
    financial_authorized = (
        ledger.get("programme", {}).get("financial_cutover_authorized") is True
    )

    for index, catalogue in enumerate(rows):
        source = catalogue.get("source")
        if not isinstance(source, Mapping):
            problems.append(f"source catalogue {index}: source is missing")
            continue
        application = str(source.get("application", ""))
        if not application:
            problems.append(f"source catalogue {index}: application is missing")
            continue
        if application in seen_applications:
            problems.append(f"duplicate source catalogue: {application}")
        seen_applications.add(application)
        for field in ("repository", "revision", "captured_at", "method"):
            if not str(source.get(field, "")).strip():
                problems.append(f"{application}: source.{field} is missing")

        classified = catalogue.get("ratchet_classified_paths")
        if not isinstance(classified, list) or not classified:
            problems.append(f"{application}: ratchet_classified_paths is empty")
            classified_paths: set[str] = set()
        else:
            classified_paths = {str(path) for path in classified}
            if len(classified_paths) != len(classified):
                problems.append(f"{application}: duplicate ratchet-classified path")

        surfaces = catalogue.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            problems.append(f"{application}: surfaces is empty")
            continue
        seen_ids: set[str] = set()
        covered_paths: set[str] = set()
        for surface_index, surface in enumerate(surfaces):
            if not isinstance(surface, Mapping):
                problems.append(
                    f"{application}: surface {surface_index} must be an object"
                )
                continue
            surface_id = str(surface.get("id", ""))
            if not surface_id:
                problems.append(f"{application}: surface {surface_index} has no id")
                continue
            if surface_id in seen_ids:
                problems.append(f"{application}: duplicate surface id {surface_id}")
            seen_ids.add(surface_id)

            disposition = surface.get("disposition")
            if disposition not in SOURCE_DISPOSITIONS:
                problems.append(
                    f"{surface_id}: invalid source disposition {disposition!r}"
                )
            authority = surface.get("authority_class")
            if authority not in AUTHORITY_CLASSES:
                problems.append(f"{surface_id}: invalid authority class {authority!r}")
            if (
                authority in PRODUCT_LOCAL_AUTHORITY_CLASSES
                and disposition != "retain-product-local"
            ):
                problems.append(
                    f"{surface_id}: product-local authority requires "
                    "retain-product-local"
                )
            if (
                authority not in PRODUCT_LOCAL_AUTHORITY_CLASSES
                and disposition == "retain-product-local"
            ):
                problems.append(
                    f"{surface_id}: connector transport cannot be retained as "
                    "product-local authority"
                )
            if (
                disposition == "retain-product-api"
                and authority != "internal-app-transport"
            ):
                problems.append(
                    f"{surface_id}: only internal application transport may be "
                    "retained as a product API"
                )

            capability = surface.get("capability")
            if not isinstance(capability, Mapping):
                problems.append(f"{surface_id}: capability classification is missing")
                capability_status = ""
                capability_id = ""
            else:
                capability_status = str(capability.get("status", ""))
                capability_id = str(capability.get("id") or "")
                if capability_status not in CAPABILITY_STATUSES:
                    problems.append(
                        f"{surface_id}: invalid capability status "
                        f"{capability_status!r}"
                    )
                if capability_status == "declared":
                    capability_owner = str(capability.get("owner", "")).strip()
                    if not capability_id or not capability_owner:
                        problems.append(
                            f"{surface_id}: declared capability needs id and owner"
                        )
                elif capability_id:
                    problems.append(
                        f"{surface_id}: only a declared capability may carry an id"
                    )
            if disposition == "migrate" and capability_status != "declared":
                problems.append(
                    f"{surface_id}: migrate requires an owned capability contract"
                )

            financial = surface.get("financial")
            if not isinstance(financial, bool):
                problems.append(f"{surface_id}: financial must be boolean")
            elif financial and not financial_authorized:
                if disposition == "migrate":
                    problems.append(
                        f"{surface_id}: financial cutover is not authorized"
                    )
                elif disposition != "retain-temporarily":
                    problems.append(
                        f"{surface_id}: closed financial gate requires "
                        "retain-temporarily"
                    )

            if (
                disposition == "retain-temporarily"
                and not str(surface.get("exit_gate", "")).strip()
            ):
                problems.append(f"{surface_id}: temporary retention needs an exit gate")
            if (
                disposition == "retire"
                and not str(surface.get("retirement_gate", "")).strip()
            ):
                problems.append(f"{surface_id}: retirement needs a retirement gate")
            for field in ("owner", "next_gate", "production_usage"):
                if not str(surface.get(field, "")).strip():
                    problems.append(f"{surface_id}: {field} is missing")

            source_paths = surface.get("source_paths")
            if not isinstance(source_paths, list) or not source_paths:
                problems.append(f"{surface_id}: source_paths is empty")
            else:
                covered_paths.update(str(path) for path in source_paths)
            test_paths = surface.get("test_paths")
            if not isinstance(test_paths, list):
                problems.append(f"{surface_id}: test_paths must be a list")
            test_status = surface.get("test_status")
            if test_status not in {"qualifying", "partial", "missing"}:
                problems.append(f"{surface_id}: invalid test status {test_status!r}")
            if test_status == "qualifying" and not test_paths:
                problems.append(f"{surface_id}: qualifying test status needs a test")

        for path in sorted(classified_paths - covered_paths):
            problems.append(
                f"{application}: ratchet-classified path is not mapped: {path}"
            )

    inventories = ledger.get("programme", {}).get("application_inventories", ())
    source_mapped = {
        str(row.get("application", ""))
        for row in inventories
        if isinstance(row, Mapping) and row.get("surface_status") == "source-mapped"
    }
    if source_mapped != seen_applications:
        problems.append(
            "source-mapped applications disagree with source catalogues: "
            f"inventories={sorted(source_mapped)}, "
            f"catalogues={sorted(seen_applications)}"
        )
    return problems


def fleet_baseline_problems(
    ledger: Mapping[str, Any],
    catalogue: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_catalogues: Iterable[Mapping[str, Any]] = (),
) -> list[str]:
    """Bind the capability ledger to every application in the fleet sweep.

    A category count is useful discovery evidence, but it is not a capability
    catalogue.  Keeping those states distinct prevents Sub's mature registry
    from making ERP, CRM or Academy look inventoried by association.
    """

    problems: list[str] = []
    programme = ledger.get("programme", {})
    required = {str(value) for value in programme.get("required_applications", ())}
    measured = {str(value) for value in baseline.get("repos_measured", ())}
    if required != measured:
        problems.append(
            "required applications disagree with the fleet baseline: "
            f"required={sorted(required)}, measured={sorted(measured)}"
        )

    rows = programme.get("application_inventories", ())
    if not isinstance(rows, list):
        return [*problems, "programme application_inventories must be a list"]

    by_application: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            problems.append(f"application inventory {index} must be an object")
            continue
        application = str(row.get("application", ""))
        if not application:
            problems.append(f"application inventory {index} has no application")
            continue
        if application in by_application:
            problems.append(f"duplicate application inventory: {application}")
        by_application[application] = row

    for application in sorted(required - set(by_application)):
        problems.append(f"required application lacks an inventory: {application}")
    for application in sorted(set(by_application) - required):
        problems.append(
            f"application inventory is outside required scope: {application}"
        )

    counts = baseline.get("counts", {})
    catalogue_application = str(catalogue.get("source", {}).get("application", ""))
    source_catalogue_applications = {
        str(row.get("source", {}).get("application", "")) for row in source_catalogues
    }
    for application, row in sorted(by_application.items()):
        status = row.get("surface_status")
        disposition = row.get("disposition")
        if status not in SURFACE_STATUSES:
            problems.append(f"{application}: invalid surface status {status!r}")
        if disposition not in APPLICATION_DISPOSITIONS:
            problems.append(
                f"{application}: invalid application disposition {disposition!r}"
            )
        if not str(row.get("owner", "")).strip():
            problems.append(f"{application}: application inventory owner is missing")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            problems.append(f"{application}: application inventory evidence is missing")
        if not str(row.get("next_gate", "")).strip():
            problems.append(
                f"{application}: application inventory next gate is missing"
            )

        category_counts = counts.get(application, {})
        if not isinstance(category_counts, Mapping):
            problems.append(f"{application}: fleet baseline counts are missing")
            continue
        total = sum(
            value for value in category_counts.values() if isinstance(value, int)
        )
        if status == "measured-zero" and total != 0:
            problems.append(
                f"{application}: nonzero fleet surface cannot be measured-zero"
            )
        if total == 0 and status != "measured-zero":
            problems.append(
                f"{application}: zero fleet surface must be recorded as measured-zero"
            )
        if status == "capability-catalogued" and application != catalogue_application:
            problems.append(
                f"{application}: capability-catalogued has no matching source catalogue"
            )
        if (
            status == "source-mapped"
            and source_catalogue_applications
            and application not in source_catalogue_applications
        ):
            problems.append(
                f"{application}: source-mapped has no matching source catalogue"
            )
        if status == "category-counted" and total == 0:
            problems.append(f"{application}: category-counted has no measured surface")
        if disposition == "not-applicable" and status != "measured-zero":
            problems.append(
                f"{application}: not-applicable requires a measured-zero surface"
            )
    return problems


def _step_status(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(value.get("status", ""))
    return ""


def _step_evidence(value: object) -> list[object]:
    if isinstance(value, Mapping):
        evidence = value.get("evidence", ())
        if isinstance(evidence, list):
            return evidence
    return []


def _packet_incomplete(row: Mapping[str, Any]) -> list[str]:
    packet = row.get("packet", {})
    if not isinstance(packet, Mapping):
        return list(PACKET_FIELDS)
    incomplete: list[str] = []
    for field in PACKET_FIELDS:
        value = packet.get(field)
        status = _step_status(value)
        if status not in {"ready", "not-applicable"}:
            incomplete.append(field)
    return incomplete


def _nested_forbidden_fields(value: object, path: str = "snapshot") -> list[str]:
    problems: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            next_path = f"{path}.{key}"
            if normalized in FORBIDDEN_SNAPSHOT_FIELDS:
                problems.append(f"forbidden snapshot field: {next_path}")
            problems.extend(_nested_forbidden_fields(nested, next_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            problems.extend(_nested_forbidden_fields(nested, f"{path}[{index}]"))
    return problems


def snapshot_problems(snapshot: Mapping[str, Any]) -> list[str]:
    problems = _nested_forbidden_fields(snapshot)
    source = snapshot.get("source")
    if not isinstance(source, Mapping):
        return [*problems, "snapshot source is missing"]
    for field in (
        "application",
        "environment",
        "server_name",
        "database_name",
        "captured_at",
        "source_revision",
    ):
        if not str(source.get(field, "")).strip():
            problems.append(f"snapshot source.{field} is missing")

    coverage = snapshot.get("coverage")
    if not isinstance(coverage, Mapping):
        problems.append("snapshot coverage is missing")
    else:
        unknown = set(coverage) - REQUIRED_COVERAGE
        missing = REQUIRED_COVERAGE - set(coverage)
        if unknown:
            problems.append(
                f"snapshot coverage has unknown categories: {sorted(unknown)}"
            )
        if missing:
            problems.append(
                f"snapshot coverage is missing categories: {sorted(missing)}"
            )
        for category, measured in coverage.items():
            if not isinstance(measured, bool):
                problems.append(f"snapshot coverage {category} must be boolean")

    bindings = snapshot.get("bindings")
    if not isinstance(bindings, list):
        problems.append("snapshot bindings must be a list")
    else:
        for index, binding in enumerate(bindings):
            if not isinstance(binding, Mapping):
                problems.append(f"snapshot binding {index} must be an object")
                continue
            for field in (
                "connector_key",
                "connector_version",
                "installation_state",
                "capability_id",
                "binding_state",
                "count",
            ):
                if field not in binding:
                    problems.append(f"snapshot binding {index} lacks {field}")

    legacy_surfaces = snapshot.get("legacy_surfaces")
    if not isinstance(legacy_surfaces, list):
        problems.append("snapshot legacy_surfaces must be a list")
    else:
        seen_surface_ids: set[str] = set()
        for index, surface in enumerate(legacy_surfaces):
            if not isinstance(surface, Mapping):
                problems.append(f"snapshot legacy surface {index} must be an object")
                continue
            surface_id = str(surface.get("surface_id", ""))
            if not surface_id:
                problems.append(f"snapshot legacy surface {index} lacks surface_id")
            elif surface_id in seen_surface_ids:
                problems.append(f"duplicate snapshot legacy surface: {surface_id}")
            seen_surface_ids.add(surface_id)
            observation = surface.get("observation")
            if observation not in LEGACY_OBSERVATIONS:
                problems.append(
                    f"snapshot legacy surface {surface_id} has invalid observation "
                    f"{observation!r}"
                )
            count = surface.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                problems.append(
                    f"snapshot legacy surface {surface_id} count must be a "
                    "non-negative integer"
                )
            elif observation == "absent" and count != 0:
                problems.append(
                    f"snapshot legacy surface {surface_id} absent requires zero"
                )
            elif observation in {"active", "configured", "historical"} and count < 1:
                problems.append(
                    f"snapshot legacy surface {surface_id} {observation} requires "
                    "a positive count"
                )
            if not str(surface.get("evidence_kind", "")).strip():
                problems.append(
                    f"snapshot legacy surface {surface_id} lacks evidence_kind"
                )
    return problems


def _lower_hex(value: object, length: int) -> bool:
    text_value = str(value)
    return len(text_value) == length and all(
        character in "0123456789abcdef" for character in text_value
    )


def migration_rehearsal_problems(rehearsal: Mapping[str, Any]) -> list[str]:
    """Validate one staging-derived migration and survivor proof.

    The product census proves what exists after migration.  This second record
    binds that result to the pre-migration staging population and is the only
    place allowed to claim billing/non-target preservation.
    """

    problems = _nested_forbidden_fields(rehearsal, "migration_rehearsal")
    if rehearsal.get("schema_version") != 1:
        problems.append("migration rehearsal schema_version must be 1")
    if rehearsal.get("evidence_kind") != "integrator-adoption-migration-rehearsal":
        problems.append("migration rehearsal evidence_kind is invalid")

    source = rehearsal.get("source")
    if not isinstance(source, Mapping):
        return [*problems, "migration rehearsal source is missing"]
    for field in (
        "application",
        "environment",
        "server_name",
        "captured_at",
        "staging_database_name",
        "candidate_base_revision",
        "candidate_patch_sha256",
        "candidate_image_digest",
        "clone_database_name",
        "capture_method",
    ):
        if not str(source.get(field, "")).strip():
            problems.append(f"migration rehearsal source.{field} is missing")
    if source.get("environment") != "staging":
        problems.append("migration rehearsal evidence is not from staging")
    if not _lower_hex(source.get("candidate_base_revision"), 40):
        problems.append("migration rehearsal candidate base revision is invalid")
    if not _lower_hex(source.get("candidate_patch_sha256"), 64):
        problems.append("migration rehearsal candidate patch digest is invalid")
    image_digest = str(source.get("candidate_image_digest", ""))
    if not image_digest.startswith("sha256:") or not _lower_hex(
        image_digest.removeprefix("sha256:"), 64
    ):
        problems.append("migration rehearsal candidate image digest is invalid")

    migration = rehearsal.get("migration")
    if not isinstance(migration, Mapping):
        problems.append("migration rehearsal migration evidence is missing")
    else:
        before_heads = migration.get("before_heads")
        after_heads = migration.get("after_heads")
        if not isinstance(before_heads, list) or not before_heads:
            problems.append("migration rehearsal before_heads are missing")
        if not isinstance(after_heads, list) or not after_heads:
            problems.append("migration rehearsal after_heads are missing")
        repairs = migration.get("repaired_preconditions")
        if not isinstance(repairs, list):
            problems.append("migration rehearsal repair evidence is missing")
        else:
            repairs_expected = migration.get("repairs_expected")
            if not isinstance(repairs_expected, bool):
                problems.append("migration rehearsal repairs_expected must be boolean")
            elif repairs_expected != bool(repairs):
                problems.append(
                    "migration rehearsal repair expectation disagrees with evidence"
                )
            for repair in repairs:
                if not isinstance(repair, Mapping):
                    problems.append("migration rehearsal repair row is invalid")
                    continue
                row_count = repair.get("row_count")
                non_null = repair.get("non_null_count")
                distinct = repair.get("distinct_count")
                if not all(
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                    for value in (row_count, non_null, distinct)
                ) or not (row_count == non_null == distinct):
                    problems.append(
                        "migration rehearsal repair population is ambiguous: "
                        f"{repair.get('table', '')}.{repair.get('column', '')}"
                    )
                if repair.get("before_primary_key") is not False:
                    problems.append("migration rehearsal repair lacks precondition")
                if not str(repair.get("after_primary_key", "")).strip():
                    problems.append("migration rehearsal repair lacks resulting key")

    survivor = rehearsal.get("survivor_counts")
    if not isinstance(survivor, Mapping):
        problems.append("migration rehearsal survivor counts are missing")
    else:
        financial_before = survivor.get("financial_before")
        financial_after = survivor.get("financial_after")
        if not isinstance(financial_before, Mapping) or not financial_before:
            problems.append("migration rehearsal financial baseline is missing")
        elif financial_before != financial_after:
            problems.append("migration rehearsal changed financial counts")
        nonfinancial_before = survivor.get("nonfinancial_before")
        nonfinancial_after = survivor.get("nonfinancial_after")
        if not isinstance(nonfinancial_before, Mapping) or not nonfinancial_before:
            problems.append("migration rehearsal nonfinancial baseline is missing")
        elif nonfinancial_before != nonfinancial_after:
            problems.append("migration rehearsal changed nonfinancial counts")
        marker_before = survivor.get("legacy_marker_row_total_before")
        marker_after = survivor.get("legacy_marker_row_total_after")
        digest_before = survivor.get("legacy_marker_counts_sha256_before")
        digest_after = survivor.get("legacy_marker_counts_sha256_after")
        if (
            not isinstance(marker_before, int)
            or isinstance(marker_before, bool)
            or marker_before < 0
            or marker_before != marker_after
            or not _lower_hex(digest_before, 64)
            or digest_before != digest_after
        ):
            problems.append("migration rehearsal changed legacy marker counts")

    safety = rehearsal.get("safety_observations")
    if not isinstance(safety, Mapping):
        problems.append("migration rehearsal safety observations are missing")
    else:
        for field in (
            "source_snapshot_read_only",
            "clone_migrations_only",
            "exact_candidate_image_proven",
            "financial_counts_preserved",
            "billing_non_interference_proven",
            "untouched_integration_counts_preserved",
            "temporary_secret_and_snapshot_files_removed",
        ):
            if safety.get(field) is not True:
                problems.append(f"migration rehearsal safety proof is false: {field}")
        if safety.get("shared_staging_database_modified") is not False:
            problems.append("migration rehearsal modified the shared staging database")
        if safety.get("payment_execution_changed") is not False:
            problems.append("migration rehearsal changed payment execution")
        if safety.get("financial_cutover_authorized") is not False:
            problems.append("migration rehearsal authorized financial cutover")
        if safety.get("production_evidence") is not False:
            problems.append("migration rehearsal is mislabeled as production evidence")
    return problems


def snapshot_catalogue_problems(
    snapshots: Iterable[Mapping[str, Any]],
    catalogues: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Bind aggregate runtime identities back to pinned committed-source maps."""

    catalogue_surfaces = {
        str(catalogue.get("source", {}).get("application", "")): {
            str(surface.get("id", "")) for surface in catalogue.get("surfaces", ())
        }
        for catalogue in catalogues
    }
    problems: list[str] = []
    for snapshot in snapshots:
        application = str(snapshot.get("source", {}).get("application", ""))
        known = catalogue_surfaces.get(application)
        if known is None:
            continue
        for surface in snapshot.get("legacy_surfaces", ()):
            if not isinstance(surface, Mapping):
                continue
            surface_id = str(surface.get("surface_id", ""))
            if surface_id not in known:
                problems.append(
                    f"{application}: runtime observation names no source surface: "
                    f"{surface_id}"
                )
    return problems


def production_snapshot_coverage_problems(
    snapshot: Mapping[str, Any],
    ledger: Mapping[str, Any],
    catalogue: Mapping[str, Any],
    source_catalogues: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Prove that one production census accounts for its committed source.

    A partial production query is useful evidence, but it is not a completed
    inventory.  Source-mapped products must name every mapped surface, and a
    capability-catalogued product must account for every declared capability,
    including explicit zero/absent rows.  This keeps "no row" from meaning
    both "absent" and "the adapter never looked".
    """

    if snapshot.get("source", {}).get("environment") != "production":
        return []

    application = str(snapshot.get("source", {}).get("application", ""))
    problems = list(snapshot_problems(snapshot))
    incomplete_coverage = sorted(
        category
        for category in REQUIRED_COVERAGE
        if snapshot.get("coverage", {}).get(category) is not True
    )
    if incomplete_coverage:
        problems.append(
            f"{application}: production coverage is incomplete: "
            + ", ".join(incomplete_coverage)
        )
    safety = snapshot.get("safety_observations")
    if (
        not isinstance(safety, Mapping)
        or safety.get("database_read_only_proven") is not True
    ):
        problems.append(f"{application}: database read-only proof is missing")

    inventories = {
        str(row.get("application", "")): row
        for row in ledger.get("programme", {}).get("application_inventories", ())
        if isinstance(row, Mapping)
    }
    inventory = inventories.get(application)
    if inventory is None:
        return [*problems, f"{application}: no application inventory exists"]

    legacy_surfaces = [
        surface
        for surface in snapshot.get("legacy_surfaces", ())
        if isinstance(surface, Mapping)
    ]
    for surface in legacy_surfaces:
        if surface.get("observation") == "unmeasured":
            problems.append(
                f"{application}: production observation is unmeasured: "
                f"{surface.get('surface_id', '')}"
            )

    status = inventory.get("surface_status")
    source_by_application = {
        str(row.get("source", {}).get("application", "")): row
        for row in source_catalogues
    }
    if status == "source-mapped":
        source_catalogue = source_by_application.get(application)
        if source_catalogue is None:
            problems.append(
                f"{application}: source-mapped production inventory has no catalogue"
            )
            return sorted(set(problems))
        deployed_revision = str(snapshot.get("source", {}).get("source_revision", ""))
        mapped_revision = str(source_catalogue.get("source", {}).get("revision", ""))
        if deployed_revision != mapped_revision:
            problems.append(
                f"{application}: source catalogue revision differs from deployed "
                f"revision: catalogue={mapped_revision}, deployed={deployed_revision}"
            )

        expected = {
            str(surface.get("id", ""))
            for surface in source_catalogue.get("surfaces", ())
            if isinstance(surface, Mapping)
        }
        observed = {str(surface.get("surface_id", "")) for surface in legacy_surfaces}
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        if missing:
            problems.append(
                f"{application}: source surfaces are unobserved: " + ", ".join(missing)
            )
        if unknown:
            problems.append(
                f"{application}: production observations name no source surface: "
                + ", ".join(unknown)
            )
    elif status == "capability-catalogued":
        catalogue_application = str(catalogue.get("source", {}).get("application", ""))
        if application != catalogue_application:
            problems.append(
                f"{application}: capability catalogue belongs to "
                f"{catalogue_application}"
            )
            return sorted(set(problems))
        deployed_revision = str(snapshot.get("source", {}).get("source_revision", ""))
        mapped_revision = str(catalogue.get("source", {}).get("revision", ""))
        if deployed_revision != mapped_revision:
            problems.append(
                f"{application}: source catalogue revision differs from deployed "
                f"revision: catalogue={mapped_revision}, deployed={deployed_revision}"
            )

        expected = catalogue_capability_keys(catalogue)
        observed = {
            _capability_key(
                application,
                str(binding.get("connector_key", "")),
                str(binding.get("capability_id", "")),
            )
            for binding in snapshot.get("bindings", ())
            if isinstance(binding, Mapping)
        }
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        if missing:
            problems.append(
                f"{application}: catalogued capabilities are unobserved: "
                + ", ".join(missing)
            )
        if unknown:
            problems.append(
                f"{application}: production bindings name no catalogued capability: "
                + ", ".join(unknown)
            )
    elif status == "measured-zero":
        live_bindings = [
            binding
            for binding in snapshot.get("bindings", ())
            if isinstance(binding, Mapping)
            and isinstance(binding.get("count"), int)
            and int(binding.get("count", 0)) > 0
        ]
        observed_surfaces = [
            surface
            for surface in legacy_surfaces
            if surface.get("observation") != "absent"
            or int(surface.get("count", 0)) > 0
        ]
        if live_bindings or observed_surfaces:
            problems.append(
                f"{application}: measured-zero application has a production surface"
            )
    else:
        problems.append(
            f"{application}: surface status {status!r} cannot prove a complete "
            "production inventory"
        )
    return sorted(set(problems))


def production_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    ledger: Mapping[str, Any] | None = None,
    catalogue: Mapping[str, Any] | None = None,
    source_catalogues: Iterable[Mapping[str, Any]] = (),
) -> list[Mapping[str, Any]]:
    strict_context = ledger is not None and catalogue is not None
    result: list[Mapping[str, Any]] = []
    for snapshot in snapshots:
        if snapshot.get("source", {}).get("environment") != "production":
            continue
        if snapshot_problems(snapshot):
            continue
        if not all(
            snapshot.get("coverage", {}).get(category) is True
            for category in REQUIRED_COVERAGE
        ):
            continue
        if strict_context and production_snapshot_coverage_problems(
            snapshot,
            ledger,
            catalogue,
            source_catalogues,
        ):
            continue
        result.append(snapshot)
    return result


def _cohort_by_key(ledger: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in ledger.get("cohorts", ()):
        for capability in row.get("capabilities", ()):
            result[
                _capability_key(
                    str(row.get("application", "")),
                    str(row.get("connector_key", "")),
                    str(capability),
                )
            ] = row
    return result


def _live_bindings(snapshot: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    application = str(snapshot.get("source", {}).get("application", ""))
    live: list[tuple[str, Mapping[str, Any]]] = []
    for binding in snapshot.get("bindings", ()):
        if not isinstance(binding, Mapping):
            continue
        if int(binding.get("count", 0)) <= 0:
            continue
        if binding.get("installation_state") not in LIVE_STATES:
            continue
        if binding.get("binding_state") not in LIVE_STATES:
            continue
        live.append(
            (
                _capability_key(
                    application,
                    str(binding.get("connector_key", "")),
                    str(binding.get("capability_id", "")),
                ),
                binding,
            )
        )
    return live


def staging_snapshot_for_target(
    ledger: Mapping[str, Any],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    target_cohort_id: str,
) -> Mapping[str, Any] | None:
    """Select staging evidence for the target's owning application."""

    target = next(
        (
            row
            for row in ledger.get("cohorts", ())
            if str(row.get("id", "")) == target_cohort_id
        ),
        None,
    )
    if target is None:
        return None
    application = str(target.get("application", ""))
    return next(
        (
            snapshot
            for snapshot in snapshots
            if snapshot.get("source", {}).get("environment") == "staging"
            and str(snapshot.get("source", {}).get("application", "")) == application
        ),
        None,
    )


def staging_rehearsal_blockers(
    ledger: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    target_cohort_id: str,
) -> list[str]:
    """Return what prevents one capability from entering a staging rehearsal."""

    blockers: list[str] = []
    if snapshot.get("source", {}).get("environment") != "staging":
        blockers.append("rehearsal evidence is not from staging")
        return blockers
    if snapshot_problems(snapshot):
        blockers.append("staging snapshot is structurally invalid")
        return blockers

    incomplete_coverage = sorted(
        category
        for category in REQUIRED_COVERAGE
        if snapshot.get("coverage", {}).get(category) is not True
    )
    if incomplete_coverage:
        blockers.append(
            "staging coverage is incomplete: " + ", ".join(incomplete_coverage)
        )

    safety = snapshot.get("safety_observations", {})
    if safety.get("billing_non_interference_proven") is not True:
        blockers.append("billing non-interference is unproven")

    rows_by_id = {str(row.get("id", "")): row for row in ledger.get("cohorts", ())}
    target = rows_by_id.get(target_cohort_id)
    if target is None:
        blockers.append(f"staging target cohort is unknown: {target_cohort_id}")
    else:
        target_application = str(target.get("application", ""))
        snapshot_application = str(snapshot.get("source", {}).get("application", ""))
        if snapshot_application != target_application:
            blockers.append(
                f"staging snapshot application {snapshot_application} does not "
                f"match target application {target_application}"
            )

    cohorts = _cohort_by_key(ledger)
    for key, _binding in _live_bindings(snapshot):
        row = cohorts.get(key)
        if row is None:
            blockers.append(f"staging live capability has no disposition: {key}")
            continue
        row_id = str(row.get("id", ""))
        if row_id == target_cohort_id:
            continue
        survivor = row.get("survivor_canary")
        if _step_status(survivor) != "ready" or not _step_evidence(survivor):
            blockers.append(f"survivor canary is missing: {row_id}")
    return sorted(set(blockers))


def retirement_blockers(
    ledger: Mapping[str, Any],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    catalogue: Mapping[str, Any] | None = None,
    source_catalogues: Iterable[Mapping[str, Any]] = (),
) -> list[str]:
    snapshots = list(snapshots)
    source_catalogues = tuple(source_catalogues)
    production = production_snapshots(
        snapshots,
        ledger=ledger if catalogue is not None else None,
        catalogue=catalogue,
        source_catalogues=source_catalogues,
    )
    blockers: list[str] = []
    if catalogue is not None:
        for snapshot in snapshots:
            if snapshot.get("source", {}).get("environment") != "production":
                continue
            application = str(snapshot.get("source", {}).get("application", ""))
            blockers.extend(
                f"production inventory incomplete for {application}: {problem}"
                for problem in production_snapshot_coverage_problems(
                    snapshot,
                    ledger,
                    catalogue,
                    source_catalogues,
                )
            )
    required_apps = set(ledger.get("programme", {}).get("required_applications", ()))
    measured_apps = {
        str(snapshot.get("source", {}).get("application", ""))
        for snapshot in production
    }
    for application in sorted(required_apps - measured_apps):
        blockers.append(f"production inventory is unmeasured for {application}")

    for inventory in ledger.get("programme", {}).get("application_inventories", ()):
        if not isinstance(inventory, Mapping):
            continue
        application = str(inventory.get("application", ""))
        if inventory.get("surface_status") in {"category-counted", "source-mapped"}:
            blockers.append(f"capability inventory incomplete for {application}")
        disposition = inventory.get("disposition")
        if disposition == "unclassified":
            blockers.append(f"application disposition is unclassified: {application}")
        elif (
            disposition == "retire"
            and _step_status(inventory.get("retirement_evidence")) != "ready"
        ):
            blockers.append(
                f"application retirement evidence is missing: {application}"
            )

    cohorts = _cohort_by_key(ledger)
    required_rows: dict[str, Mapping[str, Any]] = {
        str(row.get("id", "")): row
        for row in ledger.get("cohorts", ())
        if row.get("programme_required") is True
    }
    for snapshot in production:
        for key, _binding in _live_bindings(snapshot):
            row = cohorts.get(key)
            if row is None:
                blockers.append(f"live capability has no disposition: {key}")
            else:
                required_rows[str(row.get("id", ""))] = row

    for row_id, row in sorted(required_rows.items()):
        disposition = row.get("disposition")
        if disposition == "migrate":
            incomplete = _packet_incomplete(row)
            if incomplete:
                blockers.append(
                    f"migration packet incomplete for {row_id}: {', '.join(incomplete)}"
                )
        elif disposition == "retire":
            retirement = row.get("retirement", {})
            for field in ("zero_traffic_evidence", "retirement_gate"):
                if _step_status(retirement.get(field)) != "ready":
                    blockers.append(
                        f"retirement evidence incomplete for {row_id}: {field}"
                    )
        elif disposition == "retain-temporarily":
            blockers.append(f"capability is retained temporarily: {row_id}")

    if production:
        for snapshot in production:
            counts = snapshot.get("shared_table_counts", {})
            for table in ledger.get("programme", {}).get(
                "shared_sub_control_plane_tables", ()
            ):
                if snapshot.get("source", {}).get("application") != "dotmac_sub":
                    continue
                if int(counts.get(table, 0)) > 0:
                    blockers.append(f"shared control-plane table is not empty: {table}")
    return sorted(set(blockers))


def structural_problems(
    ledger: Mapping[str, Any],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    catalogue: Mapping[str, Any] | None = None,
    source_catalogues: Iterable[Mapping[str, Any]] = (),
) -> list[str]:
    snapshots = list(snapshots)
    source_catalogues = tuple(source_catalogues)
    problems: list[str] = []
    programme = ledger.get("programme")
    if not isinstance(programme, Mapping):
        return ["ledger programme is missing"]

    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for index, row in enumerate(ledger.get("cohorts", ())):
        row_id = str(row.get("id", ""))
        if not row_id:
            problems.append(f"cohort {index} has no id")
        elif row_id in seen_ids:
            problems.append(f"duplicate cohort id: {row_id}")
        seen_ids.add(row_id)

        disposition = row.get("disposition")
        if disposition not in DISPOSITIONS:
            problems.append(f"{row_id}: invalid disposition {disposition!r}")
        if not str(row.get("owner", "")).strip():
            problems.append(f"{row_id}: owner is missing")
        if not str(row.get("disposition_basis", "")).strip():
            problems.append(f"{row_id}: disposition basis is missing")
        capabilities = row.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            problems.append(f"{row_id}: capabilities must be a non-empty list")
            continue
        for capability in capabilities:
            key = _capability_key(
                str(row.get("application", "")),
                str(row.get("connector_key", "")),
                str(capability),
            )
            if key in seen_keys:
                problems.append(f"duplicate capability disposition: {key}")
            seen_keys.add(key)

        if row.get("financial") is True and disposition == "migrate":
            if programme.get("financial_cutover_authorized") is not True:
                problems.append(f"{row_id}: financial cutover is not authorized")
        if disposition == "migrate":
            packet = row.get("packet")
            if not isinstance(packet, Mapping):
                problems.append(f"{row_id}: migration packet is missing")
            else:
                if set(packet) != set(PACKET_FIELDS):
                    problems.append(f"{row_id}: migration packet fields disagree")
                for field, value in packet.items():
                    status = _step_status(value)
                    if status not in STEP_STATUSES:
                        problems.append(f"{row_id}.{field}: invalid status {status!r}")
                    if status in {"ready", "not-applicable"} and not _step_evidence(
                        value
                    ):
                        problems.append(f"{row_id}.{field}: status needs evidence")
        elif disposition == "retain-temporarily":
            if not str(row.get("retain_until_gate", "")).strip():
                problems.append(f"{row_id}: temporary retention needs an exit gate")
        elif disposition == "retire":
            retirement = row.get("retirement")
            if not isinstance(retirement, Mapping):
                problems.append(f"{row_id}: retirement evidence is missing")

        survivor = row.get("survivor_canary")
        if survivor is not None:
            status = _step_status(survivor)
            if status not in STEP_STATUSES:
                problems.append(f"{row_id}.survivor_canary: invalid status {status!r}")
            if status in {"ready", "not-applicable"} and not _step_evidence(survivor):
                problems.append(f"{row_id}.survivor_canary: status needs evidence")

    for row in ledger.get("catalogue_only", ()):
        if row.get("disposition") != "retire":
            problems.append(
                f"catalogue-only {row.get('connector_key')}: disposition must be retire"
            )
        if row.get("capabilities"):
            connector_key = row.get("connector_key")
            problems.append(
                f"catalogue-only {connector_key}: fake capabilities are forbidden"
            )

    for snapshot in snapshots:
        problems.extend(snapshot_problems(snapshot))

    if programme.get("platform_retirement_claimed") is True:
        blockers = retirement_blockers(
            ledger,
            snapshots,
            catalogue=catalogue,
            source_catalogues=source_catalogues,
        )
        if blockers:
            problems.append(
                "platform retirement is claimed while blockers remain: "
                + "; ".join(blockers)
            )
    return sorted(set(problems))


def synthetic_production_snapshot(
    *, connector_key: str, capability_id: str
) -> dict[str, Any]:
    """Return a complete synthetic observation for sensitivity tests only."""

    return {
        "schema_version": 1,
        "source": {
            "application": "dotmac_sub",
            "environment": "production",
            "server_name": "synthetic-production",
            "database_name": "synthetic",
            "captured_at": "2026-08-18T00:00:00Z",
            "source_revision": "synthetic",
        },
        "coverage": dict.fromkeys(REQUIRED_COVERAGE, True),
        "bindings": [
            {
                "connector_key": connector_key,
                "connector_version": "1.0.0",
                "installation_state": "enabled",
                "capability_id": capability_id,
                "binding_state": "enabled",
                "count": 1,
            }
        ],
        "shared_table_counts": {},
        "legacy_surfaces": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=pathlib.Path, default=DEFAULT_LEDGER)
    parser.add_argument("--catalogue", type=pathlib.Path, default=DEFAULT_CATALOGUE)
    parser.add_argument(
        "--fleet-baseline", type=pathlib.Path, default=DEFAULT_FLEET_BASELINE
    )
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        action="append",
        dest="snapshots",
        help="typed runtime inventory snapshot; may be repeated",
    )
    parser.add_argument(
        "--rehearsal",
        type=pathlib.Path,
        action="append",
        dest="rehearsals",
        help="typed staging migration rehearsal evidence; may be repeated",
    )
    parser.add_argument(
        "--require-retirement-ready",
        action="store_true",
        help="fail while any truthful programme blocker remains",
    )
    parser.add_argument(
        "--require-staging-ready",
        action="store_true",
        help="fail until the current capability has staging and survivor evidence",
    )
    parser.add_argument(
        "--target-cohort",
        help="staging target; defaults to programme.current_target_cohort_id",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ledger = _read(args.ledger)
    catalogue = _read(args.catalogue)
    source_catalogues = [_read(path) for path in DEFAULT_SOURCE_CATALOGUES]
    fleet_baseline = _read(args.fleet_baseline)
    paths = tuple(args.snapshots or DEFAULT_SNAPSHOTS)
    snapshots = [_read(path) for path in paths]
    rehearsal_paths = tuple(args.rehearsals or DEFAULT_REHEARSALS)
    rehearsals = [_read(path) for path in rehearsal_paths]
    problems = [
        *catalogue_problems(ledger, catalogue),
        *source_catalogue_problems(source_catalogues, ledger),
        *fleet_baseline_problems(ledger, catalogue, fleet_baseline, source_catalogues),
        *snapshot_catalogue_problems(snapshots, source_catalogues),
        *(
            problem
            for rehearsal in rehearsals
            for problem in migration_rehearsal_problems(rehearsal)
        ),
        *structural_problems(
            ledger,
            snapshots,
            catalogue=catalogue,
            source_catalogues=source_catalogues,
        ),
    ]
    if problems:
        print("INTEGRATOR ADOPTION LEDGER INVALID", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    if args.require_staging_ready:
        target = args.target_cohort or ledger["programme"].get(
            "current_target_cohort_id", ""
        )
        staging = staging_snapshot_for_target(
            ledger,
            snapshots,
            target_cohort_id=str(target),
        )
        if staging is None:
            target_application = next(
                (
                    str(row.get("application", ""))
                    for row in ledger.get("cohorts", ())
                    if str(row.get("id", "")) == str(target)
                ),
                str(target),
            )
            print("INTEGRATOR STAGING REHEARSAL BLOCKED", file=sys.stderr)
            print(
                f"- staging inventory is unmeasured for {target_application}",
                file=sys.stderr,
            )
            return 1
        staging_blockers = staging_rehearsal_blockers(
            ledger, staging, target_cohort_id=str(target)
        )
        if staging_blockers:
            print("INTEGRATOR STAGING REHEARSAL BLOCKED", file=sys.stderr)
            for blocker in staging_blockers:
                print(f"- {blocker}", file=sys.stderr)
            return 1
        print("INTEGRATOR STAGING REHEARSAL READY")
        return 0

    blockers = retirement_blockers(
        ledger,
        snapshots,
        catalogue=catalogue,
        source_catalogues=source_catalogues,
    )
    if blockers:
        print("INTEGRATOR PLATFORM RETIREMENT BLOCKED")
        for blocker in blockers:
            print(f"- {blocker}")
        return 1 if args.require_retirement_ready else 0
    print("INTEGRATOR PLATFORM RETIREMENT READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
