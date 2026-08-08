"""This feature's own setting declarations.

`registration_policy` gates `POST /auth/register`, which lives in this feature,
so the spec does too — and being `SettingSpec[str]` means the `policy != "open"`
comparison is against a `str` rather than `Any`.
"""

from __future__ import annotations

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

REGISTRATION_POLICY: SettingSpec[str] = SettingSpec(
    domain=SettingDomain.auth,
    key="registration_policy",
    value_type=SettingValueType.string,
    default="closed",
    allowed={"open", "closed"},
    label="Self-registration policy (open | closed)",
    description=(
        "Whether anyone may create an account through POST /auth/register. "
        "Closed is the default: a tenant admin invites instead."
    ),
)

register_specs([REGISTRATION_POLICY])

__all__ = ["REGISTRATION_POLICY"]
