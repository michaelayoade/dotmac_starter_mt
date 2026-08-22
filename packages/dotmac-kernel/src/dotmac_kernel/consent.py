"""The one service that decides whether we may contact someone (ADR-0006 § 5c).

**Owner of:** *may we send `<category>` to `<address>` on `<channel>`?*

Ported from `dotmac_sub:app/services/communication_eligibility.py`, the fleet's
only qualifying implementation. Sub's docstring records why it exists: marketing
eligibility used to be decided inside the campaign segment filter, where opting
in was an *optional checkbox* and there was no unsubscribe ledger at all — so the
answer depended on who was asking, and a customer who unsubscribed from one
sender stayed reachable by every other. Every sender now asks this module, and it
reads one table.

## The distinction that matters

Marketing consent and transactional consent are **not** the same thing, and
collapsing them turns a consent ledger into a billing incident.

An unsubscribe is a refusal of *marketing*. It is not permission to stop sending
someone their invoice, their outage notice, or their service credentials — there
is a contractual and regulatory duty to send those, and a customer who clicked
"unsubscribe" on a promo has not waived it. So a suppression carries a scope:
`marketing` blocks marketing only; `all` blocks everything and is reserved for
addresses that must not be contacted at all.

## Transactional by default, and why the default runs that way

`is_marketing()` is an allowlist: a category is transactional unless a product
has explicitly declared it marketing. Defaulting the other way would make any new
or misspelled category silently suppressible — a typo could stop someone's
invoices. **The failure mode of this default is an unwanted promo; the failure
mode of the opposite default is an unsent invoice.**

## Vocabulary is the product's, the rule is the kernel's

Sub hardcodes `MARKETING_CATEGORIES = {"marketing", "campaign", "promotion"}`.
Those are a product's words. Following ADR-0008 and the same split Template
Studio uses for render contexts, the kernel owns *transactional-unless-declared*
and the product declares which of ITS categories are marketing, via
`register_marketing_categories(...)` at import time.

A deployment that declares nothing gets a ledger where only `all`-scoped
suppressions bite — which is the safe direction: no send is wrongly blocked.

## Transactions

Every mutator FLUSHES and leaves the commit to the caller's session scope —
`dotmac_kernel.db` is the one transaction authority. Sub's four `*_committed`
wrappers were deliberately NOT ported; a service that manages its own
transaction cannot be composed into a caller's unit of work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.consent_models import (
    REASON_UNSUBSCRIBE,
    SCOPE_ALL,
    SCOPE_MARKETING,
    SUPPRESSION_REASONS,
    SUPPRESSION_SCOPES,
    CommunicationSuppression,
)
from dotmac_kernel.db import conflict_savepoint

_DIGITS = re.compile(r"\D+")

#: Channels whose addresses are phone numbers, normalised to digits only.
#: An open set a product extends — the kernel ships the two it can reason about.
_NUMERIC_CHANNELS: set[str] = {"sms", "whatsapp"}

_MARKETING_CATEGORIES: set[str] = set()


class ConsentError(ValueError):
    """A malformed consent operation — never a "not allowed to send" answer."""


# ── The product's vocabulary ────────────────────────────────────────────────


def register_marketing_categories(*categories: str) -> None:
    """Declare which of this product's message categories are MARKETING.

    Everything not declared here is transactional and is not stopped by an
    unsubscribe. Keep the list short and explicit: adding a category makes it
    suppressible, and getting it wrong the other way — marking `billing` as
    marketing — would silently stop sending invoices.
    """
    for category in categories:
        normalised = (category or "").strip().lower()
        if not normalised:
            raise ConsentError("a marketing category needs a name")
        _MARKETING_CATEGORIES.add(normalised)


def registered_marketing_categories() -> frozenset[str]:
    return frozenset(_MARKETING_CATEGORIES)


def is_marketing(category: str | None) -> bool:
    """Transactional unless explicitly declared marketing. See the module docstring."""
    return (category or "").strip().lower() in _MARKETING_CATEGORIES


def register_numeric_channels(*channels: str) -> None:
    """Declare channels whose addresses are phone numbers (digits-only form).

    `sms` and `whatsapp` ship registered. A product adding an SMS-like channel
    registers it here so a suppression cannot be dodged by punctuation.
    """
    for channel in channels:
        normalised = (channel or "").strip().lower()
        if not normalised:
            raise ConsentError("a numeric channel needs a name")
        _NUMERIC_CHANNELS.add(normalised)


def _reset_registries_for_tests(
    *, marketing: Iterable[str] = (), numeric: Iterable[str] = ("sms", "whatsapp")
) -> tuple[frozenset[str], frozenset[str]]:
    """Replace both registries wholesale. Tests only — never product code."""
    previous = (frozenset(_MARKETING_CATEGORIES), frozenset(_NUMERIC_CHANNELS))
    _MARKETING_CATEGORIES.clear()
    _MARKETING_CATEGORIES.update(c.strip().lower() for c in marketing)
    _NUMERIC_CHANNELS.clear()
    _NUMERIC_CHANNELS.update(c.strip().lower() for c in numeric)
    return previous


# ── Channel and address canonicalisation ───────────────────────────────────


def normalize_channel(channel: str | None) -> str:
    """Canonical channel identity used by every ledger read and write."""
    return (channel or "").strip().lower()


def normalize_address(channel: str, address: str | None) -> str:
    """Canonical form of a recipient, so a suppression cannot be dodged.

    `Foo@Bar.com` and `foo@bar.com` are one address; `+234 801 234 5678` and
    `2348012345678` are one number.
    """
    value = (address or "").strip()
    if not value:
        return ""
    if normalize_channel(channel) in _NUMERIC_CHANNELS:
        return _DIGITS.sub("", value)
    return value.lower()


# ── The question ────────────────────────────────────────────────────────────


def may_send(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str | None,
    category: str | None,
) -> bool:
    """The question. One answer, one table, every sender.

    A `marketing`-scoped suppression stops marketing and nothing else; an `all`
    -scoped one stops everything.
    """
    return (
        suppression_reason(
            db, tenant_id, channel=channel, address=address, category=category
        )
        is None
    )


def suppression_reason(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str | None,
    category: str | None,
) -> str | None:
    """The canonical reason this send is blocked, or None when sendable."""
    normalized_channel = normalize_channel(channel)
    normalized = normalize_address(normalized_channel, address)
    if not normalized:
        # No address is a DELIVERY bug, not a consent decision — let the sender
        # fail loudly on its own terms rather than being silently classed as
        # suppressed, which would hide the bug as a consent outcome.
        return None

    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address == normalized,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.scope == SCOPE_ALL or is_marketing(category):
        return row.reason
    return None


def suppression_reasons_for_addresses(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    addresses: Iterable[str],
    category: str | None,
) -> dict[str, str]:
    """Blocked canonical addresses → reason, in ONE query."""
    normalized_channel = normalize_channel(channel)
    normalized = {
        normalize_address(normalized_channel, address)
        for address in addresses
        if normalize_address(normalized_channel, address)
    }
    if not normalized:
        return {}

    rows = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address.in_(normalized),
        )
    ).scalars()
    marketing = is_marketing(category)
    return {
        row.address: row.reason for row in rows if row.scope == SCOPE_ALL or marketing
    }


def filter_eligible(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    addresses: Iterable[str],
    category: str | None,
) -> list[str]:
    """Bulk form, for audience building. Same rule, one query.

    A campaign must not hand-roll this: a per-recipient loop calling `may_send`
    is a second code path that will drift from this one, which is exactly how
    Sub's pre-ledger campaign filter came to disagree with every other sender.
    """
    wanted = {normalize_address(channel, a): a for a in addresses if a}
    if not wanted:
        return []
    blocked = suppression_reasons_for_addresses(
        db, tenant_id, channel=channel, addresses=wanted.values(), category=category
    )
    return [original for norm, original in wanted.items() if norm not in blocked]


# ── Writing the ledger ──────────────────────────────────────────────────────


def suppress(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str,
    scope: str = SCOPE_MARKETING,
    reason: str = REASON_UNSUBSCRIBE,
    party_id: UUID | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> CommunicationSuppression:
    """Record a suppression. Idempotent on `(tenant, channel, address)`.

    Re-suppressing ESCALATES scope (`marketing` → `all`) and never de-escalates:
    a hard bounce must not be downgraded to a marketing-only block by a later
    unsubscribe click.
    """
    if scope not in SUPPRESSION_SCOPES:
        raise ConsentError(
            f"unknown suppression scope {scope!r} — expected one of "
            f"{', '.join(SUPPRESSION_SCOPES)}"
        )
    if reason not in SUPPRESSION_REASONS:
        raise ConsentError(
            f"unknown suppression reason {reason!r} — expected one of "
            f"{', '.join(SUPPRESSION_REASONS)}"
        )
    normalized_channel = normalize_channel(channel)
    if not normalized_channel:
        raise ConsentError("cannot suppress on an empty channel")
    normalized = normalize_address(normalized_channel, address)
    if not normalized:
        raise ConsentError("cannot suppress an empty address")

    def lookup() -> CommunicationSuppression | None:
        return db.execute(
            select(CommunicationSuppression).where(
                CommunicationSuppression.tenant_id == tenant_id,
                CommunicationSuppression.channel == normalized_channel,
                CommunicationSuppression.address == normalized,
            )
        ).scalar_one_or_none()

    def escalate(existing: CommunicationSuppression) -> CommunicationSuppression:
        if existing.scope == SCOPE_MARKETING and scope == SCOPE_ALL:
            existing.scope = scope
            existing.reason = reason
            existing.note = note or existing.note
            # Persist the escalation. Sub carries a scar here: without the
            # flush the mutation lived only in the Session, the row stayed
            # `marketing`, and invoices resumed to an address that hard-bounced.
            db.flush()
        return existing

    existing = lookup()
    if existing is not None:
        return escalate(existing)

    row = CommunicationSuppression(
        tenant_id=tenant_id,
        channel=normalized_channel,
        address=normalized,
        raw_address=address,
        party_id=party_id,
        scope=scope,
        reason=reason,
        note=note,
        created_by=created_by,
    )
    try:
        # Provider callbacks and imports can race. The unique constraint picks
        # one winner while the savepoint keeps the caller's outer transaction
        # (and its SET LOCAL tenant context) usable for replay.
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError:
        winner = lookup()
        if winner is None:
            raise
        return escalate(winner)
    return row


def unsuppress(db: Session, tenant_id: UUID, *, channel: str, address: str) -> bool:
    """Remove any suppression for an address (re-subscribe). True if one went.

    Deletes an `all`-scoped row too, so this is the operator-authority form —
    see `unsuppress_marketing` for the one campaign administration may call.
    """
    normalized_channel = normalize_channel(channel)
    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address
            == normalize_address(normalized_channel, address),
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def unsuppress_marketing(
    db: Session, tenant_id: UUID, *, channel: str, address: str
) -> bool:
    """Remove only a `marketing`-scoped suppression.

    Campaign administration is not authority to clear a hard bounce, complaint or
    erasure row, whose `all` scope also protects transactional delivery.
    """
    normalized_channel = normalize_channel(channel)
    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address
            == normalize_address(normalized_channel, address),
        )
    ).scalar_one_or_none()
    if row is None or row.scope != SCOPE_MARKETING:
        return False
    db.delete(row)
    db.flush()
    return True


def list_suppressions(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str | None = None,
    scope: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CommunicationSuppression]:
    stmt = select(CommunicationSuppression).where(
        CommunicationSuppression.tenant_id == tenant_id
    )
    if channel is not None:
        stmt = stmt.where(
            CommunicationSuppression.channel == normalize_channel(channel)
        )
    if scope is not None:
        stmt = stmt.where(CommunicationSuppression.scope == scope)
    return list(
        db.execute(
            stmt.order_by(CommunicationSuppression.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )


__all__ = [
    "ConsentError",
    "filter_eligible",
    "is_marketing",
    "list_suppressions",
    "may_send",
    "normalize_address",
    "normalize_channel",
    "register_marketing_categories",
    "register_numeric_channels",
    "registered_marketing_categories",
    "suppress",
    "suppression_reason",
    "suppression_reasons_for_addresses",
    "unsuppress",
    "unsuppress_marketing",
]
