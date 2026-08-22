# dotmac-platform-health

`dotmac-platform-health` owns platform-wide, provider-neutral runtime-health
observations, rebuildable latest-state projections and fresh/stale/missing
summaries. It stores bounded facts, not raw telemetry.

Deployment Control still owns desired state and rollout decisions. Observability
systems retain metrics, logs and traces; Ticketing retains incident cases; the
Integrator retains transport, authentication and retry evidence.
