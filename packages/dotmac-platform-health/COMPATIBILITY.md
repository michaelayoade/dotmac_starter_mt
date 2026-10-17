# Compatibility — dotmac-platform-health

Version `0.2.0a1` requires `dotmac-kernel>=0.1.0a88`, which allocates
`mod_health` and migration prefix `ph`. The package is platform-only and is not
composed by the Starter reference assembly. Migration `ph_0002` leaves
acceptance-time freshness provenance nullable for legacy observations; it is
never inferred from current component policy. Rebuild and evidence production
refuse those observations until a newer observation records a positive
freshness snapshot.


The source-edge helpers reuse `HealthObservationInput` and `HealthState`; no
second input DTO or state vocabulary is introduced. Labels are defensively
snapshotted while retaining their public `Mapping[str, str]` type. Serialize
commands with `health_observation_document`, which copies labels to a JSON
object. Service fingerprints retain their original encoding, including offset
ISO timestamps and the original `received_at`. Retries must retain the entire
command. The identity-only key helper intentionally makes payload changes under
one source observation identity conflict. Shared validation additionally checks
the existing storage widths (200/200/120) before persistence, without imposing
new blank-text or summary-length restrictions. All existing package public names
remain available; service and manifest names resolve lazily so contracts and
canonical evidence can be imported without persistence dependencies.
