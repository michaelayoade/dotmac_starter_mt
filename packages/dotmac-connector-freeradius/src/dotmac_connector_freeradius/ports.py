"""Transport ports: the only places this connector touches the outside.

Three seams, each injected so conformance runs without a database, a product
or a NAS:

* `RadiusDbPort` — the FreeRADIUS SQL target (apply/read/poll/nas-secret).
* `SecretFetchPort` — the credential owner's single-use fetch (ADR-0076
  section 7). The production HTTP binding is assembly work; this package
  ships the protocol and refuses to guess a transport for a secret.
* `CoaPort` — one Disconnect-Request to one NAS endpoint.

Production implementations live beside their protocols (`sqlalchemy_radius_db`
in `database.py`, `UdpCoaPort` in `coa.py`). Nothing here imports an engine,
a session or a settings object: a DSN arrives only as a materialized secret
at handler construction time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from dotmac_connector_freeradius.render import RenderedProjection, Row


class SecretFetchRefused(RuntimeError):
    """The owner refused the fetch (closed or consumed projection request)."""


class SecretFetchUnavailable(RuntimeError):
    """The fetch could not complete — retryable transport failure."""


@dataclass(frozen=True, slots=True)
class PostAuthRow:
    row_id: int
    username: str
    reply: str
    authdate: datetime
    nas_address: str | None


@dataclass(frozen=True, slots=True)
class AccountingRow:
    row_id: int
    username: str
    nas_address: str | None
    session_id: str
    status_type: str
    input_octets: int
    output_octets: int
    session_seconds: int
    event_at: datetime


@runtime_checkable
class RadiusDbPort(Protocol):
    def ping(self) -> None:
        """Raise when the target is unreachable or its tables are absent."""
        ...

    def apply_projection(
        self,
        *,
        usernames: Sequence[str],
        rendered: RenderedProjection,
        lock_key: str,
    ) -> int:
        """DELETE every row for `usernames`, then INSERT `rendered`, atomically.

        Implementations take a transaction-scoped advisory lock on
        `lock_key` first (Postgres), so two applies for one login can never
        interleave delete/insert. Returns rows written.
        """
        ...

    def read_projection(
        self, username: str
    ) -> tuple[tuple[Row, ...], tuple[Row, ...], tuple[Row, ...]]:
        """(radcheck, radreply, radusergroup) rows currently held."""
        ...

    def poll_postauth(
        self, *, after_id: int, limit: int
    ) -> tuple[PostAuthRow, ...]: ...

    def poll_accounting(
        self, *, after_id: int, limit: int
    ) -> tuple[AccountingRow, ...]: ...

    def nas_secret(self, *, nas_ref: str, nas_address: str) -> str | None:
        """The shared secret the target's `nas` table holds for this NAS."""
        ...


@runtime_checkable
class SecretFetchPort(Protocol):
    def fetch_secret(
        self,
        *,
        credential_id: str,
        secret_version: int,
        projection_request_ref: str,
        operation_ref: str,
    ) -> str:
        """Return the cleartext for exactly this open projection request.

        Raises `SecretFetchRefused` when the owner will not honour the fetch
        (request closed, consumed, or version superseded) and
        `SecretFetchUnavailable` for transport failure. The returned secret
        is used in memory by the caller and never persisted by it.
        """
        ...


@dataclass(frozen=True, slots=True)
class CoaOutcome:
    outcome: str  # acknowledged | refused | unanswered | unsupported
    error_cause: str | None = None


@runtime_checkable
class CoaPort(Protocol):
    def disconnect(
        self,
        *,
        address: str,
        port: int,
        secret: str,
        attributes: Mapping[str, str],
        timeout_seconds: float,
    ) -> CoaOutcome: ...


class UnconfiguredSecretFetch:
    """The shipped default: no fetch transport is bound, so refuse loudly.

    A secret transport guessed by a connector would be a custody decision
    taken by the wrong party. Until the assembly binds the owner's
    mutually-authenticated endpoint, an enabled projection fails retryably
    and says why, instead of silently trying something else.
    """

    def fetch_secret(
        self,
        *,
        credential_id: str,
        secret_version: int,
        projection_request_ref: str,
        operation_ref: str,
    ) -> str:
        raise SecretFetchUnavailable(
            "no secret-fetch transport is bound for this installation"
        )


__all__ = [
    "AccountingRow",
    "CoaOutcome",
    "CoaPort",
    "PostAuthRow",
    "RadiusDbPort",
    "SecretFetchPort",
    "SecretFetchRefused",
    "SecretFetchUnavailable",
    "UnconfiguredSecretFetch",
]
