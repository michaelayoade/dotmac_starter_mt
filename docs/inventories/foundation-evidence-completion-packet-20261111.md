# Foundation evidence completion packet — 2026-11-11

Status: prepared source/evidence plan; not an operational authorization or a
certification. Michael owns the release-captain, architect, CP and approval
roles. No other person is required. Separate signing purposes and service
principals remain separate even with one human owner.

## Deliverable and verified baseline

Release Foundation `0.4.0a2` once through the governed release lane, then deploy
Sub-thin on the selfcare server (`94.72.107.76`) with fresh backup and the
accepted explicit first-deploy recovery procedure. `81.0.221.52` remains a
rehearsal host. Legacy Sub remains the production writer until the cutover.
No billing work in legacy Sub, telemetry enablement or brand flip is included.

The source sequence #1027, #1029, #1034 and #1035 is merged. PR #1035 merged
as `669bc2fbec797e5ef48521061829f5fbee6622c9`, all 34 checks green. It closes
late producer ordering and private artifact transport, not complete proof.
Source basis for this packet: Starter
`a9b777d6caa5aead4f89a767a9118508958deeb7`. CP main was measured as
`bf7893c75b524c61eaf5a2fe4c083fecc70234ab`; its current source was verified read-only from the fetched commit, not the
stale active docs checkout. The canonical program record is Knowledge
`dotmac-foundation-gate2-040a2-20261106`.

`0.4.0a1` remains spent. `0.4.0a2` remains unallocated. This packet changes
no Foundation package bytes, version identity, candidate receipt or trust pin.

## First close transport qualification

