"""Tests for the packaged test kit `dotmac_kernel.testing` (kernel Task 5).

Proves the kit a consumer assembly depends on actually works: the harness wiring
(engine/session/TestClient), the deterministic fakes, and the parametrized
provisioning-provider contract driven against `FakeProvisioningProvider`.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.providers.provisioning import (
    ProvisioningProvider,
    ProvisioningRequest,
    ProvisioningStatus,
)
from dotmac_kernel.testing import (
    FakeClock,
    FakeProvisioningProvider,
    FakeSeeder,
    InMemoryRateLimitStore,
    assembly_test_client,
    check_provisioning_provider_contract,
    create_test_engine,
    fake_branding,
    isolated_session,
)


# ── harness ──────────────────────────────────────────────────────────────────
def test_assembly_test_client_boots_and_serves_health() -> None:
    """The kit boots a real create_app assembly and overrides its DB deps onto
    an isolated session — the same path a consumer's integration-ish unit test
    takes."""
    engine = create_test_engine()
    try:
        with isolated_session(engine) as session:
            app = create_app(ProductAssemblySpec(name="kit-test", modules=()))
            with assembly_test_client(app, session=session) as client:
                resp = client.get("/health")
                assert resp.status_code == 200
                assert resp.json() == {"status": "ok"}
            # overrides removed on exit — the app is left clean.
            assert app.dependency_overrides == {}
    finally:
        engine.dispose()


def test_isolated_session_rolls_back_between_uses() -> None:
    from dotmac_kernel.models import Tenant

    engine = create_test_engine()
    try:
        with isolated_session(engine) as session:
            session.add(Tenant(slug="acme", name="Acme"))
            session.commit()  # even a commit is rolled back by the savepoint
            assert session.query(Tenant).count() == 1
        with isolated_session(engine) as session:
            assert session.query(Tenant).count() == 0
    finally:
        engine.dispose()


# ── fakes ────────────────────────────────────────────────────────────────────
def test_fake_clock_is_deterministic_and_advanceable() -> None:
    clock = FakeClock()
    start = clock.now()
    assert start.tzinfo is UTC
    clock.advance(60)
    assert (clock.now() - start).total_seconds() == 60
    clock.set(datetime(2030, 6, 1, tzinfo=UTC))
    assert clock.now() == datetime(2030, 6, 1, tzinfo=UTC)


def test_fake_seeder_records_and_can_fail() -> None:
    ok = FakeSeeder(name="feat-a")
    ok.hook()
    ok.hook()
    assert ok.calls == ["feat-a", "feat-a"]

    boom = FakeSeeder(name="feat-b", fail=True)
    with pytest.raises(RuntimeError):
        boom.hook()


def test_fake_branding_defaults_and_overrides() -> None:
    assert fake_branding()["name"] == "Test Brand"
    assert fake_branding(name="Acme")["name"] == "Acme"


def test_in_memory_rate_limit_store_is_the_shipped_store() -> None:
    from dotmac_kernel.middleware.rate_limit import MemoryStore

    assert InMemoryRateLimitStore is MemoryStore


# ── provisioning ─────────────────────────────────────────────────────────────
def test_fake_provisioning_provider_satisfies_the_contract() -> None:
    """The reusable contract, run against the fake — the same call a consumer
    makes against THEIR provider factory to prove protocol conformance."""
    check_provisioning_provider_contract(FakeProvisioningProvider)


class _NonIdempotentProvider(FakeProvisioningProvider):
    """Structurally a valid provider, semantically broken in the realistic way.

    It satisfies the Protocol (all four methods, right shapes) but mints a fresh
    `operation_id` on every `apply`, so re-applying is a NEW operation rather
    than a no-op returning the prior result. That is the mistake a real provider
    actually makes — the protocol's hardest requirement is idempotency, not
    method presence — and a contract that only checked `isinstance` would wave
    it through.
    """

    def apply(self, request):  # type: ignore[no-untyped-def]
        self._counter = getattr(self, "_counter", 0) + 1
        fresh = ProvisioningRequest(
            intent_id=request.intent_id,
            spec=request.spec,
            operation_id=f"op-{self._counter}",
        )
        return super().apply(fresh)


def test_the_contract_rejects_a_broken_provider() -> None:
    """Canary: the contract must FAIL something. A conformance suite that has
    never been shown to reject anything is decoration — it is indistinguishable
    from one whose checks stopped running."""
    with pytest.raises(AssertionError, match="operation_id"):
        check_provisioning_provider_contract(_NonIdempotentProvider)


def test_the_contract_still_rejects_a_broken_provider_under_O() -> None:
    """The reason the contract stopped using bare `assert` (ADR-0018 work).

    `python -O` strips every `assert` statement. While the suite was written
    with asserts, a consumer running an optimised interpreter got a clean return
    from a contract that had checked nothing — a green conformance signal for an
    unverified provider. This runs the real suite in a real `-O` subprocess and
    requires the rejection to survive.
    """
    source = (
        "from dotmac_kernel.testing import check_provisioning_provider_contract\n"
        "from tests.unit.test_testing_kit import _NonIdempotentProvider\n"
        "try:\n"
        "    check_provisioning_provider_contract(_NonIdempotentProvider)\n"
        "except AssertionError:\n"
        "    print('REJECTED')\n"
        "else:\n"
        "    print('PASSED-SILENTLY')\n"
    )
    result = subprocess.run(  # noqa: S603 # nosec B603
        [sys.executable, "-O", "-c", source],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stderr
    assert "REJECTED" in result.stdout, (
        "the provider contract evaporated under `python -O` — its checks must "
        f"be explicit raises, not asserts. stdout={result.stdout!r}"
    )


def test_fake_provisioning_records_calls_and_conforms_to_protocol() -> None:
    provider = FakeProvisioningProvider()
    assert isinstance(provider, ProvisioningProvider)
    req = ProvisioningRequest(intent_id="i-9", spec={"n": 1})
    provider.plan(req)
    op = provider.apply(req).operation_id
    provider.observe(op)
    provider.cancel(op)
    assert [c[0] for c in provider.calls] == ["plan", "apply", "observe", "cancel"]


def test_fake_provisioning_partial_then_resume() -> None:
    provider = FakeProvisioningProvider(partial_first_apply=True)
    req = ProvisioningRequest(intent_id="i-1", spec={"n": 1})
    first = provider.apply(req)
    assert first.status is ProvisioningStatus.PARTIAL
    resume = ProvisioningRequest(
        intent_id="i-1", spec={"n": 1}, operation_id=first.operation_id
    )
    assert provider.apply(resume).status is ProvisioningStatus.SUCCEEDED


def test_fake_provisioning_cancel_is_terminal_and_idempotent() -> None:
    provider = FakeProvisioningProvider()
    req = ProvisioningRequest(intent_id="i-1", spec={"n": 1})
    op = provider.apply(req).operation_id
    assert provider.cancel(op).status is ProvisioningStatus.CANCELLED
    # A re-apply after cancel is a no-op that stays CANCELLED (terminal).
    assert provider.apply(req).status is ProvisioningStatus.CANCELLED
