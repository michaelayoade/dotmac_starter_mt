"""Threading and deduplication — the two rules that read the channel traits.

These are the functions that make `dotmac_kernel.channels` worth having.
Both products decide the same two things on every inbound message:

1. **Which conversation does this belong to?** → :func:`thread_key`
2. **Have I already got this message?** → :func:`dedup_key`

and both decide them by consulting hardcoded sets of channel names, in Python
and — in CRM's case — inside a partial unique index predicate in SQL. Here each
is one function reading one declared trait.

## The deduplication disagreement this resolves

CRM's ``crm_messages`` carries two overlapping partial unique indexes:

* ``uq_crm_messages_external`` — unique on
  ``(channel_type, coalesce(channel_target_id, …), external_id)``: per-account.
* ``uq_crm_messages_inbound_external`` — unique on
  ``(channel_type, external_id)`` where the direction is inbound **and the
  channel is one of three named literals**: global.

For those three channels the narrower index wins, so the same provider message
id arriving at two different connected mailboxes is rejected as a duplicate and
the second is silently dropped. Sub takes the global position everywhere
(``uq_inbox_messages_inbound_external``) without recording that it is a
position.

Neither is universally right. An RFC 5322 ``Message-ID`` is generated to be
globally unique; a Messenger message id is only meaningful within the page it
was delivered to. That is a per-channel fact, so it becomes a per-channel
declaration — :class:`~dotmac_kernel.channels.MessageIdScope` — and this module
is the single place that reads it.

The vocabulary itself is NOT this module's: `dotmac_kernel.channels` owns it,
because consent, channel policy and delivery all need the same channel facts and
a module none of them may import cannot be their source. This module reads two
of the four traits and owns neither.

## What is NOT here

No database access, no session, no models. These are pure functions over a
declaration and a payload, which is what lets them be tested exhaustively across
every trait combination without a database — and what stops the dedup rule
drifting into three call sites the way it did in both products.

Enforcing the resulting key is the caller's job, and the composite unique
indexes in :mod:`dotmac_inbox.models` are shaped to match what these return.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dotmac_kernel.channels import (
    ChannelSpec,
    MessageIdScope,
    ThreadIdentity,
    channel_spec,
)

__all__ = [
    "DedupKey",
    "InboundIdentity",
    "dedup_key",
    "thread_key",
]


@dataclass(frozen=True, slots=True)
class InboundIdentity:
    """Everything the two rules need from an inbound message, and nothing more.

    ``account_scope`` identifies the connected account the message arrived at —
    the mailbox, the Meta page, the WhatsApp business number. It is required
    even for globally-scoped channels because it is part of the thread key
    regardless: two mailboxes talking to one customer are two conversations, and
    merging them exposes one team's thread to another.
    """

    channel: str
    #: The connected account this arrived at. Never empty.
    account_scope: str
    #: The external party's address or opaque provider id, already normalized by
    #: the product when the channel's address form is not OPAQUE.
    contact: str
    #: The provider's thread id, when it supplies one.
    external_thread_id: str | None = None
    #: The provider's message id, when it supplies one.
    external_message_id: str | None = None
    #: Used only when the channel declares ``MessageIdScope.NONE``.
    subject: str | None = None
    #: Used only when the channel declares ``MessageIdScope.NONE``.
    body: str | None = None


def _spec(channel: str | ChannelSpec) -> ChannelSpec:
    return channel if isinstance(channel, ChannelSpec) else channel_spec(channel)


def thread_key(identity: InboundIdentity) -> str:
    """The stable key identifying which conversation this message belongs to.

    Reads ``thread_identity``:

    * ``PROVIDER`` — the provider's thread id, scoped to the account it arrived
      at. Falls back to the derived form when the provider declares thread
      identity but omits it on a particular message, which happens with the
      first message of a thread and with malformed email headers. The fallback
      is deliberate and total: returning ``None`` here would push a nullable
      through every caller for a case that has an obviously correct answer.
    * ``DERIVED`` — ``(channel, account_scope, contact)``. Every message from
      one contact at one of our accounts on one channel is one thread.

    The account scope is always present, for both branches. See
    :class:`InboundIdentity`.
    """
    spec = _spec(identity.channel)
    if not identity.account_scope:
        raise ValueError(
            f"channel {spec.code!r}: account_scope is required — it is part of "
            "the thread key on every channel, so that two connected accounts "
            "talking to one contact stay two conversations"
        )
    if spec.thread_identity is ThreadIdentity.PROVIDER and identity.external_thread_id:
        return f"{spec.code}:{identity.account_scope}:t:{identity.external_thread_id}"
    if not identity.contact:
        raise ValueError(
            f"channel {spec.code!r}: no external_thread_id and no contact, so "
            "the message cannot be threaded at all"
        )
    return f"{spec.code}:{identity.account_scope}:c:{identity.contact}"


@dataclass(frozen=True, slots=True)
class DedupKey:
    """The key a message is unique by, and whether it came from the provider.

    ``derived`` distinguishes "the provider told us this id" from "we hashed the
    content because the provider gives none". Callers should treat a derived
    match as weaker evidence — it is the only one that can produce a false
    positive, when a customer genuinely sends the same short message twice.
    """

    value: str
    derived: bool


def dedup_key(identity: InboundIdentity) -> DedupKey:
    """The key this message is unique by, per the channel's declared id scope.

    * ``GLOBAL`` — ``(channel, external_message_id)``. The provider id is unique
      everywhere, so the account it arrived at is irrelevant.
    * ``ACCOUNT`` — ``(channel, account_scope, external_message_id)``. The same
      id at a different connected account is a DIFFERENT message. This is the
      case CRM's narrower index gets wrong.
    * ``NONE`` — a SHA-256 over channel, account, contact, subject and body.
      Declared, not stumbled into.

    A channel declaring ``GLOBAL`` or ``ACCOUNT`` that then arrives with no
    ``external_message_id`` falls back to the content fingerprint rather than
    raising: providers do omit ids, and refusing the message loses it entirely.
    The result is flagged ``derived`` so the caller knows the match is weaker.
    """
    spec = _spec(identity.channel)
    scope = spec.message_id_scope
    external = identity.external_message_id

    if scope is not MessageIdScope.NONE and external:
        if scope is MessageIdScope.GLOBAL:
            return DedupKey(f"{spec.code}:m:{external}", derived=False)
        return DedupKey(
            f"{spec.code}:{identity.account_scope}:m:{external}", derived=False
        )

    digest = hashlib.sha256(
        "\x1f".join(
            (
                spec.code,
                identity.account_scope,
                identity.contact,
                identity.subject or "",
                identity.body or "",
            )
        ).encode("utf-8")
    ).hexdigest()
    return DedupKey(f"{spec.code}:h:{digest}", derived=True)
