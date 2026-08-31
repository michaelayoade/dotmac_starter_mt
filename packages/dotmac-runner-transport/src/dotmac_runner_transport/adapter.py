"""Adapter protocol; provider knowledge enters through this seam only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .canonical import canonical_bytes, typed_sha256
from .contracts import (
    AdapterIdentityV1,
    ExactHost,
    ProviderDomainSnapshotV1,
    RunnerTransportCapability,
    TransportEndpointV1,
)

__all__ = ["RunnerTransportAdapter", "RunnerTransportAdapterManifest"]


@dataclass(frozen=True, slots=True)
class RunnerTransportAdapterManifest:
    key: str
    version: str
    capabilities: tuple[RunnerTransportCapability, ...]
    endpoints: tuple[TransportEndpointV1, ...]
    snapshot: ProviderDomainSnapshotV1
    excluded_snapshot_domains: tuple[ExactHost, ...] = ()

    def __post_init__(self) -> None:
        if tuple(sorted(set(self.capabilities), key=str)) != self.capabilities:
            raise ValueError("adapter capabilities must be unique and sorted")
        if tuple(sorted(set(self.endpoints))) != self.endpoints:
            raise ValueError("adapter endpoints must be unique and sorted")
        if (
            tuple(sorted(set(self.excluded_snapshot_domains)))
            != self.excluded_snapshot_domains
        ):
            raise ValueError("excluded snapshot domains must be unique and sorted")
        implemented = {endpoint.capability for endpoint in self.endpoints}
        declared = set(self.capabilities)
        if implemented != declared:
            missing = sorted(str(item) for item in declared - implemented)
            surplus = sorted(str(item) for item in implemented - declared)
            raise ValueError(
                f"capability coverage mismatch: missing={missing}, surplus={surplus}"
            )
        endpoint_hosts = {endpoint.host for endpoint in self.endpoints}
        snapshot_hosts = set(self.snapshot.domains)
        excluded_hosts = set(self.excluded_snapshot_domains)
        overlap = endpoint_hosts & excluded_hosts
        if overlap:
            raise ValueError(
                "adapter cannot both implement and exclude a snapshot domain: "
                + ", ".join(sorted(item.value for item in overlap))
            )
        accounted_hosts = endpoint_hosts | excluded_hosts
        if accounted_hosts != snapshot_hosts:
            missing_hosts = sorted(
                item.value for item in snapshot_hosts - accounted_hosts
            )
            surplus_hosts = sorted(
                item.value for item in accounted_hosts - snapshot_hosts
            )
            raise ValueError(
                "adapter must classify every snapshot domain as an endpoint "
                "or explicitly exclude it: "
                f"unaccounted={missing_hosts}, outside_snapshot={surplus_hosts}"
            )

    @property
    def identity(self) -> AdapterIdentityV1:
        return AdapterIdentityV1(
            key=self.key,
            version=self.version,
            declaration_digest=typed_sha256(canonical_bytes(self)),
        )


@runtime_checkable
class RunnerTransportAdapter(Protocol):
    @property
    def manifest(self) -> RunnerTransportAdapterManifest: ...
