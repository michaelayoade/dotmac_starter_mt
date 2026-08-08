"""Settings admin service — merge specs with effective values; validate + write updates.

All `select()`/session-mutation calls for the settings domain live in
`dotmac_kernel.settings_resolver` (shared with `custom_fields`, which must consume
the same resolver — see that module's docstring). This service only shapes
requests/responses around it, following the RBAC feature's router/service
split.
"""

from __future__ import annotations

from dotmac_kernel.exceptions import NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_kernel.setting_domains import (
    UndeclaredSettingDomainError,
    active_setting_domains,
)
from dotmac_kernel.settings_admin import (
    all_specs,
    get_spec,
    resolve_with_source,
    upsert_by_key,
    validate_spec_value,
)
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec
from sqlalchemy.orm import Session

from app.features.settings.schemas import SettingOut

# Shown instead of a secret's real value whenever a tenant or platform row
# exists for it — never for the spec default (there's nothing sensitive to
# hide in a built-in default). Not a credential itself — a fixed display mask.
MASKED_SECRET_VALUE = "********"  # noqa: S105 # nosec B105


def _domain_from_str(domain: str) -> SettingDomain:
    """A path-supplied domain string, or 404.

    The registry is what makes an arbitrary string a real domain — the type
    itself is an open `str` subclass and accepts anything.
    """
    try:
        return active_setting_domains().require(domain)
    except UndeclaredSettingDomainError:
        raise NotFoundError(f"Unknown settings domain: {domain}") from None


def _to_setting_out(db: Session, tenant: Tenant, spec: SettingSpec) -> SettingOut:
    value, source = resolve_with_source(db, spec.domain, spec.key, tenant_id=tenant.id)
    if spec.is_secret and source != "default":
        value = MASKED_SECRET_VALUE
    return SettingOut(
        domain=spec.domain.value,
        key=spec.key,
        value=value,
        value_type=spec.value_type.value,
        label=spec.label,
        description=spec.description,
        is_secret=spec.is_secret,
        source=source,
    )


def list_settings(db: Session, tenant: Tenant, domain: str) -> list[SettingOut]:
    """Every registered spec for `domain`, merged with the tenant's effective values."""
    domain_enum = _domain_from_str(domain)
    specs = [spec for spec in all_specs() if spec.domain == domain_enum]
    return [_to_setting_out(db, tenant, spec) for spec in specs]


def update_setting(
    db: Session, tenant: Tenant, domain: str, key: str, value: object
) -> SettingOut:
    """Validate `value` against the spec, write the TENANT row, return it resolved."""
    domain_enum = _domain_from_str(domain)
    try:
        spec = get_spec(domain_enum, key)
    except KeyError:
        raise NotFoundError(f"Unknown setting: {domain}/{key}") from None

    coerced = validate_spec_value(spec, value)
    upsert_by_key(db, domain_enum, key, coerced, tenant_id=tenant.id)
    return _to_setting_out(db, tenant, spec)


__all__ = ["MASKED_SECRET_VALUE", "list_settings", "update_setting"]
