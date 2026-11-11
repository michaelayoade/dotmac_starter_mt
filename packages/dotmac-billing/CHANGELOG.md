# Changelog — dotmac-billing

All notable changes follow Semantic Versioning.

## 0.1.0a12 — UNRELEASED

a11 was published on 2026-11-10 (tag `dotmac-billing-v0.1.0a11`); a12 adds the
B lane and the entries below. Migrations here (`bi_0015` and later) are unreleased and
chain after `bi_0014_opening_variance_ledger`.

- B1 (`bi_0015_line_price_version_fk`): a composite foreign key
  `document_lines(obligation_id, price_source_version)` references the rated
  obligation's `(id, price_version_id)` (tenant-qualified on the tenant plane),
  so a rated line can only bill at the price version its obligation was rated
  at. The key is MATCH SIMPLE: credit-note lines (no `obligation_id`) are
  exempt, and opening-import invoices carry no document lines. The constraint is
  added `NOT VALID` and `VALIDATE` is attempted; if existing rows violate it the
  constraint stays `NOT VALID` with a constraint comment naming the manual
  exceptions review. No row is repaired or rewritten. Adds the target unique
  key `rated_obligations(id, price_version_id)` on both planes.

- B2 (`bi_0016_withholding_components`): expected withholding is frozen at
  issue as structural rows. `document_withholding_components` holds one
  immutable row per elected expected-withholding component of a `dotmac-tax`
  determination (positive amount, evidenced by a Tax party classification id;
  source owner pinned to `dotmac-tax`). Applicability, rate and amount are
  Tax's alone; Billing freezes and verifies, it selects and calculates nothing.
  An invoice with no elected component has nothing a discharge can reference:
  `accept_withholding_discharge` refuses it (`withholding_not_elected`), refuses
  evidence that differs from the frozen result
  (`withholding_evidence_differs_from_frozen`) or a non-Tax owner
  (`withholding_tax_owner`), and refuses a draw above the frozen amount
  (`withholding_exceeds_frozen_component`). The discharge table gains a
  composite foreign key on the component identity it already stores
  (added `NOT VALID`; `VALIDATE` is attempted and pre-existing discharges leave
  it `NOT VALID` with an exceptions-review comment; nothing is backfilled or
  repaired) and a BEFORE INSERT guard that keeps each component's net accepted
  amount within `[0, frozen amount]`. The one-acceptance-per-component unique
  index and permanent source claim are unchanged.
- B2: add `freeze_issued_invoice_withholding` (`FreezeIssuedInvoiceWithholding`),
  the governed command for an issued invoice that has no frozen components
  (any issued invoice without a freeze, for an authorized actor). It takes a V2
  dotmac-tax expected-withholding determination evaluated as of the invoice's
  issue date (election = its party classification) and does not trust it:
  - the determination must name the commercial source of exactly one line of the
    invoice and equal the original facts that line positively retained; a line
    whose retained evidence is V1 must be named explicitly (`line_id`);
  - each base equals the line's source amount (plus the referenced prior tax for
    a dependency-tax base);
  - dotmac-tax must return the sealed determination through the assembly-wired
    `TaxSealReader` (none wired: refused): identity, fingerprints, date, every
    original fact and the elected components (code, rule, base, rate lines,
    amount, and the sealed prior-tax amounts a dependency base folds in) must equal what the caller offered, and the rows are built from
    Tax's values;
  - one Tax determination backs one live invoice for late freezes
    (`withholding_freeze_determination_in_use`; a voided invoice releases it).
    The service refuses first, and the admission trigger enforces it for
    `late_freeze` rows under an advisory lock on (plane, tenant, determination),
    reading liveness through the same `has_live_invoice_issuance` rule the
    receivable-backing resolution uses (SQLSTATE 23505);
  - the frozen total is capped at outstanding plus withholding already accepted.
  The rows carry the bound line's obligation and are the same append-only rows
  issuance writes (`origin = 'late_freeze'`, with command id, actor, reason and
  the determination reference); the command never mutates the invoice.
  Idempotent on `command_id`; refused when components exist, for a credit note,
  void or non-live invoice, or when the determination is not as of the issue
  date. Tenant plane only; the platform plane stays fail-closed
  (`withholding_freeze_platform_unsupported`). A component's document and
  obligation must be a line of that document (composite key on `document_lines`)
  and a document has a single freeze (same origin and command id, serialized by
  the document row lock in the admission trigger; `command_id` is NOT NULL for
  every origin, issue rows carrying `issue:<document id>`). Afterwards the
  discharge foreign key, cap guard and credit-draw cap apply unchanged.