The `lane3-evidence-crypto` hosted job downloads the official age v1.3.2
Linux/amd64 archive and verifies its committed SHA-256 before extracting and
installing either executable. The checksum is from the upstream release asset:
`cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10`.
[Upstream release](https://github.com/FiloSottile/age/releases/tag/v1.3.2).
This is a test-tool coordinate, not a production tool-profile enrollment.

In a disposable GitHub-hosted job, the standalone qualification generates
short-lived synthetic identities, invokes the actual fixed adapter and actual
age decryptor, and checks exact archive members, bytes and manifest joins.
Wrong identity, altered ciphertext, wrong executable pin, malformed recipient
and output reuse must refuse. Keys remain in a private temporary fixture and
are deleted on exit. The job has no secret, OIDC, deployment or publication
capability and retains no private artifacts. The existing required Docker job
depends on this qualification. Local execution is forbidden.

A green result proves transport interoperability for this source/tool pair.
It is not production custody, complete behavioral proof, or admitted rehearsal.

## Evidence contract to complete before the private result can exist

The current V1 carrier permits exactly `probe-evidence.json` and
`exposure-calls.json`, plus their private manifest. It cannot silently gain
new evidence members. Its completeness join does not establish exact rules,
foreign preservation, failed verification or compensation. No full offline
result may be signed from that carrier alone.

| Required held evidence | Existing owner and independent check | Missing completion |
| --- | --- | --- |
| Exact candidate archive identities | Committed `CandidateArtifact.v1`; protected release acquisition, digest checks and recorded file installation | Candidate-independent source readiness first; allocate/build only after certification, then use the one recorded artifact |
| Execution plan and descriptor | CP-rendered concrete `FoundationExecutionPlanV3`; Foundation canonical bytes/digest, descriptor digest and exact target/operation | Trusted offline acquisition of those same held bytes; V3 has no document parser that itself confers trust |
| Control authorization and dispatch | Existing signed V2 pair with exactly `authorization_material` and `dispatch_material`; trusted CP verifier and current standing | Real approved pair and authenticated presentation/execution-context documents for each target |
| Committed single-use consumption | Control finalizer plus CP `lookup_committed(control_consumption_ref=...)` | Retained owner readback of the exact committed dispatch marker; never reconsume or infer success from an exception |
| Execution outcome | Existing `DeploymentOutcome.as_evidence()` emits `DeploymentEvidenceV2` when `control_consumption_ref` is present | Private retention and independent joins to the exact plan, target, operation, artifact, committed consumption and failed/successful steps |
| Executor boundaries | Actual `ExposureCallRecord` sequence, arguments, observations and exceptions | Exact descriptor-derived rule equality, nonempty foreign-rule preservation, and genuine verification-failure-to-compensation joins |
| Recovery | Foundation recovery owner, role closure/effective privilege observations, each required step and final image readiness | Real exact-candidate recovery evidence; Sub database restore rehearsal alone does not discharge it |
| Run and retained artifact | Existing newest-run-then-check oracle and receipt run/attempt binding | Exact retained ciphertext retrieval and observed lifetime; digest verified before decryption |

The next private-carrier extension must be explicitly versioned and arrive
with its real owner producers and independent consumers. Preserve existing
Foundation V3 canonical bytes and `DeploymentEvidenceV2`; do not invent another
outcome schema or serialize/reconstruct a grant as execution authority. An
archive/decryption helper alone cannot close this table.

## CP composition and the two-target exercise

CP's accepted approval source owns subject `foundation_execution_plan.v1`,
binding the real Control plan UUID, purpose, deploy/rollback operation and
execution-plan digest. `compose_approved_foundation_v3_providers` uses the real
Approvals owner and held transition barrier. That source is not installed
runtime composition or a produced approval.

Current CP source already declares the one execution-binding entry point
`dotmac-control-plane = vendor_cp.deployment.foundation_bindings:bindings`.
The factory composes the approval-guarded pair and returns the admission and
V3 authorization providers. No additional provider wrapper is required.
CP intentionally has no Foundation application dependency; the isolated
executor environment must contain both accepted distributions explicitly.
The isolated rehearsal environment still needs the actual CP provider/binding
coordinate, its required dependencies and a genuine Control V2 pair.

The exact fixed startup inputs in current CP source are `DATABASE_URL`,
`PLATFORM_DATABASE_URL`, `HOST_ADMISSION_MATERIALS` and
`EXECUTION_CONTEXT_MATERIALS`. The last two name private document paths,
not JSON text or selectors for a trust root. The host-admission document closes
over `attempt_id`, `presentation`, `candidate` and `installed`: a UUID attempt,
Control's unmodified presentation serialization and Foundation
`AttestationEnvelopeV2` candidate/installed envelopes. The context document's
key set must equal the installed `ExecutionContextV3` fields exactly. Observers
validate at startup and reread per observation. The launcher-side authenticated
producer/depositor is still missing; do not substitute an operator-authored
fixture or publish these private documents.

Current approval standing has no standalone Control getter in this composition.
Approvals' `hold_approval` checks the exact subject/frozen digest under
`approval_barrier.held_transition` with its row lock held through Control's
finalizer and the caller's commit. Separately,
`lookup_foundation_execution_consumption`, exposed by CP's
`lookup_committed(control_consumption_ref=...)`, reads the committed marker
without reconsuming. Its reference must equal `control-dispatch:<dispatch_id>`.
A cached approval status and a post-crash consumption lookup prove different
facts; neither replaces the other.

Source owners at the measured CP revision:
`src/vendor_cp/deployment/foundation_bindings.py`,
`foundation_execution_approval.py`, `host_admission_adapter.py` and
`approval_barrier.py`, plus `src/vendor_cp/approvals/adapter.py`. The
[fixed binding factory](https://github.com/michaelayoade/dotmac_platform_control_plane/blob/bf7893c75b524c61eaf5a2fe4c083fecc70234ab/src/vendor_cp/deployment/foundation_bindings.py)
and [approval/Control composition](https://github.com/michaelayoade/dotmac_platform_control_plane/blob/bf7893c75b524c61eaf5a2fe4c083fecc70234ab/src/vendor_cp/deployment/foundation_execution_approval.py)
are source evidence, not live installation or material presence.
The checked-in execution topology still has `admission_evidence: null`. Do not
replace either gap with request-selected roots, a generic plugin or a PASS flag.
A document-only CP readiness-packet check is not an authority or consumption
proof; most of its terms are presence checks.

For each independently admitted disposable target, retain this exact tuple:

- A distinct Fleet host identity/incarnation, target admission, fixture digest,
  controller presenter and current host/execution context.
- The recorded candidate coordinate when available, exact CP V3 canonical bytes
  and digest, Control plan UUID/digest and approved execution subject.
- That target's genuine signed Control V2 pair and verified current standing.
- Installed provider/binding provenance in the actual isolated candidate
  environment; normal source doubles do not establish installed adoption.
- That dispatch's committed consumption readback and V2 outcome, joined to its
  exact receipt, run/attempt and retained ciphertext.

Use one target for the positive exercise and the other for failure-driven
compensation with nonempty foreign-rule evidence. Each has a genuine distinct
Control dispatch. No HOST_STATE_UNCERTIFIED lease reuse, invented epochs,
direct rollback substitution or seeded rules before a usable authorized driver.
Target creation and the real rehearsal window remain separate operational acts.

## Concrete custody packet still required

Prepare the following public metadata and private locations before requesting
any key creation or enrollment. None is supplied by this document.

| Item | Required decision or measured evidence | Allowed retained material |
| --- | --- | --- |
| Offline verification machine | Michael names a non-runner machine and supported OS/tool layout; approved source/profile and tools must actually execute there | Machine/profile identifier and measured tool/source digests; no private path or hardware assumption invented |
| Age recipient | Dedicated native X25519 encryption recipient; private identity remains outside every runner, under Michael's custody with tested recovery | Public recipient and approved encryption-tool pin only in protected policy |
| Offline result signer | Dedicated Ed25519 purpose separate from age, host and CP keys; Michael controls use, recovery, rotation and revocation | Public signer, finite validity, exact namespace/principal, mandatory revocation file and approved source/profile pins |
| CP/host trust | Real owner-held signed envelopes, immutable trust floors, authenticated presenter/context sources and current standing | Protected public trust plus opaque references to private owner material; no credentials in Git/logs/Knowledge |
| Execution topology | Adopted-path workflow identity, actual JWT bound claims, authorized two-target admission, inside/outside vantage and complete issuer acceptance | Admitted public coordinate/evidence only; actual private addresses/materials remain in protected storage |

The current fixed production encryption executable is `/usr/bin/age` on the
execution environment. That path does not select or certify an offline machine.
The private verifier is not yet implemented or qualified; do not enroll a
profile claiming otherwise. No agent creates keys, reads custody secrets or
approves a protected human job under this preparation packet.

Actual key provisioning remains Michael's reserved authority in the recorded
admission ruling and topology section 11.2. The earlier approval selected
offline-custody design preparation. It did not authorize a custody ceremony.
A request for that ceremony must identify the completed verifier/profile,
actual machine/storage/recovery arrangement, exact protected policy diff,
negative tests and revocation procedure first.

## Exit sequence

1. Land and qualify the hosted transport proof; reconcile this matrix with CP's
   current source and complete the real retention/consumption contract.
2. Implement the private verifier with its actual owner inputs and caller;
   qualify exact rules, foreign preservation, failure/compensation, recovery,
   wrong tuple/current standing and ciphertext-before-decrypt refusals.
3. Present the complete custody/enrollment packet to Michael. Reconcile the five
   committed Seabone keyless proofs, machine outcomes, ledger evidence and the
   required next-morning rerun of proofs 2–4. Do not revive old Gate-0 D/E,
   broker/coordinator or privileged-runner provisioning.
4. Michael certifies the pre-freeze source worksheet and candidate-independent
   readiness. Freeze every candidate input, allocate `0.4.0a2`, build once and
   commit its receipt before any exact-candidate rehearsal. Candidate-bound
   admission is a later Gate 3 obligation, not a pre-allocation circular demand.
5. Install the exact recorded files and run admitted rehearsals. Publish only
   the recorded artifact, verify Forgejo readback/install, tag and merge the
   truthful release record. No rebuild promotion.
6. Separately accept CP execution readiness and first-deploy recovery, name the
   production window, take a fresh backup, deploy Sub-thin and prove health and
   the stop/restore/prior-image rollback. Brand flip and telemetry have their
   own gates.

No dates in earlier records authorize an action. This packet records neither
source certification, operational acceptance nor an approval on Michael's behalf.
