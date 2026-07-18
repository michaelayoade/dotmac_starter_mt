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

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.core.settings_models import DomainSetting, SettingDomain, SettingValueType

# Sentinel distinguishing "no default kwarg passed" from "default=None was
# passed explicitly" in resolve_value.
_UNSET = object()

# Which row (if any) determined a resolved value — see `resolve_with_source`.
SettingSource = Literal["tenant", "platform", "default"]


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
    allowed: set[str] | None = None
    min_value: int | None = None
    max_value: int | None = None
    is_secret: bool = False
    validator: Callable[[object], None] | None = None


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
    """Final-review Group 4(c): `is_active` was a dead column — declared on
    the model (`settings_models.py`), never read here. A deactivated row now
    falls through resolution exactly like a missing one (tenant row inactive
    -> platform row; platform row inactive -> spec default), instead of
    resolving as if it were still active.
    """
    return db.scalars(
        select(DomainSetting)
        .where(DomainSetting.domain == domain)
        .where(DomainSetting.key == key)
        .where(DomainSetting.tenant_id == tenant_id)
        .where(DomainSetting.is_active == True)  # noqa: E712
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


def resolve_with_source(
    db: Session,
    domain: SettingDomain,
    key: str,
    *,
    tenant_id: UUID | None,
    default: Any = _UNSET,
) -> tuple[Any, SettingSource]:
    """Resolve a setting value AND where it came from: tenant, platform, or default.

    Same precedence/coercion/fallback semantics as `resolve_value` (which now
    delegates here, keeping its signature and behavior identical) — see that
    docstring for the resolution rules. `source` reflects the ROW that
    actually determined the returned value: if a tenant or platform row
    exists but fails coercion/`allowed`/range/`validator` validation,
    resolution falls back to the spec default and `source` is `"default"`
    too (a bad stored value degrades all the way to the safe default, not to
    "the row exists but we ignored it").

    Added for the settings admin API (`GET /settings/{domain}`), which must
    tell callers whether a value is tenant-overridden, platform-default, or
    the spec's built-in default (e.g. to mask secrets only when a real row
    value is shown, not when displaying the default).
    """
    try:
        spec = get_spec(domain, key)
    except KeyError:
        if default is not _UNSET:
            return default, "default"
        raise

    row = None
    source: SettingSource = "default"
    if tenant_id is not None:
        row = _select_row(db, domain, key, tenant_id)
        if row is not None:
            source = "tenant"
    if row is None:
        row = _select_row(db, domain, key, None)
        if row is not None:
            source = "platform"

    raw = _extract_raw(row)
    if raw is None:
        raw = spec.default
        source = "default"

    value = _coerce(spec.value_type, raw)
    if value is None and raw is not None:
        # Coercion failed (e.g. a corrupted/unparseable stored value) —
        # degrade to the spec default rather than surface a bad value.
        value = spec.default
        source = "default"

    if spec.allowed is not None and value is not None and value not in spec.allowed:
        value = spec.default
        source = "default"

    if spec.value_type == SettingValueType.integer and isinstance(value, int):
        if spec.min_value is not None and value < spec.min_value:
            value = spec.default
            source = "default"
        elif spec.max_value is not None and value > spec.max_value:
            value = spec.default
            source = "default"

    if spec.validator is not None and value is not None:
        try:
            spec.validator(value)
        except ValueError:
            value = spec.default
            source = "default"

    if spec.value_type == SettingValueType.json and value is not None:
        # json-type values are mutable (dicts). `value` may be the shared
        # `spec.default` object (assigned by reference above whenever
        # resolution falls back to the default) — every caller must get an
        # independent copy so mutating one caller's result can't corrupt the
        # spec default (and thus every future resolution) for the rest of
        # the process. Scalars (bool/int/str) are immutable, no copy needed.
        value = copy.deepcopy(value)

    return value, source


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

    Thin delegator over `resolve_with_source` — see that function for the
    `source` ("tenant"/"platform"/"default") the settings admin API needs.
    """
    value, _source = resolve_with_source(
        db, domain, key, tenant_id=tenant_id, default=default
    )
    return value


def validate_spec_value(spec: SettingSpec, value: object) -> object:
    """Validate `value` against `spec`'s type/allowed/range constraints for a WRITE.

    Unlike `resolve_value`'s coercion (which silently degrades an unreadable
    *stored* value to the spec default — a read-path safety net), this is the
    write-path gate: any violation raises `BadRequestError` instead of
    guessing, so the caller (the settings admin API) can return a clean 400
    before anything is written. Returns the coerced value, ready to pass to
    `upsert_by_key`.

    `None` is never accepted, for any `value_type`. This closes a gap in
    `_normalize_for_db`: passed straight through, `None` would either violate
    the `ck_domain_settings_value_alignment` CHECK constraint at flush time
    for `json` (raising a raw `IntegrityError` instead of a clean 400) or
    silently store `false` for `boolean` (a null update would be
    misinterpreted as an explicit "off" with no error at all).

    A spec's `validator`, if set, runs last and raises `BadRequestError` too
    (wrapping its `ValueError`) — e.g. the `display` domain's IANA-timezone
    and strftime-format checks.
    """
    if value is None:
        raise BadRequestError(f"{spec.domain.value}/{spec.key}: value must not be null")

    coerced = _coerce(spec.value_type, value)
    if coerced is None:
        raise BadRequestError(
            f"{spec.domain.value}/{spec.key}: invalid value for type "
            f"{spec.value_type.value}"
        )

    if spec.value_type == SettingValueType.json and not isinstance(coerced, dict):
        raise BadRequestError(
            f"{spec.domain.value}/{spec.key}: value must be an object"
        )

    if spec.allowed is not None and coerced not in spec.allowed:
        raise BadRequestError(
            f"{spec.domain.value}/{spec.key}: value must be one of "
            f"{sorted(spec.allowed)}"
        )

    if spec.value_type == SettingValueType.integer and isinstance(coerced, int):
        if spec.min_value is not None and coerced < spec.min_value:
            raise BadRequestError(
                f"{spec.domain.value}/{spec.key}: value must be >= {spec.min_value}"
            )
        if spec.max_value is not None and coerced > spec.max_value:
            raise BadRequestError(
                f"{spec.domain.value}/{spec.key}: value must be <= {spec.max_value}"
            )

    if spec.validator is not None:
        try:
            spec.validator(coerced)
        except ValueError as exc:
            raise BadRequestError(
                f"Invalid value for {spec.domain.value}.{spec.key}: {exc}"
            ) from None

    return coerced


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

    WARNING — the bare `db.rollback()` on the race path above is safe ONLY
    because this function is called exclusively from a dedicated PLATFORM
    session (`seed_platform_defaults()` at startup, outside any request),
    which owns its whole transaction and sets no per-request state on it.
    NEVER call `ensure_by_key` from a request-scoped `get_db` session — a
    full rollback there would discard that session's `SET LOCAL
    app.current_tenant` RLS context along with the race-loser's failed
    INSERT, reintroducing finding F3 (see `app.core.db.conflict_savepoint`
    and this repo's CLAUDE.md "Hard rules" entry on feature-service
    rollbacks) for any query run afterwards on the same session. If a
    request-scoped caller ever needs "insert if missing" semantics, wrap the
    insert in `conflict_savepoint` instead of reusing this function's
    `db.rollback()`.

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
