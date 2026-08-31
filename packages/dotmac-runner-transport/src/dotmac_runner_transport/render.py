"""Deterministic host enforcement rendering from an authorized policy."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from .canonical import typed_sha256
from .contracts import (
    HostDirectEgressGrantV1,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    HostRunnerTransportSpecV1,
    WorkloadEgressPolicyV1,
)
from .policy import ResolvedRunnerTransportPolicyV1

__all__ = [
    "RunnerEgressBundleV1",
    "RunnerProxyEnvironmentV1",
    "assert_nftables_placement",
    "render_host_bundle",
]


_RESERVED_IPV4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
_RESERVED_IPV6 = (
    "::/128",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "2001:db8::/32",
    "ff00::/8",
)


@dataclass(frozen=True, slots=True)
class RunnerProxyEnvironmentV1:
    runner_name: str
    content: str
    sha256: str
    workload_content: str | None
    workload_sha256: str | None

    def __post_init__(self) -> None:
        self.assert_valid()

    def assert_valid(self) -> None:
        if self.sha256 != typed_sha256(self.content.encode("utf-8")):
            raise ValueError("runner environment digest differs from its content")
        if (self.workload_content is None) != (self.workload_sha256 is None):
            raise ValueError(
                "workload environment content and digest must be present together"
            )
        if self.workload_content is not None and self.workload_sha256 != typed_sha256(
            self.workload_content.encode("utf-8")
        ):
            raise ValueError("workload environment digest differs from its content")


@dataclass(frozen=True, slots=True)
class RunnerEgressBundleV1:
    schema: str
    policy_digest: str
    binding: HostRunnerTransportSpecV1
    squid_conf: str
    nftables_conf: str
    nftables_jump_rule: str
    runner_environments: tuple[RunnerProxyEnvironmentV1, ...]
    squid_sha256: str
    nftables_sha256: str

    def __post_init__(self) -> None:
        self.assert_valid()

    def assert_valid(self) -> None:
        """Refuse stale digest claims and incomplete late-bound host identity."""

        if self.schema != "RunnerEgressBundle.v1":
            raise ValueError("runner egress bundle schema must be v1")
        self.binding.assert_valid()
        if self.policy_digest != self.binding.policy_digest:
            raise ValueError("bundle policy differs from its host binding")
        if self.squid_sha256 != typed_sha256(self.squid_conf.encode("utf-8")):
            raise ValueError("bundle Squid digest differs from its content")
        if self.nftables_sha256 != _nftables_digest(
            self.nftables_conf, self.nftables_jump_rule
        ):
            raise ValueError(
                "bundle nftables digest differs from its chain and jump content"
            )
        expected_names = tuple(
            identity.runner_name for identity in self.binding.identities
        )
        actual_names = tuple(item.runner_name for item in self.runner_environments)
        if actual_names != expected_names:
            raise ValueError(
                "bundle runner environments must match bound identities exactly"
            )
        identity_by_name = {
            identity.runner_name: identity for identity in self.binding.identities
        }
        for environment in self.runner_environments:
            environment.assert_valid()
            expects_workload = (
                identity_by_name[environment.runner_name].workload_port is not None
            )
            if (environment.workload_content is not None) != expects_workload:
                raise ValueError(
                    "bundle workload environments must match bound listeners exactly"
                )

    @property
    def binding_digest(self) -> str:
        return self.binding.digest

    @property
    def nftables_binding(self) -> HostNftablesBindingV1:
        return self.binding.nftables_binding


def _domain_acl(name: str, hosts: tuple[str, ...]) -> list[str]:
    if not hosts:
        return [f"acl {name} dstdomain .invalid"]
    return [f"acl {name} dstdomain {host}" for host in hosts]


def _nftables_digest(nftables_conf: str, jump_rule: str) -> str:
    return typed_sha256((nftables_conf + jump_rule + "\n").encode("utf-8"))


def _environment(
    identity: HostRunnerIdentityV1,
    direct_grants: tuple[HostDirectEgressGrantV1, ...],
) -> RunnerProxyEnvironmentV1:
    bypass = tuple(
        str(network.network_address) if network.num_addresses == 1 else str(network)
        for network in (
            ipaddress.ip_network(grant.destination) for grant in direct_grants
        )
    )
    bypass_lines = ""
    if bypass:
        bypass_value = ",".join(bypass)
        bypass_lines = f"no_proxy={bypass_value}\nNO_PROXY={bypass_value}\n"
    content = (
        f"http_proxy=http://127.0.0.1:{identity.transport_port}\n"
        f"https_proxy=http://127.0.0.1:{identity.transport_port}\n"
        f"{bypass_lines}"
    )
    workload_content = None
    workload_sha256 = None
    if identity.workload_port is not None:
        workload_content = (
            f"http_proxy=http://127.0.0.1:{identity.workload_port}\n"
            f"https_proxy=http://127.0.0.1:{identity.workload_port}\n"
            f"HTTP_PROXY=http://127.0.0.1:{identity.workload_port}\n"
            f"HTTPS_PROXY=http://127.0.0.1:{identity.workload_port}\n"
            f"{bypass_lines}"
        )
        workload_sha256 = typed_sha256(workload_content.encode("utf-8"))
    return RunnerProxyEnvironmentV1(
        runner_name=identity.runner_name,
        content=content,
        sha256=typed_sha256(content.encode("utf-8")),
        workload_content=workload_content,
        workload_sha256=workload_sha256,
    )


def render_host_bundle(
    policy: ResolvedRunnerTransportPolicyV1,
    identities: tuple[HostRunnerIdentityV1, ...],
    proxy_identity: HostProxyIdentityV1,
    nftables_binding: HostNftablesBindingV1,
    workload_policies: tuple[WorkloadEgressPolicyV1, ...] = (),
    direct_grants: tuple[HostDirectEgressGrantV1, ...] = (),
) -> RunnerEgressBundleV1:
    """Render an owned chain that the host jumps to before broad accepts.

    The nftables fragment is deliberately a regular chain, not a second
    output-hook base chain. An accept in an earlier base chain is not final:
    VMID 124's later deny-by-default chain would still drop the proxy. The host
    generator must install the jump rule in its owning output chain before its
    loopback and broad-set accepts.
    """

    if not identities:
        raise ValueError("at least one runner identity is required")
    runner_uids = {item.uid for item in identities}
    if len(runner_uids) != len(identities):
        raise ValueError("runner UIDs must be unique")
    if proxy_identity.uid in runner_uids:
        raise ValueError("proxy and runner UIDs must be distinct")
    runner_names = {item.runner_name for item in identities}
    if len(runner_names) != len(identities):
        raise ValueError("runner names must be unique")
    all_ports = tuple(
        port
        for item in identities
        for port in (item.transport_port, item.workload_port)
        if port is not None
    )
    if len(set(all_ports)) != len(all_ports):
        raise ValueError("all proxy listener ports must be unique")

    workload_by_key = {item.policy_key: item for item in workload_policies}
    if len(workload_by_key) != len(workload_policies):
        raise ValueError("workload policy keys must be unique")
    workload_runner_names = {
        item.runner_name for item in identities if item.workload_port is not None
    }
    if set(workload_by_key) != workload_runner_names:
        raise ValueError("workload policies must match workload listeners exactly")
    if any(item.runner_name not in runner_names for item in direct_grants):
        raise ValueError("direct egress grant names an unknown runner")
    if tuple(sorted(set(direct_grants))) != direct_grants:
        raise ValueError("direct egress grants must be unique and sorted")

    binding = HostRunnerTransportSpecV1(
        schema="HostRunnerTransportSpec.v1",
        policy_digest=policy.digest,
        identities=tuple(sorted(identities, key=lambda item: item.runner_name)),
        workload_policies=tuple(
            sorted(workload_policies, key=lambda item: item.policy_key)
        ),
        direct_grants=direct_grants,
        proxy_identity=proxy_identity,
        nftables_binding=nftables_binding,
    )
    binding_digest = binding.digest
    transport_hosts = tuple(sorted({item.host.value for item in policy.endpoints}))
    squid: list[str] = [
        "# Generated RunnerEgressBundle.v1; do not edit.",
        f"# policy_digest={policy.digest}",
        f"# binding_digest={binding_digest}",
        "acl CONNECT method CONNECT",
        "acl SSL_ports port 443",
        "acl numeric_connect url_regex -i ^[0-9a-f:.]+:[0-9]+$",
        f"acl reserved_v4 dst {' '.join(_RESERVED_IPV4)}",
        f"acl reserved_v6 dst {' '.join(_RESERVED_IPV6)}",
        "http_access deny numeric_connect",
        "http_access deny reserved_v4",
        "http_access deny reserved_v6",
    ]
    squid.extend(_domain_acl("runner_transport", transport_hosts))
    for identity in sorted(identities, key=lambda item: item.uid):
        listener = f"transport_{identity.uid}"
        squid.extend(
            [
                f"http_port 127.0.0.1:{identity.transport_port} name={listener}",
                f"acl on_{listener} myportname {listener}",
                f"http_access allow on_{listener} CONNECT SSL_ports runner_transport",
                f"http_access deny on_{listener}",
            ]
        )
        if identity.workload_port is not None:
            workload = workload_by_key[identity.runner_name]
            workload_name = f"workload_{identity.uid}"
            squid.extend(
                _domain_acl(
                    f"allowed_{workload_name}",
                    tuple(host.value for host in workload.endpoints),
                )
            )
            squid.extend(
                [
                    "http_port "
                    f"127.0.0.1:{identity.workload_port} name={workload_name}",
                    f"acl on_{workload_name} myportname {workload_name}",
                    f"http_access allow on_{workload_name} CONNECT SSL_ports "
                    f"allowed_{workload_name}",
                    f"http_access deny on_{workload_name}",
                ]
            )
    squid.extend(
        [
            "http_access deny all",
            "logformat dotmac_runner "
            "%ts.%03tu %>a %lp %Ss/%03>Hs %<st %rm %>rd:%>rP",
            "access_log stdio:/var/log/squid/runner-transport.log dotmac_runner",
            "cache deny all",
            "via off",
            "forwarded_for delete",
        ]
    )
    squid_text = "\n".join(squid) + "\n"

    nft: list[str] = [
        "# Generated RunnerEgressBundle.v1; place inside table "
        f"{nftables_binding.family} {nftables_binding.table}.",
        f"# policy_digest={policy.digest}",
        f"# binding_digest={binding_digest}",
        "chain runner_transport {",
    ]
    proxy_ports = ", ".join(str(port) for port in sorted(all_ports))
    grants_by_runner = {
        name: tuple(item for item in direct_grants if item.runner_name == name)
        for name in runner_names
    }
    for identity in sorted(identities, key=lambda item: item.uid):
        own_ports = [identity.transport_port]
        if identity.workload_port is not None:
            own_ports.append(identity.workload_port)
        own_port_set = ", ".join(str(port) for port in sorted(own_ports))
        nft.append(
            f"  meta skuid {identity.uid} ip daddr 127.0.0.1 "
            f"tcp dport {{ {own_port_set} }} accept"
        )
        for grant in grants_by_runner[identity.runner_name]:
            family = "ip6" if ":" in grant.destination else "ip"
            interface = (
                f'oifname "{grant.output_interface}" '
                if grant.output_interface is not None
                else ""
            )
            nft.append(
                f"  meta skuid {identity.uid} {interface}{family} daddr "
                f"{grant.destination} "
                f"{grant.protocol} dport {grant.port} accept"
            )
        nft.extend(
            [
                f"  meta skuid {identity.uid} ip daddr 127.0.0.1 "
                f"tcp dport {{ {proxy_ports} }} counter comment "
                f'"runner {identity.runner_name} cross-listener refused" reject',
                f"  meta skuid {identity.uid} counter comment "
                f'"runner {identity.runner_name} direct egress refused" reject',
            ]
        )

    nft.append(
        f"  ip daddr 127.0.0.1 tcp dport {{ {proxy_ports} }} counter comment "
        '"runner unlisted identity listener refused" reject'
    )

    proxy_uid = proxy_identity.uid
    nft.extend(
        [
            f"  meta skuid {proxy_uid} ip daddr {proxy_identity.resolver_ipv4} "
            "udp dport 53 accept",
            f"  meta skuid {proxy_uid} ip daddr {proxy_identity.resolver_ipv4} "
            "tcp dport 53 accept",
        ]
    )
    if proxy_identity.resolver_ipv6 is not None:
        nft.extend(
            [
                f"  meta skuid {proxy_uid} ip6 daddr {proxy_identity.resolver_ipv6} "
                "udp dport 53 accept",
                f"  meta skuid {proxy_uid} ip6 daddr {proxy_identity.resolver_ipv6} "
                "tcp dport 53 accept",
            ]
        )
    nft.extend(
        [
            f"  meta skuid {proxy_uid} ip daddr "
            f"{{ {', '.join(_RESERVED_IPV4)} }} counter comment "
            '"runner proxy private IPv4 refused" reject',
            f"  meta skuid {proxy_uid} ip6 daddr "
            f"{{ {', '.join(_RESERVED_IPV6)} }} counter comment "
            '"runner proxy private IPv6 refused" reject',
            f"  meta skuid {proxy_uid} meta nfproto ipv4 tcp dport 443 accept",
        ]
    )
    if proxy_identity.public_ipv6:
        nft.append(f"  meta skuid {proxy_uid} meta nfproto ipv6 tcp dport 443 accept")
    else:
        nft.append(
            f"  meta skuid {proxy_uid} meta nfproto ipv6 counter comment "
            '"runner proxy public IPv6 refused" reject'
        )
    nft.extend(
        [
            f"  meta skuid {proxy_uid} counter comment "
            '"runner proxy non-HTTPS refused" reject',
            "}",
        ]
    )
    nft_text = "\n".join(nft) + "\n"
    jump_rule = (
        'jump runner_transport comment "dotmac runner transport; before loopback '
        'and broad-set accepts"'
    )
    return RunnerEgressBundleV1(
        schema="RunnerEgressBundle.v1",
        policy_digest=policy.digest,
        binding=binding,
        squid_conf=squid_text,
        nftables_conf=nft_text,
        nftables_jump_rule=jump_rule,
        runner_environments=tuple(
            _environment(identity, grants_by_runner[identity.runner_name])
            for identity in sorted(identities, key=lambda item: item.runner_name)
        ),
        squid_sha256=typed_sha256(squid_text.encode("utf-8")),
        nftables_sha256=_nftables_digest(nft_text, jump_rule),
    )


def _named_block(lines: list[str], header: str) -> tuple[int, int]:
    candidates = [index for index, line in enumerate(lines) if line.strip() == header]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one {header!r} block")
    start = candidates[0]
    depth = 0
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth == 0:
            return start, index
    raise ValueError(f"unterminated {header!r} block")


def assert_nftables_placement(ruleset: str, bundle: RunnerEgressBundleV1) -> None:
    """Refuse a host ruleset where the owned jump can be bypassed or dropped later."""

    lines = ruleset.splitlines()
    binding = bundle.nftables_binding
    table_header = f"table {binding.family} {binding.table} {{"
    table_start, table_end = _named_block(lines, table_header)
    table_lines = lines[table_start + 1 : table_end]
    chain_start, chain_end = _named_block(table_lines, "chain runner_transport {")
    expected_lines = bundle.nftables_conf.splitlines()
    expected_start, expected_end = _named_block(
        expected_lines, "chain runner_transport {"
    )
    actual_chain = [line.strip() for line in table_lines[chain_start : chain_end + 1]]
    expected_chain = [
        line.strip() for line in expected_lines[expected_start : expected_end + 1]
    ]
    if actual_chain != expected_chain:
        raise ValueError("installed runner transport chain differs from rendered bytes")
    output_start, output_end = _named_block(
        table_lines, f"chain {binding.output_chain} {{"
    )
    output = [line.strip() for line in table_lines[output_start + 1 : output_end]]
    try:
        jump_index = output.index(bundle.nftables_jump_rule)
    except ValueError as error:
        raise ValueError(
            "owning output chain has no exact runner transport jump"
        ) from error
    if output.count(bundle.nftables_jump_rule) != 1:
        raise ValueError("owning output chain must contain exactly one transport jump")
    for marker in binding.must_precede:
        if output.count(marker) != 1:
            raise ValueError(
                f"nftables placement anchor is missing or duplicated: {marker}"
            )
        if output.index(marker) < jump_index:
            raise ValueError(
                f"runner transport jump appears after bypass anchor: {marker}"
            )
