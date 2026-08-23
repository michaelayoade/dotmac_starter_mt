# Changelog

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
