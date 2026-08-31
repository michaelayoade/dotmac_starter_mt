# dotmac-runner-transport

`dotmac-runner-transport` owns the provider-neutral contract between a
self-hosted CI runner and the network transport it needs. A provider adapter
declares exact endpoints for named capabilities. This facility validates and
canonicalises that declaration, binds it by digest, and renders host policy
without learning the provider's name or cloud implementation.

The core never fetches provider metadata and never carries credentials. A
snapshot is an adapter release input. Rendering is deterministic and can be
repeated without consulting the network.

Transport and workload egress are distinct authorities. A transport endpoint
is not permission for an arbitrary workflow destination, even when both use
HTTPS. Host rendering gives each runner its own loopback proxy listeners,
admits only separately typed direct workload grants, and refuses every other
network path for that runner identity. The proxy identity can resolve through
one declared loopback resolver and reach public HTTPS only; private, reserved,
numeric and IPv6 destinations are refused unless the host binding explicitly
declares a complete IPv6 posture.

The nftables output is an owned regular chain plus a digest-bound jump rule.
The host generator installs that jump before loopback and broad host accepts;
a separate early base chain would be incorrect because an `accept` verdict does
not bypass a later deny-by-default base chain. Squid logs only method,
destination host/port and disposition, never a request path, query or header.

The CLI emits `binding.json` as the canonical host intent: runner and proxy
identities, workload policies, direct grants, nftables placement and the
resolved policy digest. Completion evidence is accepted only against the
expected rendered bundle and named runner; the binding, Squid, nftables,
transport environment and workload environment digests are all load-bearing.
The caller separately supplies the expected source revision and provider-side
runner identity from the protected workflow. Acceptance takes retained bytes,
not preconstructed document objects; it parses each canonical document and
compares its own source commit and adapter identity plus the runner's
repository, display name and required labels. The document's typed verdict and
mutation flag must also equal its receipt row. Acceptance then re-hashes those
exact bytes, so an object, status or mutation claim paired with an invented
digest cannot pass.

Version `0.1.0a2` is the first admissible core candidate. The one-time local
`0.1.0a1` build from `5d6381d` was never published or tagged and is invalidated:
its receipt could accept invented evidence digests and did not bind the adapter
or provider runner coordinates. Reusing that version name for corrected bytes
would make one version identify two contracts. The unchanged GitHub Actions
adapter remains `0.1.0a1`; its declared `>=0.1.0a1` core range admits core a2.

This package is not enrolled in the facility release lane yet. Its first
adopter is VMID 124 only after two terminal provider-recorded diagnostic runs,
a cold-reboot repetition, and retirement of the observed-broker inventory.
