"""The idempotent command envelope (kernel WS3).

A `CommandEnvelope` is the typed, tenant-scoped wrapper around a command whose
`command_id` is its idempotency key. Paired with `inbox.process_once`, the same
envelope processed twice yields the effect exactly once. It is a plain frozen
value — no DB, no I/O — so it is import-safe and cheap to construct/pass around.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

_EMPTY: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """A command to process at-most-once per `(tenant_id, command_id)`.

    - ``command_id`` — the idempotency key (client/caller-supplied; a re-delivery
      MUST reuse the same value). Unique per tenant.
    - ``command_type`` — a stable, machine-readable command name.
    - ``tenant_id`` — the owning tenant; commands are tenant-scoped.
    - ``payload`` — the command's data (JSON-serializable, never interpreted by
      the kernel).
    - ``actor_party_id`` / ``correlation_id`` / ``issued_at`` — optional
      provenance for audit and tracing.
    """

    command_id: str
    command_type: str
    tenant_id: UUID
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY)
    actor_party_id: UUID | None = None
    correlation_id: str | None = None
    issued_at: datetime | None = None


__all__ = ["CommandEnvelope"]
