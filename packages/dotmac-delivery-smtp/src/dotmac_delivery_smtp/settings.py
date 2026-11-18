"""Typed SMTP connection settings, entered in the admin settings UI.

Five specs under one domain. There is deliberately NO password spec and no
`env_var`: the password is secret material and comes from the kernel
`SecretSource` seam (`dotmac_kernel.secret_sources`), by NAME, through
`SmtpDeliveryProvider(password_secret_name=...)`. A product binds whatever
custody it has today to that seam; this package adds no secret path.
"""

from __future__ import annotations

from typing import Any

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

from dotmac_delivery_smtp.config import valid_from_address

DELIVERY_SMTP_DOMAIN = SettingDomain("delivery_smtp")


def _check_from(value: object) -> None:
    if value in ("", None):
        return
    if not isinstance(value, str) or not valid_from_address(value):
        raise ValueError("from address must be addr-spec or 'Name <addr-spec>'")


HOST: SettingSpec[str] = SettingSpec(
    domain=DELIVERY_SMTP_DOMAIN,
    key="host",
    value_type=SettingValueType.string,
    default="",
    label="SMTP host",
    description="Mail server host name. Empty disables sending (sends are refused).",
)
PORT: SettingSpec[int] = SettingSpec(
    domain=DELIVERY_SMTP_DOMAIN,
    key="port",
    value_type=SettingValueType.integer,
    default=587,
    min_value=1,
    max_value=65535,
    label="SMTP port",
    description="Submission port (STARTTLS, normally 587).",
)
STARTTLS: SettingSpec[bool] = SettingSpec(
    domain=DELIVERY_SMTP_DOMAIN,
    key="starttls",
    value_type=SettingValueType.boolean,
    default=True,
    label="Require STARTTLS",
    description=(
        "Upgrade the connection with STARTTLS and verify the server certificate. "
        "Must stay on in production: the adapter refuses to send otherwise."
    ),
)
USERNAME: SettingSpec[str] = SettingSpec(
    domain=DELIVERY_SMTP_DOMAIN,
    key="username",
    value_type=SettingValueType.string,
    default="",
    label="SMTP username",
    description=(
        "Login name. Empty means no authentication. " "The password is not a setting."
    ),
)
FROM_ADDRESS: SettingSpec[str] = SettingSpec(
    domain=DELIVERY_SMTP_DOMAIN,
    key="from_address",
    value_type=SettingValueType.string,
    default="",
    label="From address",
    description=(
        "Sender, as addr-spec or 'Name <addr-spec>'. "
        "Its domain forms the Message-ID."
    ),
    validator=_check_from,
)

ALL_SPECS: list[SettingSpec[Any]] = [HOST, PORT, STARTTLS, USERNAME, FROM_ADDRESS]
register_specs(ALL_SPECS)


__all__ = [
    "ALL_SPECS",
    "DELIVERY_SMTP_DOMAIN",
    "FROM_ADDRESS",
    "HOST",
    "PORT",
    "STARTTLS",
    "USERNAME",
]
