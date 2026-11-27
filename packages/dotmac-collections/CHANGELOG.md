# Changelog

## 0.1.0a4 — source candidate, not yet published (GAP-4 follow-up: the halted ladder is recorded)

Source only. a3 is published and tagged `dotmac-collections-v0.1.0a3`; no migration
changes and the released `cl_0001`/`cl_0002` bytes are unchanged.

- When the policy ladder stops because the next step counts from a receipt that a
  skipped step cannot supply, the case now records one `active` to `active`
  transition with reason code `policy_ladder_halted_no_anchor` instead of stopping
  silently. At most one per case: resume, hold-lift re-arm and redelivery find the
  existing transition and record nothing more. No schema, contract or allowlist
  change.

## 0.1.0a3 — released, tag `dotmac-collections-v0.1.0a3` (GAP-4: the per-account access hold)

a2 is published and its `cl_0001` migration is unchanged.

- Migration `cl_0002_collection_holds` (both planes): the nullable
  `collection_cases.access_hold_from`, the insert-only `collection_holds` and
  `collection_hold_lifts` tables (R10), the single-writer `collection_subject_access`
  state row and one version sequence per plane
  (`collection_subject_access_version_seq`, `platform_...`). Forced RLS on the tenant
  plane; the platform plane is revoked from `app_user`. A hold is live until a lift
  row names it; `(external_ref, placement_ordinal)` is unique.
- `ReceivableObservationV1.access_hold_from` (optional trailing field, default
  `None`): the instant the subject's service goes off. It needs a verified due date
  and must not precede `due_at`. `None` never demands a hold (an adopter with no
  services only gets reminders).
- Subject access state per `subject_ref` (one billing account): `clear`, `held`
  (some active case has `access_hold_from <= now` and no live hold) or `suppressed`
  (demanded but a live hold exists). Only `held` turns service off. Every governed
  command recomputes it under the subject lock; a change into or out of `held`
  allocates the next sequence value and emits one
  `collections.subject.access_hold.v1` outbox event
  (`subject_ref`, `active`, `source_version`, `source_reference`, `observed_at`,
  `policy_version`, `reason_code`) whose payload is fixed at decision time.
- Two wakes through the existing timer port: `collections.exposure.due.v1` (armed by
  `assess` when it blocks on `receivable_not_due` or `service_period_not_started`;
  the assembly routes it back to `assess`; identity entity id is
  `{source_owner}:{exposure_ref}`) and `collections.case.access_hold_due.v1` (armed
  when a case opens or advances with a future `access_hold_from`, cancelled when the
  case leaves `active`). New `CollectionCaseService.process_access_hold_due`
  (`ProcessCollectionAccessHoldDueV1`): rereads the exposure, updates the case and
  recomputes the subject. The assembly runs the Billing balance pass in an earlier
  transaction first.
- R10 holds: `PlaceCollectionHoldV1` / `LiftCollectionHoldV1`,
  `CollectionHoldService.place` / `.lift(db, command, timer)` returning
  `HoldPlaced | HoldLifted | HoldNotLive`, and `get_collection_subject_state`.
  Place and lift are idempotent by `external_ref`; a live reference on another
  subject is refused (`hold_subject_conflict`). While a hold is live `assess` arms no
  ladder step and `process_step_due` writes no notice or action request
  (`StepDueBlocked("collection_hold_active")`, the fired wake is consumed); cases
  still open and resolve. The last lift re-arms the next ladder step of each active
  case and recomputes the subject, so a still-overdue subject goes off again at once.
  `approval_ref` is stored only; approval is optional and off by default (the caller
  enforces it for staff-placed holds).
- Lock order: hold-reference lock, then case locks (by id), then the subject lock.
  Collections takes no Billing lock. An architecture test scans the order with
  planted violations. `lift` takes the case locks, then the subject lock, and only
  then decides whether it is the last live hold; an active case that appeared in
  between is refused (`subject_cases_changed`) and redelivered, so two simultaneous
  last lifts cannot both skip the ladder re-arm. `lift` now requires the timer.
- The hold counts only once engaged: `collection_cases.access_hold_engaged_at` is set
  only by `process_access_hold_due`, so no other command can cut service before the
  assembly's balance pass ran. The wake is armed at the later of the hold instant and
  now (a case that opens already past its instant, or a resumed case, gets a wake due
  at once). A hold instant that moves later disengages; leaving `active` disengages.
