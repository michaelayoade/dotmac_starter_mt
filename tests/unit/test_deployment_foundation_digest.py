"""A typed digest, and the formatting trap it generalises.

`dotmac-deployment-control` stores a plan digest as bare hex; this facility
emits the `sha256:`-prefixed form; `approve_plan` compares them with a raw
`!=`. Unnormalized, one value in two spellings is unequal, and the failure
presents as an AUTHORIZATION REFUSAL rather than a formatting bug. That is the
alarm that gets suppressed, and suppressing it removes a real control.

Every refusal below is paired with an accepting control, so the suite cannot
pass by refusing everything.
"""

from __future__ import annotations

import hashlib

import pytest
from dotmac_deployment_foundation.digest import (
    Digest,
    require_same_digest,
)
from dotmac_deployment_foundation.errors import SpecError

HEX = "a" * 64
OTHER = "b" * 64


def test_both_spellings_are_one_value() -> None:
    """The whole point. If this fails, every comparison downstream is a coin
    flip on which side happened to normalize."""
    assert Digest.parse(HEX) == Digest.parse(f"sha256:{HEX}")
    assert hash(Digest.parse(HEX)) == hash(Digest.parse(f"sha256:{HEX}"))


def test_uppercase_is_accepted_on_input_and_lowercased_on_output() -> None:
    """Hex is case-insensitive as a number and case-sensitive as a string —
    exactly the mismatch that makes raw comparison unsafe."""
    assert Digest.parse(f"SHA256:{HEX.upper()}") == Digest.parse(HEX)
    assert str(Digest.parse(HEX.upper())) == f"sha256:{HEX}"


def test_the_canonical_form_is_always_prefixed_lowercase() -> None:
    digest = Digest.parse(HEX)
    assert str(digest) == f"sha256:{HEX}"
    assert digest.hex == HEX


def test_of_bytes_matches_hashlib() -> None:
    payload = b"lane 3"
    assert Digest.of(payload).hex == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "deadbeef",
        "a" * 63,
        "a" * 65,
        f"md5:{'a' * 32}",
        f"sha512:{'a' * 128}",
        f"sha256:{'z' * 64}",
        "sha256:",
    ],
)
def test_malformed_and_unknown_algorithms_are_refused(value: str) -> None:
    with pytest.raises(SpecError):
        Digest.parse(value)


def test_a_wrong_length_raw_value_is_refused_at_construction() -> None:
    """A truncated digest is the classic way a comparison passes on a prefix."""
    with pytest.raises(SpecError, match="32 bytes"):
        Digest(algorithm="sha256", raw=b"\x00" * 16)


def test_an_unknown_algorithm_is_refused_at_construction() -> None:
    with pytest.raises(SpecError, match="unknown digest algorithm"):
        Digest(algorithm="md5", raw=b"\x00" * 16)


# ── the three-term equality gate ────────────────────────────────────────────


def test_three_agreeing_terms_pass_in_mixed_spellings() -> None:
    """The accepting control for every refusal below — and the real-world
    shape, since Control's term arrives bare and this facility's arrives
    prefixed."""
    agreed = require_same_digest(
        {
            "canonical_descriptor": f"sha256:{HEX}",
            "authorized_plan": HEX,
            "controller_execution_report": f"SHA256:{HEX.upper()}",
        },
        what="gate item 9",
    )
    assert str(agreed) == f"sha256:{HEX}"


def test_a_one_bit_mismatch_is_refused_and_names_the_odd_term() -> None:
    """A message reporting two digests without saying whose sends the reader to
    the wrong system."""
    flipped = "a" * 63 + "b"
    with pytest.raises(SpecError) as excinfo:
        require_same_digest(
            {
                "canonical_descriptor": HEX,
                "authorized_plan": flipped,
                "controller_execution_report": HEX,
            },
            what="gate item 9",
        )
    message = str(excinfo.value)
    assert "authorized_plan" in message
    assert "do not agree" in message


def test_a_wrong_algorithm_term_is_refused() -> None:
    with pytest.raises(SpecError):
        require_same_digest(
            {"a": HEX, "b": f"md5:{'a' * 32}"}, what="gate item 9"
        )


def test_a_single_term_cannot_satisfy_an_equality_check() -> None:
    """Two matching terms cannot pass a three-term gate, and one term always
    passes — so a caller must not be able to weaken the check by passing
    fewer terms than the gate names."""
    with pytest.raises(SpecError, match="at least two named terms"):
        require_same_digest({"canonical_descriptor": HEX}, what="gate item 9")
    with pytest.raises(SpecError, match="at least two named terms"):
        require_same_digest({}, what="gate item 9")


def test_two_agreeing_terms_do_not_vouch_for_an_absent_third() -> None:
    """The middle term is the authorized plan. Without it the check degenerates
    into 'the report agrees with the descriptor it was generated from', which
    is true by construction and proves nothing."""
    two = require_same_digest(
        {"canonical_descriptor": HEX, "controller_execution_report": HEX},
        what="gate item 9",
    )
    three_disagree = {
        "canonical_descriptor": HEX,
        "authorized_plan": OTHER,
        "controller_execution_report": HEX,
    }
    assert str(two) == f"sha256:{HEX}"
    with pytest.raises(SpecError):
        require_same_digest(three_disagree, what="gate item 9")
