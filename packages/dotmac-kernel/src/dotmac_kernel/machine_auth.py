"""Machine credentials — an `X-Api-Key` principal that is not a person.

Extracted product-first from `dotmac_sub` and `dotmac_erp`, which both
authenticate machines with this header and **disagree about what a credential
means**. The inventory is `docs/inventories/machine-credential-sources.md`; the
one finding worth repeating here is the reason this module exists at all:

    Sub restricts a key to exactly its scopes.
    ERP treats an empty scope list as granting EVERYTHING.

A credential created or migrated with no scopes is inert in one product and
omnipotent in the other. That is not a style difference, and it is not something
a third local implementation would have resolved.

## What this facility takes from the sources, and what it refuses

**Taken (Sub's wire):** the `X-Api-Key` header; access is exactly the declared
scopes; the principal carries no roles, so there is no administrator shortcut;
`is_active`, `revoked_at` and `expires_at` all decide the lookup.

**Refused, and each refusal is a defect in a source:**

* *No unsalted-SHA-256 fallback.* Sub hashes with HMAC when a secret is
  configured and plain SHA-256 when it is not, then accepts either form on the
  way in. The comment says production always has the key — but the fallback is
  a property of the CODE, not of the environment, and a control that is safe
  only when configured correctly is not a control. ERP has only the weak form.
  Here, no key means no authentication: `require_machine_key` raises.
* *No borrowed key.* Sub derives its HMAC subkey from the connector-credential
  Fernet key, so rotating one silently invalidates the other. This reads a
  DEDICATED held secret (ADR-0009: held at boot, never dereferenced on the
  request path).
* *No write during authentication.* Sub commits `last_used_at` — and an
  unconditional rehash-on-use — inside a GET. This module never writes, so
  there is no `last_used_at` column to tempt one: a credential row that records
  its own reads makes every authenticated GET a write, and puts that write
  inside the caller's transaction. Usage belongs in the audit trail, which has
  an owner and a retention policy.
* *No human requirement.* ERP refuses a key without a `person_id` and then
  loads the `Person`. A machine is not a person wearing a service label, and a
  facility that insists otherwise cannot express the Integrator.
* *No wildcard, and no implicit administrator.* There is no scope that means
  "all", and an empty `scopes` list authorizes NOTHING. Amended 2026-08-24:
  the inventory recorded Sub as scope-EXACT on the strength of
  `_api_key_principal`'s docstring ("access is exactly its scopes"). Re-reading
  the ENFORCEMENT, that is not what happens. `has_permission` expands the
  required permission through `_wildcard_ancestors` — `network:nas:write`
  becomes `["*", "network:*", "network:nas:*"]` — and matches the key's scopes
  against the expansion, so a key holding `network:*` invokes every capability
  in the domain and a key holding `*` invokes all of them. `_permission_domain_
  aliases` additionally rewrites `customer:` to `subscriber:` and back. Sub is
  therefore a wildcard-and-alias implementation whose docstring says otherwise,
  which is worse than one that says so plainly: a reviewer checking the comment
  is told the control exists. No expansion happens here, on either side.
* *No anonymous machine.* Neither source records WHICH APPLICATION a key
  belongs to. Sub identifies its CRM caller by the presence of an
  `integration:crm` scope (`app/api/crm.py::require_crm_service_auth`), which
  makes identity a side effect of authorization: issue that scope to a second
  key and the two callers are indistinguishable in the trail forever. Here
  `source_application` is its own column and its own question, an unattributed
  credential does not authenticate at all, and `MachinePrincipal.application`
  is non-optional.
* *No downtime rotation.* Sub's `rotate_api_key` overwrites `key_hash` in
  place and its docstring states that the old secret stops working
  immediately. For an unattended machine caller that is an outage with a
  deploy in the middle of it. Here a rotation is a WINDOW: `next_key_hash`
  holds the incoming digest, both digests authenticate to the same principal,
  and the outgoing one is retired by an explicit `complete_rotation` — never
  by a clock. See `dotmac_kernel.machine_rotation`.

## Transaction contract

Receives a `Session`; issues one SELECT and nothing else. No commit, no
rollback, no flush. Hard rule 8 keeps the transaction owner at the request
boundary, and an authentication dependency is the last place that should be
quietly ended.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import ForbiddenError, UnauthorizedError
from dotmac_kernel.machine_models import MachineCredential
from dotmac_kernel.secret_sources import get_secret

#: The header the fleet already speaks. Both source products use it, and
#: changing it would strand every existing operator runbook for no gain.
API_KEY_HEADER = "X-Api-Key"

#: The dedicated secret this facility verifies against. A NAME, not a value —
#: the product installs the material through `install_secret_source` and the
#: kernel only ever looks it up in memory (ADR-0009).
# A LOOKUP NAME, not key material — the value lives only in whatever the
# product installed at boot. Suppressed with the same pair `config.py` uses
# for its dev-default comparisons.
MACHINE_KEY_SECRET_NAME = "machine_credential_hmac_key"  # noqa: S105 # nosec B105

#: Marks the stored form so a future scheme change is detectable rather than
#: silent. There is exactly one scheme today, and no fallback to another.
_SCHEME = "hmac-sha256:"


class MachineKeyUnavailableError(RuntimeError):
    """No verification secret is installed, so no credential can be verified.

    Deliberately not an authentication failure. A missing key is a DEPLOYMENT
    fault, and reporting it as "invalid credential" is how Sub's fallback came
    to look reasonable: the operator sees a plausible 401 and never learns the
    process cannot verify anything.
    """


def hash_machine_key(raw_key: str) -> str:
    """The one stored form. Raises when no dedicated secret is installed."""
    secret = get_secret(MACHINE_KEY_SECRET_NAME)
    if secret is None:
        raise MachineKeyUnavailableError(
            f"no {MACHINE_KEY_SECRET_NAME!r} is installed, so machine credentials "
            "cannot be verified. Install it through "
            "`dotmac_kernel.secret_sources.install_secret_source` at startup. "
            "There is deliberately no unsalted fallback: a credential check "
            "that weakens itself when misconfigured is not a check."
        )
    digest = hmac.new(
        secret.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{_SCHEME}{digest}"


@dataclass(frozen=True, slots=True)
class MachinePrincipal:
    """Who is calling, when the caller is not a person.

    No roles field, and that absence is the design: a role is a bundle somebody
    can widen later, and the whole point of a machine credential is that its
    authority is enumerated where it was granted.

    `application` is the SOURCE APPLICATION — which fleet peer this is, not just
    that it is a machine. It is non-optional on the principal even though the
    column is nullable for one release, because `authenticate_machine` refuses a
    credential that cannot answer it: a principal that exists without an
    attribution is exactly the anonymous machine this facility is meant to
    abolish.
    """

    credential_id: UUID
    tenant_id: UUID
    label: str
    application: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        """EXACT membership. No wildcard, no prefix, no substring, no case fold.

        Set containment, and the single most load-bearing line in this module.
        Each rejected alternative was a real behaviour in a source:

        * *Empty means everything* — ERP's `has_scope` returns True for every
          scope when the list is empty. Here an empty set authorizes nothing,
          which is the only safe reading of "we never said what this may do".
        * *Wildcard ancestors* — Sub's enforcement expands the REQUIRED
          permission to `["*", "network:*", "network:nas:*"]` and matches the
          key's scopes against that set, so a key holding `network:*` invokes
          every capability in the domain and a key holding `*` invokes all of
          them. Its own docstring says access is "exactly its scopes"; the
          enforcement disagrees with the docstring. There is no such expansion
          here, and there is no scope string that means "all".
        * *Aliases* — Sub also rewrites `customer:` to `subscriber:` and back,
          so one grant satisfies two names. Convenient, and it makes the
          question "what can this key do" unanswerable from the row.

        A credential scoped to one capability therefore cannot invoke another,
        including a capability whose name merely contains or extends its own.
        """
        return scope in self.scopes


def _has_expired(expires_at: datetime | None, moment: datetime) -> bool:
    """Compare an expiry against `moment`, tolerating a naive stored value.

    A `DateTime(timezone=True)` column round-trips as AWARE on PostgreSQL and
    NAIVE on SQLite, and comparing the two raises. Reading a naive value as UTC
    is correct rather than merely convenient: every writer here stores UTC, and
    the alternative — refusing to compare — would make a credential
    unauthenticatable on the backend the unit suite runs on.
    """
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= moment


def authenticate_machine(
    db: Session, raw_key: str, *, now: datetime | None = None
) -> MachinePrincipal:
    """Resolve an `X-Api-Key` to a principal, inside the established tenant scope.

    Tenant scoping is RLS's, not a filter written here. The row is only visible
    when `app_current_tenant_id()` matches, so a credential minted in another
    tenant does not authenticate — and it fails as UNKNOWN rather than as
    forbidden, because a caller learning that a key exists elsewhere is already
    a cross-tenant disclosure.
    """
    moment = now or datetime.now(UTC)
    digest = hash_machine_key(raw_key)
    credential = db.execute(
        select(MachineCredential).where(
            # EITHER live digest authenticates. This is the whole rotation
            # window: during it the outgoing and incoming secrets both resolve
            # to the SAME row, so the caller gets the same principal, the same
            # scopes and the same attribution whichever one it happens to send.
            # Sub's `rotate_api_key` overwrites `key_hash` in place — its own
            # docstring says "the old secret stops working immediately" — so
            # every caller still holding the previous key fails until somebody
            # redeploys it. For an unattended machine caller that is a
            # self-inflicted outage, and it is the reason this column exists.
            sa.or_(
                MachineCredential.key_hash == digest,
                MachineCredential.next_key_hash == digest,
            ),
            MachineCredential.is_active.is_(True),
            MachineCredential.revoked_at.is_(None),
        )
    ).scalar_one_or_none()

    # One message for unknown, revoked, inactive and expired. The caller learns
    # that this key does not work here and nothing about why, because the
    # difference between "revoked" and "never existed" is information about
    # another tenant's key management.
    if credential is None or _has_expired(credential.expires_at, moment):
        raise UnauthorizedError("machine credential is not valid")

    # An UNATTRIBUTED credential does not authenticate. `source_application` is
    # nullable at the schema for exactly one release, so an existing deployment
    # can name the owner of each row before the NOT NULL lands — and this is
    # what stops that open column from being an open door meanwhile. The
    # refusal is deliberately the same opaque message as every other one: which
    # of a tenant's rows are still un-attributed is that tenant's business.
    if credential.source_application is None:
        raise UnauthorizedError("machine credential is not valid")

    return MachinePrincipal(
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        label=credential.label,
        application=credential.source_application,
        scopes=frozenset(credential.scopes or ()),
    )


def require_machine_scope(scope: str):
    """A dependency admitting exactly the machines granted `scope`.

    Takes the scope at construction, so the route DECLARES what it needs and a
    reader sees the requirement beside the path rather than inside the handler.

    Admission is `principal.has_scope(scope)` — EXACT set membership, with no
    expansion of either side. A route requiring `"licence:descriptor:read"` is
    not satisfied by `"licence:descriptor"`, `"licence:*"`, `"*"`, or
    `"licence:descriptor:read:extra"`. See `MachinePrincipal.has_scope` for
    which source behaviour each of those refusals is departing from.
    """
    if not scope or scope.strip() != scope:
        raise ValueError(f"scope {scope!r} must be a non-empty, trimmed key")

    # Imported HERE, not at module scope. `dotmac_kernel.db` builds the engine
    # on import, so a top-level import would make `import dotmac_kernel` require
    # a DATABASE_URL — which the floor probe exercises precisely to stop that.
    # `deps.py` and `app_factory.py` defer the same import for the same reason.
    from dotmac_kernel.db import get_db

    def dependency(request: Request, db: Session = Depends(get_db)) -> MachinePrincipal:
        raw_key = request.headers.get(API_KEY_HEADER)
        if not raw_key:
            raise UnauthorizedError(f"{API_KEY_HEADER} is required")
        principal = authenticate_machine(db, raw_key)
        if not principal.has_scope(scope):
            raise ForbiddenError(
                f"machine credential {principal.label!r} does not carry "
                f"scope {scope!r}"
            )
        request.state.machine_principal = principal
        # The attribution, published on the request for anything downstream that
        # records what happened. A handler writing an audit row passes
        # `source_application=request.state.source_application` rather than
        # re-deriving it, and a handler issuing a CommandEnvelope passes the
        # same value — so one request cannot be attributed two ways.
        request.state.source_application = principal.application
        return principal

    return dependency


__all__ = [
    "API_KEY_HEADER",
    "MACHINE_KEY_SECRET_NAME",
    "MachineKeyUnavailableError",
    "MachinePrincipal",
    "authenticate_machine",
    "hash_machine_key",
    "require_machine_scope",
]
