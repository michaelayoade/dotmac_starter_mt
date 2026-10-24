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
assembly file (`cash_lots=()`), optionally preserves explicitly supplied originals
and historical effects from a bound evidence sidecar, and counts the pure
preflight/report blockers per partition. Without that sidecar CLI originals
remain unavailable. It opens no database session,
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

### Canonical encodings are package-owned — 2026-10-15

The first adapter cut carried four canonicalizing `json.dumps(..., sort_keys=True)`
calls of its own, which tripped the tooling canonicalizing-population ratchet
(`SCRIPT_CANONICALIZING_MODULES`). Michael's 2026-10-15 decision (option A) was a
typed contract owned by one module, not a ratchet entry; the baseline is
unchanged. `dotmac_billing.opening_rehearsal_contracts` now owns the closed,
versioned `OpeningRehearsalAssemblyV1` (every field the adapter reads) and
`OpeningRehearsalCensusV1` (the counted blocker census), and
`dotmac_billing.opening_rehearsal` owns the only encodings:

- `parse_opening_rehearsal_assembly` (strict) and `opening_rehearsal_assembly_wire`
  are the two directions of one wire shape, both package-owned; the wire keys
  equal the dataclass field names at every level and carry
  `schema = "billing.opening.rehearsal.assembly.v1"`. Parsing is closed: an
  unknown key at any level (an `approved_by`, an `amount_override`, a partition
  `cash_lots`) is refused, every declared key is required
  (`accepted_manual_holds` included, nothing defaults), UUIDs must be canonical
  lowercase, and instants must be UTC (`Z` or `+00:00`; other offsets are
  refused, not normalised, and `Z` and `+00:00` are one instant). Every JSON
  read goes through `parse_opening_rehearsal_json`, which refuses duplicate
  object keys and non-finite constants.
- `opening_rehearsal_assembly_digest` is the single definition of an assembly's
  identity for signing, the adapter and any later verifier: the Kernel's
  sorted-key compact JSON of `{"domain": "billing.opening.rehearsal.assembly.v1",
  "value": <wire>}`, hashed with SHA-256, the same domain-separated pattern as the
  binding and cash-command digests. **Identity change:** this supersedes the
  first cut's undomained digest over the raw file; the digests differ and are
  deliberately not backward compatible, because nothing production has been
  signed. Vectors are pinned in the tests next to the literal canonical bytes.
- `render_opening_rehearsal_census` renders the census (sorted keys, indent 2,
  finite, trailing newline) and `render_opening_rehearsal_refusal` renders the
  two value-free CLI refusal lines. The census/refusal renderings are unchanged from the
  first cut; only the `assembly_digest` value they carry changed (above).

The adapter now holds no canonicalizing call. Digests identify content only;
they are not approval, qualification or activation authority.


### Explicit originals and nonempty contract canary — 2026-10-16

The existing adapter's Python `assemble_rehearsal_run` and `rehearsal_census`
accept an optional keyword-only `original_documents` tuple
of `(source_invoice_id, OpeningOriginalDocumentV1)` inputs. It preserves supplied
originals only for eligible, observed, mapped invoices and refuses duplicate,
unknown, excluded, unmapped, number/time/total/currency-mismatched mappings.
No original is constructed from V2 financial observations or V3 current
documentary observations. The closed assembly JSON remains unchanged; without the optional evidence
sidecar a normal nonempty source export still counts missing originals and
verifier unavailability. Documentary digests and mapping declarations are content
identity, never qualification. The existing importer verifier must independently
qualify the exact command and supplied original evidence.

The census API threads that typed tuple through the existing assembly checks,
and its manifest fingerprint identifies the resulting candidate manifest. The
source fingerprint and assembly digest retain their existing meanings; the
tuple is not a snapshot-bound evidence sidecar. V3 historical-unavailable
observations remain counted even when a typed original is supplied. Focused
adapter canaries cover this bridge, malformed and misbound mappings, and the
continued documentary and verifier blockers; hosted execution is required.

CLI original input remains unsupported: Billing's only reusable generic decoder
is the private `_document_facts._decode` used by persisted opening reads.
The public `decode_invoice_document_fact` accepts `InvoiceDocumentFactV1`, not
`OpeningOriginalDocumentV1`. A strict CLI sidecar bound to the source fingerprint
and assembly digest needs an owner-approved original-document wire/parser
contract; this slice introduces no private-codec import or duplicate codec.

`tests/test_billing_opening_rehearsal_import.py` adds a hosted migrated-PostgreSQL
canary for both planes, using the existing synthetic one-eligible-invoice export
fixture, an explicitly synthetic typed original, a verifier restricted to that
exact fixture command, and the existing real restricted-principal window fixture.
It asserts three distinct nonempty synthetic capture reports remain blocked on
`original_documentary_evidence_unavailable`, exact per-account
100.00 NGN receivable / zero cash residual, manual collection-evidence hold,
changed-original/command refusal, actual import, persisted documentary basis,
idempotent replay and permanent seal. This is an adapter-to-importer contract
canary; it does not query a Sub database or establish authentic original evidence,
production qualification, three live-cutoff captures or a production rehearsal.
Hosted execution is required; local static checks are not runtime evidence.
The report owner intentionally emits that blocker unconditionally for each V2
mapped eligible invoice, even when an independently supplied original and
qualified import preview exist. `compare_opening_rehearsals` therefore remains
`run_blocked`; no complete nonempty report is claimed or simulated. Cash
qualification remains unsupported. Historical discharges are preserved only
when explicitly supplied and bound to existing observed settlements and assembly
mappings; the default path supplies no historical effects.


