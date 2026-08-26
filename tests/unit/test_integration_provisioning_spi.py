"""SPI 1.2 provisioning-mode contract and sensitivity proofs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_integration.conformance import (
    FAKE_CAPABILITY,
    ConformanceFailure,
    assert_plugin_conforms,
    fake_plugin,
)
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    MODE_PROTOCOLS,
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningHandler,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
    SpiVersion,
    verify_plugin_modes,
)


def _step() -> ProvisionStep:
    return ProvisionStep(
        step_key="identity-client",
        endpoint_code=FAKE_CAPABILITY,
        depends_on=(),
        input={"desired_ref": "deployment:one"},
    )


def test_spi_1_2_closes_provision_mode_with_one_handler_contract() -> None:
    assert CURRENT_SPI_VERSION == SpiVersion(1, 2)
    assert set(MODE_PROTOCOLS) == set(ConnectorMode)
    contract = MODE_PROTOCOLS[ConnectorMode.PROVISION]
    assert contract.factory == "provisioning_handler_for"
    assert contract.handler_protocol is ProvisioningHandler


def test_conformance_fake_runs_all_four_typed_provisioning_operations() -> None:
    plugin = fake_plugin()
    assert_plugin_conforms(plugin)
    assert ConnectorMode.PROVISION in plugin.modes
    handler = plugin.provisioning_handler_for(FAKE_CAPABILITY)
    step = _step()
    plan = handler.plan(
        ProvisionPlanRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="plan-command-1",
            plan_hash="sha256:" + "1" * 64,
            steps=(step,),
            config={"endpoint": "https://service.example"},
            secrets={},
        )
    )
    assert plan.plan_hash == "sha256:" + "1" * 64
    assert plan.steps == (step,)

    applied = handler.apply(
        ProvisionApplyRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="command-1",
            operation_ref="operation-1",
            plan_hash=plan.plan_hash,
            step=step,
            config={},
            secrets={},
            idempotency_key="command-1/identity-client/1",
        )
    )
    assert applied.status is ProvisionResultStatus.SUCCEEDED

    observed = handler.observe(
        ProvisionObserveRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="observe-command-1",
            operation_ref="operation-1",
            plan_hash=plan.plan_hash,
            step_key=step.step_key,
            provider_operation_ref="provider-operation-1",
            target={"desired_ref": "deployment:one"},
            config={},
            secrets={},
        )
    )
    assert observed.status is ProvisionResultStatus.SUCCEEDED

    cancelled = handler.cancel(
        ProvisionCancelRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="cancel-command-1",
            operation_ref="operation-1",
            plan_hash=plan.plan_hash,
            step_key=step.step_key,
            provider_operation_ref="provider-operation-1",
            target={"desired_ref": "deployment:one"},
            reason="operator-requested",
            idempotency_key="cancel-command-1/identity-client",
            config={},
            secrets={},
        )
    )
    assert cancelled.status is ProvisionResultStatus.CANCELLED
    assert [type(request).__name__ for request in plugin.provisioning_seen] == [
        "ProvisionPlanRequest",
        "ProvisionApplyRequest",
        "ProvisionObserveRequest",
        "ProvisionCancelRequest",
    ]


def test_provision_requests_never_render_materialized_secrets() -> None:
    sentinel = "SENTINEL-PROVISIONING-SECRET-781a"
    request = ProvisionApplyRequest(
        capability_id=FAKE_CAPABILITY,
        command_id="command-1",
        operation_ref="operation-1",
        plan_hash="sha256:" + "2" * 64,
        step=_step(),
        config={},
        secrets={"credential": sentinel},
        idempotency_key="command-1/identity-client/1",
    )
    assert sentinel not in repr(request)
    assert sentinel not in repr(
        ProvisionPlanRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="plan-command-1",
            plan_hash="sha256:" + "2" * 64,
            steps=(_step(),),
            config={},
            secrets={"credential": sentinel},
        )
    )
    assert sentinel not in repr(
        ProvisionObserveRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="observe-command-1",
            operation_ref="operation-1",
            plan_hash="sha256:" + "2" * 64,
            step_key="identity-client",
            provider_operation_ref="provider-operation-1",
            target={"desired_ref": "deployment:one"},
            config={},
            secrets={"credential": sentinel},
        )
    )
    assert sentinel not in repr(
        ProvisionCancelRequest(
            capability_id=FAKE_CAPABILITY,
            command_id="cancel-command-1",
            operation_ref="operation-1",
            plan_hash="sha256:" + "2" * 64,
            step_key="identity-client",
            provider_operation_ref="provider-operation-1",
            target={"desired_ref": "deployment:one"},
            reason="customer_approved",
            idempotency_key="cancel-command-1/identity-client",
            config={},
            secrets={"credential": sentinel},
        )
    )


def test_a_declared_provision_mode_without_all_four_methods_is_refused() -> None:
    """Sensitivity: checking only for a factory would certify this handler."""
    from dotmac_integration.conformance import FakePlugin

    class _IncompleteProvisionHandler:
        def apply(self, request: ProvisionApplyRequest):
            raise AssertionError("must not execute during discovery")

    class _IncompletePlugin(FakePlugin):
        def provisioning_handler_for(self, capability_id: str):
            self.manifest.require_declares(capability_id)
            return _IncompleteProvisionHandler()

    plugin = _IncompletePlugin()
    with pytest.raises(Exception, match="ProvisioningHandler"):
        verify_plugin_modes(plugin)
    with pytest.raises(ConformanceFailure, match="ProvisioningHandler"):
        assert_plugin_conforms(plugin)


def test_provisioning_result_status_is_closed() -> None:
    assert {status.value for status in ProvisionResultStatus} == {
        "succeeded",
        "accepted",
        "pending",
        "retryable",
        "terminal",
        "ambiguous",
        "cancelled",
        "not_found",
    }
    with pytest.raises(ValueError):
        ProvisionResultStatus("provider-specific")


def test_provision_result_evidence_is_immutable_and_time_is_explicit() -> None:
    from dotmac_integration.spi import ProvisioningResult

    result = ProvisioningResult(
        status=ProvisionResultStatus.ACCEPTED,
        provider_operation_ref="provider-operation-1",
        evidence={"accepted_at": datetime(2026, 8, 17, tzinfo=UTC).isoformat()},
    )
    with pytest.raises(TypeError):
        result.evidence["changed"] = True  # type: ignore[index]
