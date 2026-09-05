"""The `cv1` semantic encoding is a FROZEN byte contract, and it is not
`fingerprint_of`.

Two separate things are pinned here, because they fail in different ways.

1. **The bytes.**  Consumers persist `cv1` digests as replay keys and content
   identities.  A change to a frame tag, a length prefix, a scalar rendering or
   the domain prefix re-keys every stored fingerprint SILENTLY — every replay
   check starts missing, every content-identity comparison starts disagreeing,
   and no test that merely round-trips the encoder would notice.  So the
   expected bytes are written out as literals rather than re-derived from the
   implementation.  A test that computes its expectation with the code under
   test cannot detect a change to that code.

2. **The distinction from `dotmac_kernel.fingerprints.fingerprint_of`.**  Both
   produce a stable digest of a payload, which is exactly why someone will
   eventually try to collapse them.  The two properties that make that
   impossible are asserted here on BOTH functions, side by side: `default=str`
   collapses a `Decimal` into the string that renders identically, and it
   splits ONE amount into two digests when the same value arrives at a
   different scale.  Those are not defects to fix in place — fixing either
   would move every `fingerprint_of` value already persisted — they are why the
   semantic encoder is a separate facility.  If `fingerprint_of` ever changes,
   this file fails and the person changing it has to decide deliberately, which
   is the point.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest
from dotmac_kernel import SUPPORTED_MODULES, semantic_encoding
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money
from dotmac_kernel.semantic_encoding import (
    ABSENT,
    CANONICAL_ALGORITHM,
    CanonicalEncodingError,
    canonical_decimal,
    canonical_instant,
    digest_of,
    encode,
    encode_fields,
    encode_ordered,
    encode_unordered,
)

NGN = Currency("NGN", 2)
JPY = Currency("JPY", 0)
BHD = Currency("BHD", 3)


# ── the public surface ───────────────────────────────────────────────────────


def test_the_facility_is_a_supported_module_with_a_declared_surface() -> None:
    """Reachable AND declared — a module consumers may import by name."""

    assert "dotmac_kernel.semantic_encoding" in SUPPORTED_MODULES
    assert set(semantic_encoding.__all__) == {
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
    }
    for name in semantic_encoding.__all__:
        assert hasattr(semantic_encoding, name), name


def test_the_facility_is_stateless_and_reaches_nothing() -> None:
    """No models, no lineage, no session, no I/O — checked at the imports.

    A stateless facility that quietly imports the ORM or a network client still
    passes every behaviour test in this file, so the seam is what is checked.
    """

    source = Path(semantic_encoding.__file__).read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported == {
        "__future__",
        "hashlib",
        "collections.abc",
        "datetime",
        "decimal",
        "enum",
        "typing",
        "dotmac_kernel.money",
    }


# ── the frozen cv1 bytes ─────────────────────────────────────────────────────

#: Every expectation below is a LITERAL. Re-deriving one from the encoder would
#: make this table pass under any change to the encoder, which is the only
#: change it exists to catch.
FROZEN_ENCODINGS: list[tuple[str, object, bytes]] = [
    ("empty text", "", b"s0:"),
    ("text", "ab", b"s2:ab"),
    ("the text 'None'", "None", b"s4:None"),
    ("decimal-looking text", "1.0", b"s3:1.0"),
    # UTF-8 byte length, not character count: 'é' is two bytes.
    ("non-ascii text", "é", b"s2:\xc3\xa9"),
    ("zero", 0, b"i1:0"),
    ("negative int", -1, b"i2:-1"),
    # bool is checked BEFORE int, so True is 'b', never 'i1:1'.
    ("true", True, b"b1:1"),
    ("false", False, b"b1:0"),
    ("decimal one point zero", Decimal("1.0"), b"d1:1"),
    ("decimal trailing zeroes", Decimal("1.000"), b"d1:1"),
    ("decimal negative zero", Decimal("-0"), b"d1:0"),
    ("decimal exponent form", Decimal("1E+3"), b"d4:1000"),
    ("decimal fraction", Decimal("0.075"), b"d5:0.075"),
    (
        "instant",
        datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        b"t32:2026-09-01T10:30:00.000000+00:00",
    ),
    (
        "offset instant normalizes to UTC",
        datetime(2026, 9, 1, 11, 30, tzinfo=timezone(timedelta(hours=1))),
        b"t32:2026-09-01T10:30:00.000000+00:00",
    ),
    ("bytes", b"\x00\xff", b"x2:\x00\xff"),
    ("money, 2 minor units", Money.of("100.00", NGN), b"m18:s3:NGNi1:2i5:10000"),
    ("money, 0 minor units", Money.of("100", JPY), b"m16:s3:JPYi1:0i3:100"),
    ("money, 3 minor units", Money.of("100.000", BHD), b"m19:s3:BHDi1:3i6:100000"),
    ("money, negative", Money.of("-0.01", NGN), b"m15:s3:NGNi1:2i2:-1"),
]


@pytest.mark.parametrize(
    ("label", "value", "expected"),
    FROZEN_ENCODINGS,
    ids=[row[0] for row in FROZEN_ENCODINGS],
)
def test_the_cv1_bytes_are_frozen(label: str, value: object, expected: bytes) -> None:
    assert encode(value) == expected, label


def test_the_absence_sentinel_encodes_to_frozen_bytes() -> None:
    assert encode(ABSENT) == b"z0:"


def test_an_enum_encodes_its_value_not_its_member_name() -> None:
    class Initiator(Enum):
        BUYER = "buyer"

    assert encode(Initiator.BUYER) == b"e8:s5:buyer"


def test_the_container_frames_are_frozen() -> None:
    assert encode_fields((("x", "ab"), ("y", "c"))) == b"o17:s1:xs2:abs1:ys1:c"
    assert encode_fields(()) == b"o0:"
    assert encode_ordered(["a", 1]) == b"l8:s1:ai1:1"
    assert encode_unordered([encode("b"), encode("a")]) == b"u8:s1:as1:b"
    assert encode_unordered([]) == b"u0:"


def test_the_namespaced_digest_is_frozen() -> None:
    """The digest, not just the encoding: the `cv1:<domain>:` prefix is inside
    the hash, so a change to the prefix is invisible in the encoded bytes."""

    body = encode_fields((("a", "b"),))
    assert digest_of("orders.snapshot", body) == (
        "cv1:a3417eadbb94d505d19b981c761613b846774854a60734cc92c3b5ab26b384b6"
    )
    assert digest_of("orders.submission", body) == (
        "cv1:0b646c1295a09bf793d17b9a92cfef7c16e33bdb39043c1397c23b2e69e0f78f"
    )
    assert CANONICAL_ALGORITHM == "cv1"


# ── the properties the framing exists for ────────────────────────────────────


def test_the_encoding_is_injective_over_field_boundaries() -> None:
    """Length-prefixing, as the property rather than as its presence."""

    assert encode_fields((("x", "ab"), ("y", "c"))) != encode_fields(
        (("x", "a"), ("y", "bc"))
    )


def test_an_unordered_collection_is_order_independent() -> None:
    members = [encode("a"), encode("b"), encode("c")]
    assert encode_unordered(members) == encode_unordered(list(reversed(members)))


def test_an_ordered_sequence_is_order_dependent() -> None:
    assert encode_ordered(["a", "b"]) != encode_ordered(["b", "a"])


def test_every_field_is_load_bearing() -> None:
    base = (("a", "1"), ("b", "2"))
    assert encode_fields(base) != encode_fields((("a", "1"), ("b", "3")))
    assert encode_fields(base) != encode_fields((("a", "9"), ("b", "2")))
    assert encode_fields(base) != encode_fields((("z", "1"), ("b", "2")))


def test_absence_is_a_typed_sentinel_and_none_is_refused() -> None:
    assert encode(ABSENT) != encode("None")
    assert encode(ABSENT) != encode("")
    assert encode(ABSENT) != encode(0)
    with pytest.raises(CanonicalEncodingError):
        encode(None)


def test_money_of_equal_magnitude_differs_by_currency_scale() -> None:
    assert encode(Money.of("100", JPY)) != encode(Money.of("100.00", NGN))


@pytest.mark.parametrize(
    "value", [1.5, float("nan"), object(), [1], {"a": 1}, (1,)], ids=repr
)
def test_an_unencodable_value_is_refused_rather_than_stringified(value: object) -> None:
    with pytest.raises(CanonicalEncodingError):
        encode(value)


def test_a_float_never_becomes_a_canonical_decimal() -> None:
    with pytest.raises(CanonicalEncodingError):
        canonical_decimal(1.5)  # type: ignore[arg-type]
    with pytest.raises(CanonicalEncodingError):
        canonical_decimal(Decimal("NaN"))


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(CanonicalEncodingError):
        canonical_instant(datetime(2026, 1, 1))
    assert canonical_instant(datetime(2026, 1, 1, tzinfo=UTC)) == (
        "2026-01-01T00:00:00.000000+00:00"
    )


@pytest.mark.parametrize("domain", ["", " ", " x", "x "], ids=repr)
def test_a_digest_domain_must_be_present_and_clean(domain: str) -> None:
    with pytest.raises(CanonicalEncodingError):
        digest_of(domain, b"")


# ── why this cannot be `fingerprint_of` ──────────────────────────────────────


def test_fingerprint_of_cannot_separate_a_decimal_from_its_text_and_this_can() -> None:
    """`default=str` renders `Decimal("1.0")` and `"1.0"` identically."""

    assert fingerprint_of(Decimal("1.0")) == fingerprint_of("1.0")
    assert encode(Decimal("1.0")) != encode("1.0")


def test_fingerprint_of_makes_a_false_conflict_out_of_one_amount() -> None:
    """The other direction, and the more dangerous one.

    `Decimal("1.0")` and `Decimal("1.00")` are ONE amount. `default=str`
    renders them differently, so a retry that built its amount from a
    differently-scaled source reads as a CHANGED request under
    `fingerprint_of` — a manufactured conflict on an identical decision. The
    semantic encoder normalizes first, so the two agree.
    """

    assert fingerprint_of(Decimal("1.0")) != fingerprint_of(Decimal("1.00"))
    assert encode(Decimal("1.0")) == encode(Decimal("1.00"))


def test_fingerprint_of_collapses_a_typed_value_into_its_text() -> None:
    """`default=str` is not injective: a UUID and the text of that UUID are one
    value to it, and so is every other type it does not know."""

    reference = UUID(int=7)
    assert fingerprint_of(reference) == fingerprint_of(str(reference))
    # The encoder does not stringify an unknown type at all: it refuses, so a
    # caller has to choose the representation rather than inherit `str()`'s.
    with pytest.raises(CanonicalEncodingError):
        encode(reference)


def test_fingerprint_of_accepts_none_where_this_refuses_it() -> None:
    """A JSON dump digests `None` without objection, so a caller never has to
    say whether it meant "absent" or "present and null". The semantic encoder
    refuses `None` and makes the caller say `ABSENT`, which encodes to bytes
    nothing else produces."""

    assert fingerprint_of(None)
    with pytest.raises(CanonicalEncodingError):
        encode(None)
    assert encode(ABSENT) == b"z0:"


def test_the_two_facilities_are_separate_objects_with_separate_algorithms() -> None:
    """No aliasing, and no shared digest space: `cv1:` is namespaced, the JSON
    fingerprint is a bare hex digest."""

    assert digest_of("x", b"").startswith("cv1:")
    assert not fingerprint_of({}).startswith("cv1:")
    assert "fingerprint_of" not in semantic_encoding.__all__
