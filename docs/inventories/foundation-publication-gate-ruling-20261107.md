# Foundation publication gate — proposed ruling and evidence packet

Date: 2026-11-07. Status: PROPOSED; Michael has not ratified this ruling.
Michael is the sole developer and approver and holds the release-captain,
architect and Platform CP roles. Agent analysis supplies evidence, not approval.
This proposal authorizes no allocation, freeze, candidate build, publication,
provisioning, deployment, credential access or production operation.

## Decision presented to Michael

Approve replacing the retired Gate-0/Lane-3 broker/coordinator topology with
an adopted-path publication rehearsal. Preserve the existing build-once,
authorization, executor, recovery, exposure and receipt guarantees below.
OpenBao JWT establishes authenticated workflow identity using measured issuer
claims. Verified artifact signatures and SLSA provenance establish artifact
trust. Neither establishes deployment authority or executor behavior.

Keep the existing publication oracle refusing until the successor oracle and
producer satisfy the equivalent checks and their hosted sensitivity tests pass.
Do not delete the old gate first, admit null evidence, add an override, or
publish on a statement that the replacement will be implemented later.

Michael's acceptance of this proposal would settle the direction, not certify
the open evidence rows or authorize the release/deployment. A written ruling
must be recorded with its exact accepted revision before it is treated as
ratified. Alternate decision: retain the old topology, explicitly reconciling
that reversal with the October strategy decision. Neither option is assumed.

## Basis and corrections

Source snapshot: Starter `f6a3b9f0a6d7f11ede26b52efe463c65fcaec67d`, verified
against GitHub main on 2026-11-07; CP `bee5def11077eebc175f1f4688d9722e59481da3`
(origin/main, source evidence only). The active CP checkout is an older docs
branch and must not be used as the current implementation basis.

Authoritative coordination pointers: Knowledge
`dotmac-foundation-gate2-040a2-20261106` v5;
`dotmac-debt-d1-gate-0-protected-rehearsal-issuer-readiness` v2, whose
2026-11-04 preface says the old Gate-0 E receipt and Lane-3 broker/coordinator
were stopped; current operational history is
`dotmac-sub-keyless-cutover-recovery-2026-10-21`. Knowledge records pointers
and decisions; it does not manufacture or verify runtime evidence.

Corrections to the older Gate-2 narrative:

1. The current producer is v2. `scripts/exposure_rehearsal_runner.py:1999`
   calls `lane3_receipt_v2.assemble_receipt`, which calls Foundation's
   `build_receipt_v2` (`scripts/lane3_receipt_v2.py:274`). The blocker is not
   a v1 writer: the trusted plan/authority/executor provider path refuses
   (`scripts/lane3_receipt_v2.py:308-339`). A v2 contract is not a successful
   v2 rehearsal.
2. Do not presume a dynamic `plan_digest` claim exists in GitHub's JWT.
   The implementation must enumerate actual issuer claims and approved
   OpenBao bound-claim policy. The CP/Control signed authorization binds the
   exact Foundation canonical execution-plan digest; JWT identity does not
   replace that chain.
3. Do not recreate the stopped Gate-0 E programme as an allocation condition.
   Reconcile the five committed keyless staging proofs, machine PASS/FAIL,
   ledger evidence and required next-morning rerun of proofs 2-4 with the
   adopted path. Their complete, current acceptance is not asserted here.
4. Sub-thin candidate backup/restore and production-mode health evidence are
   consumer readiness evidence. They do not establish the facility's complete
   real recovery sequence, generic admission or publication rehearsal.

## Guarantees retained and how the replacement must fail

These are required implementation acceptance criteria, not statements that
replacement machinery already exists. Keep all sixteen required rehearsal
items and the v2 contract; a future change to that public contract requires
its own explicit decision and any Foundation-byte change before freeze.

