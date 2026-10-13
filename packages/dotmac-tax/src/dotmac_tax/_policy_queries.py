"""Bounded, flush-free scalar reads of tenant-owned policy.

Catalogs traverse immutable UUID identities; per-code history traverses version.
Cursors bind the tenant, filters and order, but are not authorization tokens.
Pages read live committed/explicitly flushed state, not a cross-request snapshot:
new rows behind a cursor require a fresh traversal. No offset or count is implied.
The caller supplies its authorized tenant, as for the existing Tax get APIs; RLS
adds the independent database boundary on tenant application connections.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import asdict
from uuid import UUID

from dotmac_kernel.money import Currency
from sqlalchemy import ColumnElement, RowMapping, Table, select
from sqlalchemy.orm import Session

from dotmac_tax._policy_query_contracts import (
    ListTaxAuthoritiesV1,
    ListTaxCodesV1,
    ListTaxJurisdictionsV1,
    ListTaxRuleHistoryV1,
    TaxAuthorityPageV1,
    TaxCodePageV1,
    TaxJurisdictionPageV1,
    TaxRuleHistoryEntryV1,
    TaxRuleHistoryPageV1,
    TaxRuleHistorySupersessionV1,
)
from dotmac_tax.contracts import (
    TaxAuthorityV1,
    TaxCodeV1,
    TaxJurisdictionV1,
    TaxRuleBandV1,
    TaxRuleV1,
)
from dotmac_tax.models import (
    TaxAuthority,
    TaxCode,
    TaxJurisdiction,
    TaxRule,
    TaxRuleBand,
    TaxRuleSupersession,
)
from dotmac_tax.service import (
    TaxNotFound,
    TaxRuleViolation,
    _exact_policy_money,
    _utc_datetime,
)

_Request = (
    ListTaxAuthoritiesV1
    | ListTaxJurisdictionsV1
    | ListTaxCodesV1
    | ListTaxRuleHistoryV1
)


def _require_id(db: Session, table: Table, tenant_id: UUID, record_id: UUID) -> None:
    if (
        db.scalar(
            select(table.c.id).where(
                table.c.tenant_id == tenant_id, table.c.id == record_id
            )
        )
        is None
    ):
        raise TaxNotFound("tax policy filter record not found")


def _context(table: Table, tenant_id: UUID, request: _Request, order: str) -> str:
    filters = asdict(request)
    filters.pop("cursor")
    filters.pop("page_size")
    content = json.dumps(
        ["tax-policy-page-v1", table.fullname, str(tenant_id), order, filters],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _after(cursor: str | None, context: str, order: str) -> UUID | int | None:
    if cursor is None:
        return None
    try:
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"context", "after"}
            or payload["context"] != context
        ):
            raise ValueError("cursor context mismatch")
        value = payload["after"]
        if order == "version":
            if type(value) is not int or not 1 <= value <= 2_147_483_647:
                raise ValueError("invalid version cursor")
            return value
        if not isinstance(value, str):
            raise ValueError("invalid identity cursor")
        return UUID(value)
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise TaxRuleViolation("invalid or mismatched tax policy cursor") from exc


def _page(
    db: Session,
    table: Table,
    *,
    tenant_id: UUID,
    request: _Request,
    predicates: tuple[ColumnElement[bool], ...] = (),
    order: str = "id",
) -> tuple[list[RowMapping], str | None]:
    if not isinstance(tenant_id, UUID):
        raise TaxRuleViolation("tenant_id must be a UUID")
    context = _context(table, tenant_id, request, order)
    after = _after(request.cursor, context, order)
    statement = select(table).where(table.c.tenant_id == tenant_id, *predicates)
    if after is not None:
        statement = statement.where(table.c[order] > after)
    rows = list(
        db.execute(
            statement.order_by(table.c[order]).limit(request.page_size + 1)
        ).mappings()
    )
    visible = rows[: request.page_size]
    cursor = None
    if len(rows) > request.page_size:
        last = visible[-1][order]
        cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "context": context,
                    "after": last if order == "version" else str(last),
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
    return visible, cursor


def list_tax_authorities(
    db: Session,
    *,
    tenant_id: UUID,
    request: ListTaxAuthoritiesV1,
) -> TaxAuthorityPageV1:
    with db.no_autoflush:
        table = TaxAuthority.__table__
        predicates = (
            () if request.status is None else (table.c.status == request.status,)
        )
        rows, cursor = _page(
            db, table, tenant_id=tenant_id, request=request, predicates=predicates
        )
        return TaxAuthorityPageV1(
            tuple(
                TaxAuthorityV1(
                    tenant_id=row["tenant_id"],
                    authority_id=row["id"],
                    code=row["code"],
                    name=row["name"],
                    authority_level_code=row["authority_level_code"],
                    status=row["status"],
                )
                for row in rows
            ),
            cursor,
        )


def list_tax_jurisdictions(
    db: Session,
    *,
    tenant_id: UUID,
    request: ListTaxJurisdictionsV1,
) -> TaxJurisdictionPageV1:
    with db.no_autoflush:
        table = TaxJurisdiction.__table__
        predicates = []
        if request.authority_id is not None:
            _require_id(db, TaxAuthority.__table__, tenant_id, request.authority_id)
            predicates.append(table.c.authority_id == request.authority_id)
        if request.status is not None:
            predicates.append(table.c.status == request.status)
        if request.country_code is not None:
            predicates.append(table.c.country_code == request.country_code)
        rows, cursor = _page(
            db,
            table,
            tenant_id=tenant_id,
            request=request,
            predicates=tuple(predicates),
        )
        return TaxJurisdictionPageV1(
            tuple(
                TaxJurisdictionV1(
                    tenant_id=row["tenant_id"],
                    jurisdiction_id=row["id"],
                    authority_id=row["authority_id"],
                    code=row["code"],
                    name=row["name"],
                    country_code=row["country_code"],
                    subdivision_code=row["subdivision_code"],
                    currency=Currency(row["currency_code"], row["minor_units"]),
                    status=row["status"],
                )
                for row in rows
            ),
            cursor,
        )


def list_tax_codes(
    db: Session,
    *,
    tenant_id: UUID,
    request: ListTaxCodesV1,
) -> TaxCodePageV1:
    with db.no_autoflush:
        table = TaxCode.__table__
        predicates = []
        if request.jurisdiction_id is not None:
            _require_id(
                db, TaxJurisdiction.__table__, tenant_id, request.jurisdiction_id
            )
            predicates.append(table.c.jurisdiction_id == request.jurisdiction_id)
        if request.status is not None:
            predicates.append(table.c.status == request.status)
        if request.tax_kind_code is not None:
            predicates.append(table.c.tax_kind_code == request.tax_kind_code)
        rows, cursor = _page(
            db,
            table,
            tenant_id=tenant_id,
            request=request,
            predicates=tuple(predicates),
        )
        return TaxCodePageV1(
            tuple(
                TaxCodeV1(
                    tenant_id=row["tenant_id"],
                    tax_code_id=row["id"],
                    jurisdiction_id=row["jurisdiction_id"],
                    code=row["code"],
                    name=row["name"],
                    tax_kind_code=row["tax_kind_code"],
                    description=row["description"],
                    status=row["status"],
                )
                for row in rows
            ),
            cursor,
        )


def _rule(
    db: Session, *, tenant_id: UUID, row: RowMapping, currency: Currency
) -> TaxRuleV1:
    bands = db.execute(
        select(TaxRuleBand.__table__)
        .where(
            TaxRuleBand.tenant_id == tenant_id,
            TaxRuleBand.rule_id == row["id"],
        )
        .order_by(TaxRuleBand.sequence)
    ).mappings()
    return TaxRuleV1(
        tenant_id=row["tenant_id"],
        rule_id=row["id"],
        tax_code_id=row["tax_code_id"],
        version=row["version"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        priority=row["priority"],
        fact_kind=row["fact_kind"],
        recognition_basis_code=row["recognition_basis_code"],
        transaction_side=row["transaction_side"],
        calculation_method=row["calculation_method"],
        rate=row["rate"],
        fixed_amount=_exact_policy_money(row["fixed_amount"], currency=currency)
        if row["fixed_amount"] is not None
        else None,
        inclusive=row["inclusive"],
        recoverable_rate=row["recoverable_rate"],
        party_category=row["party_category"],
        supply_category=row["supply_category"],
        place_code=row["place_code"],
        treatment_code=row["treatment_code"],
        calculation_sequence=row["calculation_sequence"],
        calculation_base_code=row["calculation_base_code"],
        published_at=_utc_datetime(row["published_at"]),
        bands=tuple(
            TaxRuleBandV1(
                sequence=band["sequence"],
                lower_bound=band["lower_bound"],
                upper_bound=band["upper_bound"],
                rate=band["rate"],
            )
            for band in bands
        ),
    )


def _supersession(row: RowMapping) -> TaxRuleHistorySupersessionV1:
    return TaxRuleHistorySupersessionV1(
        supersession_id=row["id"],
        predecessor_rule_id=row["predecessor_rule_id"],
        predecessor_version=row["predecessor_version"],
        replacement_rule_id=row["replacement_rule_id"],
        replacement_version=row["replacement_version"],
        effective_from=row["effective_from"],
        actor_id=row["actor_id"],
        published_by_ref=row["published_by_ref"],
        reason=row["reason"],
        evidence_ref=row["evidence_ref"],
        source_ref=row["source_ref"],
        source_version=row["source_version"],
        published_at=_utc_datetime(row["published_at"]),
    )


def list_tax_rule_history(
    db: Session,
    *,
    tenant_id: UUID,
    request: ListTaxRuleHistoryV1,
) -> TaxRuleHistoryPageV1:
    """Read every immutable version of one tenant-local code and outgoing edges.

    A supersession's effective_from closes selection of its predecessor; the
    original rule's declared effective_to is returned unchanged as evidence.
    """
    with db.no_autoflush:
        _require_id(db, TaxCode.__table__, tenant_id, request.tax_code_id)
        jurisdiction = db.execute(
            select(
                TaxJurisdiction.currency_code,
                TaxJurisdiction.minor_units,
            )
            .join(
                TaxCode,
                (TaxCode.tenant_id == TaxJurisdiction.tenant_id)
                & (TaxCode.jurisdiction_id == TaxJurisdiction.id),
            )
            .where(
                TaxCode.tenant_id == tenant_id,
                TaxJurisdiction.tenant_id == tenant_id,
                TaxCode.id == request.tax_code_id,
            )
        ).one()
        currency = Currency(jurisdiction.currency_code, jurisdiction.minor_units)
        rows, cursor = _page(
            db,
            TaxRule.__table__,
            tenant_id=tenant_id,
            request=request,
            predicates=(
                TaxRule.tax_code_id == request.tax_code_id,
                TaxRule.is_published.is_(True),
            ),
            order="version",
        )
        edges = {}
        if rows:
            for edge in db.execute(
                select(TaxRuleSupersession.__table__).where(
                    TaxRuleSupersession.tenant_id == tenant_id,
                    TaxRuleSupersession.predecessor_rule_id.in_(
                        [row["id"] for row in rows]
                    ),
                )
            ).mappings():
                edges[edge["predecessor_rule_id"]] = _supersession(edge)
        return TaxRuleHistoryPageV1(
            tuple(
                TaxRuleHistoryEntryV1(
                    rule=_rule(db, tenant_id=tenant_id, row=row, currency=currency),
                    supersession=edges.get(row["id"]),
                )
                for row in rows
            ),
            cursor,
        )
