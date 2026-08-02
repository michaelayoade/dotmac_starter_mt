"""Canary tests for the WS8 receiver-applied-state contract.

Written before the implementation, per the kernel slice convention. This is the
value object both planes speak when a deployment reports what it is ACTUALLY
running — the missing channel behind three open gaps: acknowledgements the
vendor cannot authenticate, keyring-uptake lag, and revocation-application lag.

The contract's whole job is to carry claims that the VENDOR will verify, so
validation is strict and fail-closed — and it lives in ONE place
(`ReceiverAppliedState.__post_init__`): a report built directly carries exactly
the guarantees of one parsed off the wire, so a producer can never
construct-and-serialise a report the other plane would reject.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_kernel.licensing import (
    APPLIED_STATE_SCHEMA,
    UNKNOWN_DIGEST,
    MalformedAppliedStateError,
    ReceiverAppliedState,
    applied_state_payload,
    parse_applied_state,
)

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
# A REAL digest shape: `payload_digest` produces sha256: + 64 hex chars. Using a
# placeholder here would have let the fixture assert applied state under a
# digest the receiver could never have computed.
DIGEST = "sha256:" + "ab" * 32


def _state(**over) -> ReceiverAppliedState:
    fields: dict[str, object] = {
        "report_id": "rep-1",
        "deployment_ref": "edge-site-1",
        "licence_id": "lic-1",
        "licence_version": 3,
        "digest": DIGEST,
        "keyring_generation": 2,
        "revocation_list_version": 5,
        "observed_at": NOW,
        "status": "applied",
    }
    fields.update(over)
    return ReceiverAppliedState(**fields)  # type: ignore[arg-type]


# Field-level invalids: every one must fail identically whether it arrives on
# the wire (parse) or is built directly (construction) — the parity the single
# validator exists to guarantee.
FIELD_BREAKAGES: list[dict[str, object]] = [
    {"report_id": ""},
    {"report_id": None},
    {"deployment_ref": ""},
    {"licence_id": ""},
    {"licence_version": 0},  # an 'applied' claim needs a real version
    {"licence_version": -1},
    {"licence_version": "3"},
    {"licence_version": True},
    {"digest": ""},
    {"digest": UNKNOWN_DIGEST},  # an 'applied' claim needs the real digest
    {"digest": "sha256:x"},  # ...and a REAL one, not merely a populated string
    {"digest": "sha256:" + "zz" * 32},  # right length, not hex
    {"digest": "ab" * 32},  # right hex, missing the algorithm prefix
    {"keyring_generation": None},  # nothing is applied without a keyring
    {"reason": "BadSignatureError"},  # an 'applied' report explains nothing
    {"keyring_generation": 0},
    {"keyring_generation": "2"},
    {"revocation_list_version": -1},
    {"revocation_list_version": "5"},
    {"observed_at": "not-a-timestamp"},
    {"observed_at": None},
    {"status": "maybe"},
    {"status": ""},
    {"status": "rejected"},  # a rejection without a 'reason' is unexplainable
    {"status": "rejected", "reason": ""},
]

# Rejection-shaped invalids: a rejection is the ONLY status these can occur
# under, so they are checked separately from the `applied` fixture above.
REJECTION_BREAKAGES: list[dict[str, object]] = [
    # No verified identity means no verified VERSION either.
    {
        "status": "rejected",
        "reason": "MalformedLicenceError",
        "digest": UNKNOWN_DIGEST,
        "licence_version": 4,
    },
    # A rejection may carry a real digest, but not a malformed one.
    {"status": "rejected", "reason": "StaleLicenceError", "digest": "sha256:x"},
]

# Wire-only invalids: things that can only go wrong on a serialised payload.
WIRE_BREAKAGES: list[dict[str, object]] = [
    *FIELD_BREAKAGES,
    *REJECTION_BREAKAGES,
    {"schema": "dotmac-licence-applied-state/999"},
    {"schema": None},
]


# ── Round-trip ──────────────────────────────────────────────────────────────


def test_round_trip_preserves_every_field() -> None:
    state = _state()
    parsed = parse_applied_state(applied_state_payload(state))
    assert parsed == state


def test_payload_carries_the_schema() -> None:
    assert applied_state_payload(_state())["schema"] == APPLIED_STATE_SCHEMA


def test_payload_is_json_safe() -> None:
    """It crosses a process boundary, so every value must serialise without a
    custom encoder — a datetime left in place would fail at the transport."""
    import json

    json.dumps(applied_state_payload(_state()))


# ── What the report is FOR ──────────────────────────────────────────────────


def test_carries_the_uptake_versions_that_close_the_unmeasurable_alerts() -> None:
    """`keyring_generation` and `revocation_list_version` are the two signals
    the vendor cannot infer: "we published it" says nothing about what a
    deployment holds."""
    state = _state(keyring_generation=7, revocation_list_version=11)
    payload = applied_state_payload(state)
    assert payload["keyring_generation"] == 7
    assert payload["revocation_list_version"] == 11


def test_a_deployment_that_has_imported_no_revocation_list_reports_null() -> None:
    """Distinct from version 0: "never imported" and "imported an empty list"
    are different states, and conflating them would hide a deployment that has
    never received one."""
    state = _state(revocation_list_version=None)
    assert applied_state_payload(state)["revocation_list_version"] is None
    assert parse_applied_state(applied_state_payload(state)) == state


def test_same_report_id_and_content_is_the_idempotent_replay() -> None:
    """Delivery is at-least-once, so the same report will arrive twice; the
    vendor dedupes on `report_id`, and an identical redelivery is equal in
    every field."""
    assert _state(report_id="rep-1") == _state(report_id="rep-1")


def test_identical_content_under_different_report_ids_is_two_reports() -> None:
    """Dedupe keys on `report_id`, never on content: two scheduled
    observations of the same unchanged state are DISTINCT reports, and
    conflating them would make a deployment look silent."""
    first, second = _state(report_id="rep-1"), _state(report_id="rep-2")
    assert first != second
    assert applied_state_payload(first) != applied_state_payload(second)


def test_a_rejected_report_carries_its_reason() -> None:
    state = _state(status="rejected", reason="StaleLicenceError")
    assert state.reason == "StaleLicenceError"
    assert parse_applied_state(applied_state_payload(state)).status == "rejected"


def test_a_rejected_report_for_an_envelope_that_never_validated_round_trips() -> None:
    """The reference receiver's rejected ack for a malformed envelope carries
    licence_version 0 and the `UNKNOWN_DIGEST` sentinel — there is no verified
    identity to report. The contract must represent exactly that, or the
    honest rejection would be unreportable."""
    state = _state(
        status="rejected",
        reason="MalformedLicenceError",
        licence_id="unknown",
        licence_version=0,
        digest=UNKNOWN_DIGEST,
    )
    parsed = parse_applied_state(applied_state_payload(state))
    assert parsed == state
    assert (parsed.licence_version, parsed.digest) == (0, UNKNOWN_DIGEST)


def test_an_applied_report_cannot_claim_a_rejected_attempts_identity() -> None:
    """`status="applied"` asserts a committed (version, digest); version 0 or
    the unknown-digest sentinel would label a non-event as applied state."""
    with pytest.raises(MalformedAppliedStateError):
        _state(licence_version=0)
    with pytest.raises(MalformedAppliedStateError):
        _state(digest=UNKNOWN_DIGEST)


def test_the_acknowledgement_projection_is_valid_for_both_statuses() -> None:
    """`.acknowledgement` must construct a valid `LicenceAcknowledgement` for
    applied AND rejected reports — including the no-verified-identity
    rejection — so the existing ack path keeps working unchanged."""
    applied_ack = _state().acknowledgement
    assert (applied_ack.status, applied_ack.digest) == ("applied", DIGEST)
    assert applied_ack.deployment_id == "edge-site-1"

    rejected_ack = _state(
        status="rejected",
        reason="BadSignatureError",
        licence_version=0,
        digest=UNKNOWN_DIGEST,
    ).acknowledgement
    assert (rejected_ack.status, rejected_ack.reason) == (
        "rejected",
        "BadSignatureError",
    )
    assert (rejected_ack.licence_version, rejected_ack.digest) == (0, UNKNOWN_DIGEST)


# ── Strict, fail-closed validation — identical on both paths ────────────────


@pytest.mark.parametrize("breakage", WIRE_BREAKAGES)
def test_malformed_reports_fail_closed_on_parse(breakage: dict[str, object]) -> None:
    payload = applied_state_payload(_state())
    payload.update(breakage)
    with pytest.raises(MalformedAppliedStateError):
        parse_applied_state(payload)


@pytest.mark.parametrize("breakage", [*FIELD_BREAKAGES, *REJECTION_BREAKAGES])
def test_invalid_direct_construction_raises_exactly_like_parse(
    breakage: dict[str, object],
) -> None:
    """Construction parity: a producer building the value object directly hits
    the SAME error the receiving plane's parser would raise — there is no
    constructable-but-unparseable report."""
    with pytest.raises(MalformedAppliedStateError):
        _state(**breakage)


def test_a_naive_timestamp_is_rejected() -> None:
    """Cross-plane timestamps must be unambiguous; a naive one silently means
    whatever the reader's timezone happens to be."""
    payload = applied_state_payload(_state())
    payload["observed_at"] = "2026-08-02T12:00:00"
    with pytest.raises(MalformedAppliedStateError):
        parse_applied_state(payload)
    with pytest.raises(MalformedAppliedStateError):
        _state(observed_at=datetime(2026, 8, 2, 12, 0, 0))


