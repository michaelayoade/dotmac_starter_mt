"""Provider-neutral runner transport contract, rendering and receipt canaries."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "packages/dotmac-kernel/src"))
sys.path.insert(0, str(ROOT / "packages/dotmac-runner-transport/src"))

from dotmac_runner_transport import (  # noqa: E402
    EvidenceStatus,
    ExactHost,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    LifecycleEvidenceV1,
    ProviderDomainSnapshotV1,
    RunnerTransportAdapterManifest,
    RunnerTransportCapability,
    RunnerTransportReceiptV1,
    RunnerTransportRequirementsV1,
    TransportEndpointV1,
    TransportPolicyRefused,
    WorkloadEgressPolicyV1,
    canonical_bytes,
    derive_transport_policy,
    render_host_bundle,
    typed_sha256,
)
from dotmac_runner_transport.receipt import REQUIRED_ITEMS  # noqa: E402


@dataclass(frozen=True)
class FakeAdapter:
    manifest: RunnerTransportAdapterManifest


def _adapter(
    *pairs: tuple[RunnerTransportCapability, str], key: str = "fake-provider"
) -> FakeAdapter:
    endpoints = tuple(
        sorted(
            TransportEndpointV1(capability, ExactHost(host))
            for capability, host in pairs
        )
    )
    domains = tuple(sorted({endpoint.host for endpoint in endpoints}))
    snapshot = ProviderDomainSnapshotV1(
        source_uri="https://provider.invalid/meta",
        observed_at="2026-08-31T00:00:00Z",
        semantic_sha256=typed_sha256(
            canonical_bytes(tuple(item.value for item in domains))
        ),
        field="domains.exact",
        domains=domains,
    )
    return FakeAdapter(
        RunnerTransportAdapterManifest(
            key=key,
            version="1",
            capabilities=tuple(
                sorted({endpoint.capability for endpoint in endpoints}, key=str)
            ),
            endpoints=endpoints,
            snapshot=snapshot,
        )
    )


def _requirements(*values: RunnerTransportCapability) -> RunnerTransportRequirementsV1:
    return RunnerTransportRequirementsV1(tuple(sorted(values, key=str)))


def test_policy_is_deterministic_and_a_provider_change_moves_its_digest() -> None:
    pairs = (
        (RunnerTransportCapability.CONTROL, "control.invalid"),
        (RunnerTransportCapability.RESULTS, "results.invalid"),
    )
    requirements = _requirements(
        RunnerTransportCapability.CONTROL, RunnerTransportCapability.RESULTS
    )
    first = derive_transport_policy(requirements, _adapter(*pairs))
    second = derive_transport_policy(requirements, _adapter(*reversed(pairs)))
    changed = derive_transport_policy(
        requirements,
        _adapter((RunnerTransportCapability.CONTROL, "other.invalid"), pairs[1]),
    )
    assert first.canonical_bytes == second.canonical_bytes
    assert first.digest == second.digest
    assert changed.digest != first.digest


def test_omitted_policy_refuses_but_explicit_deny_all_is_valid() -> None:
    with pytest.raises(ValueError, match="omitted transport"):
        RunnerTransportRequirementsV1(())
    policy = derive_transport_policy(
        RunnerTransportRequirementsV1.deny_all(),
        _adapter((RunnerTransportCapability.CONTROL, "control.invalid")),
    )
    assert policy.endpoints == ()


@pytest.mark.parametrize(
    "host",
    ["*.example.com", "example.com.", "127.0.0.1", "https://example.com", "LOCALHOST"],
)
def test_wildcard_url_ip_and_noncanonical_hosts_refuse(host: str) -> None:
    with pytest.raises(ValueError):
        ExactHost(host)


def test_missing_capability_and_unclassified_snapshot_domain_refuse() -> None:
    adapter = _adapter((RunnerTransportCapability.CONTROL, "control.invalid"))
    with pytest.raises(TransportPolicyRefused, match="does not implement"):
        derive_transport_policy(
            _requirements(RunnerTransportCapability.RESULTS), adapter
        )
    with pytest.raises(ValueError, match="classify every snapshot"):
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1",
            capabilities=(RunnerTransportCapability.CONTROL,),
            endpoints=adapter.manifest.endpoints,
            snapshot=ProviderDomainSnapshotV1(
                source_uri="https://provider.invalid/meta",
                observed_at="2026-08-31T00:00:00Z",
                semantic_sha256="sha256:" + "2" * 64,
                field="domains.exact",
                domains=(ExactHost("control.invalid"), ExactHost("forgotten.invalid")),
            ),
        )


def test_adapter_exclusions_are_explicit_disjoint_and_digest_bound() -> None:
    endpoint = TransportEndpointV1(
        RunnerTransportCapability.CONTROL, ExactHost("control.invalid")
    )
    snapshot = ProviderDomainSnapshotV1(
        source_uri="https://provider.invalid/meta",
        observed_at="2026-08-31T00:00:00Z",
        semantic_sha256="sha256:" + "3" * 64,
        field="domains.exact",
        domains=(ExactHost("control.invalid"), ExactHost("excluded.invalid")),
    )
    manifest = RunnerTransportAdapterManifest(
        key="fake-provider",
        version="1",
        capabilities=(RunnerTransportCapability.CONTROL,),
        endpoints=(endpoint,),
        snapshot=snapshot,
        excluded_snapshot_domains=(ExactHost("excluded.invalid"),),
    )
    assert manifest.excluded_snapshot_domains == (ExactHost("excluded.invalid"),)
    with pytest.raises(ValueError, match="both implement and exclude"):
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1",
            capabilities=(RunnerTransportCapability.CONTROL,),
            endpoints=(endpoint,),
            snapshot=ProviderDomainSnapshotV1(
                source_uri="https://provider.invalid/meta",
                observed_at="2026-08-31T00:00:00Z",
                semantic_sha256="sha256:" + "4" * 64,
                field="domains.exact",
                domains=(ExactHost("control.invalid"),),
            ),
            excluded_snapshot_domains=(ExactHost("control.invalid"),),
        )


def test_adapter_disposition_change_moves_declaration_digest() -> None:
    """The same snapshot with one host excluded is a different declaration."""

    from dotmac_runner_transport_github_actions import ADAPTER

    manifest = ADAPTER.manifest
    moved = ExactHost("api.github.com")
    changed = replace(
        manifest,
        endpoints=tuple(item for item in manifest.endpoints if item.host != moved),
        excluded_snapshot_domains=tuple(
            sorted((*manifest.excluded_snapshot_domains, moved))
        ),
    )

    assert changed.snapshot == manifest.snapshot
    assert changed.identity.declaration_digest != manifest.identity.declaration_digest


def test_renderer_separates_transport_and_workload_and_refuses_direct_web() -> None:
    policy = derive_transport_policy(
        _requirements(RunnerTransportCapability.CONTROL),
        _adapter((RunnerTransportCapability.CONTROL, "transport.invalid")),
    )
    bundle = render_host_bundle(
        policy,
        (HostRunnerIdentityV1("starter", 2001, 3128, 3129),),
        HostProxyIdentityV1("squid", 2010),
        HostNftablesBindingV1(
            "inet",
            "dotmac_egress",
            "output",
            ("ip daddr @mgmt_v4 accept", 'oifname "lo" accept'),
        ),
        (WorkloadEgressPolicyV1("starter", (ExactHost("registry.invalid"),)),),
    )
    assert "transport.invalid" in bundle.squid_conf
    assert "registry.invalid" in bundle.squid_conf
    assert (
        'meta skuid 2001 counter comment "runner starter direct egress refused" '
        "reject" in bundle.nftables_conf
    )
    assert "meta skuid 2010 meta nfproto ipv4 tcp dport 443 accept" in (
        bundle.nftables_conf
    )
    assert (
        "meta skuid 2010 meta nfproto ipv6 counter comment "
        '"runner proxy public IPv6 refused" reject' in bundle.nftables_conf
    )
    assert "flush" not in bundle.nftables_conf


def _receipt(status: EvidenceStatus = EvidenceStatus.EXECUTED_PASSED):
    policy = derive_transport_policy(
        _requirements(RunnerTransportCapability.CONTROL),
        _adapter((RunnerTransportCapability.CONTROL, "transport.invalid")),
    )
    bundle = render_host_bundle(
        policy,
        (HostRunnerIdentityV1("runner-one", 2001, 3128),),
        HostProxyIdentityV1("squid", 2010),
        HostNftablesBindingV1(
            "inet",
            "dotmac_egress",
            "output",
            ("ip daddr @mgmt_v4 accept", 'oifname "lo" accept'),
        ),
    )
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
        items=tuple(
            LifecycleEvidenceV1(
                name,
                status if name == "result_uploaded" else EvidenceStatus.EXECUTED_PASSED,
                "sha256:" + "f" * 64,
            )
            for name in REQUIRED_ITEMS
        ),
    ), bundle


def test_receipt_requires_result_upload_not_only_local_exit_zero() -> None:
    accepted, bundle = _receipt()
    accepted.assert_accepted(bundle)
    refused, refused_bundle = _receipt(EvidenceStatus.NOT_EXECUTED)
    with pytest.raises(ValueError, match="result_uploaded"):
        refused.assert_accepted(refused_bundle)
