"""UdpCoaPort against a fake NAS that speaks RFC 5176 back."""

from __future__ import annotations

import hashlib
import socket
import struct
import threading

from dotmac_connector_freeradius.coa import UdpCoaPort

SHARED = "shared-value"


def _nas(response_code: int) -> tuple[int, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    def serve() -> None:
        request, sender = sock.recvfrom(4096)
        code, identifier, _length = struct.unpack("!BBH", request[:4])
        assert code == 40
        attrs = b""
        if response_code == 42:
            cause = struct.pack("!I", 503)
            attrs = struct.pack("!BB", 101, len(cause) + 2) + cause
        header = struct.pack("!BBH", response_code, identifier, 20 + len(attrs))
        authenticator = hashlib.md5(  # noqa: S324
            header + request[4:20] + attrs + SHARED.encode()
        ).digest()
        sock.sendto(header + authenticator + attrs, sender)
        sock.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def test_ack_is_acknowledged() -> None:
    port, thread = _nas(41)
    outcome = UdpCoaPort().disconnect(
        address="127.0.0.1",
        port=port,
        secret=SHARED,
        attributes={"User-Name": "user@dotmac"},
        timeout_seconds=2.0,
    )
    thread.join(timeout=2)
    assert outcome.outcome == "acknowledged"


def test_nak_is_refused_with_cause() -> None:
    port, thread = _nas(42)
    outcome = UdpCoaPort().disconnect(
        address="127.0.0.1",
        port=port,
        secret=SHARED,
        attributes={"User-Name": "user@dotmac"},
        timeout_seconds=2.0,
    )
    thread.join(timeout=2)
    assert outcome.outcome == "refused"
    assert outcome.error_cause == "503"


def test_silence_is_unanswered() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    outcome = UdpCoaPort().disconnect(
        address="127.0.0.1",
        port=port,
        secret=SHARED,
        attributes={"User-Name": "user@dotmac"},
        timeout_seconds=0.3,
    )
    sock.close()
    assert outcome.outcome == "unanswered"