def test_a_non_object_payload_is_rejected() -> None:
    for payload in ([], "applied", 7, None):
        with pytest.raises(MalformedAppliedStateError):
            parse_applied_state(payload)  # type: ignore[arg-type]


def test_unknown_fields_are_ignored_not_trusted() -> None:
    """Forward compatibility: a newer receiver may add fields. They must not
    break an older vendor, and must not silently become part of the record."""
    payload = applied_state_payload(_state())
    payload["future_field"] = "something"
    parsed = parse_applied_state(payload)
    assert not hasattr(parsed, "future_field")
    assert parsed == _state()


# ── Contradictions the gates closed ─────────────────────────────────────────


def test_an_applied_report_cannot_also_carry_a_rejection_reason() -> None:
    """`reason` explains a REJECTION. Carrying one on an accepted report would
    let a single report be simultaneously applied and explained-away, and two
    readers could reasonably believe opposite halves."""
    with pytest.raises(MalformedAppliedStateError, match="must not carry"):
        _state(reason="BadSignatureError")


def test_an_applied_report_needs_a_real_digest_not_a_populated_string() -> None:
    """A shape check, not an emptiness check: `sha256:x` is populated and
    meaningless, and accepting it would record applied state for a digest the
    receiver could never have produced."""
    with pytest.raises(MalformedAppliedStateError, match="real digest"):
        _state(digest="sha256:x")


def test_a_deployment_with_no_keyring_can_only_report_rejections() -> None:
    """`keyring_generation=None` means nothing is provisioned — a real state an
    operator must be able to see, and one in which verification cannot succeed.
    So it is reportable on a rejection and refused on an applied claim."""
    rejected = _state(
        status="rejected",
        reason="UnknownKeyError",
        keyring_generation=None,
        licence_version=0,
        digest=UNKNOWN_DIGEST,
    )
    assert rejected.keyring_generation is None
    assert parse_applied_state(applied_state_payload(rejected)) == rejected

    with pytest.raises(MalformedAppliedStateError, match="keyring generation"):
        _state(keyring_generation=None)


def test_an_unverified_rejection_cannot_claim_a_version() -> None:
    with pytest.raises(MalformedAppliedStateError, match="licence_version 0"):
        _state(
            status="rejected",
            reason="BadSignatureError",
            digest=UNKNOWN_DIGEST,
            licence_version=9,
        )
