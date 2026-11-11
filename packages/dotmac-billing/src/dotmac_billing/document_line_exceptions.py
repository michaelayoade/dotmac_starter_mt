"""Read-only listing of document lines that break the price-identity rule.

Reporting only, for the manual exceptions review. Nothing here updates,
repairs or deletes a row. A line is listed when

* it names an obligation whose ``price_version_id`` differs from the line's
  ``price_source_version`` (the bi_0015 composite key), or names no obligation
  that exists (``reason = "price_version_mismatch"``); or
* it names no obligation and is not a credit-note line of the form the credit
  writer produces, ``price_source_version = 'credit:' || credits_document_id``
  (``reason = "obligation_required"``, the bi_0015 line guard).

The listing is scoped to the routed tenant (or the platform plane), ordered by
line id and bounded by ``limit``; ``after_id`` pages forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from sqlalchemy import and_, cast, literal, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.types import String

from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import _scoped, _table

MAX_EXCEPTIONS = 1000

ExceptionReason = Literal["price_version_mismatch", "obligation_required"]


@dataclass(frozen=True, slots=True)
class DocumentLinePriceExceptionV1:
    line_id: UUID
    document_id: UUID
    billing_account_id: UUID
    document_kind: str
    obligation_id: UUID | None
    price_source_version: str
    obligation_price_version_id: str | None
    reason: ExceptionReason


def list_document_line_price_exceptions(
    db: Session,
    *,
    scope: Scope,
    limit: int = 200,
    after_id: UUID | None = None,
) -> tuple[DocumentLinePriceExceptionV1, ...]:
    """List existing violating lines. Read-only; never repairs or updates."""

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise BillingRuleViolation("invalid_limit", "limit must be an integer")
    if not 1 <= limit <= MAX_EXCEPTIONS:
        raise BillingRuleViolation(
            "invalid_limit", f"limit must be between 1 and {MAX_EXCEPTIONS}"
        )
    lines = _table(scope, "document_lines")
    documents = _table(scope, "documents")
    obligations = _table(scope, "rated_obligations")
    doc_join = lines.c.document_id == documents.c.id
    obligation_join = lines.c.obligation_id == obligations.c.id
    if isinstance(scope, TenantScope):
        doc_join = and_(doc_join, lines.c.tenant_id == documents.c.tenant_id)
        obligation_join = and_(
            obligation_join, lines.c.tenant_id == obligations.c.tenant_id
        )
    credit_version = literal("credit:") + cast(documents.c.credits_document_id, String)
    legitimate_credit = and_(
        documents.c.document_kind == "credit_note",
        documents.c.credits_document_id.is_not(None),
        lines.c.price_source_version == credit_version,
    )
    mismatch = and_(
        lines.c.obligation_id.is_not(None),
        or_(
            obligations.c.id.is_(None),
            obligations.c.price_version_id != lines.c.price_source_version,
        ),
    )
    missing = and_(lines.c.obligation_id.is_(None), ~legitimate_credit)
    statement = (
        select(
            lines.c.id,
            lines.c.document_id,
            lines.c.billing_account_id,
            documents.c.document_kind,
            lines.c.obligation_id,
            lines.c.price_source_version,
            obligations.c.price_version_id,
        )
        .select_from(
            lines.join(documents, doc_join).outerjoin(obligations, obligation_join)
        )
        .where(or_(mismatch, missing))
        .order_by(lines.c.id)
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(lines.c.id > after_id)
    with db.no_autoflush:
        rows = db.execute(_scoped(statement, scope, lines)).all()
    return tuple(
        DocumentLinePriceExceptionV1(
            line_id=row[0],
            document_id=row[1],
            billing_account_id=row[2],
            document_kind=row[3],
            obligation_id=row[4],
            price_source_version=row[5],
            obligation_price_version_id=row[6],
            reason="obligation_required"
            if row[4] is None
            else "price_version_mismatch",
        )
        for row in rows
    )
