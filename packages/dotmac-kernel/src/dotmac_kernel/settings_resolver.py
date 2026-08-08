"""Settings spec registry + tenant -> platform -> default resolver.

Lives in `dotmac_kernel`, not in a feature package: features may never import
each other, and both the `settings` and `custom_fields` features consume
`resolve_value`. A feature's own spec module DECLARES its `SettingSpec`
instances and calls `register_specs` at import time; this module owns the
registry *mechanism* (register/get/all), resolution, and the upsert/ensure
helpers over `dotmac_kernel.settings_models.DomainSetting`.

Which domains a spec may name is declared on module manifests and enforced on
the write path — see `dotmac_kernel.setting_domains`.

**Any cache added over these reads must be tenant-scoped in the key.** A key of
the shape `settings:{domain}:{key}` is correct for a single-scope settings
table and a cross-tenant leak here, where `domain_settings` rows are
tenant-scoped and Redis is shared across every worker. A cache must therefore
take a REQUIRED tenant argument (never an optional one defaulting to
unscoped), carry the scope in a form a tenant cannot occupy
(`…:tenant=<uuid>` vs a literal `…:platform`), expose the platform read as a
SEPARATELY NAMED function rather than `tenant_id=None` through the shared one,
and invalidate scope-correctly. `resolve_value(..., tenant_id=None)` is the
platform path through the shared function today, so that split has to land
WITH the cache, not after it.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.config import settings
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.messaging.outbox import enqueue_event, enqueue_platform_event
from dotmac_kernel.setting_domains import active_setting_domains
from dotmac_kernel.setting_scopes import (
    SettingScope,
    active_scope_kinds,
    resolution_chain,
)
from dotmac_kernel.setting_value_types import (
    SettingValueType,
    UndeclaredValueTypeError,
    active_setting_value_types,
)
from dotmac_kernel.settings_cache import MISS as _CACHE_MISS
from dotmac_kernel.settings_cache import cached as _cached
from dotmac_kernel.settings_cache import invalidate as _invalidate_cache
from dotmac_kernel.settings_cache import store_resolved as _store_resolved
from dotmac_kernel.settings_crypto import decrypt_value, encrypt_value
from dotmac_kernel.settings_models import (
    DomainSetting,
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
)

# Sentinel distinguishing "no default kwarg passed" from "default=None was
# passed explicitly" in resolve_value.
logger = logging.getLogger(__name__)

_UNSET = object()

# What determined a resolved value — see `resolve_with_source`.
# What determined a resolved value: the SCOPE KIND of the row that won, or
# `env`/`default` when no row did. A plain `str` rather than a closed Literal,
# because the set of scope kinds is itself declared — the kernel's two still
# render as "tenant" and "platform", so nothing visible changes today.
SettingSource = str


@dataclass(frozen=True)
class SettingSpec:
    """Declares one (domain, key) setting: its type, default, and constraints."""

    domain: SettingDomain
    key: str
    value_type: SettingValueType
    default: object | None
    label: str | None = None
    # Prose shown beside `label` on the settings screen. A setting an operator
    # cannot interpret is a setting they will not touch.
    description: str | None = None
    # Environment variable consulted BELOW the platform row and ABOVE `default`
    # — see `resolve_with_source` for why that position and no other.
    env_var: str | None = None
    # A setting the deployment cannot run correctly without. Checked at startup
    # by `validate_required_settings`, which `create_app` treats as fatal in
    # production and a warning elsewhere.
    required: bool = False
    allowed: set[str] | None = None
    min_value: int | None = None
    max_value: int | None = None
    is_secret: bool = False
    validator: Callable[[object], None] | None = None


_REGISTRY: dict[tuple[SettingDomain, str], SettingSpec] = {}


def register_specs(specs: list[SettingSpec]) -> None:
    """Add/overwrite specs in the module-level registry, keyed by (domain, key).

    Called by a feature's spec module at import time. Idempotent —
    re-registering the same (domain, key) overwrites the prior spec, so
    re-importing a spec module (e.g. under test reload) is harmless.

    Domains are NOT validated here. Registration happens at import time, before
    any registry is installed, and the registry is a property of ONE assembly
    while `_REGISTRY` is process-global — a spec module imported by the test
    suite would otherwise break an unrelated assembly's boot. The write path
    (`upsert_by_key`/`ensure_by_key`) is the enforcement point, exactly as it
    is for audit actions; `tests/architecture/test_manifest_declarations.py`
    checks the real assembly's specs against its declarations.
    """
    for spec in specs:
        _REGISTRY[(spec.domain, spec.key)] = spec


def all_specs() -> list[SettingSpec]:
    return list(_REGISTRY.values())


def get_spec(domain: SettingDomain, key: str) -> SettingSpec:
    """Look up a registered spec. Raises `KeyError` if unregistered.

    The settings admin API catches this and maps it to `NotFoundError`.
    """
    try:
        return _REGISTRY[(domain, key)]
    except KeyError:
        raise KeyError(f"No registered setting spec for {domain.value}/{key}") from None


def _scope_for(
    tenant_id: UUID | None | object, scope: SettingScope | None
) -> SettingScope:
    """Normalise the two ways a caller can name a scope into one.

    `scope=` is the real parameter. `tenant_id=` is the shorthand that predates
    scope depth and still covers the common case; it is kept because it reads
    well at a call site that genuinely means "this tenant", not for
    compatibility alone. Passing both is a caller error rather than a merge,
    because there is no sensible answer to which one wins.
    """
    if scope is not None:
        if tenant_id is not _UNSET:
            raise TypeError(
                "pass either tenant_id= or scope=, not both — they name the "
                "same thing and there is no rule for which would win"
            )
        return scope
    resolved = None if tenant_id is _UNSET else tenant_id
    if resolved is None:
        return SettingScope.platform()
    return SettingScope.tenant(cast("UUID", resolved))


def _select_row(
    db: Session, domain: SettingDomain, key: str, scope: SettingScope
) -> DomainSetting | None:
    """The active row for this (domain, key, scope), or None.

    `is_active=False` falls through resolution exactly like a missing row:
    an inactive tenant row yields to the platform row, an inactive platform
    row yields to the spec default.
    """
    statement = (
        select(DomainSetting)
        .where(DomainSetting.domain == domain)
        .where(DomainSetting.key == key)
        .where(DomainSetting.scope_kind == scope.kind)
        .where(DomainSetting.is_active == True)  # noqa: E712
    )
    # `IS NULL` rather than `== None`: SQL equality against NULL is never true,
    # so a platform row would be invisible to its own lookup.
    statement = statement.where(
        DomainSetting.tenant_id.is_(None)
        if scope.tenant_id is None
        else DomainSetting.tenant_id == scope.tenant_id
    )
    statement = statement.where(
        DomainSetting.scope_id.is_(None)
        if scope.scope_id is None
        else DomainSetting.scope_id == scope.scope_id
    )
    return db.scalars(statement).first()


def _extract_raw(setting: DomainSetting | None) -> object | None:
    """The stored value, decrypted when the row holds a secret.

    `value_json` is never encrypted: only `value_text` carries a secret, since
    a secret is a scalar credential and a JSON blob of them would need per-field
    handling this deliberately does not have. `decrypt_value` passes plaintext
    through, so a row written before a key existed still resolves.
    """
    if setting is None:
        return None
    if setting.value_json is not None:
        return setting.value_json
    if setting.is_secret:
        return decrypt_value(setting.value_text)
    return setting.value_text


def _coerce(value_type: SettingValueType, raw: object) -> object | None:
    """Read a stored value through its type's own spec.

    One line, because the type owns its encoding — `dotmac_kernel
    .setting_value_types.ValueTypeSpec.from_storage`. This used to be an
    if-ladder that knew every type, one of three such ladders.

    An UNDECLARED type reads as `None` rather than raising: the caller is the
    read path, which degrades to the spec default, and a row whose type a
    deployment no longer declares must not take down every request.
    """
    if raw is None:
        return None
    try:
        spec = active_setting_value_types().require(value_type)
    except UndeclaredValueTypeError:
        return None
    return spec.from_storage(raw)


def _finish(
    spec: SettingSpec, raw: object | None, source: SettingSource
) -> tuple[Any, SettingSource]:
    """Turn a raw stored value into the resolved one: env fallback, coercion,
    constraint checks, and the degrade-to-default rule.

    Shared by the single-key and bulk paths deliberately. Two copies of these
    rules would drift, and the drift would be invisible — a page reading twenty
    settings in bulk would quietly answer differently from the same settings
    read one at a time.
    """
    if raw is None and spec.env_var is not None:
        # BELOW both rows and ABOVE the spec default: an env var is
        # deployment-scoped so it must not beat a stored row, but it is a real
        # operator decision so it must beat a default the code shipped.
        environment = os.environ.get(spec.env_var)
        if environment is not None and environment != "":
            raw, source = environment, "env"
    if raw is None:
        raw, source = spec.default, "default"

    value = _coerce(spec.value_type, raw)
    if value is None and raw is not None:
        # Unreadable stored value — degrade to the default rather than surface
        # something the spec says is impossible.
        value, source = spec.default, "default"
    if spec.allowed is not None and value is not None and value not in spec.allowed:
        value, source = spec.default, "default"
    if isinstance(value, int) and not isinstance(value, bool):
        if spec.min_value is not None and value < spec.min_value:
            value, source = spec.default, "default"
        elif spec.max_value is not None and value > spec.max_value:
            value, source = spec.default, "default"
    if spec.validator is not None and value is not None:
        try:
            spec.validator(value)
        except ValueError:
            value, source = spec.default, "default"
    if isinstance(value, dict | list):
        # A MUTABLE resolved value may be the shared `spec.default` object, so
        # every caller must get an independent copy.
        value = copy.deepcopy(value)
    return value, source


def resolve_with_source(
    db: Session,
    domain: SettingDomain,
    key: str,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
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
    target = _scope_for(tenant_id, scope)
    try:
        spec = get_spec(domain, key)
    except KeyError:
        if default is not _UNSET:
            # Nothing is cached here: this path never touches the database, so
            # there is no read to save.
            return default, "default"
        raise

    hit = _cached(domain, key, scope=target)
    if hit is not _CACHE_MISS:
        return cast("tuple[Any, SettingSource]", hit)

    row = None
    source: SettingSource = "default"
    # Most specific first, ending at platform. The chain comes from the DECLARED
    # ranks, so a product that inserts a level (site, region, user) gets it in
    # the right position without editing this function.
    for candidate in resolution_chain(target, active_scope_kinds()):
        row = _select_row(db, domain, key, candidate)
        if row is not None:
            source = candidate.kind
            break

    value, source = _finish(spec, _extract_raw(row), source)

    _store_resolved(
        domain,
        key,
        scope=target,
        value=value,
        source=source,
        is_secret=spec.is_secret,
    )
    return value, source


def resolve_value(
    db: Session,
    domain: SettingDomain,
    key: str,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
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
        db, domain, key, tenant_id=tenant_id, scope=scope, default=default
    )
    return value


def resolve_many(
    db: Session,
    domain: SettingDomain,
    keys: Sequence[str] | None = None,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
) -> dict[str, Any]:
    """Resolve many settings of one domain at one scope, in one pass.

    `resolve_value` costs up to one query per level of the chain, so a screen
    reading twenty settings costs forty queries and the cache only helps once
    it is warm. This costs one query PER LEVEL regardless of how many keys are
    asked for — three or four, not eighty — by fetching each level's rows with
    `key IN (...)` and applying precedence in memory.

    `keys=None` means every registered key of the domain, which is what the
    settings screen actually wants.

    Precedence, coercion and the degrade-to-default rule come from `_finish`,
    the same function the single-key path uses. That sharing is deliberate: two
    implementations of these rules would drift, and a page reading in bulk would
    quietly disagree with the same settings read one at a time.
    """
    target = _scope_for(tenant_id, scope)
    wanted = (
        list(keys)
        if keys is not None
        else [spec.key for spec in all_specs() if spec.domain == domain]
    )
    specs = {}
    for key in wanted:
        try:
            specs[key] = get_spec(domain, key)
        except KeyError:
            continue
    if not specs:
        return {}

    # Most specific first; the first level to supply a key wins it.
    winner: dict[str, tuple[object | None, SettingSource]] = {}
    for candidate in resolution_chain(target, active_scope_kinds()):
        outstanding = [key for key in specs if key not in winner]
        if not outstanding:
            break
        for row in _select_rows(db, domain, outstanding, candidate):
            winner[row.key] = (_extract_raw(row), candidate.kind)

    resolved: dict[str, Any] = {}
    for key, spec in specs.items():
        raw, source = winner.get(key, (None, "default"))
        resolved[key], _ = _finish(spec, raw, source)
    return resolved


def _select_rows(
    db: Session, domain: SettingDomain, keys: Sequence[str], scope: SettingScope
) -> Sequence[DomainSetting]:
    """Every active row for these keys at exactly this scope — one query."""
    statement = (
        select(DomainSetting)
        .where(DomainSetting.domain == domain)
        .where(DomainSetting.key.in_(list(keys)))
        .where(DomainSetting.scope_kind == scope.kind)
        .where(DomainSetting.is_active == True)  # noqa: E712
    )
    statement = statement.where(
        DomainSetting.tenant_id.is_(None)
        if scope.tenant_id is None
        else DomainSetting.tenant_id == scope.tenant_id
    )
    statement = statement.where(
        DomainSetting.scope_id.is_(None)
        if scope.scope_id is None
        else DomainSetting.scope_id == scope.scope_id
    )
    return db.scalars(statement).all()


def validate_required_settings(db: Session) -> list[str]:
    """Every `required` spec that resolves to nothing, as operator-readable text.

    Called once at startup by `create_app`, AFTER seeds run — a seeded platform
    default is a real configured value, and checking before seeding would report
    settings that are about to exist. Resolution is platform-scoped
    (`tenant_id=None`): a required setting is a deployment prerequisite, and a
    per-tenant override cannot satisfy it for the tenants that lack one.

    Returns a list rather than raising so the caller decides severity — fatal in
    production, a warning elsewhere, the same split `validate_settings` already
    applies to `Settings`. Reporting ALL failures at once matters here: an
    operator bringing up a deployment should see every missing value in one
    pass, not rediscover them one restart at a time.
    """
    errors: list[str] = []
    for spec in all_specs():
        if not spec.required:
            continue
        value, source = resolve_with_source(db, spec.domain, spec.key, tenant_id=None)
        if value is None or (source == "default" and spec.default is None):
            errors.append(
                f"required setting {spec.domain.value}/{spec.key} is not "
                "configured: no platform row"
                + (f", no {spec.env_var}" if spec.env_var else "")
                + ", and no default"
            )
    return errors


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

    if spec.allowed is not None and coerced not in spec.allowed:
        raise BadRequestError(
            f"{spec.domain.value}/{spec.key}: value must be one of "
            f"{sorted(spec.allowed)}"
        )

    if isinstance(coerced, int) and not isinstance(coerced, bool):
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
    value_type: SettingValueType, value: object, *, is_secret: bool = False
) -> tuple[str | None, dict[str, Any] | None]:
    """Split a Python value into the model's (value_text, value_json) pair.

    Which column a type uses, and how it renders, both come from its
    `ValueTypeSpec` — so a new type needs no edit here. `to_storage` raises
    `ValueError` on an invalid value, which the write path surfaces as a clean
    400 rather than an `IntegrityError` at flush time.

    A secret is encrypted at this last point before the value reaches the
    column, so both writers get it from one place. Only `text`-stored types can
    be secret: encrypting a JSON structure would need per-field handling that
    does not exist, and silently storing it in the clear would be worse.
    """
    spec = active_setting_value_types().require(value_type)
    stored = spec.to_storage(value)
    if spec.storage == "json":
        if is_secret:
            raise ValueError(
                f"value type {str(value_type)!r} stores JSON and cannot be a "
                "secret — a secret must be a scalar this can encrypt whole"
            )
        return None, cast("dict[str, Any]", stored)
    text = str(stored)
    return (encrypt_value(text) if is_secret else text), None


def _stored_text(row: DomainSetting) -> str | None:
    """The row's current value as history records it — text or dumped JSON."""
    if row.value_json is not None:
        return json.dumps(row.value_json)
    return row.value_text


