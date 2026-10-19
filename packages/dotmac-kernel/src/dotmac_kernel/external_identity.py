"""Bind a VERIFIED external subject to a local `Party` — and nothing else.

This module is the local half of federated login. Something else does the
protocol: discovery, JWKS, PKCE, signature and claim validation. By the time a
caller arrives here, that work is done and the question is narrow:

    this provider registration says it authenticated subject S at issuer I —
    which local party is that, in this tenant?

Answering it takes a row lookup, and refusing to answer it is the common case.

## Two entry points, and only one of them may end in a session

`resolve_external_identity` is a READ. It answers the question and changes
nothing — the right call for an admin screen, a support lookup, or any caller
that wants to know without deciding.

`finalize_external_login` is the LOGIN path, and it is the only one. It takes
the binding under a row lock, re-checks the state that would refuse the login
while holding that lock, stamps `last_authenticated_at`, and hands back the
party so the caller mints its session in the SAME transaction with the row
still held.

## The window a read-then-issue login leaves open

    resolve_external_identity(...)      # binding is active, party is a person
    ── an administrator disables the binding, and commits ──
    db.add(AuthSession(...))            # a session for a revoked identity

The disable looks entirely successful: the row is inactive and every later
resolution refuses. Meanwhile the session it existed to prevent was minted
after it committed and outlives it. Nothing in the audit trail contradicts
either half — the disable recorded a disable, the login recorded a login, and
the ordering that makes them incompatible is invisible.

Returning `binding_id` from a resolution does NOT close this. That removed a
redundant READ; the window is between a read and the WRITE that depends on it,
and a value carried across the window is still a value read before it.

`finalize_external_login` closes it by making the read a `SELECT … FOR UPDATE`.
The disable path's own `UPDATE` needs the same row lock, so the two serialize:
either the login holds it first (a session is issued, and the disable commits
behind it against a binding that has already authenticated), or the disable
holds it first (the login blocks, re-reads `is_active = False` under the lock,
and refuses). There is no interleaving in which a session is minted from a
binding that was already inactive when the login took the lock.

## What the lock does NOT close, stated rather than implied

- **A session that already existed.** Serializing the decision cannot retract a
  session issued a minute earlier. That needs selective revocation, which needs
  session provenance — the contract below.
- **Participating party lifecycle operations.** Every binding writer first
  discovers its exact owner without a child lock, acquires that Party, and only
  then acquires/rechecks the binding. Finalization retains both locks through
  the caller's session INSERT and root completion. The common parent serializes
  participating issuance, revocation and later same-Party operations. Arbitrary
  nonparticipating writers and unknown future multi-Party roots are not proved.
- **Isolation level.** The re-check assumes READ COMMITTED (Postgres's
  default), where a `FOR UPDATE` that waits on a concurrent writer re-reads the
  committed row version once the lock is granted. Under REPEATABLE READ or
  SERIALIZABLE the same statement raises a serialization failure instead —
  which also fails closed, and the caller retries.

## Session provenance — the contract, DELIVERED in a67

Selective revocation ("disable this binding, and drop the sessions it produced")
needs a session to record WHICH binding produced it. It is built. Each numbered
point below records what was promised against what shipped:

1. `auth_sessions` gains `external_identity_binding_id UUID NULL`. NULL is
   correct and permanent: a password login has no binding, so the column is
   provenance that is absent, never provenance that is unknown. **Delivered,
   with two decisions the contract did not specify.** The FK is `ON DELETE
   RESTRICT` — `SET NULL` was the obvious choice and breaks the rule this very
   point states, by converting known provenance into the shape of absent
   provenance while leaving the session live. And the FK carries `party_id`:
   `(tenant_id, party_id, external_identity_binding_id)` →
   `(tenant_id, party_id, id)`, so a session cannot cite a binding belonging to
   a DIFFERENT party in the same tenant. Without that column in the constraint,
   selective revocation could revoke the wrong person.
2. `finalize_external_login`'s returned `binding_id` is that column's ONLY
   source. A caller that mints a session from this function without stamping it
   has produced an unattributable session. **Unchanged, and still a rule the
   kernel cannot enforce** — the assembly mints the session, so the kernel can
   only make the correct thing easy and say what the incorrect thing costs.
3. Revocation is a kernel operation beside `disable_external_identity_binding`,
   one writer, taking the same row lock in the same transaction as the disable.
   Disabling and revoking must not be two calls a caller can do half of.
   **Delivered as the PRIVATE `_revoke_sessions_for_binding`, which
   `disable_...` calls itself.** Private because nothing else needs it and
   revoking without disabling would leave the binding free to mint a
   replacement immediately.
4. Scope is SELECTIVE — the binding's sessions and nothing else. **Delivered**:
   the `WHERE` names the tenant and the binding, and password sessions carry
   NULL so they can never match.
5. Its canary is the mirror of this module's: a session from binding A survives
   a disable of B, and does not survive a disable of A. **Delivered** in
   `tests/test_session_provenance.py`, against a real database, because the
   composite FK, the delete rule and the RLS policy are half of what is asserted
   — plus the login-first race, which is the interleaving a naive
   implementation loses.

## What must never enter this module

- **No protocol.** No HTTP, no discovery document, no JWKS, no token parsing,
  no signature check. `resolve_external_identity` takes strings a caller has
  ALREADY verified; it cannot tell a verified subject from an invented one and
  does not try. A product that calls this with unverified input has an
  authentication bypass, and no amount of care here would fix it.
- **No external authorization.** Roles, groups, scopes, entitlements and
  organization claims from the provider are not read, not stored and not
  mapped. Authorization stays local, decided by
  `dotmac_kernel.deps.authorize_party` over local grants. ERP's contract states
  the rule and its architecture test enforces it by forbidding the token
  `roles` anywhere in the login path; the same rule holds here.
- **No provisioning.** An unbound subject resolves to `None`. It never creates
  a party, and it never falls back to matching on email — email is display
  evidence, and email-based linking is account takeover wearing a convenience
  argument. Binding is a separate, deliberate, evidenced act
  (`bind_external_identity`).
- **No provider vocabulary.** Nothing here knows what "OIDC" is, let alone
  Keycloak, Entra or Google. `provider_binding` is an opaque local key.

## The trust direction

`provider_binding` is the caller's own configuration identity — WHICH configured
provider it just completed a ceremony against. `issuer` and `subject` are
provider metadata. Resolution keys on the whole tuple, and the tuple's trusted
component is the one that did NOT arrive inside the credential being verified.

This is the same invariant the Integrator holds for destination scope: provider
metadata corroborates, a trusted local binding decides. A resolver that keyed on
`(issuer, subject)` alone would let any provider that can mint a token for a
known subject string authenticate as that party — including one an operator
configured for something else entirely.

## Transactions and audit

The resolver reads only. Writers use guarded immediate Core DML in a physical
public conflict SAVEPOINT; none flushes pending ORM state or commits. The caller's
assembly
owns its session boundary, while `dotmac_kernel.db` remains the kernel's public
transaction authority (hard rule 8). Expected conflicts use the kernel-private,
engine-free savepoint mechanic so importing or calling this service never
constructs a second database runtime.

No audit event is written here, deliberately. `write_audit_event` validates its
action against the declaring module's manifest, and this kernel module has no
manifest to declare one on — so the CALLING feature writes the event with its
own declared action, exactly as it owns the route and the response. Sub's
equivalent writes `credential.party_authentication_projected` from its own
service for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel._identity_operation import _Operation, _operation, _refresh_sessions
from dotmac_kernel._identity_persistence import _revoke_sessions
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import (
    AuthSession,
    ExternalIdentityBinding,
    Party,
    PartyType,
    Tenant,
)


@dataclass(frozen=True, slots=True)
class ResolvedExternalIdentity:
    """Who the subject is locally, and WHICH binding said so.

    `binding_id` names the row that answered — the provenance of the decision.
    Its purpose has NARROWED since it was introduced. It was added so a caller
    could stamp `last_authenticated_at` without re-running the resolver's
    lookup; `finalize_external_login` now does that stamp itself, under the lock
    that made the decision, so no caller needs the id for that any more.

    What it is for now is the thing the id was always better suited to: telling
    a session which binding produced it, so disabling that binding can revoke
    exactly those sessions and no others. The canonical nullable provenance column
    is delivered; password sessions
    carry None, and external sessions must stamp this exact returned binding.

    Returned by BOTH entry points, deliberately: the read and the login path
    hand back the same shape, so switching a caller from one to the other is a
    change of name and of meaning, never a change of unpacking.
    """

    party: Party
    binding_id: UUID


def _require_text(value: str, *, field: str, limit: int) -> str:
    """Non-blank and within the column's width.

    Blank-rejection matters more than it looks: `provider_binding=""` would
    otherwise be a perfectly storable binding that no legitimate caller can
    ever produce, and an empty `subject` would collapse every unbound identity
    at a provider onto one row.
    """
    text = value.strip()
    if not text:
        raise ValueError(f"external identity {field} must not be blank")
    if len(text) > limit:
        raise ValueError(
            f"external identity {field} is {len(text)} characters, "
            f"which exceeds the {limit} the column stores"
        )
    return text


def resolve_external_identity(
    db: Session,
    *,
    tenant: Tenant,
    provider_binding: str,
    issuer: str,
    subject: str,
) -> ResolvedExternalIdentity | None:
    """The verified external subject's local identity, or `None`. A READ.

    **Not the login path.** This function takes no lock and writes nothing, so
    everything it returns was true at the moment it looked and need not be true
    when the caller acts on it. A caller that will mint a session calls
    `finalize_external_login` instead — same parameters, same return shape, and
    the decision held under a lock until the session exists. What is left here
    is the honest use of a read: showing an operator which party a subject maps
    to, or answering a support question, where a stale answer is a stale answer
    and not an admitted login.

    `None` is the correct and common answer — an unbound subject, a disabled
    binding, a provider registration this deployment does not know, or a party
    that has since been deactivated. The caller turns that into its own refusal;
    this function neither raises nor guesses.

    Fail-closed by construction: there is ONE query, it requires an exact
    `(tenant, provider_binding, issuer, subject)` match on an ACTIVE row, and
    there is no second lookup to fall back to. In particular there is no
    match-by-email path — see the module docstring.

    The party is re-checked for `is_active` and `party_type == person` on every
    resolution rather than trusted from bind time, mirroring ERP's second check
    of local person state: a binding made a year ago says nothing about whether
    the account is still allowed to log in today. Organization parties can never
    authenticate, the same defence-in-depth `authenticate_request` applies.

    Does NOT stamp `last_authenticated_at` — that is a write, and a resolution
    is a read.
    """
    binding = db.scalars(
        select(ExternalIdentityBinding)
        .where(ExternalIdentityBinding.tenant_id == tenant.id)
        .where(ExternalIdentityBinding.provider_binding == provider_binding.strip())
        .where(ExternalIdentityBinding.issuer == issuer.strip())
        .where(ExternalIdentityBinding.subject == subject.strip())
        .where(ExternalIdentityBinding.is_active.is_(True))
    ).first()
    if binding is None:
        return None

    party = db.get(Party, binding.party_id)
    if party is None or party.tenant_id != tenant.id:
        return None
    if not party.is_active or party.party_type != PartyType.person:
        return None
    return ResolvedExternalIdentity(party=party, binding_id=binding.id)


def finalize_external_login(
    db: Session,
    *,
    tenant: Tenant,
    provider_binding: str,
    issuer: str,
    subject: str,
) -> ResolvedExternalIdentity | None:
    """Decide and stamp a verified login under retained Party then binding locks.

    Detached discovery grants nothing. Recheck the same owner, enabled binding,
    tenant and active person after waiting. Every business refusal is None; no
    subject-enumeration detail is returned. The caller mints its provenance-
    bearing session in the SAME root while Party and binding remain held.

    Requires clean pending ORM state and an active caller root. A guarded public
    conflict SAVEPOINT is physically materialized before queries; Core DML never
    flushes caller work. Both clean ORM reads refresh under their respective
    locks. Same-root later children remain supported without a closure registry.
    Arbitrary unknown-future multi-Party deadlock freedom is not claimed."""
    with _operation(db) as owner:
        tid = tenant.id
        owner.check()
        selectors = (
            ExternalIdentityBinding.tenant_id == tid,
            ExternalIdentityBinding.provider_binding == provider_binding.strip(),
            ExternalIdentityBinding.issuer == issuer.strip(),
            ExternalIdentityBinding.subject == subject.strip(),
        )
        discovered = db.scalar(
            select(ExternalIdentityBinding.party_id).where(*selectors)
        )
        if discovered is None:
            return None
        # The common parent is held through the caller's later session INSERT
        # and root completion. A waiting disable cannot already hold a child.
        party = db.scalars(
            select(Party)
            .where(Party.tenant_id == tid, Party.id == discovered)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        owner.check()
        if party is None:
            return None
        binding = db.scalars(
            select(ExternalIdentityBinding)
            .where(*selectors)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        owner.check()
        if binding is None or binding.party_id != discovered or not binding.is_active:
            return None
        if not party.is_active or party.party_type != PartyType.person:
            return None
        owner.track(binding, ("last_authenticated_at", "updated_at"))
        db.execute(
            update(ExternalIdentityBinding.__table__)
            .where(
                ExternalIdentityBinding.id == binding.id,
                ExternalIdentityBinding.tenant_id == tid,
            )
            .values(last_authenticated_at=datetime.now(UTC))
        )
        db.refresh(binding, attribute_names=["last_authenticated_at", "updated_at"])
        owner.check()
        return ResolvedExternalIdentity(party=party, binding_id=binding.id)


def bind_external_identity(
    db: Session,
    *,
    tenant: Tenant,
    party: Party,
    provider_binding: str,
    issuer: str,
    subject: str,
    bound_by: str,
    reason: str,
) -> ExternalIdentityBinding:
    """Bind a verified subject to a local person, with explicit evidence.

    This administrative act never provisions, matches email or repoints a
    different owner. Original text/tenant/person checks and advisory errors are
    retained. A fresh detached different-owner collision refuses BEFORE an
    irrelevant proposed Party lock. Successful paths lock Party, then recheck
    exact binding/provider membership; same-party rebind reactivates/re-evidences.
    Unique constraints still arbitrate distinct-party concurrent subject claims.
    IntegrityError becomes the original recoverable ConflictError after the
    operation SAVEPOINT rolls back, preserving earlier root work and tenant GUC.

    Clean pending state and active caller root precede all IO. Checked Core DML
    replaces pending ORM writes; return is the actual clean same-Session row.
    No commit, new root, closure declaration or fresh-root restriction exists."""
    try:
        with _operation(db) as owner:
            provider_binding = _require_text(
                provider_binding, field="provider_binding", limit=80
            )
            issuer = _require_text(issuer, field="issuer", limit=512)
            subject = _require_text(subject, field="subject", limit=255)
            bound_by = _require_text(bound_by, field="bound_by", limit=120)
            reason = _require_text(reason, field="bind_reason", limit=500)
            proposed_tenant = party.tenant_id
            owner.check()
            tid = tenant.id
            owner.check()
            if proposed_tenant != tid:
                raise ConflictError(
                    "party belongs to a different tenant than the binding being created"
                )
            proposed_type = party.party_type
            owner.check()
            if proposed_type != PartyType.person:
                raise ConflictError(
                    "only a person party can hold an external identity — "
                    "an organization has no one to authenticate"
                )
            pid = party.id
            owner.check()
            selectors = (
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.provider_binding == provider_binding,
                ExternalIdentityBinding.issuer == issuer,
                ExternalIdentityBinding.subject == subject,
            )
            # Fresh detached negative evidence refuses without acquiring an
            # irrelevant proposed Party or conflicting owner's binding lock.
            observed = db.scalar(
                select(ExternalIdentityBinding.party_id).where(*selectors)
            )
            if observed is not None and observed != pid:
                _different_party()
            locked = db.scalars(
                select(Party)
                .where(Party.tenant_id == tid, Party.id == pid)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            owner.check()
            if locked is None:
                raise ConflictError(
                    "party belongs to a different tenant than the binding being created"
                )
            if locked.party_type != PartyType.person:
                raise ConflictError(
                    "only a person party can hold an external identity — "
                    "an organization has no one to authenticate"
                )
            existing = db.scalars(
                select(ExternalIdentityBinding)
                .where(*selectors)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            owner.check()
            if existing is not None:
                if existing.party_id != pid:
                    _different_party()
                owner.track(
                    existing,
                    ("is_active", "bound_at", "bound_by", "bind_reason", "updated_at"),
                )
                db.execute(
                    update(ExternalIdentityBinding.__table__)
                    .where(
                        ExternalIdentityBinding.id == existing.id,
                        ExternalIdentityBinding.tenant_id == tid,
                        ExternalIdentityBinding.party_id == pid,
                    )
                    .values(
                        is_active=True,
                        bound_at=datetime.now(UTC),
                        bound_by=bound_by,
                        bind_reason=reason,
                    )
                )
                db.refresh(existing)
                owner.check()
                return existing
            held = db.scalar(
                select(ExternalIdentityBinding.id).where(
                    ExternalIdentityBinding.tenant_id == tid,
                    ExternalIdentityBinding.provider_binding == provider_binding,
                    ExternalIdentityBinding.party_id == pid,
                )
            )
            if held is not None:
                raise ConflictError(
                    "this party already holds an external identity at this provider — "
                    "a party may hold at most one per provider registration"
                )
            bid = db.scalar(
                insert(ExternalIdentityBinding.__table__)
                .values(
                    tenant_id=tid,
                    party_id=pid,
                    provider_binding=provider_binding,
                    issuer=issuer,
                    subject=subject,
                    is_active=True,
                    bound_at=datetime.now(UTC),
                    bound_by=bound_by,
                    bind_reason=reason,
                )
                .returning(ExternalIdentityBinding.id)
            )
            return db.scalars(
                select(ExternalIdentityBinding).where(
                    ExternalIdentityBinding.id == bid,
                    ExternalIdentityBinding.tenant_id == tid,
                )
            ).one()
    except IntegrityError as exc:
        raise ConflictError(
            "this external identity was bound concurrently — either the subject "
            "is now bound at this provider, or the party already holds an "
            "identity there. Re-read the current binding before retrying; the "
            "unique constraints arbitrate, and this request lost."
        ) from exc


def disable_external_identity_binding(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> ExternalIdentityBinding:
    """Disable and revoke exactly this tenant binding's sessions atomically.

    The subject remains occupied: disable never releases or repoints it.
    Detached discovery precedes Party lock, binding lock and ownership recheck.
    The complete current binding-session cohort is acquired in UUID order before
    shared private persistence changes unrevoked rows. Expired rows are included;
    prior timestamps, token hashes, expiry and provenance survive. Other bindings
    and password sessions are untouched. Repeated disable is a supported no-op
    for prior revocations; missing/foreign binding is the original NotFoundError.

    Clean pending state and active caller root are checked before IO; a guarded
    physical public conflict SAVEPOINT encloses Core writes. Only known clean
    affected attributes refresh; caller owns root completion and retained locks."""
    with _operation(db) as owner:
        tid = tenant.id
        owner.check()
        discovered = db.scalar(
            select(ExternalIdentityBinding.party_id).where(
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.id == binding_id,
            )
        )
        if discovered is None:
            raise NotFoundError("external identity binding not found")
        principal = db.scalar(
            select(Party.id)
            .where(Party.tenant_id == tid, Party.id == discovered)
            .with_for_update()
        )
        if principal is None:
            raise NotFoundError("external identity binding not found")
        binding = db.scalars(
            select(ExternalIdentityBinding)
            .where(
                ExternalIdentityBinding.id == binding_id,
                ExternalIdentityBinding.tenant_id == tid,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        owner.check()
        if binding is None or binding.party_id != discovered:
            raise NotFoundError("external identity binding not found")
        owner.track(binding, ("is_active", "updated_at"))
        _revoke_sessions_for_binding(
            db,
            tenant=tenant,
            binding_id=binding.id,
            party_id=discovered,
            owner=owner,
        )
        db.execute(
            update(ExternalIdentityBinding.__table__)
            .where(
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.id == binding.id,
                ExternalIdentityBinding.party_id == discovered,
                ExternalIdentityBinding.is_active.is_(True),
            )
            .values(is_active=False)
        )
        db.refresh(binding, attribute_names=["is_active", "updated_at"])
        owner.check()
        return binding


def _revoke_sessions_for_binding(
    db: Session, *, tenant: Tenant, binding_id: UUID, party_id: UUID, owner: _Operation
) -> int:
    """Private selective adapter to the sole Kernel revocation DML owner.

    Party and exact binding are already held by disable. Acquire the complete
    current binding-session cohort sorted, then sample its operation time. The
    shared owner preserves NULL-only/idempotent/expired/provenance semantics.
    Four dependent Academy writers remain open; no global retirement is claimed."""
    ids = tuple(
        db.scalars(
            select(AuthSession.id)
            .where(
                AuthSession.tenant_id == tenant.id,
                AuthSession.party_id == party_id,
                AuthSession.external_identity_binding_id == binding_id,
            )
            .order_by(AuthSession.id)
            .with_for_update()
        )
    )
    changed = _revoke_sessions(
        db,
        tenant_id=tenant.id,
        party_id=party_id,
        binding_id=binding_id,
        session_ids=ids,
        revoked_at=datetime.now(UTC),
    )
    _refresh_sessions(db, ids, owner=owner)
    return len(changed)


def record_external_authentication(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> None:
    """DEPRECATED: stamp an already-decided authentication, never mint a login.

    Retained for an evidenced step-up ceremony with no new session; removed next
    minor unless a real consumer is named. Login must use finalize_external_login.
    This writer still participates in Party-before-binding serialization and
    exact owner recheck. Missing/foreign binding remains NotFoundError; inactive
    bindings may still be stamped, preserving the old supported behavior.

    Clean pending state and active caller root precede a guarded materialized
    public conflict SAVEPOINT/Core update. No commit or pending ORM flush."""
    with _operation(db) as owner:
        tid = tenant.id
        owner.check()
        discovered = db.scalar(
            select(ExternalIdentityBinding.party_id).where(
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.id == binding_id,
            )
        )
        if discovered is None:
            raise NotFoundError("external identity binding not found")
        principal = db.scalar(
            select(Party.id)
            .where(
                Party.tenant_id == tid,
                Party.id == discovered,
            )
            .with_for_update()
        )
        if principal is None:
            raise NotFoundError("external identity binding not found")
        binding = db.scalars(
            select(ExternalIdentityBinding)
            .where(
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.id == binding_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        owner.check()
        if binding is None or binding.party_id != discovered:
            raise NotFoundError("external identity binding not found")
        owner.track(binding, ("last_authenticated_at", "updated_at"))
        db.execute(
            update(ExternalIdentityBinding.__table__)
            .where(
                ExternalIdentityBinding.tenant_id == tid,
                ExternalIdentityBinding.id == binding_id,
                ExternalIdentityBinding.party_id == discovered,
            )
            .values(last_authenticated_at=datetime.now(UTC))
        )
        db.refresh(binding, attribute_names=["last_authenticated_at", "updated_at"])
        owner.check()


__all__ = [
    "ResolvedExternalIdentity",
    "bind_external_identity",
    "disable_external_identity_binding",
    "finalize_external_login",
    "record_external_authentication",
    "resolve_external_identity",
]


def _different_party() -> None:
    raise ConflictError(
        "this external subject is already bound to a different party at "
        "this provider. Disabling that binding does NOT release the "
        "subject: the row keeps occupying the unique key, so the only "
        "paths are to re-enable it for the same party or to delete it "
        "deliberately, which discards the evidence of who bound it."
    )
