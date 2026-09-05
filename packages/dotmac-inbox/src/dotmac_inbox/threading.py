"""Threading and deduplication — the two rules that read the channel traits.

These are the functions that make the module's declared channel traits useful.
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
declaration — :class:`~dotmac_inbox.channels.MessageIdScope` — and this module
is the single place that reads it.

This narrow vocabulary belongs to `dotmac-inbox`: the traits answer only
conversation threading and message-identity questions. Consent, outbound
channel policy and delivery retain their existing kernel contracts. A future
fleet-wide declaration mechanism remains an explicit architecture candidate;
this extraction does not silently promote one into the kernel.

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

from dotmac_inbox.channels import (
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
    contact: str | None
    #: The provider's thread id, when it supplies one.
    external_thread_id: str | None = None
    #: The provider's message id, when it supplies one.
    external_message_id: str | None = None
    #: Used only when the channel declares ``MessageIdScope.NONE``.
    subject: str | None = None
    #: Used only when the channel declares ``MessageIdScope.NONE``.
    body: str | None = None
    #: A stable product-owned thread reference for ``ThreadIdentity.SUPPLIED``.
    supplied_thread_ref: str | None = None
    #: A stable product-owned message reference for ``MessageIdScope.SUPPLIED``.
    supplied_message_ref: str | None = None


def _spec(channel: str | ChannelSpec) -> ChannelSpec:
    return channel if isinstance(channel, ChannelSpec) else channel_spec(channel)


def _supplied_digest(*parts: str) -> str:
    """Hash an unambiguous, length-prefixed UTF-8 tuple for supplied keys only."""
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _required_account_scope(value: str, *, channel: str) -> str:
    if not value:
        raise ValueError(
            f"channel {channel!r}: account_scope is required — it is part of "
            "the thread key on every channel, so that two connected accounts "
            "talking to one contact stay two conversations"
        )
    if len(value) > 160:
        raise ValueError(f"channel {channel!r}: account_scope exceeds 160 characters")
    return value


def _required_supplied_ref(value: str | None, *, label: str) -> str:
    if value is None:
        raise ValueError(f"{label} is required and cannot be blank")
    if len(value) > 255:
        raise ValueError(f"{label} exceeds 255 characters")
    if not value.strip():
        raise ValueError(f"{label} is required and cannot be blank")
    return value


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
    * ``SUPPLIED`` — a product-owned stable local thread reference. It is not
      transport evidence and has no contact fallback.

    The account scope is always present, for both branches. See
    :class:`InboundIdentity`.
    """
    spec = _spec(identity.channel)
    account_scope = _required_account_scope(identity.account_scope, channel=spec.code)
    if spec.thread_identity is ThreadIdentity.SUPPLIED:
        if identity.external_thread_id is not None:
            raise ValueError(
                "supplied thread identity cannot also carry external_thread_id"
            )
        supplied = _required_supplied_ref(
            identity.supplied_thread_ref, label="supplied_thread_ref"
        )
        digest = _supplied_digest(spec.code, account_scope, supplied)
        return f"{spec.code}:t1:{digest}"
    if identity.supplied_thread_ref is not None:
        raise ValueError(
            "supplied_thread_ref is valid only for supplied thread identity"
        )
    if spec.thread_identity is ThreadIdentity.PROVIDER and identity.external_thread_id:
        return f"{spec.code}:{account_scope}:t:{identity.external_thread_id}"
    if not identity.contact:
        raise ValueError(
            f"channel {spec.code!r}: no external_thread_id and no contact, so "
            "the message cannot be threaded at all"
        )
    return f"{spec.code}:{account_scope}:c:{identity.contact}"


@dataclass(frozen=True, slots=True)
class DedupKey:
    """The key a message is unique by, and whether it is content-derived.

    ``derived=False`` means a declared stable identity supplied by either a
    provider or the product. ``derived=True`` means a content fingerprint, the
    weaker identity that can falsely match repeated identical messages.
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
    * ``SUPPLIED`` — a product-owned stable message reference, scoped to its
      channel and connected account. It never falls back to content.

    A channel declaring ``GLOBAL`` or ``ACCOUNT`` that then arrives with no
    ``external_message_id`` falls back to the content fingerprint rather than
    raising: providers do omit ids, and refusing the message loses it entirely.
    The result is flagged ``derived`` so the caller knows the match is weaker.
    """
    spec = _spec(identity.channel)
    account_scope = _required_account_scope(identity.account_scope, channel=spec.code)
    scope = spec.message_id_scope
    external = identity.external_message_id

    if scope not in {MessageIdScope.NONE, MessageIdScope.SUPPLIED} and external:
        if scope is MessageIdScope.GLOBAL:
            return DedupKey(f"{spec.code}:m:{external}", derived=False)
        return DedupKey(f"{spec.code}:{account_scope}:m:{external}", derived=False)

    if scope is MessageIdScope.SUPPLIED:
        supplied = _required_supplied_ref(
            identity.supplied_message_ref, label="supplied_message_ref"
        )
        digest = _supplied_digest(spec.code, account_scope, supplied)
        return DedupKey(f"{spec.code}:s1:{digest}", derived=False)
    if identity.supplied_message_ref is not None:
        raise ValueError(
            "supplied_message_ref is valid only for supplied message scope"
        )

    digest = hashlib.sha256(
        "\x1f".join(
            (
                spec.code,
                account_scope,
                identity.contact or "",
                identity.subject or "",
                identity.body or "",
            )
        ).encode("utf-8")
    ).hexdigest()
    return DedupKey(f"{spec.code}:h:{digest}", derived=True)
