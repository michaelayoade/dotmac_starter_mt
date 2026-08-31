from __future__ import annotations

from dataclasses import replace

import pytest
from dotmac_runner_transport import (
    AdapterIdentityV1,
    EvidenceStatus,
    ExactHost,
    HostDirectEgressGrantV1,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    LifecycleEvidenceV1,
    ResolvedRunnerTransportPolicyV1,
    RunnerTransportCapability,
    RunnerTransportReceiptV1,
    RunnerTransportRequirementsV1,
    TransportEndpointV1,
    WorkloadEgressPolicyV1,
    render_host_bundle,
)
from dotmac_runner_transport.receipt import REQUIRED_ITEMS


def _item(name: str, status: EvidenceStatus = EvidenceStatus.EXECUTED_PASSED):
    return LifecycleEvidenceV1(name, status, "sha256:" + "a" * 64)


def _bundle(*direct_grants: HostDirectEgressGrantV1):
    endpoint = TransportEndpointV1(
        RunnerTransportCapability.CONTROL, ExactHost("transport.invalid")
    )
    policy = ResolvedRunnerTransportPolicyV1(
        schema="RunnerEgressPolicy.v1",
        requirements=RunnerTransportRequirementsV1(
            (RunnerTransportCapability.CONTROL,)
        ),
        adapter=AdapterIdentityV1("fake-provider", "1", "sha256:" + "c" * 64),
        snapshot_digest="sha256:" + "d" * 64,
        endpoints=(endpoint,),
    )
    return render_host_bundle(
        policy,
        (HostRunnerIdentityV1("runner-one", 2001, 3128, 3129),),
        HostProxyIdentityV1("squid", 2010),
        HostNftablesBindingV1(
            "inet",
            "dotmac_egress",
            "output",
            ("ip daddr @mgmt_v4 accept", 'oifname "lo" accept'),
        ),
        (WorkloadEgressPolicyV1("runner-one", (ExactHost("work.invalid"),)),),
        tuple(direct_grants),
    )


def _receipt(bundle, *items: LifecycleEvidenceV1) -> RunnerTransportReceiptV1:
    environment = bundle.runner_environments[0]
    return RunnerTransportReceiptV1(
        schema="RunnerTransportReceipt.v1",
        source_revision="a" * 40,
        repository="owner/repository",
        runner_name="runner-one",
        required_labels=("runner-one", "self-hosted"),
        specification_digest=bundle.policy_digest,
        authorized_plan_digest=bundle.policy_digest,
        execution_policy_digest=bundle.policy_digest,
        adapter_key="fake-provider",
        adapter_version="1",
        adapter_declaration_digest="sha256:" + "c" * 64,
        binding_digest=bundle.binding_digest,
        rendered_squid_digest=bundle.squid_sha256,
        rendered_nftables_digest=bundle.nftables_sha256,
        runner_environment_digest=environment.sha256,
        workload_environment_digest=environment.workload_sha256,
        items=items,
    )


def test_all_executed_rows_accept() -> None:
    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    receipt.assert_accepted(bundle)
    assert receipt.digest.startswith("sha256:")


@pytest.mark.parametrize(
    "status",
    [
        status
        for status in EvidenceStatus
        if status is not EvidenceStatus.EXECUTED_PASSED
    ],
)
def test_every_nonpassing_status_refuses_independently(status: EvidenceStatus) -> None:
    bundle = _bundle()
    items = [_item(name) for name in REQUIRED_ITEMS]
    items[6] = _item("result_uploaded", status)
    with pytest.raises(ValueError, match="result_uploaded"):
        _receipt(bundle, *items).assert_accepted(bundle)


def test_zero_exit_shape_with_no_provider_completion_refuses() -> None:
    bundle = _bundle()
    items = [_item(name) for name in REQUIRED_ITEMS]
    items[7] = _item("provider_recorded_success", EvidenceStatus.NOT_EXECUTED)
    with pytest.raises(ValueError, match="provider_recorded_success"):
        _receipt(bundle, *items).assert_accepted(bundle)


