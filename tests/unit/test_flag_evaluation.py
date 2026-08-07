"""Flag evaluation: precedence, determinism, explainability, cache scope.

Unit lane — `evaluate` is pure, so most of this needs no database at all. The
Postgres canary for override isolation lives in
`tests/test_flag_override_isolation.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel.cache import MemoryCache
from dotmac_kernel.flags import (
    DEPLOYMENT_SCOPE,
    TENANT_SCOPE,
    FeatureFlagSpec,
    FlagOverrideRecord,
    cached_evaluate,
    evaluate,
    evaluation_cache_key,
    rollout_bucket,
)

SPEC = FeatureFlagSpec(code="probe.flow", value_type=bool, default=False, owner="probe")


def _tenant_row(**kwargs) -> FlagOverrideRecord:
    return FlagOverrideRecord(flag_code=SPEC.code, **kwargs)


# ── Precedence ──────────────────────────────────────────────────────────────


def test_the_declared_default_wins_when_nothing_overrides() -> None:
    result = evaluate(SPEC)
    assert result.value is False
    assert (result.source, result.reason) == ("default", "declared_default")


def test_a_platform_override_beats_the_default() -> None:
    result = evaluate(SPEC, [_tenant_row(tenant_id=None, value=True)])
    assert result.value is True
    assert result.source == "platform_override"


def test_a_tenant_override_beats_a_platform_override() -> None:
    tenant = uuid4()
    result = evaluate(
        SPEC,
        [
            _tenant_row(tenant_id=None, value=True),
            _tenant_row(tenant_id=tenant, value=False),
        ],
        tenant_id=tenant,
    )
    assert result.value is False
    assert result.source == "tenant_override"


def test_a_kill_switch_outranks_every_override() -> None:
    """The ordering the whole mechanism exists for: turning a feature off at 3am
    must not require unwinding every override first."""
    tenant = uuid4()
    result = evaluate(
        SPEC,
        [
            _tenant_row(tenant_id=tenant, value=True),
            _tenant_row(tenant_id=None, rollout_percentage=100),
            _tenant_row(tenant_id=None, kill_switch=True),
        ],
        tenant_id=tenant,
    )
    assert result.value is False
    assert (result.source, result.reason) == ("kill_switch", "killed")


def test_a_kill_switch_forces_off_even_when_the_default_is_on() -> None:
    """Forced OFF, not "back to default" — a default of True would otherwise
    make the kill switch a no-op, the one thing it must never be."""
    on_by_default = FeatureFlagSpec(code="probe.on", default=True, owner="probe")
    result = evaluate(
        on_by_default,
        [FlagOverrideRecord(flag_code="probe.on", tenant_id=None, kill_switch=True)],
    )
    assert result.value is False


def test_a_tenant_scope_override_is_ignored_when_the_flag_forbids_it() -> None:
    deployment_only = FeatureFlagSpec(
        code="probe.deploy_only",
        owner="probe",
        allowed_scopes=frozenset({DEPLOYMENT_SCOPE}),
    )
    tenant = uuid4()
    result = evaluate(
        deployment_only,
        [
            FlagOverrideRecord(
                flag_code="probe.deploy_only", tenant_id=tenant, value=True
            )
        ],
        tenant_id=tenant,
    )
    assert result.value is False
    assert result.source == "default"


def test_another_tenants_override_is_never_applied() -> None:
    """The evaluator is the last place the tenant boundary can be enforced, so
    it enforces it rather than trusting the query that fetched the rows."""
    mine, theirs = uuid4(), uuid4()
    result = evaluate(SPEC, [_tenant_row(tenant_id=theirs, value=True)], tenant_id=mine)
    assert result.value is False
    assert result.source == "default"


# ── Rollouts ────────────────────────────────────────────────────────────────


def test_a_rollout_is_deterministic_for_a_subject() -> None:
    tenant = uuid4()
    rows = [_tenant_row(tenant_id=tenant, rollout_percentage=50)]
    first = evaluate(SPEC, rows, tenant_id=tenant)
    second = evaluate(SPEC, rows, tenant_id=tenant)
    assert first.value == second.value, "the same tenant must not flip between requests"


def test_a_full_rollout_includes_everyone_and_an_empty_one_excludes_everyone() -> None:
    tenant = uuid4()
    assert (
        evaluate(
            SPEC,
            [_tenant_row(tenant_id=tenant, rollout_percentage=100)],
            tenant_id=tenant,
        ).value
        is True
    )
    assert (
        evaluate(
            SPEC,
            [_tenant_row(tenant_id=tenant, rollout_percentage=0)],
            tenant_id=tenant,
        ).value
        is False
    )


def test_two_flags_do_not_select_the_same_half_of_the_fleet() -> None:
    """Salting the hash with the flag code is what keeps an A/B result from
    being an artefact of the bucketing."""
    subjects = [str(uuid4()) for _ in range(200)]
    a = {s for s in subjects if rollout_bucket("flag.a", s) < 50}
    b = {s for s in subjects if rollout_bucket("flag.b", s) < 50}
    assert a != b


def test_the_rollout_reason_distinguishes_in_from_out() -> None:
    tenant = uuid4()
    inside = evaluate(
        SPEC, [_tenant_row(tenant_id=tenant, rollout_percentage=100)], tenant_id=tenant
    )
    outside = evaluate(
        SPEC, [_tenant_row(tenant_id=tenant, rollout_percentage=0)], tenant_id=tenant
    )
    assert inside.reason == "in_rollout"
    assert outside.reason == "out_of_rollout"


# ── Explainability ──────────────────────────────────────────────────────────


def test_the_deciding_rule_is_identified() -> None:
    """ "It was on" is useless in an incident; "rule X turned it on" is not."""
    tenant, rule = uuid4(), uuid4()
    result = evaluate(
        SPEC,
        [_tenant_row(tenant_id=tenant, value=True, rule_id=rule)],
        tenant_id=tenant,
    )
    assert result.rule_id == rule


# ── Cache ───────────────────────────────────────────────────────────────────


def test_cache_keys_are_scoped_per_tenant() -> None:
    a, b = uuid4(), uuid4()
    assert evaluation_cache_key(SPEC.code, tenant_id=a, version=1) != (
        evaluation_cache_key(SPEC.code, tenant_id=b, version=1)
    )
    assert evaluation_cache_key(SPEC.code, tenant_id=a, version=1) != (
        evaluation_cache_key(SPEC.code, tenant_id=None, version=1)
    )


def test_a_version_bump_retires_the_previous_answer() -> None:
    """Invalidation is a version bump, not a delete sweep."""
    store = MemoryCache()
    tenant = uuid4()
    rows = [_tenant_row(tenant_id=tenant, value=True)]
    first = cached_evaluate(SPEC, rows, tenant_id=tenant, version=1, store=store)
    assert first.value is True
    # Overrides removed AND the version bumped — the new generation is computed.
    second = cached_evaluate(SPEC, [], tenant_id=tenant, version=2, store=store)
    assert second.value is False


def test_a_cached_answer_is_reused_within_a_version() -> None:
    store = MemoryCache()
    tenant = uuid4()
    cached_evaluate(
        SPEC,
        [_tenant_row(tenant_id=tenant, value=True)],
        tenant_id=tenant,
        version=1,
        store=store,
    )
    # Same version, different overrides: the cache is authoritative, which is
    # exactly why the version must move whenever an override does.
    again = cached_evaluate(SPEC, [], tenant_id=tenant, version=1, store=store)
    assert again.value is True


def test_one_tenants_cached_answer_never_serves_another(tenant_ids=None) -> None:
    """The leak this whole cache design exists to prevent."""
    store = MemoryCache()
    a, b = uuid4(), uuid4()
    cached_evaluate(
        SPEC,
        [_tenant_row(tenant_id=a, value=True)],
        tenant_id=a,
        version=1,
        store=store,
    )
    for_b = cached_evaluate(SPEC, [], tenant_id=b, version=1, store=store)
    assert for_b.value is False, "tenant B read tenant A's cached evaluation"


@pytest.mark.parametrize("scope", [TENANT_SCOPE, DEPLOYMENT_SCOPE])
def test_declared_scopes_are_normalised_to_a_frozenset(scope: str) -> None:
    spec = FeatureFlagSpec(code="probe.scoped", allowed_scopes={scope})
    assert isinstance(spec.allowed_scopes, frozenset)
