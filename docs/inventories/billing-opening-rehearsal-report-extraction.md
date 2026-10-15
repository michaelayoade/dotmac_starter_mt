# Opening rehearsal report inventory — 2026-10-14

This inventory precedes the report implementation. Inspected source is Starter
`f75c69e6586732b14a0eab6b55422c71e4ddefdf` and Sub integration
`c65237a9f06c5833ef18800db5a65b566a8bc8de`. Neither coordinate is a production
capture or adoption receipt.

Sub's existing `scripts/migration/billing_opening_export.py` consumes
`preview_billing_opening_export` under a read-only snapshot and serializes the
actual V2 export contract. That contract already carries account/currency lane
controls, source observations, capture cutoffs, schema heads, deterministic
exceptions and a content fingerprint. It deliberately qualifies no payment:
`cash_evidence.qualified_payment_ids` remains empty. Source observations are not
trusted confirmation or source-key qualification.

Starter's existing `opening_validation.py` and
`opening_receivable_validation.py` own the actual V1/V2 import preflights.
They produce typed deterministic blockers and separate residuals. No report
may reinterpret verifier-unavailable as success or turn those previews into
financial admission, an opening seal or a Sub authority switch.

The PORT-DELTA is a typed pure report over those existing observations and
preflight results: exact per-account/currency reconciliation, deterministic
blocked reason counts, explicit unclassified money deltas and evidence for
three consecutive complete reconciliations. The module must not create another
tax, cash qualification, mapping, import, posting or balance-calculation owner.
Sub does not import unpublished Starter APIs. The existing hosted opening
canaries consume the report against the real preflight contracts; real import
rehearsal remains a disposable migrated PostgreSQL CI workload.

Keep separate financial lanes. WHT evidence carried by historical receivable
discharges must not be invented as a cash aggregate. Unsupported/non-qualified
lanes and incomplete population are explicit blockers. External record mapping
must preserve actual export provenance and cannot invent qualified payments,
source settlement keys or trusted verification evidence.

Distinguish repeating the same immutable snapshot from reconciling successive
live cutoffs: different capture clocks do not invalidate identical content;
different financial content must reconcile independently. Do not compare opaque
watermarks lexically or call three copied summaries three complete runs. Bind
the source, schema, target, policy and artifact versions actually supplied.

Hosted canaries must fail on a missing partition, mismatched account/currency,
duplicate evidence, precision mismatch, one-minor-unit delta, incomplete run,
missing verifier, unsupported lane, incompatible bindings or inadequate run
sequence. Zero unclassified drift is mandatory. Explicitly accepted manual
holds are reported separately and never become a tolerance for money deltas.

The pure report and real-preflight consumer canaries are implemented and have
independent static review; hosted runtime acceptance remains required. Actual
Sub V2 lacks full original-documentary evidence and WHT/non-cash replay
attribution. Nonempty eligible-invoice reports therefore remain blocked; the
positive three-run canaries use genuinely empty financial populations and do
not claim a nonempty production rehearsal. Extending the actual read-only
source export and verifying its assembly mapping remain necessary.

This is candidate source work, not a completed production capture or rehearsal.
The named Sub host remains authorized for read-only capture, but current SSH
sandbox access is blocked. Historical exception counts are dated baselines.
No import, mutation, credentials, publication or activation is authorized by
this report, and local static checks do not establish runtime acceptance.

## Opening-rehearsal adapter (disposable assembly tooling) — 2026-10-14

`scripts/billing_opening_rehearsal_adapter.py` is the unpublished read-only
Sub -> Starter translator for the rehearsal census: it mechanically projects
Sub's serialized `sub-billing-opening-census-v2`/`-v3` export onto
`OpeningSubObservationV2`, assembles a candidate manifest from an explicit
assembly file only (`cash_lots=()`, `original=None` always), and counts the
pure preflight/report blockers per partition. It opens no database session,
calls no writer, constructs no verifier, qualifies no cash and computes no
tax. Its unit tests live in
`tests/unit/test_billing_opening_rehearsal_adapter.py` with hand-authored
fixtures (including a fixture assembly, for tests only) under
`tests/fixtures/opening_rehearsal/`. The production assembly file remains a
separate Michael-owned signed artifact; the adapter refuses to run without
one.

Deletion trigger: delete the adapter and its fixtures when (a) a verified
`OpeningWindowSealV1` is stored for the target scope — after which new opening
admission fails closed and rehearsal has no consumer — and (b) three
successive complete runs have been compared via `compare_opening_rehearsals`
for the record. Earlier supersession: if an `OpeningSubObservationV3` replaces
the V2 projection, delete the v3-sidecar path in the same change. Deletion
removes only `scripts/billing_opening_rehearsal_adapter.py` and its fixtures;
the `opening_rehearsal*.py` report types remain package-owned.
