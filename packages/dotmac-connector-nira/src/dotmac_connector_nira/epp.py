"""The EPP-over-TLS transport core: frames on the wire, nothing above them.

This is the one substantial file in the connector, and it is still only
transport. It opens a TLS socket to the registry, exchanges length-prefixed
EPP frames (RFC 5734), and classifies the ``<result code>`` a response
carries. It decides nothing about domains: whether a name *should* be
registered, what it costs, or whether to try again are the owning
application's and the engine's, never this module's.

The framing is RFC 5734 § 4: each frame is a 4-byte big-endian total length
(header included) followed by the XML. A blocking socket reads exactly that
many bytes; a short read is a broken connection, not an empty frame.

Ported in shape from the CoCCA ``Net/EPP`` reference client shipped in NiRA's
registrar module (``cocca-whmcs-v9-2026-02-27``). The reference is PHP over a
PHP stream; this is Python over ``ssl``. The wire contract is identical.
"""

from __future__ import annotations

import re
import socket
import ssl
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from xml.etree import ElementTree as ET  # nosec B405 - DTD gate below is tested

__all__ = [
    "EppError",
    "EppProtocolError",
    "EppTransportError",
    "EppResult",
    "EppSession",
    "classify_result",
    "safe_fromstring",
    "EPP_NS",
]

#: RFC 5730 EPP namespace. Objects (domain/host/contact) and extensions
#: (fee/secDNS/rgp) declare their own; those live in the frame builders, not
#: here, because this module frames and reads — it does not compose commands.
EPP_NS: Final = "urn:ietf:params:xml:ns:epp-1.0"

#: RFC 5734 § 4.1: the length field includes its own four octets. A frame may
#: not be larger than the registry admits; 1 MiB is far above any real EPP
#: response and guards a hostile or desynchronised peer from a huge allocation.
_MAX_FRAME: Final = 1 << 20
_HEADER: Final = 4


def safe_fromstring(xml: str) -> ET.Element:
    """Parse EPP XML from the socket with entity/DTD attacks refused.

    The registry is authenticated, but its frames still arrive over the wire and
    are parsed before anything is trusted, so they get the same hardening as any
    untrusted input. ``defusedxml`` is deliberately not a dependency (this
    connector is stdlib-only), and CPython's C-accelerated ``ElementTree`` parser
    does not expose the expat handle needed to install a DOCTYPE handler. So the
    prolog is scanned for a document type declaration and the parse is refused if
    one is present, BEFORE handing the (verified DTD-free) document to
    ``ET.fromstring`` — which keeps ElementTree's namespace handling intact.

    Refusing a DOCTYPE defeats both classes of XML attack at once: billion-laughs
    needs internal entities declared in a DTD, and external-entity injection needs
    a DOCTYPE to declare the entity. A legitimate EPP frame never carries a DTD,
    so this rejects nothing the registry legitimately sends. External general
    entities cannot appear without the DOCTYPE that declares them, so scanning the
    prolog is sufficient — an ``&name;`` reference in element content with no DTD
    is simply an undefined-entity parse error.
    """
    if _DOCTYPE_RE.search(xml):
        raise EppProtocolError("DTD/DOCTYPE is not permitted in an EPP frame")
    return ET.fromstring(xml)  # nosec B314  # noqa: S314 (DTD refused above)


#: A document type declaration. A DOCTYPE is only legal in the prolog, and a
#: ``<!`` sequence in element content can only begin a comment or CDATA, never a
#: DOCTYPE — so a match anywhere in the document is a real DTD to refuse.
_DOCTYPE_RE: Final = re.compile(r"<!DOCTYPE", re.IGNORECASE)


class EppError(Exception):
    """Base class for every EPP transport or protocol fault."""


class EppTransportError(EppError):
    """The socket failed: connect, TLS, or a truncated/oversized frame.

    Retry safety depends on WHEN it failed. A connect/login failure precedes a
    business effect and may be retried; a failure while sending or reading the
    business command is ambiguous because the registry may already have
    committed it. The delivery adapter owns that phase-aware classification.
    """


