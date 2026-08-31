"""Provider-neutral self-hosted runner transport policy."""

from .adapter import RunnerTransportAdapter, RunnerTransportAdapterManifest
from .canonical import canonical_bytes, typed_sha256
from .contracts import (
    AdapterIdentityV1,
    ExactHost,
    HostDirectEgressGrantV1,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    HostRunnerTransportSpecV1,
    ProviderDomainSnapshotV1,
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    TransportEndpointV1,
    WorkloadEgressPolicyV1,
)
from .discovery import ADAPTER_GROUP, AdapterDiscoveryError, discover_adapter
from .policy import (
    ResolvedRunnerTransportPolicyV1,
    TransportPolicyRefused,
    derive_transport_policy,
)
from .receipt import EvidenceStatus, LifecycleEvidenceV1, RunnerTransportReceiptV1
from .render import (
    RunnerEgressBundleV1,
    RunnerProxyEnvironmentV1,
    assert_nftables_placement,
    render_host_bundle,
)

__all__ = [
    "ADAPTER_GROUP",
    "AdapterDiscoveryError",
    "AdapterIdentityV1",
    "EvidenceStatus",
    "ExactHost",
    "HostDirectEgressGrantV1",
    "HostNftablesBindingV1",
    "HostProxyIdentityV1",
    "HostRunnerIdentityV1",
    "HostRunnerTransportSpecV1",
    "LifecycleEvidenceV1",
    "ProviderDomainSnapshotV1",
    "ResolvedRunnerTransportPolicyV1",
    "RunnerEgressBundleV1",
    "RunnerProxyEnvironmentV1",
    "RunnerTransportAdapter",
    "RunnerTransportAdapterManifest",
    "RunnerTransportCapability",
    "RunnerTransportReceiptV1",
    "RunnerTransportRequirementsV1",
    "TransportEndpointV1",
    "TransportPolicyRefused",
    "WorkloadEgressPolicyV1",
    "canonical_bytes",
    "assert_nftables_placement",
    "derive_transport_policy",
    "discover_adapter",
    "render_host_bundle",
    "typed_sha256",
]
