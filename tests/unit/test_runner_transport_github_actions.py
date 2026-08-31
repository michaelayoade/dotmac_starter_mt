"""The GitHub adapter owns its transitive provider details and drift."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "packages/dotmac-kernel/src"))
sys.path.insert(0, str(ROOT / "packages/dotmac-runner-transport/src"))
sys.path.insert(0, str(ROOT / "packages/dotmac-runner-transport-github-actions/src"))

from dotmac_runner_transport import (  # noqa: E402
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    derive_transport_policy,
)
from dotmac_runner_transport_github_actions import (  # noqa: E402
    ADAPTER,
    DOMAIN_NAMES,
    SNAPSHOT,
)
from dotmac_runner_transport_github_actions.adapter import (  # noqa: E402
    SELF_HOSTED_DOMAINS_BY_FUNCTION_SOURCE,
    _package_group_is_complete,
    _runner_update_group_is_complete,
)
from dotmac_runner_transport_github_actions.collector import (  # noqa: E402
    MetadataDrift,
    assert_current,
    parse_candidate,
)


def _document(domains: list[str]) -> bytes:
    return json.dumps(
        {"domains": {"actions_inbound": {"full_domains": domains}}}
    ).encode()


def _hosts_for(capability: RunnerTransportCapability) -> set[str]:
    return {
        endpoint.host.value
        for endpoint in ADAPTER.manifest.endpoints
        if endpoint.capability is capability
    }


def _exact_actions_hosts() -> set[str]:
    return {
        host for host in DOMAIN_NAMES if host.endswith(".actions.githubusercontent.com")
    }


def test_exact_snapshot_covers_broker_and_results_without_cloud_wildcard() -> None:
    assert tuple(sorted(DOMAIN_NAMES)) == DOMAIN_NAMES
    assert "run-actions-2-azure-eastus.actions.githubusercontent.com" in DOMAIN_NAMES
    assert "results-receiver.actions.githubusercontent.com" in DOMAIN_NAMES
    assert "productionresultssa19.blob.core.windows.net" in DOMAIN_NAMES
    assert not any("*" in domain for domain in DOMAIN_NAMES)
    endpoint_hosts = {endpoint.host for endpoint in ADAPTER.manifest.endpoints}
    excluded_hosts = set(ADAPTER.manifest.excluded_snapshot_domains)
    assert endpoint_hosts.isdisjoint(excluded_hosts)
    assert endpoint_hosts | excluded_hosts == set(SNAPSHOT.domains)


def test_results_policy_is_exact_and_cannot_become_a_cloud_grant() -> None:
    policy = derive_transport_policy(
        RunnerTransportRequirementsV1(
            (
                RunnerTransportCapability.ARTIFACTS_CACHE,
                RunnerTransportCapability.RESULTS,
            )
        ),
        ADAPTER,
    )
    hosts = {endpoint.host.value for endpoint in policy.endpoints}
    assert "results-receiver.actions.githubusercontent.com" in hosts
    assert "productionresultssa0.blob.core.windows.net" in hosts
    assert "core.windows.net" not in hosts


def test_official_self_hosted_functional_groups_are_exact() -> None:
    actions_hosts = _exact_actions_hosts()
    assert _hosts_for(RunnerTransportCapability.CONTROL) == {
        "api.github.com",
        "github.com",
        *actions_hosts,
    }
    assert _hosts_for(RunnerTransportCapability.OIDC) == actions_hosts
    assert _hosts_for(RunnerTransportCapability.ACTION_FETCH) == {"codeload.github.com"}
    results = {
        host
        for host in DOMAIN_NAMES
        if host == "results-receiver.actions.githubusercontent.com"
        or host.endswith(".blob.core.windows.net")
    }
    assert _hosts_for(RunnerTransportCapability.RESULTS) == results
    assert _hosts_for(RunnerTransportCapability.ARTIFACTS_CACHE) == results
    assert _hosts_for(RunnerTransportCapability.RELEASE_ASSETS) == {
        "release-assets.githubusercontent.com"
    }
    assert _hosts_for(RunnerTransportCapability.RUNNER_UPDATE) == {
        "github-registry-files.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "objects-origin.githubusercontent.com",
        "objects.githubusercontent.com",
    }


def test_functional_group_completeness_canaries_fail_in_both_directions() -> None:
    assert "#accessible-domains-by-function" in (SELF_HOSTED_DOMAINS_BY_FUNCTION_SOURCE)
    assert not _package_group_is_complete(set(DOMAIN_NAMES))
    assert _package_group_is_complete(
        {*DOMAIN_NAMES, "pkg-containers.githubusercontent.com"}
    )
    assert _runner_update_group_is_complete(set(DOMAIN_NAMES))
    assert not _runner_update_group_is_complete(
        set(DOMAIN_NAMES) - {"objects-origin.githubusercontent.com"}
    )


def test_incomplete_packages_and_source_contracts_refuse() -> None:
    assert RunnerTransportCapability.PACKAGES not in ADAPTER.manifest.capabilities
    assert RunnerTransportCapability.SOURCE_FETCH not in ADAPTER.manifest.capabilities
    with pytest.raises(ValueError, match="does not implement"):
        derive_transport_policy(
            RunnerTransportRequirementsV1((RunnerTransportCapability.PACKAGES,)),
            ADAPTER,
        )
    excluded = {item.value for item in ADAPTER.manifest.excluded_snapshot_domains}
    assert "ghcr.io" in excluded
    assert "npm.pkg.github.com" in excluded
    assert "hosted-compute-request-orchestrator-prod-eus-01.githubapp.com" in excluded


def test_candidate_drift_is_reported_and_never_auto_admitted() -> None:
    current = parse_candidate(_document(list(reversed(DOMAIN_NAMES))))
    assert current.semantic_sha256 == SNAPSHOT.semantic_sha256
    assert_current(current)
    changed = parse_candidate(_document([*DOMAIN_NAMES, "future.provider.invalid"]))
    assert [item.value for item in changed.added] == ["future.provider.invalid"]
    with pytest.raises(MetadataDrift, match="review"):
        assert_current(changed)


def test_removing_a_result_storage_endpoint_is_visible() -> None:
    changed = parse_candidate(
        _document(
            [
                item
                for item in DOMAIN_NAMES
                if item != "productionresultssa0.blob.core.windows.net"
            ]
        )
    )
    assert [item.value for item in changed.removed] == [
        "productionresultssa0.blob.core.windows.net"
    ]
