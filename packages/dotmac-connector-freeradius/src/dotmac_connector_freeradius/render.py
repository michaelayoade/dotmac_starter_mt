"""Render one neutral projection step input into exact FreeRADIUS rows.

This is the golden-row surface: for a given (step input, config, secret)
the rows are byte-identical to what Sub's incumbent writer produces for the
same desired state (`app/services/radius_population.py::_radreply_attrs`,
`_radcheck_policy_attrs`, `_projection_rows_for_item` at dotmac_sub
origin/main 1abba5686). The shadow comparison in the suite cutover diffs
these rows against the incumbent's; any drift here is a connector bug.

Rendering branches on `desired_state` and, for enabled logins, on
`policy_code`: Sub's composition publishes its planner vocabulary there
(`active`, `captive`, `reject`); any other enabled policy renders as active.
`disabled` and `suspended` withdraw the identity (no rows) — credential
suspension is an identity fact owned by access-credentials (ADR-0076), while
captive/reject are access decisions for a live identity and therefore stay
`enabled` projections with different rows.

Test-access rows (`TEST_RADIUS_*` in the incumbent) have no v1 contract
vocabulary and are never rendered here; see the contract module in Sub.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

Row = dict[str, object]

POLICY_ACTIVE: Final = "active"
POLICY_CAPTIVE: Final = "captive"
POLICY_REJECT: Final = "reject"


class RenderRefused(ValueError):
    """The step input cannot be rendered — terminal, never retried."""


@dataclass(frozen=True, slots=True)
class RenderConfig:
    """Connector-side render constants (capability configuration)."""

    password_attribute: str = "Cleartext-Password"
    password_op: str = ":="
    default_reply_op: str = ":="
    interim_interval_seconds: int = 300
    use_group: bool = True
    group_priority: int = 1
    simultaneous_use_in_check: bool = True
    active_group: str | None = None
    captive_group: str | None = None
    suspended_group: str | None = None
    access_group_priority: int = 10


@dataclass(frozen=True, slots=True)
class RenderedProjection:
    check_rows: tuple[Row, ...]
    reply_rows: tuple[Row, ...]
    group_rows: tuple[Row, ...]

    @property
    def rows_written(self) -> int:
        return len(self.check_rows) + len(self.reply_rows) + len(self.group_rows)


def _attributes(step_input: Mapping[str, object]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    raw = step_input.get("attributes") or ()
    if not isinstance(raw, Sequence):
        raise RenderRefused("attributes must be a list")
    for item in raw:
        if not isinstance(item, Mapping):
            raise RenderRefused("each attribute must be an object")
        key, value = item.get("key"), item.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            raise RenderRefused("attribute key/value must be strings")
        grouped.setdefault(key, []).append(value)
    return grouped


def _one(grouped: Mapping[str, list[str]], key: str) -> str | None:
    values = grouped.get(key) or []
    if len(values) > 1:
        raise RenderRefused(f"attribute {key!r} may appear at most once")
    return values[0] if values else None


def render_projection(
    step_input: Mapping[str, object],
    *,
    config: RenderConfig,
    cleartext_secret: str | None,
) -> RenderedProjection:
    """The exact rows for one username. Pure: no I/O, no clock."""
    username = step_input.get("username")
    desired_state = step_input.get("desired_state")
    policy_code = str(step_input.get("policy_code") or POLICY_ACTIVE)
    if not isinstance(username, str) or not username:
        raise RenderRefused("username is required")
    if desired_state in ("disabled", "suspended"):
        return RenderedProjection((), (), ())
    if desired_state != "enabled":
        raise RenderRefused(f"unknown desired_state {desired_state!r}")
    if policy_code == POLICY_REJECT:
        return RenderedProjection(
            check_rows=(
                {
                    "username": username,
                    "attribute": "Auth-Type",
                    "op": ":=",
                    "value": "Reject",
                },
            ),
            reply_rows=(),
            group_rows=_access_group_rows(username, config, suspended=True),
        )
    if cleartext_secret is None:
        raise RenderRefused("an enabled projection requires the fetched secret")

    grouped = _attributes(step_input)
    captive = policy_code == POLICY_CAPTIVE

    check_rows: list[Row] = [
        {
            "username": username,
            "attribute": config.password_attribute,
            "op": config.password_op,
            "value": cleartext_secret,
        }
    ]
    simultaneous_use = _one(grouped, "simultaneous_use") or "1"
    if config.simultaneous_use_in_check:
        check_rows.append(
            {
                "username": username,
                "attribute": "Simultaneous-Use",
                "op": ":=",
                "value": simultaneous_use,
            }
        )

    reply_rows: list[Row] = [
        {
            "username": username,
            "attribute": "Service-Type",
            "op": ":=",
            "value": "Framed-User",
        },
        {
            "username": username,
            "attribute": "Framed-Protocol",
            "op": ":=",
            "value": "PPP",
        },
        {
            "username": username,
            "attribute": "Acct-Interim-Interval",
            "op": ":=",
            "value": str(config.interim_interval_seconds),
        },
    ]
    ipv4 = _one(grouped, "framed_ipv4_address")
    if ipv4 and ipv4 != "0.0.0.0":  # noqa: S104 # nosec B104 -- IP-string compare, not a bind
        reply_rows.append(
            {
                "username": username,
                "attribute": "Framed-IP-Address",
                "op": ":=",
                "value": ipv4,
            }
        )
    ipv6 = _one(grouped, "framed_ipv6_prefix")
    if ipv6:
        reply_rows.append(
            {
                "username": username,
                "attribute": "Framed-IPv6-Prefix",
                "op": ":=",
                "value": ipv6,
            }
        )
    delegated = _one(grouped, "delegated_ipv6_prefix")
    if delegated:
        reply_rows.append(
            {
                "username": username,
                "attribute": "Delegated-IPv6-Prefix",
                "op": ":=",
                "value": delegated,
            }
        )
    rate = _one(grouped, "rate_limit")
    if rate:
        reply_rows.append(
            {
                "username": username,
                "attribute": "Mikrotik-Rate-Limit",
                "op": ":=",
                "value": rate,
            }
        )
    if not config.simultaneous_use_in_check:
        reply_rows.append(
            {
                "username": username,
                "attribute": "Simultaneous-Use",
                "op": ":=",
                "value": simultaneous_use,
            }
        )
    idle_timeout = _one(grouped, "idle_timeout_seconds")
    if idle_timeout:
        reply_rows.append(
            {
                "username": username,
                "attribute": "Idle-Timeout",
                "op": ":=",
                "value": idle_timeout,
            }
        )
    if captive:
        address_list = _one(grouped, "address_list") or "suspended"
        reply_rows.append(
            {
                "username": username,
                "attribute": "Mikrotik-Address-List",
                "op": ":=",
                "value": address_list,
            }
        )
    else:
        # Repeatable, order-stable, de-duplicated, never the primary /32 —
        # the incumbent's wipe-hazard guard, preserved.
        primary_host = f"{ipv4}/32" if ipv4 else None
        seen: set[str] = set()
        for route in grouped.get("framed_route", []):
            route = route.strip()
            if not route or route == primary_host or route in seen:
                continue
            seen.add(route)
            reply_rows.append(
                {
                    "username": username,
                    "attribute": "Framed-Route",
                    "op": "+=",
                    "value": f"{route} 0.0.0.0 1",
                }
            )

    group_rows: list[Row] = []
    profile_group = _one(grouped, "profile_group")
    # The incumbent writes the profile group for active logins only; a
    # captive login is grouped by its access group, never its profile.
    if config.use_group and profile_group and not captive:
        group_rows.append(
            {
                "username": username,
                "groupname": profile_group,
                "priority": config.group_priority,
            }
        )
    group_rows.extend(_access_group_rows(username, config, captive=captive))

    return RenderedProjection(
        check_rows=tuple(check_rows),
        reply_rows=tuple(reply_rows),
        group_rows=tuple(group_rows),
    )


def _access_group_rows(
    username: str,
    config: RenderConfig,
    *,
    captive: bool = False,
    suspended: bool = False,
) -> tuple[Row, ...]:
    group = (
        config.captive_group
        if captive
        else config.suspended_group
        if suspended
        else config.active_group
    )
    if not group:
        return ()
    return (
        {
            "username": username,
            "groupname": group,
            "priority": config.access_group_priority,
        },
    )


def rows_match(
    rendered: RenderedProjection,
    *,
    check_rows: Sequence[Mapping[str, object]],
    reply_rows: Sequence[Mapping[str, object]],
    group_rows: Sequence[Mapping[str, object]],
    password_attribute: str,
) -> bool:
    """Exact read-back comparison, secret compared by presence only.

    The password row's VALUE is never compared (and never logged): the
    connector fetched it under a single-use authorization for exactly this
    credential version, so presence of a non-empty password row plus exact
    equality of every other row is the convergence test.
    """

    def normalized(
        rows: Sequence[Mapping[str, object]], columns: tuple[str, ...]
    ) -> list[list[str]]:
        out: list[list[str]] = []
        for row in rows:
            values = [str(row.get(column) or "") for column in columns]
            if values[1:2] == [password_attribute] and len(values) > 3:
                values[3] = "<present>" if values[3] else ""
            out.append(values)
        return sorted(out)

    return (
        normalized(check_rows, ("username", "attribute", "op", "value"))
        == normalized(rendered.check_rows, ("username", "attribute", "op", "value"))
        and normalized(reply_rows, ("username", "attribute", "op", "value"))
        == normalized(rendered.reply_rows, ("username", "attribute", "op", "value"))
        and normalized(group_rows, ("username", "groupname", "priority"))
        == normalized(rendered.group_rows, ("username", "groupname", "priority"))
    )


__all__ = [
    "POLICY_ACTIVE",
    "POLICY_CAPTIVE",
    "POLICY_REJECT",
    "RenderConfig",
    "RenderRefused",
    "RenderedProjection",
    "Row",
    "render_projection",
    "rows_match",
]