| Guarantee | Required evidence / owner | Required refusal |
| --- | --- | --- |
| Build once, promote exact bytes | CandidateArtifact.v1 with wheel AND sdist coordinates/digests; existing fetch, inspect, byte recheck and registry readback (Starter release lane) | Spent version, unavailable/expired candidate, mismatched archive or rebuild attempt |
| Protected source and distinct revisions | Existing protected-main freshness, candidate ancestry to runner and release revisions, runner equal to release revision | Divergent ancestry, shallow/unknown history, stale release tip or wrong runner revision |
| Workflow identity and least privilege | Enumerated issuer/audience/subject/claim contract and approved OpenBao JWT policy; authenticated run/ref/repository identity | Missing/wrong issuer, audience, claim, repository/workflow/ref/SHA; no credential or effect before verification |
| Artifact trust | Cryptographically verified signatures AND SLSA provenance covering both exact archive digests, source/builder/run coordinates, separately approved trust roots/identity policy | Missing signature/provenance, wrong subject/builder/source, untrusted/revoked identity or unknown standing; uploaded JSON alone never passes |
| Exact execution authorization | CP-held Foundation plan bytes/digest; genuine Control V2 pair, current standing, single-use consumption and owned approval subject | Caller text, wrong plan/target/operation/artifact, stale/revoked approval, replay or ambiguous un-reconciled consumption |
| Apply under one lock | Public Executor driven in-process using the trusted plan, exact pre-observation and installed admission providers | Lock contention, absent/wrong provider, mismatched observation or unadmitted artifact |
| Exposure truth | Real outside/inside observations for declared source sets and both required address families, private evidence held securely | Host-only claim, inert-chain containment, privileged/wrong vantage, unavailable measurement or vacuous service state |
| Failure-driven compensation | Genuine failed verification provokes Executor compensation with nonempty foreign-rule preservation evidence | Direct rollback call substituted for provocation, no failure induced, missing restoration or discarded failed outcome |
| Recovery | Real required recovery sequence with role closure and effective-privilege checks, per-step outcomes and exact-image readiness | Partial restore, missing role/membership/privilege, fabricated role declaration, failed step hidden by later success or unavailable readiness |
| Receipt and run identity | Candidate-installed v2 parser; newest matching run selected THEN checked; receipt from that run/attempt; descriptor-plan-grant-outcome chain and candidate digest | Older green masking newer failure; swapped artifact/run/attempt, v1 receipt, partial/blocked/hand-measured/vacuous item or tampered link |
| Evidence lifetime and disclosure | Refetchable retained artifact, observed expiry/margin, opaque public references and protected evidence delivery | Expired/missing evidence, guessed retention, leaked topology/materials or unverified delivery |

Existing checks are in `.github/workflows/release-facility.yml:124-129`
(main tip), `:181-219` (candidate fetch/verify), `:241-302` (isolated
candidate interpreter and rehearsal oracle), `:324-329` (ancestry),
`:380-439` (credential-side freshness/binding), and `:473-569` (registry
readback before tag). The successor must retain these properties.
`require_rehearsal.py` selects and reads one run; the new oracle must not
split selection and artifact retrieval across independently chosen runs.

## Pre-freeze certification worksheet — none certified by this document

| Question / Rule 49 gap | Source observation | Still required |
| --- | --- | --- |
| Q1: public Executor sufficient for apply and provocation? | `scripts/lane3_provocation.py:10-17` identifies compensation behind authorized V3 Executor; runner's apply and provocation rows explicitly remain blocked (`exposure_rehearsal_runner.py:1688-1729,1934-1967`) | Michael's source-backed answer plus executable positive path/refusal proof; any missing Foundation API lands before freeze |
| Q2: CP constructs both executors with its provider? | CP V3 pair/finalizer exists (`src/vendor_cp/deployment/host_admission_adapter.py:490-588,676-704`); no app/runner installation observed at this snapshot | Declared installed-artifact entrypoint, real composition into both executor bindings, accepted published Foundation dependency and positive admission evidence |
| Exposure ceases to be second execution authority | Dossier still lists it in `packages/dotmac-deployment-foundation/EXTRACTION.toml:154` | Enumerate all mutating entrypoints; prove effects flow only through authorized Executor and plant a bypass refusal |
| Dispatcher retains exact pre-observation and authorized plan | Required by dossier and canonical plan contract; no integrated execution proof supplied | Retain/hash exact held documents across approval and execution; changed bytes must refuse |
| One caller-held lock covers every mutation | Requirement exists; blocked runner cannot demonstrate it | Real executor composition/provocation with lock held throughout; contention and bypass tests |
| Control-owned rehearsal/provocation grant | Producer authority acquisition refuses; no caller digest may substitute | Genuine signed/consumed Control pair for exact plan, operation and target, including failure exercise |
| HostSource binds its own artifact digest | CP adapter has host-admission provider seam | Independently observed installed artifact/interpreter binding through genuine provider; wrong artifact/host must refuse |
| Generic profile admission through installed artifacts | Source verifier/ports are insufficient for positive adoption | Installed provider distribution metadata and complete concern bindings; no checkout path substitution |
| CP approval subject / barrier | `DispatchApprovalSubjectUnavailable` explicitly names the missing FOUNDATION_EXECUTION subject (`host_admission_adapter.py:66-80`); dispatch refuses approval-required plans (`:559-588`) | Own and wire subject/approval hold; withdrawal, expiry, replay and ambiguity handling. Do not set requires_approval=false to make conformance pass |
| Adopted D1 operational proof | Historical record explicitly retires old E sequence | Addressable current five-proof outcomes, required rerun and ledger acceptance; no operational action authorized by this packet |

