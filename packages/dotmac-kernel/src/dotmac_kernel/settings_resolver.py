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
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.config import settings
from dotmac_kernel.exceptions import BadRequestError
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

# What determined a resolved value: the SCOPE KIND of the row that won, or
# `default` when no row did. Never `env` — see `seed_settings_from_env`.
#
# A plain `str` rather than a closed Literal, because the set of scope kinds is
# itself declared; the kernel's two still render as "tenant" and "platform".
SettingSource = str


@dataclass(frozen=True, slots=True)
class SettingChangeContext:
    """Who is making a settings change, and on what request.

    Passed to the writers so the history row records it. Every field is
    optional because a seed, a migration or a CLI genuinely has no actor, and
    recording "unknown" honestly is better than inventing one.

    Frozen, and every field a scalar — no mutable container to be shared
    between two changes and then edited.
    """

    actor_party_id: UUID | None = None
    reason: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


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
    # Environment variable that BOOTSTRAPS this setting's platform row on first
    # start — see `seed_settings_from_env`. It is NOT consulted at read time.
    #
    # A live env fallback makes resolution depend on process environment rather
    # than on data: two workers with different environments answer the same
    # question differently and silently, the value has no history and no owner,
    # and restoring the database does not reproduce it. Seeding a real row
    # instead keeps one authority, gives the value an audit trail, and makes
    # every process agree. (`dotmac_sub` reached this rule first; the kernel
    # had the weaker behaviour.)
    env_var: str | None = None
    # The SCOPE KIND at which this setting must be configured, or None if it is
    # optional. `"platform"` means the deployment cannot run without it, and is
    # checked at startup. A finer kind — `"tenant"`, `"site"` — means each
    # instance of that level must have its own value, which startup cannot check
    # because instances come and go long after boot; callers ask
    # `missing_required_settings` for one scope when it matters (provisioning a
    # tenant, opening a site).
    #
    # A bool could only ever express the deployment case, which is why "every
    # tenant must set a billing contact" had no way to be stated.
    required_at: str | None = None
    allowed: set[str] | None = None
    min_value: int | None = None
    max_value: int | None = None
    is_secret: bool = False
    validator: Callable[[object], None] | None = None


_REGISTRY: dict[tuple[SettingDomain, str], SettingSpec] = {}


class DuplicateSettingSpecError(ValueError):
    """Two modules declared the same setting with different definitions."""


class InvalidSpecDefaultError(ValueError):
    """A spec's own default violates the spec's own constraints."""


def _fingerprint(spec: SettingSpec) -> tuple[object, ...]:
    """What makes two declarations of one setting the SAME declaration.

    Re-importing a spec module (a test reload, a module imported twice through
    different paths) builds an equal-but-not-identical `SettingSpec`, and its
    `validator` is a fresh function object every time — so plain equality would
    reject legitimate re-registration. Validators are compared by qualified name
    instead: same function, same declaration.
    """
    return (
        str(spec.domain),
        spec.key,
        str(spec.value_type),
        repr(spec.default),
        spec.label,
        spec.description,
        spec.env_var,
        spec.required_at,
        tuple(sorted(spec.allowed)) if spec.allowed else None,
        spec.min_value,
        spec.max_value,
        spec.is_secret,
        getattr(spec.validator, "__qualname__", None),
    )


