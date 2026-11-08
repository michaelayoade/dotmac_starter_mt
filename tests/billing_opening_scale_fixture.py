"""Deterministic synthetic opening census at (and below) the full-run scale.

Reproduces the SHAPE of the Phase A full run (15,293 partitions, 120,670
invoices, closed/draft/void/proforma populations, status-open candidates in a
few hundred partitions, 165k-class settlement-provenance exceptions, an A10
class per account) with invented identities and amounts only. Nothing here is
derived from customer data. Pure functions of their arguments: no clock, no
randomness, so two builds are byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "opening_rehearsal"
NS = UUID("00000000-0000-0000-0000-00000000a110")
ACCOUNT_NS = UUID("00000000-0000-0000-0000-00000000a111")
FULL_PARTITIONS = 15_293
FULL_INVOICES = 120_670
_TS = "2026-10-18T00:00:00+00:00"

# Full-run proportions (counts at FULL_INVOICES); scaled down for small builds.
_OPEN, _OPEN_PARTITIONS = 598, 188
_DRAFT, _VOID, _PROFORMA, _NOFIN = 2_788, 1_401, 3_206, 41
_TABLES = (
    ("payments", "payments"),
    ("payment_settlements", "payment_settlements"),
    ("payment_provider_events", "provider_events"),
    ("integration_inbox", "inbox_receipts"),
    ("payment_refunds", "refunds"),
    ("payment_reversals", "reversals"),
    ("payment_proofs", "proofs"),
    ("payment_proof_corrections", "proof_corrections"),
    ("topup_intents", "topups"),
)

PRECEDENCE = [
    "draft_allocation",
    "a10_missing",
    "a10_opening_not_exact",
    "a10_post_cutover_drift",
    "census_blocking_exception",
    "status_residual_conflict",
    "lane_control_mismatch",
    "v1_vat_evidence_missing",
    "original_evidence_missing",
    "reseller_mapping_unavailable",
]


def _id(kind: str, index: int) -> str:
    return str(uuid5(NS, f"{kind}:{index}"))


def account_uuid(index: int) -> str:
    return str(uuid5(ACCOUNT_NS, f"account:{index}"))


def rules_document() -> dict[str, object]:
    return {
        "schema": "billing.opening.admissibility-rules.v1",
        "rules_version": "scale-fixture-rules:1",
        "decision_refs": ["D-2", "M8", "X1", "V1"],
        "a10": {
            "exact_opening_class": "opening_exact",
            "matching_post_cause": "match",
            "draft_allocation_post_causes": ["known:stale_draft_allocation"],
        },
        # M8: provenance gaps on CLOSED history are findings, not blockers.
        "code_policies": [{"code": "settlement_provenance", "policy": "finding_only"}],
        "closed_invoice_statuses": ["paid", "written_off", "void"],
        "open_invoice_statuses": ["issued", "overdue", "partially_paid"],
        "drafts": {"draft_status": "draft"},
        "v1_gate": {
            "require_lines": True,
            "line_tax_snapshot_fields": [
                "tax_rate_code_snapshot",
                "tax_rate_percent_snapshot",
                "tax_rate_snapshot_version",
                "tax_application",
            ],
        },
        "originals": {"require_for_open_documents": False},
        "hold_precedence": PRECEDENCE,
    }


def target_state_document() -> dict[str, object]:
    base = json.loads((FIXTURES / "assembly.json").read_text(encoding="utf-8"))
    scalar = (
        "scope",
        "source_system",
        "recorded_cutoff",
        "source_authority_ref",
        "account_mapping_version",
        "cash_mapping_version",
        "receivable_mapping_version",
        "tax_mapping_version",
        "source_resolver_version",
        "collection_authority",
        "collection_mapping_version",
        "migration_authority_ref",
        "occurred_at",
        "starter_head_sha",
        "sub_exit_code",
        "binding",
        "accepted_manual_holds",
    )
    state: dict[str, object] = {key: base[key] for key in scalar}
    state.update(
        schema="billing.opening.rehearsal.target-state.v1",
        account_id_derivation={
            "method": "uuid5",
            "namespace": "00000000-0000-0000-0000-00000000a112",
            "name_format": "scale-target|{source_account_id}|{currency}",
        },
        idempotency_key_format="scale-key:{source_account_id}:{currency}",
        pristine_positions={
            "NGN": {
                "expected_position_fingerprint": "scale-pristine-position",
                "posting_watermark": "scale-posting:0",
            }
        },
        partition_targets=[],
    )
    return state


def build_scale_inputs(
    partitions: int = FULL_PARTITIONS, invoices: int = FULL_INVOICES
) -> dict[str, object]:
    """(census, a10, rules, target_state, expected) for the requested scale."""
    if partitions < 4 or invoices < partitions:
        raise ValueError(
            "scale fixture needs partitions >= 4 and invoices >= partitions"
        )
    ratio = invoices / FULL_INVOICES
    n_open = max(4, round(_OPEN * ratio))
    n_open_parts = min(
        max(2, round(_OPEN_PARTITIONS * partitions / FULL_PARTITIONS)), n_open
    )
    n_draft = max(1, round(_DRAFT * ratio))
    n_void = max(1, round(_VOID * ratio))
    n_proforma = max(1, round(_PROFORMA * ratio))
    n_nofin = max(1, round(_NOFIN * ratio))
    stride = max(1, partitions // n_open_parts)
    open_partitions = [min(i * stride, partitions - 1) for i in range(n_open_parts)]
    open_partitions = sorted(set(open_partitions))

    accounts = [account_uuid(i) for i in range(partitions)]
    base, extra = divmod(invoices, partitions)
    rows: list[dict[str, object]] = []
    settlements: list[dict[str, object]] = []
    payments: list[dict[str, object]] = []
    exclusions: list[dict[str, object]] = []
    exceptions: list[dict[str, object]] = []
    provenance_gaps: list[tuple[int, str, str]] = []
    lines: list[dict[str, object]] = []
    unavailable: list[dict[str, object]] = []
    lane_receivable = [0] * partitions
    control_totals = [
        [0, 0, 0] for _ in range(partitions)
    ]  # total, discharge, residual
    invoice_counts = [0] * partitions
    excluded_counts = [0] * partitions
    # status plan: how many of each special status remain to place
    pending = {
        "draft": n_draft,
        "void": n_void,
        "proforma": n_proforma,
        "nofin": n_nofin,
    }
    open_left = n_open
    open_slot = {p: 0 for p in open_partitions}
    clean_open_partitions = set(
        open_partitions[: max(1, (len(open_partitions) * 85) // 100)]
    )

    n = 0
    first_invoice = [0] * partitions
    open_by_partition: dict[int, list[str]] = {}
    for p in range(partitions):
        count = base + (1 if p < extra else 0)
        first_invoice[p] = n + 1
        for j in range(count):
            n += 1
            invoice_id = _id("invoice", n)
            account = accounts[p]
            total, discharge = 10_000, 10_000  # minor units
            status, proforma, active = "paid", False, True
            exclusion = "fully_closed"
            if p in open_slot and open_left > 0 and j < 4:
                status = ("issued", "overdue", "partially_paid")[n % 3]
                discharge = 4_000 if status == "partially_paid" else 0
                exclusion = ""
                open_left -= 1
            elif pending["draft"] > 0 and n % 40 == 7:
                pending["draft"] -= 1
                status, discharge, exclusion = "draft", 0, "draft"
            elif pending["void"] > 0 and n % 86 == 11:
                pending["void"] -= 1
                status, discharge, exclusion = "void", 0, "void"
            elif pending["proforma"] > 0 and n % 37 == 5:
                pending["proforma"] -= 1
                status, proforma, discharge = "issued", True, 0
                exclusion = "inactive_or_proforma"
            elif pending["nofin"] > 0 and n % 2900 == 13:
                pending["nofin"] -= 1
                total, discharge, exclusion = 0, 0, "no_financial_activity"
            residual = total - discharge
            settled = discharge > 0
            settlement_ids: list[str] = []
            if settled:
                settlement_id = _id("settlement", n)
                payment_id = _id("payment", n)
                settlement_ids.append(settlement_id)
                settlements.append(
                    {
                        "active": True,
                        "amount": _money(discharge),
                        "consumption_ledger_entry_id": None,
                        "created_at": _TS,
                        "economic_at": None,
                        "evidence_fingerprint": None,
                        "id": settlement_id,
                        "invoice_id": invoice_id,
                        "kind": "payment",
                        "ledger_entry_id": None,
                        "recorded_account_id": None,
                        "recorded_currency": None,
                        "source_id": payment_id,
                    }
                )
                payments.append(
                    {
                        "id": payment_id,
                        "account_id": account,
                        "amount": _money(discharge),
                        "currency": "NGN",
                        "status": "succeeded",
                        "is_active": True,
                        "created_at": _TS,
                        "paid_at": _TS,
                    }
                )
                if status == "paid":  # provenance gaps sit on closed invoices
                    provenance_gaps.append((p, account, settlement_id))
            rows.append(
                {
                    "account_id": account,
                    "active": active,
                    "canonical_discharge": _money(discharge),
                    "collections_due_basis_verified": False,
                    "created_at": _TS,
                    "credits_applied": "0.00",
                    "currency": "NGN",
                    "due_at": _TS,
                    "due_basis": None,
                    "due_basis_ref": None,
                    "due_policy_version": None,
                    "invoice_id": invoice_id,
                    "invoice_number": f"SYN-{n:07d}",
                    "issued_at": _TS,
                    "line_ids": [],
                    "opening_funding_applied": "0.00",
                    "payments_applied": _money(discharge),
                    "precision": 2,
                    "proforma": proforma,
                    "residual": _money(residual),
                    "settlement_ids": settlement_ids,
                    "status": status,
                    "stored_residual": _money(residual),
                    "total": _money(total),
                    "updated_at": _TS,
                }
            )
            invoice_counts[p] += 1
            control_totals[p][0] += total
            control_totals[p][1] += discharge
            control_totals[p][2] += residual
            if exclusion:
                exclusions.append({"source_id": invoice_id, "reason": exclusion})
                excluded_counts[p] += 1
            else:
                lane_receivable[p] += residual
                open_by_partition.setdefault(p, []).append(invoice_id)
                lines.append(
                    {
                        "line_id": _id("line", n),
                        "invoice_id": invoice_id,
                        "description": "synthetic line",
                        "tax_rate_id": None,
                        "tax_rate_snapshot_version": "synthetic:1",
                        "tax_rate_code_snapshot": "SYN",
                        "tax_rate_percent_snapshot": "7.50",
                        "tax_rate_is_active_snapshot": None,
                        "tax_application": "exclusive",
                    }
                )
                unavailable.append(
                    {
                        "invoice_id": invoice_id,
                        "requirements": ["documentary_digest", "seller"],
                        "reasons": ["no_authenticated_frozen_document"],
                    }
                )
    # a handful of the open partitions carry residual_mismatch (stay held)
    mismatch_partitions = [p for p in open_partitions if p not in clean_open_partitions]
    for p in mismatch_partitions:
        exceptions.append(
            {
                "account_id": accounts[p],
                "code": "residual_mismatch",
                "currency": "NGN",
                "detail": "synthetic residual mismatch",
                "source_id": _id("invoice", first_invoice[p]),
            }
        )

    # A10 classes: ~91% exact+match; the remainder cycle through the other
    # classes; every 157th account is a draft-allocation account.
    a10_accounts: list[dict[str, str]] = []
    for p in range(partitions):
        if p % 157 == 3:
            klass, cause = "opening_exact", "known:stale_draft_allocation"
        elif p % 11 == 5:
            klass, cause = "opening_exact", "known:pre_s1_payment"
        elif p % 53 == 9:
            klass, cause = "opening_gap_other", "match"
        else:
            klass, cause = "opening_exact", "match"
        a10_accounts.append(
            {"account_id": accounts[p], "opening_class": klass, "post_cause": cause}
        )
    for k in range(32):  # A10 accounts the census does not know (ignored)
        a10_accounts.append(
            {
                "account_id": account_uuid(partitions + k),
                "opening_class": "opening_exact",
                "post_cause": "match",
            }
        )
    clean_admitted = {
        p
        for p in clean_open_partitions
        if a10_accounts[p]["opening_class"] == "opening_exact"
        and a10_accounts[p]["post_cause"] == "match"
    }
    eligible_expected = sum(len(open_by_partition.get(p, ())) for p in clean_admitted)
    # Provenance gaps sit on closed history in every partition, admitted ones
    # included (M8 finding_only); the admitted population stays as designed.
    for _p, account, settlement_id in provenance_gaps:
        exceptions.append(
            {
                "account_id": account,
                "code": "settlement_provenance",
                "currency": "NGN",
                "detail": "synthetic provenance gap",
                "source_id": settlement_id,
            }
        )

    lanes = [
        {
            "account_id": accounts[p],
            "adjustment_total": "0.00",
            "authority": "authoritative",
            "authority_cutover_id": _id("cutover", 0),
            "collectible_receivable": _money(lane_receivable[p]),
            "currency": "NGN",
            "fingerprint": f"lane-fp:{p}",
            "posting_group_count": 1,
            "prepaid_funding_consumed": "0.00",
            "prepaid_funding_reserved": "0.00",
            "refunded_total": "0.00",
            "unapplied_customer_credit": "0.00",
            "written_off_total": "0.00",
        }
        for p in range(partitions)
    ]
    controls = [
        {
            "account_id": accounts[p],
            "canonical_discharge": _money(control_totals[p][1]),
            "currency": "NGN",
            "eligible_count": 0,
            "eligible_residual": "0.00",
            "exception_count": 0,
            "excluded_count": excluded_counts[p],
            "invoice_count": invoice_counts[p],
            "original_total": _money(control_totals[p][0]),
            "residual": _money(control_totals[p][2]),
            "source_count": 0,
        }
        for p in range(partitions)
    ]
    census = {
        "blocking_exceptions": exceptions,
        "capture": {
            "captured_at": _TS,
            "declared_source": {
                "currency_precisions": [["NGN", 2]],
                "economic_cutoff": _TS,
                "installation_id": _id("installation", 0),
                "population_version": "sub-billing-opening-census-v3",
                "source_repository_sha": "synthetic",
            },
            "isolation": "repeatable read",
            "mutable_history_reconstructed": False,
            "population_complete": True,
            "schema_heads": ["synthetic-head:1"],
            "snapshot_id": "synthetic-snapshot",
        },
        "cash_evidence": {
            "blocking_exceptions": [],
            **{field: [] for _t, field in _TABLES if field != "payments"},
            "payments": payments,
            "qualified_payment_ids": [],
            "source_counts": [
                {
                    "table": table,
                    "count": len(payments) if table == "payments" else 0,
                }
                for table, _f in _TABLES
            ],
        },
        "content_fingerprint": "synthetic-census-fp",
        "controls": controls,
        "documentary_observations": {
            "current_invoices": [],
            "current_lines": lines,
            "unavailable_original_evidence": unavailable,
        },
        "effects": [],
        "eligible_invoice_ids": [],  # the census all-or-nothing rule: 0 (F1)
        "exclusions": exclusions,
        "financial_sources": [],
        "invoices": rows,
        "lanes": lanes,
        "lines": [],
        "settlements": settlements,
        "subscriber_ids": [],
    }
    return {
        "census": census,
        "a10": {
            "schema": "billing.opening.a10-account-classes.v1",
            "accounts": a10_accounts,
        },
        "rules": rules_document(),
        "target_state": target_state_document(),
        "expected": {
            "partitions": partitions,
            "invoices": n,
            "eligible_invoices": eligible_expected,
            "open_candidates": n_open - open_left,
            "draft": n_draft - pending["draft"],
        },
    }


def _money(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"
