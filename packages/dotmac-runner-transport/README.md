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

This package is not enrolled in the facility release lane yet. Its first
adopter is VMID 124 only after two terminal provider-recorded diagnostic runs,
a cold-reboot repetition, and retirement of the observed-broker inventory.
