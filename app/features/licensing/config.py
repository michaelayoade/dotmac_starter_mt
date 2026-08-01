"""Receiver trust configuration — deployment-level env knobs, documented in
`.env.example` (the "everything by config" rule).

The keyring is PUBLIC verification material only (the vendor's licence signing
keys); it is deployment trust config, not tenant settings-as-data, so it lives
in env — same tier as `DATABASE_URL`. Fail-loud: malformed configuration
raises at load, never silently verifies nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotmac_kernel.licensing import KeyStatus, LicenceKey, LicenceKeyRing

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    """Resolved verification inputs for this deployment."""

    keyring: LicenceKeyRing
    deployment_id: str | None
    require_binding: bool


def load_receiver_config() -> ReceiverConfig:
    """Build the receiver config from env.

    - `LICENCE_VERIFICATION_KEYS` — JSON list of
      `{"key_id": ..., "public_key_b64": ..., "status": "active|retired|revoked"}`
      (default `[]` — verification then fails closed with `UnknownKeyError`).
    - `LICENCE_DEPLOYMENT_ID` — this deployment's identity for bound licences
      (default unset — a bound licence is then rejected, fail closed).
    - `LICENCE_REQUIRE_BINDING` — refuse unbound licences (default false).
    """
    raw = os.getenv("LICENCE_VERIFICATION_KEYS", "[]")
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise ValueError("LICENCE_VERIFICATION_KEYS is not valid JSON") from exc
    if not isinstance(entries, list):
        raise ValueError("LICENCE_VERIFICATION_KEYS must be a JSON list")
    keys = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("LICENCE_VERIFICATION_KEYS entries must be objects")
        try:
            keys.append(
                LicenceKey(
                    key_id=str(entry["key_id"]),
                    public_key_b64=str(entry["public_key_b64"]),
                    status=KeyStatus(entry.get("status", "active")),
                )
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid LICENCE_VERIFICATION_KEYS entry: {exc}") from exc
    return ReceiverConfig(
        keyring=LicenceKeyRing(keys),
        deployment_id=os.getenv("LICENCE_DEPLOYMENT_ID") or None,
        require_binding=os.getenv("LICENCE_REQUIRE_BINDING", "false").lower()
        in _TRUTHY,
    )
