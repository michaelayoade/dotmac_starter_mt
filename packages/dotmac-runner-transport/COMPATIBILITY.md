# Compatibility

- Python 3.11 through 3.13.
- No runtime dependency outside the standard library.
- Adapter entry-point group: `dotmac.runner_transport.adapters`.
- Policy schema: `RunnerEgressPolicy.v1`.
- Host binding schema: `HostRunnerTransportSpec.v1`.
- Host bundle schema: `RunnerEgressBundle.v1`.
- Receipt schema: `RunnerTransportReceipt.v1`.

Provider snapshots contain exact DNS hosts. Raw wildcards, suffix grants, IP
literals, URLs, paths and non-HTTPS ports are refused in v1. A provider whose
contract cannot be expressed as exact names needs a new typed endpoint kind and
an enforcement mechanism that can prove it; it must not be approximated by a
cloud CIDR.

An adapter accounts for every raw snapshot member either as a capability
endpoint or as an explicit, digest-bound exclusion. Silent omission is refused,
as is advertising a partial functional group. `runner.update.v1` represents
the provider-neutral runner-binary update function; provider-specific update
hosts remain adapter data.

Host bindings name runner UIDs, a distinct positive proxy UID, loopback
resolver, unique listener ports, address-family posture and any exact direct
egress grants. They participate in the binding digest. The renderer emits a
regular nftables chain and an insertion rule; the owning host generator must
place the jump in its existing output chain before loopback and broad-set
accepts. V1 does not claim that a standalone hooked table can override another
base chain's terminal drop.

`binding.json` is canonical input evidence, not an opaque command-line digest.
A `RunnerTransportReceipt.v1` is valid only against the expected bundle and
runner name, including exact transport and workload environment digests; a
changed direct-grant bypass therefore invalidates the receipt even when all
lifecycle rows still say `executed_passed`.
