from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_runner_transport import (
    RunnerTransportCapability,
    RunnerTransportRequirementsV1,
    derive_transport_policy,
)
from dotmac_runner_transport_github_actions import ADAPTER, DOMAIN_NAMES, SNAPSHOT
from dotmac_runner_transport_github_actions.adapter import (
    SELF_HOSTED_DOMAINS_BY_FUNCTION_SOURCE,
    _classify,
    _package_group_is_complete,
    _runner_update_group_is_complete,
)


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


def test_snapshot_is_exact_sorted_and_contains_current_result_path() -> None:
    assert tuple(sorted(DOMAIN_NAMES)) == DOMAIN_NAMES
    assert not any("*" in host for host in DOMAIN_NAMES)
    assert "results-receiver.actions.githubusercontent.com" in DOMAIN_NAMES
    assert "productionresultssa0.blob.core.windows.net" in DOMAIN_NAMES
    assert "run-actions-2-azure-eastus.actions.githubusercontent.com" in DOMAIN_NAMES


def test_every_snapshot_domain_is_classified_or_explicitly_excluded() -> None:
    endpoint_hosts = {item.host for item in ADAPTER.manifest.endpoints}
    excluded_hosts = set(ADAPTER.manifest.excluded_snapshot_domains)
    assert endpoint_hosts.isdisjoint(excluded_hosts)
    assert endpoint_hosts | excluded_hosts == set(SNAPSHOT.domains)


def test_an_unknown_provider_domain_is_not_silently_called_control() -> None:
    with pytest.raises(ValueError, match="no reviewed capability"):
        _classify("future.provider.invalid")


def test_results_capability_is_not_cloud_wide() -> None:
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
    assert "productionresultssa19.blob.core.windows.net" in hosts
    assert not any("*" in host for host in hosts)
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
    result_hosts = {
        host
        for host in DOMAIN_NAMES
        if host == "results-receiver.actions.githubusercontent.com"
        or host.endswith(".blob.core.windows.net")
    }
    assert _hosts_for(RunnerTransportCapability.RESULTS) == result_hosts
    assert _hosts_for(RunnerTransportCapability.ARTIFACTS_CACHE) == result_hosts
    assert _hosts_for(RunnerTransportCapability.RELEASE_ASSETS) == {
        "release-assets.githubusercontent.com"
    }
    assert _hosts_for(RunnerTransportCapability.RUNNER_UPDATE) == {
        "github-registry-files.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "objects-origin.githubusercontent.com",
        "objects.githubusercontent.com",
    }


def test_official_group_canaries_are_load_bearing() -> None:
    assert "#accessible-domains-by-function" in (SELF_HOSTED_DOMAINS_BY_FUNCTION_SOURCE)
    assert not _package_group_is_complete(set(DOMAIN_NAMES))
    assert _package_group_is_complete(
        {*DOMAIN_NAMES, "pkg-containers.githubusercontent.com"}
    )
    assert _runner_update_group_is_complete(set(DOMAIN_NAMES))
    assert not _runner_update_group_is_complete(
        set(DOMAIN_NAMES) - {"objects.githubusercontent.com"}
    )


def test_packages_and_source_fetch_are_not_advertised_without_a_complete_contract() -> (
    None
):
    assert RunnerTransportCapability.PACKAGES not in ADAPTER.manifest.capabilities
    assert RunnerTransportCapability.SOURCE_FETCH not in ADAPTER.manifest.capabilities
    with pytest.raises(ValueError, match="does not implement"):
        derive_transport_policy(
            RunnerTransportRequirementsV1((RunnerTransportCapability.PACKAGES,)),
            ADAPTER,
        )
    excluded = {item.value for item in ADAPTER.manifest.excluded_snapshot_domains}
    assert "ghcr.io" in excluded
    assert "containers.pkg.github.com" in excluded
    assert "hosted-compute-request-orchestrator-prod-eus-01.githubapp.com" in excluded


def test_provider_names_exist_only_in_the_adapter_distribution() -> None:
    core = Path(__file__).parents[2] / "dotmac-runner-transport" / "src"
    core_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(core.rglob("*.py"))
    ).lower()
    assert "github" not in core_text
    assert "azure" not in core_text
