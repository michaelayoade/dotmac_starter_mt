# Foundation Gate 2 source worksheet — 2026-11-12

Status: source findings prepared for Michael's certification. This document
certifies nothing on his behalf and authorizes no freeze, allocation, build,
key ceremony, host operation or deployment. Michael is the sole approver.

## Immutable evidence and phase boundary

The assessment reads Starter `9c1e958c19ffa89c997d2c2af56926666c8b4fb7`
and CP `3adb98a8961881a01a213072d319fc983c6537dd`. Current branch tips may
advance; those named trees define these findings. No tests or product imports
were executed during this audit. Existing hosted evidence is separate below.

The approved November 7 gate direction retains the sixteen receipt items,
authorization, exposure, compensation, recovery and build-once guarantees.
Source and candidate-independent readiness precede allocation. Positive
installed-artifact admission and the complete behavior exercise follow the
single recorded a2 build. Requiring their a2-bound results before allocation
would create a bootstrap cycle. Neither phase may be reported as the other.

Foundation `0.4.0a1` is spent; do not build or publish it again. `0.4.0a2`
remains unallocated. An editable install, fabricated distribution metadata,
source shadowing an installed wheel, patched artifact reader or approving
replacement provider cannot establish the required installed positive.

## Current source findings

| Obligation | Source owner and finding | Remaining evidence / phase |
| --- | --- | --- |
| One deployment authority | Foundation `engine/run.py` requires a grant, concrete V3 plan and admission provider; the public executor owns exposure reconciliation and compensation. The old exposure entry point cannot apply as a second executor. | Michael's Q1 assessment; actual exact-candidate apply/compensation at Gate 3. No new Foundation public entry point was identified here. |
| Exact descriptor, plan and pre-observation | `execution_plan_v3.py` binds descriptor, candidate archive, target/controller/host incarnation and enrollment. `authorization_v3.py` checks installed subject and fresh provider facts, then retains the actual V2 authorization/dispatch pair for finalization. | Genuine trusted source documents and later installed-candidate positive/refusal proof. A parsed dictionary or matching operator strings are not authority. |
| One caller-held lock | `Executor.run` checks the held lock before admission, grant revalidation, consumption and mutation; rollback has the same caller-lock boundary. | Composed lock contention, changed-plan/prestate and mutation-after-consumption proofs in the protected exercise. |
| Control approval subject | CP `foundation_execution_approval.py` owns `foundation_execution_plan.v1`, bound to the real Control plan UUID, purpose, operation and execution digest. | This source gap is already implemented; do not implement it again or disable `requires_approval`. Genuine current approval and later execution evidence remain due. |
| Held standing and committed consumption | CP calls Approvals' `held_transition`/`hold_approval` through Control finalization and the caller's commit. Distinct `lookup_committed` reads Control's committed dispatch marker without reconsuming. | Keep current approval standing and post-commit lookup distinct. Retain both owner evidence and exact dispatch/outcome joins in the private proof. |
| Declared execution binding | CP `pyproject.toml` declares `dotmac-control-plane = vendor_cp.deployment.foundation_bindings:bindings`. The fixed factory calls the approval-guarded composer and supplies deployment admission plus V3 authorization. | Declaration/source composition exists. Actual accepted CP installation beside the exact Foundation candidate and runtime adoption are not established by it. |
| Startup observer materials | CP's closed host-admission document names attempt, Control presentation and candidate/installed envelopes; the context document matches installed `ExecutionContextV3` fields. PR264 parses presentation through Control's actual public type and rereads materials each observation. | The trusted launcher producer/depositor, genuine attestations/presentation and independent live context facts are still missing. Parsing is not signature verification or approval. |
| Own-artifact HostSource binding | Foundation `host_source_admission.py` independently reads its installed artifact and compares its distribution/version/archive with signed subjects. `authorize_v3` independently reads the installed subject too. | Positive admission is a Gate 3 result from the one recorded candidate, not a source-checkout test to force green. |
| Generic profile admission | `application_profile.py` has entry-point discovery and installed-artifact comparison; the per-concern source suite exercises its general mechanism. | Actual consumer profile, image imports and exact installed candidate evidence remain required. Mechanism coverage is not product adoption. |
| Exposure private evidence | The runner records actual Executor crossings, closes the manifest/member set, encrypts after execution and binds ciphertext into the receipt. Public result authentication and publisher freshness checks are implemented with unenrolled policy. | Complete private verification must bind actual rules, foreign preservation, failed verification/compensation and Control/outcome/negative-target evidence. V1 carries only probes and crossings. |
| Isolated recovery | `recovery_execution.py` requires admission before creating a fresh `RestoreTarget`; it owns the real ten-step isolated restore sequence. No production target is accepted by that effects protocol. | A trusted CP-owned isolated rehearsal session/driver and genuine exact-candidate admission, roles/privileges, per-step failures and exact-image readiness remain due. Do not call this governed production recovery. |