SETTING_CHANGED_EVENT = "settings.changed"


def _emit_change(
    db: Session, *, spec: SettingSpec, key: str, scope: SettingScope, action: str
) -> None:
    """Announce a setting change on the outbox, in the caller's transaction.

    A setting change is invisible to anything holding derived state — another
    worker's cache, a projection, a process that read the value at boot. The
    kernel already has an outbox for exactly this, and settings simply were not
    using it.

    **The value is never in the payload.** A subscriber that needs it resolves
    it, which keeps one reader of the value and means a secret does not travel
    through a delivery pipeline with its own retention and logging.

    Off unless `SETTINGS_CHANGE_EVENTS` is set: an event with no relay running
    is a row that accumulates forever. Failure to enqueue is swallowed and
    logged — a notification that could not be sent must not roll back the write
    it was describing.
    """
    if not settings.settings_change_events:
        return
    payload: dict[str, object] = {
        "domain": str(spec.domain),
        "key": key,
        "action": action,
        "scope_kind": scope.kind,
        "scope_id": str(scope.scope_id) if scope.scope_id else None,
        "is_secret": spec.is_secret,
    }
    try:
        if scope.tenant_id is None:
            enqueue_platform_event(
                db, event_type=SETTING_CHANGED_EVENT, payload=payload
            )
        else:
            enqueue_event(
                db,
                tenant_id=scope.tenant_id,
                event_type=SETTING_CHANGED_EVENT,
                payload=payload,
            )
    except Exception as exc:  # never fail the write for a notification
        logger.warning("Settings change event not enqueued for %s: %s", key, exc)


