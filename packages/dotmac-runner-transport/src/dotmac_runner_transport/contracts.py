"""Typed, provider-neutral runner transport contracts."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import StrEnum

from .canonical import canonical_bytes, typed_sha256

__all__ = [
    "AdapterIdentityV1",
    "ExactHost",
    "HostDirectEgressGrantV1",
    "HostNftablesBindingV1",
    "HostProxyIdentityV1",
    "HostRunnerIdentityV1",
    "HostRunnerTransportSpecV1",
    "ProviderDomainSnapshotV1",
    "RunnerTransportCapability",
    "RunnerTransportRequirementsV1",
    "TransportEndpointV1",
    "WorkloadEgressPolicyV1",
]

_HOST_RE = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class RunnerTransportCapability(StrEnum):
    """Provider-neutral reasons a runner needs network transport."""

    CONTROL = "runner.control.v1"
    SOURCE_FETCH = "runner.source-fetch.v1"
    ACTION_FETCH = "runner.action-fetch.v1"
    RESULTS = "runner.results.v1"
    ARTIFACTS_CACHE = "runner.artifacts-cache.v1"
    OIDC = "runner.oidc.v1"
    PACKAGES = "runner.packages.v1"
    RELEASE_ASSETS = "runner.release-assets.v1"
    RUNNER_UPDATE = "runner.update.v1"


@dataclass(frozen=True, slots=True, order=True)
class ExactHost:
    """One canonical DNS host; v1 deliberately has no wildcard form."""

    value: str

    def __post_init__(self) -> None:
        value = self.value
        if value != value.lower() or value.endswith("."):
            raise ValueError("host must be lowercase without a trailing dot")
        if any(marker in value for marker in ("://", "/", "*", "@")):
            raise ValueError("host must be an exact DNS name, not a URL or wildcard")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            raise ValueError("IP literals are not transport host identities")
        if not _HOST_RE.fullmatch(value):
            raise ValueError(f"invalid exact DNS host: {value!r}")


@dataclass(frozen=True, slots=True, order=True)
class TransportEndpointV1:
    """One exact HTTPS endpoint attributed to one capability."""

    capability: RunnerTransportCapability
    host: ExactHost
    port: int = 443
    protocol: str = "tcp"

    def __post_init__(self) -> None:
        if self.protocol != "tcp" or self.port != 443:
            raise ValueError("RunnerTransport v1 supports exact-host TCP/443 only")


@dataclass(frozen=True, slots=True)
class ProviderDomainSnapshotV1:
    """Immutable adapter input; collection happens outside the request path."""

    source_uri: str
    observed_at: str
    semantic_sha256: str
    field: str
    domains: tuple[ExactHost, ...]

    def __post_init__(self) -> None:
        if not self.source_uri.startswith("https://"):
            raise ValueError("snapshot source must be HTTPS")
        if not self.domains:
            raise ValueError("provider snapshot cannot be empty")
        if tuple(sorted(set(self.domains))) != self.domains:
            raise ValueError("snapshot domains must be unique and sorted")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.semantic_sha256):
            raise ValueError("snapshot digest must be a typed sha256 value")


@dataclass(frozen=True, slots=True)
class AdapterIdentityV1:
    key: str
    version: str
    declaration_digest: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.key):
            raise ValueError("adapter key must be a lowercase kebab identifier")
        if not self.version:
            raise ValueError("adapter version cannot be empty")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.declaration_digest):
            raise ValueError("adapter declaration digest must be typed")


@dataclass(frozen=True, slots=True)
class RunnerTransportRequirementsV1:
    """Capabilities the consuming runner explicitly asks the adapter to supply."""

    capabilities: tuple[RunnerTransportCapability, ...]

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("omitted transport policy is invalid; use deny_all()")
        if tuple(sorted(set(self.capabilities), key=str)) != self.capabilities:
            raise ValueError("capabilities must be unique and sorted")

    @classmethod
    def deny_all(cls) -> RunnerTransportRequirementsV1:
        """Explicit empty policy, distinct from an omitted policy."""

        instance = object.__new__(cls)
        object.__setattr__(instance, "capabilities", ())
        return instance


@dataclass(frozen=True, slots=True)
class WorkloadEgressPolicyV1:
    """Repository workload destinations, never merged into transport authority."""

    policy_key: str
    endpoints: tuple[ExactHost, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.policy_key):
            raise ValueError("workload policy key must be a lowercase identifier")
        if tuple(sorted(set(self.endpoints))) != self.endpoints:
            raise ValueError("workload endpoints must be unique and sorted")


@dataclass(frozen=True, slots=True)
class HostRunnerIdentityV1:
    """Late-bound host identity used only by an enforcement renderer."""

    runner_name: str
    uid: int
    transport_port: int
    workload_port: int | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.runner_name):
            raise ValueError("runner name must be a lowercase host identifier")
        if self.uid <= 0:
            raise ValueError("runner UID must be positive")
        ports = [self.transport_port]
        if self.workload_port is not None:
            ports.append(self.workload_port)
        if any(port < 1024 or port > 65535 for port in ports):
            raise ValueError("proxy listener ports must be unprivileged TCP ports")


@dataclass(frozen=True, slots=True)
class HostProxyIdentityV1:
    """Late-bound identity of the FQDN-aware proxy and local resolver."""

    service_name: str
    uid: int
    resolver_ipv4: str = "127.0.0.53"
    resolver_ipv6: str | None = None
    public_ipv6: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", self.service_name):
            raise ValueError("proxy service name must be a lowercase host identity")
        if self.uid <= 0:
            raise ValueError("proxy UID must be positive")
        for value in (self.resolver_ipv4, self.resolver_ipv6):
            if value is None:
                continue
            address = ipaddress.ip_address(value)
            if not address.is_loopback:
                raise ValueError("proxy resolvers must be loopback addresses")


@dataclass(frozen=True, slots=True, order=True)
class HostDirectEgressGrantV1:
    """One explicit non-proxy host binding for a named runner workload."""

    runner_name: str
    destination: str
    port: int
    protocol: str = "tcp"
    output_interface: str | None = None

    def __post_init__(self) -> None:
        if not self.runner_name:
            raise ValueError("direct egress grants require a runner name")
        network = ipaddress.ip_network(self.destination, strict=True)
        if str(network) != self.destination:
            raise ValueError("direct egress destination must be canonical CIDR")
        if self.protocol not in {"tcp", "udp"}:
            raise ValueError("direct egress protocol must be tcp or udp")
        if self.port < 1 or self.port > 65535:
            raise ValueError("direct egress port is outside the valid range")
        if self.output_interface is not None and not re.fullmatch(
            r"[a-zA-Z0-9_.:-]{1,15}", self.output_interface
        ):
            raise ValueError("direct egress output interface is invalid")


@dataclass(frozen=True, slots=True)
class HostNftablesBindingV1:
    """Exact placement contract inside the host's owning output chain."""

    family: str
    table: str
    output_chain: str
    must_precede: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier = re.compile(r"[a-z_][a-z0-9_-]{0,63}")
        if self.family not in {"inet", "ip", "ip6"}:
            raise ValueError("nftables family must be inet, ip or ip6")
        if not identifier.fullmatch(self.table):
            raise ValueError("nftables table must be a lowercase identifier")
        if not identifier.fullmatch(self.output_chain):
            raise ValueError("nftables output chain must be a lowercase identifier")
        if not self.must_precede:
            raise ValueError("nftables placement requires at least one later anchor")
        if tuple(sorted(set(self.must_precede))) != self.must_precede:
            raise ValueError("nftables placement anchors must be unique and sorted")
        if any(not item.strip() or "\n" in item for item in self.must_precede):
            raise ValueError("nftables placement anchors must be one nonempty line")


