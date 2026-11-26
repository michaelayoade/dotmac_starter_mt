"""The target request/response signing contract (DESIGN-rev3 s2.6).

Every request carries an Ed25519 signature from the TargetCredential PoP key over
`(method, path, sha256(body), credential_id, target_ref, issued_at, nonce)`.
Every response is signed under the purpose `cp-target-response.v1`.

The byte layout, header names, path shape and encodings are owned by Deployment
Control (`contract/target_api_v1.json`, ruling R1); this module is the agent's
copy, pinned by the vendored vectors (`tests/unit/test_deploy_agent_conformance.py`).
Keys and signatures are canonical UNPADDED base64url.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canon import sha256_hex
from .refusal import Refusal

REQUEST_PURPOSE: Final = "dotmac.target_request.v1"
RESPONSE_PURPOSE: Final = "cp-target-response.v1"
BASE_PATH: Final = "/platform/deploy/target/v1"

H_CREDENTIAL: Final = "X-Dotmac-Credential-Id"
H_TARGET: Final = "X-Dotmac-Target-Ref"
H_ISSUED_AT: Final = "X-Dotmac-Issued-At"
H_NONCE: Final = "X-Dotmac-Nonce"
H_SIGNATURE: Final = "X-Dotmac-Signature"
H_RESPONSE_SIGNATURE: Final = "X-Dotmac-Response-Signature"
MAX_SKEW_SECONDS: Final = 60

#: The agent's routes and their fixed verbs (Control's `ENDPOINTS`, caller=agent).
#: `resolve`, `consume` and `committed/<id>` belong to the bindings, never here.
ROUTES: Final = {
    "materials": "GET",
    "observations": "POST",
    "lease/acquire": "POST",
    "lease/covers": "POST",
    "lease/release": "POST",
    "lease/standing": "GET",
}
#: Routes of Control's target_api `_v2` that the vendored v1 vectors do not yet
#: carry. Kept apart so `ROUTES` stays equal to the vendored file; they fold into
#: `ROUTES` (and this goes away) when the `_v2` file is vendored.
PENDING_V2_ROUTES: Final = {"lease/renew": "POST"}
_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_NONCE: Final = re.compile(r"[0-9a-f]{32}")
_STAMP: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_B64URL: Final = re.compile(r"[A-Za-z0-9_-]*")


def check_route(method: str, route: str) -> None:
    if {**ROUTES, **PENDING_V2_ROUTES}.get(route) != method:
        raise Refusal("materials_invalid")


def target_path(target_ref: str, route: str) -> str:
    """The route as signed: three plain segments, no encoding, no query string."""
    parts = target_ref.split("/")
    if len(parts) != 3 or not all(_SEGMENT.fullmatch(part) for part in parts):
        raise Refusal("config_invalid")
    if route not in ROUTES and route not in PENDING_V2_ROUTES:
        raise Refusal("materials_invalid")
    return f"{BASE_PATH}/targets/{target_ref}/{route}"


def check_nonce(value: str) -> str:
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        raise Refusal("materials_invalid")
    return value


def request_headers(
    *,
    credential_id: str,
    target_ref: str,
    issued_at: str,
    nonce: str,
    signature_b64url: str,
) -> dict[str, str]:
    return {
        H_CREDENTIAL: credential_id,
        H_TARGET: target_ref,
        H_ISSUED_AT: issued_at,
        H_NONCE: check_nonce(nonce),
        H_SIGNATURE: signature_b64url,
    }


def classify_unsigned_response(status: int) -> str:
    """An UNSIGNED 413, 429 or 5xx is a retryable fault, not a forgery."""
    if status in (413, 429) or 500 <= status <= 599:
        return "cp_unreachable"
    return "response_signature_invalid"


def _line(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise Refusal("materials_invalid")
    return value


def request_message(
    *,
    method: str,
    path: str,
    body: bytes,
    credential_id: str,
    target_ref: str,
    issued_at: str,
    nonce: str,
) -> bytes:
    parse_stamp(issued_at)
    check_nonce(nonce)
    parts = [
        REQUEST_PURPOSE,
        method.upper(),
        path,
        sha256_hex(body),
        credential_id,
        target_ref,
        issued_at,
        nonce,
    ]
    return "\n".join(_line(part) for part in parts).encode("utf-8")


def response_message(
    *, method: str, path: str, request_nonce: str, status: int, body: bytes
) -> bytes:
    """Bound to the request nonce: a captured response answers nothing else."""
    check_nonce(request_nonce)
    parts = [
        RESPONSE_PURPOSE,
        method.upper(),
        path,
        request_nonce,
        str(status),
        sha256_hex(body),
    ]
    return "\n".join(_line(part) for part in parts).encode("utf-8")


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    """`YYYY-MM-DDTHH:MM:SSZ` and nothing else."""
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise Refusal("response_malformed")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Refusal("response_malformed") from None
    return parsed.replace(tzinfo=UTC)


def check_skew(issued_at: str, store_now: str) -> None:
    """Refuse anything more than 60 s from the signed `store_now`."""
    delta = abs((parse_stamp(issued_at) - parse_stamp(store_now)).total_seconds())
    if delta > MAX_SKEW_SECONDS:
        raise Refusal("clock_skew")


def new_nonce() -> str:
    return secrets.token_hex(16)


def encode_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: object) -> bytes | None:
    """Strict, as Control's `b64u_decode`: no padding, no standard alphabet, no
    whitespace. None when refused."""
    if (
        not isinstance(value, str)
        or not _B64URL.fullmatch(value)
        or len(value) % 4 == 1
    ):
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        return None
    # Non-zero trailing bits decode to the same bytes (`AB` == `AA`): one
    # signature would have several spellings. Require the round trip (Control).
    return raw if encode_b64url(raw) == value else None


def decode_b64url(value: object, *, length: int) -> bytes | None:
    """Canonical unpadded base64url of exactly `length` bytes, else None."""
    raw = b64url_decode(value)
    return raw if raw is not None and len(raw) == length else None


#: Public keys of the two Ed25519 seeds Control's vector file PUBLISHES. They
#: authenticate nobody: the agent refuses them as a pinned CP response key or a
#: PoP key (Control's `DENIED_PUBLIC_KEYS_B64U`; vector `denied_public_keys`).
DENIED_PUBLIC_KEYS_B64URL: Final = frozenset(
    {
        "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
        "Kay64UG8yvCyLhqU000LxzYeUm0L_hLIl5S8kyKWbdc",
    }
)
_DENIED_RAW: Final = frozenset(
    decode_b64url(key, length=32) for key in DENIED_PUBLIC_KEYS_B64URL
)


def is_denied_public_key(text: str) -> bool:
    """True for a published test key, in unpadded base64url or standard base64."""
    if not isinstance(text, str):
        return False
    raw = b64url_decode(text)
    if raw is None:
        try:
            raw = base64.b64decode(text, validate=True)
        except ValueError:
            try:
                raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
            except ValueError:
                return False
    return raw in _DENIED_RAW


def parse_json_body(body: bytes) -> object:
    """Parse a CP body as Control does: UTF-8, no duplicate keys, no NaN/Infinity.
    A parser that keeps the last duplicate and one that keeps the first would read
    different documents from the same signed bytes."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in items:
            if key in seen:
                raise Refusal("response_malformed", "duplicate_key")
            seen[key] = value
        return seen

    def constant(_name: str) -> object:
        raise Refusal("response_malformed", "bad_json")

    try:
        return json.loads(
            body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
    except (UnicodeDecodeError, ValueError):
        raise Refusal("response_malformed", "bad_json") from None


def check_target_binding(target_ref: str, product_code: str, environment: str) -> str:
    """`<product_code>/<environment>/<host>`: the environment segment IS the
    OpenBao namespace. Returns the host alias."""
    parts = target_ref.split("/") if isinstance(target_ref, str) else []
    if len(parts) != 3 or not all(_SEGMENT.fullmatch(p) for p in parts):
        raise Refusal("materials_invalid", "contract_violation")
    if (parts[0], parts[1]) != (product_code, environment):
        raise Refusal("materials_target_mismatch", "target_ref_mismatch")
    return parts[2]


@dataclass(frozen=True)
class PopSigner:
    """The target PoP private key, held in memory only. Never printed, never stored."""

    credential_id: str
    _key: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, credential_id: str, raw: bytes) -> PopSigner:
        """`raw` is the credential file: unpadded base64url of the 32-byte seed."""
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise Refusal("config_invalid") from None
        seed = decode_b64url(text, length=32)
        if seed is None:
            raise Refusal("config_invalid")
        return cls(credential_id, Ed25519PrivateKey.from_private_bytes(seed))

    def sign(self, message: bytes) -> str:
        return encode_b64url(self._key.sign(message))

    @property
    def public_key_b64url(self) -> str:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        return encode_b64url(
            self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

    def check_admissible(self) -> None:
        """Refuse a PoP key whose seed is published (it would authenticate nobody)."""
        if is_denied_public_key(self.public_key_b64url):
            raise Refusal("config_invalid", "denied_public_key")

    def __repr__(self) -> str:
        return f"PopSigner(credential_id={self.credential_id!r})"


_FORBIDDEN_PATH_CHARS: Final = frozenset("%?#\\ \t\r\n\x00")
_ALL_ROUTES: Final = {
    **ROUTES,
    **PENDING_V2_ROUTES,
    "resolve": "POST",
    "consume": "POST",
    "committed/<dispatch_id>": "GET",
}
_DISPATCH_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
SIGNATURE_BYTES: Final = 64
HeaderInput = Mapping[str, str] | Sequence[tuple[str, str]]


def parse_target_path(path: str) -> tuple[str, str]:
    """`(target_ref, route)` of a well-formed target-API path, else refuse. Mirrors
    Control's `parse_target_path` (no encoding, no query, no empty segment)."""
    prefix = f"{BASE_PATH}/targets/"
    if (
        not isinstance(path, str)
        or not path.startswith(prefix)
        or _FORBIDDEN_PATH_CHARS & set(path)
        or path.endswith("/")
        or "//" in path
    ):
        raise Refusal("response_binding_invalid")
    rest = path[len(prefix) :].split("/")
    if len(rest) < 4 or not all(_SEGMENT.fullmatch(p) for p in rest[:3]):
        raise Refusal("response_binding_invalid")
    route = "/".join(rest[3:])
    if route.startswith("committed/") and route.count("/") == 1:
        if not _DISPATCH_ID.fullmatch(route.split("/", 1)[1]):
            raise Refusal("response_binding_invalid")
        return "/".join(rest[:3]), "committed/<dispatch_id>"
    if route not in _ALL_ROUTES or route == "committed/<dispatch_id>":
        raise Refusal("response_binding_invalid")
    return "/".join(rest[:3]), route


def single_header(headers: HeaderInput, name: str) -> str:
    """The ONE value of a header: absent, empty or repeated (any case) is refused.
    A framework lookup returns the first of a repeated header and a dict keeps the
    last; refusing repetition removes the difference."""
    items = headers.items() if isinstance(headers, Mapping) else headers
    values = [v for k, v in items if str(k).lower() == name.lower()]
    if not values or not isinstance(values[0], str) or not values[0]:
        raise Refusal("response_signature_invalid", "missing_header")
    if len(values) > 1:
        raise Refusal("response_signature_invalid", "duplicate_header")
    return values[0]


@dataclass(frozen=True)
class ResponseCheck:
    """What a client verifies: `signature` over `message` under the pinned key."""

    message: bytes
    signature: bytes


def check_response(
    *,
    method: str,
    path: str,
    request_nonce: str,
    status: int,
    body: bytes,
    headers: HeaderInput,
) -> ResponseCheck:
    """The CLIENT half, matching Control's `check_response`: everything about a
    response before its signature check. A well-formed path and request nonce, and
    exactly one canonical 64-byte `X-Dotmac-Response-Signature`. An UNSIGNED 413,
    429 or 5xx is a retryable fault (`cp_unreachable`); any other unsigned or
    malformed response is `response_signature_invalid`."""
    try:
        parse_target_path(path)
        check_nonce(request_nonce)
    except Refusal:
        raise Refusal("response_binding_invalid") from None
    unsigned = classify_unsigned_response(status)
    try:
        text = single_header(headers, H_RESPONSE_SIGNATURE)
    except Refusal:
        raise Refusal(unsigned) from None
    signature = b64url_decode(text) or b""
    if len(signature) != SIGNATURE_BYTES:
        raise Refusal("response_signature_invalid")
    return ResponseCheck(
        response_message(
            method=method,
            path=path,
            request_nonce=request_nonce,
            status=status,
            body=body,
        ),
        signature,
    )


def verify_signature(
    public_keys_b64url: tuple[str, ...],
    message: bytes,
    signature: bytes,
    *,
    allow_denied: bool = False,
) -> None:
    """Under ANY pinned CP response key; never a denied one. Failure is
    `response_signature_invalid`."""
    for encoded in public_keys_b64url:
        raw = decode_b64url(encoded, length=32)
        if raw is None or (not allow_denied and is_denied_public_key(encoded)):
            raise Refusal("config_invalid")
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(signature, message)
        except InvalidSignature:
            continue
        return
    raise Refusal("response_signature_invalid")


def verify_response(
    public_keys_b64url: tuple[str, ...],
    message: bytes,
    signature_b64: str | None,
    *,
    allow_denied: bool = False,
) -> None:
    """Verify a response signature given as text (conformance helper)."""
    signature = decode_b64url(signature_b64, length=SIGNATURE_BYTES)
    if signature is None:
        raise Refusal("response_signature_invalid")
    verify_signature(public_keys_b64url, message, signature, allow_denied=allow_denied)
