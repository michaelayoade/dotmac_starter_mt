# dotmac-platform-health

`dotmac-platform-health` owns platform-wide, provider-neutral runtime-health
observations, rebuildable latest-state projections and fresh/stale/missing
summaries. It stores bounded facts, not raw telemetry, and snapshots the
acceptance-time freshness policy on each new observation. Legacy observations
whose provenance is unknown are never backfilled or inferred: rebuild and
canonical evidence production refuse until a newer observation supplies a
positive snapshot.

Platform Health authors canonical `DeploymentHealthEvidence` and signs it
through the authoritative `produce_signed_health_evidence` service entry
point, using an assembly-supplied `HealthEvidenceSigner`. It owns no key
material, cryptography, provider transport or deployment decision.

Deployment Control still owns desired state and rollout decisions. Observability
systems retain metrics, logs and traces; Ticketing retains incident cases; the
Integrator retains transport, authentication and retry evidence.
