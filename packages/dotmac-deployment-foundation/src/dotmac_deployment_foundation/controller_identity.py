"""``ControllerSshFingerprintV1`` — the controller key, DECODED rather than matched.

## The defect this replaces

Both planes of the lease contract carried the controller's SSH key fingerprint
as a bare string, and the release plane validated it against
``^sha256:[0-9a-f]{64}$``. That regex is the shape of a CONTENT DIGEST — the
prefix `Digest.of` emits — and it is not the shape of an OpenSSH fingerprint.
``ssh-keygen -lf`` emits ``SHA256:`` followed by 43 characters of unpadded
standard base64, which is the same 32 bytes written a different way and matches
that regex for no value at all.

So the field named "the credential that mutated the host" could never have held
a real one. The bootstrap plan in
`docs/inventories/deployment-exposure-rehearsal.md` § "Record the fingerprint
before use" says the value comes from ``ssh-keygen -lf``; the validator would
have refused every such value and accepted a 64-hex string that is not a key
fingerprint at all.

## Why decoding rather than a wider regex

The obvious repair is to broaden the pattern to accept base64 too. That is the
wrong direction: **a wider regex accepts more strings, and accepting more
strings is not the same as establishing what a string IS.**
``SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`` satisfies any
shape-matching pattern and is not a SHA-256 digest of anything in particular
only because nothing checked.

This type decodes instead. It requires the exact ``SHA256:`` algorithm label,
decodes the body as strict standard base64, and requires the result to be
exactly 32 bytes — which is what "a SHA-256 digest" means. The identity it then
compares on is those bytes, so equality is an answer about the KEY rather than
about how the key was spelled.

## Canonical, so one key has one spelling

43 unpadded base64 characters carry 258 bits for a 256-bit digest, so two bytes
of the last character are slack: distinct strings decode to identical digests.
A record stores the string form and the whole store is content-digested, so two
spellings of one key would produce two documents and two lease digests for one
fact. `parse` therefore re-encodes and refuses anything that is not the
canonical spelling, and `__str__` emits that spelling and no other.

## Scope: the V2 lease plane and the release plane, not the historical reader

`HostLease` (``HostLease.v2``) and `HostLeaseReleaseV1` both use this type, so
the two planes name the same thing the same way with the same type.

`HistoricalLeaseV1` deliberately does NOT. It reads ``HostLease.v1`` records
written by five shipped candidate wheels under a validator that only required
the field to be non-empty, so those records may legitimately hold a string this
type refuses. Parsing them strictly would make a shipped record unreadable, and
the point of that class is that V1 records stay LEGIBLE — legibility is not
authority, and a historical record does not acquire a well-formed fingerprint by
being read.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
from typing import Any, Final

from .errors import SpecError

__all__ = [
    "CONTROLLER_FINGERPRINT_MALFORMED",
    "ControllerSshFingerprintV1",
]

#: Refused: a value offered as a controller SSH fingerprint is not one.
#: Assert this; read the prose.
CONTROLLER_FINGERPRINT_MALFORMED: Final = "controller_identity.malformed"

#: The one algorithm label this type accepts. OpenSSH writes it upper-case, and
#: the lower-case ``sha256:`` spelling is deliberately NOT accepted: that prefix
#: is this package's CONTENT-DIGEST prefix, and accepting both would put two
#: different kinds of value behind one field again.
_ALGORITHM: Final = "SHA256:"

#: A SHA-256 digest, in bytes. Not a length to match — the length a decode has
#: to produce for the value to be one.
_DIGEST_BYTES: Final = 32

#: 32 bytes is 43 characters of unpadded base64.
_ENCODED_LENGTH: Final = 43


@dataclasses.dataclass(frozen=True, slots=True)
class ControllerSshFingerprintV1:
    """One SSH public key's SHA-256 fingerprint, held as the digest itself.

    Constructed from the DECODED digest, never from the text: a caller with a
    string calls `parse`, which is the only place the OpenSSH spelling is
    interpreted. Equality is over the 32 bytes, so two records naming one key
    compare equal and a record naming another key does not — which is the
    comparison the destroy gate turns on.
    """

    digest: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.digest, bytes):
            raise SpecError(
                f"a controller SSH fingerprint holds the DECODED digest, got "
                f"{type(self.digest).__name__}. A caller holding the OpenSSH "
                "text calls `ControllerSshFingerprintV1.parse`, which is the "
                "one place that spelling is interpreted",
                code=CONTROLLER_FINGERPRINT_MALFORMED,
            )
        if len(self.digest) != _DIGEST_BYTES:
            raise SpecError(
                f"a controller SSH fingerprint is a {_DIGEST_BYTES}-byte "
                f"SHA-256 digest, got {len(self.digest)} bytes",
                code=CONTROLLER_FINGERPRINT_MALFORMED,
            )

    @classmethod
    def parse(
        cls, value: Any, *, field: str, code: str = CONTROLLER_FINGERPRINT_MALFORMED
    ) -> ControllerSshFingerprintV1:
        """The canonical OpenSSH spelling, ESTABLISHED as a SHA-256 digest.

        ``code`` is overridable so a caller with its own stable refusal
        vocabulary — `HostLeaseReleaseV1` refuses with ``lease_release.malformed``
        — keeps naming its own refusal rather than leaking this module's.
        """
        text = str(value).strip()
        if not text:
            raise SpecError(
                f"{field} is required. It is the credential that mutated the "
                "host, and a release that cannot name it is bound to no work",
                code=code,
            )
        if not text.startswith(_ALGORITHM):
            raise SpecError(
                f"{field} {text!r} does not begin with {_ALGORITHM!r}. "
                "`ssh-keygen -lf` emits `SHA256:` followed by base64; a "
                "lower-case `sha256:` prefix is this package's CONTENT-DIGEST "
                "prefix and names a different kind of value, and `MD5:`, "
                "`SHA1:` or any other label is a different algorithm whose "
                "output is not a SHA-256 digest",
                code=code,
            )
        body = text[len(_ALGORITHM) :]
        if len(body) != _ENCODED_LENGTH:
            raise SpecError(
                f"{field} {text!r} carries {len(body)} character(s) after "
                f"{_ALGORITHM!r} and a {_DIGEST_BYTES}-byte digest is "
                f"{_ENCODED_LENGTH} characters of unpadded base64. A padded or "
                "truncated spelling is refused rather than repaired: the record "
                "is content-digested, so a second spelling of one key is a "
                "second document for one fact",
                code=code,
            )
        try:
            raw = base64.b64decode(body + "=", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SpecError(
                f"{field} {text!r} is not standard base64 ({exc}). The "
                "URL-safe alphabet is refused for the same reason padding is: "
                "one key, one spelling",
                code=code,
            ) from exc
        if len(raw) != _DIGEST_BYTES:  # pragma: no cover - 43 chars always give 32
            raise SpecError(
                f"{field} {text!r} decodes to {len(raw)} bytes, not "
                f"{_DIGEST_BYTES}",
                code=code,
            )
        canonical = base64.b64encode(raw).decode("ascii").rstrip("=")
        if canonical != body:
            raise SpecError(
                f"{field} {text!r} is not the canonical encoding of the digest "
                f"it decodes to ({_ALGORITHM}{canonical}). The last base64 "
                "character carries two bits of slack, so distinct strings "
                "decode to one digest — and the record that stores this field "
                "is digested by content",
                code=code,
            )
        return cls(digest=raw)

    def __str__(self) -> str:
        """The canonical OpenSSH spelling, and the only one this type emits."""
        return _ALGORITHM + base64.b64encode(self.digest).decode("ascii").rstrip("=")
