"""Lane 3 same-job broker handoff: the shared wire contract, strict and closed.

Protocol ``dotmac.lane3.broker-handoff.v1``. Both sides import this module: the
job-side client (``lane3_handoff_client``) and the host-side controller and
coordinator (``lane3_handoff_controller``, ``lane3_handoff_coordinator``).

Framing is one request per connection: a 4-byte big-endian unsigned length,
then one UTF-8 JSON object of at most 8192 bytes. Decoding refuses duplicate
keys, extra or missing keys, wrong types, nesting deeper than four,
NaN/Infinity, non-UTF-8, surrogates and trailing bytes. Each message has its
own 5 second read/write deadline.

Every refusal is a :class:`ProtocolRefused` carrying a fixed label from
:data:`CATEGORIES`, never input text, exception text, paths or values.

Python standard library only; Python 3.12.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import struct
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

PROTOCOL: Final = "dotmac.lane3.broker-handoff.v1"
MAX_MESSAGE: Final = 8192
MAX_DEPTH: Final = 4
IO_DEADLINE_S: Final = 5.0
HEADER: Final = struct.Struct(">I")

#: The per-lease directory, socket and files (contract "Paths").
RUN_ROOT: Final = "/run/dotmac-lane3-handoff"
SOCKET_NAME: Final = "control.sock"
CHALLENGE_NAME: Final = "challenge.json"
GRANT_NAME: Final = "grant.json"
LEASE_DIR_MODE: Final = 0o750
SOCKET_MODE: Final = 0o660
FILE_MODE: Final = 0o640

#: One common window, a metadata deadline inside it, and grant margins.
WINDOW_NS: Final = 300 * 1_000_000_000
METADATA_DEADLINE_NS: Final = 45 * 1_000_000_000
GRANT_MIN_REMAINING_NS: Final = 45 * 1_000_000_000
SNAPSHOT_MAX_AGE_NS: Final = 60 * 1_000_000_000
MAX_ADDRESSES: Final = 32
BROKER_PORT: Final = 443

STATUSES: Final = frozenset({"WAIT", "REFUSED", "GRANTED", "CONSUMED"})

#: The closed set of refusal categories that may appear in a response.
CATEGORIES: Final = frozenset(
    {
        "frame.invalid",
        "schema.invalid",
        "protocol.mismatch",
        "lease.unknown",
        "nonce.mismatch",
        "peer.refused",
        "state.invalid",
        "report.changed",
        "origin.invalid",
        "origin.not_admitted",
        "origin.flags",
        "binding.mismatch",
        "approval.missing",
        "assignment.ambiguous",
        "snapshot.refused",
        "deadline.insufficient",
        "lease.expired",
        "install.failed",
        "grant.mismatch",
        "grant.consumed",
        "boot.mismatch",
        "internal",
    }
)

_LEASE: Final = re.compile(r"[0-9a-f]{32}")
_NONCE: Final = re.compile(r"[0-9a-f]{64}")
_HEX40: Final = re.compile(r"[0-9a-f]{40}")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_BOOT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_MAX_INT: Final = 2**63 - 1


class ProtocolRefused(ValueError):
    """A fixed refusal. ``label`` is always a member of :data:`CATEGORIES`."""

    def __init__(self, label: str) -> None:
        if label not in CATEGORIES:
            label = "internal"
        super().__init__(label)
        self.label = label

    def __str__(self) -> str:
        return self.label


# ── canonical JSON and digests ──────────────────────────────────────────────


def canonical_json(value: Any) -> bytes:
    """Sorted keys, ``(",", ":")`` separators, UTF-8, no NaN/Infinity."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ProtocolRefused("schema.invalid") from None


def digest(value: Any) -> str:
    """SHA-256 hex of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def grant_digest(grant: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the whole grant object."""
    return digest(dict(grant))


# ── strict decoding ─────────────────────────────────────────────────────────


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolRefused("frame.invalid")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ProtocolRefused("frame.invalid")


