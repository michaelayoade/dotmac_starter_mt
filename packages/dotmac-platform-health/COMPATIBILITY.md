# Compatibility — dotmac-platform-health

The enrollment-admission source draft requires `dotmac-kernel>=0.1.0a98`, the
first published release containing the engine-free public
`dotmac_kernel.transactions.conflict_savepoint` used by grant and receipt
writers. Namespace allocation remains at `0.1.0a88`, which allocates `mod_health`
and migration prefix `ph`. No package version is allocated by this source draft.
The package is platform-only and is not
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

Enrollment-bound writers require `HealthAdmission` constructed with a startup
authority. There is no accepting production authority implementation. The
authority must verify independent runtime and credential-assignment evidence
against the exact Control enrollment before returning its immutable public
projection. Transport authentication supplies `AuthenticatedHealthSource` on
every request; neither value belongs in request JSON. New component reservation
and provenance are retained by `ph_0003_enrollment_grants`; historical observations
remain unbound. Exact same-grant fingerprint replay survives closure and current
authority outage, after checking immutable authenticated identity and membership.

Legacy unreserved observation/summary behavior remains. Reserved summaries and
rebuilds require authority and select only exact-grant observations. Unsigned
deployment evidence reports unbound components as unknown/missing; deployment
signing now requires current authority and one exact active permanent grant
roster. Existing callers of signing must supply this boundary explicitly.
`DeploymentHealthEvidence.v1`, canonical serialization and frozen observation
fingerprints/timestamps remain unchanged. Populated downgrade is refused;
rollback disables exposure and retains immutable evidence.

Renewable health eligibility is separate from permanent enrollment identity.
`EnrollmentProjection.identity_document()` excludes only `eligible_from`,
`eligible_until` and `standing`; all installation, configuration, candidate,
source/account/transport/credential, roster and baseline evidence/authority
references stay immutable. The complete initial projection and its original
fingerprint remain retained without rewriting. The required injected authority
now also supplies `HealthEligibilityAssessment` through `health_eligibility`:
a durable assessment reference, enrollment and identity digest, assessed time,
bounded owner-provided validity, standing, and current runtime/credential
evidence and authority references. There is no default producer or PH TTL.

`ph_0003` retains exact assessments append-only and links each newly accepted
bound observation to its assessment and grant. Fresh operations always consult
the authority; an old snapshot never bypasses closure, revocation, expiry or an
outage. Exact authenticated historical receipt replay retains its existing
semantics. Evidence validity is bounded by the current assessment and immutable
observation freshness deadline. The original admission assessment remains audit
provenance; its past expiry does not cap renewed evidence. Its v1 schema and
canonical encoding remain unchanged. Execution-token expiry does not itself end
an unchanged runtime's health eligibility when the health authority supplies
fresh evidence; this supplies no execution or token reconsumption permission.
Released `ph_0001` and `ph_0002` remain unchanged. This source draft is not a
qualified producer or release authorization.
