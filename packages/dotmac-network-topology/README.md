# dotmac-network-topology

Tenant-only owner of declared and observed links, forwarding decisions,
resolved paths, reachability, coverage gaps, and rebuildable topology
projections. Inputs are typed opaque identity/observation references. It owns
neither device inventory, fiber physical state, incidents, nor commands.
Status: audit-complete `0.1.0a1` candidate; no adopter yet.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.
