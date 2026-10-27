"""Golden rows: rendering must equal the incumbent writer's rows exactly."""

from __future__ import annotations

import pytest
from dotmac_connector_freeradius.render import (
    RenderConfig,
    RenderRefused,
    render_projection,
    rows_match,
)

HELD = "held-test-material"

CONFIG = RenderConfig(active_group="access-active", captive_group="access-captive")

ENABLED = {
    "subject_ref": "svc-1",
    "username": "user@dotmac",
    "desired_state": "enabled",
    "policy_code": "active",
    "policy_version": "v1",
    "attributes": [
        {"key": "framed_ipv4_address", "value": "10.0.0.5"},
        {"key": "framed_ipv6_prefix", "value": "2001:db8:1::/64"},
        {"key": "rate_limit", "value": "50M/50M"},
        {"key": "idle_timeout_seconds", "value": "600"},
        {"key": "profile_group", "value": "residential"},
        {"key": "framed_route", "value": "10.9.0.0/24"},
        {"key": "framed_route", "value": "10.9.0.0/24"},
        {"key": "framed_route", "value": "10.0.0.5/32"},
    ],
    "credential_id": "cred-1",
    "secret_version": 3,
    "projection_request_ref": "proj-1",
    "expected_fingerprint": "a" * 64,
}


def test_enabled_golden_rows() -> None:
    rendered = render_projection(ENABLED, config=CONFIG, cleartext_secret=HELD)
    assert rendered.check_rows == (
        {
            "username": "user@dotmac",
            "attribute": "Cleartext-Password",
            "op": ":=",
            "value": HELD,
        },
        {
            "username": "user@dotmac",
            "attribute": "Simultaneous-Use",
            "op": ":=",
            "value": "1",
        },
    )
    assert rendered.reply_rows == (
        {
            "username": "user@dotmac",
            "attribute": "Service-Type",
            "op": ":=",
            "value": "Framed-User",
        },
        {
            "username": "user@dotmac",
            "attribute": "Framed-Protocol",
            "op": ":=",
            "value": "PPP",
        },
        {
            "username": "user@dotmac",
            "attribute": "Acct-Interim-Interval",
            "op": ":=",
            "value": "300",
        },
        {
            "username": "user@dotmac",
            "attribute": "Framed-IP-Address",
            "op": ":=",
            "value": "10.0.0.5",
        },
        {
            "username": "user@dotmac",
            "attribute": "Framed-IPv6-Prefix",
            "op": ":=",
            "value": "2001:db8:1::/64",
        },
        {
            "username": "user@dotmac",
            "attribute": "Mikrotik-Rate-Limit",
            "op": ":=",
            "value": "50M/50M",
        },
        {
            "username": "user@dotmac",
            "attribute": "Idle-Timeout",
            "op": ":=",
            "value": "600",
        },
        {
            "username": "user@dotmac",
            "attribute": "Framed-Route",
            "op": "+=",
            "value": "10.9.0.0/24 0.0.0.0 1",
        },
    )
    assert rendered.group_rows == (
        {"username": "user@dotmac", "groupname": "residential", "priority": 1},
        {"username": "user@dotmac", "groupname": "access-active", "priority": 10},
    )


def test_captive_gets_address_list_and_no_routes_or_profile_group() -> None:
    rendered = render_projection(
        {**ENABLED, "policy_code": "captive"}, config=CONFIG, cleartext_secret=HELD
    )
    attributes = [row["attribute"] for row in rendered.reply_rows]
    assert "Mikrotik-Address-List" in attributes
    assert "Framed-Route" not in attributes
    groups = [row["groupname"] for row in rendered.group_rows]
    assert groups == ["access-captive"]


def test_reject_is_one_check_row() -> None:
    rendered = render_projection(
        {**ENABLED, "policy_code": "reject"}, config=CONFIG, cleartext_secret=None
    )
    assert rendered.check_rows == (
        {
            "username": "user@dotmac",
            "attribute": "Auth-Type",
            "op": ":=",
            "value": "Reject",
        },
    )
    assert rendered.reply_rows == ()


def test_withdrawn_states_render_nothing() -> None:
    for state in ("disabled", "suspended"):
        rendered = render_projection(
            {**ENABLED, "desired_state": state}, config=CONFIG, cleartext_secret=None
        )
        assert rendered.rows_written == 0


def test_enabled_without_secret_is_refused() -> None:
    with pytest.raises(RenderRefused, match="fetched secret"):
        render_projection(ENABLED, config=CONFIG, cleartext_secret=None)


def test_rows_match_masks_only_the_password_value() -> None:
    rendered = render_projection(ENABLED, config=CONFIG, cleartext_secret=HELD)
    observed_check = [
        {**row, "value": "different-secret"}
        if row["attribute"] == "Cleartext-Password"
        else dict(row)
        for row in rendered.check_rows
    ]
    assert rows_match(
        rendered,
        check_rows=observed_check,
        reply_rows=list(rendered.reply_rows),
        group_rows=list(rendered.group_rows),
        password_attribute=CONFIG.password_attribute,
    )
    drifted = [dict(row) for row in rendered.reply_rows[:-1]]
    assert not rows_match(
        rendered,
        check_rows=observed_check,
        reply_rows=drifted,
        group_rows=list(rendered.group_rows),
        password_attribute=CONFIG.password_attribute,
    )
