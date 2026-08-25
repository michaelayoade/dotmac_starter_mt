# Compatibility

The stable surface is the names exported by `dotmac_positioning.__all__`.

Version `0.1.0a1` requires `dotmac-kernel >=0.1.0a71`, which allocates the
immutable `mod_pos` schema and `po` migration lineage. The module is tenant
plane only and requires `tenant_scope_catalog.v1` plus
`module_database_roles.v1` from its composing assembly.

All mutation functions participate in the caller's transaction and never
commit or roll back. Source, purpose, and context strings are open product
inputs; they are not centrally enumerated.

## Composition boundary

Every public operation requires an explicit `TenantScope`; the package has no
default tenant or platform bucket. Adopting products must also supply the
observation policy, collection purpose, receipt time, trail limit, retention
cutoff, and exact geofence selection when those choices apply. The package does
not read environment variables, settings, provider catalogues, product models,
or presentation configuration.

The remaining fixed values are protocol invariants, not product policy:

- geographic latitude and longitude validity bounds;
- circle and polygon geometry plus neutral entry and exit transitions;
- deterministic current-position ordering, spherical distance calculation, and
  SHA-256 replay fingerprints;
- finite storage bounds of 32 characters for normalized source/purpose codes,
  128 characters for opaque source/context references, and 64 hexadecimal
  characters for the replay fingerprint.

Source and purpose codes are trimmed and normalized to lowercase. Opaque source
and context references are trimmed but preserve case. Changing these invariants
is a versioned storage/contract change; operational thresholds and business
meaning remain caller-owned inputs.