class EppProtocolError(EppError):
    """A frame arrived but is not EPP we can read (bad XML, no result code)."""


@dataclass(frozen=True, slots=True)
class EppResult:
    """One registry ``<result>``: the code, its message, and the raw response.

    ``code`` is the RFC 5730 four-digit result. ``raw`` is the response XML,
    retained so a delivery handler can extract the typed fields its capability
    contract publishes (a domain's ``exDate``, a check's ``avail`` flag) —
    parsing that is the handler's job against its own schema, not this layer's.
    """

    code: int
    message: str
    raw: str


def classify_result(code: int) -> str:
    """Name an EPP result code in the CONNECTOR's vocabulary.

    Returns a short stable token the delivery handler maps to an engine
    ``OutcomeStatus``. This function does not itself decide retry vs terminal —
    it gives the handler a named category so that mapping is one reviewable
    table rather than scattered integer comparisons. The engine never sees
    these strings; it sees the ``Outcome`` the handler builds from them.
    """
    if code in (1000, 1500):  # ordinary success / successful session-ending logout
        return "ok"
    if code in (1001,):
        return "pending"  # accepted, action completes asynchronously
    if code in (1300, 1301):
        return "ok_no_messages"  # poll queue empty / message dequeued
    if code in (2302,):
        return "object_exists"
    if code in (2303,):
        return "object_absent"
    if code in (2201, 2202):
        return "authorization"  # not authorized / IP prohibited
    if code in (2200,):
        return "authentication"
    if 2000 <= code < 2400:
        return "client_error"  # our command was malformed/invalid
    if 2400 <= code < 2600:
        return "registry_failure"  # command failed / registry unavailable
    return "unknown"