- B2: new permission `billing.withholding.freeze` (manifest `PermissionSpec`,
  default role `admin`) and two assembly-wired ports, `ActorPermissionReader` and
  `TaxSealReader` (`TaxDeterminationSealV1`, `TaxSealedComponentV1`,
  `TaxSealedLineV1`). The command refuses with no reader wired. `holds` authorizes
  the actor the caller names, not the caller: the route must use
  `permission_guard("billing.withholding.freeze")`, pass the authenticated party
  that guard returns as `actor_ref`, and the assembly must wire
  `ActorPermissionReader` over `dotmac_kernel.deps.authorize_party`. When the
  assembly installs billing, its explicit `starter.admin` grant list must add the
  permission by hand.
- B2: an opening-imported invoice (it has an `opening_receivable_bases` row) is
  refused with `withholding_freeze_opening_import_unavailable`, detected
  structurally. It has no line source and billing accounts carry no party
  reference; the branch waits for a party mapping from the dotmac_sub_thin
  composition.
- B2: `apply_available_credit` may draw at most the invoice's outstanding
  receivable less its undischarged frozen withholding
  (`credit_exceeds_non_withholding_room`), so credit is drawn before the
  discharge and can never consume the reserved withholding. Cash allocation and
  non-cash credit are unchanged.
- B2: add `WithholdingCashRequirementV1` (salvaged from the superseded #1006,
  without its tax code): a read-only quote of cash required from an invoice total
  and a Tax-owned withholding amount.

- B3: add `get_account_statement` (`GetAccountStatementV1` ->
  `AccountStatementV1`): a read-only, lock-free projection of one account's
  posting groups in one currency over a half-open `occurred_at` window, ordered
  by `(occurred_at, source_version)`, with opening/closing and running balances
  for the three lanes (never netted) and a sealed SHA-256 digest
  (`compute_account_statement_digest`, recomputable by a consumer; the dataclass
  verifies it on construction). Anchored to the ledger watermark read, bounded to
  5000 lines (`statement_too_large`). No DDL, no write, no event.
- G1 (money-flows, export-only; no behaviour change): the existing owner
  commands and their command types become part of the declared top-level
  `__all__`, so a composing app can call them under ADR 0024 §14. Functions:
  `accept_settlement`, `allocate_settlement`, `accept_withholding_discharge`,
  `apply_available_credit`, `deallocate_settlement`, `refund_settlement`,
  `reverse_posting_group`, `void_document`, `issue_document`,
  `issue_credit_note`, `grant_non_cash_credit`. Commands: `AllocationCommand`,
  `ApplyAvailableCreditCommand`, `DeallocationCommand`, `RefundCommand`,
  `ReversePostingGroupCommand`, `IssueDocument`, `IssueCreditNote`
  (`AcceptWithholdingDischarge` and `GrantNonCashCredit` were already
  exported). Opening lane entry points: `import_opening_lane_partition`,
  `admit_held_opening_lane_partition`, `seal_opening_lane`,
  `OpeningLaneAssemblyAuthorityV1`, `OpeningLanePartitionResultV1`.
  `void_document` has no command type. This is a declared-public-API
  commitment, not a control relaxation.
