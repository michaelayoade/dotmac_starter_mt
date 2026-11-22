"""Capability configuration: where one installation sends, and as whom.

Everything here is safe to persist in a configuration revision. The password is
NOT configuration: it is the `smtp_password` secret binding, materialised only
for the duration of one dispatch. There is deliberately no default for any
destination field, so an installation that was never configured refuses to send
rather than reaching for a stock host.

`host` is the name the server certificate is verified against. `address` is the
IP literal the connection is made to; it must fall inside a network target the
OPERATOR bound to the installation (`provider.SmtpTransport.send` checks it before
any I/O). Pinning the address in configuration means no name is resolved at send
time, so neither DNS nor a changed record can move a checked destination.

STARTTLS is not a setting: this connector always requires it, with certificate
and hostname verification, TLS 1.2 minimum (`SmtpConfig(starttls=True,
production=True)`). A plaintext mode does not exist to be misconfigured.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dotmac_connector_smtp.config import SmtpConfig, SmtpConfigError

SECRET_PASSWORD: Final = "smtp_password"

CONFIG_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["host", "address", "port", "from_address"],
    "properties": {
        "host": {"type": "string", "minLength": 1, "maxLength": 253},
        "address": {"type": "string", "minLength": 2, "maxLength": 45},
        "port": {"const": 587},
        "username": {"type": "string", "maxLength": 320},
        "from_address": {"type": "string", "minLength": 3, "maxLength": 320},
    },
}

_KEYS: Final = frozenset({"host", "address", "port", "username", "from_address"})


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One validated destination: the transport config plus the pinned address."""

    config: SmtpConfig
    address: str


def endpoint_config(config: Mapping[str, object]) -> Endpoint:
    """Validate a handler config into an `Endpoint`, or raise `SmtpConfigError`.

    Unknown keys other than the engine-owned `_network_targets` are refused, so
    a stray setting cannot be silently ignored.
    """
    unknown = {k for k in config if k not in _KEYS and k != "_network_targets"}
    if unknown:
        raise SmtpConfigError("SMTP configuration has unsupported keys")
    host, address = config.get("host"), config.get("address")
    port, username = config.get("port"), config.get("username", "")
    sender = config.get("from_address")
    if not isinstance(host, str) or not isinstance(address, str):
        raise SmtpConfigError("SMTP host and address are required")
    if not isinstance(sender, str):
        raise SmtpConfigError("SMTP from address is required")
    if not isinstance(port, int) or isinstance(port, bool):
        raise SmtpConfigError("SMTP port is required")
    if not isinstance(username, str):
        raise SmtpConfigError("SMTP username must be text")
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise SmtpConfigError("SMTP address must be an IP address") from None
    smtp = SmtpConfig(
        host=host,
        port=port,
        starttls=True,
        username=username,
        from_address=sender,
        production=True,
    )
    smtp.validate()
    return Endpoint(config=smtp, address=address)


__all__ = ["CONFIG_SCHEMA", "SECRET_PASSWORD", "Endpoint", "endpoint_config"]
