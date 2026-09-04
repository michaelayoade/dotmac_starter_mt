"""The `cv1` semantic encoding — canonical bytes a fingerprint can be taken over.

A semantic fingerprint answers "is this the SAME decision?", and it can only
answer that if the bytes it digests are injective over the decision's shape.
That is a stronger requirement than "stable", and it is the whole reason this
module exists beside `dotmac_kernel.fingerprints.fingerprint_of` rather than
replacing it:

`fingerprint_of` is a sorted-keys JSON dump with `default=str`. It is a
STABILITY contract — the same payload digests the same way across processes —
and it is used widely enough that its bytes are themselves a compatibility
surface. But it does not length-prefix, so the field pair `("ab", "c")` and
`("a", "bc")` collapse to identical bytes; and `default=str` renders
`Decimal("1.0")` and the string `"1.0"` identically, so a typed amount and a
string that looks like one cannot be told apart. Neither is a defect in
`fingerprint_of`; they are the cost of being a JSON dump. They ARE defects in a
conflict detector, which is a different job.

So this module encodes rather than dumps. Every value goes into a
length-prefixed, self-describing frame — `<tag><byte-length>:<body>` — and:

* **Framing makes it injective.** No concatenation of field values can be
  reassociated into a different field split with the same bytes.
* **Absence is a typed sentinel.** `ABSENT` encodes as `z0:` and nothing else
  does. `None` is REFUSED, so a missing field and a null-valued field can never
  collide, and neither can collide with `""`, `0` or the text `"None"`.
* **Money is exact at currency scale.** Integer minor units plus the currency
  code and its exponent — never a rendered float, and never a decimal string
  whose trailing zeroes depend on how it was built.
* **Unordered collections canonicalize.** `encode_unordered` sorts already-
  encoded member frames by their own bytes, so reordering the input cannot move
  the digest. A retry whose members arrive in another order is a replay, not a
  manufactured conflict.
* **The algorithm is namespaced.** `digest_of` emits `cv1:<sha256>` over
  `cv1:<domain>:` + body, so two domains never share a digest and a future
  `cv2` is a cutover rather than a silent rehash.

**The `cv1` bytes are frozen.** Consumers persist these digests as replay keys
and content identities; changing a frame tag, a length, a scalar rendering or
the domain prefix would silently re-key every stored fingerprint. A change to
the encoding is a NEW algorithm name, never an edit to this one.

**Product-first extraction, not a design.** The `cv1` encoder was written in the
Orders incubation slice, whose `_fingerprint` had previously delegated to
`fingerprint_of` and could not distinguish the cases above. The Refund Warrants
slice then needed exactly the same encoding and — unable to import across local
incubation branches — carried a copy whose bodies were byte-identical once
docstrings were stripped. Two copies of one byte-exact contract, each free to
drift with no digest ever disagreeing, is the shape that says the facility has
no home; this module is that home. Orders is the qualifying source and the code
below is its implementation ported unchanged, not a rewrite.

Stateless by construction: no models, no lineage, no session, no I/O. It imports
`dotmac_kernel.money` (pure value objects) and the standard library, nothing
else.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Final

from dotmac_kernel.money import Money

CANONICAL_ALGORITHM: Final = "cv1"

#: Typed absence. A field that is not present encodes as this and nothing else;
#: it is distinguishable from an empty string, from zero, and from the literal
#: text ``"None"``, each of which a ``str()``-based encoder collapses together.
ABSENT: Final = object()


class CanonicalEncodingError(ValueError):
    """A value cannot be canonically encoded, so it cannot be fingerprinted."""


def canonical_decimal(value: Decimal) -> str:
    """Language-neutral, non-exponent decimal text with no insignificant zeroes."""
    if isinstance(value, bool) or isinstance(value, float):
        raise CanonicalEncodingError("A canonical decimal is never built from float.")
    if not isinstance(value, Decimal) or not value.is_finite():
        raise CanonicalEncodingError("A canonical decimal must be a finite Decimal.")
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def canonical_instant(value: datetime) -> str:
    """UTC-normalized microsecond ISO text. A naive instant is refused."""
    if not isinstance(value, datetime):
        raise CanonicalEncodingError("A canonical instant must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalEncodingError("A canonical instant must be timezone-aware.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _frame(tag: str, body: bytes) -> bytes:
    """Length-prefixed frame: ``<tag><byte-length>:<body>``.

    Framing is what makes the encoding injective. Without it the field pair
    ``("ab", "c")`` and ``("a", "bc")`` produce identical bytes, and a
    fingerprint that cannot tell those apart cannot be a conflict detector.
    """
    return f"{tag}{len(body)}:".encode("ascii") + body


def _text(value: str) -> bytes:
    return _frame("s", value.encode("utf-8"))


def encode(value: object) -> bytes:
    """Encode one value into canonical, length-prefixed, self-describing bytes."""
    if value is ABSENT:
        return b"z0:"
    if value is None:
        raise CanonicalEncodingError(
            "None is not encodable; absence is expressed with the ABSENT sentinel "
            "so that a missing value and a null-valued field cannot collide."
        )
    if isinstance(value, Money):
        # Exact at currency scale: integer minor units, never a rendered float.
        minor = int(value.amount.scaleb(value.currency.minor_units).to_integral_value())
        body = (
            _text(value.currency.code)
            + _frame("i", str(value.currency.minor_units).encode("ascii"))
            + _frame("i", str(minor).encode("ascii"))
        )
        return _frame("m", body)
    if isinstance(value, Enum):
        return _frame("e", _text(str(value.value)))
    if isinstance(value, bool):
        return _frame("b", b"1" if value else b"0")
    if isinstance(value, int):
        return _frame("i", str(value).encode("ascii"))
    if isinstance(value, Decimal):
        return _frame("d", canonical_decimal(value).encode("ascii"))
    if isinstance(value, datetime):
        return _frame("t", canonical_instant(value).encode("ascii"))
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, bytes):
        return _frame("x", value)
    raise CanonicalEncodingError(f"No canonical encoding for {type(value).__name__}.")


def encode_fields(fields: Sequence[tuple[str, object]]) -> bytes:
    """Encode an ORDERED field list. Order is part of the declared shape."""
    body = b"".join(_text(name) + encode(value) for name, value in fields)
    return _frame("o", body)


def encode_ordered(items: Iterable[object]) -> bytes:
    """Encode a sequence whose order is meaningful."""
    parts = [encode(item) if not isinstance(item, bytes) else item for item in items]
    return _frame("l", b"".join(parts))


def encode_unordered(items: Iterable[bytes]) -> bytes:
    """Encode a collection whose order is NOT meaningful.

    The members are already-encoded frames; they are sorted by their own bytes,
    so reordering the input cannot move the digest. The same members listed in
    another order are the same collection, and a retry whose members arrive
    reordered is a replay rather than a manufactured key-reuse conflict.
    """
    return _frame("u", b"".join(sorted(items)))


def _digest(domain: str, body: bytes) -> str:
    prefix = f"{CANONICAL_ALGORITHM}:{domain}:".encode("ascii")
    return f"{CANONICAL_ALGORITHM}:{hashlib.sha256(prefix + body).hexdigest()}"


def digest_of(domain: str, body: bytes) -> str:
    """Namespaced digest over already-canonical bytes."""
    if not domain or domain != domain.strip():
        raise CanonicalEncodingError("A fingerprint domain must be non-empty.")
    return _digest(domain, body)


__all__ = [
    "ABSENT",
    "CANONICAL_ALGORITHM",
    "CanonicalEncodingError",
    "canonical_decimal",
    "canonical_instant",
    "digest_of",
    "encode",
    "encode_fields",
    "encode_ordered",
    "encode_unordered",
]