- G2: add `list_open_receivable_documents(db, *, scope, account, currency,
  cursor=None, limit=100)`, a read-only, RLS-scoped page of an account's open
  (`open` or `partially_resolved`), collectible invoices in one currency, each
  with `document_id`, `issued_at`, `due_at`, `collectible_receivable`,
  `service_ref`, `source_version` and `financial_state`
  (`OpenReceivableDocumentV1`, `OpenReceivableDocumentPageV1`,
  `OpenReceivableDocumentCursorV1`). Every entry is re-derived through
  `get_document_exposure`, so a corrupt or stale exposure refuses the call.
  Billing applies no policy ordering: pages follow the stable
  `(issued_at, document_id)` keyset, the limit is 1 to 200, and one call scans
  at most 1000 candidates (a page may be short while `next_cursor` is set).
  The caller (flows) orders by policy. No table, no migration.

## 0.1.0a11 — published 2026-11-10

- Breaking for adapters: `NumberingProvider.allocate` now also receives a
  required `document_reference`, the stable identity of the document being
  numbered (an invoice: `billing:<document id>`; a credit note: the
  command-stable `billing:credit:<correlation id>`, never a per-execution
  value). A locked or enrolled Numbering series requires it; the adapter decides
  whether to forward it for a series that does not accept it.
- Add `discounts` (`DiscountSnapshotV1`) to the one rated intake Sub uses,
  `AcceptRatedObligationV3`; no other intake version changes. `pre_tax_amount`
  is the net taxable base and the gross is net plus discounts. Discounts are
  retained in the existing immutable obligation envelope (only when present, so
  a10 bytes and replay fingerprints are unchanged; no DDL). Issued line facts
  carry the real `discount_total` and snapshots. Malformed or duplicate
  discounts, and Tax determined on a base larger than the net, are refused.
- Add `get_document_exposure` (latest sealed per-document exposure, re-verified
  against receivable resolution; imported items return the opening-owned V2),
  `get_document_withholding_evidence`, and `list_accounting_facts`: a
  total-ordered keyset-paged accounting-fact index, and
  `summarize_accounting_fact_window`, the separate once-per-window-close count
  and chained digest. A consumer closes a window only after a summary taken at
  least one settle interval after its end, because `occurred_at` is business time.
- Refuse a document whose lines carry different `service_ref` with
  `exposure_service_ambiguous` at drafting, issuing and exposure derivation,
  because exposure is anchored to the first line.
- An unlinked taxed credit needs no new code: a credit note always requires an
  issued invoice and non-cash grants carry no tax.
- Adapters should raise a number collision as
  `BillingRuleViolation("document_number_collision")`; Billing does not import
  Numbering and does not map it.
- Write-off needs no new command: it is a `grant_non_cash_credit` whose
  classification category is a policy value, restricted to the document and
  applied on issue. Choosing credit note or grant is the caller's policy.
- Not changed: a credit note larger than the open receivable is still refused
  (`credit_exceeds_receivable`). Excess is composed above Billing (unapply prior
  discharges, then issue the full credit note); Billing gains no excess path.
- Source preparation only; hosted CI and publication remain required.
- ST-3 (recorded widening D-5; includes unreleased `bi_0013`): the opening seal
  records a hold list (`SealOpeningWindowV1.held_account_ids`); one held partition
  per command may later be admitted by `admit_held_opening_account_v2` with a
  reviewed disposition digest and an Approvals maker-checker decision (port only;
  tenant-plane hold awaits approvals 0.1.0a9). A deferred constraint trigger makes
  its `opening_hold_admissions` audit row mandatory. `seal_opening_window` accepts
  an `authority_switch` participant inside the seal savepoint; its
  `OpeningAuthoritySwitchReceiptV1` is stored append-only. `opening_lane` is the
  import-specific assembly authority port.
- ST-4 (A10 gate and held-draft rules superseded by the ST-4c projection below):
  opening admissibility as data (`opening_admissibility`): a reviewed rules
  document (digest-identified, closed schema) plus the A10 account classes
  decide account-level admissibility (A10 `opening_exact` and post-cutover
  `match`; D-2). Every census blocking code is blocking: no rule relaxes,
  drops or carries a finding, and an unlisted code is blocking. Every held
  partition has exactly one reason from a closed, rules-ordered set. Every
  member account of a reseller partition must pass A10. A held partition that
  is not admitted leaves the manifest and is routed to governed correction; the
  decision record (`admissibility.json`) lists every routed-out partition with
  its reason, rows and stated lane figures, plus the exact archived totals.
  There is no manifest-marked hold (ST-4c, Q1): the seal's hold list equals the
  typed operational holds (`target_not_pristine`, `import_refused`) exactly
  (`opening_hold_list_invalid`). Drafts
  are excluded and archived (X1; accounts whose A10 cause is a draft allocation
  are held), V1 gates only status-open documents, D-3 needs a supplied original
  for each, and reseller option A re-keys a managed subscriber's invoices and
  wallet lanes to the reseller account with one `service_ref` per document.
