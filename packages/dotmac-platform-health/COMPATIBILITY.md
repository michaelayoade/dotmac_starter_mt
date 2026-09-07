# Compatibility — dotmac-platform-health

Version `0.2.0a1` requires `dotmac-kernel>=0.1.0a88`, which allocates
`mod_health` and migration prefix `ph`. The package is platform-only and is not
composed by the Starter reference assembly. Migration `ph_0002` leaves
acceptance-time freshness provenance nullable for legacy observations; it is
never inferred from current component policy. Rebuild and evidence production
refuse those observations until a newer observation records a positive
freshness snapshot.
