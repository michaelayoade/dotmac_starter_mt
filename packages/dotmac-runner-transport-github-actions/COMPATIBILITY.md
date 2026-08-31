# Compatibility

- Requires `dotmac-runner-transport >=0.1.0a1`.
- Adapter key: `github-actions`.
- Metadata source: `https://api.github.com/meta`.
- Selected field: `domains.actions_inbound.full_domains`.
- Capability source: GitHub's self-hosted runner `Accessible domains by
  function` contract.
- Snapshot profile: exact hosts only; no wildcard or CIDR fallback.

The v1 adapter supports only the complete functional groups declared in
`adapter.py`. In particular, it does not support `runner.packages.v1` while
`pkg-containers.githubusercontent.com` is absent, and it does not infer a
`runner.source-fetch.v1` group from hostname spelling. Raw snapshot members
outside supported groups remain explicit typed exclusions.

Adding or removing an endpoint, capability mapping, source response or semantic
snapshot changes a typed digest and requires a reviewed adapter release.
