from __future__ import annotations

from dataclasses import dataclass

import pytest
from dotmac_runner_transport import (
    ExactHost,
    ProviderDomainSnapshotV1,
    RunnerTransportAdapterManifest,
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    TransportEndpointV1,
    TransportPolicyRefused,
    canonical_bytes,
    derive_transport_policy,
    typed_sha256,
)


def _snapshot(*hosts: str) -> ProviderDomainSnapshotV1:
    domains = tuple(sorted(ExactHost(host) for host in hosts))
    material = canonical_bytes(tuple(item.value for item in domains))
    return ProviderDomainSnapshotV1(
        source_uri="https://provider.invalid/meta",
        observed_at="2026-08-31T00:00:00Z",
        semantic_sha256=typed_sha256(material),
        field="domains.exact",
        domains=domains,
    )


@dataclass(frozen=True)
class _Adapter:
    manifest: RunnerTransportAdapterManifest


def _adapter(*endpoints: TransportEndpointV1) -> _Adapter:
    hosts = tuple(sorted({endpoint.host.value for endpoint in endpoints}))
    capabilities = tuple(
        sorted({endpoint.capability for endpoint in endpoints}, key=str)
    )
    return _Adapter(
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1.0",
            capabilities=capabilities,
            endpoints=tuple(sorted(endpoints)),
            snapshot=_snapshot(*hosts),
        )
    )


def _endpoint(capability: RunnerTransportCapability, host: str) -> TransportEndpointV1:
    return TransportEndpointV1(capability=capability, host=ExactHost(host))


def _requirements(
    *capabilities: RunnerTransportCapability,
) -> RunnerTransportRequirementsV1:
    return RunnerTransportRequirementsV1(tuple(sorted(capabilities, key=str)))


def test_same_declaration_is_byte_stable_regardless_of_input_order() -> None:
    control = _endpoint(RunnerTransportCapability.CONTROL, "control.invalid")
    results = _endpoint(RunnerTransportCapability.RESULTS, "results.invalid")
    first = derive_transport_policy(
        _requirements(
            RunnerTransportCapability.CONTROL, RunnerTransportCapability.RESULTS
        ),
        _adapter(control, results),
    )
    second = derive_transport_policy(
        _requirements(
            RunnerTransportCapability.RESULTS, RunnerTransportCapability.CONTROL
        ),
        _adapter(results, control),
    )
    assert first.canonical_bytes == second.canonical_bytes
    assert first.digest == second.digest


def test_omitted_transport_differs_from_explicit_deny_all() -> None:
    with pytest.raises(ValueError, match="omitted transport"):
        RunnerTransportRequirementsV1(())
    denied = RunnerTransportRequirementsV1.deny_all()
    adapter = _adapter(_endpoint(RunnerTransportCapability.CONTROL, "one.invalid"))
    policy = derive_transport_policy(denied, adapter)
    assert policy.endpoints == ()


@pytest.mark.parametrize(
    "value",
    [
        "HTTPS://EXAMPLE.COM",
        "example.com/thing",
        "*.example.com",
        "example.com.",
        "127.0.0.1",
        "::1",
        "localhost",
    ],
)
def test_non_exact_host_shapes_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        ExactHost(value)


def test_non_https_endpoint_is_refused() -> None:
    with pytest.raises(ValueError, match="TCP/443"):
        TransportEndpointV1(
            RunnerTransportCapability.CONTROL, ExactHost("one.invalid"), port=80
        )


def test_missing_adapter_capability_refuses() -> None:
    adapter = _adapter(_endpoint(RunnerTransportCapability.CONTROL, "one.invalid"))
    with pytest.raises(TransportPolicyRefused, match="does not implement"):
        derive_transport_policy(
            _requirements(RunnerTransportCapability.RESULTS), adapter
        )


def test_unclassified_snapshot_domain_refuses() -> None:
    endpoint = _endpoint(RunnerTransportCapability.CONTROL, "one.invalid")
    with pytest.raises(ValueError, match="classify every snapshot"):
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1",
            capabilities=(RunnerTransportCapability.CONTROL,),
            endpoints=(endpoint,),
            snapshot=_snapshot("one.invalid", "forgotten.invalid"),
        )


def test_snapshot_domain_may_be_explicitly_excluded_but_not_silently_dropped() -> None:
    endpoint = _endpoint(RunnerTransportCapability.CONTROL, "one.invalid")
    manifest = RunnerTransportAdapterManifest(
        key="fake-provider",
        version="1",
        capabilities=(RunnerTransportCapability.CONTROL,),
        endpoints=(endpoint,),
        snapshot=_snapshot("excluded.invalid", "one.invalid"),
        excluded_snapshot_domains=(ExactHost("excluded.invalid"),),
    )
    assert manifest.excluded_snapshot_domains == (ExactHost("excluded.invalid"),)
    with pytest.raises(ValueError, match="both implement and exclude"):
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1",
            capabilities=(RunnerTransportCapability.CONTROL,),
            endpoints=(endpoint,),
            snapshot=_snapshot("one.invalid"),
            excluded_snapshot_domains=(ExactHost("one.invalid"),),
        )


def test_adapter_or_endpoint_change_moves_the_policy_digest() -> None:
    requirements = _requirements(RunnerTransportCapability.CONTROL)
    first = derive_transport_policy(
        requirements,
        _adapter(_endpoint(RunnerTransportCapability.CONTROL, "one.invalid")),
    )
    second = derive_transport_policy(
        requirements,
        _adapter(_endpoint(RunnerTransportCapability.CONTROL, "two.invalid")),
    )
    assert first.digest != second.digest


def test_a_second_provider_needs_no_core_change() -> None:
    requirements = _requirements(RunnerTransportCapability.CONTROL)
    other = _adapter(_endpoint(RunnerTransportCapability.CONTROL, "other.invalid"))
    assert derive_transport_policy(requirements, other).endpoints[0].host.value == (
        "other.invalid"
    )
