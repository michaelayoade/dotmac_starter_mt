"""The relay transport for `delivery.dispatch.v1` (ADR-0006 § 5g, K-8).

**Owner of:** *turning one claimed `{dispatch_id}` outbox event into at most one
provider send, and recording what came back.*

A `DispatchRelayTransport` is a `DeliveryTransport` for the kernel outbox worker
(`dotmac_kernel.messaging.worker`). It adds no queue, no retry policy and no
send path of its own: attempts, backoff, leasing and dead-lettering stay with
the relay, and `delivery_providers.send` stays the one place that checks consent,
calls the provider and writes the receipt.

## What flows where

- The outbox payload is ONLY `{"dispatch_id": ...}`. Anything else is refused as
  malformed. The dispatcher connection therefore never carries an address, a
  token or a body (`claim_batch` selects the payload).
- Content was rendered at enqueue and snapshotted in the dispatch row. This
  transport only LOADS it (`load_dispatch` re-verifies the fingerprint) in the
  worker's tenant-RLS session, never re-renders it.
- Attachments are references. Their bytes come from an `AttachmentResolverRegistry`
  keyed by `AttachmentRef.kind`, run in that same tenant session, lazily (so a
  suppressed or deferred message resolves nothing). The fingerprint covers the
  refs, not the bytes, because generated documents are time-dependent.

## Outcome mapping

| `send()` result / raise                     | dispatch     | outbox event          |
|---------------------------------------------|--------------|-----------------------|
| `Sent` receipt `accepted` / `delivered`     | `sent`       | sent, hooks run       |
| `Suppressed`                                | `suppressed` | sent (nothing to do)  |
| `DeliveryDeferred` (K-4)                    | `pending`    | rescheduled, no charge|
| receipt `failed` (transient)                | `pending`    | retried with backoff  |
| receipt `rejected` / `bounced` / `complaint`| `failed`     | sent (verdict final)  |
| `DeliveryOutcomeUnknown`                    | `dead`       | dead at once, audited |
| any other raise                             | `pending`    | retried; dead at max  |
| attempts / deferrals exhausted              | `dead`       | `dead`                |

A `failed` receipt is committed before the retry is raised, so the evidence of
each attempt survives. A bounce has already suppressed the address by the time
`send()` returns (`record_receipt`), which is what stops the next dispatch.

**Unknown outcome.** The provider may have taken the message. Most providers,
SMTP included, do not honour the dispatch idempotency key, so a retry can
double-send. It is therefore never retried: the dispatch goes `dead`, the outbox
event is dead-lettered immediately and one `communication.dispatch.outcome_unknown`
audit row records it. That row, the `dead` dispatch and the dead outbox row are
the operator's evidence. The operator decides, after checking with the provider,
whether to use the audited requeue (K-5), which will re-send. Nothing here
decides that on their behalf. The audit action must be writable BEFORE a send is
attempted (checked up front), so an unknown outcome can never be invisible.

## Transactions

The transport commits inside `deliver`, so the outcome of a send is durable
before anything that could fail afterwards:

1. phase 1: send, receipt, dispatch state, commit;
2. phase 2, only for an accepted send: projection hooks, commit.

If phase 2 raises, the event is retried; the retry finds the dispatch `sent` and
re-runs only the hooks (never the send). **Projection hooks must be idempotent**:
they can run more than once for one dispatch. Because `app.current_tenant` is
transaction-local, the tenant context is re-set after every commit.

## Resolver and hook registries

Both follow the kernel's install/active pattern (`audit_actions`,
`outbox_event_types`): a process installs one registry, and `active_*()` refuses
an assembly that never did, rather than guessing "no hooks". A worker that
needs none installs an empty registry. Tests swap them with a fixture that puts
back whatever was installed.

Error text is stored on the outbox row. This module raises only fixed text, never
an address, subject, body, token or provider response body; anything unexpected
is reduced to its exception type name.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.delivery import (
    DeliveryDeferred,
    DeliveryError,
    DeliveryOutcomeUnknown,
    DeliveryTerminal,
    latest_receipt_for_dispatch,
)
from dotmac_kernel.delivery_dispatch import (
    DISPATCH_EVENT_TYPE,
    DispatchError,
    DispatchIntegrityError,
    LoadedDispatch,
    load_dispatch,
    settle_dispatch,
)
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DISPATCH_DEAD,
    DISPATCH_FAILED,
    DISPATCH_SENT,
    DISPATCH_SUPPRESSED,
    CommunicationDispatch,
)
from dotmac_kernel.delivery_providers import (
    AttachmentRef,
    DeliveryProvider,
    Sent,
    send,
)
from dotmac_kernel.messaging.relay import ClaimedEvent

logger = logging.getLogger("dotmac_kernel.delivery_relay")

AUDIT_ACTION_OUTCOME_UNKNOWN = "communication.dispatch.outcome_unknown"
AUDIT_ENTITY_TYPE = "communication_dispatch"
#: The sent statuses: the provider took the message.
_SENT_STATUSES = frozenset({DELIVERY_ACCEPTED, DELIVERY_DELIVERED})


# --------------------------------------------------------------------------
# Resolver registry
# --------------------------------------------------------------------------


class DuplicateRelayRegistrationError(ValueError):
    """Two owners registered the same resolver kind or hook name."""


class UnknownAttachmentKind(LookupError):
    """No resolver is registered for an attachment's `kind`."""


