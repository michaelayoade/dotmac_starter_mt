"""Capability configuration: one FreeRADIUS SQL target per installation.

The DSN is credential material and therefore a secret binding, never config.
Everything here is safe to persist in a configuration revision: table names,
render constants and poll bounds. `target_ref` is the opaque name results
carry back so a multi-target fleet can tell its evidence apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from dotmac_connector_freeradius.render import RenderConfig

CONFIG_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_ref": {"type": "string", "minLength": 1, "maxLength": 120},
        "radcheck_table": {"type": "string", "maxLength": 64},
        "radreply_table": {"type": "string", "maxLength": 64},
        "radusergroup_table": {"type": "string", "maxLength": 64},
        "radacct_table": {"type": "string", "maxLength": 64},
        "radpostauth_table": {"type": "string", "maxLength": 64},
        "nas_table": {"type": "string", "maxLength": 64},
        "password_attribute": {"type": "string", "maxLength": 64},
        "password_op": {"type": "string", "maxLength": 4},
        "default_reply_op": {"type": "string", "maxLength": 4},
        "interim_interval_seconds": {
            "type": "integer",
            "minimum": 60,
            "maximum": 3600,
        },
        "use_group": {"type": "boolean"},
        "group_priority": {"type": "integer", "minimum": 0, "maximum": 100},
        "simultaneous_use_in_check": {"type": "boolean"},
        "active_group": {"type": ["string", "null"], "maxLength": 64},
        "captive_group": {"type": ["string", "null"], "maxLength": 64},
        "suspended_group": {"type": ["string", "null"], "maxLength": 64},
        "access_group_priority": {"type": "integer", "minimum": 0, "maximum": 100},
        "secret_fetch_base_url": {"type": ["string", "null"], "maxLength": 253},
        "secret_fetch_timeout_seconds": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 30,
        },
        "poll_limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "coa_timeout_seconds": {"type": "number", "minimum": 0.5, "maximum": 30},
        "lock_key_prefix": {"type": "string", "maxLength": 64},
    },
}


@dataclass(frozen=True, slots=True)
class TargetConfig:
    target_ref: str = "primary"
    radcheck_table: str = "radcheck"
    radreply_table: str = "radreply"
    radusergroup_table: str = "radusergroup"
    radacct_table: str = "radacct"
    radpostauth_table: str = "radpostauth"
    nas_table: str = "nas"
    secret_fetch_base_url: str | None = None
    secret_fetch_timeout_seconds: float = 10.0
    poll_limit: int = 200
    coa_timeout_seconds: float = 5.0
    lock_key_prefix: str = "freeradius-projection"
    render: RenderConfig = field(default_factory=RenderConfig)


def target_config(config: Mapping[str, object]) -> TargetConfig:
    def text(name: str, default: str) -> str:
        value = config.get(name)
        return value if isinstance(value, str) and value else default

    def optional_text(name: str) -> str | None:
        value = config.get(name)
        return value if isinstance(value, str) and value else None

    def integer(name: str, default: int) -> int:
        value = config.get(name)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) else default
        )

    def boolean(name: str, default: bool) -> bool:
        value = config.get(name)
        return value if isinstance(value, bool) else default

    fetch_timeout = config.get("secret_fetch_timeout_seconds")
    timeout = config.get("coa_timeout_seconds")
    return TargetConfig(
        target_ref=text("target_ref", "primary"),
        radcheck_table=text("radcheck_table", "radcheck"),
        radreply_table=text("radreply_table", "radreply"),
        radusergroup_table=text("radusergroup_table", "radusergroup"),
        radacct_table=text("radacct_table", "radacct"),
        radpostauth_table=text("radpostauth_table", "radpostauth"),
        nas_table=text("nas_table", "nas"),
        secret_fetch_base_url=optional_text("secret_fetch_base_url"),
        secret_fetch_timeout_seconds=(
            float(fetch_timeout) if isinstance(fetch_timeout, int | float) else 10.0
        ),
        poll_limit=integer("poll_limit", 200),
        coa_timeout_seconds=(
            float(timeout) if isinstance(timeout, int | float) else 5.0
        ),
        lock_key_prefix=text("lock_key_prefix", "freeradius-projection"),
        render=RenderConfig(
            password_attribute=text("password_attribute", "Cleartext-Password"),
            password_op=text("password_op", ":="),
            default_reply_op=text("default_reply_op", ":="),
            interim_interval_seconds=integer("interim_interval_seconds", 300),
            use_group=boolean("use_group", True),
            group_priority=integer("group_priority", 1),
            simultaneous_use_in_check=boolean("simultaneous_use_in_check", True),
            active_group=optional_text("active_group"),
            captive_group=optional_text("captive_group"),
            suspended_group=optional_text("suspended_group"),
            access_group_priority=integer("access_group_priority", 10),
        ),
    )


__all__ = ["CONFIG_SCHEMA", "TargetConfig", "target_config"]
