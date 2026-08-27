"""A fake CoCCA EPP server: real framing, canned responses, zero network egress.

The connector's transport speaks length-prefixed EPP over a TLS socket. To test
handlers without the registry, this starts a plaintext EPP server on localhost
that frames responses exactly as RFC 5734 requires and answers a small script
of commands. It proves the wire translation — a passing fake is not evidence
the real registry works, but it gates every regression in the framing and the
result-code mapping.

TLS is deliberately omitted here: the transport's ``connect`` wraps in TLS, so
the fake is used by pointing the session's raw framing at it (see the tests),
not by standing up a certificate. The framing, not the crypto, is what these
tests exercise.
"""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Callable, Iterator

import pytest

_GREETING = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<epp xmlns="urn:ietf:params:xml:ns:epp-1.0"><greeting>'
    "<svID>NIRA-FAKE</svID><svDate>2026-08-26T00:00:00.0Z</svDate>"
    "<svcMenu>"
    "<objURI>urn:ietf:params:xml:ns:domain-1.0</objURI>"
    "<objURI>urn:ietf:params:xml:ns:host-1.0</objURI>"
    "<objURI>urn:ietf:params:xml:ns:contact-1.0</objURI>"
    "<svcExtension>"
    "<extURI>urn:ietf:params:xml:ns:epp:fee-1.0</extURI>"
    "<extURI>urn:ietf:params:xml:ns:secDNS-1.1</extURI>"
    "</svcExtension>"
    "</svcMenu></greeting></epp>"
)


def _result(code: int, msg: str, extra: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<epp xmlns="urn:ietf:params:xml:ns:epp-1.0"><response>'
        f'<result code="{code}"><msg>{msg}</msg></result>'
        f"{extra}"
        "</response></epp>"
    )


class FakeEppServer:
    """A one-connection EPP server driven by a command->response function."""

    def __init__(self, responder: Callable[[str], str]) -> None:
        self._responder = responder
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            self._write(conn, _GREETING)
            while True:
                frame = self._read(conn)
                if frame is None:
                    return
                reply = self._responder(frame)
                self._write(conn, reply)
                if "<logout" in frame:
                    return

    @staticmethod
    def _write(conn: socket.socket, xml: str) -> None:
        body = xml.encode()
        conn.sendall(struct.pack(">I", len(body) + 4) + body)

    @staticmethod
    def _read(conn: socket.socket) -> str | None:
        header = b""
        while len(header) < 4:
            chunk = conn.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        length = struct.unpack(">I", header)[0] - 4
        body = b""
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                return None
            body += chunk
        return body.decode()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def default_responder(frame: str) -> str:
    """Answer a small canonical script of EPP commands."""
    if "<login" in frame:
        return _result(1000, "Command completed successfully")
    if "<logout" in frame:
        return _result(1500, "Command completed successfully; ending session")
    if "<domain:check" in frame:
        extra = (
            "<resData><domain:chkData "
            'xmlns:domain="urn:ietf:params:xml:ns:domain-1.0">'
            '<domain:cd><domain:name avail="1">example.ng</domain:name>'
            "</domain:cd></domain:chkData></resData>"
        )
        return _result(1000, "Command completed successfully", extra)
    if "<host:create" in frame:
        return _result(1000, "Command completed successfully")
    if '<poll op="req"' in frame:
        return _result(1300, "Command completed successfully; no messages")
    return _result(2400, "Command failed")


@pytest.fixture
def fake_registry() -> Iterator[FakeEppServer]:
    server = FakeEppServer(default_responder)
    server.start()
    try:
        yield server
    finally:
        server.close()