- ST-4: import cost: the command digest binds the manifest by its fingerprint (not its
  body) and the fingerprint/analysis are cached per manifest object, so
  `import_opening_account_v2` validation no longer scales with manifest size
  (15,293 commands: 28 s). The a11 command digest therefore differs from a10's.
- ST-4: scale (F2): the complete-manifest validators run once per run
  (`preview_opening_account_import_batch_v2`, `opening_validation_scope`), the
  rehearsal reporter indexes instead of rescanning, and a manifest-wide
  finding is reported once, not once per partition. Single-command validators
  keep their results and ordering.
- ST-4: land Gap A: a supplied, bound original satisfies the M8-minimal documentary
  check (commit c15afa28).
- ST-4: D-8 Starter-native verifier production profile: `OpeningVerifierProfileV1`
  pins scope, installation, audience, both public-key fingerprints and
  validity/revocation ceilings; `ProfileBoundAuthorityReaderV1` refuses any
  policy outside the pin (unavailable, never verified);
  `build_starter_native_opening_import_verifier_v2` composes the signed a10
  providers. Rehearsal and production audiences are disjoint namespaces.
  Import reversal is never granted. Provision/seal verifiers and Gap C
  (cash lots) are not part of this change.

- ST-4c: opening variance ledger (bi_0014; a11, unreleased). The opening never
  adjusts what it transfers: per partition and lane, `source = transferred +
  sum(variance)` exactly in minor units, no tolerance, no netting. A variance
  (`OpeningVarianceV1`) is an append-only typed record with structural identity
  (window, account, import receipt, currency, lane, basis, reason, source), a
  closed reason policy enforced in Python and SQL, and a lifecycle
  (`recorded -> resolving -> partially_resolved|resolved`, terminal
  `voided_by_import_reversal`). `ledger_lane` (the Sub AR lane) is the only
  money basis; `document_residual`, `stored_balance` and `rebuild` are memo
  bases. Equations E1 (per lane), E2, E3, E4 (`rebuild = T_net + sum V`, with
  `T_net = T_receivable - sum(lot residuals)`; rebuild basis is receivable-lane
  only) and the per-partition count control are checked at window admission
  (`opening_manifest_unclosed`), at import, and by the seal in SQL over every
  control partition minus the held ones. `opening_control_report` is the
  closure report Finance acknowledges; cumulative flow fields appear as history
  rows with no equation. Resolution is per record: `position_corrected`
  (credit note, or a grant applied on issue, for a receivable-lane
  customer-adverse variance only), `recognised` (`recognise_opening_variance`;
  never on a customer-adverse variance; GL class per lane), `evidence_confirmed`
  (memo bases only). Every resolution needs the Approvals port and the database
  checks maker-checker parity with hold admission. A posting group a resolution
  references cannot be reversed (`variance_resolution_locked`).
  Breaking: `OpeningManifestV2` gains `measures`, `variances` and
  `batch_controls` in place, so its shape and fingerprint change in a11 and an
  a10-produced manifest is refused (`partition_measures_missing`). A V5 fact
  embeds the whole V2 manifest, so V5 payload bytes for new imports differ from
  a10 (no V5 fact exists anywhere: zero opening import receipts); the opening
  has never run (zero rows in the opening tables on Sub production and staging).
  A partition with cash lots keeps its credit under the cash control: a
  non-zero unapplied-credit or funding figure, or a credit/funding variance,
  is refused (`credit_transfer_unsupported`) until Gap C (ST-4b). B7 and
  B1/IssueCharge are not resolution primitives yet.
