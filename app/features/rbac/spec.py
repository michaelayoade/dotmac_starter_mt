"""This feature's own setting declarations.

`retention_days` is read here — `_audit_retention_cutoff` feeds it straight
into `timedelta(days=...)` — so it is declared here. Under the old string-keyed
read it arrived as `Any` and that `timedelta` call was unchecked; typing the
spec `SettingSpec[int]` makes the arithmetic checked at the call site.
"""

from __future__ import annotations

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

AUDIT_RETENTION_DAYS: SettingSpec[int] = SettingSpec(
    domain=SettingDomain.audit,
    key="retention_days",
    value_type=SettingValueType.integer,
    default=365,
    min_value=1,
    label="Audit event retention period (days)",
    description=(
        "How long audit events are kept before a retention job may remove "
        "them. Shortening it does not delete anything retroactively."
    ),
)

register_specs([AUDIT_RETENTION_DAYS])

__all__ = ["AUDIT_RETENTION_DAYS"]
