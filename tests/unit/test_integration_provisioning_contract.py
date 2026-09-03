"""SPI 1.5 provisioning contract — provider-neutral and persistence-free."""

from __future__ import annotations

import dataclasses

import pytest
from dotmac_integration.conformance import fake_manifest, fake_plugin
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    MODE_PROTOCOLS,
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionPlanResult,
    ProvisionPlugin,
    ProvisionResultStatus,
    ProvisionStep,
    ProvisioningHandler,
    ProvisioningResult,
    SpiVersion,
    verify_plugin_modes,
)


def test_spi_1_5_adds_one_closed_provision_mode() -> None:
    assert CURRENT_SPI_VERSION == SpiVersion(1, 5)
    assert set(ConnectorMode) == {
        ConnectorMode.INGRESS,
        ConnectorMode.POLL,
        ConnectorMode.DELIVERY,
        ConnectorMode.PROVISION,
    }
    contract = MODE_PROTOCOLS[ConnectorMode.PROVISION]
    assert contract.plugin_protocol is ProvisionPlugin
    assert contract.factory == "provisioning_handler_for"
    assert contract.handler_protocol is ProvisioningHandler


def test_provider_free_fake_exercises_all_provision_operations() -> None:
    plugin = fake_plugin()
    handler = plugin.provisioning_handler_for(plugin.manifest.capabilities[0].capability_id)
    step = ProvisionStep(
        step_key="mailbox",
        endpoint_code="mailbox.apply",
        depends_on=(),
        input={"mailbox_ref": "mbx-1"},
    )
    plan = ProvisionPlanRequest(
        capability_id=plugin.manifest.capabilities[0].capability_id,
        command_id="cmd-1",
        plan_hash="a" * 64,
        steps=(step,),
        config={"region": "ng"},
        secrets={"api": "material"},
    )
    assert handler.plan(plan) == ProvisionPlanResult(
        plan_hash=plan.plan_hash,
        steps=plan.steps,
    )

    apply = ProvisionApplyRequest(
        capability_id=plan.capability_id,
        command_id=plan.command_id,
        operation_ref="op-1",
        plan_hash=plan.plan_hash,
        step=step,
        config=plan.config,
        secrets=plan.secrets,
        idempotency_key="idem-1",
    )
    observe = ProvisionObserveRequest(
        capability_id=plan.capability_id,
        command_id=plan.command_id,
        operation_ref="op-1",
        plan_hash=plan.plan_hash,
        step_key=step.step_key,
        provider_operation_ref="provider-op-1",
        target={"mailbox_ref": "mbx-1"},
        config=plan.config,
        secrets=plan.secrets,
    )
    cancel = ProvisionCancelRequest(
        capability_id=plan.capability_id,
        command_id=plan.command_id,
        operation_ref="op-1",
        plan_hash=plan.plan_hash,
        step_key=step.step_key,
        provider_operation_ref="provider-op-1",
        target={"mailbox_ref": "mbx-1"},
        reason="owner withdrew intent",
        idempotency_key="idem-cancel-1",
        config=plan.config,
        secrets=plan.secrets,
    )
    expected = ProvisioningResult(status=ProvisionResultStatus.SUCCEEDED)
    assert handler.apply(apply) == expected
    assert handler.observe(observe) == expected
    assert handler.cancel(cancel) == expected
    assert plugin.provision_requests_seen == [plan, apply, observe, cancel]


def test_provision_request_material_is_hidden_and_immutable() -> None:
    supplied = {"token": "material"}
    request = ProvisionPlanRequest(
        capability_id="managed.resource.v1",
        command_id="cmd-1",
        plan_hash="a" * 64,
        steps=(),
        secrets=supplied,
    )
    supplied["token"] = "changed"
    assert request.secrets["token"] == "material"
    assert "material" not in repr(request)
    with pytest.raises(TypeError):
        request.secrets["token"] = "changed"  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.command_id = "changed"  # type: ignore[misc]


def test_default_fake_really_conforms_to_the_new_mode() -> None:
    plugin = fake_plugin(manifest_=fake_manifest())
    verify_plugin_modes(plugin)
    assert isinstance(plugin, ProvisionPlugin)

