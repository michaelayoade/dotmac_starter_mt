"""Test signer for WS8 signed-licence verification (`dotmac_kernel.licensing`).

`FakeLicenceSigner` is the ONE place in the kernel a private signing key
exists: an **ephemeral, per-instance Ed25519 key generated in memory** for
tests — never persisted, never a real issuer key. Vendor-plane and product
tests use it to build valid (and deliberately broken) envelopes without any
key-custody machinery; production issuance lives in the vendor control plane.

Needs the `cryptography` package (the `licensing` or `testing` extra).
Deterministic apart from the keypair itself: default payload timestamps are
fixed constants, and nothing here reads the wall clock.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping

from dotmac_kernel.licensing import (
    ENVELOPE_SCHEMA,
    LICENCE_SCHEMA,
    REVOCATION_SCHEMA,
    KeyStatus,
    LicenceKey,
    LicenceKeyRing,
    VerificationUnavailableError,
)

_FIXED_ISSUED_AT = "2026-01-01T00:00:00+00:00"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeLicenceSigner:
    """Signs licence payloads with an ephemeral in-memory Ed25519 key."""

    def __init__(self, key_id: str = "fake-key-1") -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except ImportError as exc:  # pragma: no cover — needs an extra-less env
            raise VerificationUnavailableError(
                "FakeLicenceSigner needs the 'cryptography' package — install "
                "the dotmac-kernel[licensing] (or [testing]) extra"
            ) from exc
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        self.key_id = key_id
        self._private_key = Ed25519PrivateKey.generate()
        self.public_key_b64 = _b64url(
            self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

    # ── keyring side ────────────────────────────────────────────────────────

    def key(self, status: KeyStatus = KeyStatus.ACTIVE) -> LicenceKey:
        """This signer's PUBLIC key as a keyring entry, in any rotation state."""
        return LicenceKey(
            key_id=self.key_id, public_key_b64=self.public_key_b64, status=status
        )

    def keyring(self) -> LicenceKeyRing:
        """A one-key ring trusting this signer's active key."""
        return LicenceKeyRing([self.key()])

    # ── signing side ────────────────────────────────────────────────────────

    @staticmethod
    def licence_payload(**overrides: object) -> dict[str, object]:
        """A valid default `dotmac-licence/1` payload dict; keyword overrides
        replace fields verbatim (including with invalid values, for
        fail-closed tests)."""
        payload: dict[str, object] = {
            "schema": LICENCE_SCHEMA,
            "licence_id": "lic-test",
            "licence_version": 1,
            "issuer": "test-issuer",
            "product": "test-product",
            "edition": None,
            "subject": {"customer": "test-customer"},
            "capabilities": [{"code": "example.use", "limits": {}}],
            "issued_at": _FIXED_ISSUED_AT,
            "not_before": None,
            "expires_at": None,
            "grace_days": 0,
            "constraints": {},
        }
        payload.update(overrides)
        return payload

    def sign(self, payload: bytes | Mapping[str, object]) -> dict[str, object]:
        """Wrap `payload` in a signed `dotmac-licence-envelope/1`. A mapping is
        serialized once here — the resulting BYTES are what is signed."""
        if isinstance(payload, Mapping):
            payload = json.dumps(payload).encode()
        signature = self._private_key.sign(payload)
        return {
            "schema": ENVELOPE_SCHEMA,
            "payload_b64": _b64url(payload),
            "signatures": [
                {
                    "key_id": self.key_id,
                    "algorithm": "ed25519",
                    "signature_b64": _b64url(signature),
                }
            ],
        }

    def envelope(self, **overrides: object) -> dict[str, object]:
        """A signed envelope around `licence_payload(**overrides)`."""
        return self.sign(self.licence_payload(**overrides))

    def add_signature(self, envelope: Mapping[str, object]) -> dict[str, object]:
        """A copy of `envelope` with this signer's signature appended — the
        rotation-window double-signing shape."""
        payload_b64 = envelope["payload_b64"]
        assert isinstance(payload_b64, str)
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        signatures = envelope["signatures"]
        assert isinstance(signatures, list)
        signed = self.sign(payload)
        signed_signatures = signed["signatures"]
        assert isinstance(signed_signatures, list)
        return {
            "schema": envelope["schema"],
            "payload_b64": payload_b64,
            "signatures": [*signatures, *signed_signatures],
        }

    def sign_revocation_list(
        self,
        *,
        list_version: int,
        revoked_licence_ids: list[str],
        issued_at: str = _FIXED_ISSUED_AT,
    ) -> dict[str, object]:
        """A signed `dotmac-licence-revocation/1` envelope."""
        return self.sign(
            {
                "schema": REVOCATION_SCHEMA,
                "list_version": list_version,
                "issued_at": issued_at,
                "revoked_licence_ids": list(revoked_licence_ids),
            }
        )


class FakeDeploymentSigner:
    """Signs applied-state envelopes with an ephemeral in-memory Ed25519 key.

    The DEPLOYMENT-side counterpart to `FakeLicenceSigner`: it plays the
    receiver proving its identity, where that one plays the vendor proving
    issuance. Kept as a separate class rather than a flag on the other because
    the two sign different structures, in different directions, under different
    key custody — and a single class would invite a test to sign a licence with
    a deployment key, which is precisely the confusion ADR-0007's separate
    envelope exists to prevent.

    Production key custody belongs to the product receiver (a private key read
    once from a configured file materialised from OpenBao). Nothing here is
    ever a real deployment key.
    """

    def __init__(self, key_id: str = "fake-deployment-key-1") -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except ImportError as exc:  # pragma: no cover — needs an extra-less env
            raise VerificationUnavailableError(
                "FakeDeploymentSigner needs the 'cryptography' package — install "
                "the dotmac-kernel[licensing] (or [testing]) extra"
            ) from exc
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        self.key_id = key_id
        self._private_key = Ed25519PrivateKey.generate()
        self.public_key_b64 = _b64url(
            self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

    def sign(self, data: bytes) -> bytes:
        """Sign exactly the bytes given — `seal_applied_state` supplies the
        domain-separated signing input."""
        return self._private_key.sign(data)

    # ── deliberately-wrong helpers, for the fail-closed canaries ────────────

    def sign_raw(self, payload: bytes) -> bytes:
        """Sign a payload WITHOUT the domain separator.

        Exists so a test can prove the separator is load-bearing rather than
        decorative: this signature is valid Ed25519 over the payload and must
        still be refused.
        """
        return self._private_key.sign(payload)


__all__ = ["FakeDeploymentSigner", "FakeLicenceSigner"]
