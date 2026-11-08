"""Pure closure checks for the opening variance ledger (design 2.1, 2.2, 3.3).

Every equation is exact integer arithmetic with no tolerance and no netting
across lanes, bases, partitions or signs:

* E1 per lane: ``ledger_lane = T_lane + sum V[ledger_lane, lane]``
* E2: ``document_residual = T_receivable + sum V[document_residual]``
* E3: ``stored_balance = T_receivable + sum V[stored_balance]``
* E4: ``rebuild = T_net + sum V[rebuild]`` (skipped, with a typed control gap,
  when no rebuild figure exists)
* count control: every census row lands in exactly one bucket
* batch: the stated source of rows that belong to no partition equals the sum
  of their batch-scope variances

``T`` is what the import actually transfers; it is never adjusted. The credit
and funding lanes transfer nothing until Gap C (ST-4b), so their ``T`` is 0
here and a non-zero source figure must be carried as variance.

Open definition, recorded not invented: ``T_net`` for E4 is
``T_receivable - T_credit - T_funding``; the rebuild's own definition
(``rebuild.py`` in Sub) is not readable from this repository.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import fields

from dotmac_billing.opening_contracts import (
    OpeningPartitionV1,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_variance_contracts import (
    AGGREGATE_KINDS,
    LEDGER_BASIS,
    REMAINDER_KIND,
    UNEXPLAINED,
)

Block = Callable[[OpeningPartitionV1 | None, str, str], None]

ARCHIVED_REASON = "archived_draft"
#: Cumulative flow fields stay history, not balances (fail closed: must be 0).
_HISTORY_FIELDS = frozenset(
    {
        "available_non_cash_credit_minor",
        "prepaid_funding_consumed_minor",
        "written_off_total_minor",
        "refunded_total_minor",
        "adjustment_total_minor",
    }
)
_LANE_FIELD = {
    "receivable": "collectible_receivable_minor",
    "customer_credit": "unapplied_customer_credit_minor",
    "prepaid_funding": "prepaid_funding_reserved_minor",
}
#: Lanes whose source figure is governed by E1 instead of "must be zero".
VARIANCE_GOVERNED_LANE_FIELDS = frozenset(
    {
        "collectible_receivable_minor",
        "open_ar_minor",
        "unapplied_customer_credit_minor",
        "prepaid_funding_reserved_minor",
    }
)


def variance_citations(manifest: OpeningManifestV2) -> frozenset[str]:
    """Keys of the source rows cited by any variance (for carried-residue checks)."""
    return frozenset(
        v.source.key for v in manifest.variances if v.source.kind not in AGGREGATE_KINDS
    )


def check_variances(
    manifest: OpeningManifestV2,
    ar_totals: Counter[OpeningPartitionV1],
    block: Block,
) -> None:
    controls = {c.partition: c for c in manifest.controls}
    partitions = set(controls)

    # ---- measures and holds are one row per control partition -------------
    measures: dict[OpeningPartitionV1, object] = {}
    for m in manifest.measures:
        if m.partition not in partitions:
            block(
                m.partition,
                "measures_partition_unknown",
                m.partition.source_account_key,
            )
        elif m.partition in measures:
            block(m.partition, "measures_duplicate", m.partition.source_account_key)
        measures[m.partition] = m
    for part in partitions:
        if part not in measures:
            block(part, "partition_measures_missing", part.source_account_key)
    held: set[OpeningPartitionV1] = set()
    for hold in manifest.holds:
        if hold.partition not in partitions:
            block(
                hold.partition,
                "partition_hold_unknown",
                hold.partition.source_account_key,
            )
        elif hold.partition in held:
            block(
                hold.partition,
                "partition_hold_duplicate",
                hold.partition.source_account_key,
            )
        held.add(hold.partition)

    # ---- variance structure ----------------------------------------------
    population = set(manifest.population)
    disposition = {d.identity: d for d in manifest.dispositions}
    batch_rows: set[OpeningSourceIdentityV1] = set()
    batch_keys: Counter[tuple[str, str, str]] = Counter()
    for control in manifest.batch_controls:
        batch_rows.update(control.rows)
        batch_keys[(control.currency, control.lane, control.basis)] += 1
        if (control.basis != LEDGER_BASIS and control.lane != "receivable") or any(
            r not in population for r in control.rows
        ):
            block(None, "batch_control_invalid", f"{control.lane}:{control.basis}")
    for key, count in batch_keys.items():
        if count != 1:
            block(None, "batch_control_duplicate", ":".join(key))

    identities = Counter(v.identity for v in manifest.variances)
    remainders: Counter[tuple[OpeningPartitionV1, str, str]] = Counter()
    sums: dict[tuple[OpeningPartitionV1 | None, str, str], int] = defaultdict(int)
    by_partition: dict[OpeningPartitionV1, list[OpeningSourceIdentityV1]] = defaultdict(
        list
    )
    batch_sums: dict[tuple[str, str, str], int] = defaultdict(int)
    for v in manifest.variances:
        part = v.partition
        ref = f"{v.reason_code}:{v.source.kind}:{v.source.key}"
        if identities[v.identity] != 1:
            block(part, "variance_duplicate", ref)
        if part is not None and part not in partitions:
            block(part, "variance_partition_unknown", ref)
            continue
        resolved = True
        if v.source.kind in AGGREGATE_KINDS:
            if part is None:
                resolved = v.source in batch_rows
            else:
                resolved = v.source.key == part.source_account_key
                if v.source.kind == "rebuild_account":
                    m = measures.get(part)
                    resolved = (
                        resolved and getattr(m, "rebuild_minor", None) is not None
                    )
        else:
            resolved = v.source in population
            row = disposition.get(v.source)
            if resolved and part is not None and row is not None:
                resolved = row.partition == part
        if not resolved:
            block(part, "variance_source_unresolved", ref)
        if v.reason_code == UNEXPLAINED:
            if part is not None:
                remainders[(part, v.lane, v.basis)] += 1
        if part is None:
            batch_sums[(v.currency, v.lane, v.basis)] += v.amount_minor
        else:
            sums[(part, v.lane, v.basis)] += v.amount_minor
            by_partition[part].append(v.source)
    for (part, lane, basis), count in remainders.items():
        if count > 1:
            block(
                part,
                "variance_remainder_unbounded",
                f"{part.source_account_key}:{lane}:{basis}",
            )
    for key, total in batch_sums.items():
        stated = next(
            (
                c.source_minor
                for c in manifest.batch_controls
                if (c.currency, c.lane, c.basis) == key
            ),
            None,
        )
        if stated != total:
            block(None, "batch_control_unclosed", ":".join(key))
    for c in manifest.batch_controls:
        if (c.currency, c.lane, c.basis) not in batch_sums and c.source_minor != 0:
            block(None, "batch_control_unclosed", f"{c.lane}:{c.basis}")

    # ---- E1 per lane; flow fields stay history (zero) -----------------------
    for part, control in controls.items():
        key = part.source_account_key
        transferred = {
            "receivable": ar_totals[part],
            "customer_credit": 0,
            "prepaid_funding": 0,
        }
        for lane, field_name in _LANE_FIELD.items():
            carried = sums.get((part, lane, LEDGER_BASIS), 0)
            if getattr(control, field_name) != transferred[lane] + carried:
                block(part, "lane_control_unclosed", f"{key}:{lane}")
        if control.open_ar_minor != control.collectible_receivable_minor:
            block(part, "receivable_control_mismatch", key)
        for field in fields(control):
            if field.name in _HISTORY_FIELDS and getattr(control, field.name) != 0:
                block(part, "unsupported_lane", f"{key}:{field.name}")

        m = measures.get(part)
        if m is None:
            continue
        t = ar_totals[part]
        memo = {
            b: sum(v for (p, _l, bb), v in sums.items() if p == part and bb == b)
            for b in ("document_residual", "stored_balance", "rebuild")
        }
        if m.document_residual_minor != t + memo["document_residual"]:  # type: ignore[attr-defined]
            block(part, "document_control_unclosed", key)
        if m.stored_balance_minor != t + memo["stored_balance"]:  # type: ignore[attr-defined]
            block(part, "stored_balance_control_unclosed", key)
        if m.rebuild_minor is None:  # type: ignore[attr-defined]
            if memo["rebuild"] != 0 or any(
                p == part and bb == "rebuild" for (p, _l, bb) in sums
            ):
                block(part, "variance_rebuild_gap", key)
        elif m.rebuild_minor != t + memo["rebuild"]:  # type: ignore[attr-defined]
            block(part, "rebuild_control_unclosed", key)

        # ---- count control: derived buckets must equal the stated ones -----
        cited = set(by_partition.get(part, ()))
        rows = [d for d in manifest.dispositions if d.partition == part]
        derived = Counter()
        for d in rows:
            if d.disposition == "eligible":
                derived["transferred_rows"] += 1
            elif d.disposition == "blocking":
                derived["non_carryable_rows"] += 1
            elif d.reason == ARCHIVED_REASON:
                derived["archived_rows"] += 1
            elif d.identity in cited:
                derived["carried_rows"] += 1
            else:
                derived["excluded_rows"] += 1
        derived["population_rows"] = len(rows)
        for name in (
            "population_rows",
            "transferred_rows",
            "carried_rows",
            "excluded_rows",
            "archived_rows",
            "non_carryable_rows",
        ):
            if getattr(m, name) != derived[name]:
                block(part, "partition_count_mismatch", f"{key}:{name}")


def carryable_accounts(manifest: OpeningManifestV2) -> frozenset[object]:
    """The one carryable definition: ``T != 0`` or any variance on the partition."""
    totals: Counter[object] = Counter()
    for item in (*manifest.cash_lots, *manifest.receivables):
        totals[item.partition.account_id] += item.residual_minor
    accounts = {a for a, total in totals.items() if total != 0}
    accounts.update(
        v.partition.account_id for v in manifest.variances if v.partition is not None
    )
    return frozenset(accounts)


__all__ = [
    "ARCHIVED_REASON",
    "REMAINDER_KIND",
    "VARIANCE_GOVERNED_LANE_FIELDS",
    "carryable_accounts",
    "check_variances",
    "variance_citations",
]
