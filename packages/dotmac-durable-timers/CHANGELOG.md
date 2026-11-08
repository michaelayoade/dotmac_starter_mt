# Changelog

## UNRELEASED - 0.1.0a2 proposal

Proposed successor for a fresh build through the modern release provenance
lane, for the cutover-release-1 aggregate. This version has not been built or
published.

- Runtime behavior, public API and migrations remain unchanged from the
  published a1 source; the Kernel floor remains `>=0.1.0a72`.
- Declare a new `0.1.0a2` candidate identity rather than reissue a1 or relabel
  its tag. The three version surfaces, the catalogue row and the
  declared-publication baseline change; the a1 tag and its historical evidence
  remain unchanged.
- `EXTRACTION.toml`: the adoption plan (`shadow_and_drift`, `first_cutover`,
  `next_action`) is rewritten from a shadow comparison to saved tests plus a
  backtest. No shadow run or N-day gate is part of adoption.
- The ten PostgreSQL proofs and their sensitivity companions are to be re-run
  on the CI integration job for the exact candidate head; see the PR for the
  recorded result.
- A fresh build, publication, install-back verification, canonical tag
  evidence and an accepted release record remain required before a2 can be
  adopted. This proposal does not establish a product authority switch.

## 0.1.0a1

- Initial publication (tag `dotmac-durable-timers-v0.1.0a1`): generation-safe
  durable timers on selectable tenant and platform planes over the kernel
  outbox relay.
