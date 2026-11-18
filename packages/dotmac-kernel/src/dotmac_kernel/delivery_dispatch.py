"""Dispatch identity: dedupe key, rendered snapshot, provenance (ADR-0006 § 5g).

**Owner of:** *this message has an identity, and what it says is frozen.*

Not a queue. `create_dispatch` inserts the identity and enqueues ONE outbox event
in the caller's transaction; attempts, backoff, leasing and dead-letter stay with
the kernel outbox relay, and `delivery_providers.send` stays the one send path.
The relay transport (K-8) will call `load_dispatch` in a tenant-RLS session.

## Why the outbox payload is only `{"dispatch_id": ...}`

`claim_outbox_batch` runs on the cross-tenant `outbox_dispatcher` connection and
selects the payload. Reset links, invite tokens, addresses and the idempotency key
(which products derive from personal data) therefore never go into the event. The
dispatcher role has NO privilege on `communication_dispatches`; the body is read
only in a tenant-RLS session. The Academy "lab worker" role concept has none
either.

## Duplicate keys

`INSERT ... ON CONFLICT (tenant_id, idempotency_key) DO NOTHING` then a read-back.
A concurrent second creator blocks on the first transaction's unique entry, and
once that commits does nothing and reads the winner: exactly one row and one event.
If the first rolls back, the second inserts and enqueues. A duplicate with
DIFFERENT content returns the existing dispatch with `same_content=False` and is
not refused: the caller decides, because a retried request must stay safe.

## Legacy carry-forward (D7)

`import_history_key` records a legacy key as history-only (no content, never
enqueued, never loadable). `import_pending` carries a pending row's stored content
byte-for-byte with caller-supplied `legacy:<source>` provenance and its own
`available_at`. The cutover command is the adopter's; these are primitives.

## Load verifies

`load_dispatch` rebuilds the `OutboundMessage` and recomputes its fingerprint; a
mismatch, a history-only row, a redacted row or a row that no longer passes message
validation raises a `DispatchError` (terminal, never retried; a redacted snapshot
is never rebuilt).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from dotmac_kernel.delivery import DeliveryError
from dotmac_kernel.delivery_models import (
    DISPATCH_HISTORY,
    DISPATCH_PENDING,
    CommunicationDispatch,
)
from dotmac_kernel.delivery_providers import AttachmentRef, OutboundMessage
from dotmac_kernel.messaging.outbox import enqueue_event

#: Outbox routing code for a dispatch. The payload is only `{"dispatch_id": ...}`.
DISPATCH_EVENT_TYPE = "delivery.dispatch.v1"
LEGACY_PROVENANCE_PREFIX = "legacy:"
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_PROVENANCE_LENGTH = 255


class DispatchError(DeliveryError):
    """A dispatch cannot be created or loaded. Terminal, not retryable."""


class DispatchNotFound(DispatchError):
    pass


class DispatchHistoryOnly(DispatchError):
    """An imported legacy key: it has no content and is never sent."""


class DispatchRedacted(DispatchError):
    """Bodies were redacted after a terminal state; never rebuild from this."""


class DispatchIntegrityError(DispatchError):
    """The stored content does not match its fingerprint, or no longer builds."""


@dataclass(frozen=True, slots=True)
class DispatchResult:
    #: Plain values captured at call time. Prefer these after the caller's
    #: commit: the transaction-local tenant context is gone by then, so an
    #: expired `dispatch` row cannot refresh under FORCE RLS.
    dispatch_id: UUID
    status: str
    dispatch: CommunicationDispatch
    #: True when this call inserted the row (and, for non-history, enqueued).
    created: bool
    #: For a duplicate key: whether the requested content matches the stored one
    #: (always True on creation). History-only rows cannot compare: False.
    same_content: bool


@dataclass(frozen=True, slots=True)
class LoadedDispatch:
    dispatch: CommunicationDispatch
    #: Rebuilt from the snapshot, fingerprint verified.
    message: OutboundMessage


def _check_key(key: str) -> str:
    if (
        not isinstance(key, str)
        or not key
        or key != key.strip()
        or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise ValueError(
            f"idempotency_key must be a non-empty, unpadded string of at most "
            f"{MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        )
    return key


def _check_provenance(value: str, *, legacy: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_PROVENANCE_LENGTH
    ):
        raise ValueError("template_provenance must be a non-empty, unpadded string")
    if legacy and not value.startswith(LEGACY_PROVENANCE_PREFIX):
        raise ValueError(
            f"imported rows need a {LEGACY_PROVENANCE_PREFIX}<source> provenance"
        )
    return value


def _insert_ignoring_conflict(db: Session, values: dict[str, object]) -> bool:
    """Insert, doing nothing on a `(tenant_id, idempotency_key)` conflict.

    Returns True when this statement inserted the row."""
    dialect = db.get_bind().dialect.name
    insert = (sqlite if dialect == "sqlite" else postgresql).insert
    stmt = (
        insert(CommunicationDispatch)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        .returning(CommunicationDispatch.dispatch_id)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def find_dispatch_by_key(
    db: Session, tenant_id: UUID, idempotency_key: str
) -> CommunicationDispatch | None:
    """The dispatch for a key, for domain code ("was this already sent?")."""
    return db.execute(
        select(CommunicationDispatch).where(
            CommunicationDispatch.tenant_id == tenant_id,
            CommunicationDispatch.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def _existing(
    db: Session, tenant_id: UUID, key: str, fingerprint: str | None
) -> DispatchResult:
    row = db.execute(
        select(CommunicationDispatch)
        .where(
            CommunicationDispatch.tenant_id == tenant_id,
            CommunicationDispatch.idempotency_key == key,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        # Under a snapshot isolation level the winner can be invisible to us.
        raise DispatchError(
            "idempotency key conflicted but the existing dispatch is not "
            "visible to this transaction"
        )
    return DispatchResult(
        dispatch_id=row.dispatch_id,
        status=row.status,
        dispatch=row,
        created=False,
        same_content=fingerprint is not None
        and row.content_fingerprint == fingerprint,
    )


def create_dispatch(
    db: Session,
    tenant_id: UUID,
    *,
    idempotency_key: str,
    channel: str,
    category: str,
    address: str,
    template_provenance: str,
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
    headers: Mapping[str, str] | None = None,
    attachments: Sequence[AttachmentRef] = (),
    template_revision_id: UUID | None = None,
    available_at: datetime | None = None,
) -> DispatchResult:
    """Create a dispatch identity and enqueue its outbox event, in the caller's
    transaction (`add`/`flush` only, never commit).

    The content is validated by constructing the S6 `OutboundMessage` (a
    `ValueError` refuses it before anything is written). On a duplicate
    `(tenant, key)` the existing dispatch is returned and nothing is enqueued.
    """
    _check_key(idempotency_key)
    _check_provenance(template_provenance)
    dispatch_id = uuid4()
    message = OutboundMessage(
        dispatch_id=dispatch_id,
        channel=channel,
        address=address,
        body=None,
        category=category,
        subject=subject,
        text=text,
        html=html,
        headers=headers,
        attachments=attachments,
    )
    fingerprint = message.request_fingerprint
    values: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key,
        "channel": channel,
        "category": category,
        "address": address,
        "subject": message.subject,
        "text_body": message.text,
        "html_body": message.html,
        "headers": dict(message.headers or {}),
        "attachments": [
            {
                "kind": a.kind,
                "ref": a.ref,
                "filename": a.filename,
                "content_type": a.content_type,
            }
            for a in message.attachments
        ],
        "content_fingerprint": fingerprint,
        "template_provenance": template_provenance,
        "template_revision_id": template_revision_id,
        "status": DISPATCH_PENDING,
    }
    if available_at is not None:
        values["available_at"] = available_at
    if not _insert_ignoring_conflict(db, values):
        return _existing(db, tenant_id, idempotency_key, fingerprint)

    event = enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=DISPATCH_EVENT_TYPE,
        payload={"dispatch_id": str(dispatch_id)},
    )
    if available_at is not None:
        event.available_at = available_at
        db.flush()
    row = db.get(CommunicationDispatch, dispatch_id)
    assert row is not None
    return DispatchResult(
        dispatch_id=dispatch_id,
        status=row.status,
        dispatch=row,
        created=True,
        same_content=True,
    )


def import_history_key(
    db: Session,
    tenant_id: UUID,
    *,
    idempotency_key: str,
    channel: str,
    category: str,
    template_provenance: str,
    created_at: datetime | None = None,
) -> DispatchResult:
    """Import a legacy key as a HISTORY-ONLY dispatch (D7).

    Holds no address and no content, is never enqueued and can never be loaded or
    sent; its only job is to make a later `create_dispatch` of the same key a
    duplicate. Re-importing the same key is a no-op returning the existing row.
    `template_provenance` must be `legacy:<source>`.
    """
    _check_key(idempotency_key)
    _check_provenance(template_provenance, legacy=True)
    if not (channel or "").strip() or not (category or "").strip():
        raise ValueError("channel and category are required")
    dispatch_id = uuid4()
    values: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key,
        "channel": channel,
        "category": category,
        "template_provenance": template_provenance,
        "status": DISPATCH_HISTORY,
    }
    if created_at is not None:
        values["created_at"] = created_at
        values["terminal_at"] = created_at
    else:
        values["terminal_at"] = func.now()
    if not _insert_ignoring_conflict(db, values):
        return _existing(db, tenant_id, idempotency_key, None)
    row = db.get(CommunicationDispatch, dispatch_id)
    assert row is not None
    return DispatchResult(
        dispatch_id=dispatch_id,
        status=row.status,
        dispatch=row,
        created=True,
        same_content=True,
    )


def import_pending(
    db: Session,
    tenant_id: UUID,
    *,
    idempotency_key: str,
    channel: str,
    category: str,
    address: str,
    template_provenance: str,
    available_at: datetime,
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
    headers: Mapping[str, str] | None = None,
    attachments: Sequence[AttachmentRef] = (),
) -> DispatchResult:
    """Carry a legacy pending row forward as a real dispatch (D7).

    The stored content is kept as given (no rendering, no template lookup) and
    `available_at` is preserved, so the outbox event is not due before the legacy
    row was. `template_provenance` must be `legacy:<source>`. Content the S6
    message rules refuse raises `ValueError`; the adopter's command then reports
    that row for manual review rather than altering it.
    """
    _check_provenance(template_provenance, legacy=True)
    return create_dispatch(
        db,
        tenant_id,
        idempotency_key=idempotency_key,
        channel=channel,
        category=category,
        address=address,
        template_provenance=template_provenance,
        subject=subject,
        text=text,
        html=html,
        headers=headers,
        attachments=attachments,
        available_at=available_at,
    )


def load_dispatch(db: Session, tenant_id: UUID, dispatch_id: UUID) -> LoadedDispatch:
    """Load a dispatch and its verified `OutboundMessage` (for the relay, K-8).

    Refuses: unknown id (`DispatchNotFound`), history-only (`DispatchHistoryOnly`),
    redacted (`DispatchRedacted`), and stored content that does not rebuild into
    its recorded fingerprint (`DispatchIntegrityError`). All are terminal.
    """
    row = db.execute(
        select(CommunicationDispatch).where(
            CommunicationDispatch.tenant_id == tenant_id,
            CommunicationDispatch.dispatch_id == dispatch_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise DispatchNotFound(f"no dispatch {dispatch_id}")
    if row.status == DISPATCH_HISTORY:
        raise DispatchHistoryOnly(f"dispatch {dispatch_id} is history-only")
    if row.redacted_at is not None:
        raise DispatchRedacted(f"dispatch {dispatch_id} bodies were redacted")
    try:
        message = OutboundMessage(
            dispatch_id=row.dispatch_id,
            channel=row.channel,
            address=row.address or "",
            body=None,
            category=row.category,
            subject=row.subject,
            text=row.text_body,
            html=row.html_body,
            headers=row.headers or None,
            attachments=tuple(AttachmentRef(**a) for a in (row.attachments or ())),
        )
    except (ValueError, TypeError) as exc:
        raise DispatchIntegrityError(
            f"dispatch {dispatch_id} content no longer builds a valid message: {exc}"
        ) from None
    if message.request_fingerprint != row.content_fingerprint:
        raise DispatchIntegrityError(
            f"dispatch {dispatch_id} content does not match its fingerprint"
        )
    return LoadedDispatch(dispatch=row, message=message)


__all__ = [
    "DISPATCH_EVENT_TYPE",
    "DispatchError",
    "DispatchHistoryOnly",
    "DispatchIntegrityError",
    "DispatchNotFound",
    "DispatchRedacted",
    "DispatchResult",
    "LoadedDispatch",
    "create_dispatch",
    "find_dispatch_by_key",
    "import_history_key",
    "import_pending",
    "load_dispatch",
]
