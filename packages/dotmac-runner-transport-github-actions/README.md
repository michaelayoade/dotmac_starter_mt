# GitHub Actions runner-transport adapter

This package maps provider-neutral runner capabilities to GitHub's published
exact `domains.actions_inbound.full_domains` contract. Cloud storage names in
that contract are transitive GitHub implementation data, not a Dotmac cloud
dependency. The shared facility never sees or branches on them.

Capability meaning comes from GitHub's separate official
`Accessible domains by function` contract, not from substrings that happen to
appear in a hostname. The adapter intersects those functional groups with the
pinned exact snapshot:

- control: `github.com`, `api.github.com`, and every exact snapshot member of
  `*.actions.githubusercontent.com`;
- OIDC: every exact snapshot member of `*.actions.githubusercontent.com`;
- action downloads: `codeload.github.com`;
- results, logs, artifacts and caches: the exact results receiver and exact
  `*.blob.core.windows.net` snapshot members;
- runner updates: all four documented update hosts, only as a complete group;
- release assets: `release-assets.githubusercontent.com`.

`SOURCE_FETCH` is not advertised because GitHub does not publish a complete
source-fetch functional group. `PACKAGES` is also refused: the snapshot has
`ghcr.io` and exact `*.pkg.github.com` members but lacks the documented
`pkg-containers.githubusercontent.com` member. Partial groups are not useful
capabilities. The eighteen broad-metadata observations outside supported
groups are a reviewed, digest-bound exclusion set; a new observation fails
construction until somebody maps or explicitly excludes it.

The adapter snapshot is checked in and digest-bound. `collector.py` validates a
fresh `https://api.github.com/meta` response offline; it does not update the
snapshot or render policy. A scheduled, credential-free hosted job may propose
an ordinary reviewed snapshot update. Fetch failure or semantic drift retains
the last working release and fails loudly.

No wildcard or cloud CIDR is admitted. The exact profile is larger than the
old observed-host list by design: observation is not authority, and a new
broker shard must not require an emergency firewall edit to upload a job log.