def _validate_default(spec: SettingSpec) -> None:
    """A spec's default must satisfy the spec's own constraints.

    Otherwise resolution's degrade-to-default path returns a value the spec
    forbids — silently, and for every reader. Checked at registration because it
    is a property of the DECLARATION, knowable without a database or a request.

    `default=None` is legitimate and skipped: a setting that must be configured
    (`required_at`) has no sensible built-in default.
    """
    if spec.default is None:
        return
    try:
        validate_spec_value(spec, spec.default)
    except BadRequestError as exc:
        raise InvalidSpecDefaultError(
            f"setting {spec.domain.value}/{spec.key} declares a default its own "
            f"spec rejects: {exc}. Resolution degrades to this default, so every "
            "reader would silently receive a forbidden value."
        ) from None


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
        existing = _REGISTRY.get((spec.domain, spec.key))
        if existing is not None and _fingerprint(existing) != _fingerprint(spec):
            raise DuplicateSettingSpecError(
                f"setting {spec.domain.value}/{spec.key} is declared twice with "
                "different definitions — whichever module imported last would "
                "silently win, so the effective spec would depend on import "
                "order. Every other registry in the kernel fails here; this one "
                "used to overwrite quietly."
            )
        _validate_default(spec)
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
        return decrypt_value(setting.value_text, tenant_id=setting.tenant_id)
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

    # Serve whatever is already cached, and only query for the rest. Without
    # this the bulk path neither read nor warmed the cache, so the screen that
    # most needs it got no benefit AND left single-key reads still missing.
    resolved: dict[str, Any] = {}
    outstanding_specs: dict[str, SettingSpec] = {}
    for key, spec in specs.items():
        hit = _cached(domain, key, scope=target)
        if hit is not _CACHE_MISS:
            resolved[key] = cast("tuple[Any, SettingSource]", hit)[0]
        else:
            outstanding_specs[key] = spec
    if not outstanding_specs:
        return resolved
    specs = outstanding_specs

    # Most specific first; the first level to supply a key wins it.
    winner: dict[str, tuple[object | None, SettingSource]] = {}
    for candidate in resolution_chain(target, active_scope_kinds()):
        outstanding = [key for key in specs if key not in winner]
        if not outstanding:
            break
        for row in _select_rows(db, domain, outstanding, candidate):
            winner[row.key] = (_extract_raw(row), candidate.kind)

    for key, spec in specs.items():
        raw, source = winner.get(key, (None, "default"))
        value, resolved_source = _finish(spec, raw, source)
        resolved[key] = value
        # Warm the cache from the bulk read too, so a later single-key read of
        # the same setting hits rather than repeating the work.
        _store_resolved(
            domain,
            key,
            scope=target,
            value=value,
            source=resolved_source,
            is_secret=spec.is_secret,
        )
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


def prune_setting_history(
    db: Session,
    *,
    older_than_days: int,
    scope: SettingScope | None = None,
) -> int:
    """Delete history rows older than `older_than_days`. Returns the count.

    `DomainSettingHistory` is append-only and had no retention, so it grows for
    the life of the deployment. Append-only is about who may rewrite it, not
    about keeping it forever.

    Deliberately a FUNCTION a caller schedules rather than something the write
    path does: pruning inside a write would make an ordinary setting change
    occasionally do unbounded work, and the operator — not the kernel — decides
    how long a change record is worth keeping.

    `scope=None` prunes every scope. Callers hold the transaction; this flushes
    but never commits, like every other writer here.
    """
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    statement = delete(DomainSettingHistory).where(
        DomainSettingHistory.changed_at < cutoff
    )
    if scope is not None:
        statement = statement.where(
            DomainSettingHistory.tenant_id.is_(None)
            if scope.tenant_id is None
            else DomainSettingHistory.tenant_id == scope.tenant_id
        )
    # `CursorResult` carries `rowcount`; the base `Result` protocol does not,
    # and a DELETE always yields the former.
    result = cast("CursorResult[Any]", db.execute(statement))
    removed = result.rowcount or 0
    db.flush()
    logger.info("Pruned %d setting-history row(s) older than %s", removed, cutoff)
    return removed


def seed_settings_from_env(db: Session) -> int:
    """Create the platform row for any spec whose `env_var` is set and which has
    no row yet. Returns how many were created.

    This is what `env_var` means: a BOOTSTRAP input, read once, turned into a
    real row that then behaves like every other value — visible on the settings
    screen, editable, historied, and identical in every process.

    Idempotent and non-destructive: a setting that already has a platform row is
    left alone, so an operator who has since changed the value does not have it
    reverted on the next restart by a stale variable in the unit file. That
    one-way property is the reason this is safe to run on every boot.

    An empty variable is treated as unset — an exported-but-empty value is how a
    shell says "not configured", not how an operator says "the empty string".
    """
    created = 0
    for spec in all_specs():
        if not spec.env_var:
            continue
        raw = os.environ.get(spec.env_var)
        if raw is None or raw == "":
            continue
        platform = SettingScope.platform()
        if _select_row(db, spec.domain, spec.key, platform) is not None:
            continue
        try:
            ensure_by_key(db, spec.domain, spec.key, raw, scope=platform)
        except (BadRequestError, ValueError) as exc:
            # A bad variable must not stop the boot, and must not be silent.
            logger.error(
                "Could not seed %s/%s from %s: %s",
                spec.domain.value,
                spec.key,
                spec.env_var,
                exc,
            )
            continue
        created += 1
    if created:
        logger.info("Seeded %d setting(s) from the environment", created)
    return created