class RelayRegistryNotInstalledError(RuntimeError):
    """The process never installed this registry (an assembly wiring omission)."""


@dataclass(frozen=True, slots=True)
class AttachmentContext:
    """What a resolver gets. `db` is the worker's tenant-scoped session, already
    RLS-restored to `tenant_id`: read through it, never commit."""

    db: Session
    tenant_id: UUID
    dispatch_id: UUID
    ref: AttachmentRef


#: Returns the attachment bytes for `ctx.ref`. Raising means "could not resolve":
#: the attempt is retried (it self-heals when the document exists).
AttachmentResolver = Callable[[AttachmentContext], bytes]


class AttachmentResolverRegistry:
    """Immutable `kind -> resolver` map. A kind has one owner."""

    __slots__ = ("_by_kind",)

    def __init__(
        self, declarations: Iterable[tuple[str, str, AttachmentResolver]]
    ) -> None:
        by_kind: dict[str, tuple[str, AttachmentResolver]] = {}
        for owner, kind, resolver in declarations:
            if not kind or not callable(resolver):
                raise ValueError("a resolver needs a kind and a callable")
            existing = by_kind.get(kind)
            if existing is not None and existing[0] != owner:
                raise DuplicateRelayRegistrationError(
                    f"attachment kind {kind!r} registered by both {existing[0]!r} "
                    f"and {owner!r} — a kind has one resolver"
                )
            by_kind[kind] = (owner, resolver)
        self._by_kind = by_kind

    def resolver_for(self, kind: str) -> AttachmentResolver:
        entry = self._by_kind.get(kind)
        if entry is None:
            raise UnknownAttachmentKind(
                f"no attachment resolver is registered for kind {kind!r}"
            )
        return entry[1]

    def kinds(self) -> frozenset[str]:
        return frozenset(self._by_kind)

    def owner(self, kind: str) -> str | None:
        entry = self._by_kind.get(kind)
        return entry[0] if entry else None


_active_resolvers: AttachmentResolverRegistry | None = None


def install_attachment_resolvers(registry: AttachmentResolverRegistry) -> None:
    """Install the process's attachment resolvers (the assembly, once)."""
    global _active_resolvers
    _active_resolvers = registry


def active_attachment_resolvers() -> AttachmentResolverRegistry:
    if _active_resolvers is None:
        raise RelayRegistryNotInstalledError(
            "no attachment resolver registry is installed in this process; a "
            "relay worker must install one (an empty registry if it needs none)"
        )
    return _active_resolvers


# --------------------------------------------------------------------------
# Projection hook registry
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    """What a projection hook gets after the provider ACCEPTED a dispatch.

    `db` is the worker's tenant-scoped session; the hook's writes commit with the
    hook phase, or roll back with it if any hook raises. The hook may run more
    than once for one dispatch and must be idempotent.
    """

    db: Session
    tenant_id: UUID
    dispatch_id: UUID
    idempotency_key: str
    category: str
    template_provenance: str
    template_revision_id: UUID | None
    receipt_status: str
    provider_message_id: str | None