def _depth_ok(value: Any, depth: int) -> bool:
    if isinstance(value, dict):
        if depth > MAX_DEPTH:
            return False
        return all(_depth_ok(v, depth + 1) for v in value.values()) and all(
            _string_ok(k) for k in value
        )
    if isinstance(value, list):
        if depth > MAX_DEPTH:
            return False
        return all(_depth_ok(v, depth + 1) for v in value)
    if isinstance(value, str):
        return _string_ok(value)
    return True


def _string_ok(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def decode_message(raw: bytes) -> dict[str, Any]:
    """One strict JSON object from exactly ``raw``; nothing before or after."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_MESSAGE:
        raise ProtocolRefused("frame.invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
        decoder = json.JSONDecoder(
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        if not text.startswith("{"):
            raise ProtocolRefused("frame.invalid")
        value, end = decoder.raw_decode(text)
    except ProtocolRefused:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProtocolRefused("frame.invalid") from None
    if end != len(text) or not isinstance(value, dict):
        raise ProtocolRefused("frame.invalid")
    if not _depth_ok(value, 1):
        raise ProtocolRefused("frame.invalid")
    return value


def encode_message(value: Mapping[str, Any]) -> bytes:
    """Canonical bytes of one object, refused when over :data:`MAX_MESSAGE`."""
    if not isinstance(value, Mapping):
        raise ProtocolRefused("schema.invalid")
    raw = canonical_json(dict(value))
    if len(raw) > MAX_MESSAGE or not _depth_ok(dict(value), 1):
        raise ProtocolRefused("frame.invalid")
    return raw


def frame(value: Mapping[str, Any]) -> bytes:
    """Length prefix plus canonical payload."""
    raw = encode_message(value)
    return HEADER.pack(len(raw)) + raw


# ── socket I/O with a per-message deadline ─────────────────────────────────


def _recv_exact(
    sock: socket.socket, count: int, deadline: float, clock: Callable[[], float]
) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        left = deadline - clock()
        if left <= 0:
            raise ProtocolRefused("frame.invalid")
        sock.settimeout(left)
        try:
            chunk = sock.recv(min(remaining, 4096))
        except OSError:
            raise ProtocolRefused("frame.invalid") from None
        if not chunk:
            raise ProtocolRefused("frame.invalid")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(
    sock: socket.socket,
    *,
    deadline_s: float = IO_DEADLINE_S,
    require_eof: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Read one frame within ``deadline_s``; refuse trailing bytes.

    ``require_eof`` (the client reading a response) additionally requires the
    peer to close after the frame. Without it (the server reading a request)
    any byte already queued after the frame is refused.
    """
    deadline = clock() + deadline_s
    (length,) = HEADER.unpack(_recv_exact(sock, HEADER.size, deadline, clock))
    if not 0 < length <= MAX_MESSAGE:
        raise ProtocolRefused("frame.invalid")
    raw = _recv_exact(sock, length, deadline, clock)
    if require_eof:
        left = deadline - clock()
        if left <= 0:
            raise ProtocolRefused("frame.invalid")
        sock.settimeout(left)
        try:
            extra = sock.recv(1)
        except OSError:
            raise ProtocolRefused("frame.invalid") from None
        if extra:
            raise ProtocolRefused("frame.invalid")
    else:
        # Non-blocking peek: a socket timeout would first wait for data.
        sock.setblocking(False)
        try:
            extra = sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            extra = b""
        except OSError:
            raise ProtocolRefused("frame.invalid") from None
        if extra:
            raise ProtocolRefused("frame.invalid")
    return decode_message(raw)


def write_frame(
    sock: socket.socket,
    value: Mapping[str, Any],
    *,
    deadline_s: float = IO_DEADLINE_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Write one frame within ``deadline_s``."""
    data = memoryview(frame(value))
    deadline = clock() + deadline_s
    while data:
        left = deadline - clock()
        if left <= 0:
            raise ProtocolRefused("frame.invalid")
        sock.settimeout(left)
        try:
            sent = sock.send(data)
        except OSError:
            raise ProtocolRefused("frame.invalid") from None
        data = data[sent:]


# ── field validators ────────────────────────────────────────────────────────


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtocolRefused("schema.invalid")
    return value


def _match(pattern: re.Pattern[str], value: Any) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ProtocolRefused("schema.invalid")
    return value


def _int(value: Any, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_INT:
        raise ProtocolRefused("schema.invalid")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ProtocolRefused("schema.invalid")
    return value


def _protocol(value: Mapping[str, Any]) -> None:
    if value.get("protocol") != PROTOCOL:
        raise ProtocolRefused("protocol.mismatch")


def lease_id(value: Any) -> str:
    return _match(_LEASE, value)


def nonce(value: Any) -> str:
    return _match(_NONCE, value)


def hex40(value: Any) -> str:
    return _match(_HEX40, value)


def hex64(value: Any) -> str:
    return _match(_HEX64, value)


def boot_id(value: Any) -> str:
    return _match(_BOOT, value)


def origin(value: Any) -> str:
    """The canonical broker origin (``lane3_broker_origin.canonical_origin``).

    The import is deferred so this module stays importable on its own; a
    missing or failing origin module refuses, it never relaxes the check.
    """
    try:
        from lane3_broker_origin import canonical_origin

        canonical = canonical_origin(value)
    except Exception:
        raise ProtocolRefused("origin.invalid") from None
    if canonical != value:
        raise ProtocolRefused("origin.invalid")
    return canonical


def address_row(value: Any) -> dict[str, Any]:
    """``{"family": 4|6, "address": str}`` in canonical compressed form."""
    row = _exact(value, {"family", "address"})
    family, text = row["family"], row["address"]
    if type(family) is not int or family not in (4, 6) or type(text) is not str:
        raise ProtocolRefused("schema.invalid")
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        raise ProtocolRefused("schema.invalid") from None
    if parsed.version != family or str(parsed) != text:
        raise ProtocolRefused("schema.invalid")
    return row


def validate_challenge(value: Any) -> dict[str, Any]:
    challenge = _exact(
        value, {"protocol", "lease_id", "nonce", "boot_id", "expires_at_monotonic_ns"}
    )
    _protocol(challenge)
    lease_id(challenge["lease_id"])
    nonce(challenge["nonce"])
    boot_id(challenge["boot_id"])
    _int(challenge["expires_at_monotonic_ns"])
    return challenge


def validate_report(value: Any) -> dict[str, Any]:
    report = _exact(
        value, {"protocol", "op", "lease_id", "nonce", "origin", "flags", "expected"}
    )
    _protocol(report)
    if report["op"] != "report":
        raise ProtocolRefused("schema.invalid")
    lease_id(report["lease_id"])
    nonce(report["nonce"])
    flags = _exact(report["flags"], {"explicit_port_present", "userinfo_present"})
    _bool(flags["explicit_port_present"])
    _bool(flags["userinfo_present"])
    expected = _exact(
        report["expected"], {"run_id", "run_attempt", "workflow_sha", "starter_commit"}
    )
    _int(expected["run_id"])
    _int(expected["run_attempt"])
    hex40(expected["workflow_sha"])
    hex40(expected["starter_commit"])
    origin(report["origin"])
    return report


def validate_poll(value: Any) -> dict[str, Any]:
    poll = _exact(value, {"protocol", "op", "lease_id", "nonce"})
    _protocol(poll)
    if poll["op"] != "poll":
        raise ProtocolRefused("schema.invalid")
    lease_id(poll["lease_id"])
    nonce(poll["nonce"])
    return poll


def validate_consume(value: Any) -> dict[str, Any]:
    consume = _exact(value, {"protocol", "op", "lease_id", "nonce", "grant_digest"})
    _protocol(consume)
    if consume["op"] != "consume":
        raise ProtocolRefused("schema.invalid")
    lease_id(consume["lease_id"])
    nonce(consume["nonce"])
    hex64(consume["grant_digest"])
    return consume


_REQUESTS: Final[dict[str, Callable[[Any], dict[str, Any]]]] = {
    "report": validate_report,
    "poll": validate_poll,
    "consume": validate_consume,
}


def validate_request(value: Any) -> dict[str, Any]:
    """Dispatch on ``op`` to exactly one of the three request schemas."""
    if not isinstance(value, dict):
        raise ProtocolRefused("schema.invalid")
    _protocol(value)
    op = value.get("op")
    if type(op) is not str or op not in _REQUESTS:
        raise ProtocolRefused("schema.invalid")
    return _REQUESTS[op](value)


def response(status: str, category: str | None = None) -> dict[str, Any]:
    """Build a response; ``category`` only (and always) with REFUSED."""
    value = {"protocol": PROTOCOL, "status": status, "category": category}
    return validate_response(value)


def validate_response(value: Any) -> dict[str, Any]:
    reply = _exact(value, {"protocol", "status", "category"})
    _protocol(reply)
    status, category = reply["status"], reply["category"]
    if type(status) is not str or status not in STATUSES:
        raise ProtocolRefused("schema.invalid")
    if status == "REFUSED":
        if type(category) is not str or category not in CATEGORIES:
            raise ProtocolRefused("schema.invalid")
    elif category is not None:
        raise ProtocolRefused("schema.invalid")
    return reply


_BINDING_KEYS: Final = {
    "repository_id",
    "run_id",
    "run_attempt",
    "job_id",
    "runner_id",
    "workflow_sha",
    "workflow_blob",
    "starter_commit",
}
_GRANT_KEYS: Final = {
    "protocol",
    "state",
    "lease_id",
    "nonce",
    "boot_id",
    "sequence",
    "expires_at_monotonic_ns",
    "binding",
    "origin",
    "snapshot",
    "policy_digest",
    "manifest_digest",
    "controller_digest",
}


def validate_binding(value: Any) -> dict[str, Any]:
    binding = _exact(value, _BINDING_KEYS)
    for key in ("repository_id", "run_id", "run_attempt", "job_id", "runner_id"):
        _int(binding[key])
    for key in ("workflow_sha", "workflow_blob", "starter_commit"):
        hex40(binding[key])
    return binding


def validate_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _exact(value, {"digest", "expires_at_monotonic_ns", "port", "addresses"})
    hex64(snapshot["digest"])
    _int(snapshot["expires_at_monotonic_ns"])
    if type(snapshot["port"]) is not int or snapshot["port"] != BROKER_PORT:
        raise ProtocolRefused("schema.invalid")
    rows = snapshot["addresses"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ADDRESSES:
        raise ProtocolRefused("schema.invalid")
    seen: set[str] = set()
    for row in rows:
        address_row(row)
        if row["address"] in seen:
            raise ProtocolRefused("schema.invalid")
        seen.add(row["address"])
    return snapshot


def validate_grant(value: Any) -> dict[str, Any]:
    """The full grant schema; it says nothing about whether it is authentic."""
    grant = _exact(value, _GRANT_KEYS)
    _protocol(grant)
    if grant["state"] != "GRANTED":
        raise ProtocolRefused("schema.invalid")
    lease_id(grant["lease_id"])
    nonce(grant["nonce"])
    boot_id(grant["boot_id"])
    if type(grant["sequence"]) is not int or grant["sequence"] != 1:
        raise ProtocolRefused("schema.invalid")
    _int(grant["expires_at_monotonic_ns"])
    validate_binding(grant["binding"])
    origin(grant["origin"])
    # Either expiry may be the earlier one; the connection deadline is
    # min(lease expiry, snapshot expiry) and the client computes it.
    validate_snapshot(grant["snapshot"])
    for key in ("policy_digest", "manifest_digest", "controller_digest"):
        hex64(grant[key])
    if len(canonical_json(grant)) > MAX_MESSAGE:
        raise ProtocolRefused("schema.invalid")
    return grant
