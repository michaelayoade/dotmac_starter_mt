"""Template Studio's own setting declarations (ADR-0006 § 5h, maker-checker).

`require_separate_publisher` is OFF by default: a tenant that wants authoring
and publishing split across two people turns it on. When on, publishing a
revision for the first time is refused for the party who authored it (see
`service.publish_version`).

The domain is declared on this module's manifest (`setting_domains`), so the
settings write path accepts it, and the spec is registered at import time like
every other spec.
"""

from __future__ import annotations

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec, register_specs

TEMPLATE_STUDIO_DOMAIN = SettingDomain("template_studio")

REQUIRE_SEPARATE_PUBLISHER: SettingSpec[bool] = SettingSpec(
    domain=TEMPLATE_STUDIO_DOMAIN,
    key="require_separate_publisher",
    value_type=SettingValueType.boolean,
    default=False,
    label="Require a different person to publish a template",
    description=(
        "When on, the person who authored a template version cannot be the one "
        "who first publishes it. Rolling back to a version that was already "
        "published is not affected. Off by default."
    ),
)

register_specs([REQUIRE_SEPARATE_PUBLISHER])

__all__ = ["REQUIRE_SEPARATE_PUBLISHER", "TEMPLATE_STUDIO_DOMAIN"]