def _record_history(
    db: Session,
    *,
    row: DomainSetting,
    spec: SettingSpec,
    action: SettingChangeAction,
    before: str | None,
) -> DomainSettingHistory:
    """Record one value transition. Called by both writers, never by a caller.

    A secret's value is redacted rather than stored — see
    `DomainSettingHistory`'s docstring for why a history table must not become
    the place a rotated credential outlives its rotation. The actor is NOT
    recorded here: `write_audit_event` owns who-did-what, and the two records
    correlate on `(tenant_id, domain, key)` and adjacent timestamps.
    """
    entry = DomainSettingHistory(
        tenant_id=row.tenant_id,
        domain=row.domain,
        key=row.key,
        setting_id=row.id,
        action=action,
        value_before=None if spec.is_secret else before,
        value_after=None if spec.is_secret else _stored_text(row),
        secret_changed=spec.is_secret,
    )
    db.add(entry)
    db.flush()
    return entry


def upsert_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    value: object,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
) -> DomainSetting:
    """Create or overwrite the (domain, key, tenant_id) row with `value`.

    `tenant_id=None` writes the PLATFORM row — only callers holding a
    platform-role session (`dotmac_kernel.db.PlatformSessionLocal` /
    `get_platform_db`, i.e. the `platform_api` DB role) may pass `None`;
    `app_user` cannot write NULL-tenant rows (enforced by the settings
    migration's RLS policy), so a tenant-scoped session attempting this fails
    at the DB layer, not here.
    """
    domain = active_setting_domains().require(domain)
    target = _scope_for(tenant_id, scope)
    active_scope_kinds().require(target.kind)
    spec = get_spec(domain, key)
    value_text, value_json = _normalize_for_db(
        spec.value_type, value, is_secret=spec.is_secret
    )

    row = _select_row(db, domain, key, target)
    if row is not None:
        # Captured BEFORE the assignment: once the attribute is set, the prior
        # value is only recoverable from the session's history, which a later
        # flush discards.
        before = _stored_text(row)
        row.value_type = spec.value_type
        row.value_text = value_text
        row.value_json = value_json
        db.flush()
        _record_history(
            db,
            row=row,
            spec=spec,
            action=SettingChangeAction.update,
            before=before,
        )
        _invalidate_cache(domain, key, scope=target)
        _emit_change(db, spec=spec, key=key, scope=target, action="update")
        return row

    row = DomainSetting(
        tenant_id=target.tenant_id,
        scope_kind=target.kind,
        scope_id=target.scope_id,
        domain=domain,
        key=key,
        value_type=spec.value_type,
        value_text=value_text,
        value_json=value_json,
        is_secret=spec.is_secret,
    )
    db.add(row)
    db.flush()
    _record_history(
        db, row=row, spec=spec, action=SettingChangeAction.create, before=None
    )
    _invalidate_cache(domain, key, scope=target)
    _emit_change(db, spec=spec, key=key, scope=target, action="create")
    return row


