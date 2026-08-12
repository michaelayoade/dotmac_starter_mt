"""Artifact identity: a digest, and a reference that cannot drift from it.

This module is the whole point of the distribution. Everything else here is
storage for what it decides.

## An artifact is its content, not its name

`docs/design/domain-foundation.md` states the rule the vendor control plane's
`ArtifactSelectionService` must never break — the exact artifact a deployment
runs "must not become a mutable tag such as `:latest`". A tag is a *pointer the
publisher can move after you approved it*. Approving `app:1.4.2` and deploying
`app:1.4.2` are the same words about two different sets of bytes if someone
re-pushed the tag in between, and nothing in the audit trail records that they
diverged.

So identity here is a **content digest**, and a reference is only admissible if
the digest is already inside it. That is not a stylistic preference about how to
write image references; it is the property that makes a plan hash mean anything.

## Why parsing is refusal, not repair

`Digest.parse` and `pinned_reference` raise rather than normalising. A caller
that hands over `app:latest` has a bug in the layer above — it resolved a tag
somewhere it should have resolved a digest — and silently "fixing" it by
resolving the tag *here* would put a network call and a moment-in-time decision
inside a value object, which is exactly how a mutable tag ends up laundered into
an immutable-looking record.

## Scope of the algorithm allowlist

Only `sha256` today, because that is what every registry, SBOM format and
signature envelope in use emits. It is an allowlist rather than "any
`<alg>:<hex>`" because an unrecognised algorithm is not a harmless unknown: a
weaker one that still parses would be accepted as an identity and compared for
equality with the same confidence as a strong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: The one accepted digest algorithm, and its exact hex width.
SHA256: Final[str] = "sha256"
_DIGEST_WIDTHS: Final[dict[str, int]] = {SHA256: 64}

_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^([a-z0-9]+):([0-9a-f]+)$")

#: A digest-pinned reference: anything, then `@<alg>:<hex>` at the very end.
#: The `@` is what distinguishes a pin from a tag in every registry syntax that
#: supports both, and requiring it at the END is what stops
#: `repo@sha256:...:latest` — a pin with a tag appended — from passing.
_PINNED_REF_RE: Final[re.Pattern[str]] = re.compile(r"^(\S+)@([a-z0-9]+:[0-9a-f]+)$")


class ArtifactIdentityError(ValueError):
    """Base: this value cannot serve as an artifact identity.

    Both concrete errors below are raised from `pinned_reference`, because a
    reference carries a digest inside it and either half can be wrong. A caller
    that only wants to know "is this usable?" catches this; a caller that wants
    to report precisely *why* catches the subclasses. Without a shared base,
    every call site would need two excepts or would fall back to bare
    `ValueError` and catch unrelated failures with it.
    """


class DigestError(ArtifactIdentityError):
    """A digest is malformed, or uses an algorithm this catalogue will not accept."""


class UnpinnedReferenceError(ArtifactIdentityError):
    """A reference names an artifact by something a publisher can move.

    Raised for a bare tag (`app:1.4.2`), a floating tag (`app:latest`), and a
    reference with no digest at all. The message names the value so the layer
    that produced it is findable, because this failure is always a bug upstream
    rather than bad user input.
    """


@dataclass(frozen=True, slots=True)
class Digest:
    """A content address. Frozen, compared by value, and valid by construction.

    There is deliberately no way to build one that has not been through
    `parse`: the class is the proof that a string was checked, so accepting a
    `Digest` in a signature is accepting a checked value.
    """

    algorithm: str
    hex_digest: str

    def __post_init__(self) -> None:
        width = _DIGEST_WIDTHS.get(self.algorithm)
        if width is None:
            raise DigestError(
                f"unsupported digest algorithm {self.algorithm!r}; "
                f"accepted: {', '.join(sorted(_DIGEST_WIDTHS))}"
            )
        if len(self.hex_digest) != width:
            raise DigestError(
                f"{self.algorithm} digest must be {width} hex characters, "
                f"got {len(self.hex_digest)}"
            )
        if _DIGEST_RE.fullmatch(f"{self.algorithm}:{self.hex_digest}") is None:
            raise DigestError(
                "digest must be lowercase hexadecimal; "
                "uppercase and non-hex characters are refused rather than "
                "normalised, so two spellings of one digest cannot both be stored"
            )

    @classmethod
    def parse(cls, value: str) -> Digest:
        """Parse `<algorithm>:<hex>`, or raise `DigestError`."""
        match = _DIGEST_RE.fullmatch(value.strip())
        if match is None:
            raise DigestError(
                f"{value!r} is not a digest; expected '<algorithm>:<hex>', "
                f"e.g. 'sha256:{'0' * 64}'"
            )
        return cls(algorithm=match.group(1), hex_digest=match.group(2))

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.hex_digest}"


def pinned_reference(reference: str, *, expected: Digest | None = None) -> str:
    """Return `reference` unchanged, having proved it is digest-pinned.

    Raises `UnpinnedReferenceError` when the reference is not digest-pinned at
    all, and `DigestError` when it is pinned by something that is not a digest
    this catalogue accepts. Both share `ArtifactIdentityError`; the two are kept
    distinct because "you gave me a tag" and "your sha256 is 63 characters" send
    the reader to different places.

    `expected`, when given, additionally proves the reference pins the digest
    the caller believes it does. That check exists because the two values are
    stored in adjacent columns, and adjacent columns drift: a row whose
    `artifact_ref` pins a *different* artifact than its `digest` column would be
    a pin that passes every syntactic check and deploys the wrong bytes.
    """
    match = _PINNED_REF_RE.fullmatch(reference.strip())
    if match is None:
        raise UnpinnedReferenceError(
            f"{reference!r} is not digest-pinned. A reference must end in "
            "'@<algorithm>:<hex>' — a tag is a pointer the publisher can move "
            "after the plan naming it was approved."
        )
    digest = Digest.parse(match.group(2))
    if expected is not None and digest != expected:
        raise UnpinnedReferenceError(
            f"{reference!r} pins {digest}, but the artifact's digest is "
            f"{expected}. The reference and the digest must address the same bytes."
        )
    return reference.strip()


__all__ = [
    "SHA256",
    "ArtifactIdentityError",
    "Digest",
    "DigestError",
    "UnpinnedReferenceError",
    "pinned_reference",
]
