"""The idempotent command envelope (kernel WS3).

A `CommandEnvelope` is the typed, tenant-scoped wrapper around a command whose
`command_id` is its idempotency key. Paired with `inbox.process_once`, the same
envelope processed twice yields the effect exactly once. It is a plain frozen
value — no DB, no I/O — so it is import-safe and cheap to construct/pass around.

## Every command says which application issued it, or it does not exist

`source_application` is not optional and has no default. An envelope that cannot
name its issuer raises at CONSTRUCTION, which is the earliest possible moment
and, more importantly, the only moment at which the answer is still knowable:
once an unattributed command is in a queue, in a retry, or halfway through a
handler, nobody can recover who sent it, and every remedy available at that
point invents an answer.

There is deliberately no `"system"`, no `"internal"` and no `"unknown"` value to
reach for. A scheduled job inside this application is issued BY this
application, and naming it — `dotmac_kernel.source_applications
.active_host_application()` — is a true statement about a real issuer. An
anonymous principal with a name on it is the thing this field exists to prevent,
so making one convenient would defeat the field entirely.

Sub's nearest equivalent, `CommandContext.actor`, is a free-text `str` and its
`CommandContext.system(actor=...)` constructor takes whatever a call site hands
it. That is attribution as a comment: unvalidated, unenumerated, and impossible
to query across. Shape is validated here; membership of the deployment's
accepted set is checked where a command is ACCEPTED (`inbox.process_once`),
because that is the boundary an untrusted attribution actually crosses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from dotmac_kernel.source_applications import validate_source_application

_EMPTY: Mapping[str, object] = MappingProxyType({})


class UnattributedCommandError(ValueError):
    """A command envelope that does not name the application that issued it."""


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """A command to process at-most-once per `(tenant_id, command_id)`.

    - ``command_id`` — the idempotency key (client/caller-supplied; a re-delivery
      MUST reuse the same value). Unique per tenant.
    - ``command_type`` — a stable, machine-readable command name.
    - ``tenant_id`` — the owning tenant; commands are tenant-scoped.
    - ``source_application`` — REQUIRED. Which application issued this. See the
      module docstring for why it has no default and no anonymous value.
    - ``payload`` — the command's data (JSON-serializable, never interpreted by
      the kernel).
    - ``actor_party_id`` / ``correlation_id`` / ``issued_at`` — optional
      provenance for audit and tracing.

    ``source_application`` carries a `None` default ONLY because the three
    fields after it already have defaults and a dataclass cannot follow a
    defaulted field with a required one. `None` is refused in `__post_init__`,
    so it is a syntax accommodation rather than a permitted value — the error
    a caller omitting it gets says exactly that.
    """

    command_id: str
    command_type: str
    tenant_id: UUID
    source_application: str | None = None
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY)
    actor_party_id: UUID | None = None
    correlation_id: str | None = None
    issued_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.source_application is None:
            raise UnattributedCommandError(
                f"command {self.command_type!r} does not say which application "
                "issued it. Pass `source_application=` — from "
                "`request.state.source_application` for a machine-authenticated "
                "caller, or `active_host_application()` for something this "
                "process originates. There is deliberately no 'system' default: "
                "an unattributed command is refused, not attributed to nobody."
            )
        validate_source_application(self.source_application)

    def issuer(self) -> str:
        """The issuing application, narrowed to `str`.

        `source_application` is typed optional for the dataclass-ordering reason
        above; `__post_init__` guarantees it is a validated string on every
        instance that exists. This accessor is how a caller says that without a
        `# type: ignore` at each use.

        The re-check is a real branch rather than an `assert`, because `python
        -O` strips asserts and an attribution guard that a runtime flag can
        remove is not a guard.
        """
        if self.source_application is None:  # pragma: no cover — __post_init__
            raise UnattributedCommandError(
                f"command {self.command_type!r} has no issuing application"
            )
        return self.source_application


__all__ = ["CommandEnvelope", "UnattributedCommandError"]