def ensure_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    value: object,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
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
    INSERT, reintroducing finding F3 (see `dotmac_kernel.db.conflict_savepoint`
    and this repo's CLAUDE.md "Hard rules" entry on feature-service
    rollbacks) for any query run afterwards on the same session. If a
    request-scoped caller ever needs "insert if missing" semantics, wrap the
    insert in `conflict_savepoint` instead of reusing this function's
    `db.rollback()`.

    `tenant_id=None` targets the PLATFORM row — only a platform-role session
    should pass `None` (see `upsert_by_key`'s docstring).
    """
    domain = active_setting_domains().require(domain)
    target = _scope_for(tenant_id, scope)
    active_scope_kinds().require(target.kind)
    existing = _select_row(db, domain, key, target)
    if existing is not None:
        return existing

    spec = get_spec(domain, key)
    value_text, value_json = _normalize_for_db(
        spec.value_type, value, is_secret=spec.is_secret
    )
    row = DomainSetting(
        tenant_id=target.tenant_id,
        scope_kind=target.kind,
        scope_id=target.scope_id,
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
        raced = _select_row(db, domain, key, target)
        if raced is not None:
            # The race LOSER wrote nothing, so it records nothing: the winner
            # already recorded the one creation that happened.
            return raced
        raise
    _record_history(
        db, row=row, spec=spec, action=SettingChangeAction.create, before=None
    )
    _invalidate_cache(domain, key, scope=target)
    _emit_change(db, spec=spec, key=key, scope=target, action="create")
    return row


__all__ = [
    "SettingSpec",
    "register_specs",
    "SETTING_CHANGED_EVENT",
    "resolve_many",
    "resolve_value",
    "validate_required_settings",
]
