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
  "all", and an empty `scopes` list authorizes NOTHING.

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

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.db import get_db
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
    """

    credential_id: UUID
    tenant_id: UUID
    label: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        """Exact membership. No wildcard, and empty authorizes nothing.

        The inverse of ERP's `has_scope`, which returns True for every scope
        when the list is empty. An unscoped credential here can do nothing at
        all, which is the only safe reading of "we never said what this may do".
        """
        return scope in self.scopes


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
    credential = db.execute(
        select(MachineCredential).where(
            MachineCredential.key_hash == hash_machine_key(raw_key),
            MachineCredential.is_active.is_(True),
            MachineCredential.revoked_at.is_(None),
        )
    ).scalar_one_or_none()

    # One message for unknown, revoked, inactive and expired. The caller learns
    # that this key does not work here and nothing about why, because the
    # difference between "revoked" and "never existed" is information about
    # another tenant's key management.
    if credential is None or (
        credential.expires_at is not None and credential.expires_at <= moment
    ):
        raise UnauthorizedError("machine credential is not valid")

    return MachinePrincipal(
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        label=credential.label,
        scopes=frozenset(credential.scopes or ()),
    )


def require_machine_scope(scope: str):
    """A dependency admitting exactly the machines granted `scope`.

    Takes the scope at construction, so the route DECLARES what it needs and a
    reader sees the requirement beside the path rather than inside the handler.
    """
    if not scope or scope.strip() != scope:
        raise ValueError(f"scope {scope!r} must be a non-empty, trimmed key")

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