- `collection_cases.due_at` (the exposure's verified due date): reminders anchored on
  `exposure_at` count from the due date, not from when the case opened.
- Late cases never re-send past reminders (ruling 2026-11-22): when a case opens, resumes
  or its last hold lifts, every notice step counted from the due date whose instant is
  already before that moment is passed over and recorded as a `skipped` step attempt
  (cl_0002 widens the attempt-kind check to `notice`, `action`, `skipped`), and the
  ladder carries on from the first step not yet due. The first step that is an action,
  is not yet due, or has no anchor ends the pass without error. A skip stores the instant the step was
  scheduled as its attempt instant, and that instant anchors a successor counted from
  its request (the walk continues, so a case opened at due+10 skips notice 1 and arms
  notice 2 at its scheduled time); a successor counted from a receipt, which a skip
  cannot supply, halts the ladder instead of raising `policy_anchor_unavailable`.
  Replay and audit read the skips.
- `republish_subject_state` (`RepublishSubjectStateV1`): re-emits the subject's current
  state under the next sequence version, for a product that must apply the state on
  service activation or transfer and would otherwise see a stale version.
- `get_collection_case` (`CollectionCaseViewV1`): a read-only view of a case (subject,
  exposure, lifecycle, due and hold instants), so the assembly can run the balance
  pass for the wake's case without private knowledge.
- `assess(..., exposure_wake=...)` accepts the fired exposure-due wake, and cancels any
  pending exposure wake once the exposure is actionable, so no timer row stays
  scheduled.
- Manifest: three new outbox types (`collections.exposure.due.v1`,
  `collections.case.access_hold_due.v1`, `collections.subject.access_hold.v1`).
- Policy values (due days, grace, notice offsets) stay settings of the adopter and
  of Billing's account terms; nothing is hard-coded here.

## 0.1.0a2 — released, tag `dotmac-collections-v0.1.0a2`

Successor built through the modern release provenance lane after the Kernel
a105 publication record was accepted. Published and tagged.

- Runtime behavior, public API and migrations remain unchanged from the
  published a1 source; the Kernel floor remains `>=0.1.0a89`.
- Declare a new `0.1.0a2` candidate identity rather than reissue a1 or relabel
  its legacy tag. The version and release documentation change in this
  proposal; the a1 tag and historical evidence remain unchanged.
- A fresh build, publication, install-back verification, canonical tag evidence
  and accepted release record remain required before a2 can be adopted as a
  published release. This proposal does not establish a product authority
  switch.
- Scheduled as the Collections constituent (ST-7) of the cutover-release-1
  aggregate. The Kernel a105 publication record is on main
  (`docs/inventories/kernel-release-verifications/0.1.0a105.json`).
- Test-only addition, outside the package tree: an X1 conformance fixture
  (`tests/unit/test_collections_x1_unknown_due_conformance.py`) drives a
  Billing `ReceivableExposureV1` with an UNKNOWN due date through the
  assembly map into `CollectionCaseService.assess` and proves it stays
  fail-closed (`due_date_unverified`; no case, timer, notice or action
  request) on first read and on replay until Billing publishes a verified
  due-date basis.

## 0.1.0a1 — released, tag `dotmac-collections-v0.1.0a1`

- Publish the complete typed adopter surface at `dotmac_collections` while
  keeping ORM persistence private.
- Require a caller-owned session on the timer port and declare the neutral
  `collections.case.step_due.v1` wake-up vocabulary.
- Wire case opening, replanning, pause and closure to exact timer generations;
  process due wakes through the Collections owner with a fresh receivable read;
  and advance or retry only from typed owner receipts and immutable policy data.
- Make notice purpose, action effect scope, receipt requirement and retry
  offsets explicit immutable policy-step facts, and carry expected source
  version on every scheduled timer generation.
- Add frozen, fully typed receivable-reader, policy, case-action, notice,
  arrangement, grace and durable-timer port contracts.
- Rename the peer input to `ReceivableObservationV1`; remove the competing
  position owner and the available-credit/funding lanes; preserve Billing's
  financial state, source authority and projection mode without laundering
  them into Collections state.
- Fail closed on unknown/unverified or future due dates and model reversal as a
  new upstream version that may reopen, never as a terminal Collections state.
- Add one selectable dual-plane lineage: tenant tables use forced RLS and
  platform mirrors omit tenant identity/RLS, revoke `app_user`, and are
  reachable through `platform_api`.
- Run the same typed service lifecycle on `TenantScope` or `PlatformScope`,
  selecting the matching kernel idempotency ledger and persistence models.
- Preserve product-proven Sub scenarios without importing Billing, Timers,
  product implementations, provider clients, or accounting code.
