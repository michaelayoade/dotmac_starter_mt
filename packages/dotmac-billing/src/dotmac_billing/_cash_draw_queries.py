"""Private persisted-scalar proof for Billing's current pooled cash draw query."""

from __future__ import annotations

import re
from decimal import Decimal, DecimalException, localcontext
from typing import Any, NoReturn
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Money
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dotmac_billing._document_facts import _decode
from dotmac_billing._opening_values import cash_decimal_context, minor_units_of
from dotmac_billing._receivable_backing import resolve_receivable_backing
from dotmac_billing.contracts import (
    AccountingFactV1,
    CashCreditDrawState,
    CurrentCashCreditDrawV1,
    GetCurrentCashCreditDrawV1,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import (
    _scoped,
    _table,
    get_accounting_fact_v4,
    get_opening_cash_effect,
    get_opening_cash_lot_position,
)


def observe_cash_credit_draw(
    db: Session, *, scope: Scope, request: GetCurrentCashCreditDrawV1
) -> CurrentCashCreditDrawV1:
    if type(request) is not GetCurrentCashCreditDrawV1:
        raise BillingRuleViolation(
            "cash_draw_request_invalid", "unsupported draw request"
        )
    request.__post_init__()
    if not isinstance(scope, TenantScope | PlatformScope) or scope != request.scope:
        raise BillingRuleViolation("scope_mismatch", "draw scope disagrees")

    def refuse(message: str) -> NoReturn:
        raise BillingRuleViolation("cash_draw_observation_unavailable", message)

    def rows(name: str, **filters: object) -> list[Any]:
        table = _table(scope, name)
        statement = select(table)
        for key, value in filters.items():
            statement = statement.where(table.c[key] == value)
        return list(db.execute(_scoped(statement, scope, table)).mappings())

    def one(name: str, identity: UUID) -> Any:
        result = rows(name, id=identity)
        if len(result) != 1:
            refuse("exact draw evidence was not found")
        return result[0]

    currency = request.amount.currency

    def envelope(row: Any) -> None:
        if (row["billing_account_id"], row["currency"], row["minor_units"]) != (
            request.billing_account_id,
            currency.code,
            currency.minor_units,
        ):
            refuse("draw account or currency envelope disagrees")

    def quantity(raw: Decimal) -> Decimal:
        if not isinstance(raw, Decimal) or not raw.is_finite():
            refuse("stored draw quantity is invalid")
        # Reject fractional minor units rather than normalizing persisted money.
        money = Money(raw, currency)
        if money.amount != raw:
            refuse("stored draw quantity requires rounding")
        minor_units_of(money)
        return raw

    checked_groups: set[UUID] = set()

    def movement_group(group_id: UUID) -> Any:
        group = one("posting_groups", group_id)
        envelope(group)
        if group_id in checked_groups:
            return group
        checked_groups.add(group_id)
        allocations = rows("allocation_effects", posting_group_id=group_id)
        openings = rows("opening_cash_effects", posting_group_id=group_id)
        if not allocations and not openings:
            refuse("draw movement group has no source membership")
        net = Decimal(0)
        for item in allocations:
            envelope(item)
            if item["document_id"] != request.document_id:
                refuse("draw movement crosses documents")
            settlement = one("confirmed_settlements", item["settlement_id"])
            envelope(settlement)
            if settlement["funding_lane"] != "available_credit":
                refuse("draw source is not available cash credit")
            net += quantity(item["amount_delta"])
        for item in openings:
            envelope(item)
            effect = get_opening_cash_effect(db, scope=scope, effect_id=item["id"])
            if effect.document_id != request.document_id:
                refuse("opening movement crosses documents")
            net -= quantity(item["amount_delta"])
        effects = rows("posting_effects", posting_group_id=group_id)
        expected = {"receivable": -net, "available_credit": -net}
        if len(effects) != 2 or {item["lane"] for item in effects} != set(expected):
            refuse("draw posting lanes are incomplete or ambiguous")
        for item in effects:
            envelope(item)
            if quantity(item["amount_delta"]) != expected[item["lane"]]:
                refuse("draw posting lanes disagree with exact source quantities")
        if group["group_kind"] in ("opening_cash_applied", "cash_credit_draw_reversed"):
            get_accounting_fact_v4(db, scope=scope, posting_group_id=group_id)
        elif openings:
            refuse("unsupported opening correction group")
        return group

    with db.no_autoflush, localcontext(cash_decimal_context()):
        if (
            db.connection().get_isolation_level() != "READ COMMITTED"
            or db.execute(text("SHOW transaction_read_only")).scalar_one() != "off"
        ):
            refuse("current draw requires a read-write READ COMMITTED transaction")
        account = _table(scope, "billing_accounts")
        locked = (
            db.execute(
                _scoped(
                    select(account)
                    .where(account.c.id == request.billing_account_id)
                    .with_for_update(),
                    scope,
                    account,
                )
            )
            .mappings()
            .one_or_none()
        )
        if locked is None or (locked["currency"], locked["minor_units"]) != (
            currency.code,
            currency.minor_units,
        ):
            refuse("draw account with exact currency was not found")
        group = movement_group(request.posting_group_id)
        if (
            group["group_kind"] not in ("allocation", "opening_cash_applied")
            or group["reverses_group_id"] is not None
            or group["source_ref"] != request.source_ref
        ):
            refuse("posting group is not the exact original cash draw")
        ordinary = rows("allocation_effects", posting_group_id=group["id"])
        opening = rows("opening_cash_effects", posting_group_id=group["id"])
        if bool(opening) != (group["group_kind"] == "opening_cash_applied"):
            refuse("original draw kind disagrees with source membership")
        if len({item["settlement_id"] for item in ordinary}) != len(ordinary):
            refuse("original draw duplicates a confirmed cash source")
        original = Decimal(0)
        for root in ordinary:
            if (
                root["effect_kind"] != "allocation"
                or root["offsets_allocation_id"] is not None
                or quantity(root["amount_delta"]) <= 0
            ):
                refuse("draw requires original positive cash allocations")
            original += root["amount_delta"]
        for root in opening:
            if (
                root["effect_kind"] != "application"
                or root["original_effect_id"] is not None
                or quantity(root["amount_delta"]) >= 0
            ):
                refuse("draw requires original opening cash applications")
            original -= root["amount_delta"]
        if original != request.amount.amount:
            refuse("exact original draw amount disagrees")
        document = one("documents", request.document_id)
        envelope(document)
        backing = resolve_receivable_backing(
            db, scope=scope, document_id=request.document_id
        )
        active = backing.active and not rows(
            "document_events", document_id=request.document_id, event_kind="voided"
        )
        visited: set[tuple[str, UUID]] = set()

        def net(parent: Any, *, is_opening: bool) -> Decimal:
            name = "opening_cash_effects" if is_opening else "allocation_effects"
            edge = "original_effect_id" if is_opening else "offsets_allocation_id"
            key = (name, parent["id"])
            if key in visited or len(visited) >= 128:
                refuse("draw inverse lineage cycles or exceeds supported depth")
            visited.add(key)
            envelope(parent)
            raw = quantity(parent["amount_delta"])
            if raw == 0 or parent["document_id"] != request.document_id:
                refuse("draw inverse quantity or document disagrees")
            children = rows(name, **{edge: parent["id"]})
            reversal_groups = rows(
                "posting_groups", reverses_group_id=parent["posting_group_id"]
            )
            if len(reversal_groups) > 1:
                refuse("draw inverse group branches")
            expected_reversals = {item["id"] for item in reversal_groups}
            actual_reversals: set[UUID] = set()
            total = raw
            for child in children:
                correction = movement_group(child["posting_group_id"])
                child_raw = quantity(child["amount_delta"])
                source_key = "lot_id" if is_opening else "settlement_id"
                if child[source_key] != parent[source_key] or child_raw * raw >= 0:
                    refuse("draw inverse source or sign disagrees")
                if correction["group_kind"] == "cash_credit_draw_reversed":
                    fact = get_accounting_fact_v4(
                        db, scope=scope, posting_group_id=correction["id"]
                    )
                    if (
                        parent["posting_group_id"] != request.posting_group_id
                        or fact.corrects_posting_group_id != request.posting_group_id
                        or child["effect_kind"]
                        != ("application_inverse" if is_opening else "reversal")
                        or abs(child_raw) > abs(raw)
                    ):
                        refuse("mixed inverse lacks exact original draw ancestry")
                elif not is_opening and correction["group_kind"] == "deallocation":
                    if (
                        parent["effect_kind"] != "allocation"
                        or correction["reverses_group_id"] is not None
                        or child["effect_kind"] != "deallocation"
                        or child_raw >= 0
                        or len(rows(name, posting_group_id=correction["id"])) != 1
                    ):
                        refuse("deallocation ancestry is malformed")
                elif not is_opening and correction["group_kind"] == "reversal":
                    if (
                        correction["reverses_group_id"] != parent["posting_group_id"]
                        or child["effect_kind"] != "reversal"
                        or child_raw != -raw
                    ):
                        refuse("generic inverse lacks exact parent group")
                    actual_reversals.add(correction["id"])
                    original_members = rows(
                        name, posting_group_id=parent["posting_group_id"]
                    )
                    inverse_members = rows(name, posting_group_id=correction["id"])
                    if len(original_members) != len(inverse_members) or {
                        item[edge] for item in inverse_members
                    } != {item["id"] for item in original_members}:
                        refuse("inverse group has incomplete original membership")
                else:
                    refuse("unsupported cash draw correction")
                total += net(child, is_opening=is_opening)
            if actual_reversals != expected_reversals:
                refuse("reversal group lacks exact inverse source edges")
            if not min(raw, Decimal(0)) <= total <= max(raw, Decimal(0)):
                refuse("draw inverse exceeds original source quantity")
            return total

        remaining = Decimal(0)
        for root in ordinary:
            source = one("confirmed_settlements", root["settlement_id"])
            movements = rows("allocation_effects", settlement_id=source["id"])
            used = Decimal(0)
            for item in movements:
                envelope(item)
                used += quantity(item["amount_delta"])
                if item["document_id"] == request.document_id:
                    # Validate every competing movement's ancestry too: a broken
                    # edge must not hide an original draw's removal from its tree.
                    current_item = item
                    ancestry: set[UUID] = set()
                    while True:
                        if current_item["id"] in ancestry or len(ancestry) >= 128:
                            refuse("source/document allocation ancestry cycles")
                        ancestry.add(current_item["id"])
                        envelope(current_item)
                        movement = movement_group(current_item["posting_group_id"])
                        parent_id = current_item["offsets_allocation_id"]
                        if parent_id is None:
                            if (
                                current_item["effect_kind"] != "allocation"
                                or quantity(current_item["amount_delta"]) <= 0
                                or movement["group_kind"]
                                not in ("allocation", "opening_cash_applied")
                                or movement["reverses_group_id"] is not None
                            ):
                                refuse("unlinked movement cannot prove original draw")
                            break
                        parent_item = one("allocation_effects", parent_id)
                        envelope(parent_item)
                        if (
                            parent_item["settlement_id"],
                            parent_item["document_id"],
                        ) != (source["id"], request.document_id) or quantity(
                            parent_item["amount_delta"]
                        ) * quantity(current_item["amount_delta"]) >= 0:
                            refuse("allocation ancestry crosses source or document")
                        if movement["group_kind"] == "reversal":
                            if (
                                current_item["effect_kind"] != "reversal"
                                or movement["reverses_group_id"]
                                != parent_item["posting_group_id"]
                                or current_item["amount_delta"]
                                != -parent_item["amount_delta"]
                            ):
                                refuse("allocation inverse parentage disagrees")
                        elif movement["group_kind"] == "deallocation":
                            if (
                                current_item["effect_kind"] != "deallocation"
                                or parent_item["effect_kind"] != "allocation"
                                or current_item["amount_delta"] >= 0
                            ):
                                refuse("deallocation parentage disagrees")
                        elif movement["group_kind"] == "cash_credit_draw_reversed":
                            fact = get_accounting_fact_v4(
                                db, scope=scope, posting_group_id=movement["id"]
                            )
                            if (
                                current_item["effect_kind"] != "reversal"
                                or fact.corrects_posting_group_id
                                != parent_item["posting_group_id"]
                            ):
                                refuse("mixed inverse parentage disagrees")
                        else:
                            refuse("unlinked reallocation or unsupported correction")
                        current_item = parent_item
            if not 0 <= used <= quantity(source["amount"]):
                refuse("source consumption exceeds confirmed cash")
            claims = rows("cash_source_claims", settlement_id=source["id"])
            if len(claims) != 1:
                refuse("confirmed cash source claim is missing or ambiguous")
            envelope(claims[0])
            if claims[0]["opening_cash_lot_id"] is not None or any(
                claims[0][name] != source[name]
                for name in ("source_system", "source_settlement_key")
            ):
                refuse("confirmed cash source claim identity disagrees")
            validate_confirmed_cash_origin(db, scope=scope, source=source)
            acceptances = rows(
                "posting_groups",
                source_ref=f"settlement:{source['id']}",
                group_kind="settlement_accepted",
            )
            if (
                len(acceptances) != 1
                or acceptances[0]["reverses_group_id"] is not None
                or source["amount"] <= 0
                or not source["confirmation_evidence"]
            ):
                refuse("original confirmed cash backing is ambiguous")
            current = acceptances[0]
            expected = source["amount"]
            lineage: set[UUID] = set()
            source_active = True
            while True:
                envelope(current)
                if current["id"] in lineage or len(lineage) >= 128:
                    refuse("cash backing lineage cycles")
                lineage.add(current["id"])
                effects = rows("posting_effects", posting_group_id=current["id"])
                if len(effects) != 1:
                    refuse("cash backing lanes are ambiguous")
                envelope(effects[0])
                if (
                    effects[0]["lane"] != "available_credit"
                    or quantity(effects[0]["amount_delta"]) != expected
                    or rows("allocation_effects", posting_group_id=current["id"])
                    or rows("opening_cash_effects", posting_group_id=current["id"])
                ):
                    refuse("cash backing does not match confirmed receipt")
                children = rows("posting_groups", reverses_group_id=current["id"])
                if not children:
                    break
                if len(children) != 1 or children[0]["group_kind"] != "reversal":
                    refuse("cash backing inverse is ambiguous")
                current = children[0]
                expected = -expected
                source_active = not source_active
            active = active and source_active
            remaining += net(root, is_opening=False)
        for root in opening:
            for item in rows(
                "opening_cash_effects",
                lot_id=root["lot_id"],
                document_id=request.document_id,
            ):
                envelope(item)
                movement = movement_group(item["posting_group_id"])
                parent_id = item["original_effect_id"]
                if parent_id is None:
                    if (
                        item["effect_kind"] != "application"
                        or item["amount_delta"] >= 0
                        or movement["group_kind"] != "opening_cash_applied"
                    ):
                        refuse("unlinked opening cash application")
                else:
                    parent = one("opening_cash_effects", parent_id)
                    envelope(parent)
                    fact = get_accounting_fact_v4(
                        db, scope=scope, posting_group_id=movement["id"]
                    )
                    if (
                        item["effect_kind"] != "application_inverse"
                        or item["amount_delta"] <= 0
                        or parent["effect_kind"] != "application"
                        or parent["original_effect_id"] is not None
                        or parent["document_id"] != request.document_id
                        or parent["lot_id"] != root["lot_id"]
                        or fact.corrects_posting_group_id != parent["posting_group_id"]
                        or movement["group_kind"] != "cash_credit_draw_reversed"
                    ):
                        refuse("opening cash inverse ancestry disagrees")
            position = get_opening_cash_lot_position(
                db, scope=scope, lot_id=root["lot_id"]
            )
            active = active and not position.unwound
            remaining -= net(root, is_opening=True)
        if not 0 <= remaining <= original:
            refuse("draw remaining quantity exceeds original")
        state = (
            CashCreditDrawState.INACTIVE_BACKING
            if not active
            else CashCreditDrawState.REMOVED
            if remaining == 0
            else CashCreditDrawState.CURRENT
            if remaining == original
            else CashCreditDrawState.REDUCED
        )
        return CurrentCashCreditDrawV1(
            scope,
            request.billing_account_id,
            request.posting_group_id,
            request.document_id,
            request.amount,
            Money.of(remaining, currency),
            request.source_ref,
            state,
        )


def validate_confirmed_cash_origin(db: Session, *, scope: Scope, source: Any) -> None:
    """Validate permanent receipt facts; the old request hash is opaque provenance.

    PostgreSQL normalizes timestamp offsets and NUMERIC scale. The published
    request fingerprint includes their original spelling, which cannot be
    reconstructed. Never replace that fingerprint or depend on purgeable replay
    records. This proves the stored financial chain, not external confirmation.
    """

    def refuse() -> NoReturn:
        raise BillingRuleViolation(
            "settlement_evidence_corrupt",
            "confirmed cash original fact chain disagrees",
        )

    with db.no_autoflush:
        if (
            not isinstance(source["request_fingerprint"], str)
            or re.fullmatch(r"[0-9a-f]{64}", source["request_fingerprint"]) is None
        ):
            refuse()
        groups = _table(scope, "posting_groups")
        group_rows = list(
            db.execute(
                _scoped(
                    select(groups).where(
                        groups.c.source_ref == f"settlement:{source['id']}",
                        groups.c.group_kind == "settlement_accepted",
                    ),
                    scope,
                    groups,
                )
            ).mappings()
        )
        if len(group_rows) != 1:
            refuse()
        group = group_rows[0]
        if (
            group["reverses_group_id"] is not None
            or group["billing_account_id"] != source["billing_account_id"]
            or group["occurred_at"] != source["occurred_at"]
            or (group["currency"], group["minor_units"])
            != (source["currency"], source["minor_units"])
        ):
            refuse()
        table = _table(scope, "accounting_facts")
        facts = list(
            db.execute(
                _scoped(
                    select(table).where(table.c.posting_group_id == group["id"]),
                    scope,
                    table,
                )
            ).mappings()
        )
        if len(facts) != 1:
            refuse()
        row = facts[0]
        if (
            fingerprint_of(row["fact_payload"]) != row["fact_digest"]
            or row["fact_version"] != 1
        ):
            refuse()
        try:
            fact = _decode(row["fact_payload"], AccountingFactV1)
        except (TypeError, ValueError, KeyError, DecimalException):
            refuse()
        if (
            fact.scope != scope
            or fact.fact_id != row["id"]
            or fact.fact_version != 1
            or fact.billing_account_id != source["billing_account_id"]
            or fact.posting_group_id != group["id"]
            or fact.source_ref != group["source_ref"]
            or fact.source_system != row["source_system"]
            or fact.source_system != "dotmac-billing"
            or fact.source_authority != row["source_authority"]
            or fact.source_authority != "internal"
            or fact.effect_kind != "settlement_accepted"
            or row["effect_kind"] != fact.effect_kind
            or fact.occurred_at != source["occurred_at"]
            or row["occurred_at"] != fact.occurred_at
            or fact.allocations
            or fact.tax_snapshots
            or fact.fx_snapshot is not None
            or fact.reverses_fact_id is not None
            or row["reverses_fact_id"] is not None
            or len(fact.effects) != 1
        ):
            refuse()
        money = fact.effects[0].amount_delta
        if (
            fact.effects[0].lane != source["funding_lane"]
            or money.amount != source["amount"]
            or (money.currency.code, money.currency.minor_units)
            != (source["currency"], source["minor_units"])
        ):
            refuse()
        table = _table(scope, "posting_effects")
        effects = list(
            db.execute(
                _scoped(
                    select(table).where(table.c.posting_group_id == group["id"]),
                    scope,
                    table,
                )
            ).mappings()
        )
        if (
            len(effects) != 1
            or any(
                effects[0][name] != source[name]
                for name in ("billing_account_id", "currency", "minor_units")
            )
            or effects[0]["lane"] != source["funding_lane"]
            or effects[0]["amount_delta"] != source["amount"]
        ):
            refuse()
        for name in ("allocation_effects", "opening_cash_effects"):
            table = _table(scope, name)
            if (
                db.execute(
                    _scoped(
                        select(table.c.id).where(
                            table.c.posting_group_id == group["id"]
                        ),
                        scope,
                        table,
                    )
                ).first()
                is not None
            ):
                refuse()