CP's generic `compose_foundation_v3_providers` deliberately retains its
approval-required refusal. That is not evidence that the separately owned
approval-guarded composition is absent: `foundation_bindings` calls
`compose_approved_foundation_v3_providers`. Looking only for callers of the
generic composer or reading its refusal would incorrectly reopen completed work.

## Recovery scope is already ruled

The existing publication scope is the real **isolated restore rehearsal**,
not a new in-place production recovery API. Knowledge
`foundation-admission-ruled-plan-2026-09-08` orders one recorded a2 candidate,
exact-file protected installation, both executors and the real ten-step restore
sequence. `control-foundation-recover-vocabulary-divergence` v3 preserves the
separate typed production recovery grant/signing identity and refuses adding
`recover` to deployment `OPERATIONS` merely to align vocabularies.

Foundation `recovery_execution.py` and `authorization.py` expressly distinguish
fresh isolated restore from failed-system production recovery. The data-only
`RecoveryExecutionPlanV1` is not a consumed authorization. ADR-0074 mentioned in
older Knowledge is not present in the assessed Starter tree; do not cite it as
a landed ratification. Control's historical wording "Option B" describes the
production recovery vocabulary fence, not a new choice in this worksheet.

Sub-thin's separately ruled first-deploy procedure remains stop, restore the
fresh pre-execute backup, then restart the prior image. It must be rehearsed
before the named deploy. It neither proves the Foundation ten-step sequence nor
requires Foundation to acquire a general production `recover` operation.

The recovery CLI remains refusing and CP's published binding supplies no
`build_recovery_session`. The source completion target is therefore the trusted
isolated session/driver and its evidence, using the existing public executor.
If that composition requires modifying Foundation, its exact missing contract
must be identified before freeze. This audit did not demonstrate such a missing
Foundation public entry point. It does not certify that composition is complete.

## Existing hosted acceptance coordinates

- Starter PR1035 merged `669bc2fb`, with all 34 checks green: current-run
  encryption ordering and ciphertext-only delivery. PR1039 qualification landed
  via PR1043, `61316947034be7644960d7439c3ba21e26e9b1ca`; its final head had
  35 green checks. Synthetic-key real age round-trip proves transport
  interoperability, not production policy loading, custody or complete proof.
- CP PR264 head `2caca31ba88c7eb9ef032c051c6d69fe40eaf7be` passed all eight
  contexts and merged as `3adb98a8961881a01a213072d319fc983c6537dd` with the
  identical reviewed tree. Main CI `43159530323` passed. The separately approved
  one-image run `43160362442` accepted, published, read back and retained the
  receipt for that exact source. No runtime installation followed.
- Canonical current pointers: Knowledge
  `dotmac-foundation-gate2-040a2-20261106` v57 and
  `dotmac-cp-foundation-execution-approval-source-20261107` v5. They index the
  underlying evidence; they do not create approval or observations.

## Certification still owed

Michael's source certification must identify which worksheet findings he
accepts, any actual Foundation-byte changes still required, and the remaining
candidate-independent material/trust/driver prerequisites. It cannot assert a
runtime observation or installed positive that has not occurred.

Before allocation: certify the required Foundation source contract and relevant
CP composition, identify and land any Foundation-byte changes, and establish the
adopted-path candidate-independent readiness. Retain unavailable evidence as
unavailable. Do not demand a candidate attestation before its candidate exists.
The existing build-once roadmap owns the freeze boundary: runner/offline tooling
may advance at a later release revision where that roadmap permits, provided it
does not require changing frozen candidate inputs or waive a retained guarantee.
This worksheet adds no new requirement to finish all publication tooling before
allocation. A complete private proof remains required before publication.
After allocation: install and rehearse the one recorded a2 artifact through
both required executors with genuine admission and evidence. All sixteen
receipt items, deployment authorization/lock/compensation, real role/privilege
closure and image readiness remain mandatory before publication.

No generic recovery feature, new signing identity, waiver or extra version is
introduced by documenting these boundaries. Legacy Sub remains production.


## Proposed recorded-rule source closure in this change

The accompanying offline consumer change binds the caller-held descriptor to
the V3 product/digest and canonical reconciliations, and checks every recorded
replacement's exact owner-derived ordered rule arguments, including failed
writes. It refuses exposure-side compose calls. This closes an argument
consistency gap in the proposed source; hosted qualification is still required.
It does not change the assessments of Control/outcome joins, applied host state,
foreign preservation, compensation, custody or installed admission above.
