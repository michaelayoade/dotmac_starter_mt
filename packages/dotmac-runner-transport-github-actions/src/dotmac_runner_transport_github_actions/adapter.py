"""GitHub-specific domain classification behind the provider-neutral SPI."""

from __future__ import annotations

from dotmac_runner_transport import (
    RunnerTransportAdapterManifest,
    RunnerTransportCapability,
    TransportEndpointV1,
)

from .snapshot import SNAPSHOT

__all__ = ["ADAPTER", "GitHubActionsAdapter"]


SELF_HOSTED_DOMAINS_BY_FUNCTION_SOURCE = (
    "https://docs.github.com/en/actions/reference/runners/"
    "self-hosted-runners#accessible-domains-by-function"
)

_SNAPSHOT_HOSTS = {name.value for name in SNAPSHOT.domains}
_ACTIONS_HOSTS = {
    host for host in _SNAPSHOT_HOSTS if host.endswith(".actions.githubusercontent.com")
}
_CONTROL_HOSTS = {"api.github.com", "github.com", *_ACTIONS_HOSTS}
_OIDC_HOSTS = set(_ACTIONS_HOSTS)

_ACTION_HOSTS = _SNAPSHOT_HOSTS & {"codeload.github.com"}
_RESULT_HOSTS = {
    host
    for host in _SNAPSHOT_HOSTS
    if host == "results-receiver.actions.githubusercontent.com"
    or host.endswith(".blob.core.windows.net")
}
_RELEASE_HOSTS = _SNAPSHOT_HOSTS & {"release-assets.githubusercontent.com"}
_RUNNER_UPDATE_REQUIRED_HOSTS = {
    "github-registry-files.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "objects-origin.githubusercontent.com",
    "objects.githubusercontent.com",
}


def _runner_update_group_is_complete(domains: set[str]) -> bool:
    return _RUNNER_UPDATE_REQUIRED_HOSTS <= domains


_RUNNER_UPDATE_HOSTS = (
    _RUNNER_UPDATE_REQUIRED_HOSTS
    if _runner_update_group_is_complete(_SNAPSHOT_HOSTS)
    else set()
)

_OBSERVED_PACKAGE_HOSTS = {
    name.value
    for name in SNAPSHOT.domains
    if name.value == "ghcr.io" or ".pkg.github.com" in name.value
}
_PACKAGE_GROUP_REQUIRED_EXACT_HOSTS = {
    "ghcr.io",
    "pkg-containers.githubusercontent.com",
}


def _package_group_is_complete(domains: set[str]) -> bool:
    """Apply GitHub's three-part Packages functional-group requirement."""

    return _PACKAGE_GROUP_REQUIRED_EXACT_HOSTS <= domains and any(
        host.endswith(".pkg.github.com") for host in domains
    )


_PACKAGE_GROUP_COMPLETE = _package_group_is_complete(_SNAPSHOT_HOSTS)
_PACKAGE_HOSTS = _OBSERVED_PACKAGE_HOSTS if _PACKAGE_GROUP_COMPLETE else set()

# GitHub's broad metadata includes provider observations outside the supported
# self-hosted functional groups. This reviewed set is deliberately explicit:
# a new snapshot name must force a mapping or exclusion decision, never fall
# automatically into a computed remainder.
_EXCLUDED_SNAPSHOT_HOSTS = {
    "cafe.github.com",
    "containers.pkg.github.com",
    "docker-proxy.pkg.github.com",
    "docker.pkg.github.com",
    "ghcr.io",
    "hosted-compute-request-orchestrator-prod-eus-01.githubapp.com",
    "hosted-compute-request-orchestrator-prod-eus-02.githubapp.com",
    "hosted-compute-request-orchestrator-prod-iad-01.githubapp.com",
    "hosted-compute-request-orchestrator-prod-iad-02.githubapp.com",
    "maven.pkg.github.com",
    "npm-beta-proxy.pkg.github.com",
    "npm-beta.pkg.github.com",
    "npm-proxy.pkg.github.com",
    "npm.pkg.github.com",
    "nuget.pkg.github.com",
    "pypi.pkg.github.com",
    "rubygems.pkg.github.com",
    "swift.pkg.github.com",
}


def _classify(host: str) -> tuple[RunnerTransportCapability, ...]:
    if host not in _SNAPSHOT_HOSTS:
        raise ValueError(f"GitHub snapshot host has no reviewed capability: {host}")
    if host in _EXCLUDED_SNAPSHOT_HOSTS:
        return ()
    capabilities: set[RunnerTransportCapability] = set()
    if host in _CONTROL_HOSTS:
        capabilities.add(RunnerTransportCapability.CONTROL)
    if host in _PACKAGE_HOSTS:
        capabilities.add(RunnerTransportCapability.PACKAGES)
    if host in _RESULT_HOSTS:
        capabilities.update(
            {
                RunnerTransportCapability.RESULTS,
                RunnerTransportCapability.ARTIFACTS_CACHE,
            }
        )
    if host in _RELEASE_HOSTS:
        capabilities.add(RunnerTransportCapability.RELEASE_ASSETS)
    if host in _ACTION_HOSTS:
        capabilities.add(RunnerTransportCapability.ACTION_FETCH)
    if host in _OIDC_HOSTS:
        capabilities.add(RunnerTransportCapability.OIDC)
    if host in _RUNNER_UPDATE_HOSTS:
        capabilities.add(RunnerTransportCapability.RUNNER_UPDATE)
    if not capabilities:
        raise ValueError(f"GitHub snapshot host has no reviewed capability: {host}")
    return tuple(sorted(capabilities, key=str))


_ENDPOINTS = tuple(
    sorted(
        TransportEndpointV1(capability=capability, host=host)
        for host in SNAPSHOT.domains
        for capability in _classify(host.value)
    )
)
_IMPLEMENTED_CAPABILITIES = tuple(
    sorted({endpoint.capability for endpoint in _ENDPOINTS}, key=str)
)


class GitHubActionsAdapter:
    """Immutable adapter singleton exposed through package metadata."""

    manifest: RunnerTransportAdapterManifest = RunnerTransportAdapterManifest(
        key="github-actions",
        version="0.1.0a1",
        capabilities=_IMPLEMENTED_CAPABILITIES,
        endpoints=_ENDPOINTS,
        snapshot=SNAPSHOT,
        excluded_snapshot_domains=tuple(
            sorted(
                name
                for name in SNAPSHOT.domains
                if name.value in _EXCLUDED_SNAPSHOT_HOSTS
            )
        ),
    )


ADAPTER = GitHubActionsAdapter()
