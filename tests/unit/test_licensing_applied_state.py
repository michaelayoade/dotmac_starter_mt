"""Canary tests for the WS8 receiver-applied-state contract.

Written before the implementation, per the kernel slice convention. This is the
value object both planes speak when a deployment reports what it has ACTUALLY
applied — the missing channel behind three open gaps: acknowledgements that
cannot prove identity, keyring-uptake lag, and revocation-application lag.

The contract's whole job is to carry claims that the VENDOR will verify, so the
parsing is strict and fail-closed: a report that cannot be trusted must be
rejected here rather than half-interpreted downstream.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_kernel.licensing import (
    APPLIED_STATE_SCHEMA,
    MalformedAppliedStateError,
    ReceiverAppliedState,
    applied_state_payload,
    parse_applied_state,
)

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)


def _state(**over) -> ReceiverAppliedState:
    fields: dict[str, object] = {
        "report_id": "rep-1",
        "deployment_ref": "edge-site-1",
        "licence_id": "lic-1",
        "licence_version": 3,
        "digest": "sha256:abcd",
        "keyring_generation": 2,
        "revocation_list_version": 5,
        "applied_at": NOW,
        "status": "applied",
    }
    fields.update(over)
    return ReceiverAppliedState(**fields)  # type: ignore[arg-type]


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


def test_report_id_is_the_idempotency_key() -> None:
    """Delivery is at-least-once, so the same report will arrive twice; the
    vendor dedupes on this rather than on content, which may legitimately
    repeat."""
    first = _state(report_id="rep-1")
    second = _state(report_id="rep-1")
    assert first.report_id == second.report_id == "rep-1"


def test_a_rejected_report_carries_its_reason() -> None:
    state = _state(status="rejected", reason="StaleLicenceError")
    assert state.reason == "StaleLicenceError"
    assert parse_applied_state(applied_state_payload(state)).status == "rejected"


# ── Strict, fail-closed parsing ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "breakage",
    [
        {"schema": "dotmac-licence-applied-state/999"},
        {"schema": None},
        {"report_id": ""},
        {"report_id": None},
        {"deployment_ref": ""},
        {"licence_id": ""},
        {"licence_version": 0},
        {"licence_version": -1},
        {"licence_version": "3"},
        {"licence_version": True},
        {"digest": ""},
        {"keyring_generation": 0},
        {"keyring_generation": "2"},
        {"revocation_list_version": -1},
        {"revocation_list_version": "5"},
        {"applied_at": "not-a-timestamp"},
        {"applied_at": None},
        {"status": "maybe"},
        {"status": ""},
    ],
)
def test_malformed_reports_fail_closed(breakage: dict[str, object]) -> None:
    payload = applied_state_payload(_state())
    payload.update(breakage)
    with pytest.raises(MalformedAppliedStateError):
        parse_applied_state(payload)


def test_a_naive_timestamp_is_rejected() -> None:
    """Cross-plane timestamps must be unambiguous; a naive one silently means
    whatever the reader's timezone happens to be."""
    payload = applied_state_payload(_state())
    payload["applied_at"] = "2026-08-02T12:00:00"
    with pytest.raises(MalformedAppliedStateError):
        parse_applied_state(payload)


def test_a_non_object_payload_is_rejected() -> None:
    for payload in ([], "applied", 7, None):
        with pytest.raises(MalformedAppliedStateError):
            parse_applied_state(payload)  # type: ignore[arg-type]


def test_construction_validates_too() -> None:
    """The value object cannot be built invalid, so code that constructs one
    directly gets the same guarantees as code that parses one."""
    with pytest.raises(ValueError, match="status"):
        _state(status="perhaps")
    with pytest.raises(ValueError, match="timezone-aware"):
        _state(applied_at=datetime(2026, 8, 2, 12, 0, 0))


def test_unknown_fields_are_ignored_not_trusted() -> None:
    """Forward compatibility: a newer receiver may add fields. They must not
    break an older vendor, and must not silently become part of the record."""
    payload = applied_state_payload(_state())
    payload["future_field"] = "something"
    parsed = parse_applied_state(payload)
    assert not hasattr(parsed, "future_field")
    assert parsed == _state()
