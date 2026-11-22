"""The server-side request check, as a TEST DOUBLE of Control's `check_request`.

Control owns `check_request`; Platform CP calls it. The agent never serves a
request, so this port lives with the tests: it composes the agent's own wire
primitives, and `test_deploy_agent_conformance.py` replays Control's
`request_check` vectors through it, so the CP double the agent is proved against
refuses exactly what the real server refuses.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from dotmac_deploy_agent import wire
from dotmac_deploy_agent.refusal import Refusal

_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DISPATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_FORBIDDEN = frozenset("%?#\\ \t\r\n\x00")
_HEADERS = (
    wire.H_CREDENTIAL,
    wire.H_TARGET,
    wire.H_ISSUED_AT,
    wire.H_NONCE,
    wire.H_SIGNATURE,
)
_VERBS = {
    **wire.ROUTES,
    "resolve": "POST",
    "consume": "POST",
    "committed/<dispatch_id>": "GET",
}


class RequestRefused(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Coordinates:
    method: str
    path: str
    target_ref: str
    route: str
    dispatch_id: str | None
    credential_id: str
    issued_at: str
    nonce: str
    signature_b64u: str
    signed_message: bytes
    nonce_ledger_key: str


def parse_target_path(path: str) -> tuple[str, str, str | None]:
    prefix = f"{wire.BASE_PATH}/targets/"
    if (
        not path.startswith(prefix)
        or _FORBIDDEN & set(path)
        or path.endswith("/")
        or "//" in path
    ):
        raise RequestRefused("bad_path")
    rest = path[len(prefix) :].split("/")
    if len(rest) < 4 or not all(_SEGMENT.fullmatch(p) for p in rest[:3]):
        raise RequestRefused("bad_path")
    target_ref, route = "/".join(rest[:3]), "/".join(rest[3:])
    if route.startswith("committed/") and route.count("/") == 1:
        dispatch_id = route.split("/", 1)[1]
        if not _DISPATCH_ID.fullmatch(dispatch_id):
            raise RequestRefused("bad_path")
        return target_ref, "committed/<dispatch_id>", dispatch_id
    if route not in _VERBS or route == "committed/<dispatch_id>":
        raise RequestRefused("bad_path")
    return target_ref, route, None


def check_request(
    *,
    method: str,
    raw_path: str,
    query_string: str,
    headers: Mapping[str, str],
    body: bytes,
    store_now: str,
    credential_target_ref: str,
) -> Coordinates:
    """Every coordinate check, before any signature (Control's order)."""
    target_ref, route, dispatch_id = parse_target_path(raw_path)
    if _VERBS.get(route) != method:
        raise RequestRefused("wrong_verb")
    if query_string:
        raise RequestRefused("query_string")
    lowered = {str(k).lower(): v for k, v in headers.items()}
    found: dict[str, str] = {}
    for name in _HEADERS:
        value = lowered.get(name.lower())
        if not isinstance(value, str) or not value:
            raise RequestRefused("missing_header")
        found[name] = value
    if found[wire.H_TARGET] != target_ref:
        raise RequestRefused("target_mismatch_header_path")
    if credential_target_ref != target_ref:
        raise RequestRefused("target_mismatch_credential")
    credential_id = found[wire.H_CREDENTIAL]
    if not _DISPATCH_ID.fullmatch(credential_id):
        raise RequestRefused("bad_credential_id")
    try:
        nonce = wire.check_nonce(found[wire.H_NONCE])
    except Refusal:
        raise RequestRefused("bad_nonce") from None
    try:
        wire.parse_stamp(found[wire.H_ISSUED_AT])
    except Refusal:
        raise RequestRefused("bad_timestamp") from None
    try:
        wire.check_skew(found[wire.H_ISSUED_AT], store_now)
    except Refusal:
        raise RequestRefused("skew") from None
    decoded = wire.decode_b64url(found[wire.H_SIGNATURE], length=64)
    if decoded is None:
        raise RequestRefused("bad_signature_encoding")
    return Coordinates(
        method=method,
        path=raw_path,
        target_ref=target_ref,
        route=route,
        dispatch_id=dispatch_id,
        credential_id=credential_id,
        issued_at=found[wire.H_ISSUED_AT],
        nonce=nonce,
        signature_b64u=found[wire.H_SIGNATURE],
        signed_message=wire.request_message(
            method=method,
            path=raw_path,
            body=body,
            credential_id=credential_id,
            target_ref=target_ref,
            issued_at=found[wire.H_ISSUED_AT],
            nonce=nonce,
        ),
        nonce_ledger_key=f"{credential_id}/{nonce}",
    )
