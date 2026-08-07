"""Typed feature flags — declaration, evaluation, and the precedence that
makes an evaluation explainable (module control-plane directive step 5).

## A flag is not a permission and not an entitlement

Three decisions that look alike and are not:

- **Permission** — may this ACTOR act? (`require_permission`)
- **Entitlement** — does this TENANT have the feature at all? (`require_capability`)
- **Flag** — is this code path ON for this request? (here)

A flag answers "which implementation runs", never "who may run it". The
directive states the rule flatly — *flags cannot grant permissions* — and it is
enforced statically: flag codes and permission/capability codes are disjoint
namespaces, so a flag can never be mistaken for an authorization decision by a
reader or by a guard.

## Evaluation is explainable, and the reason is part of the answer

`evaluate` returns a `FlagEvaluation`, never a bare bool. "It was on" is not
useful during an incident; "it was on because tenant override `rule-42` set it,
against a default of off" is. Every branch below records why it won.

## Precedence, highest first

1. **Kill switch** — an emergency off, and it OUTRANKS everything including a
   rollout. That ordering is the whole point of having one: the person turning a
   feature off at 3am must not have to find and unwind every override first.
2. **Tenant override** — an explicit value for one tenant.
3. **Tenant rollout** — a deterministic percentage bucket.
4. **Platform override** — an explicit deployment-wide value.
5. **Platform rollout**.
6. **The declared default** — what the owning module says it is.

## Rollouts are deterministic, not random

A percentage rollout hashes `(flag code, subject)` and compares the bucket. The
same tenant therefore gets the same answer on every request and every process,
which is what makes a partial rollout debuggable at all — `random()` would flip
a tenant's experience between two requests and make any report unreproducible.
Hashing the code alongside the subject means two flags at 50% do not select the
same half of the fleet.

## Caching

Evaluations are cached through `dotmac_kernel.cache`, so every entry carries its
scope, and invalidation is a VERSION bump rather than a delete sweep: the value
is derived from a mutable override set, so a targeted delete would have to
enumerate what it invalidates and would be wrong the moment the derivation
changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final
from uuid import UUID

from dotmac_kernel.cache import CacheStore, PlatformScope, Scope, TenantScope, cache_key

# The scopes a flag may be overridden at. `deployment` is the platform-wide
# value; `tenant` is one tenant's. Named as strings on the spec because a
# module DECLARES which are meaningful for its flag — a kill-switch-only
# operational flag may legitimately forbid tenant overrides.
DEPLOYMENT_SCOPE: Final[str] = "deployment"
TENANT_SCOPE: Final[str] = "tenant"
VALID_SCOPES: Final[frozenset[str]] = frozenset({DEPLOYMENT_SCOPE, TENANT_SCOPE})

# Supported declared types. Deliberately small: a flag is a switch or a tuning
# knob, and anything richer belongs in settings, which has validation,
# provenance and an editor already.
FlagValue = bool | int | str
_SUPPORTED_TYPES: Final[tuple[type, ...]] = (bool, int, str)

_CACHE_NAMESPACE: Final[str] = "flags"


class FlagError(ValueError):
    """A flag declaration or reference is invalid."""


class DuplicateFlagError(FlagError):
    """Two modules declared the same flag code — there is no single owner."""


class UndeclaredFlagError(KeyError):
    """A flag code was referenced that no installed module declares."""


@dataclass(frozen=True, slots=True)
class FeatureFlagSpec:
    """One flag a module DECLARES and owns.

    `expires_on` is the flag's removal date, and it is not decoration: a flag
    that outlives its rollout is a permanently-branching code path nobody
    remembers the purpose of. A governance test fails the build once the date
    passes, which forces the choice — delete the flag and its dead branch, or
    move the date deliberately.

    `operational` separates the two populations the directive distinguishes: an
    OPERATIONAL flag (a kill switch, a capacity guard) is flipped by operators
    and must not emit an exposure event per evaluation, while an EXPERIMENT may
    record exposure. Getting that backwards floods the audit trail with traffic
    instead of decisions.
    """

    code: str
    value_type: type = bool
    default: FlagValue = False
    owner: str = ""
    description: str = ""
    allowed_scopes: frozenset[str] = frozenset({DEPLOYMENT_SCOPE, TENANT_SCOPE})
    expires_on: date | None = None
    operational: bool = True

    def __post_init__(self) -> None:
        if not self.code:
            raise FlagError("flag spec requires a non-empty `code`")
        if self.value_type not in _SUPPORTED_TYPES:
            raise FlagError(
                f"flag {self.code!r} declares unsupported type "
                f"{self.value_type!r} — expected one of "
                f"{', '.join(t.__name__ for t in _SUPPORTED_TYPES)}"
            )
        # `bool` is a subclass of `int`, so an int-typed flag must not silently
        # accept True — that would make the declared type a suggestion.
        if not _matches(self.default, self.value_type):
            raise FlagError(
                f"flag {self.code!r} declares default {self.default!r}, which is "
                f"not a {self.value_type.__name__}"
            )
        unknown = set(self.allowed_scopes) - VALID_SCOPES
        if unknown:
            raise FlagError(
                f"flag {self.code!r} allows unknown scope(s) {sorted(unknown)} — "
                f"expected a subset of {sorted(VALID_SCOPES)}"
            )
        if not self.allowed_scopes:
            raise FlagError(
                f"flag {self.code!r} allows no scope at all — it could never be "
                "overridden, which makes it a constant, not a flag"
            )
        object.__setattr__(self, "allowed_scopes", frozenset(self.allowed_scopes))

    @property
    def is_expired(self) -> bool:
        """True once the removal date has passed. Read by the governance test,
        never by the evaluator: an expired flag keeps working in production so
        an expiry never causes an outage — it fails the BUILD instead."""
        return self.expires_on is not None and self.expires_on < _today()


def _today() -> date:
    """Indirection so the expiry test can pin a date without freezing time
    globally."""
    from datetime import UTC, datetime

    return datetime.now(UTC).date()


def _matches(value: object, value_type: type) -> bool:
    if value_type is bool:
        return isinstance(value, bool)
    if value_type is int:
        # `bool` is an `int` subclass — exclude it explicitly.
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, value_type)


@dataclass(frozen=True, slots=True)
class FlagEvaluation:
    """The explainable outcome of one flag evaluation.

    `source` says WHICH rule won (`default`, `platform_override`,
    `tenant_override`, `platform_rollout`, `tenant_rollout`, `kill_switch`);
    `reason` is a stable language-neutral code; `rule_id` identifies the
    override row when one decided it, so an operator can go straight to the row
    rather than guessing which of several might have applied.
    """

    code: str
    value: FlagValue
    source: str
    reason: str
    rule_id: UUID | None = None
    evaluated_version: int = 0


@dataclass(frozen=True, slots=True)
class FlagOverrideRecord:
    """One override, as the evaluator consumes it — deliberately decoupled from
    the ORM row so `evaluate` stays pure and testable without a database."""

    flag_code: str
    tenant_id: UUID | None
    value: FlagValue | None = None
    rollout_percentage: int | None = None
    kill_switch: bool = False
    rule_id: UUID | None = None


class FlagCatalogue:
    """The declared flags across an installed module set."""

    __slots__ = ("_by_code", "_owner_by_code")

    def __init__(self, specs: Iterable[FeatureFlagSpec] = ()) -> None:
        self._by_code: dict[str, FeatureFlagSpec] = {}
        self._owner_by_code: dict[str, str] = {}
        for spec in specs:
            self._by_code[spec.code] = spec
            self._owner_by_code[spec.code] = spec.owner

    @classmethod
    def from_manifests(cls, manifests: Iterable[object]) -> FlagCatalogue:
        specs: list[FeatureFlagSpec] = []
        owner_by_code: dict[str, str] = {}
        for manifest in manifests:
            name = getattr(manifest, "name", "")
            for spec in getattr(manifest, "feature_flags", ()) or ():
                existing = owner_by_code.get(spec.code)
                if existing is not None and existing != name:
                    raise DuplicateFlagError(
                        f"flag {spec.code!r} declared by both {existing!r} and "
                        f"{name!r} — a flag code has one owning module"
                    )
                owner_by_code[spec.code] = name
                # The manifest is the authority on ownership: a spec that
                # omitted `owner` inherits the module that declared it, so
                # "every flag has an owner" is true by construction.
                specs.append(spec if spec.owner else _with_owner(spec, name))
        return cls(specs)

    def is_declared(self, code: str) -> bool:
        return code in self._by_code

    def require(self, code: str) -> FeatureFlagSpec:
        spec = self._by_code.get(code)
        if spec is None:
            raise UndeclaredFlagError(
                f"flag code {code!r} is not declared by any installed module"
            )
        return spec

    def codes(self) -> frozenset[str]:
        return frozenset(self._by_code)

    def specs(self) -> tuple[FeatureFlagSpec, ...]:
        return tuple(self._by_code[code] for code in sorted(self._by_code))

    def expired(self) -> tuple[FeatureFlagSpec, ...]:
        return tuple(spec for spec in self.specs() if spec.is_expired)


def _with_owner(spec: FeatureFlagSpec, owner: str) -> FeatureFlagSpec:
    from dataclasses import replace

    return replace(spec, owner=owner)


# ── Deterministic rollout ───────────────────────────────────────────────────


def rollout_bucket(code: str, subject: str) -> int:
    """A stable bucket in [0, 100) for `(code, subject)`.

    Deterministic so a tenant's experience does not flip between requests, and
    salted with the flag code so two flags at 50% do not select the same half of
    the fleet — which would make an A/B result an artefact of the bucketing.
    """
    digest = hashlib.sha256(f"{code}:{subject}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


# ── Evaluation ──────────────────────────────────────────────────────────────


def evaluate(
    spec: FeatureFlagSpec,
    overrides: Sequence[FlagOverrideRecord] = (),
    *,
    tenant_id: UUID | None = None,
    version: int = 0,
) -> FlagEvaluation:
    """Resolve `spec` against `overrides`. Pure — no I/O, no clock.

    `overrides` may contain rows of both scopes; this picks among them by the
    precedence in the module docstring. Rows for another tenant are ignored
    rather than trusted to have been filtered upstream: the evaluator is the
    last place the tenant boundary can be enforced, so it enforces it.
    """
    relevant = [
        o
        for o in overrides
        if o.flag_code == spec.code
        and (
            o.tenant_id is None or (tenant_id is not None and o.tenant_id == tenant_id)
        )
    ]
    tenant_rows = [o for o in relevant if o.tenant_id is not None]
    platform_rows = [o for o in relevant if o.tenant_id is None]

    def _result(value: FlagValue, source: str, reason: str, rule: UUID | None):
        return FlagEvaluation(
            code=spec.code,
            value=value,
            source=source,
            reason=reason,
            rule_id=rule,
            evaluated_version=version,
        )

    # 1. Kill switch — either scope, and it outranks everything below.
    for row in (*tenant_rows, *platform_rows):
        if row.kill_switch:
            # Forced OFF, not "back to default": a default of True would make a
            # kill switch a no-op, which is the one thing it must never be.
            return _result(False, "kill_switch", "killed", row.rule_id)

    # 2/3. Tenant scope, if the flag allows it.
    if TENANT_SCOPE in spec.allowed_scopes and tenant_id is not None:
        for row in tenant_rows:
            if row.value is not None:
                return _result(row.value, "tenant_override", "explicit", row.rule_id)
        for row in tenant_rows:
            if row.rollout_percentage is not None:
                inside = (
                    rollout_bucket(spec.code, str(tenant_id)) < row.rollout_percentage
                )
                return _result(
                    inside,
                    "tenant_rollout",
                    "in_rollout" if inside else "out_of_rollout",
                    row.rule_id,
                )

    # 4/5. Deployment scope.
    if DEPLOYMENT_SCOPE in spec.allowed_scopes:
        for row in platform_rows:
            if row.value is not None:
                return _result(row.value, "platform_override", "explicit", row.rule_id)
        for row in platform_rows:
            if row.rollout_percentage is not None:
                subject = str(tenant_id) if tenant_id is not None else "platform"
                inside = rollout_bucket(spec.code, subject) < row.rollout_percentage
                return _result(
                    inside,
                    "platform_rollout",
                    "in_rollout" if inside else "out_of_rollout",
                    row.rule_id,
                )

    # 6. The owning module's declared default.
    return _result(spec.default, "default", "declared_default", None)


def evaluation_cache_key(code: str, *, tenant_id: UUID | None, version: int) -> str:
    """The cache key for one evaluation — built by `dotmac_kernel.cache`, so the
    scope segment cannot be omitted and a tenant's answer can never land in the
    platform entry."""
    scope: Scope = TenantScope(tenant_id) if tenant_id is not None else PlatformScope()
    return cache_key(_CACHE_NAMESPACE, code, scope=scope, version=version)


def cached_evaluate(
    spec: FeatureFlagSpec,
    overrides: Sequence[FlagOverrideRecord],
    *,
    tenant_id: UUID | None,
    version: int,
    store: CacheStore,
) -> FlagEvaluation:
    """`evaluate`, memoised per (flag, scope, version).

    Invalidation is the `version` argument: bump it and every prior entry is
    unreachable at once. The caller owns that number (see
    `dotmac_kernel.flag_models.override_version`) precisely so invalidation is
    an explicit act rather than a TTL someone has to reason about.
    """
    key = evaluation_cache_key(spec.code, tenant_id=tenant_id, version=version)
    cached = store.get(key)
    if isinstance(cached, FlagEvaluation):
        return cached
    result = evaluate(spec, overrides, tenant_id=tenant_id, version=version)
    store.set(key, result)
    return result


# ── The process-active catalogue ────────────────────────────────────────────

_EMPTY: Final[FlagCatalogue] = FlagCatalogue(())
_active: FlagCatalogue = _EMPTY


def install_flags(catalogue: FlagCatalogue) -> None:
    """Install the process-active flag catalogue (called by `create_app`)."""
    global _active
    _active = catalogue


def active_flags() -> FlagCatalogue:
    """The process-active catalogue — empty until installed.

    Empty means every reference raises `UndeclaredFlagError`. A flag read is not
    an authorization decision, so failing loudly on an unwired catalogue is
    safe: it surfaces the wiring mistake instead of silently serving defaults
    that look like a deliberate configuration.
    """
    return _active


__all__ = [
    "DEPLOYMENT_SCOPE",
    "TENANT_SCOPE",
    "VALID_SCOPES",
    "DuplicateFlagError",
    "FeatureFlagSpec",
    "FlagCatalogue",
    "FlagError",
    "FlagEvaluation",
    "FlagOverrideRecord",
    "FlagValue",
    "UndeclaredFlagError",
    "active_flags",
    "cached_evaluate",
    "evaluate",
    "evaluation_cache_key",
    "install_flags",
    "rollout_bucket",
]
