# Changelog — dotmac-qualification

## 0.1.0a2 — unreleased

- Add a decision read for warrant verification: `read_decision` returns a
  `DecisionRead` (decision and case ids, subject and specification references,
  outcome, decided and expiry instants) and `verify_warrant` returns it only
  when the decision exists in the tenant, is `ELIGIBLE`, is in force at the
  verifier's explicit timezone-aware instant (`decided_at <= at < expires_at`)
  and names exactly the supplied subject reference. Any other state raises
  `WarrantRefused` carrying a closed, data-free `WarrantRefusal` reason.
- The read is read-only, takes no clock, and omits the rationale and every
  evidence fact, so a verification can never carry location, address or network
  data to its caller. A decision is the only promotion warrant; evidence stays
  observation.
- No migration, no schema change and no change to existing signatures.

## 0.1.0a1 — 2026-08-20

- Extracts Sub's qualification case/evidence/decision boundary.
- Refuses decisions without evidence valid at decision time.
- Creates three directly tenant-scoped, forced-RLS tables.
