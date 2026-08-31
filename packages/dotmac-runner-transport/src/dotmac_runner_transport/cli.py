"""Offline policy rendering CLI; provider metadata is never fetched here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import (
    ExactHost,
    HostDirectEgressGrantV1,
    HostNftablesBindingV1,
    HostProxyIdentityV1,
    HostRunnerIdentityV1,
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    WorkloadEgressPolicyV1,
)
from .discovery import discover_adapter
from .policy import derive_transport_policy
from .render import render_host_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--capability", action="append", required=True)
    parser.add_argument(
        "--runner",
        action="append",
        required=True,
        help="name:uid:transport_port[:workload_port]",
    )
    parser.add_argument("--proxy-service", required=True)
    parser.add_argument("--proxy-uid", required=True, type=int)
    parser.add_argument("--resolver-ipv4", default="127.0.0.53")
    parser.add_argument("--resolver-ipv6")
    parser.add_argument("--public-ipv6", action="store_true")
    parser.add_argument("--nft-family", choices=("inet", "ip", "ip6"), default="inet")
    parser.add_argument("--nft-table", required=True)
    parser.add_argument("--nft-output-chain", required=True)
    parser.add_argument(
        "--nft-before",
        action="append",
        required=True,
        help="exact later rule that the transport jump must precede",
    )
    parser.add_argument(
        "--workload",
        action="append",
        default=[],
        help="runner-name=exact.host,other.host",
    )
    parser.add_argument(
        "--direct-grant",
        action="append",
        default=[],
        help="runner-name,protocol,port,canonical-cidr[,output-interface]",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    requirements = RunnerTransportRequirementsV1(
        tuple(
            sorted(
                {RunnerTransportCapability(item) for item in args.capability}, key=str
            )
        )
    )
    identities: list[HostRunnerIdentityV1] = []
    for raw in args.runner:
        fields = raw.split(":")
        if len(fields) not in {3, 4}:
            parser.error("--runner must have three or four colon-separated fields")
        name, uid, port = fields[:3]
        workload_port = int(fields[3]) if len(fields) == 4 else None
        identities.append(
            HostRunnerIdentityV1(name, int(uid), int(port), workload_port)
        )
    workload_policies: list[WorkloadEgressPolicyV1] = []
    for raw in args.workload:
        name, separator, hosts = raw.partition("=")
        if not separator or not hosts:
            parser.error("--workload must be runner-name=exact.host,other.host")
        workload_policies.append(
            WorkloadEgressPolicyV1(
                name,
                tuple(sorted(ExactHost(host) for host in hosts.split(","))),
            )
        )
    direct_grants: list[HostDirectEgressGrantV1] = []
    for raw in args.direct_grant:
        fields = raw.split(",", 4)
        if len(fields) not in {4, 5}:
            parser.error("--direct-grant must be runner,protocol,port,cidr[,interface]")
        name, protocol, port, destination = fields[:4]
        output_interface = fields[4] if len(fields) == 5 else None
        direct_grants.append(
            HostDirectEgressGrantV1(
                name, destination, int(port), protocol, output_interface
            )
        )
    policy = derive_transport_policy(requirements, discover_adapter(args.adapter))
    bundle = render_host_bundle(
        policy,
        tuple(identities),
        HostProxyIdentityV1(
            args.proxy_service,
            args.proxy_uid,
            args.resolver_ipv4,
            args.resolver_ipv6,
            args.public_ipv6,
        ),
        HostNftablesBindingV1(
            args.nft_family,
            args.nft_table,
            args.nft_output_chain,
            tuple(sorted(args.nft_before)),
        ),
        tuple(workload_policies),
        tuple(sorted(direct_grants)),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "squid.conf").write_text(bundle.squid_conf, encoding="utf-8")
    (args.output / "runner-transport-chain.nft").write_text(
        bundle.nftables_conf, encoding="utf-8"
    )
    (args.output / "runner-transport-jump.rule").write_text(
        bundle.nftables_jump_rule + "\n", encoding="utf-8"
    )
    (args.output / "policy.json").write_bytes(policy.canonical_bytes)
    (args.output / "binding.json").write_bytes(bundle.binding.canonical_bytes)
    for environment in bundle.runner_environments:
        (args.output / f"runner-{environment.runner_name}.env").write_text(
            environment.content, encoding="utf-8"
        )
        if environment.workload_content is not None:
            (args.output / f"workload-{environment.runner_name}.env").write_text(
                environment.workload_content, encoding="utf-8"
            )
    receipt = {
        "schema": bundle.schema,
        "policy_digest": bundle.policy_digest,
        "policy_document_sha256": bundle.policy_digest,
        "adapter": {
            "key": policy.adapter.key,
            "version": policy.adapter.version,
            "declaration_digest": policy.adapter.declaration_digest,
            "snapshot_digest": policy.snapshot_digest,
        },
        "capabilities": tuple(str(item) for item in policy.requirements.capabilities),
        "binding_digest": bundle.binding_digest,
        "binding_document_sha256": bundle.binding_digest,
        "nftables_binding": {
            "family": bundle.nftables_binding.family,
            "table": bundle.nftables_binding.table,
            "output_chain": bundle.nftables_binding.output_chain,
            "must_precede": bundle.nftables_binding.must_precede,
        },
        "squid_sha256": bundle.squid_sha256,
        "nftables_sha256": bundle.nftables_sha256,
        "runner_environment_sha256": {
            item.runner_name: item.sha256 for item in bundle.runner_environments
        },
        "workload_environment_sha256": {
            item.runner_name: item.workload_sha256
            for item in bundle.runner_environments
            if item.workload_sha256 is not None
        },
    }
    (args.output / "bundle.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0