def test_digest_mismatch_refuses() -> None:
    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    object.__setattr__(receipt, "execution_policy_digest", "sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="digests differ"):
        receipt.assert_accepted(bundle)


def test_missing_duplicate_or_unknown_rows_refuse() -> None:
    bundle = _bundle()
    items = [_item(name) for name in REQUIRED_ITEMS]
    items[-1] = _item("provider_selected")
    with pytest.raises(ValueError, match="missing, duplicated or unknown"):
        _receipt(bundle, *items).assert_accepted(bundle)


def test_binding_digest_drift_refuses() -> None:
    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    object.__setattr__(receipt, "binding_digest", "sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="receipt binding differs"):
        receipt.assert_accepted(bundle)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "RunnerTransportReceipt.v2", "schema must be v1"),
        ("source_revision", "A" * 40, "lowercase Git SHA"),
        ("repository", "", "repository cannot be empty"),
        ("runner_name", "", "runner name cannot be empty"),
        ("adapter_key", "", "adapter key cannot be empty"),
        ("adapter_version", "", "adapter version cannot be empty"),
        ("required_labels", ("self-hosted", "runner-one"), "unique and sorted"),
        ("binding_digest", "f" * 64, "binding digest must be typed"),
    ],
)
def test_malformed_receipt_coordinates_refuse(field, value, message) -> None:
    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    with pytest.raises(ValueError, match=message):
        replace(receipt, **{field: value})


def test_no_proxy_and_both_environment_digests_are_load_bearing() -> None:
    original = _bundle()
    changed = _bundle(
        HostDirectEgressGrantV1("runner-one", "100.64.53.1/32", 8200, "tcp", "wg0")
    )
    original_environment = original.runner_environments[0]
    changed_environment = changed.runner_environments[0]
    assert "NO_PROXY=100.64.53.1" in changed_environment.content
    assert "NO_PROXY=100.64.53.1" in (changed_environment.workload_content or "")

    receipt = _receipt(original, *(_item(name) for name in REQUIRED_ITEMS))
    object.__setattr__(receipt, "binding_digest", changed.binding_digest)
    object.__setattr__(receipt, "rendered_squid_digest", changed.squid_sha256)
    object.__setattr__(receipt, "rendered_nftables_digest", changed.nftables_sha256)
    with pytest.raises(ValueError, match="runner environment differs"):
        receipt.assert_accepted(changed)

    object.__setattr__(receipt, "runner_environment_digest", changed_environment.sha256)
    assert receipt.workload_environment_digest == original_environment.workload_sha256
    with pytest.raises(ValueError, match="workload environment differs"):
        receipt.assert_accepted(changed)


def test_acceptance_revalidates_bundle_content_instead_of_trusting_digest_fields() -> (
    None
):
    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    object.__setattr__(bundle.binding, "schema", "HostRunnerTransportSpec.v2")
    with pytest.raises(ValueError, match="host runner transport spec schema"):
        receipt.assert_accepted(bundle)

    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    object.__setattr__(bundle, "squid_conf", bundle.squid_conf + "# stale bytes\n")
    with pytest.raises(ValueError, match="Squid digest differs"):
        receipt.assert_accepted(bundle)

    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    environment = bundle.runner_environments[0]
    object.__setattr__(
        environment, "content", environment.content + "NO_PROXY=203.0.113.9\n"
    )
    with pytest.raises(ValueError, match="runner environment digest differs"):
        receipt.assert_accepted(bundle)

    bundle = _bundle()
    receipt = _receipt(bundle, *(_item(name) for name in REQUIRED_ITEMS))
    environment = bundle.runner_environments[0]
    object.__setattr__(
        environment,
        "workload_content",
        (environment.workload_content or "") + "# stale bytes\n",
    )
    with pytest.raises(ValueError, match="workload environment digest differs"):
        receipt.assert_accepted(bundle)
