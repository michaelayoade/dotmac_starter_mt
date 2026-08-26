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
plus the cursor to persist next. The cursor is proof that Integration durably
recorded that registry message on an EARLIER call; only then may this handler
ack the same queue head. It returns at most the next head without acking it.
That two-phase handshake means a crash before record re-reads the unacked head,
while a crash after an ambiguous ack is recovered by comparing the next head
with the still-persisted cursor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from xml.etree.ElementTree import (  # nosec B405 - parse happens behind DTD gate
    ParseError,
    tostring,
)

from dotmac_integration.spi import InboundDisposition, InboundEvent

from dotmac_connector_nira import frames
from dotmac_connector_nira.delivery import (
    ALLOWED_EGRESS_HOSTS,
    CLIENT_PEM,
    EPP_PASSWORD,
    _material,
    _num,
    _text,
)
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


class NiraPollHandler:
    """Read one new head and ack only an earlier durably persisted head."""

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
            host not in ALLOWED_EGRESS_HOSTS
            or not isinstance(port, int)
            or isinstance(port, bool)
            or clid is None
            or pw is None
            or connect_timeout is None
            or read_timeout is None
        ):
            raise EppProtocolError("poll configuration or required material is invalid")

        session = EppSession(
            host,
            port,
            client_pem=_material(secrets, CLIENT_PEM),
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        try:
            session.connect()
            login = session.request(frames.login(clid, pw, cltrid="poll-login"))
            if classify_result(login.code) != "ok":
                raise EppProtocolError(
                    f"registry refused poll login with EPP {login.code}"
                )

            head = _read_head(session)
            if head is None:
                return (), cursor

            msg_id, event = head
            if cursor is not None and msg_id == cursor:
                # The input cursor is durable Integration state from the prior
                # call. Equality with the live head is the only proof that this
                # exact message is safe to remove from the provider queue.
                ack = session.request(frames.poll_ack(msg_id, cltrid="poll-ack"))
                if classify_result(ack.code) not in ("ok", "ok_no_messages"):
                    raise EppProtocolError("registry refused the poll acknowledgement")

                # Only after the durable predecessor is gone may one new head
                # be returned. It remains unacked until its returned cursor is
                # committed and supplied on a later call.
                head = _read_head(session)
                if head is None:
                    return (), cursor
                msg_id, event = head

            # A different head means an earlier ambiguous ack already took
            # effect. Never ack the different id using a stale cursor: return
            # it for Integration to persist first.
            return (event,), msg_id
        finally:
            try:
                session.request(frames.logout(cltrid="poll-logout"))
            except (EppTransportError, EppProtocolError):
                pass
            session.close()


def _read_head(session: EppSession) -> tuple[str, InboundEvent] | None:
    """Read, but never acknowledge, the current queue head."""
    result = session.request(frames.poll_request(cltrid="poll-req"))
    status = classify_result(result.code)
    if status == "ok_no_messages" and result.code == 1300:
        return None
    if status not in ("ok", "ok_no_messages"):
        raise EppProtocolError("registry refused the poll request")
    parsed = _parse_message(result.raw)
    if parsed is None:
        raise EppProtocolError("poll response carries no queue message")
    return parsed


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
    # The message body and structured result are registry-defined. Preserve the
    # serialized subtree as transport evidence rather than interpreting it: a
    # later durable-cursor call may delete the provider's only copy.
    body = msgq.find(f"{{{EPP_NS}}}msg")
    text = "".join(body.itertext()).strip() if body is not None else ""
    resdata = root.find(f"{{{EPP_NS}}}response/{{{EPP_NS}}}resData")
    resdata_xml = tostring(resdata, encoding="unicode") if resdata is not None else None
    event = InboundEvent(
        provider_event_id=f"nira-msg:{msg_id}",
        event_type=MESSAGE_CAPABILITY,
        payload={
            "capability_id": MESSAGE_CAPABILITY,
            "registry_message_id": msg_id,
            "message_text": text,
            "has_structured_data": resdata is not None,
            "transport_evidence": {
                "source": "epp_poll",
                "res_data_xml": resdata_xml,
            },
        },
        disposition=InboundDisposition.DELIVER,
    )
    return msg_id, event