@dataclass(frozen=True, slots=True)
class HostRunnerTransportSpecV1:
    """Canonical late-bound host intent consumed by the enforcement renderer."""

    schema: str
    policy_digest: str
    adapter: AdapterIdentityV1
    identities: tuple[HostRunnerIdentityV1, ...]
    workload_policies: tuple[WorkloadEgressPolicyV1, ...]
    direct_grants: tuple[HostDirectEgressGrantV1, ...]
    proxy_identity: HostProxyIdentityV1
    nftables_binding: HostNftablesBindingV1

    def __post_init__(self) -> None:
        self.assert_valid()

    def assert_valid(self) -> None:
        if self.schema != "HostRunnerTransportSpec.v1":
            raise ValueError("host runner transport spec schema must be v1")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.policy_digest):
            raise ValueError("host binding policy digest must be typed")
        if not self.identities:
            raise ValueError("host binding requires at least one runner identity")
        if tuple(sorted(self.identities, key=lambda item: item.runner_name)) != (
            self.identities
        ):
            raise ValueError("host runner identities must be sorted by runner name")
        runner_names = {item.runner_name for item in self.identities}
        runner_uids = {item.uid for item in self.identities}
        if len(runner_names) != len(self.identities):
            raise ValueError("host runner names must be unique")
        if len(runner_uids) != len(self.identities):
            raise ValueError("host runner UIDs must be unique")
        if self.proxy_identity.uid in runner_uids:
            raise ValueError("host proxy and runner UIDs must be distinct")
        listener_ports = tuple(
            port
            for identity in self.identities
            for port in (identity.transport_port, identity.workload_port)
            if port is not None
        )
        if len(set(listener_ports)) != len(listener_ports):
            raise ValueError("host proxy listener ports must be unique")
        if (
            tuple(sorted(self.workload_policies, key=lambda item: item.policy_key))
            != self.workload_policies
        ):
            raise ValueError("host workload policies must be sorted by policy key")
        workload_keys = {item.policy_key for item in self.workload_policies}
        if len(workload_keys) != len(self.workload_policies):
            raise ValueError("host workload policy keys must be unique")
        workload_runner_names = {
            item.runner_name
            for item in self.identities
            if item.workload_port is not None
        }
        if workload_keys != workload_runner_names:
            raise ValueError(
                "host workload policies must match workload listeners exactly"
            )
        if tuple(sorted(set(self.direct_grants))) != self.direct_grants:
            raise ValueError("host direct grants must be unique and sorted")
        if any(item.runner_name not in runner_names for item in self.direct_grants):
            raise ValueError("host direct egress grant names an unknown runner")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)

    @property
    def digest(self) -> str:
        return typed_sha256(self.canonical_bytes)