- ST-4c: the projection carries findings instead of dropping or blocking them.
  `carry_as_variance` is a third rules policy (fixed code to reason map, never
  chosen by the rules document): invoice-level `residual_mismatch` ->
  `stored_balance_difference`, `excluded_nonzero_residue` ->
  `closed_invoice_residue`, `negative_residual` -> `negative_residue`, an open
  invoice with `invoice_provenance`, `duplicate_identity`, `source_after_cutoff`,
  `currency_precision`, `settlement_provenance`, `original_evidence_missing`,
  `status_residual_conflict` or `v1_vat_evidence_missing` ->
  `item_not_transferred:<code>` (the item is excluded from T and its residual
  carried), account-level mismatch -> `lane_document_difference`, `unbacked_lane`
  -> `lane_not_transferred:<lane>`. The adapter converts the plan to exact minor
  units: at most one `unexplained_difference` remainder per partition, lane and
  basis, T never adjusted, unattributed control rows in batch scope (no GL fact).
  A10 is no longer an admission gate (W-V1): the new drafter input
  `billing.opening.a10-account-rebuild.v1` (`{account_id, currency, rebuild_net,
  opening_class, post_cause}`, NGN only, exact decimal, credit-positive) gives
  `rebuild_minor = -to_minor(rebuild_net)`, one attributed rebuild memo variance
  per partition (`rebuild_<class>`, else `rebuild_<cause>`, else
  `unexplained_difference`), `a10_missing` / `a10_currency_unsupported` control
  gaps, and `a10_rebuild_required` when the input is absent. The decision record
  adds the routed-out reconciliation (census = manifest + batch + routed-out, by
  invoice rows and by currency and lane) and binds its digest. The read-only
  converter `scripts/billing_opening_a10_rebuild_converter.py` turns the A10
  classified CSV into that input. Not yet carried: reseller fold
  (`reseller_fold_unavailable`) and settlement-level `draft_allocation`
  itemisation; both stay routed out or fall to the lane remainder.
- ST-4c: Add distinct AccountingFactV6 (`billing.accounting.fact.v6`) for ledger-lane opening-variance recorded/voided/recognised/position-corrected facts on effect-free posting groups; V1-V5 classes and literals unchanged; V5 payload carries the a11 manifest. Each fact sits on its own posting group of the same kind;
  `opening_variance_recorded` and `_voided` are written by the opening principal,
  the other two online. `opening_variance_events.fact_posting_group_id` (unique,
  checked at commit) binds each event to its group. The voided fact links the
  recorded one with `reverses_fact_id`; the group's `reverses_group_id` stays NULL.
  `get_accounting_fact_v6` is the verified reader. The three
  `billing.opening.variance.*` outbox types are dropped.

## 0.1.0a10 — released

- Retain complete public Tax V2 dependency evidence and opaque `rv2:` seals,
  original commercial identity, prior references and selected component snapshots.
  Trusted Tax public reads verify owner seals; Billing validates observable
  bindings and includes all copied fields in its original-fact digest and replay.
- Add rated V3 authoring/original facts and a V3 reader retaining V1/V2 support.
  Explicit unique `commercial_sources` exactly match principal wrapper identities,
  retaining distinct original line amounts and identities through obligation JSON,
  multiline original facts and both replay identities. Reject successor
  sets/components at legacy evidence boundaries before persistence.
  Add lossless V2 withholding lineage to the existing discharge owner.
- Reuse immutable bi12 JSON with an explicit versioned array envelope; no DDL.
  Prior audit results are not added to charged invoice tax. Legacy readers,
  evidence and all migration bytes remain unchanged. Tax V2 remains tenant-only.
- Includes the complete a9 evidence and bi12 source allocation. Full hosted CI,
  direct published a8/bi11-to-bi12 migration qualification on both planes and
  verified a10 publication precede adoption; no publication is claimed.

## 0.1.0a9 — unpublished source allocation, superseded by a10

- Add V2 rated invoice authoring and original document facts carrying complete
  Tax-authored determination sets, rule versions, source/result fingerprints,
  ordered components, fixed/progressive lines and exact currency/scale.
