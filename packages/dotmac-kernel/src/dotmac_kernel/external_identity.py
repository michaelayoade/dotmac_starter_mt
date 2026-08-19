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
- **A party deactivated concurrently.** `finalize_external_login` re-reads the
  party under the binding's lock, so it sees every deactivation committed
  before that point. It deliberately does not take a second lock on `parties`:
  that row has many other writers, and a login that locked binding-then-party
  would deadlock against any transaction that touches a party before its
  binding. The residual window is the same shape as the one above and has the
  same answer — revoke the sessions, do not lock the world.
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

Every function here mutates and flushes; none commits. The caller's assembly
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
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel._transactions import conflict_savepoint
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import (
    AuthSession,
    ExternalIdentityBinding,
    Party,
    PartyType,
    Tenant,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import CursorResult


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
    exactly those sessions and no others. The column that receives it does not
    exist yet — see the module docstring's deferred contract — and until it
    does, `binding_id` is a value a caller may record in its own audit event.

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
    """Decide the login and record it as ONE locked step, or refuse.

    The only entry point a caller may end a session on. It locks the binding,
    re-checks under that lock everything that would refuse the login, stamps
    `last_authenticated_at`, and returns the party — so the caller adds its
    session in the SAME transaction, with the row still held. A concurrent
    disable cannot land between the decision and the session, because it needs
    the lock this call is holding. The module docstring has the interleaving
    this closes, and the ones it does not.

    Same parameters and same return shape as `resolve_external_identity`, so
    moving a caller off the racy pair is one identifier. `None` for every
    refusal, for the same reason resolution refuses that way: a typed error per
    reason would let a caller distinguish "no such subject" from "disabled
    binding", and that distinction is a subject-enumeration oracle handed to
    whoever can drive a login. The caller turns `None` into its own refusal.

    Two implementation details are load-bearing rather than incidental:

    * The locking read matches on the identity tuple ALONE and checks
      `is_active` in Python afterwards. Putting `is_active` in the `WHERE`
      would work — Postgres re-evaluates a locking query's predicate against
      the newly committed row version, so a disabled row would simply drop out
      — but it would put the re-check in a place a reader cannot see, and it
      would silently stop being a re-check under an isolation level that
      raises instead of re-evaluating. The tuple is the unique key, so the
      lock is still on exactly one row.
    * `populate_existing=True` on both reads. Without it a session that had
      already loaded either row would get the identity map's copy: the `SELECT
      … FOR UPDATE` would take the lock, and then the code would test a value
      cached from BEFORE the concurrent disable. A lock that guards a stale
      attribute is worse than no lock, because it looks correct.

    Mutates and flushes; `dotmac_kernel.db` owns the commit (hard rule 8) — and
    that ownership is the point, not a formality. The caller's commit is what
    releases the lock, so the session it mints and the stamp made here become
    visible together or not at all.
    """
    binding = db.scalars(
        select(ExternalIdentityBinding)
        .where(ExternalIdentityBinding.tenant_id == tenant.id)
        .where(ExternalIdentityBinding.provider_binding == provider_binding.strip())
        .where(ExternalIdentityBinding.issuer == issuer.strip())
        .where(ExternalIdentityBinding.subject == subject.strip())
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if binding is None:
        return None
    # Under the lock, and only now, is `is_active` an answer rather than an
    # observation: no other transaction can flip it until this one commits.
    if not binding.is_active:
        return None

    party = db.scalars(
        select(Party)
        .where(Party.tenant_id == tenant.id)
        .where(Party.id == binding.party_id)
        .execution_options(populate_existing=True)
    ).first()
    if party is None:
        return None
    if not party.is_active or party.party_type != PartyType.person:
        return None

    binding.last_authenticated_at = datetime.now(UTC)
    db.flush()
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
    """Bind a verified external subject to `party`, with evidence.

    Deliberately an ADMINISTRATIVE act, not something a login performs. The
    whole no-auto-provision posture rests on this being a separate decision:
    somebody with authority decided this external subject is this person, and
    `bound_by`/`reason` record who and why. Both are required and non-blank,
    a port delta over ERP (which records neither) taken from Sub's CHECK-forced
    evidence pair.

    Idempotent for the SAME party — re-binding reactivates and re-evidences the
    existing row, which is how a disabled binding is restored (ERP's behaviour).
    Re-binding a subject that already belongs to a DIFFERENT party raises
    `ConflictError`: silently repointing it would transfer an account, and the
    unique constraints refuse it anyway — this just makes the refusal legible
    instead of an `IntegrityError` at flush time.

    Raises `ValueError` on blank/oversized input, `ConflictError` on a
    cross-party collision — including one that only materialises under
    concurrency, where the unique constraints arbitrate and the loser gets a
    `ConflictError` rather than a transaction-aborting `IntegrityError`.
    Mutates and flushes; the caller owns the commit.
    """
    provider_binding = _require_text(
        provider_binding, field="provider_binding", limit=80
    )
    issuer = _require_text(issuer, field="issuer", limit=512)
    subject = _require_text(subject, field="subject", limit=255)
    bound_by = _require_text(bound_by, field="bound_by", limit=120)
    reason = _require_text(reason, field="bind_reason", limit=500)

    if party.tenant_id != tenant.id:
        raise ConflictError(
            "party belongs to a different tenant than the binding being created"
        )
    if party.party_type != PartyType.person:
        raise ConflictError(
            "only a person party can hold an external identity — an organization "
            "has no one to authenticate"
        )

    existing = db.scalars(
        select(ExternalIdentityBinding)
        .where(ExternalIdentityBinding.tenant_id == tenant.id)
        .where(ExternalIdentityBinding.provider_binding == provider_binding)
        .where(ExternalIdentityBinding.issuer == issuer)
        .where(ExternalIdentityBinding.subject == subject)
    ).first()
    if existing is not None:
        if existing.party_id != party.id:
            raise ConflictError(
                "this external subject is already bound to a different party at "
                "this provider. Disabling that binding does NOT release the "
                "subject: the row keeps occupying the unique key, so the only "
                "paths are to re-enable it for the same party or to delete it "
                "deliberately, which discards the evidence of who bound it."
            )
        existing.is_active = True
        existing.bound_at = datetime.now(UTC)
        existing.bound_by = bound_by
        existing.bind_reason = reason
        db.flush()
        return existing

    # The party's OTHER binding at this same provider, if any. The unique
    # constraint would refuse this too; catching it here names which of the two
    # rules was broken, since the two failures need different operator actions.
    held = db.scalars(
        select(ExternalIdentityBinding)
        .where(ExternalIdentityBinding.tenant_id == tenant.id)
        .where(ExternalIdentityBinding.provider_binding == provider_binding)
        .where(ExternalIdentityBinding.party_id == party.id)
    ).first()
    if held is not None:
        raise ConflictError(
            "this party already holds an external identity at this provider — "
            "a party may hold at most one per provider registration"
        )

    # The two lookups above are ADVISORY, not arbitration. Between them and this
    # INSERT, a concurrent binder can pass the same checks — both see no row,
    # both proceed, and the database decides. Without a savepoint the loser's
    # `IntegrityError` would abort the whole request transaction (taking `SET
    # LOCAL app.current_tenant` with it, so any DB access in the caller's
    # `except ConflictError` handler runs with no tenant context and fails
    # closed under FORCE RLS — the F3 failure `conflict_savepoint` exists for),
    # and the caller would get a raw `IntegrityError` instead of the
    # `ConflictError` this function documents.
    #
    # The unique constraints are what actually arbitrate; the earlier lookups
    # only buy a SPECIFIC message naming which of the two rules was broken, and
    # they cannot tell them apart once the race is lost. Hence the generic
    # message here — it must not claim to know which constraint fired.
    try:
        with conflict_savepoint(db):
            binding = ExternalIdentityBinding(
                tenant_id=tenant.id,
                party_id=party.id,
                provider_binding=provider_binding,
                issuer=issuer,
                subject=subject,
                is_active=True,
                bound_at=datetime.now(UTC),
                bound_by=bound_by,
                bind_reason=reason,
            )
            db.add(binding)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "this external identity was bound concurrently — either the subject "
            "is now bound at this provider, or the party already holds an "
            "identity there. Re-read the current binding before retrying; the "
            "unique constraints arbitrate, and this request lost."
        ) from exc
    return binding


def disable_external_identity_binding(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> ExternalIdentityBinding:
    """Deactivate a binding, keeping the row.

    Retention is the point (ERP: *"Disabling a binding retains the record for
    auditability"*). A deleted row would erase the evidence of who bound the
    identity and why, at exactly the moment that evidence becomes interesting.

    **Disabling does NOT release the subject.** The row keeps occupying
    `uq_external_identity_bindings_tenant_provider_subject`, so the same
    external subject cannot afterwards be bound to a DIFFERENT party while it
    exists — `bind_external_identity` raises `ConflictError`. Re-binding the
    same party reactivates this row. That is deliberate and it is the safe
    default: silently freeing the tuple would make "disable" a step on the path
    to handing an external identity to somebody else, which is the takeover this
    table exists to prevent. Reassignment is a delete, and a delete is a
    decision to discard evidence.

    **It REVOKES the sessions that binding produced**, in this call and this
    transaction. The contract's wording was deliberate: *disabling and revoking
    must not be two calls a caller can do half of*. There is no
    `revoke_sessions=False` — a flag whose off position leaves live sessions for
    a disabled identity is not flexibility.

    ## The lock is EXPLICIT, and that is a correction

    An earlier draft read the row with `db.get` and relied on the eventual
    `UPDATE` to serialise. That does not hold: `db.get` takes no lock, so a login
    already inside `finalize_external_login` could commit its session AFTER this
    transaction had scanned for sessions to revoke, and that session would
    survive its own binding's disablement. The read here is
    `SELECT … FOR UPDATE` on the same row `finalize_external_login` locks, which
    is what actually orders the two.

    Both interleavings then end correctly:

    * **login first** — it holds the lock, so this call WAITS. The login commits
      its session, this call acquires the lock, and the revocation below sweeps
      up the session that was just issued. Disabling is never beaten by a login
      that happened to be a moment earlier.
    * **disable first** — this call holds the lock, so the login blocks. On
      acquiring it, `finalize_external_login` re-reads under EvalPlanQual, sees
      `is_active = False`, and refuses. No session is issued at all.

    Scope is SELECTIVE. Sessions from other bindings and from password logins
    are untouched; a global logout is a different decision with a different blast
    radius and does not belong on this path.

    Raises `NotFoundError` if no such binding exists IN THIS TENANT — a
    cross-tenant id is a miss, not an error disclosing that the id is real.
    Mutates and flushes; the caller owns the commit, and it is the commit that
    releases the lock.
    """
    binding = db.execute(
        select(ExternalIdentityBinding)
        .where(
            ExternalIdentityBinding.id == binding_id,
            ExternalIdentityBinding.tenant_id == tenant.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if binding is None:
        raise NotFoundError("external identity binding not found")

    binding.is_active = False
    _revoke_sessions_for_binding(db, tenant=tenant, binding_id=binding.id)
    db.flush()
    return binding


def _revoke_sessions_for_binding(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> int:
    """Revoke every unrevoked session this binding produced. Returns the count.

    PRIVATE, and it stays private until something other than
    `disable_external_identity_binding` needs it. A public function with one
    in-module caller is surface a consumer can reach around the decision that
    owns it — here, revoking without disabling would leave the binding able to
    mint a replacement session immediately, which is not an operation anybody
    has asked for and is a footgun if offered.

    `auth_sessions.revoked_at` has ONE writer. A product that revoked sessions
    itself would be a second one. An assembly MINTS sessions — its own decision,
    and it stamps the provenance column when it does — but ending them on an
    IDENTITY decision belongs to the kernel, which is what knows a binding was
    disabled.

    Idempotent by predicate rather than by bookkeeping: `revoked_at IS NULL`
    means a repeat revokes nothing, and a session revoked earlier for another
    reason keeps its original timestamp. Overwriting it would move the recorded
    moment somebody was signed out, which is the one fact the column carries.

    Expired sessions are included deliberately. One past `expires_at` is already
    refused at authentication so revoking changes no access — but it makes the
    row say what happened, and an operator reading the trail should not have to
    reason about which rows were skipped because a clock had passed.
    """
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.tenant_id == tenant.id,
            AuthSession.external_identity_binding_id == binding_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    # `rowcount` lives on the DBAPI cursor result; the ORM's `Result` does not
    # declare it, so it is read through `cast` rather than silenced.
    return int(cast("CursorResult[Any]", result).rowcount)


def record_external_authentication(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> None:
    """DEPRECATED. Stamp `last_authenticated_at` for a decision made elsewhere.

    It survives, deprecated rather than deleted or absorbed, and the argument
    for each is worth writing down because the wrong reading of "keep it" is
    what would reopen the defect.

    **Why not absorbed.** `finalize_external_login` does stamp, so the stamping
    code is duplicated — but they are not the same operation. This one records
    an authentication the CALLER already decided; that one MAKES the decision
    and records it in the same locked step. Folding this into that would mean
    exporting a way to stamp without deciding, which is precisely the half of
    the pair that made resolve-then-issue look safe.

    **Why not deleted.** It has one use that is not the defect: recording that
    an already-authenticated principal exercised the binding again — a step-up
    or re-authentication ceremony where no new session is minted, so there is
    nothing to linearize and nothing a lock would protect. That case is real
    but has no consumer in the fleet today, which is why this is a deprecation
    with a condition and not a permanent surface: it is REMOVED in the next
    minor unless such a consumer appears and is named here.

    **Never** as the second half of resolve-then-stamp. That pair reads the
    binding without a lock and writes after, so an administrator disabling the
    binding in between gets a successful-looking disable and a live session
    derived from the identity it revoked. If a session is going to exist at the
    end of the flow, the flow starts at `finalize_external_login`.

    Raises `NotFoundError` if no such binding exists IN THIS TENANT.
    """
    binding = db.get(ExternalIdentityBinding, binding_id)
    if binding is None or binding.tenant_id != tenant.id:
        raise NotFoundError("external identity binding not found")
    binding.last_authenticated_at = datetime.now(UTC)
    db.flush()


__all__ = [
    "ResolvedExternalIdentity",
    "bind_external_identity",
    "disable_external_identity_binding",
    "finalize_external_login",
    "record_external_authentication",
    "resolve_external_identity",
]
