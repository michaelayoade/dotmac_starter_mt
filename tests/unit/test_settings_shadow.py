"""The shadow harness: what it serves, what it records, and what it never says.

The behaviours worth pinning are about SAFETY, not comparison: a shadow phase
must not change what a request is served until the phase changes, must not
crash a request when an adapter is wrong, and must never put a settings value
into a report that ends up in an issue tracker.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel import settings_shadow as sh
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain


@pytest.fixture
def spec():
    """A registered spec, removed again so it cannot leak into other modules."""
    key = f"shadow_{uuid4().hex[:8]}"
    declared: sr.SettingSpec[str] = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=key,
        value_type=SettingValueType.string,
        default="kernel-default",
    )
    sr.register_specs([declared])
    yield declared
    sr._REGISTRY.pop((SettingDomain.auth, key), None)


def test_agreement_reports_nothing(db, spec):
    assert sh.compare_one(db, spec.domain, spec.key, lambda: "kernel-default") is None


def test_disagreement_is_reported(db, spec):
    divergence = sh.compare_one(db, spec.domain, spec.key, lambda: "something-else")
    assert divergence is not None
    assert divergence.key == spec.key
    assert divergence.error is None


def test_a_divergence_never_carries_either_value(db, spec):
    """A settings table holds credentials, and a divergence report is exactly
    the artefact that gets pasted into a ticket."""
    secret = "hunter2-the-actual-password"
    divergence = sh.compare_one(db, spec.domain, spec.key, lambda: secret)
    assert divergence is not None
    rendered = divergence.describe() + repr(divergence)
    assert secret not in rendered
    assert "kernel-default" not in rendered
    # Types ARE reported: `int` vs `str` is the common real cause.
    assert "str" in rendered


def test_a_raising_legacy_reader_is_recorded_not_raised(db, spec):
    """An adapter bug during a shadow phase must not become a 500."""

    def _broken():
        raise RuntimeError("legacy resolver exploded")

    divergence = sh.compare_one(db, spec.domain, spec.key, _broken)
    assert divergence is not None
    assert divergence.error is not None
    assert "RuntimeError" in divergence.error
    assert "exploded" not in divergence.error  # message could quote a value


def test_legacy_authoritative_serves_legacy_even_when_they_disagree(db, spec):
    """The load-bearing property of phase 1: comparing must not change what a
    request gets. Otherwise 'shadow' is just an unverified cutover."""
    served = sh.resolve_shadowed(
        db, spec, lambda: "legacy-wins", phase=sh.ShadowPhase.LEGACY_AUTHORITATIVE
    )
    assert served == "legacy-wins"


def test_kernel_authoritative_serves_the_kernel_but_still_compares(db, spec, caplog):
    """Phase 2 keeps the legacy reader running so a regression is visible
    immediately and the phase can be stepped back without a deploy."""
    calls = []

    def _legacy():
        calls.append(1)
        return "legacy-value"

    with caplog.at_level(logging.WARNING, logger=sh.__name__):
        served = sh.resolve_shadowed(
            db, spec, _legacy, phase=sh.ShadowPhase.KERNEL_AUTHORITATIVE
        )
    assert served == "kernel-default"
    assert calls == [1], "phase 2 must still call the legacy reader"
    assert "divergence" in caplog.text.lower()


def test_kernel_only_never_calls_the_legacy_reader(db, spec):
    """Phase 3 is what makes deleting the old resolver safe."""

    def _must_not_run():
        raise AssertionError("legacy reader called during KERNEL_ONLY")

    assert (
        sh.resolve_shadowed(db, spec, _must_not_run, phase=sh.ShadowPhase.KERNEL_ONLY)
        == "kernel-default"
    )


def test_decimal_and_int_are_not_treated_as_drift(db, spec):
    """A report that flags representation differences trains people to ignore
    it, which is worse than not having one."""
    assert sh._values_agree(5, Decimal("5")) is True
    assert sh._values_agree(Decimal("5.0"), 5) is True


def test_a_bool_read_as_an_int_IS_drift():
    """`True == 1` in Python, so the naive comparison would pass a real
    disagreement about a boolean setting."""
    assert sh._values_agree(True, 1) is False
    assert sh._values_agree(0, False) is False


def test_none_only_agrees_with_none():
    assert sh._values_agree(None, None) is True
    assert sh._values_agree(None, "") is False
    assert sh._values_agree("", None) is False


def test_an_uncomparable_pair_is_drift_not_an_exception():
    class _Hostile:
        def __eq__(self, other):
            raise RuntimeError("no")

    assert sh._values_agree(_Hostile(), 1) is False


def test_sweep_reports_clean_when_everything_agrees(db, spec):
    report = sh.sweep(db, lambda domain, key: sr.resolve_value(db, domain, key))
    assert report.clean, report.describe()
    assert report.compared > 0


def test_sweep_is_not_vacuous(db, spec):
    """A sweep that compares nothing must not read as success."""
    empty = sh.sweep(db, lambda domain, key: None, specs=[])
    assert empty.compared == 0
    assert empty.clean, "an empty sweep is clean but proves nothing"
    real = sh.sweep(db, lambda domain, key: "wrong", specs=[spec])
    assert real.compared == 1
    assert not real.clean


def test_sweep_describe_names_the_setting_but_not_the_values(db, spec):
    report = sh.sweep(db, lambda domain, key: "leaked-secret-value", specs=[spec])
    described = report.describe()
    assert spec.key in described
    assert "leaked-secret-value" not in described


def test_sweep_scopes_merges_and_platform_alone_would_have_missed_it(db, spec):
    """Platform agreement proves nothing about tenant overrides, which is where
    a precedence difference between two resolvers actually shows up."""
    tenant_id = uuid4()
    sr.upsert_by_key(
        db,
        spec.domain,
        spec.key,
        "tenant-override",
        scope=SettingScope.tenant(tenant_id),
    )
    db.flush()

    def _legacy(domain, key):
        # A legacy resolver that knows nothing about tenant overrides.
        return "kernel-default"

    platform_only = sh.sweep(db, _legacy, scope=SettingScope.platform(), specs=[spec])
    assert platform_only.clean, "platform agrees — and that is the trap"

    both = sh.sweep_scopes(
        db,
        _legacy,
        [SettingScope.platform(), SettingScope.tenant(tenant_id)],
    )
    assert not both.clean
    assert both.compared > platform_only.compared
