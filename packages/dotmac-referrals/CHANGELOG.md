# Changelog — dotmac-referrals

## 0.1.0a2 — unreleased

- Add `service_location_ref` (`rf_0002_service_location_ref`, additive; released
  `rf_0001` bytes are unchanged) and referral capture V2,
  `CaptureReferralV2` / `capture_referral_v2`. The ref is optional (a prospect
  may not have a place yet) and, when present, an opaque token of 1 to 160
  letters, digits, `.`, `_`, `:`, `-`. Address text and coordinate pairs are
  refused by construction; the module holds no coordinate or address field and
  never resolves the token (ADR-0039).
- A V2 replay of a source event must carry the same location; a different one,
  or dropping it, is a `Conflict` rather than a silent overwrite. The reward
  outbox fact is unchanged and never carries the location.
- `capture_referral` (V1) and `CaptureReferral` are unchanged and leave the
  column NULL. The column is nullable on `referrals` only; the application can
  roll back without rolling the schema back.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `401f0006` by release run
`32473508534`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

### Added

- Ports Sub's programme/code/attribution/conversion behavior into an independent
  tenant owner using only opaque collaborator references.
- Adds immutable programme-policy versions, finite codes, idempotent source
  attribution, evidence-bound conversion and transactional reward-request facts.
- Creates five tenant-scoped tables with composite identity and foreign keys,
  forced RLS and same-migration role grants in the `rf` lineage.
