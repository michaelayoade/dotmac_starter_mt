"""One native/imported backing resolver over actual source-version rows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing._opening_values import cash_decimal_context
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import _scoped, _table


@dataclass(frozen=True, slots=True)
class ReceivableBacking:
    basis_id: UUID | None
    initial_amount: Decimal
    remaining: Decimal
    source_version: int
    posting_watermark: UUID
    active: bool


def resolve_receivable_backing(
    db: Session,
    *,
    scope: Scope,
    document_id: UUID,
    source_version: int | None = None,
) -> ReceivableBacking:
    if not isinstance(scope, TenantScope | PlatformScope):
        raise BillingRuleViolation("scope_mismatch", "backing requires explicit scope")
    documents = _table(scope, "documents")
    groups = _table(scope, "posting_groups")
    bases = _table(scope, "opening_receivable_bases")
    receipts = _table(scope, "opening_import_receipts")
    with db.no_autoflush, localcontext(cash_decimal_context()):
        document = (
            db.execute(
                _scoped(
                    select(documents).where(documents.c.id == document_id),
                    scope,
                    documents,
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            document is None
            or document["document_kind"] != "invoice"
            or document["issued_at"] is None
        ):
            raise BillingRuleViolation(
                "invoice_issuance_not_live", "invoice backing is unavailable"
            )
        basis = (
            db.execute(
                _scoped(
                    select(bases).where(bases.c.document_id == document_id),
                    scope,
                    bases,
                )
            )
            .mappings()
            .one_or_none()
        )
        imported = document["authoring_mode"] == "imported_open_item"
        if imported != (basis is not None):
            raise BillingRuleViolation(
                "receivable_backing_integrity", "document backing mode disagrees"
            )
        statement = _scoped(
            select(groups).where(
                groups.c.billing_account_id == document["billing_account_id"],
            ),
            scope,
            groups,
        )
        if source_version is not None:
            statement = statement.where(groups.c.source_version <= source_version)
        group = (
            db.execute(statement.order_by(groups.c.source_version.desc()).limit(1))
            .mappings()
            .one_or_none()
        )
        if group is None or (
            source_version is not None and group["source_version"] != source_version
        ):
            raise BillingRuleViolation(
                "receivable_backing_integrity", "posting watermark is unavailable"
            )
        version = group["source_version"]
        prefix = "platform_" if isinstance(scope, PlatformScope) else ""
        args = [document_id, document["billing_account_id"], version]
        if isinstance(scope, TenantScope):
            args.append(scope.tenant_id)
        remaining = Decimal(
            db.execute(
                select(
                    getattr(func.mod_billing, f"opening_{prefix}invoice_room_at")(*args)
                )
            ).scalar_one()
        )
        initial = Decimal(
            document["grand_total"] if basis is None else basis["initial_receivable"]
        )
        if not remaining.is_finite() or remaining < 0 or remaining > initial:
            raise BillingRuleViolation(
                "invoice_room_integrity", "invoice resolution exceeds exact backing"
            )
        active = True
        if basis is not None:
            unwind = db.execute(
                _scoped(
                    select(receipts.c.posting_group_id).where(
                        receipts.c.original_receipt_id == basis["receipt_id"],
                    ),
                    scope,
                    receipts,
                )
            ).scalar_one_or_none()
            if unwind is not None:
                inverse_version = db.execute(
                    _scoped(
                        select(groups.c.source_version).where(
                            groups.c.id == unwind,
                        ),
                        scope,
                        groups,
                    )
                ).scalar_one()
                active = inverse_version > version
        else:
            live_args = [
                document_id,
                document["billing_account_id"],
                document["currency"],
                document["minor_units"],
            ]
            if isinstance(scope, TenantScope):
                live_args.append(scope.tenant_id)
            active = bool(
                db.execute(
                    select(
                        getattr(func.mod_billing, f"has_{prefix}live_invoice_issuance")(
                            *live_args
                        )
                    )
                ).scalar_one()
            )
        return ReceivableBacking(
            None if basis is None else basis["id"],
            initial,
            remaining,
            version,
            group["id"],
            active,
        )