A CP relay runner is the platform-outbox runner, not a Foundation executor.
CP `docs/ARCHITECTURE.md:625-639` says Foundation is not an application
dependency and factory wiring waits for published/pinned adoption.
`conformance/test_host_admission_conformance.py:1511-1562` proves an expected
approval refusal, not successful execution. Do not mark these gaps closed on
unit doubles or on a pin alone. Open rows may require Foundation changes;
"composition only" remains a hypothesis until the worksheet is certified.

## Ordered implementation after direction is accepted

1. Michael records the gate direction and accepted packet revision. Add the
   corresponding dated CP contract amendment where ownership changes; Starter
   prose cannot amend CP's authority or invent missing deployment evidence.
2. Close the source worksheet with bounded CP/Control/Foundation changes.
   First CP slice: the owned execution approval subject and actual installed
   executor bindings. Retain exact plan/pre-observation and ambiguity recovery;
   do not introduce an unconditional approving provider. Keep source readiness
   candidate-independent to avoid the original bootstrap cycle.
3. Implement the adopted-path runner/receipt producer and publication oracle,
   actual trust roots/claim contract and signature/provenance checks. Record
   provisioning values privately; host/credential changes have separate scope.
   Migrate all old topology dependencies together, including guards and owning
   rules. The current gate stays closed until replacement admission is proved.
4. Run hosted tests over the merge result, with positive admission and separate
   planted refusals for each row. Do not run tests/dependency installs locally
   or on fleet test hosts; Starter AGENTS.md requires Git-hosted CI.
5. Certify source and candidate-independent readiness. Only then freeze every
   candidate input, allocate 0.4.0a2 and build once, commit the artifact record,
   then sign/record provenance in the accepted non-circular order. Preserve the
   spent a1 identity and dispositions; no receipt is rewritten.
6. Rehearse those exact bytes at the later release revision, satisfy the
   equivalent admitted publication oracle, publish without rebuilding, verify
   both archives from Forgejo, install outside checkout, tag the candidate
   source, merge truthful release record and end the named freeze.
7. Separately accept Sub-thin first-deploy recovery and CP execution readiness,
   then authorize a named governed deployment with fresh backup and final
   product domain bindings. No brand flip or telemetry enablement follows
   automatically. No host has been changed by this proposal.

Hosted validation targets for the implementation include
`tests/architecture/test_deployment_release_lane.py`,
`tests/architecture/test_lane3_execution_oracle.py`,
`tests/architecture/test_lane3_candidate_artifact_execution.py`,
`tests/unit/test_lane3_runner_receipt_v2.py`,
`tests/unit/test_lane3_receipt_v2.py`, and the source/authorization tests
owned by each changed component. Merely rerunning current tests cannot prove
new claim/signature/runner semantics; add sensitivity cases with the change.

## Requested ruling and remaining dates

Ruling: 2026-11-13. Certifications: 2026-11-18. Escalation/reminder to Michael:
2026-11-20. These dates do not schedule notifications or permit early allocation.

Recommended wording for Michael's explicit decision:

> Approve the adopted-path direction in this packet, preserving its guarantees.
> Retire the old broker/coordinator topology as a publication prerequisite once
> its equivalent oracle is implemented and admitted. Keep all allocation,
> publication and deployment holds until their own evidence gates pass.

This wording is a proposed decision for review, not a recorded approval.
