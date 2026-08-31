"""Resolve requirements into one immutable provider-neutral transport policy."""

from __future__ import annotations

from dataclasses import dataclass

from .adapter import RunnerTransportAdapter
from .canonical import canonical_bytes, typed_sha256
from .contracts import (
    AdapterIdentityV1,
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    TransportEndpointV1,
)

__all__ = [
    "ResolvedRunnerTransportPolicyV1",
    "TransportPolicyRefused",
    "derive_transport_policy",
]


class TransportPolicyRefused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedRunnerTransportPolicyV1:
    schema: str
    requirements: RunnerTransportRequirementsV1
    adapter: AdapterIdentityV1
    snapshot_digest: str
    endpoints: tuple[TransportEndpointV1, ...]

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)

    @property
    def digest(self) -> str:
        return typed_sha256(self.canonical_bytes)


def derive_transport_policy(
    requirements: RunnerTransportRequirementsV1,
    adapter: RunnerTransportAdapter,
) -> ResolvedRunnerTransportPolicyV1:
    manifest = adapter.manifest
    requested = set(requirements.capabilities)
    declared = set(manifest.capabilities)
    if not requested:
        endpoints: tuple[TransportEndpointV1, ...] = ()
    else:
        missing = requested - declared
        if missing:
            raise TransportPolicyRefused(
                "adapter does not implement required capabilities: "
                + ", ".join(sorted(str(item) for item in missing))
            )
        endpoints = tuple(
            endpoint
            for endpoint in manifest.endpoints
            if endpoint.capability in requested
        )
        covered: set[RunnerTransportCapability] = {
            endpoint.capability for endpoint in endpoints
        }
        if covered != requested:
            raise TransportPolicyRefused(
                "requested capabilities have no endpoint coverage"
            )
    return ResolvedRunnerTransportPolicyV1(
        schema="RunnerEgressPolicy.v1",
        requirements=requirements,
        adapter=manifest.identity,
        snapshot_digest=manifest.snapshot.semantic_sha256,
        endpoints=tuple(sorted(endpoints)),
    )
