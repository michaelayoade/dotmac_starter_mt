# dotmac-fiber-plant

Tenant-only owner of outside-plant structures, cables, strands, splices,
terminations, continuity, field evidence, and controlled as-built changes. It
does not own projects, work orders, tickets, customers, generic device
inventory, provider GIS clients, or notification transport. Status:
audit-complete `0.1.0a1` candidate; no adopter yet.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.
