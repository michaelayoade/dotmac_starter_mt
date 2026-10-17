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


Source adapters use the existing `HealthObservationInput` and `HealthState`
from the package root or `dotmac_platform_health.contracts`. Those imports and
the canonical evidence exports load no ORM, kernel models or service. Service
and manifest exports retain their public names and load when requested.

`validate_health_observation(command)` is the pure admission check also used by
`record_observation`: timezone-aware ordered timestamps; at most 20 string label
pairs, with keys at most 80 characters and values at most 160; source and
observation key at most 200 characters; component code at most 120. The existing
blank-text semantics remain; active-component registration and authenticated
source resolution are separate checks. Summary is text without an additional
length cap. Construction snapshots labels behind the public `Mapping[str, str]`
shape, so later changes to a collector's dictionary cannot change a retry.

`health_observation_document(command)` validates and returns the precise
`HealthObservationDocument` TypedDict with JSON values: the same eight fields,
string state, ISO timestamps retaining their offsets, and an independent labels
dictionary. Use this serializer for transport rather than dataclass deep-copy
helpers. It adds no transport envelope, authentication or signed evidence.

`health_observation_key(source_ref, component_code, source_observation_identity)`
returns `health-observation:v1:` followed by a SHA-256 digest. Its hash input is
that ASCII domain prefix followed by UTF-8 JSON of the three identity strings
in argument order, with ASCII escaping and compact separators. The source
identity must be nonblank and stable for one observation, such as a persisted
heartbeat sequence. Payload, state and receive time never enter the key.

Freeze the complete command, including `received_at`, when first collecting it,
and reuse that command and key on retries. The existing service fingerprint
retains sorted labels, timestamp offsets, `received_at`, state and summary;
changed content under the same identity raises `HealthConflict`. A new key must
represent a new source observation, not a changed payload. A key cannot prove
`source_ref` authenticity: Integrator remains the authenticated transport and
retry-evidence owner, and the assembly supplies its verified source identity.
