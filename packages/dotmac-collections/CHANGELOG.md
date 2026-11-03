# Changelog

## UNRELEASED — 0.1.0a2 proposal

Proposed successor for a fresh build through the modern release provenance
lane, pending review and Michael's source/release authorization after the
Kernel a105 publication record is accepted. This version has not been built
or published.

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

## 0.1.0a1 - Unreleased

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