- Retain expected withholding solely as an expected display deduction, separate
  from accepted withholding discharge proof and full accounting receivables.
- Add bi12 nullable immutable obligation JSON and final append-only guards.
  Predecessor V1 evidence is never rewritten. The successor public document
  reader verifies and decodes original V1 or V2 facts for presentation.
- Add get_accounting_fact_v1 for scoped immutable original posting/allocation
  evidence, including after linked reversals; no current capacity is inferred.
- This allocation is carried completely by a10; hosted CI and the final a10
  release remain required. It is not an intermediate publication prerequisite.

## 0.1.0a8 — UNRELEASED

- Require Kernel `>=0.1.0a105` for the public exact-root rollback-latch
  observer consumed by opening-window refusal recovery. Published a98-a104
  contain the transaction module but omit this export; a89 remains Billing's
  namespace allocation. This source dependency update does not establish
  Kernel or Billing publication, hosted acceptance, or product adoption.

- Add atomic V2 opening receivables plus cash, preserving original documentary
  invoice totals, individually evidenced historical discharges and exact
  residuals without native issuance, new revenue or historical cash replay.
- Resolve native and imported receivable backing through one owner across
  current allocations, WHT, non-cash, eligible credits and linked inverses.
- Add immutable historical bases, replay evidence, shared live/historical
  source-claim occupancy and truthful imported ExposureV2 on both planes.
- Add distinct V5 opening recognition/full-unwind facts and verified immutable
  queries; retain prior command/fact shapes and permanent source claims.
- Add bi8 scoped affinity, isolation, append-only, current/historical capacity,
  completeness and source-identity enforcement. Source and hosted acceptance
  remain pending; no package adoption or production activation is implied.

## 0.1.0a7 — UNRELEASED

- Add verified cash-only opening import into pristine accounts with immutable
  receipts, source lots and permanently occupied exact cash-source claims.
- Complete deterministic ordinary/opening credit draw, atomic partial/full mixed
  draw inverses, independently evidenced unused refunds and full one-time refund
  corrections, and authority-qualified full import unwind after net uses close.
- Add distinct AccountingFactV4 with actual source effect identities and complete
  source ordering; preserve settlement-only V1–V3 facts and historical retry bytes.
- Add `bi_0007_opening_cash` scoped affinity, append-only, room and completeness
  fences on both planes; preserve accepted rows and migration bytes. No release,
  operational import, adoption or activation is implied.

## 0.1.0a6 — UNRELEASED

- Add provenance-only immutable cash-source claims for real settlements on both
  planes, consumed inside existing settlement acceptance and verified on replay.
  Add `bi_0006_cash_source_claim` after accepted `bi_0005`, backfilling exact
  stored identities without changing money, confirmation or retry evidence.
  Fence older writers: new settlements need their claim at transaction completion.
  No opening money, import command or activation is introduced.

## 0.1.0a5 — UNRELEASED

- Add immutable complete-population opening-manifest contracts and a pure
  cash-only evidence preflight with exact minor-unit controls, ordered cash
  provenance, target bindings and counted refusals. Require an assembly-owned
  verifier for source/cash/migration evidence; candidate hashes establish no
  authority. No opening writer, storage, spendable funding or activation.

## 0.1.0a4 — UNRELEASED

- Refresh the locked draft header and line identities before issuance so a
  concurrent draft edit cannot leave emitted facts with stale totals. Preserve
  pending caller edits and exact replay without completing its transaction.
- Add append-only non-cash grants, applications, partial application reversals
  and unused-grant reductions, with separately linked replacement grants.
- Keep cash funding and prepaid credit distinct. Ordinary account credits carry
  zero tax; immutable versioned classification evidence records policy authority.
- Add bi_0005 grant/effect and V2 position evidence in both persistence planes,
  source-version and per-invoice capacity guards, and V3 accounting facts while
  preserving prior V1/V2 contracts. Older writers require a fenced upgrade.
- Source candidate only; publication, product UI/adoption and production writer
  acceptance remain separate gates.

## 0.1.0a3 — UNRELEASED

