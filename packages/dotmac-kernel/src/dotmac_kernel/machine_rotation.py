"""Issuing, rotating and revoking a machine credential — the WRITE side.

Separate module from `machine_auth` on purpose. That module's contract is "one
SELECT and nothing else", and its docstring says so; putting a mutation next to
it is how the authentication-time write both source products carry got there in
the first place. A reader who opens `machine_auth` should find nothing that
writes, and a reviewer of a diff to it should be suspicious of one that does.

## Rotation is a WINDOW, and the window closes when an operator says so

Sub rotates in place: `web_system_api_key_mutations.rotate_api_key` generates a
new secret, overwrites `key_hash`, and — its own docstring — "the old secret
stops working immediately". Every caller still holding the previous key fails
from that instant until somebody redeploys it. For a human with a UI in front of
them that is an inconvenience; for the unattended machine callers this table
exists to serve it is an outage, and the operator who caused it usually finds
out from the other application's error rate.

So a rotation here has three explicit steps and no fourth implicit one:

    begin_rotation(...)     -> the NEW raw key, returned once.
                               Both secrets now authenticate.
    (the caller deploys the new key at its own pace)
    complete_rotation(...)  -> the OLD secret stops working. Now, because
                               somebody said so.

or, if the migration is abandoned:

    cancel_rotation(...)    -> the NEW secret stops working; the old one was
                               never disturbed, so nothing failed.

**There is no TTL and no scheduler.** Nothing in this module, in the model, or
in the authentication path retires the outgoing secret on a timer.
`rotation_started_at` is recorded so "how long has this been half-rotated" is a
query rather than a memory, and it is deliberately NOT an input to any
authentication decision — the moment it were, the window would close itself
while a caller was still holding a key that used to work, which is the failure
mode the window exists to prevent. Ageing windows are an operational report,
and reporting them is the product's job.

## Which key material this module handles, and where it goes

`issue_credential` and `begin_rotation` generate a raw key with
`secrets.token_urlsafe` and RETURN it. That is the only moment it exists in a
readable form: only the digest is stored, and there is no path that recovers the
raw key from the row. A caller therefore has exactly one chance to deliver it to
whoever needs it — which is a property, not an inconvenience, and it is why
adoption of this facility is credential reissuance rather than a hash migration
(`docs/inventories/machine-credential-sources.md`).

The returned value is never logged here, never put in an exception message, and
never written to the row in any form but the HMAC digest.

## Transaction contract

Every function takes a `Session`, `add`/mutates, and `flush`es. No commit, no
rollback — hard rule 8 keeps the transaction owner at the request boundary.
Same add-and-flush contract as `dotmac_kernel.audit.write_audit_event`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.machine_auth import hash_machine_key
from dotmac_kernel.machine_models import MachineCredential
from dotmac_kernel.source_applications import (
    active_source_applications,
    validate_source_application,
)

#: Bytes of entropy behind a generated key. `token_urlsafe(32)` is 256 bits,
#: matching what Sub already generates, so an operator moving between the two
#: is not silently given a weaker secret.
_KEY_BYTES = 32


class RotationStateError(RuntimeError):
    """A rotation step was asked for in a state that cannot mean what it says.

    Deliberately loud rather than idempotent-by-tolerance. "Complete a rotation
    that was never begun" and "begin a second rotation while one is open" are
    both operator confusion about which secret is currently live, and silently
    succeeding would leave them confident about the wrong answer.
    """


def _validate_scopes(scopes: tuple[str, ...] | list[str]) -> list[str]:
    """Scope strings must be trimmed and non-empty; the LIST may be empty.

    The asymmetry is the point. An empty scope list is a legitimate, meaningful
    credential — it authorizes nothing — whereas `""` or `" billing:read"` as a
    scope is a typo that would silently never match anything, because matching
    is exact.
    """
    cleaned: list[str] = []
    for scope in scopes:
        if not isinstance(scope, str) or not scope or scope.strip() != scope:
            raise ValueError(
                f"scope {scope!r} must be a non-empty, trimmed key. Matching is "
                "EXACT, so an untrimmed scope would never authorize anything "
                "and would look granted in the row."
            )
        if scope in cleaned:
            raise ValueError(f"scope {scope!r} is listed twice")
        cleaned.append(scope)
    return cleaned


def issue_credential(
    db: Session,
    *,
    tenant_id: UUID,
    label: str,
    source_application: str,
    scopes: tuple[str, ...] | list[str],
    expires_at: datetime | None = None,
) -> tuple[MachineCredential, str]:
    """Mint a credential and return it with its raw key — the ONLY time that
    value exists in readable form.

    `source_application` is required and is checked against the deployment's
    installed registry: a credential that cannot say whose it is may not be
    created, which is the write-side half of `authenticate_machine` refusing to
    authenticate one. There is no "unknown" or "system" value to pass.

    `scopes` may be empty, and such a credential authorizes nothing at all.
    That is a real thing to want — a caller that must authenticate before its
    permissions are decided — and it is emphatically NOT ERP's empty-means-
    everything.
    """
    validate_source_application(source_application)
    active_source_applications().require(source_application)
    cleaned_scopes = _validate_scopes(scopes)
    if not label or label.strip() != label:
        raise ValueError(f"label {label!r} must be non-empty and trimmed")

    raw_key = secrets.token_urlsafe(_KEY_BYTES)
    credential = MachineCredential(
        tenant_id=tenant_id,
        label=label,
        source_application=source_application,
        key_hash=hash_machine_key(raw_key),
        scopes=cleaned_scopes,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(credential)
    db.flush()
    return credential, raw_key


def begin_rotation(
    db: Session, credential: MachineCredential, *, now: datetime | None = None
) -> str:
    """Open a rotation window and return the INCOMING raw key, once.

    Both secrets authenticate from here until `complete_rotation`. The caller's
    old key keeps working, so it can pick up the new one on its own deployment
    schedule instead of on this transaction's.

    Refuses when a window is already open: two incoming keys would mean nobody
    can say which secret `complete_rotation` is about to make canonical.
    """
    moment = now or datetime.now(UTC)
    if credential.next_key_hash is not None:
        raise RotationStateError(
            f"credential {credential.label!r} is already rotating (window "
            f"opened {credential.rotation_started_at!r}). Complete or cancel "
            "that rotation before starting another — a second incoming key "
            "would make 'which secret is next' unanswerable."
        )
    if not credential.is_active or credential.revoked_at is not None:
        raise RotationStateError(
            f"credential {credential.label!r} is revoked or inactive; rotating "
            "it would produce a secret that cannot authenticate. Issue a new "
            "credential instead."
        )

    raw_key = secrets.token_urlsafe(_KEY_BYTES)
    digest = hash_machine_key(raw_key)
    # A generated key colliding with a live digest in this tenant is not
    # credible at 256 bits, but the DB constraints that would catch it fire as
    # an opaque IntegrityError at flush — inside the caller's transaction, and
    # long after the raw key was handed back. Checking here fails before any
    # secret is returned, so the caller never believes it holds a working key.
    clash = db.execute(
        select(MachineCredential.id).where(
            MachineCredential.tenant_id == credential.tenant_id,
            MachineCredential.key_hash == digest,
        )
    ).first()
    if clash is not None:
        raise RotationStateError(
            "the generated key collides with an existing credential in this "
            "tenant; retry the rotation"
        )

    credential.next_key_hash = digest
    credential.rotation_started_at = moment
    db.flush()
    return raw_key


def complete_rotation(
    db: Session, credential: MachineCredential, *, now: datetime | None = None
) -> MachineCredential:
    """Close the window: the incoming secret becomes the only one that works.

    This is the step that makes the OLD key stop working, and it happens here
    and nowhere else. Nothing calls it on a schedule.
    """
    moment = now or datetime.now(UTC)
    if credential.next_key_hash is None:
        raise RotationStateError(
            f"credential {credential.label!r} has no open rotation, so there is "
            "no incoming secret to promote. Completing nothing would report "
            "success for a rotation that never happened."
        )
    credential.key_hash = credential.next_key_hash
    credential.next_key_hash = None
    credential.rotation_started_at = None
    credential.rotated_at = moment
    db.flush()
    return credential


def cancel_rotation(db: Session, credential: MachineCredential) -> MachineCredential:
    """Abandon the window: the INCOMING secret stops working, the old one stays.

    The safe direction. Nothing that was working stops working, which is why
    this is the correct response to "we are not sure the new key reached the
    caller" — unlike completing, which is irreversible for anyone still holding
    the old one.
    """
    if credential.next_key_hash is None:
        raise RotationStateError(
            f"credential {credential.label!r} has no open rotation to cancel"
        )
    credential.next_key_hash = None
    credential.rotation_started_at = None
    db.flush()
    return credential


def revoke_credential(
    db: Session, credential: MachineCredential, *, now: datetime | None = None
) -> MachineCredential:
    """Stop BOTH secrets immediately — including a half-rotated incoming one.

    Clearing `next_key_hash` here is not tidiness. A revoked credential whose
    incoming digest survived would leave a secret in the row that an operator
    reading it cannot tell is dead, and any future change that relaxed the
    `is_active` predicate would resurrect it.
    """
    moment = now or datetime.now(UTC)
    credential.is_active = False
    credential.revoked_at = moment
    credential.next_key_hash = None
    credential.rotation_started_at = None
    db.flush()
    return credential


__all__ = [
    "RotationStateError",
    "begin_rotation",
    "cancel_rotation",
    "complete_rotation",
    "issue_credential",
    "revoke_credential",
]
