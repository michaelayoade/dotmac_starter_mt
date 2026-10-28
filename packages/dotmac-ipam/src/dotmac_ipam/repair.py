"""Fingerprinted, idempotent preview -> apply repair, IPv6 link and unlink.

Extracted from Sub's ``ip_assignment_lifecycle`` (incl. #3812's IPv6 link and
ownership unlink). Everything Sub knew about subscriptions (serviceable,
terminal, retention, live use, served column, owning subscriber) arrives as
caller-owned :class:`RepairFacts` and is part of the fingerprint, so a changed
fact makes a stale preview refuse. Nothing here reads sessions, NAS,
subscriptions or devices, and nothing commits.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from dotmac_ipam import _common as c
from dotmac_ipam.allocator import _mark_primary
from dotmac_ipam.contracts import (
    EVENT_ASSIGNMENT_RELEASED,
    EVENT_ASSIGNMENT_REPAIRED,
    ActiveAssignmentEvidence,
    ApplyIPv6Link,
    ApplyRepair,
    ApplyUnlink,
    LinkOutcome,
    LinkPreview,
    RepairDecision,
    RepairFacts,
    RepairOutcome,
    RepairPreview,
    UnlinkOutcome,
    UnlinkPreview,
)
from dotmac_ipam.errors import (
    IpamConflict,
    IpamError,
    IpamNotFound,
    StalePreview,
    UnsafeRepair,
)
from dotmac_ipam.guard import command
from dotmac_ipam.models import IPAssignment, IPv4Address, IPv6Address

D = RepairDecision
_MAX_KEY = 120


def _facts_payload(facts: RepairFacts) -> dict[str, Any]:
    return {
        "subscription_exists": facts.subscription_exists,
        "subscriber_exists": facts.subscriber_exists,
        "serviceable": facts.serviceable,
        "terminal": facts.terminal,
        "retention_release": facts.retention_release,
        "retention_expired": facts.retention_expired,
        "live_use_addresses": sorted(facts.live_use_addresses),
        "served_address": facts.served_address,
        "live_session_present": facts.live_session_present,
        "subscription_subscriber_id": (
            str(facts.subscription_subscriber_id)
            if facts.subscription_subscriber_id is not None
            else None
        ),
    }


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_payload(
    items: tuple[ActiveAssignmentEvidence, ...],
) -> list[dict[str, str | None]]:
    return [
        {
            "assignment_id": str(i.assignment_id),
            "subscriber_id": str(i.subscriber_id) if i.subscriber_id else None,
            "subscription_id": str(i.subscription_id) if i.subscription_id else None,
            "address_id": str(i.address_id),
            "address": i.address,
        }
        for i in items
    ]


def _idem_ref(kind: str, key: str) -> str:
    cleaned = key.strip()
    if not cleaned:
        raise IpamError(
            "an idempotency key is required", code="ipam.missing_idempotency_key"
        )
    if len(cleaned) > _MAX_KEY:
        raise IpamError("idempotency key is too long", code="ipam.bad_idempotency_key")
    return f"idempotency:{kind}:{cleaned}"


def _prior(
    db: Session, tenant_id: UUID, ref: str, fingerprint: str
) -> dict[str, Any] | None:
    from dotmac_ipam.models import IpamEvent

    event = db.scalar(
        select(IpamEvent).where(
            IpamEvent.tenant_id == tenant_id, IpamEvent.aggregate_ref == ref
        )
    )
    if event is None:
        return None
    payload = dict(event.payload or {})
    if not secrets.compare_digest(
        str(payload.get("preview_fingerprint", "")), fingerprint
    ):
        raise IpamConflict(
            "the idempotency key was already used for different IPAM evidence",
            code="ipam.idempotency_conflict",
        )
    return payload


def _evidence(
    db: Session,
    tenant_id: UUID,
    *,
    family: str,
    subscriber_id: UUID,
    desired_address_id: UUID | None,
) -> tuple[ActiveAssignmentEvidence, ...]:
    addr_model: Any
    if family == c.IPV4:
        addr_model, fk = IPv4Address, IPAssignment.ipv4_address_id
    else:
        addr_model, fk = IPv6Address, IPAssignment.ipv6_address_id
    ownership = IPAssignment.subscriber_id == subscriber_id
    if desired_address_id is not None:
        ownership = or_(ownership, fk == desired_address_id)
    rows = db.execute(
        select(IPAssignment, addr_model.address)
        .join(
            addr_model,
            (addr_model.tenant_id == IPAssignment.tenant_id) & (fk == addr_model.id),
        )
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.is_active.is_(True),
            IPAssignment.ip_version == family,
            ownership,
        )
        .order_by(IPAssignment.id)
    ).all()
    return tuple(
        ActiveAssignmentEvidence(
            assignment_id=a.id,
            subscriber_id=a.subscriber_id,
            subscription_id=a.subscription_id,
            address_id=a.ipv4_address_id if family == c.IPV4 else a.ipv6_address_id,
            address=c.bare(address),
        )
        for a, address in rows
    )


def _retention_decision(
    db: Session,
    tenant_id: UUID,
    *,
    exact_rows: list[ActiveAssignmentEvidence],
    facts: RepairFacts,
) -> RepairDecision:
    if not facts.retention_expired:
        return D.RETENTION_NOT_EXPIRED
    for address_id in {item.address_id for item in exact_rows}:
        row = db.scalar(
            select(IPv4Address).where(
                IPv4Address.tenant_id == tenant_id, IPv4Address.id == address_id
            )
        )
        if (
            row is None
            or row.device_ref is not None
            or row.is_reserved
            or str(row.allocation_type or "").strip().lower()
            in c.NON_SERVICE_ALLOCATION_TYPES
        ):
            return D.ADDRESS_NOT_RELEASABLE
    addresses = {item.address for item in exact_rows}
    if {c.bare(v) for v in facts.live_use_addresses} & addresses:
        return D.ADDRESS_IN_LIVE_USE
    return D.READY_RELEASE_RETENTION_EXPIRED


def preview_repair(
    db: Session,
    *,
    tenant_id: UUID,
    subscription_id: UUID,
    subscriber_id: UUID | None,
    desired_address_id: UUID | None,
    facts: RepairFacts,
    deactivate_assignment_ids: tuple[UUID, ...] = (),
    retention_cutoff: datetime | None = None,
) -> RepairPreview:
    """Read-only, exact-service plan for one reviewed IPv4 assignment repair."""
    ids = tuple(sorted(set(deactivate_assignment_ids), key=str))
    desired_address: str | None = None
    active: tuple[ActiveAssignmentEvidence, ...] = ()
    decision = D.SUBSCRIPTION_NOT_FOUND

    if not facts.subscription_exists:
        pass
    elif subscriber_id is None or not facts.subscriber_exists:
        decision = D.SUBSCRIBER_NOT_FOUND
    else:
        active = _evidence(
            db,
            tenant_id,
            family=c.IPV4,
            subscriber_id=subscriber_id,
            desired_address_id=desired_address_id,
        )
        by_id = {item.assignment_id: item for item in active}
        requested = [by_id.get(value) for value in ids]
        if any(item is None for item in requested):
            decision = D.DEACTIVATION_ASSIGNMENT_NOT_FOUND
        elif any(
            item is not None and item.subscription_id != subscription_id
            for item in requested
        ):
            decision = D.DEACTIVATION_NOT_EXACT_SERVICE
        elif desired_address_id is None:
            exact = [i for i in active if i.subscription_id == subscription_id]
            remaining = [i for i in exact if i.assignment_id not in ids]
            retention_release = retention_cutoff is not None and facts.retention_release
            if not facts.terminal and not retention_release:
                decision = D.SUBSCRIPTION_NOT_TERMINAL
            elif remaining:
                decision = D.INCOMPLETE_DEACTIVATION_SET
            elif not exact:
                decision = D.NOOP
            elif retention_release:
                decision = _retention_decision(
                    db, tenant_id, exact_rows=exact, facts=facts
                )
            else:
                decision = D.READY_RELEASE
        else:
            target = db.scalar(
                select(IPv4Address).where(
                    IPv4Address.tenant_id == tenant_id,
                    IPv4Address.id == desired_address_id,
                )
            )
            if not facts.serviceable:
                decision = D.SUBSCRIPTION_NOT_SERVICEABLE
            elif target is None:
                decision = D.TARGET_ADDRESS_NOT_FOUND
            else:
                desired_address = c.bare(target.address)
                pool = (
                    c.get_pool_or_none(db, tenant_id, target.pool_id)
                    if target.pool_id
                    else None
                )
                target_assignment = next(
                    (i for i in active if i.address_id == desired_address_id), None
                )
                remaining = [
                    i
                    for i in active
                    if i.subscription_id == subscription_id
                    and i.address_id != desired_address_id
                    and i.assignment_id not in ids
                ]
                if not c.v4_is_serviceable(target, pool):
                    decision = D.TARGET_ADDRESS_NOT_SERVICEABLE
                elif c.in_routed_block(db, tenant_id, desired_address):
                    decision = D.TARGET_ADDRESS_IN_ROUTED_BLOCK
                elif target_assignment is not None and (
                    target_assignment.subscriber_id != subscriber_id
                    or target_assignment.subscription_id not in {None, subscription_id}
                ):
                    decision = D.TARGET_OWNED_BY_OTHER_SERVICE
                elif (
                    target_assignment is not None
                    and target_assignment.assignment_id in ids
                ):
                    decision = D.DESIRED_ASSIGNMENT_SELECTED_FOR_DEACTIVATION
                elif remaining:
                    decision = D.INCOMPLETE_DEACTIVATION_SET
                elif target_assignment is None:
                    decision = D.READY_CREATE
                elif target_assignment.subscription_id is None:
                    decision = D.READY_LINK
                elif ids:
                    decision = D.READY_KEEP
                else:
                    decision = D.NOOP

    payload: dict[str, object] = {
        "subscription_id": str(subscription_id),
        "subscriber_id": str(subscriber_id) if subscriber_id else None,
        "desired_address_id": str(desired_address_id) if desired_address_id else None,
        "desired_address": desired_address,
        "deactivate_assignment_ids": [str(v) for v in ids],
        "active_assignments": _evidence_payload(active),
        "decision": decision.value,
        "facts": _facts_payload(facts),
    }
    if retention_cutoff is not None:
        payload["retention_cutoff"] = c.aware(retention_cutoff).isoformat()
    return RepairPreview(
        subscription_id=subscription_id,
        subscriber_id=subscriber_id,
        desired_address_id=desired_address_id,
        desired_address=desired_address,
        deactivate_assignment_ids=ids,
        active_assignments=active,
        decision=decision,
        fingerprint=_fingerprint(payload),
    )


def _lock_nowait(db: Session, tenant_id: UUID, address_ids: list[UUID]) -> None:
    try:
        db.scalars(
            select(IPv4Address)
            .where(IPv4Address.tenant_id == tenant_id, IPv4Address.id.in_(address_ids))
            .order_by(IPv4Address.id)
            .with_for_update(nowait=True)
        ).all()
    except OperationalError as exc:
        if "55P03" not in str(getattr(exc.orig, "sqlstate", "")) and (
            "could not obtain lock" not in str(exc.orig).lower()
        ):
            raise
        raise IpamConflict(
            "a released address is locked by another writer; retry later",
            code="ipam.address_lock_unavailable",
        ) from exc


@command
def apply_repair(
    db: Session, *, tenant_id: UUID, command: ApplyRepair
) -> RepairOutcome:
    ref = _idem_ref("assignment_repair", command.idempotency_key)
    if len(set(command.deactivate_assignment_ids)) != len(
        command.deactivate_assignment_ids
    ):
        raise IpamError(
            "the deactivation cohort repeats an assignment",
            code="ipam.duplicate_assignment",
        )

    def replay(prior: dict[str, Any]) -> RepairOutcome:
        desired = prior.get("desired_assignment_id")
        return RepairOutcome(
            subscription_id=UUID(str(prior["subscription_id"])),
            desired_assignment_id=UUID(str(desired)) if desired else None,
            linked_count=int(prior.get("linked_count") or 0),
            created_count=int(prior.get("created_count") or 0),
            deactivated_count=int(prior.get("deactivated_count") or 0),
            preview_fingerprint=str(prior["preview_fingerprint"]),
            replayed=True,
        )

    prior = _prior(db, tenant_id, ref, command.preview_fingerprint)
    if prior is not None:
        return replay(prior)

    desired_address = None
    desired_pool = None
    if command.desired_address_id is not None:
        desired_address = db.scalar(
            select(IPv4Address)
            .where(
                IPv4Address.tenant_id == tenant_id,
                IPv4Address.id == command.desired_address_id,
            )
            .with_for_update()
        )
        if desired_address is not None and desired_address.pool_id is not None:
            desired_pool = c.get_pool(db, tenant_id, desired_address.pool_id, lock=True)
    lock_filter = IPAssignment.subscriber_id == command.subscriber_id
    if command.desired_address_id is not None:
        lock_filter = or_(
            lock_filter, IPAssignment.ipv4_address_id == command.desired_address_id
        )
    locked = list(
        db.scalars(
            select(IPAssignment)
            .where(IPAssignment.tenant_id == tenant_id, lock_filter)
            .order_by(IPAssignment.id)
            .with_for_update()
        ).all()
    )
    if command.desired_address_id is None:
        released_ids = [
            r.ipv4_address_id
            for r in locked
            if r.is_active and r.ip_version == c.IPV4 and r.ipv4_address_id
        ]
        if released_ids:
            _lock_nowait(db, tenant_id, released_ids)

    prior = _prior(db, tenant_id, ref, command.preview_fingerprint)
    if prior is not None:
        return replay(prior)

    current = preview_repair(
        db,
        tenant_id=tenant_id,
        subscription_id=command.subscription_id,
        subscriber_id=command.subscriber_id,
        desired_address_id=command.desired_address_id,
        facts=command.facts,
        deactivate_assignment_ids=command.deactivate_assignment_ids,
        retention_cutoff=command.retention_cutoff,
    )
    if not secrets.compare_digest(current.fingerprint, command.preview_fingerprint):
        raise StalePreview(
            "IPAM lifecycle evidence changed after preview; preview again"
        )
    if not current.applicable:
        raise UnsafeRepair(
            "the reviewed IPAM lifecycle repair is no longer safe "
            f"({current.decision.value})"
        )

    at = c.now()
    active_by_id = {a.id: a for a in locked if a.is_active}
    deactivated = 0
    for assignment_id in current.deactivate_assignment_ids:
        row = active_by_id[assignment_id]
        row.is_active = False
        row.is_primary = False
        row.released_at = at
        row.release_reason = command.reason or "repair"
        deactivated += 1
        c.event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"subscription:{command.subscription_id}",
            event_type=EVENT_ASSIGNMENT_RELEASED,
            payload={"assignment_id": str(row.id), "reason": "repair"},
            occurred_at=at,
        )
    if deactivated:
        db.flush()

    desired: IPAssignment | None = None
    linked = created = 0
    if command.desired_address_id is not None:
        desired = next(
            (
                a
                for a in locked
                if a.is_active
                and a.ip_version == c.IPV4
                and a.ipv4_address_id == command.desired_address_id
            ),
            None,
        )
        if desired is None:
            if desired_address is None:
                raise UnsafeRepair("the reviewed target address no longer exists")
            desired = IPAssignment(
                tenant_id=tenant_id,
                subscriber_id=command.subscriber_id,
                subscription_id=command.subscription_id,
                ip_version=c.IPV4,
                ipv4_address_id=desired_address.id,
                prefix_length=c.prefix_length_of(desired_pool),
                gateway=desired_pool.gateway if desired_pool else None,
                dns_primary=desired_pool.dns_primary if desired_pool else None,
                dns_secondary=desired_pool.dns_secondary if desired_pool else None,
                is_active=True,
            )
            db.add(desired)
            db.flush()
            _mark_primary(
                db, tenant_id, command.subscription_id, desired.ipv4_address_id
            )
            created = 1
        elif desired.subscription_id is None:
            desired.subscription_id = command.subscription_id
            db.flush()
            _mark_primary(
                db, tenant_id, command.subscription_id, desired.ipv4_address_id
            )
            linked = 1

    payload = {
        "subscription_id": str(command.subscription_id),
        "desired_assignment_id": str(desired.id) if desired is not None else None,
        "desired_address_id": (
            str(command.desired_address_id) if command.desired_address_id else None
        ),
        "deactivate_assignment_ids": [
            str(v) for v in current.deactivate_assignment_ids
        ],
        "decision": current.decision.value,
        "preview_fingerprint": current.fingerprint,
        "linked_count": linked,
        "created_count": created,
        "deactivated_count": deactivated,
        "reason": command.reason,
    }
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=ref,
        event_type=EVENT_ASSIGNMENT_REPAIRED,
        payload=payload,
        occurred_at=at,
    )
    db.flush()
    return RepairOutcome(
        subscription_id=command.subscription_id,
        desired_assignment_id=desired.id if desired is not None else None,
        linked_count=linked,
        created_count=created,
        deactivated_count=deactivated,
        preview_fingerprint=current.fingerprint,
        replayed=False,
    )


# --- IPv6 service link (#3812) ---------------------------------------------


def preview_ipv6_link(
    db: Session,
    *,
    tenant_id: UUID,
    subscription_id: UUID,
    subscriber_id: UUID | None,
    desired_address_id: UUID,
    facts: RepairFacts,
) -> LinkPreview:
    """Link-only twin of the IPv4 ``ready_link`` branch for IPv6.

    Nothing is created, released or deactivated; ``is_primary`` is never set
    (the primary marker and its index are IPv4-only)."""
    desired_address: str | None = None
    active: tuple[ActiveAssignmentEvidence, ...] = ()
    decision = D.SUBSCRIPTION_NOT_FOUND
    if not facts.subscription_exists:
        pass
    elif subscriber_id is None or not facts.subscriber_exists:
        decision = D.SUBSCRIBER_NOT_FOUND
    else:
        active = _evidence(
            db,
            tenant_id,
            family=c.IPV6,
            subscriber_id=subscriber_id,
            desired_address_id=desired_address_id,
        )
        target = db.scalar(
            select(IPv6Address).where(
                IPv6Address.tenant_id == tenant_id,
                IPv6Address.id == desired_address_id,
            )
        )
        if not facts.serviceable:
            decision = D.SUBSCRIPTION_NOT_SERVICEABLE
        elif target is None:
            decision = D.TARGET_ADDRESS_NOT_FOUND
        else:
            desired_address = c.bare(target.address)
            pool = (
                c.get_pool_or_none(db, tenant_id, target.pool_id)
                if target.pool_id
                else None
            )
            target_assignment = next(
                (i for i in active if i.address_id == desired_address_id), None
            )
            remaining = [
                i
                for i in active
                if i.subscription_id == subscription_id
                and i.address_id != desired_address_id
            ]
            if not c.v6_is_serviceable(target, pool):
                decision = D.TARGET_ADDRESS_NOT_SERVICEABLE
            elif target_assignment is None:
                decision = D.TARGET_ASSIGNMENT_NOT_FOUND
            elif target_assignment.subscriber_id != subscriber_id or (
                target_assignment.subscription_id not in {None, subscription_id}
            ):
                decision = D.TARGET_OWNED_BY_OTHER_SERVICE
            elif remaining:
                decision = D.SERVICE_ALREADY_HAS_IPV6_ASSIGNMENT
            elif target_assignment.subscription_id is None:
                decision = D.READY_LINK
            else:
                decision = D.NOOP
    payload: dict[str, object] = {
        "subscription_id": str(subscription_id),
        "subscriber_id": str(subscriber_id) if subscriber_id else None,
        "desired_address_id": str(desired_address_id),
        "desired_address": desired_address,
        "active_assignments": _evidence_payload(active),
        "decision": decision.value,
        "facts": _facts_payload(facts),
    }
    return LinkPreview(
        subscription_id=subscription_id,
        subscriber_id=subscriber_id,
        desired_address_id=desired_address_id,
        desired_address=desired_address,
        active_assignments=active,
        decision=decision,
        fingerprint=_fingerprint(payload),
    )


@command
def apply_ipv6_link(
    db: Session, *, tenant_id: UUID, command: ApplyIPv6Link
) -> LinkOutcome:
    ref = _idem_ref("ipv6_link", command.idempotency_key)

    def replay(prior: dict[str, Any]) -> LinkOutcome:
        desired = prior.get("desired_assignment_id")
        return LinkOutcome(
            subscription_id=command.subscription_id,
            desired_assignment_id=UUID(str(desired)) if desired else None,
            linked_count=int(prior.get("linked_count") or 0),
            preview_fingerprint=str(prior["preview_fingerprint"]),
            replayed=True,
        )

    prior = _prior(db, tenant_id, ref, command.preview_fingerprint)
    if prior is not None:
        return replay(prior)
    db.scalar(
        select(IPv6Address)
        .where(
            IPv6Address.tenant_id == tenant_id,
            IPv6Address.id == command.desired_address_id,
        )
        .with_for_update()
    )
    locked = db.scalars(
        select(IPAssignment)
        .where(
            IPAssignment.tenant_id == tenant_id,
            or_(
                IPAssignment.subscriber_id == command.subscriber_id,
                IPAssignment.ipv6_address_id == command.desired_address_id,
            ),
        )
        .order_by(IPAssignment.id)
        .with_for_update()
    ).all()
    current = preview_ipv6_link(
        db,
        tenant_id=tenant_id,
        subscription_id=command.subscription_id,
        subscriber_id=command.subscriber_id,
        desired_address_id=command.desired_address_id,
        facts=command.facts,
    )
    if not secrets.compare_digest(current.fingerprint, command.preview_fingerprint):
        raise StalePreview("IPAM link evidence changed after preview; preview again")
    if not current.applicable:
        raise UnsafeRepair(
            f"the reviewed IPv6 link is no longer safe ({current.decision.value})"
        )
    target = next(
        a
        for a in locked
        if a.is_active
        and a.ip_version == c.IPV6
        and a.ipv6_address_id == command.desired_address_id
    )
    target.subscription_id = command.subscription_id
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=ref,
        event_type=EVENT_ASSIGNMENT_REPAIRED,
        payload={
            "subscription_id": str(command.subscription_id),
            "desired_assignment_id": str(target.id),
            "decision": current.decision.value,
            "preview_fingerprint": current.fingerprint,
            "linked_count": 1,
            "reason": command.reason,
        },
    )
    db.flush()
    return LinkOutcome(
        subscription_id=command.subscription_id,
        desired_assignment_id=target.id,
        linked_count=1,
        preview_fingerprint=current.fingerprint,
        replayed=False,
    )


# --- ownership unlink (#3812; the inverse of link) --------------------------


def preview_unlink(
    db: Session,
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    expected_subscription_id: UUID,
    facts: RepairFacts,
) -> UnlinkPreview:
    """Read-only plan to undo one exact service link (IPv4 or IPv6).

    Served-column and live-session evidence is fingerprint-bound for evidence
    only: it never makes the preview inapplicable, so rollback stays possible."""
    row = db.scalar(
        select(IPAssignment).where(
            IPAssignment.tenant_id == tenant_id, IPAssignment.id == assignment_id
        )
    )
    subscriber_id = row.subscriber_id if row else None
    current_sub = row.subscription_id if row else None
    address_id: UUID | None = None
    address: str | None = None
    decision = D.ASSIGNMENT_NOT_FOUND
    if row is not None:
        address_row = (
            db.scalar(
                select(IPv6Address).where(
                    IPv6Address.tenant_id == tenant_id,
                    IPv6Address.id == row.ipv6_address_id,
                )
            )
            if row.ip_version == c.IPV6
            else db.scalar(
                select(IPv4Address).where(
                    IPv4Address.tenant_id == tenant_id,
                    IPv4Address.id == row.ipv4_address_id,
                )
            )
        )
        address_id = address_row.id if address_row else None
        address = c.bare(address_row.address) if address_row else None
        if not row.is_active:
            decision = D.ASSIGNMENT_NOT_ACTIVE
        elif current_sub is None:
            decision = D.ASSIGNMENT_NOT_LINKED
        elif current_sub != expected_subscription_id:
            decision = D.SUBSCRIPTION_MISMATCH
        elif not facts.subscription_exists:
            decision = D.SUBSCRIPTION_NOT_FOUND
        elif facts.subscription_subscriber_id != subscriber_id:
            decision = D.SUBSCRIBER_MISMATCH
        else:
            decision = D.READY_UNLINK
    served = c.bare(facts.served_address) or None
    matches = served is not None and served == address
    is_primary = bool(row.is_primary) if row else False
    payload: dict[str, object] = {
        "assignment_id": str(assignment_id),
        "subscriber_id": str(subscriber_id) if subscriber_id else None,
        "current_subscription_id": str(current_sub) if current_sub else None,
        "expected_subscription_id": str(expected_subscription_id),
        "ip_version": row.ip_version if row else None,
        "address_id": str(address_id) if address_id else None,
        "address": address,
        "served_address": served,
        "served_address_matches_assignment": matches,
        "live_session_present": facts.live_session_present,
        "is_primary": is_primary,
        "decision": decision.value,
        "facts": _facts_payload(facts),
    }
    return UnlinkPreview(
        assignment_id=assignment_id,
        subscriber_id=subscriber_id,
        current_subscription_id=current_sub,
        expected_subscription_id=expected_subscription_id,
        ip_version=row.ip_version if row else None,
        address_id=address_id,
        address=address,
        served_address=served,
        served_address_matches_assignment=matches,
        live_session_present=facts.live_session_present,
        is_primary=is_primary,
        decision=decision,
        fingerprint=_fingerprint(payload),
    )


@command
def apply_unlink(
    db: Session, *, tenant_id: UUID, command: ApplyUnlink
) -> UnlinkOutcome:
    """Rollback only: clear the service link and the primary marker the link
    set. Never deactivates the address, never re-promotes a sibling."""
    ref = _idem_ref("ownership_unlink", command.idempotency_key)
    prior = _prior(db, tenant_id, ref, command.preview_fingerprint)
    if prior is not None:
        previous = prior.get("previous_subscription_id")
        return UnlinkOutcome(
            assignment_id=command.assignment_id,
            previous_subscription_id=UUID(str(previous)) if previous else None,
            preview_fingerprint=str(prior["preview_fingerprint"]),
            replayed=True,
        )
    row = db.scalar(
        select(IPAssignment)
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.id == command.assignment_id,
        )
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("a reviewed IP assignment no longer exists")
    current = preview_unlink(
        db,
        tenant_id=tenant_id,
        assignment_id=command.assignment_id,
        expected_subscription_id=command.expected_subscription_id,
        facts=command.facts,
    )
    if not secrets.compare_digest(current.fingerprint, command.preview_fingerprint):
        raise StalePreview(
            "IPAM ownership evidence changed after preview; preview again"
        )
    if not current.applicable:
        raise UnsafeRepair(
            "the reviewed assignment is no longer safe to unlink "
            f"({current.decision.value})"
        )
    previous = row.subscription_id
    row.subscription_id = None
    row.is_primary = False
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=ref,
        event_type=EVENT_ASSIGNMENT_REPAIRED,
        payload={
            "assignment_id": str(row.id),
            "previous_subscription_id": str(previous) if previous else None,
            "preview_fingerprint": current.fingerprint,
            "unlinked_count": 1,
            "reason": command.reason,
        },
    )
    db.flush()
    return UnlinkOutcome(
        assignment_id=row.id,
        previous_subscription_id=previous,
        preview_fingerprint=current.fingerprint,
        replayed=False,
    )


__all__ = [
    "apply_ipv6_link",
    "apply_repair",
    "apply_unlink",
    "preview_ipv6_link",
    "preview_repair",
    "preview_unlink",
]