- Add invoice-bound withholding acceptance and linked reversal with immutable
  Tax determination evidence. Accepted withholding reduces receivables without
  creating cash or spendable credit; claims and certificate observations remain
  distinct from the accepted financial decision.
- Add append-only withholding effects in `bi_0004_withholding_discharge`, scoped
  account/document links and natural Tax-component duplicate prevention.
- Publish WHT-specific accounting facts as V2 while preserving existing V1
  events and fingerprints. Source presence is not release or Sub adoption.

## 0.1.0a2 — UNRELEASED

- Close line insertion, reparenting, edits and deletion for every issued
  document kind in additive `bi_0003_issued_line_freeze`, with parent-row
  locking and bounded migration table locks. Stage credit-note
  initial lines before issuance in the caller's transaction. Preserve both
  earlier migrations and historical monetary/fact evidence.
- Replay fully credited invoices' exact credit-note commands before mutable
  receivable admission, preserving original keys, fingerprints and effects.
- Add exact-version immutable invoice-fact queries with persisted identity and
  recording metadata, strict typed decoding, scope/plane isolation and raw-JSON
  presentation-digest verification. Reads preserve caller transactions and
  ignore dirty ORM state; malformed or missing evidence fails closed.

- Add typed homogeneous multiline drafts with structural account/obligation
  affinity, exact explicit prices and immutable authoring provenance in an
  additive migration. Keep the released root migration unchanged.
- Preserve all line tax evidence and common FX, revalidate at issuance and
  prevent adding lines to an issued invoice. Exclude unrelated document-owned
  tax/FX rows from credit-note facts.
- Add a typed, atomic cash-backed available-credit draw across confirmed
  settlement sources, preserving allocation provenance and existing financial
  events. Non-cash grants and opening-balance funding remain separate gaps.
- Refuse allocation from reversed settlement funding while preserving exact
  replay of a previously accepted command.
- Rebuild position attestations from persisted numeric values, independent of
  ORM cache scale, preserving the hash algorithm and strict drift refusal.
- Reconcile Sub-first, pay-then-record and open-items-only cutover preparation.

## 0.1.0a1 — historical publication

Publication tag: `dotmac-billing-v0.1.0a1`, peeled commit
`92a1626b16d7e068f92536d8cfcb2ef9b6f270c2`. The historical record is in
`docs/inventories/module-release-legacy-unverified.json`; it does not constitute
a modern wheel-verification receipt or evidence of product adoption.

- First complete Billing V1: frozen obligation, settlement, receivable,
  accounting and invoice-document contracts; one pure financial engine; two
  declared persistence planes; immutable source effects and rebuild hashes.
- Every published command/fact is a closed immutable type, including nested
  party/address/payment/line/discount/accounting-effect values; architecture
  canaries reject `Any`, `object` and unshaped dictionaries at the boundary.
- Make Billing the sole owner of the account/currency `ReceivablePositionV1`
  and invoice-grained `ReceivableExposureV1`, use kernel `Money` on every public
  amount, own financial state without a `reversed` steady state, and preserve
  each exposure's subject/service, service-period and due-date provenance.
- Record exact renderer/file provenance for the Billing-owned official
  artifact relation, with a structural record key, a separate current-row-bound
  repair key, strict semantic mismatch, append-only repair supersession and
  cancellation withdrawal. Artifact creation serializes with cancellation and
  refuses a new medium after a void while preserving historical replay.
- Keep `RatedObligationOutputV1` exclusively on Subscriptions' public surface;
  Billing owns and names only its distinct `AcceptRatedObligationV1` input.
- Emits complete typed accounting and receivable-position payloads through the
  transactional outbox, including applied tax/FX and immutable service-period
  and due-date-basis evidence.
- Ships the `bi_0001_billing` migration with exact tenant RLS and platform
  revokes/grants, including column-only account lock permission, plus live
  concurrency, immutability, isolation and rebuild/hash canaries.
- Declares the exact `dotmac-kernel>=0.1.0a89` and `alembic>=1.13` floors so a
  clean wheel install can import the published migration/linking surface.
- Includes no provider connector, rendering, byte storage, cadence, collections
  policy, product consequence, numbering engine, GL or ERP posting path.
