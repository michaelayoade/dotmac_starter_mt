"""RFC 5176 Disconnect-Request over stdlib UDP (no pyrad dependency).

Packet discipline mirrors Sub's proven pair: the hand-rolled Access-Request
in `radius_probe.py` (authenticator construction, attribute TLVs, response
verification) and the Disconnect semantics in `services/enforcement.py`
(ACK=41, NAK=42, Error-Cause on refusal). The Response Authenticator is
verified before any outcome is believed; an unverifiable reply is
`unanswered`, whatever its code byte claims.
"""

from __future__ import annotations

import hashlib
import secrets
import socket
import struct
from collections.abc import Mapping

from dotmac_connector_freeradius.ports import CoaOutcome

_DISCONNECT_REQUEST = 40
_DISCONNECT_ACK = 41
_DISCONNECT_NAK = 42

#: Attribute types the disconnect command may name (RFC 2865/5176 numbers).
_ATTRIBUTE_TYPES: dict[str, int] = {
    "User-Name": 1,
    "NAS-IP-Address": 4,
    "Framed-IP-Address": 8,
    "Acct-Session-Id": 44,
    "Error-Cause": 101,
}


def _encode_attributes(attributes: Mapping[str, str]) -> bytes:
    out = bytearray()
    for name, value in attributes.items():
        type_id = _ATTRIBUTE_TYPES.get(name)
        if type_id is None:
            continue
        raw = value.encode("utf-8")[:253]
        out += struct.pack("!BB", type_id, len(raw) + 2) + raw
    return bytes(out)


def _parse_attributes(payload: bytes) -> dict[int, list[bytes]]:
    parsed: dict[int, list[bytes]] = {}
    offset = 0
    while offset + 2 <= len(payload):
        type_id, length = payload[offset], payload[offset + 1]
        if length < 2 or offset + length > len(payload):
            break
        parsed.setdefault(type_id, []).append(payload[offset + 2 : offset + length])
        offset += length
    return parsed


class UdpCoaPort:
    """One request, one bounded wait, no retries — retry is the engine's."""

    def disconnect(
        self,
        *,
        address: str,
        port: int,
        secret: str,
        attributes: Mapping[str, str],
        timeout_seconds: float,
    ) -> CoaOutcome:
        secret_bytes = secret.encode("utf-8")
        identifier = secrets.randbelow(256)
        encoded = _encode_attributes(attributes)
        header = struct.pack("!BBH", _DISCONNECT_REQUEST, identifier, 20 + len(encoded))
        # RFC 2865/5176 authenticators are MD5 by specification.
        authenticator = hashlib.md5(  # noqa: S324 # nosec B324 -- RFC 2865/5176 authenticators are MD5 by specification; the protocol offers no stronger option
            header + b"\x00" * 16 + encoded + secret_bytes
        ).digest()
        packet = header + authenticator + encoded

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_seconds)
            try:
                sock.sendto(packet, (address, port))
                response, _ = sock.recvfrom(4096)
            except TimeoutError:
                return CoaOutcome(outcome="unanswered")
            except OSError:
                return CoaOutcome(outcome="unanswered")

        if len(response) < 20:
            return CoaOutcome(outcome="unanswered")
        code, response_id, length = struct.unpack("!BBH", response[:4])
        if response_id != identifier or length > len(response):
            return CoaOutcome(outcome="unanswered")
        response = response[:length]
        expected = hashlib.md5(  # noqa: S324 # nosec B324 -- RFC 2865/5176 authenticators are MD5 by specification; the protocol offers no stronger option
            response[:4] + authenticator + response[20:] + secret_bytes
        ).digest()
        if expected != response[4:20]:
            return CoaOutcome(
                outcome="unanswered", error_cause="authenticator_mismatch"
            )
        if code == _DISCONNECT_ACK:
            return CoaOutcome(outcome="acknowledged")
        if code == _DISCONNECT_NAK:
            causes = _parse_attributes(response[20:]).get(101, [])
            cause = None
            if causes and len(causes[0]) >= 4:
                cause = str(struct.unpack("!I", causes[0][:4])[0])
                if cause in ("401", "404"):
                    # Unsupported attribute / NAS not CoA-capable in fleet use.
                    return CoaOutcome(outcome="unsupported", error_cause=cause)
            return CoaOutcome(outcome="refused", error_cause=cause)
        return CoaOutcome(outcome="unanswered", error_cause=f"unexpected_code_{code}")


__all__ = ["UdpCoaPort"]
