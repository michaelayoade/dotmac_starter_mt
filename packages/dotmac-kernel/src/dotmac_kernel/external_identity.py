"""Bind a VERIFIED external subject to a local `Party` — and nothing else.

This module is the local half of federated login. Something else does the
protocol: discovery, JWKS, PKCE, signature and claim validation. By the time a
caller arrives here, that work is done and the question is narrow:

    this provider registration says it authenticated subject S at issuer I —
    which local party is that, in this tenant?

Answering it takes a row lookup, and refusing to answer it is the common case.

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

Every function here mutates and flushes; none commits. `dotmac_kernel.db` keeps
transaction authority (hard rule 8) — and is imported INSIDE the function that
needs it, because importing it builds the engine and this module is reachable
from `import dotmac_kernel`.

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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import ExternalIdentityBinding, Party, PartyType, Tenant


@dataclass(frozen=True, slots=True)
class ResolvedExternalIdentity:
    """Who the subject is locally, and WHICH binding said so.

    Both halves are needed and returning only the party was a defect: a caller
    that has accepted the login must then stamp `last_authenticated_at`, and
    with a bare `Party` it had no binding id to stamp — so it would have to
    re-run the same four-column lookup, on the same request, to find the row the
    resolver had just read. The second query is not merely wasteful; it can
    return something different if the binding was disabled in between, which
    means the row that authorised the login and the row that records it need not
    be the same one.

    `binding_id` is also the value `record_external_authentication` takes, so
    the two calls compose without the caller reconstructing anything.
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
    """The verified external subject's local identity, or `None`.

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
    is a read. A caller that has decided the login truly succeeded passes the
    returned `binding_id` to `record_external_authentication`.
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
    # Imported HERE, not at module scope. `dotmac_kernel.db` builds the engine
    # from `settings.database_url` at import time, so a module-level import
    # would make `import dotmac_kernel` itself require a database URL — this
    # module is re-exported from the package root. `errors.py` imports
    # `WebAuthRedirect` function-locally for exactly this reason, and CI's
    # kernel-floor probe (`import dotmac_kernel` in a clean venv) is what
    # catches it.
    from dotmac_kernel.db import conflict_savepoint

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

    Raises `NotFoundError` if no such binding exists IN THIS TENANT — a
    cross-tenant id is a miss, not an error disclosing that the id is real.
    """
    binding = db.get(ExternalIdentityBinding, binding_id)
    if binding is None or binding.tenant_id != tenant.id:
        raise NotFoundError("external identity binding not found")
    binding.is_active = False
    db.flush()
    return binding


def record_external_authentication(
    db: Session, *, tenant: Tenant, binding_id: UUID
) -> None:
    """Stamp `last_authenticated_at` after a login the caller accepted.

    Separate from `resolve_external_identity` so that resolution stays a read:
    a caller that resolves a party and then refuses the login for its own
    reasons must not leave evidence saying the identity authenticated.
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
    "record_external_authentication",
    "resolve_external_identity",
]
