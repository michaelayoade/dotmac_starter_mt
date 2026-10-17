# Deployment enrollment admission source inventory — 2026-10-17

Status: source implementation draft only. No transport exposure, released-wheel
composition, runtime measurement, credential assignment, deployment or acceptance
proof is claimed. Releases remain paused.

## Qualified existing behavior

| Source snapshot | Existing owner and evidence | Disposition |
| --- | --- | --- |
| Sub c7707880232c9298ef89c24a1aea66d35bd3a1e6 | `app/api/integrator_observations.py`, `tests/test_integrator_observation_port.py` | Preserve authentication before owner work, scoped machine principal, binding resolution, frozen fingerprint collision refusal, provider signature never substitutes for Integrator authentication. No health protocol is extracted from the messaging/settlement commands. |
| ERP 93d7e111d9278b8377bac311e78308c99f51da79 | `app/api/integrator_observations.py`, `tests/api/test_integrator_observations.py` | Preserve service-principal organization binding, configured capability binding, strict schema and exact transactional identity replay. Invoice accounting remains ERP domain behavior. |
| Integrator ff6796aaa0d063b2d30327748e64627f0bccb394 | `src/dotmac_integrator` generic ProductObservation delivery | Existing generic v1 transport is the candidate transport owner. Starter retained v2 context is a different contract; version negotiation remains unresolved and no v1 fields or endpoint are added here. |
| Starter 62ecb6b0e198ba08bef9a096037194a9dfca4da0 | PH `contracts.py`, `service.py`, `evidence.py`; PH unit/isolation/upgrade tests; Integration held-key authentication and destination-binding seams | Extend the existing durable PH owner and replay fingerprint. Reuse Kernel `transactions.conflict_savepoint`; do not copy sibling authentication, transport, retry receipt or crypto implementations. |

## Owner additions

| Concept | Owner | Durable evidence |
| --- | --- | --- |
| Immutable public Control enrollment projection | Independent Control publisher; PH consumes an injected authority | Opaque candidate/configuration/runtime/credential evidence references, eligible standing and bounded aware eligibility interval. PH holds no accepting production verifier. |
| Authenticated health source | Existing transport authentication owner; PH consumes an immutable result | Installation, source, account, transport, credential generation and configuration bindings; caller payloads cannot supply enrollment projections. |
| Permanent installation grant and component reservation | PH `grants.py`, `models.py`, `ph_0003_enrollment_grants` | One immutable installation/enrollment per grant; globally unique component membership, retained even after closure; append-only closure. |
| Observation provenance and deployment selection | PH `service.py`, nullable observation `grant_id` | Historical rows remain NULL; exact grant and original fingerprint replay precedes current standing/closure/activity checks. Reserved selection excludes unbound evidence, and signed evidence requires one exact active permanent roster. |

Grant creation, legacy writes and authenticated writes serialize on the same
ordered component rows. Database uniqueness and Kernel conflict savepoints keep
expected conflict recovery inside the caller's transaction. Services only flush.
Reserved rebuilds validate current authority before deleting projections;
authority outage cannot reach the signer or manufacture a healthy summary.
Unreserved legacy summaries and historical legacy replay remain supported.
Unsigned deployment evidence reports unbound components as unknown/missing.
Canonical `DeploymentHealthEvidence.v1`, observation fingerprint encoding and
producer `received_at` remain unchanged and coordinate-free.

## Mandatory unresolved qualification

No inspected product implements independently verified runtime/configuration plus
credential-assignment proof for this deployment enrollment. A concrete trusted
producer and independent Control adapter must still be specified and reviewed.
`EnrollmentAuthority` is a required startup boundary, not evidence that this
producer exists. Missing authority refuses activation; missing evidence references
or ineligible standing refuses projection construction. Opaque references alone
are not authentication: an authority implementation must verify and join their
provenance to the exact candidate/configuration before returning a projection.

Generic v1/v2 negotiation and actual Integrator/CP public composition remain
unresolved. These are stop conditions for receiver exposure and production
enablement; this draft deliberately adds no HTTP route, network client, credential
format, scheduler, accepting verifier, Control persistence or CP composition.

Hosted tests have been authored for bindings, closure/replay, unbound contamination,
authority outage, exact signing roster, migrated PostgreSQL races, SQL immutability,
ACLs, old NULL provenance and downgrade refusal. Local verification is limited to
stdlib AST/TOML and pinned Ruff 0.6.9 static checks; no test success is claimed.
