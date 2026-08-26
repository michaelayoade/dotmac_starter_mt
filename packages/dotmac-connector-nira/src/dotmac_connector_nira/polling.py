"""The POLL handler: drain the registry message queue into typed observations.

The registry queues messages for the registrar — inbound transfer requests,
transfers-away, host and contact changes it made. EPP ``<poll op="req">`` reads
the head of that queue; ``<poll op="ack">`` dequeues it. This handler turns
each message into an :class:`InboundEvent` the engine persists and the owning
application reconciles.

## Observations, never decisions

A poll event is a *typed observation*: "the registry says a transfer was
requested for this domain." It is NOT a status write. The connector does not
decide the domain's lifecycle from it — ``dotmac-domains`` reads the observation
and decides. This is the inbound half of the same rule the delivery handler
obeys outbound.

## The engine owns the checkpoint

``poll`` receives the cursor the engine persisted and returns the events found
plus the cursor to persist next. It never writes the cursor itself, so it can
never advance past a message it failed to hand back. The cursor is the last
registry message id acked in this batch; a crash before the engine records it
simply re-reads the same queue head next time — at-least-once, which the
engine's idempotency makes at-most-once downstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from xml.etree.ElementTree import ParseError

from dotmac_integration.spi import InboundDisposition, InboundEvent

from dotmac_connector_nira import frames
from dotmac_connector_nira.delivery import EPP_PASSWORD, _material, _num, _text
from dotmac_connector_nira.epp import (
    EPP_NS,
    EppProtocolError,
    EppSession,
    EppTransportError,
    classify_result,
    safe_fromstring,
)

__all__ = ["NiraPollHandler", "MESSAGE_CAPABILITY"]

MESSAGE_CAPABILITY: Final = "registry.message.v1"

#: Bound on how many messages one poll pass drains, so a large backlog is
#: worked in bounded batches rather than one unbounded session. The engine
#: schedules the next pass; this is not a retry loop.
_MAX_PER_PASS: Final = 50


class NiraPollHandler:
    """Reads and acks the registry message queue for `registry.message.v1`."""

    def poll(
        self,
        cursor: str | None,
        *,
        config: Mapping[str, object],
        secrets: Mapping[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        host = _text(config.get("host"))
        port = config.get("port")
        clid = _text(config.get("clid"))
        pw = _material(secrets, EPP_PASSWORD)
        connect_timeout = _num(config.get("connect_timeout"))
        read_timeout = _num(config.get("read_timeout"))
        if (
            host is None
            or not isinstance(port, int)
            or isinstance(port, bool)
            or clid is None
            or pw is None
            or connect_timeout is None
            or read_timeout is None
        ):
            # A misconfigured poll returns no events and does not advance the
            # cursor: nothing was read, so nothing may be marked read.
            return (), cursor

        session = EppSession(
            host,
            port,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        events: list[InboundEvent] = []
        last_acked = cursor
        try:
            session.connect()
            login = session.request(frames.login(clid, pw, cltrid="poll-login"))
            if classify_result(login.code) != "ok":
                return (), cursor
            for _ in range(_MAX_PER_PASS):
                res = session.request(frames.poll_request(cltrid="poll-req"))
                poll_status = classify_result(res.code)
                if poll_status == "ok_no_messages" and res.code == 1300:
                    break  # queue empty
                if poll_status not in ("ok", "ok_no_messages"):
                    break  # registry fault; stop, keep the cursor we have
                parsed = _parse_message(res.raw)
                if parsed is None:
                    break
                msg_id, event = parsed
                events.append(event)
                ack = session.request(frames.poll_ack(msg_id, cltrid="poll-ack"))
                if classify_result(ack.code) not in ("ok", "ok_no_messages"):
                    # Could not dequeue: do NOT advance past it. The event is
                    # already returned; the engine's idempotency absorbs the
                    # re-read next pass.
                    break
                last_acked = msg_id
        except (EppTransportError, EppProtocolError):
            # Return whatever we fully acked; the cursor only advances for
            # messages the registry confirmed dequeued.
            pass
        finally:
            try:
                session.request(frames.logout(cltrid="poll-logout"))
            except (EppTransportError, EppProtocolError):
                pass
            session.close()
        return tuple(events), last_acked


def _parse_message(xml: str) -> tuple[str, InboundEvent] | None:
    """Turn one poll response into (message_id, observation), or None if empty."""
    try:
        root = safe_fromstring(xml)
    except ParseError:
        return None
    msgq = root.find(f"{{{EPP_NS}}}response/{{{EPP_NS}}}msgQ")
    if msgq is None:
        return None
    msg_id = msgq.get("id")
    if not msg_id:
        return None
    # The message body is registry-defined; we carry it verbatim as transport
    # evidence rather than interpreting it. The owning application classifies
    # the observation against its own contract.
    body = msgq.find(f"{{{EPP_NS}}}msg")
    text = "".join(body.itertext()).strip() if body is not None else ""
    resdata = root.find(f"{{{EPP_NS}}}response/{{{EPP_NS}}}resData")
    event = InboundEvent(
        provider_event_id=f"nira-msg:{msg_id}",
        event_type=MESSAGE_CAPABILITY,
        payload={
            "capability_id": MESSAGE_CAPABILITY,
            "registry_message_id": msg_id,
            "message_text": text,
            "has_structured_data": resdata is not None,
            "transport_evidence": {"source": "epp_poll"},
        },
        disposition=InboundDisposition.DELIVER,
    )
    return msg_id, event