ProjectionHook = Callable[[ProjectionContext], None]


@dataclass(frozen=True, slots=True)
class _Hook:
    owner: str
    name: str
    hook: ProjectionHook
    categories: frozenset[str] | None


class ProjectionHookRegistry:
    """Immutable, ordered set of projection hooks.

    A hook is registered under a unique `name`; `categories=None` runs it for
    every dispatch, otherwise only for dispatches whose `category` is listed.
    Hooks run in registration order.
    """

    __slots__ = ("_hooks",)

    def __init__(
        self,
        declarations: Iterable[tuple[str, str, ProjectionHook, Iterable[str] | None]],
    ) -> None:
        hooks: list[_Hook] = []
        seen: dict[str, str] = {}
        for owner, name, hook, categories in declarations:
            if not name or not callable(hook):
                raise ValueError("a projection hook needs a name and a callable")
            if name in seen:
                raise DuplicateRelayRegistrationError(
                    f"projection hook {name!r} registered by both {seen[name]!r} "
                    f"and {owner!r} — a hook name has one owner"
                )
            seen[name] = owner
            cats = None if categories is None else frozenset(categories)
            if cats is not None and (not cats or not all(cats)):
                raise ValueError("categories must be None or non-empty names")
            hooks.append(_Hook(owner, name, hook, cats))
        self._hooks = tuple(hooks)

    def hooks_for(self, category: str) -> tuple[tuple[str, ProjectionHook], ...]:
        return tuple(
            (h.name, h.hook)
            for h in self._hooks
            if h.categories is None or category in h.categories
        )

    def names(self) -> tuple[str, ...]:
        return tuple(h.name for h in self._hooks)


_active_hooks: ProjectionHookRegistry | None = None


def install_projection_hooks(registry: ProjectionHookRegistry) -> None:
    """Install the process's projection hooks (the assembly, once)."""
    global _active_hooks
    _active_hooks = registry


def active_projection_hooks() -> ProjectionHookRegistry:
    if _active_hooks is None:
        raise RelayRegistryNotInstalledError(
            "no projection hook registry is installed in this process; a relay "
            "worker must install one (an empty registry if it needs none)"
        )
    return _active_hooks


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


class TransientProviderFailure(RuntimeError):
    """The provider reported a retryable `failed` receipt. Retried with backoff."""


class ProjectionFailed(RuntimeError):
    """A projection hook raised after the send was already recorded. The event is
    retried and only the hooks re-run."""


class ResolvedAttachments:
    """A callable `AttachmentRef -> bytes` bound to one dispatch's tenant session.

    It has exactly the signature of the SMTP adapter's `attachment_resolver`, so a
    product builds its provider per delivery:

        DispatchRelayTransport(provider_factory=lambda resolved:
            SmtpDeliveryProvider(config=..., attachment_resolver=resolved))

    Bytes are resolved lazily, on first use, and cached for the one delivery.
    """

    def __init__(
        self,
        *,
        db: Session,
        tenant_id: UUID,
        dispatch_id: UUID,
        registry: AttachmentResolverRegistry,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._dispatch_id = dispatch_id
        self._registry = registry
        self._cache: dict[AttachmentRef, bytes] = {}

    def __call__(self, ref: AttachmentRef) -> bytes:
        if ref not in self._cache:
            resolver = self._registry.resolver_for(ref.kind)
            data = resolver(
                AttachmentContext(
                    db=self._db,
                    tenant_id=self._tenant_id,
                    dispatch_id=self._dispatch_id,
                    ref=ref,
                )
            )
            if not isinstance(data, bytes | bytearray) or not data:
                raise UnknownAttachmentKind(
                    f"resolver for kind {ref.kind!r} returned no bytes"
                )
            self._cache[ref] = bytes(data)
        return self._cache[ref]


ProviderFactory = Callable[[ResolvedAttachments], DeliveryProvider]


def _set_tenant(db: Session, tenant_id: UUID) -> None:
    # Transaction-local (`true`), so it must be re-set after every commit.
    db.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )


def _dispatch_id_of(payload: Mapping[str, object]) -> UUID:
    """The dispatch id from an id-only payload; anything else is malformed."""
    if set(payload) != {"dispatch_id"} or not isinstance(payload["dispatch_id"], str):
        raise DeliveryTerminal("malformed payload: expected only dispatch_id")
    try:
        return UUID(payload["dispatch_id"])
    except ValueError:
        raise DeliveryTerminal("malformed payload: dispatch_id is not a UUID") from None


class DispatchRelayTransport:
    """`DeliveryTransport` for `delivery.dispatch.v1`. See the module docstring."""

    event_type = DISPATCH_EVENT_TYPE

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory,
        attachment_resolvers: AttachmentResolverRegistry | None = None,
        projection_hooks: ProjectionHookRegistry | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._resolvers = attachment_resolvers
        self._hooks = projection_hooks

    # -- worker protocol -------------------------------------------------

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        if event.event_type != DISPATCH_EVENT_TYPE:
            raise DeliveryTerminal("unexpected event type for the dispatch transport")
        dispatch_id = _dispatch_id_of(event.payload)
        tenant_id = event.tenant_id
        # Wiring faults surface BEFORE a send is attempted, never after one.
        resolvers = self._resolvers or active_attachment_resolvers()
        hooks = self._hooks or active_projection_hooks()
        active_audit_actions().require(AUDIT_ACTION_OUTCOME_UNKNOWN)

        row = tenant_db.execute(
            select(CommunicationDispatch)
            .where(
                CommunicationDispatch.tenant_id == tenant_id,
                CommunicationDispatch.dispatch_id == dispatch_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise DeliveryTerminal("dispatch not found")
        status = row.status
        if status == DISPATCH_DEAD:
            raise DeliveryTerminal("dispatch is dead")
        if status in (DISPATCH_FAILED, DISPATCH_SUPPRESSED):
            return  # already settled (a duplicate event): nothing to do
        if status == DISPATCH_SENT:
            self._project(tenant_db, tenant_id, row, hooks)
            return

        try:
            loaded = load_dispatch(tenant_db, tenant_id, dispatch_id)
        except DispatchIntegrityError:
            settle_dispatch(tenant_db, tenant_id, dispatch_id, DISPATCH_DEAD)
            tenant_db.commit()
            raise DeliveryTerminal("dispatch content failed verification") from None
        except DispatchError as exc:
            raise DeliveryTerminal(
                f"dispatch cannot be sent: {type(exc).__name__}"
            ) from None

        self._send_and_record(tenant_db, tenant_id, loaded, resolvers, hooks)

    def dead_letter(self, event: ClaimedEvent, tenant_db: Session) -> None:
        """Worker hook (idempotent): the event is about to be dead-lettered, so
        the dispatch goes `dead` too. An unparsable payload has no dispatch."""
        try:
            dispatch_id = _dispatch_id_of(event.payload)
        except DeliveryTerminal:
            return
        settle_dispatch(tenant_db, event.tenant_id, dispatch_id, DISPATCH_DEAD)

    # -- internals -------------------------------------------------------

    def _send_and_record(
        self,
        db: Session,
        tenant_id: UUID,
        loaded: LoadedDispatch,
        resolvers: AttachmentResolverRegistry,
        hooks: ProjectionHookRegistry,
    ) -> None:
        dispatch_id = loaded.message.dispatch_id
        resolved = ResolvedAttachments(
            db=db, tenant_id=tenant_id, dispatch_id=dispatch_id, registry=resolvers
        )
        try:
            provider = self._provider_factory(resolved)
            outcome = send(db, tenant_id, provider=provider, message=loaded.message)
        except DeliveryDeferred:
            raise  # a hold: nothing written, the worker reschedules
        except DeliveryOutcomeUnknown as unknown:
            self._record_unknown(db, tenant_id, dispatch_id, unknown)
            raise DeliveryTerminal(
                "provider outcome unknown; not retried (operator review)"
            ) from None
        except DeliveryTerminal:
            raise
        except DeliveryError:
            raise  # our own fixed-text refusals (misconfiguration): retried
        except Exception as exc:
            logger.warning(
                "dispatch %s send raised %s", dispatch_id, type(exc).__name__
            )
            raise TransientProviderFailure(
                f"send raised {type(exc).__name__}"
            ) from None

        if not isinstance(outcome, Sent):
            settle_dispatch(db, tenant_id, dispatch_id, DISPATCH_SUPPRESSED)
            db.commit()
            return

        receipt_status = outcome.receipt.status
        if receipt_status == DELIVERY_FAILED:
            # Keep the receipt (evidence of this attempt), then retry.
            code = outcome.receipt.response_code
            db.commit()
            raise TransientProviderFailure(
                "provider reported a transient failure" + (f" ({code})" if code else "")
            )
        if receipt_status in _SENT_STATUSES:
            settle_dispatch(db, tenant_id, dispatch_id, DISPATCH_SENT)
            db.commit()
            _set_tenant(db, tenant_id)
            row = db.get(CommunicationDispatch, dispatch_id)
            if row is not None:
                self._project(db, tenant_id, row, hooks)
            return
        # rejected / bounced / complaint: a final verdict on the recipient. The
        # bounce has already suppressed the address inside `send`.
        settle_dispatch(db, tenant_id, dispatch_id, DISPATCH_FAILED)
        db.commit()

    def _record_unknown(
        self,
        db: Session,
        tenant_id: UUID,
        dispatch_id: UUID,
        unknown: DeliveryOutcomeUnknown,
    ) -> None:
        # Terminal state first and durable, then the audit row: the dead state
        # must not depend on the audit write.
        db.rollback()
        _set_tenant(db, tenant_id)
        settle_dispatch(db, tenant_id, dispatch_id, DISPATCH_DEAD)
        db.commit()
        _set_tenant(db, tenant_id)
        try:
            write_audit_event(
                db,
                tenant_id=tenant_id,
                actor_type="system",
                actor_id="delivery-relay",
                action=AUDIT_ACTION_OUTCOME_UNKNOWN,
                entity_type=AUDIT_ENTITY_TYPE,
                entity_id=str(dispatch_id),
                is_success=False,
                details={
                    "reason": unknown.reason[:200],
                    "retried": False,
                    "operator_action": "verify with the provider, then requeue",
                },
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error(
                "dispatch %s outcome-unknown audit write failed (%s)",
                dispatch_id,
                type(exc).__name__,
            )

    def _project(
        self,
        db: Session,
        tenant_id: UUID,
        row: CommunicationDispatch,
        hooks: ProjectionHookRegistry,
    ) -> None:
        # Plain values first: a rollback below expires `row`, and under FORCE RLS
        # an expired row cannot refresh once the transaction-local tenant is gone.
        dispatch_id = row.dispatch_id
        category = row.category
        idempotency_key = row.idempotency_key
        provenance = row.template_provenance
        revision_id = row.template_revision_id
        receipt = latest_receipt_for_dispatch(db, tenant_id, dispatch_id=dispatch_id)
        if receipt is None or receipt.status not in _SENT_STATUSES:
            return
        ctx = ProjectionContext(
            db=db,
            tenant_id=tenant_id,
            dispatch_id=dispatch_id,
            idempotency_key=idempotency_key,
            category=category,
            template_provenance=provenance,
            template_revision_id=revision_id,
            receipt_status=receipt.status,
            provider_message_id=receipt.provider_message_id,
        )
        for name, hook in hooks.hooks_for(category):
            try:
                hook(ctx)
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "dispatch %s projection %r raised %s",
                    dispatch_id,
                    name,
                    type(exc).__name__,
                )
                raise ProjectionFailed(
                    f"projection hook {name!r} raised {type(exc).__name__}"
                ) from None
        db.commit()


__all__ = [
    "AUDIT_ACTION_OUTCOME_UNKNOWN",
    "AttachmentContext",
    "AttachmentResolver",
    "AttachmentResolverRegistry",
    "DispatchRelayTransport",
    "DuplicateRelayRegistrationError",
    "ProjectionContext",
    "ProjectionFailed",
    "ProjectionHook",
    "ProjectionHookRegistry",
    "ProviderFactory",
    "RelayRegistryNotInstalledError",
    "ResolvedAttachments",
    "TransientProviderFailure",
    "UnknownAttachmentKind",
    "active_attachment_resolvers",
    "active_projection_hooks",
    "install_attachment_resolvers",
    "install_projection_hooks",
]