class EppSession:
    """A single TLS-framed EPP conversation.

    Stateless across dispatches by design: the engine hands a handler
    materialized secrets per call, the handler opens a session, runs its one
    command (after ``login``), and closes. Nothing here is cached between
    invocations — no pooled socket, no retained credential. A pooled session
    would be state this connector is forbidden to own.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        client_pem: str | None = None,
        connect_timeout: float = 15.0,
        read_timeout: float = 30.0,
    ) -> None:
        self._host = host
        self._port = port
        self._client_pem = client_pem
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._sock: ssl.SSLSocket | None = None

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> str:
        """Open TLS, read and return the registry ``<greeting>`` XML.

        The greeting is unsolicited: RFC 5734 § 2 says the server sends it the
        moment the transport is up, before any command. Reading it here both
        proves the channel and lets ``validate_connection`` inspect the
        registry's declared objects and extensions without a login.
        """
        context = self._tls_context()
        try:
            raw = socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            )
        except OSError as exc:
            raise EppTransportError(
                f"connect to {self._host}:{self._port} failed: {exc}"
            ) from exc
        try:
            self._sock = context.wrap_socket(raw, server_hostname=self._host)
            self._sock.settimeout(self._read_timeout)
        except (ssl.SSLError, OSError) as exc:
            raw.close()
            raise EppTransportError(f"TLS handshake failed: {exc}") from exc
        return self._read_frame()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> EppSession:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- request/response ----------------------------------------------------

    def request(self, xml: str) -> EppResult:
        """Send one command frame, read one response, classify its result."""
        self._write_frame(xml)
        return self._parse_result(self._read_frame())

    # -- framing (RFC 5734 § 4) ---------------------------------------------

    def _write_frame(self, xml: str) -> None:
        if self._sock is None:
            raise EppTransportError("session is not connected")
        body = xml.encode("utf-8")
        header = struct.pack(">I", len(body) + _HEADER)
        try:
            self._sock.sendall(header + body)
        except OSError as exc:
            raise EppTransportError(f"send failed: {exc}") from exc

    def _read_frame(self) -> str:
        header = self._read_exactly(_HEADER)
        total = struct.unpack(">I", header)[0]
        length = total - _HEADER
        if length < 0 or length > _MAX_FRAME:
            raise EppTransportError(f"implausible frame length {length}")
        return self._read_exactly(length).decode("utf-8", "replace")

    def _read_exactly(self, count: int) -> bytes:
        if self._sock is None:
            raise EppTransportError("session is not connected")
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self._sock.recv(remaining)
            except OSError as exc:
                raise EppTransportError(f"read failed: {exc}") from exc
            if not chunk:
                raise EppTransportError("connection closed mid-frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- result extraction ---------------------------------------------------

    def _parse_result(self, xml: str) -> EppResult:
        try:
            root = safe_fromstring(xml)
        except ET.ParseError as exc:
            raise EppProtocolError(f"response is not XML: {exc}") from exc
        result = root.find(f"{{{EPP_NS}}}response/{{{EPP_NS}}}result")
        if result is None:
            raise EppProtocolError("response carries no <result>")
        code_attr = result.get("code")
        if code_attr is None or not code_attr.isdigit():
            raise EppProtocolError("response <result> has no numeric code")
        msg_el = result.find(f"{{{EPP_NS}}}msg")
        message = (msg_el.text or "").strip() if msg_el is not None else ""
        return EppResult(code=int(code_attr), message=message, raw=xml)

    # -- TLS -----------------------------------------------------------------

    def _tls_context(self) -> ssl.SSLContext:
        """Build the client TLS context, loading the registrar cert if present.

        The registry pins registrar access to a source IP and, where required,
        a client certificate. The cert+key are supplied as ONE concatenated
        PEM (the CoCCA reference's documented shape, and the fix for its most
        common install failure). Verification of the registry's own cert is
        left to the platform trust store; a private registry with a
        self-signed cert is configured out of band, not weakened here.
        """
        context = ssl.create_default_context()
        if self._client_pem:
            _load_client_pem(context, self._client_pem)
        return context


def _load_client_pem(context: ssl.SSLContext, pem: str) -> None:
    """Load a combined cert+key PEM into the context via a transient file.

    ``SSLContext.load_cert_chain`` requires a filesystem path; the PEM arrives
    as materialized secret text and must never be persisted. It is written to a
    private temp file, loaded, and unlinked immediately — on disk only for the
    duration of the load call, and only readable by this process.
    """
    import os
    import tempfile

    fd, path = tempfile.mkstemp(prefix="nira-epp-", suffix=".pem")
    try:
        os.write(fd, pem.encode("utf-8"))
        os.close(fd)
        context.load_cert_chain(path)
    except (ssl.SSLError, OSError) as exc:
        raise EppTransportError(f"client certificate load failed: {exc}") from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def declared_services(greeting_xml: str) -> Mapping[str, tuple[str, ...]]:
    """Extract the objects and extensions a greeting declares.

    Used by ``validate_connection`` to confirm the registry offers what the
    connector needs (domain/host/contact objects and the fee extension) before
    an installation is ever enabled. Returns a plain mapping
    so the diagnostic layer decides sufficiency — this function only reads.
    """
    try:
        root = safe_fromstring(greeting_xml)
    except ET.ParseError as exc:
        raise EppProtocolError(f"greeting is not XML: {exc}") from exc
    svc = root.find(f"{{{EPP_NS}}}greeting/{{{EPP_NS}}}svcMenu")
    if svc is None:
        return {"objects": (), "extensions": ()}
    objects = tuple(
        el.text or "" for el in svc.findall(f"{{{EPP_NS}}}objURI") if el.text
    )
    ext_menu = svc.find(f"{{{EPP_NS}}}svcExtension")
    extensions = (
        tuple(
            el.text or "" for el in ext_menu.findall(f"{{{EPP_NS}}}extURI") if el.text
        )
        if ext_menu is not None
        else ()
    )
    return {"objects": objects, "extensions": extensions}
