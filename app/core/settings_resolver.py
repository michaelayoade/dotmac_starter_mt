"""Settings spec registry + tenant -> platform -> default resolver.

Lives in `app.core` (not `app.features.settings`) even though the spec
*declarations* and seed data live in the feature: a later task wires the
`custom_fields` feature to consume `resolve_value` directly, and features may
never import each other, so the resolver mechanics have to sit somewhere both
`custom_fields` and `settings` can import — that's core. `app/features/
settings/spec.py` DECLARES the initial `SettingSpec` instances and calls
`register_specs` at import time; this module only owns the registry
*mechanism* (register/get/all) plus resolution and the upsert/ensure helpers
that operate on `app.core.settings_models.DomainSetting`.

No caching here: phase 1 has no Redis. Backlog: a Redis-backed settings cache
lands in phase 3 (see `dotmac_sub:app/services/settings_cache.py` for the
shape to port when that lands).

Race-safety and precedence mechanics are ported from
`dotmac_sub:app/services/domain_settings.py::ensure_by_key` and
`dotmac_sub:app/services/settings_spec.py::resolve_value`, adapted to this
app's partial-unique-index pair (`uq_domain_settings_platform` /
`uq_domain_settings_tenant`) and explicit `tenant_id: UUID | None` parameter
instead of sub's single-tenant assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.settings_models import DomainSetting, SettingDomain, SettingValueType

# Sentinel distinguishing "no default kwarg passed" from "default=None was
# passed explicitly" in resolve_value.
_UNSET = object()


@dataclass(frozen=True)
class SettingSpec:
    """Declares one (domain, key) setting: its type, default, and constraints.

    Shape matches `dotmac_sub:app/services/settings_spec.py::SettingSpec`
    minus `env_var` — this app seeds explicitly via `seed.py` rather than
    reading env vars into settings rows at spec-declaration time.
    """

    domain: SettingDomain
    key: str
    value_type: SettingValueType
    default: object | None
    label: str | None = None
    required: bool = False
    allowed: set[str] | None = None
    min_value: int | None = None
    max_value: int | None = None
    is_secret: bool = False


_REGISTRY: dict[tuple[SettingDomain, str], SettingSpec] = {}


def register_specs(specs: list[SettingSpec]) -> None:
    """Add/overwrite specs in the module-level registry, keyed by (domain, key).

    Called by `app/features/settings/spec.py` at import time. Idempotent —
    re-registering the same (domain, key) overwrites the prior spec, so
    re-importing a spec module (e.g. under test reload) is harmless.
    """
    for spec in specs:
        _REGISTRY[(spec.domain, spec.key)] = spec


def all_specs() -> list[SettingSpec]:
    return list(_REGISTRY.values())


def get_spec(domain: SettingDomain, key: str) -> SettingSpec:
    """Look up a registered spec. Raises `KeyError` if unregistered.

    The API layer (a later task) catches this and maps it to `NotFoundError`.
    """
    try:
        return _REGISTRY[(domain, key)]
    except KeyError:
        raise KeyError(f"No registered setting spec for {domain.value}/{key}") from None


def _select_row(
    db: Session, domain: SettingDomain, key: str, tenant_id: UUID | None
) -> DomainSetting | None:
    return db.scalars(
        select(DomainSetting)
        .where(DomainSetting.domain == domain)
        .where(DomainSetting.key == key)
        .where(DomainSetting.tenant_id == tenant_id)
    ).first()


def _extract_raw(setting: DomainSetting | None) -> object | None:
    if setting is None:
        return None
    if setting.value_json is not None:
        return setting.value_json
    return setting.value_text


def _coerce(value_type: SettingValueType, raw: object) -> object | None:
    """Coerce a raw stored value to `value_type`. Returns None on failure."""
    if raw is None:
        return None
    if value_type == SettingValueType.boolean:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return None
    if value_type == SettingValueType.integer:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                return None
        return None
    if value_type == SettingValueType.string:
        if isinstance(raw, str):
            return raw
        return str(raw)
    # json: stored value is already a Python object (dict/list/bool/...).
    return raw


def resolve_value(
    db: Session,
    domain: SettingDomain,
    key: str,
    *,
    tenant_id: UUID | None,
    default: Any = _UNSET,
) -> Any:
    """Resolve a setting value: tenant row -> platform row -> spec default.

    Precedence: a tenant-owned row (`tenant_id = tenant_id`) wins over the
    platform-default row (`tenant_id IS NULL`) wins over the spec's own
    `default`. The tenant lookup is skipped entirely when `tenant_id is None`
    (platform-level callers).

    Values are coerced to the spec's `value_type`. If coercion fails, or the
    coerced value violates `allowed` / `min_value` / `max_value`, resolution
    falls back to the spec default rather than raising — this mirrors
    `dotmac_sub`'s `resolve_value` behavior (a bad stored value degrades to
    the safe default instead of breaking every caller).

    If `key` isn't registered (e.g. the settings feature is disabled in this
    deployment), core consumers that must not hard-depend on the settings
    feature being enabled can pass an explicit `default=` kwarg, returned
    as-is. Without a `default=` kwarg, an unregistered key raises `KeyError`.

    No caching: see module docstring — a Redis-backed cache is phase 3.
    """
    try:
        spec = get_spec(domain, key)
    except KeyError:
        if default is not _UNSET:
            return default
        raise

    row = None
    if tenant_id is not None:
        row = _select_row(db, domain, key, tenant_id)
    if row is None:
        row = _select_row(db, domain, key, None)

    raw = _extract_raw(row)
    if raw is None:
        raw = spec.default

    value = _coerce(spec.value_type, raw)
    if value is None and raw is not None:
        # Coercion failed (e.g. a corrupted/unparseable stored value) —
        # degrade to the spec default rather than surface a bad value.
        value = spec.default

    if spec.allowed is not None and value is not None and value not in spec.allowed:
        value = spec.default

    if spec.value_type == SettingValueType.integer and isinstance(value, int):
        if spec.min_value is not None and value < spec.min_value:
            value = spec.default
        elif spec.max_value is not None and value > spec.max_value:
            value = spec.default

    return value


def _normalize_for_db(
    value_type: SettingValueType, value: object
) -> tuple[str | None, dict[str, Any] | None]:
    """Split a Python value into the model's (value_text, value_json) pair,
    respecting the `ck_domain_settings_value_alignment` CHECK constraint."""
    if value_type == SettingValueType.json:
        if value is not None and not isinstance(value, dict):
            raise TypeError(f"json setting value must be a dict, got {type(value)!r}")
        return None, value
    if value_type == SettingValueType.boolean:
        return ("true" if value else "false"), None
    return str(value), None


def upsert_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    value: object,
    *,
    tenant_id: UUID | None,
) -> DomainSetting:
    """Create or overwrite the (domain, key, tenant_id) row with `value`.

    `tenant_id=None` writes the PLATFORM row — only callers holding a
    platform-role session (`app.core.db.PlatformSessionLocal` /
    `get_platform_db`, i.e. the `platform_api` DB role) may pass `None`;
    `app_user` cannot write NULL-tenant rows (enforced by the settings
    migration's RLS policy), so a tenant-scoped session attempting this fails
    at the DB layer, not here.
    """
    spec = get_spec(domain, key)
    value_text, value_json = _normalize_for_db(spec.value_type, value)

    row = _select_row(db, domain, key, tenant_id)
    if row is not None:
        row.value_type = spec.value_type
        row.value_text = value_text
        row.value_json = value_json
        db.flush()
        return row

    row = DomainSetting(
        tenant_id=tenant_id,
        domain=domain,
        key=key,
        value_type=spec.value_type,
        value_text=value_text,
        value_json=value_json,
        is_secret=spec.is_secret,
    )
    db.add(row)
    db.flush()
    return row


def ensure_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    value: object,
    *,
    tenant_id: UUID | None,
) -> DomainSetting:
    """Insert the (domain, key, tenant_id) row with `value` if missing; else no-op.

    Idempotent and never overwrites an existing (e.g. operator-set) row —
    unlike `upsert_by_key`, a second call with a different `value` is a no-op.
    Used by `seed.py::seed_platform_defaults()` so re-seeding on every app
    boot never clobbers an admin's changes.

    Race-safe: if two callers race to insert the same row (e.g. two app
    instances seeding concurrently on startup), the partial unique index
    (`uq_domain_settings_platform` / `uq_domain_settings_tenant`) makes the
    loser's INSERT raise `IntegrityError`. That path rolls back and re-selects
    the winner's row instead of propagating the error — ported from
    `dotmac_sub:app/services/domain_settings.py::ensure_by_key`.

    `tenant_id=None` targets the PLATFORM row — only a platform-role session
    should pass `None` (see `upsert_by_key`'s docstring).
    """
    existing = _select_row(db, domain, key, tenant_id)
    if existing is not None:
        return existing

    spec = get_spec(domain, key)
    value_text, value_json = _normalize_for_db(spec.value_type, value)
    row = DomainSetting(
        tenant_id=tenant_id,
        domain=domain,
        key=key,
        value_type=spec.value_type,
        value_text=value_text,
        value_json=value_json,
        is_secret=spec.is_secret,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = _select_row(db, domain, key, tenant_id)
        if raced is not None:
            return raced
        raise
    return row
