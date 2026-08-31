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
    EvidenceFieldV1,
    EvidenceStatus,
    ExactHost,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    LifecycleEvidenceDocumentV1,
    LifecycleEvidenceV1,
    ProviderDomainSnapshotV1,
    ProviderRunnerIdentityV1,
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

SOURCE_REVISION = "a" * 40


def _retained(
    documents: tuple[LifecycleEvidenceDocumentV1, ...],
) -> tuple[bytes, ...]:
    return tuple(document.canonical_bytes for document in documents)


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
    identity = ProviderRunnerIdentityV1(
        logical_runner_name="runner-one",
        provider_runner_name="provider-runner-one",
        repository="owner/repository",
        required_labels=("runner-one", "self-hosted"),
    )
    documents = tuple(
        LifecycleEvidenceDocumentV1(
            schema="RunnerTransportLifecycleEvidence.v1",
            item=name,
            status=EvidenceStatus.EXECUTED_PASSED,
            mutated=False,
            source="provider-control-plane",
            observed_at="2026-08-31T15:00:00Z",
            source_revision=SOURCE_REVISION,
            adapter=bundle.adapter,
            runner_identity=identity,
            fields=(EvidenceFieldV1("outcome", "success"),),
        )
        for name in REQUIRED_ITEMS
    )
    receipt = RunnerTransportReceiptV1(
        schema="RunnerTransportReceipt.v1",
        source_revision=SOURCE_REVISION,
        runner_identity=identity,
        specification_digest=bundle.policy_digest,
        authorized_plan_digest=bundle.policy_digest,
        execution_policy_digest=bundle.policy_digest,
        adapter=bundle.adapter,
        binding_digest=bundle.binding_digest,
        rendered_squid_digest=bundle.squid_sha256,
        rendered_nftables_digest=bundle.nftables_sha256,
        runner_environment_digest=environment.sha256,
        workload_environment_digest=environment.workload_sha256,
        items=tuple(
            LifecycleEvidenceV1(
                name,
                status if name == "result_uploaded" else EvidenceStatus.EXECUTED_PASSED,
                document.digest,
            )
            for name, document in zip(REQUIRED_ITEMS, documents, strict=True)
        ),
    )
    return receipt, bundle, identity, documents


def test_receipt_requires_result_upload_not_only_local_exit_zero() -> None:
    accepted, bundle, identity, documents = _receipt()
    accepted.assert_accepted(bundle, SOURCE_REVISION, identity, _retained(documents))
    refused, refused_bundle, refused_identity, refused_documents = _receipt(
        EvidenceStatus.NOT_EXECUTED
    )
    with pytest.raises(ValueError, match="result_uploaded"):
        refused.assert_accepted(
            refused_bundle,
            SOURCE_REVISION,
            refused_identity,
            _retained(refused_documents),
        )


def test_receipt_binds_source_revision_to_the_expected_commit() -> None:
    receipt, bundle, identity, documents = _receipt()
    replayed = replace(receipt, source_revision="b" * 40)
    with pytest.raises(ValueError, match="wrong source revision"):
        replayed.assert_accepted(bundle, "b" * 40, identity, _retained(documents))


def test_receipt_binds_adapter_to_the_expected_bundle() -> None:
    receipt, bundle, identity, documents = _receipt()
    object.__setattr__(
        receipt, "adapter", replace(receipt.adapter, key="other-provider")
    )
    with pytest.raises(ValueError, match="adapter identity differs"):
        receipt.assert_accepted(bundle, SOURCE_REVISION, identity, _retained(documents))


def test_receipt_evidence_cannot_replay_under_another_adapter() -> None:
    receipt, bundle, identity, documents = _receipt()
    adapter = replace(bundle.adapter, key="other-provider")
    changed_binding = replace(bundle.binding, adapter=adapter)
    changed_bundle = replace(bundle, adapter=adapter, binding=changed_binding)
    replayed = replace(
        receipt,
        adapter=adapter,
        binding_digest=changed_bundle.binding_digest,
    )
    with pytest.raises(ValueError, match="wrong adapter identity"):
        replayed.assert_accepted(
            changed_bundle,
            SOURCE_REVISION,
            identity,
            _retained(documents),
        )


@pytest.mark.parametrize(
    "expected_identity",
    [
        ProviderRunnerIdentityV1(
            "runner-one",
            "provider-runner-two",
            "owner/repository",
            ("runner-one", "self-hosted"),
        ),
        ProviderRunnerIdentityV1(
            "runner-one",
            "provider-runner-one",
            "other/repository",
            ("runner-one", "self-hosted"),
        ),
        ProviderRunnerIdentityV1(
            "runner-one",
            "provider-runner-one",
            "owner/repository",
            ("extra-label", "runner-one", "self-hosted"),
        ),
    ],
)
def test_receipt_binds_provider_runner_repository_and_labels(
    expected_identity: ProviderRunnerIdentityV1,
) -> None:
    receipt, bundle, _, documents = _receipt()
    with pytest.raises(ValueError, match="provider runner identity differs"):
        receipt.assert_accepted(
            bundle, SOURCE_REVISION, expected_identity, _retained(documents)
        )


def test_receipt_requires_the_retained_canonical_evidence_bytes() -> None:
    receipt, bundle, identity, documents = _receipt()
    with pytest.raises(ValueError, match="documents are missing"):
        receipt.assert_accepted(
            bundle, SOURCE_REVISION, identity, _retained(documents[:-1])
        )

    tampered = replace(documents[0], fields=(EvidenceFieldV1("outcome", "failure"),))
    with pytest.raises(ValueError, match="retained evidence bytes differ"):
        receipt.assert_accepted(
            bundle,
            SOURCE_REVISION,
            identity,
            _retained((tampered, *documents[1:])),
        )

    with pytest.raises(ValueError, match="must be canonical bytes"):
        receipt.assert_accepted(
            bundle,
            SOURCE_REVISION,
            identity,
            documents,  # type: ignore[arg-type]
        )

    replayed = replace(
        receipt,
        items=(replace(receipt.items[0], mutated=True), *receipt.items[1:]),
    )
    with pytest.raises(ValueError, match="wrong mutation flag"):
        replayed.assert_accepted(
            bundle,
            SOURCE_REVISION,
            identity,
            _retained(documents),
        )