### Aligned original-evidence CLI bridge — 2026-10-16

`--original-evidence PATH` optionally supplies the closed
`billing.opening.rehearsal.original-evidence.v1` shape:

```json
{
  "schema": "billing.opening.rehearsal.original-evidence.v1",
  "census_sha256": "<64 lowercase hex: SHA-256 of exact census file bytes>",
  "assembly_digest": "<existing canonical opening_rehearsal_assembly_digest>",
  "invoices": [
    {
      "source_invoice_id": "<source invoice UUID>",
      "original": "<complete OpeningOriginalDocumentV1 JSON object>",
      "historical_discharges": ["<complete OpeningHistoricalDischargeV1 JSON object>"]
    }
  ]
}
```

The angle-bracket strings illustrate required values, not valid sample DTOs.
`OpeningRehearsalOriginalEvidenceV1` and `OpeningRehearsalInvoiceEvidenceV1`
are owned by `opening_rehearsal_contracts`; the public
`parse_opening_rehearsal_original_evidence` reuses Billing's canonical
schema decoder inside the package. All declared fields are required, including
nullable fields and `replay_bindings`; unknown fields, wrong types, unsupported
versions and duplicate invoice records refuse. Nested money is represented by
`{"amount": "100.00", "currency": "NGN", "minor_units": 2}`; decimal values are
strings, instants ISO text, and immutable tuples JSON arrays. Every input JSON
file passes `parse_opening_rehearsal_json`, rejecting duplicate keys and
nonfinite constants. No schema fallback, defaults, tax calculation or evidence
reconstruction is performed.

The adapter reads census bytes once. Before passing evidence to assembly it
requires both the exact raw-file SHA-256 and the existing canonical assembly
digest to agree. Even whitespace changes to the census require a newly aligned
sidecar. Invoice originals must bind to unique eligible, observed and mapped
invoices with matching original number, issue instant, total, currency and
precision. Supplied historical effects must bind uniquely to explicit assembly
discharge references and observed active settlement kind/id/source-id mappings,
the same invoice and exact minor-unit amount. Unknown, excluded, unmapped,
duplicate or mismatched supplied records refuse. Missing evidence remains a
counted blocker; it is never synthesized. The Python entry points also accept
explicit typed `historical_discharges` tuples of `(source_invoice_id, effects)`
alongside `original_documents`.

Parent namespaces, live identities, evidence references and mapping versions
remain the supplied rich evidence: the CLI makes no qualification claim about
them and invents no namespace mapping. Alignment hashes identify content only.
The existing importer verifier must independently qualify the exact command
and evidence before any import. Documentary/verifier report blockers remain,
V3 historical-unavailable observations remain visible, and `cash_lots=()` still.
Omitting the new option preserves prior output and default semantics. This
bridge adds no storage, custody, verifier, cash writer or production operation.

Focused hosted proofs cover closed/nested parsing, changed raw census or
assembly, duplicate/unknown/mismatched invoice and settlement inputs, propagation
of actual originals/effects into manifests and reports, preserved V3 observations
and refusal to qualify an original alone. These proofs are authored for hosted
CI; local pinned Ruff and stdlib static checks do not establish runtime acceptance.

## Opening-rehearsal assembly drafter (disposable assembly tooling) — 2026-10-24

`scripts/billing_opening_assembly_drafter.py` is the unpublished read-only
drafter for the candidate `billing.opening.rehearsal.assembly.v1` file the
adapter requires (Step 5, Phase A). Inputs are Sub's census wire JSON and a
closed, declared target-state file (the assembly scalars, the copy's pristine
position per partition or one shared pristine position, and an optional UUIDv5
namespace for target account ids). It performs identity mapping only: source
account, invoice and settlement ids are carried across verbatim, and
precisions come from the census's declared `currency_precisions`. It computes,
defaults, rounds and qualifies no financial value, and a partition, invoice or
settlement it cannot map identically is omitted and counted as a blocker in
`summary.csv` so the adapter reports it as unmapped. Output is deterministic
(sorted, canonical JSON; same sha256 across runs), refuses to overwrite, and
the package parser and single assembly digest are the only judge of validity.
A draft is not approval: Michael approves the exact digest separately.

Unit tests: `tests/unit/test_billing_opening_assembly_drafter.py`.

Deletion trigger: delete the script and its tests with the adapter, once a
verified `OpeningWindowSealV1` is stored for the target scope and three
complete runs have been compared.