def missing_required_settings(
    db: Session,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
) -> list[str]:
    """Required settings that resolve to nothing AT THIS SCOPE, as readable text.

    A spec is checked when its `required_at` names this scope's kind, so one
    call answers "is this tenant configured" without dragging in the
    deployment's own prerequisites or another tenant's.

    Returns a list rather than raising so the caller decides severity — fatal at
    boot for the deployment, a blocked provisioning step for a tenant, a warning
    on a settings screen. Reporting ALL of them at once matters: an operator
    bringing something up should see every missing value in one pass rather than
    rediscover them one restart at a time.
    """
    target = _scope_for(tenant_id, scope)
    errors: list[str] = []
    for spec in all_specs():
        if spec.required_at != target.kind:
            continue
        value, source = resolve_with_source(db, spec.domain, spec.key, scope=target)
        if value is None or (source == "default" and spec.default is None):
            errors.append(
                f"required setting {spec.domain.value}/{spec.key} is not "
                f"configured at scope {target.kind!r}: no row"
                + (f" (and {spec.env_var} was unset at boot)" if spec.env_var else "")
                + ", and no default"
            )
    return errors


def validate_required_settings(db: Session) -> list[str]:
    """The DEPLOYMENT's own prerequisites — every spec with
    `required_at="platform"`.

    Called once at startup by `create_app`, AFTER seeds run, because a seeded
    platform default is a real configured value. Finer scopes are deliberately
    not checked here: a tenant that does not exist yet cannot be missing
    anything, and enumerating every tenant at boot would make startup cost grow
    with the customer count. Use `missing_required_settings` at the moment a
    given scope matters.
    """
    return missing_required_settings(db, scope=SettingScope.platform())


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

    # Validated through the WRITE direction (`to_storage`), not the read one.
    # `_coerce` answers "can this STORED form be read back", which is a
    # different question: a `money` value arrives here as a `Money` object and
    # is stored as a dict, so read-direction coercion rejected every valid write.
    # The two directions are a matched pair on one spec precisely so each is
    # used for its own half.
    # Round-tripped through the type's OWN matched pair: `to_storage` rejects an
    # invalid value, and `from_storage` of the result is the canonical Python
    # form — so a form's "30" becomes 30 and a `Money` stays a `Money`.
    #
    # Validating with `from_storage` alone (as this did) asks the wrong
    # question — "can this STORED form be read back" — and rejected every valid
    # `money` write, since one arrives as a `Money` and is stored as a dict.
    # Validating with `to_storage` alone skips the coercion the form boundary
    # depends on, so "abc" for an integer sailed through.
    try:
        type_spec = active_setting_value_types().require(spec.value_type)
        coerced = type_spec.from_storage(type_spec.to_storage(value))
    except UndeclaredValueTypeError as exc:
        raise BadRequestError(f"{spec.domain.value}/{spec.key}: {exc}") from None
    except ValueError as exc:
        raise BadRequestError(
            f"{spec.domain.value}/{spec.key}: invalid value for type "
            f"{spec.value_type.value} ({exc})"
        ) from None
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
    value_type: SettingValueType,
    value: object,
    *,
    is_secret: bool = False,
    tenant_id: UUID | None = None,
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
    return (encrypt_value(text, tenant_id=tenant_id) if is_secret else text), None


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
    # Imported HERE, not at module scope: the outbox reaches the database layer,
    # and `import dotmac_kernel.settings_resolver` must keep working without a
    # DATABASE_URL — that is a documented property of the supported surface and
    # the `kernel-floors` job proves it by importing with the variable unset.
    from dotmac_kernel.messaging.outbox import enqueue_event, enqueue_platform_event

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
    changed_by: SettingChangeContext | None = None,
) -> DomainSettingHistory:
    """Record one value transition. Called by both writers, never by a caller.

    A secret's value is redacted rather than stored — see
    `DomainSettingHistory`'s docstring for why a history table must not become
    the place a rotated credential outlives its rotation. WHO made the change is
    recorded, because it is intrinsic to the change record; without it an
    operator answering "who turned this off" would be joining tables on
    timestamp proximity.
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
        changed_by_party_id=changed_by.actor_party_id if changed_by else None,
        change_reason=changed_by.reason if changed_by else None,
        ip_address=changed_by.ip_address if changed_by else None,
        user_agent=changed_by.user_agent if changed_by else None,
        request_id=changed_by.request_id if changed_by else None,
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
    changed_by: SettingChangeContext | None = None,
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
    # Enforced HERE, not only in whichever service remembered to call it. The
    # writer already refuses an undeclared domain, an undeclared scope kind and
    # an uncoercible value; leaving `allowed`/min/max/`validator` to the caller
    # meant an out-of-range write succeeded and the READ then degraded it to the
    # default — so the operator saw no error and the setting silently did not
    # take effect. Returns the coerced value, which is what gets stored.
    value = validate_spec_value(spec, value)
    value_text, value_json = _normalize_for_db(
        spec.value_type, value, is_secret=spec.is_secret, tenant_id=target.tenant_id
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
            changed_by=changed_by,
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
        db,
        row=row,
        spec=spec,
        action=SettingChangeAction.create,
        before=None,
        changed_by=changed_by,
    )
    _invalidate_cache(domain, key, scope=target)
    _emit_change(db, spec=spec, key=key, scope=target, action="create")
    return row


def clear_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
    changed_by: SettingChangeContext | None = None,
) -> bool:
    """Remove the override at exactly this scope. Returns whether one existed.

    Setting a value had no inverse: a tenant could override a platform default
    but never go back to inheriting it, which is the kind of hole that ends with
    someone running DELETE by hand in production.

    DELETES the row rather than deactivating it. `is_active=False` would leave a
    tombstone that the uniqueness index still counts, so the scope could never
    be set again — and "inherit from above" is precisely the absence of a row,
    not a row that says nothing.

    Only this scope's own row goes. A tenant clearing its override does not
    touch the platform value it falls back to, and clearing something that was
    never set is a no-op rather than an error.
    """
    domain = active_setting_domains().require(domain)
    target = _scope_for(tenant_id, scope)
    active_scope_kinds().require(target.kind)
    spec = get_spec(domain, key)

    row = _select_row(db, domain, key, target)
    if row is None:
        return False

    before = _stored_text(row)
    _record_history(
        db,
        row=row,
        spec=spec,
        action=SettingChangeAction.delete,
        before=before,
        changed_by=changed_by,
    )
    db.delete(row)
    db.flush()
    _invalidate_cache(domain, key, scope=target)
    _emit_change(db, spec=spec, key=key, scope=target, action="delete")
    return True


def ensure_by_key(
    db: Session,
    domain: SettingDomain,
    key: str,
    value: object,
    *,
    tenant_id: UUID | None | object = _UNSET,
    scope: SettingScope | None = None,
    changed_by: SettingChangeContext | None = None,
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
    # Same enforcement as `upsert_by_key`: a seed that plants a value the spec
    # forbids should fail at boot, not resolve to the default forever after.
    value = validate_spec_value(spec, value)
    value_text, value_json = _normalize_for_db(
        spec.value_type, value, is_secret=spec.is_secret, tenant_id=target.tenant_id
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
        db,
        row=row,
        spec=spec,
        action=SettingChangeAction.create,
        before=None,
        changed_by=changed_by,
    )
    _invalidate_cache(domain, key, scope=target)
    _emit_change(db, spec=spec, key=key, scope=target, action="create")
    return row


__all__ = [
    "SettingSpec",
    "register_specs",
    "SETTING_CHANGED_EVENT",
    "SettingChangeContext",
    "clear_by_key",
    "missing_required_settings",
    "prune_setting_history",
    "resolve_many",
    "seed_settings_from_env",
    "resolve_value",
    "validate_required_settings",
]
