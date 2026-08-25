"""DotMac Inbox — a tenant-scoped, trait-driven conversation record (ADR-0006 M2).

Not "an omni-channel inbox". The audit
(`docs/inventories/inbox-sources.md`) found that most of what the fleet's one
implementation calls an inbox is transport, ISP identity policy, or workforce
policy — none of which belongs behind a shared version. What is left, and what
this module owns, is:

*a durable, threaded exchange between one tenant and one external party across a
declared channel, with an auditable record of every message and the provider
fact each one came from.*

## The layering, which is the whole design

    channel trait     4, FIXED      what the core branches on: identity, transport,
                                    threading, message-id scope
    channel           open          the product's own vocabulary; declared, not
                                    enumerated
    status            4, CLOSED     open / pending / snoozed / resolved
    status reason     open          why it is in that status; product-declared
    tag               open          searchable only, no behaviour

**No channel name appears in a conditional anywhere in this package**, and a
test fails the build if one does. That property is what makes the module
composable: Sub's ten channels and CRM's six reduce to four traits with no
residue, and the next product's channel is a declaration rather than a patch.

Both source products instead keep hardcoded sets of channel names — Sub's
five-name `_OPAQUE_CONTACT_CHANNELS`, and CRM's three-name literal list *inside
a partial unique index predicate*, which a second index on the same table
silently contradicts.

## The inbound seam is the kernel's

`dotmac_kernel.inbound` admits a provider fact before anything decides on it,
and `dotmac_kernel.inbound_models` holds the connected-account registry and the
observation ledger. Both moved there on 2026-08-12: admission needs
`dotmac_kernel.idempotency` (hard rule 21) and a registry that consent, delivery
and any conversation module all sit beside. An `InboxMessage` points back at the
observation it came from; the kernel cannot point forward into a module schema.

## Subjects and contacts live in the product

There are no `subscriber_id` / `person_id` / `ticket_id` columns, and no contact
table. Resolving "who is this address" is 340+ lines of ISP identity policy in
Sub alone; the module records what the provider said and stops.

## Public surface

Everything importable from this top-level namespace is the module's contract;
submodule paths are not. Import from here, so that internal reorganisation stays
internal. Pre-1.0 the surface is still settling, and `CHANGELOG.md` calls out
every change to it.
"""

from __future__ import annotations

from dotmac_inbox.lifecycle import (
    Direction,
    InvalidTransitionError,
    ReasonSpec,
    Status,
    UnknownReasonError,
    is_open,
    register_reasons,
    registered_reasons,
    validate_reason,
    validate_transition,
)
from dotmac_inbox.manifest import module
from dotmac_inbox.models import SCHEMA, InboxConversation, InboxMessage
from dotmac_inbox.threading import DedupKey, InboundIdentity, dedup_key, thread_key

__version__ = "0.1.0a1"

__all__ = [
    "SCHEMA",
    "DedupKey",
    "Direction",
    "InboundIdentity",
    "InboxConversation",
    "InboxMessage",
    "InvalidTransitionError",
    "ReasonSpec",
    "Status",
    "UnknownReasonError",
    "__version__",
    "dedup_key",
    "is_open",
    "module",
    "register_reasons",
    "registered_reasons",
    "thread_key",
    "validate_reason",
    "validate_transition",
]
