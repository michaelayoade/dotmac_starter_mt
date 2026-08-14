"""Measure the fleet duplication baseline the decomposition matrix freezes.

`docs/inventories/fleet-decomposition-matrix.md` claims, per capability family,
how many persisted tables each source monolith owns and how many table NAMES are
implemented in more than one of them. Those numbers are the programme's only
countable duplication signal, so they are measured here rather than eyeballed.

Two deliberate choices, both learned from `restatement_sweep.py`:

* **AST, not grep.** `__tablename__` is assigned in a class body, sometimes
  beside `__table_args__` on the same line in a mixin, and a grep for the
  literal also matches the string inside a docstring or a migration comment.
* **A missing repo is UNMEASURED, never zero.** A sweep that silently scores 0
  for a repo it could not find would report the duplication as solved. Absent
  repos are named in the output and make the ratchet abstain.

Run it from a checkout that has the fleet beside it::

    python scripts/fleet_decomposition_sweep.py --check
    python scripts/fleet_decomposition_sweep.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "fleet-decomposition-baseline.json"

# Repo -> the subtree its persisted models live under. The three source
# monoliths follow the same `app/models/` convention; the vendor control plane
# is a thin assembly whose models sit beside their feature, so the path is
# per-repo rather than assumed.
MODEL_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dotmac_erp", ("app", "models")),
    ("dotmac_crm", ("app", "models")),
    ("dotmac_sub", ("app", "models")),
    ("dotmac_vendor_control_plane", ("src", "vendor_cp")),
)

REPOS = tuple(repo for repo, _ in MODEL_ROOTS)

# The three being decomposed. The vendor control plane is measured for a
# different reason — see the matrix § "The fourth repository is not a fourth
# monolith" — so anything that reasons about "the monoliths" must say so
# explicitly rather than meaning "everything in REPOS".
SOURCE_MONOLITHS = ("dotmac_erp", "dotmac_crm", "dotmac_sub")

# Ordered: first match wins, so platform invariants claim their tables before a
# broader domain pattern can. A family here is a MEASUREMENT bucket for the
# matrix, not an approved package boundary (ADR-0006 § "The extraction rule").
FAMILIES: tuple[tuple[str, str], ...] = (
    # --- Vendor/product-lifecycle families, first because they are narrow and
    # would otherwise be swallowed by a broader product pattern. `offer_versions`
    # is the live example: an immutable priced VENDOR offer is not an ISP service
    # offer, and `subscriber-service`'s `^offer` would have claimed it.
    ("licensing-issuance", r"^(licence|license)"),
    ("entitlement-allocation", r"^(allocation|allocations$|entitlement)"),
    ("commercial-offers", r"^offer_version"),
    ("fleet-deployment", r"^(deployment_|applied_state_)"),
    (
        "identity-access",
        r"^(sessions|user_credentials|mfa_|api_keys|oauth_tokens|"
        r"federated_identities|access_credentials|access_invitations|"
        r"authorization_presets|system_users|reseller_users|vendor_users|"
        r"device_tokens|.*_sessions$|.*_access_tokens$)",
    ),
    (
        "authorization",
        r"^(roles|permissions|role_permissions|person_roles|party_roles|"
        r"capabilit|.*_roles$|.*_permissions$)",
    ),
    (
        "party-identity",
        r"^(parties|party_|people|person_|organization|org_bank_directory|"
        r"tenants?$|contacts?$|addresses|resellers$|vendors$|"
        r"vendor_accounts$|customers?$|customer_identity)",
    ),
    ("audit-events", r"(^audit|^event_store$|_audit_|^cross_app_drift)"),
    (
        "settings",
        r"^(domain_settings|domain_setting_history|system_configuration|"
        r"feature_flag|user_filter_preferences|table_column_|"
        r"custom_field_definition|.*_settings$|.*_config$|.*_configs$)",
    ),
    (
        "scheduling-runtime",
        r"^(scheduled_tasks|system_jobs|task_executions|saga_|"
        r"event_outbox|event_handler_|durable_timer|idempotency|"
        r"batch_operations|recurring_|.*_dead_letter$)",
    ),
    (
        "engagement-inbox",
        r"^(inbox_|team_inbox_|crm_conversation|crm_messages|"
        r"crm_message_|crm_outbox|crm_agent|crm_team|crm_routing|"
        r"crm_social|crm_pipeline|automation_rule|pipelines$|"
        r"pipeline_stages|conversation|agent_|presence|macro|messages$)",
    ),
    (
        "notifications-comms",
        r"^(notification|notifications$|comms_|communication|"
        r"campaign|crm_campaign|survey|email_|sms_|push_|"
        r"admin_alerts$|alert_notification|portal_messages|message_template|"
        r"module_email_routing|.*_notifications$|.*_notification_.*)",
    ),
    (
        "ticketing-sla",
        r"^(ticket|tickets$|support_ticket|support_team|sla_|"
        r"queue_mappings|workqueue|on_call_|.*_tickets$)",
    ),
    (
        "field-workforce",
        r"^(field_|technician|shift|skill|availability_|dispatch_|eta_|"
        r"work_order|work_link|work_log|work_outcome|installation_|"
        r"install_appointments|crew_|cost_rates|time_entry|"
        r"crm_agent_location)",
    ),
    (
        "outside-plant",
        r"^(fiber_|olt_|ont_|onu_|pon_|splitter|splice|fdh_|as_built_|"
        r"proposed_route|buildout_|wireless_|service_buildings|"
        r".*_strands$)",
    ),
    (
        "geospatial-qualification",
        r"^(geo_|coverage_areas|service_qualifications|gis_|"
        r"region_zones|pop_site|location|.*_locations?$|"
        r".*_location_.*)",
    ),
    (
        "subscriber-service",
        r"^(subscriber|subscription|offer|add_on|plan_|tariff|"
        r"catalog_|provision|bulk_provisioning|service_|quota_|usage_|"
        r"fup_|outage|speed_profiles|connection_type|account_lifecycle|"
        r"customer_(experience|outage|retention|uptime))",
    ),
    (
        "network-operations",
        r"^(nas_|tr069_|cpe_|uisp_|wireguard_|radius_|vlan|port|ip_|"
        r"ipv4_|ipv6_|dns_|jump_hosts|snmp|signal_|speed_test|"
        r"bandwidth_|device|router|interface_|monitoring_|network_|"
        r"forwarding_|connectivity_|alert_events|alert_rules|alerts$|"
        r"infrastructure_)",
    ),
    (
        "billing-revenue",
        r"^(invoice|payment|billing_|autopay|collection|credit_|refund|"
        r"subledger|dunning|policy_(sets|dunning)|prepaid_|topup|"
        r"enforcement_lock|compensation_failure|financial_access|"
        r"splynx_billing|cutover_balance|consolidated_(credit|payment)|"
        r"customer_(payment|posting|position|subledger|tax)|"
        r"account_adjustments|.*_invoices$|.*_invoice_.*|.*_payments$)",
    ),
    (
        "finance-ledger",
        r"^(gl_|ap_|ar_|journal|ledger|posted_ledger|posting_batch|bank_|"
        r"account|coa_|currency|exchange_rate|fx_|fiscal_|budget|cons_|"
        r"ipsas_|lease_|tax_|withholding_tax|deferred_tax|fund$|"
        r"appropriation|allotment|commitment|virement|reconciliation_|"
        r"financial_statement|consolidat|elimination_|intercompany|"
        r"legal_entity|business_unit|cost_center|reporting_segment|"
        r"revenue_recognition|performance_obligation|payee|pfa_directory|"
        r"remita_|card_transaction|corporate_card|cash_|ownership_interest|"
        r"transaction_rule|numbering_sequence|price_list|analysis_cube|"
        r"control_evidence$|disclosure_checklist$|"
        r"saved_analysis|balance_refresh)",
    ),
    (
        "people-payroll",
        r"^(employee|employment_|payroll|salary_|leave_|attendance|"
        r"recruit|interview|job_(applicant|description|offer|opening)|"
        r"training|discipl|grievance|exit_interview|holiday|hr_document|"
        r"loan_|succession_|position|designation|department|competency|"
        r"kra$|performance_(contract|improvement|review)|appraisal|"
        r"case_(action|document|response|witness)$|clearance_item|"
        r"staging_(employee|department|designation|"
        r"employment))",
    ),
    (
        "inventory-procurement",
        r"^(inventory|stock_|warehouse|item|bom_|bill_of_materials|"
        r"purchase_|supplier|vendor_(purchase|prequalification|"
        r"material|advance|model)|requisition|goods_|shipment|rfq_|"
        r"request_for_quotation|quotation_response|bid_evaluation|"
        r"procurement_|transfer_batch)",
    ),
    (
        "assets-fleet",
        r"^(asset|fixed_asset|fleet_|vehicle|fuel_log|maintenance_|"
        r"depreciation_|cash_generating_unit)",
    ),
    ("expenses", r"^(expense|reimburse|material_request|advance_)"),
    (
        "sales-agreements",
        r"^(quote|crm_quote|sales_order|contract|legal_documents|"
        r"document_sequences|referral|proposal|lead|crm_leads|"
        r"opportunit|commission|reseller_|generated_document|signature)",
    ),
    (
        "projects-tasks",
        r"^(project|task|milestone|template_task|checklist_template|"
        r"resource_allocation)",
    ),
    (
        "integration-external",
        r"^(integration_|connector_|external_|webhook|nextcloud|"
        r"meta_|sync_|import|erp_|crm_sync|dotmac_sub_sync|"
        r"staging_sync|owner_output_receipts|operational_|"
        r".*_sync$|.*_sync_.*)",
    ),
    (
        "analytics-reporting",
        r"^(kpi|analytics|report_|ai_|agent_performance|"
        r"department_performance|scorecard|strategic_objective|"
        r"monthly_review|institutional_|pms_|performance$|coach_|"
        r".*_snapshots?$|.*_aggregates$)",
    ),
    # A1 audit (2026-08-14): the old `governance-workflow` catch-all merged
    # eight owners on prefixes alone. These are deliberately narrow families;
    # approval, automation, forms and human work items do not share authority.
    ("approvals", r"^approval_"),
    ("workflow-automation", r"^workflow_(rule|execution)"),
    ("work-items", r"^workflow_task$"),
    ("forms-data-capture", r"^form($|_)"),
    (
        "content-help",
        r"^(admin_whats_new|help_|attachment|stored_files|avatar|public_media|"
        r".*_attachments?$|.*_comments?$)",
    ),
    ("branding-templates", r"^(brand|template_|document_template|.*_templates$)"),
    ("other", r".*"),
)

_COMPILED = tuple((name, re.compile(pattern)) for name, pattern in FAMILIES)

# A product namespace prefix is packaging, not a different capability: CRM's
# `crm_conversations` and Sub's `inbox_conversations` are the same thing built
# twice. Exact-name collisions alone therefore UNDERCOUNT the duplication, which
# is the failure mode this second measure exists to close.
_NAMESPACE_PREFIX = re.compile(r"^(crm_|team_inbox_|inbox_|support_|erp_|sub_|dotmac_)")


def denamespace(table: str) -> str:
    previous = None
    while previous != table:
        previous, table = table, _NAMESPACE_PREFIX.sub("", table)
    return table


def classify(table: str) -> str:
    for name, pattern in _COMPILED:
        if pattern.search(table):
            return name
    return "other"  # pragma: no cover - the last family matches everything


def tables_in(models_root: pathlib.Path) -> set[str]:
    """Every `__tablename__` literal assigned in a class body under `models_root`."""
    found: set[str] = set()
    for path in sorted(models_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                targets = (
                    stmt.targets
                    if isinstance(stmt, ast.Assign)
                    else [stmt.target]
                    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None
                    else []
                )
                if not any(getattr(t, "id", None) == "__tablename__" for t in targets):
                    continue
                value = stmt.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
    return found


def measure(fleet_root: pathlib.Path) -> tuple[dict, list[str]]:
    per_repo: dict[str, set[str]] = {}
    absent: list[str] = []
    for repo, parts in MODEL_ROOTS:
        models = fleet_root.joinpath(repo, *parts)
        if not models.is_dir():
            absent.append(repo)
            continue
        per_repo[repo] = tables_in(models)

    families: dict[str, dict] = collections.defaultdict(
        lambda: {repo: 0 for repo in REPOS} | {"collisions": 0, "aliased": 0}
    )
    for repo, tables in per_repo.items():
        for table in tables:
            families[classify(table)][repo] += 1

    every_table = set().union(*per_repo.values()) if per_repo else set()
    exact = {t for t in every_table if sum(t in ts for ts in per_repo.values()) >= 2}
    for table in exact:
        families[classify(table)]["collisions"] += 1

    stems: dict[str, dict[str, list[str]]] = collections.defaultdict(dict)
    for repo, tables in per_repo.items():
        for table in tables:
            stems[denamespace(table)].setdefault(repo, []).append(table)
    for stem, by_repo in stems.items():
        if len(by_repo) >= 2 and stem not in exact:
            families[classify(stem)]["aliased"] += 1

    report = {
        "repos_measured": sorted(per_repo),
        "totals": {repo: len(tables) for repo, tables in sorted(per_repo.items())},
        "duplicated_table_names": {
            "exact": len(exact),
            "aliased": sum(
                1
                for stem, by_repo in stems.items()
                if len(by_repo) >= 2 and stem not in exact
            ),
        },
        "families": {name: dict(counts) for name, counts in sorted(families.items())},
    }
    return report, absent


def _ratchet(measured: dict, baseline: dict) -> list[str]:
    """Two-directional: duplication may not grow, and a win must be recorded.

    ADR-0018 — a baseline that only ever fails upward stops being evidence the
    moment the real number drops, because nothing forces the frozen figure to
    follow reality down.
    """
    failures: list[str] = []
    names = sorted(set(measured["families"]) | set(baseline["families"]))
    for name in names:
        got = measured["families"].get(name)
        want = baseline["families"].get(name)
        if want is None:
            failures.append(f"{name}: measured but absent from the baseline")
            continue
        if got is None:
            failures.append(f"{name}: in the baseline but no longer measured")
            continue
        for key in (*REPOS, "collisions", "aliased"):
            g, w = got.get(key, 0), want.get(key, 0)
            if g > w:
                failures.append(
                    f"{name}.{key}: rose {w} -> {g}; duplication may only shrink"
                )
            elif g < w:
                failures.append(
                    f"{name}.{key}: fell {w} -> {g}; lower the baseline to record it"
                )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-root", type=pathlib.Path, default=PROJECT_ROOT.parent)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    measured, absent = measure(args.fleet_root)
    if absent:
        print(
            f"UNMEASURED (repo not found under {args.fleet_root}): {', '.join(absent)}"
        )
    if not measured["repos_measured"]:
        print("No fleet repository found; nothing measured. Not a pass.")
        return 0 if not args.check else 2

    if args.write_baseline:
        BASELINE.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
        print(f"wrote {BASELINE.relative_to(PROJECT_ROOT)}")
        return 0

    print(json.dumps(measured, indent=2, sort_keys=True))

    if not args.check:
        return 0
    if absent:
        print(
            "\nRatchet abstains: the baseline covers every repository in MODEL_ROOTS."
        )
        return 2
    failures = _ratchet(measured, json.loads(BASELINE.read_text()))
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
