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

``T_net`` for E4 is ``T_receivable - sum(lot residuals)`` (the credit imported as
cash lots; ST-4b adds credit/funding T). The rebuild is a net, credit-positive
figure, NGN only; the drafter emits ``rebuild_minor = -rebuild`` (receivable-
positive). A partition with cash lots keeps its credit under the cash control:
a non-zero unapplied-credit or funding figure, or a credit/funding variance, is
refused (``credit_transfer_unsupported``) so the same credit is never carried
twice. Cumulative flow fields are history rows with no equation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Final

from dotmac_billing.opening_contracts import (
    OpeningDispositionV1,
    OpeningPartitionV1,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_variance_contracts import (
    AGGREGATE_KINDS,
    HISTORY_FIELDS,
    LEDGER_BASIS,
    REMAINDER_KIND,
    UNEXPLAINED,
    OpeningPartitionMeasuresV1,
)

Block = Callable[[OpeningPartitionV1 | None, str, str], None]

ARCHIVED_REASON = "archived_draft"
#: Cumulative flow fields are history, not balances (Q4 ruling 2026-11-08): no
#: equation, no blocker, no variance. They stay in the fingerprinted controls
#: and appear in the closure report as history rows.
#: Still must be zero: the adapter always emits 0 and nothing transfers it.
_MUST_BE_ZERO = ("available_non_cash_credit_minor",)
_LANE_FIELD = {
    "receivable": "collectible_receivable_minor",
    "customer_credit": "unapplied_customer_credit_minor",
    "prepaid_funding": "prepaid_funding_reserved_minor",
}
#: Lanes whose source figure is governed by E1 (or is history) instead of "must
#: be zero". ``available_cash_credit_minor`` keeps its own cash control.
VARIANCE_GOVERNED_LANE_FIELDS = frozenset(
    {
        "collectible_receivable_minor",
        "open_ar_minor",
        "unapplied_customer_credit_minor",
        "prepaid_funding_reserved_minor",
        *HISTORY_FIELDS,
    }
)
ITEMISED_KINDS: Final = frozenset(
    {"invoice", "settlement", "ledger_entry", "credit_note", "payment"}
)
PARTITION_AGGREGATE_KINDS: Final = frozenset(
    {"lane", "rebuild_account", REMAINDER_KIND}
)


def batch_control_key(currency: str, lane: str, basis: str) -> str:
    return f"{currency}:{lane}:{basis}"


def variance_citations(manifest: OpeningManifestV2) -> frozenset[str]:
    """Keys of the source rows cited by any variance (for carried-residue checks)."""
    return frozenset(
        v.source.key for v in manifest.variances if v.source.kind not in AGGREGATE_KINDS
    )


def lot_residuals(manifest: OpeningManifestV2) -> Counter[OpeningPartitionV1]:
    """Sum of cash-lot residuals per partition (the credit imported as lots)."""
    totals: Counter[OpeningPartitionV1] = Counter()
    for lot in manifest.cash_lots:
        totals[lot.partition] += lot.residual_minor
    return totals


def check_variances(
    manifest: OpeningManifestV2,
    ar_totals: Counter[OpeningPartitionV1],
    block: Block,
) -> None:
    """Every closure, count, citation, duplicate and batch finding is manifest-wide
    (Finding 5): a control partition cannot leave the population silently."""

    def mw(code: str, reference: str) -> None:
        block(None, code, reference)

    controls = {c.partition: c for c in manifest.controls}
    partitions = set(controls)
    lots_residual = lot_residuals(manifest)
    lot_partitions = {lot.partition for lot in manifest.cash_lots}

    # ---- measures are one row per control partition -----------------------
    measures: dict[OpeningPartitionV1, OpeningPartitionMeasuresV1] = {}
    for row_m in manifest.measures:
        if row_m.partition not in partitions:
            mw("measures_partition_unknown", row_m.partition.source_account_key)
        elif row_m.partition in measures:
            mw("measures_duplicate", row_m.partition.source_account_key)
        measures[row_m.partition] = row_m
    for unmeasured in sorted(partitions, key=lambda p: p.source_account_key):
        if unmeasured not in measures:
            mw("partition_measures_missing", unmeasured.source_account_key)
    for d in manifest.dispositions:
        if d.disposition == "blocking" or d.reason.startswith("held:"):
            mw("manifest_noncarryable_row", d.identity.key)
    for mpart, mrow in measures.items():
        if mrow.non_carryable_rows != 0:
            mw("manifest_noncarryable_row", mpart.source_account_key)

    # ---- cash lots: the cash control governs a lot partition's credit -------
    # (ST-4b replaces this rule with its credit/funding transfer equation.)
    for lot_part in sorted(lot_partitions, key=lambda p: p.source_account_key):
        lot_control = controls.get(lot_part)
        if lot_control is None:
            continue
        for field_name in (
            "unapplied_customer_credit_minor",
            "prepaid_funding_reserved_minor",
        ):
            if getattr(lot_control, field_name) != 0:
                mw(
                    "credit_transfer_unsupported",
                    f"{lot_part.source_account_key}:{field_name}",
                )
    for v in manifest.variances:
        if v.partition in lot_partitions and v.lane in (
            "customer_credit",
            "prepaid_funding",
        ):
            mw(
                "credit_transfer_unsupported",
                f"{v.reason_code}:{v.source.kind}:{v.source.key}",
            )

    # ---- batch controls -----------------------------------------------------
    population = set(manifest.population)
    disposition = {d.identity: d for d in manifest.dispositions}
    batch_keys: Counter[tuple[str, str, str]] = Counter()
    payment_controls: dict[OpeningSourceIdentityV1, list[tuple[str, str, str]]] = (
        defaultdict(list)
    )
    for bc in manifest.batch_controls:
        bkey = (bc.currency, bc.lane, bc.basis)
        batch_keys[bkey] += 1
        for batch_row in bc.rows:
            payment_controls[batch_row].append(bkey)
        if (bc.basis != LEDGER_BASIS and bc.lane != "receivable") or any(
            r not in population for r in bc.rows
        ):
            mw("batch_control_invalid", f"{bc.lane}:{bc.basis}")
    for ckey, count in batch_keys.items():
        if count != 1:
            mw("batch_control_duplicate", ":".join(ckey))

    # ---- variance structure -------------------------------------------------
    identities = Counter(v.identity for v in manifest.variances)
    remainders: Counter[tuple[OpeningPartitionV1, str, str]] = Counter()
    batch_remainders: Counter[tuple[str, str, str]] = Counter()
    sums: dict[tuple[OpeningPartitionV1, str, str], int] = defaultdict(int)
    by_partition: dict[OpeningPartitionV1, list[OpeningSourceIdentityV1]] = defaultdict(
        list
    )
    batch_sums: dict[tuple[str, str, str], int] = defaultdict(int)
    for v in manifest.variances:
        part = v.partition
        ref = f"{v.reason_code}:{v.source.kind}:{v.source.key}"
        if identities[v.identity] != 1:
            mw("variance_duplicate", ref)
        if part is not None and part not in partitions:
            mw("variance_partition_unknown", ref)
            continue
        kind = v.source.kind
        resolved: bool
        if part is None:
            ckey = (v.currency, v.lane, v.basis)
            if kind == "payment":
                resolved = payment_controls.get(v.source) == [ckey]
            elif kind == "control_row":
                resolved = (
                    v.source.key == batch_control_key(*ckey)
                    and batch_keys.get(ckey) == 1
                )
                if resolved:
                    batch_remainders[ckey] += 1
            else:
                resolved = False
        elif kind in PARTITION_AGGREGATE_KINDS:
            resolved = v.source.key == part.source_account_key
            if kind == "rebuild_account":
                m = measures.get(part)
                resolved = resolved and m is not None and m.rebuild_minor is not None
        elif kind in ITEMISED_KINDS:
            resolved = v.source in population
            row = disposition.get(v.source)
            if resolved and row is not None:
                resolved = row.partition == part
        else:
            resolved = False
        if not resolved:
            mw("variance_source_unresolved", ref)
        if v.reason_code == UNEXPLAINED and part is not None:
            remainders[(part, v.lane, v.basis)] += 1
        if part is None:
            batch_sums[(v.currency, v.lane, v.basis)] += v.amount_minor
        else:
            sums[(part, v.lane, v.basis)] += v.amount_minor
            by_partition[part].append(v.source)
    for (part, lane, basis), count in remainders.items():
        if count > 1:
            mw(
                "variance_remainder_unbounded",
                f"{part.source_account_key}:{lane}:{basis}",
            )
    for ckey, count in batch_remainders.items():
        if count > 1:
            mw("variance_remainder_unbounded", f"batch:{':'.join(ckey)}")
    for ckey, total in batch_sums.items():
        stated = next(
            (
                c.source_minor
                for c in manifest.batch_controls
                if (c.currency, c.lane, c.basis) == ckey
            ),
            None,
        )
        if stated != total:
            mw("batch_control_unclosed", ":".join(ckey))
    for c in manifest.batch_controls:
        if (c.currency, c.lane, c.basis) not in batch_sums and c.source_minor != 0:
            mw("batch_control_unclosed", f"{c.currency}:{c.lane}:{c.basis}")

    # one pass each, so the per-partition work below is a dictionary read
    memo_sums: dict[tuple[OpeningPartitionV1, str], int] = defaultdict(int)
    for (sum_part, _lane, sum_basis), amount in sums.items():
        if sum_basis != LEDGER_BASIS:
            memo_sums[(sum_part, sum_basis)] += amount
    rows_of: dict[OpeningPartitionV1, list[OpeningDispositionV1]] = defaultdict(list)
    for disposition_row in manifest.dispositions:
        rows_of[disposition_row.partition].append(disposition_row)

    # ---- E1 per lane; history is not an equation ----------------------------
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
                mw("lane_control_unclosed", f"{key}:{lane}")
        if control.open_ar_minor != control.collectible_receivable_minor:
            mw("receivable_control_mismatch", key)
        for name in _MUST_BE_ZERO:
            if getattr(control, name) != 0:
                mw("unsupported_lane", f"{key}:{name}")

        m = measures.get(part)
        if m is None:
            continue
        t = ar_totals[part]
        t_net = t - lots_residual[part]
        memo = {
            b: memo_sums.get((part, b), 0)
            for b in ("document_residual", "stored_balance", "rebuild")
        }
        if m.document_residual_minor != t + memo["document_residual"]:
            mw("document_control_unclosed", key)
        if m.stored_balance_minor != t + memo["stored_balance"]:
            mw("stored_balance_control_unclosed", key)
        if m.rebuild_minor is None:
            if (part, "rebuild") in memo_sums:
                mw("variance_rebuild_gap", key)
        elif m.rebuild_minor != t_net + memo["rebuild"]:
            mw("rebuild_control_unclosed", key)

        # ---- count control: derived buckets must equal the stated ones -----
        cited = set(by_partition.get(part, ()))
        rows = rows_of.get(part, [])
        derived: Counter[str] = Counter()
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
                mw("partition_count_mismatch", f"{key}:{name}")


def carryable_accounts(manifest: OpeningManifestV2) -> frozenset[object]:
    """The one carryable definition: ``T != 0`` or any variance on the partition."""
    totals: Counter[object] = Counter()
    for lot in manifest.cash_lots:
        totals[lot.partition.account_id] += lot.residual_minor
    for receivable in manifest.receivables:
        totals[receivable.partition.account_id] += receivable.residual_minor
    accounts = {a for a, total in totals.items() if total != 0}
    accounts.update(
        v.partition.account_id for v in manifest.variances if v.partition is not None
    )
    return frozenset(accounts)


__all__ = [
    "ARCHIVED_REASON",
    "HISTORY_FIELDS",
    "ITEMISED_KINDS",
    "REMAINDER_KIND",
    "VARIANCE_GOVERNED_LANE_FIELDS",
    "batch_control_key",
    "carryable_accounts",
    "check_variances",
    "lot_residuals",
    "variance_citations",
]
