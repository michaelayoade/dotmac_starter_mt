"""Original provider cash facts and transaction-scoped admission.

This owner performs no provider I/O and posts no ledger entries. An installed
trusted evidence authority is mandatory; all reads bypass dirty ORM state and
autoflush. Transport/source fences remain held until the caller ends its outer
transaction. Callers must admit again before every new financial effect.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.outbox import enqueue_event
from dotmac_kernel.money import Currency, Money
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from dotmac_payments.contracts import (
    AuthorizedProviderCashV1,
    Conflict,
    GetOriginalMerchantContextV1,
    MerchantContextV1,
    MerchantTransportContextV1,
    OriginalProviderCashEvidenceV1,
    ProviderCashEvidenceAuthority,
    ProviderCashQualificationV1,
    QualifyProviderCashV1,
    ResolveMerchantContextV1,
)
from dotmac_payments.models import ProviderCashQualification
from dotmac_payments.service import (
    _merchant_digest,
    _merchant_instant,
    _merchant_lock,
    _merchant_text,
    _merchant_uuid,
    _transport_values,
    get_original_merchant_context,
    resolve_current_merchant_context,
)


def _digest(domain: str, values: list[object]) -> str:
    frames = [str(item).encode("utf-8") for item in [domain, *values]]
    return hashlib.sha256(
        b"".join(str(len(item)).encode("ascii") + b":" + item for item in frames)
    ).hexdigest()


def _clock(value: datetime) -> str:
    return _merchant_instant(value).isoformat(timespec="microseconds")


def _scope(scope: TenantScope) -> UUID:
    if type(scope) is not TenantScope:
        raise Conflict("provider cash requires an exact TenantScope")
    return _merchant_uuid(scope.tenant_id)


def _evidence(value: OriginalProviderCashEvidenceV1) -> list[str]:
    if type(value) is not OriginalProviderCashEvidenceV1:
        raise Conflict("typed original provider evidence is required")
    return [
        _merchant_text(value.owner, 160, "original evidence owner"),
        _merchant_text(value.reference, 512, "original evidence reference"),
        _merchant_text(value.schema_version, 80, "original evidence schema"),
        _merchant_digest(value.sha256),
    ]


def provider_cash_request_fingerprint(request: QualifyProviderCashV1) -> str:
    if type(request) is not QualifyProviderCashV1:
        raise Conflict("typed provider cash qualification request is required")
    return _digest(
        "provider-cash-request-v1",
        [
            str(_merchant_uuid(request.command_id)),
            *_evidence(request.original_evidence),
        ],
    )


def _money(value: Money) -> list[object]:
    if type(value) is not Money or type(value.currency) is not Currency:
        raise Conflict("exact typed Money and Currency are required")
    amount, currency = value.amount, value.currency
    if (
        type(amount) is not Decimal
        or not amount.is_finite()
        or amount <= 0
        or type(currency.minor_units) is not int
        or not 0 <= currency.minor_units <= 6
        or type(currency.code) is not str
        or len(currency.code) != 3
        or not currency.code.isascii()
        or not currency.code.isalpha()
        or not currency.code.isupper()
        or amount >= Decimal("100000000000000")
    ):
        raise Conflict(
            "provider cash Money is invalid or exceeds exact storage precision"
        )
    try:
        exact = amount.quantize(Decimal(1).scaleb(-currency.minor_units))
    except InvalidOperation as exc:
        raise Conflict("provider cash Money precision is invalid") from exc
    if exact != amount:
        raise Conflict("provider cash must not round provider amounts")
    return [format(exact, "f"), currency.code, currency.minor_units]


def _capture(value: AuthorizedProviderCashV1) -> list[object]:
    if type(value) is not AuthorizedProviderCashV1:
        raise Conflict("typed original provider authority attestation is required")
    _scope(value.scope)
    _merchant_uuid(value.command_id)
    _merchant_digest(value.request_fingerprint)
    _evidence(value.original_evidence)
    _transport_values(value.transport)
    _merchant_uuid(value.context_id)
    verified = _merchant_instant(value.verified_at)
    paid = _merchant_instant(value.paid_at)
    observed = _merchant_instant(value.observed_at)
    if paid > verified or verified > observed or observed > datetime.now(UTC):
        raise Conflict(
            "provider payment, verification and observation clocks are invalid"
        )
    if type(value.status) is not str or value.status != "succeeded":
        raise Conflict("only explicitly successful provider cash capture is admissible")
    return [
        _merchant_text(value.transaction_kind, 80, "transaction kind"),
        _merchant_text(value.transaction_id, 160, "provider transaction"),
        _merchant_text(value.checkout_reference, 120, "original checkout reference"),
        value.status,
        *_money(value.money),
        _clock(paid),
    ]


def provider_cash_economic_identity(
    value: AuthorizedProviderCashV1, physical_merchant_id: str
) -> tuple[str, str, str]:
    """Shared opening/live tuple; no local IDs or retrieval provenance enter it."""
    capture = _capture(value)
    namespace = [
        value.transport.provider_type,
        value.transport.environment,
        _merchant_text(physical_merchant_id, 160, "physical merchant"),
        value.transaction_kind,
    ]
    return (
        "pcn1:" + _digest("provider-cash-namespace-v1", namespace),
        value.transaction_id,
        "pcv1:" + _digest("provider-cash-economic-capture-v1", [*namespace, *capture]),
    )


def _provenance(value: AuthorizedProviderCashV1) -> str:
    return _digest(
        "provider-cash-original-provenance-v1",
        [
            str(value.scope.tenant_id),
            str(value.command_id),
            value.request_fingerprint,
            *_evidence(value.original_evidence),
            *_transport_values(value.transport),
            str(value.context_id),
            _clock(value.verified_at),
            _clock(value.observed_at),
        ],
    )


def _verify(
    db: Session,
    scope: TenantScope,
    request: QualifyProviderCashV1,
    authority: ProviderCashEvidenceAuthority,
) -> AuthorizedProviderCashV1:
    tenant = _scope(scope)
    fingerprint = provider_cash_request_fingerprint(request)
    if not callable(getattr(authority, "verify_original", None)):
        raise Conflict("installed original provider evidence authority is required")
    with db.no_autoflush:
        result = authority.verify_original(
            db, scope=scope, request=request, request_fingerprint=fingerprint
        )
    _capture(result)
    if (
        provider_cash_request_fingerprint(request) != fingerprint
        or result.scope.tenant_id != tenant
        or result.command_id != request.command_id
        or result.request_fingerprint != fingerprint
        or _evidence(result.original_evidence) != _evidence(request.original_evidence)
    ):
        raise Conflict(
            "original evidence authority does not bind exact scope/request/evidence"
        )
    return result


def _lock(
    db: Session,
    scope: TenantScope,
    value: AuthorizedProviderCashV1,
    namespace: str | None = None,
) -> None:
    # Suppress autoflush for the isolation probe and locks as well as reads.
    with db.no_autoflush:
        if db.get_bind().dialect.name != "postgresql":
            raise Conflict("provider cash qualification requires PostgreSQL")
        _merchant_lock(db, _scope(scope), value.command_id, value.transport)
        if namespace is not None:
            db.execute(
                text(
                    "SELECT mod_payments.provider_cash_source_lock("
                    "CAST(:tenant AS uuid),:namespace,:key)"
                ),
                {
                    "tenant": str(scope.tenant_id),
                    "namespace": namespace,
                    "key": value.transaction_id,
                },
            )


def _rows(
    db: Session, scope: TenantScope, *conditions: ColumnElement[bool]
) -> list[ProviderCashQualificationV1]:
    table = ProviderCashQualification.__table__
    with db.no_autoflush:
        records = (
            db.execute(
                select(*table.c).where(table.c.tenant_id == _scope(scope), *conditions)
            )
            .mappings()
            .all()
        )
    result = []
    for row in records:
        original = AuthorizedProviderCashV1(
            scope,
            row["command_id"],
            row["request_fingerprint"],
            OriginalProviderCashEvidenceV1(
                row["evidence_owner"],
                row["evidence_reference"],
                row["evidence_schema_version"],
                row["evidence_sha256"],
            ),
            MerchantTransportContextV1(
                row["provider_type"],
                row["environment"],
                row["installation_reference"],
                row["capability_reference"],
                row["config_revision_reference"],
                row["config_digest"],
            ),
            row["context_id"],
            row["verified_at"],
            row["transaction_kind"],
            row["transaction_id"],
            row["checkout_reference"],
            row["status"],
            Money.of(row["amount"], Currency(row["currency_code"], row["minor_units"])),
            row["paid_at"],
            row["observed_at"],
        )
        identity = provider_cash_economic_identity(
            original, row["physical_merchant_id"]
        )
        if (
            identity
            != (row["source_namespace"], row["source_key"], row["source_version"])
            or _provenance(original) != row["provenance_fingerprint"]
        ):
            raise Conflict(
                "persisted provider cash identity/provenance is inconsistent"
            )
        result.append(
            ProviderCashQualificationV1(
                scope,
                row["id"],
                row["command_id"],
                row["request_fingerprint"],
                original,
                row["physical_merchant_id"],
                *identity,
                row["provenance_fingerprint"],
            )
        )
    return result


def _original_context(
    db: Session, scope: TenantScope, value: AuthorizedProviderCashV1
) -> MerchantContextV1:
    return get_original_merchant_context(
        db,
        scope=scope,
        query=GetOriginalMerchantContextV1(
            value.context_id, value.transport, value.verified_at
        ),
    )


def _admit_context(
    db: Session, scope: TenantScope, value: AuthorizedProviderCashV1
) -> None:
    # Current server time, original verification and provider payment clocks are
    # deliberately distinct. A prior valid context does not survive expiration.
    for instant in (value.verified_at, datetime.now(UTC)):
        context = resolve_current_merchant_context(
            db, scope=scope, query=ResolveMerchantContextV1(value.transport, instant)
        )
        if context.context_id != value.context_id:
            raise Conflict("original provider context is no longer applicable")


def qualify_provider_cash(
    db: Session,
    *,
    scope: TenantScope,
    request: QualifyProviderCashV1,
    authority: ProviderCashEvidenceAuthority,
) -> ProviderCashQualificationV1:
    """Append exact original qualification; replay remains historical evidence."""
    value = _verify(db, scope, request, authority)
    _lock(db, scope, value)
    context = _original_context(db, scope, value)
    identity = provider_cash_economic_identity(
        value, context.configuration.physical_merchant_id
    )
    _lock(db, scope, value, identity[0])
    existing = _rows(
        db, scope, ProviderCashQualification.command_id == request.command_id
    )
    if existing:
        row = existing[0]
        if (
            row.original != value
            or (row.source_namespace, row.source_key, row.source_version) != identity
        ):
            raise Conflict("qualification command replay differs from original cash")
        return row
    _admit_context(db, scope, value)
    if _rows(
        db,
        scope,
        ProviderCashQualification.source_namespace == identity[0],
        ProviderCashQualification.source_key == identity[1],
    ):
        raise Conflict("physical provider cash already has an original qualification")
    row = ProviderCashQualification(
        id=uuid4(),
        tenant_id=scope.tenant_id,
        command_id=value.command_id,
        request_fingerprint=value.request_fingerprint,
        evidence_owner=value.original_evidence.owner,
        evidence_reference=value.original_evidence.reference,
        evidence_schema_version=value.original_evidence.schema_version,
        evidence_sha256=value.original_evidence.sha256,
        context_id=value.context_id,
        provider_type=value.transport.provider_type,
        environment=value.transport.environment,
        physical_merchant_id=context.configuration.physical_merchant_id,
        installation_reference=value.transport.installation_reference,
        capability_reference=value.transport.capability_reference,
        config_revision_reference=value.transport.config_revision_reference,
        config_digest=value.transport.config_digest,
        verified_at=value.verified_at,
        transaction_kind=value.transaction_kind,
        transaction_id=value.transaction_id,
        checkout_reference=value.checkout_reference,
        status=value.status,
        amount=value.money.amount,
        currency_code=value.money.currency.code,
        minor_units=value.money.currency.minor_units,
        paid_at=value.paid_at,
        observed_at=value.observed_at,
        source_namespace=identity[0],
        source_key=identity[1],
        source_version=identity[2],
        provenance_fingerprint=_provenance(value),
    )
    db.add(row)
    db.flush([row])
    persisted = _rows(db, scope, ProviderCashQualification.id == row.id)
    if (
        len(persisted) != 1
        or persisted[0].original != value
        or persisted[0].physical_merchant_id
        != context.configuration.physical_merchant_id
    ):
        raise Conflict(
            "persisted qualification differs from authorized original evidence"
        )
    enqueue_event(
        db,
        tenant_id=scope.tenant_id,
        event_type="payments.provider_cash.qualified.v1",
        payload={
            "qualification_id": str(row.id),
            "source_namespace": identity[0],
            "source_key": identity[1],
            "source_version": identity[2],
        },
    )
    return persisted[0]


def get_provider_cash_qualification(
    db: Session, *, scope: TenantScope, qualification_id: UUID
) -> ProviderCashQualificationV1:
    """Historical read only; never grants financial admission."""
    rows = _rows(
        db, scope, ProviderCashQualification.id == _merchant_uuid(qualification_id)
    )
    if len(rows) != 1:
        raise Conflict("provider cash qualification is absent in tenant")
    return rows[0]


def require_provider_cash_admission(
    db: Session,
    *,
    scope: TenantScope,
    qualification_id: UUID,
    authority: ProviderCashEvidenceAuthority,
) -> ProviderCashQualificationV1:
    """Mandatory before ANY new Billing effect, in that effect's outer tx.

    The returned immutable DTO is not reusable authorization. Locks persist
    through caller commit; a savepoint or historical replay never substitutes.
    """
    row = get_provider_cash_qualification(
        db, scope=scope, qualification_id=qualification_id
    )
    value = _verify(
        db,
        scope,
        QualifyProviderCashV1(row.command_id, row.original.original_evidence),
        authority,
    )
    _lock(db, scope, value, row.source_namespace)
    if value != row.original:
        raise Conflict("admission original evidence differs from qualification")
    _original_context(db, scope, value)
    _admit_context(db, scope, value)
    return get_provider_cash_qualification(
        db, scope=scope, qualification_id=qualification_id
    )


__all__ = [
    "provider_cash_request_fingerprint",
    "provider_cash_economic_identity",
    "qualify_provider_cash",
    "get_provider_cash_qualification",
    "require_provider_cash_admission",
]
