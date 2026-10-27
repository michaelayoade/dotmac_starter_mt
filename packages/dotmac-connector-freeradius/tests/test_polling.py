"""Poll cursor, event mapping and fingerprint stability."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_connector_freeradius.polling import (
    FreeRadiusPollError,
    FreeRadiusPollHandler,
)
from dotmac_connector_freeradius.ports import AccountingRow, PostAuthRow

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


class FakeDb:
    def __init__(self, postauth=(), acct=()) -> None:
        self._postauth = postauth
        self._acct = acct

    def poll_postauth(self, *, after_id, limit):
        return tuple(r for r in self._postauth if r.row_id > after_id)[:limit]

    def poll_accounting(self, *, after_id, limit):
        return tuple(r for r in self._acct if r.row_id > after_id)[:limit]


POSTAUTH = (
    PostAuthRow(1, "user@dotmac", "Access-Accept", NOW, "10.0.0.1"),
    PostAuthRow(2, "", "Access-Accept", NOW, "10.0.0.1"),
    PostAuthRow(3, "bad@dotmac", "Access-Reject", NOW, "10.0.0.1"),
)
ACCT = (
    AccountingRow(1, "user@dotmac", "10.0.0.1", "sess-1", "Start", 0, 0, 0, NOW),
    AccountingRow(2, "user@dotmac", "10.0.0.1", "sess-1", "Stop", 10, 20, 60, NOW),
    AccountingRow(
        3, "user@dotmac", "10.0.0.1", "sess-1", "Accounting-On", 0, 0, 0, NOW
    ),
)


def _handler() -> FreeRadiusPollHandler:
    return FreeRadiusPollHandler(
        db_factory=lambda config, secrets: FakeDb(POSTAUTH, ACCT)
    )


def test_poll_maps_events_skips_junk_and_advances_past_it() -> None:
    events, cursor = _handler().poll(None, config={}, secrets={})
    kinds = [
        (e.event_type, e.payload.get("outcome") or e.payload.get("event_kind"))
        for e in events
    ]
    assert kinds == [
        ("authentication", "accepted"),
        ("authentication", "rejected"),
        ("accounting", "start"),
        ("accounting", "stop"),
    ]
    assert cursor == '{"acct_id":3,"postauth_id":3}'
    # Idempotent at the tail: re-polling the returned cursor emits nothing.
    again, again_cursor = _handler().poll(cursor, config={}, secrets={})
    assert again == ()
    assert again_cursor == cursor


def test_fingerprints_are_stable_across_polls() -> None:
    first, _ = _handler().poll(None, config={}, secrets={})
    second, _ = _handler().poll(None, config={}, secrets={})
    assert [e.payload["fingerprint"] for e in first] == [
        e.payload["fingerprint"] for e in second
    ]


def test_a_partial_cursor_is_refused() -> None:
    with pytest.raises(FreeRadiusPollError, match="exactly"):
        _handler().poll('{"postauth_id": 1}', config={}, secrets={})
    with pytest.raises(FreeRadiusPollError, match="canonical JSON"):
        _handler().poll("not-json", config={}, secrets={})
