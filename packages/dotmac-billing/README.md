# dotmac-billing

`dotmac-billing` is the single reusable owner of operational receivables. It
accepts immutable rated obligations and independently confirmed settlements,
owns invoice and credit-note meaning, posts immutable allocation/correction
effects, and derives separate per-currency receivable, available-credit and
prepaid-funding positions.

Subscriptions alone owns `RatedObligationOutputV1`; Billing neither defines nor
names that producer contract. Billing owns only its distinct
`AcceptRatedObligationV1` command, and an assembly maps between them.

It is not a general ledger, PSP connector, subscription scheduler, collections
policy engine, renderer, file store, numbering engine or product consequence
owner. Applications install their own selected plane and synchronize through
the frozen V1 contracts; they never share `mod_billing` rows. Every public V1
command and fact is an immutable closed type. Every amount uses the
kernel-owned `dotmac_kernel.money.Money`; Billing does not publish a second
money type. `ReceivablePositionV1` is the account/currency aggregate with three
separate financial lanes. `ReceivableExposureV1` is the invoice-grained fact
with subject/service, service-period and due-date provenance that an assembly
may translate for Collections. Billing owns financial state at both grains;
reversal, refund and chargeback remain immutable movements that may reopen a
fact, not steady states. Its nested party, address, payment, line, tax/FX and
accounting-effect values are typed as well. Boundary
canaries reject `Any`, `object` and unshaped dictionaries.

Billing records one official artifact structurally per fact version and media
type. Physical repair is a separate typed command that names the exact current
relation, preserves the semantic digest, appends the replacement opaque file
relation and retains supersession evidence. Billing never stores or dereferences
file bytes. Artifact creation locks the invoice lifecycle and refuses a new
medium after cancellation; a pre-existing idempotent command still replays its
historical relation.

`get_current_settlement_allocation` observes one exact original allocation via
`GetCurrentSettlementAllocationV1` and returns frozen
`CurrentSettlementAllocationV1`. It locks the Billing account in the caller's
READ COMMITTED transaction, validates exact scope/account/group/settlement/invoice
and Money, live invoice issuance and settlement acceptance backing, and follows
linked deallocation and reversal effects. Its state is `current`, `reduced`,
`removed`, or `inactive_backing`; only `current` with the exact original and
remaining Money proves full present funding. Reallocation lacks an original
allocation pointer and is refused for that settlement/invoice pair. Missing,
malformed or ambiguous lineage and other isolation levels are refused. No
aggregate balance or immutable accounting fact substitutes for this check.
Re-observe before each product decision within the same transaction; a returned
observation is not a reusable admission token. Allocation command replay retains
its historical semantics. This source addition is not a published package.

Settlement replay preserves the published source-key and money/version
fingerprint algorithm. Billing additionally compares persisted account, funding
lane, confirmation evidence and exact source identifiers before returning a
settlement, including after a concurrent acceptance. A retry's `observed_at` is
transport receipt metadata and retains the original observation.

The database enforces Billing's internal relationship graph on both planes:
obligations and settlements belong to their account, document lines belong to
the same obligation/document pair, posting and allocation effects cannot cross
accounts, financial facts point to their source posting group, and artifact
repairs remain inside one semantic fact and media type. A rated-obligation
correction may append one same-account successor only before the obligation is
documented; after issuance, correction is a credit note.

The tenant plane requires `TenantScope`, `tenant_id NOT NULL` and forced RLS.
The platform plane requires `PlatformScope`, has no tenant column or RLS, is
revoked from `app_user`, and is reachable by `platform_api`.

Adopters relate their own subjects to Billing accounts with the public
`link_tenant_billing_account` or `link_platform_billing_account` migration
helper. The plane is named by the function, never selected by a boolean. The
tenant helper emits a composite Billing-account reference plus forced RLS; the
platform helper emits no tenant column or RLS and revokes all table and column
privileges from `app_user`.

Services accept a caller-owned SQLAlchemy session, mutate and flush only.
`dotmac_kernel.db` remains transaction authority.

Every Billing event code passed to the kernel outbox is declared on the module
manifest, including rated-obligation acceptance, document facts and artifact
relations, settlement acceptance, accounting facts, aggregate positions and
invoice-grained exposures. An architecture canary derives the emitted set from
the service and requires an exact match, so a new routing code cannot bypass
the manifest-owned vocabulary.

The historical first release candidate is `0.1.0a1`; the current unreleased
development successor is `0.1.0a9` and requires
`dotmac-kernel>=0.1.0a105` plus
`alembic>=1.13` for its published migration/linking surface. Its wheel contains
`py.typed`, the manifest and the complete `bi` migration lineage.

Opening-window refusal recovery consumes the public
`dotmac_kernel.transactions.conflict_savepoint_requires_rollback` observer.
Kernel a98 introduced the module but a98 through a104 export only
`conflict_savepoint`; the rollback observer requires a105. Billing retains its
a89 namespace allocation. This dependency declaration is source preparation;
verified Kernel publication and hosted floor checks remain required before
Billing publication or adoption.


`ApplyAvailableCreditCommand` and `apply_available_credit` draw an explicit
positive Money amount from one account into one issued, noncancelled invoice.
They serialize the account, replay the exact command before checking current
funding, and create one allocation posting group with per-settlement edges.
Both the immutable account available-credit position and eligible source room
cap the draw. Only independently confirmed `available_credit` settlements fund
it; `prepaid_funding` is excluded. Sources sort by `occurred_at`, then UUID.
An exact, linear acceptance/reversal chain determines whether cash backing is
active: odd reversals exclude it, even reversals restore it, and ambiguous
identity/effects/branches fail closed. Direct settlement allocation shares
this backing and target guard. Existing accounting, position and exposure
facts/events carry the effects; this adds no event vocabulary or settlement.

The source is `dotmac_sub` commit
`c7707880232c9298ef89c24a1aea66d35bd3a1e6` (local inspected snapshot,
not a production revision claim),
`app/services/billing/account_credit.py::_source_payments` and
`AccountCreditApplications.apply`: account locking, per-currency account and
source room caps, deterministic source selection, and the
`min(remaining, settlement_room)` allocation loop. Source tests inspected:
`tests/test_account_credit_deposits.py::test_eligible_invoice_statuses_consume_credit_in_fifo_order`,
`tests/integration/test_account_credit_concurrency.py::test_two_applicators_cannot_spend_one_credit_source_twice`,
and `_source_payments` characterization in
`tests/test_opening_settlement_correction.py`. Fresh migrated PostgreSQL
canaries cover both Billing planes; these are new extraction evidence, not a
claim that Sub tests execute against this module.

This slice targets one invoice and an explicit amount. It does not port Sub's
automatic invoice sweep, reviewed opening positions, reserved-purchase or
legacy-payment metadata branches, noncash credit grants, issuance funding
reservations, or product-specific prepaid finalization. No new table, mutable
balance, external contract or sibling-product import is introduced.

The unpublished successor also adds `DraftDocumentLine`,
`CreateMultiLineDraftDocument` and `create_multi_line_draft_document`. This
command requires at least two accepted obligations with one account, Money
identity, subject, service, collection timing and service period. Explicit unit
prices and quantities must reproduce the rated amounts exactly. Tax snapshots
retain their obligation provenance and must reconcile exactly; FX must be
uniform. Issuance rechecks the aggregate and immutable authoring mode preserves
the distinction from the shipped single-line command.

The additive `bi_0002_multiline` migration introduces account-affinity line FKs,
immutable document authoring provenance and issued-invoice insertion refusal.
The additive `bi_0003_issued_line_freeze` extends insertion refusal to every
issued document kind and serializes edits, deletion and reparenting with
issuance by locking and checking both parents. Migration table locks drain
predecessor writers before function replacement, with a five-second lock
acquisition timeout. Credit notes stage
their initial line under a draft parent before issuance and fact creation in
the same transaction. Both earlier migrations and historical facts stay unchanged.
Exact credit-note replay precedes mutable receivable admission, including
after a full credit exhausts the invoice's outstanding amount.
Downgrade after explicit multiline authoring is refused. Heterogeneous invoices and
line-specific tax/FX wire attribution require a later contract design. See
`docs/inventories/billing-multiline-extraction.md` for source dispositions,
limits and required hosted proofs; no Sub adoption is claimed.

Position rebuilds read persisted scalar effects, so freshly flushed ORM decimal
representations cannot change the attestation hash. The existing hash algorithm
and strict comparison with immutable position facts are preserved. Historical
facts that already disagree with their persisted effects still fail closed;
this change neither rewrites them nor supplies a compatibility fallback.


`get_invoice_document_fact` accepts an explicit routed `scope` and frozen
`GetInvoiceDocumentFactV1(scope, invoice_id, fact_version)` request. It returns
`StoredInvoiceDocumentFactV1` with the persisted fact UUID, contract version,
recording instant and typed `InvoiceDocumentFactV1`. Version selection is exact;
a missing version, mismatched route, unsupported contract, malformed evidence
or mismatched presentation digest raises a coded `BillingRuleViolation`.

The query reads only immutable fact-table scalars under `no_autoflush`. Dirty
ORM objects, later credit notes and cancellation cannot replace the requested
historical evidence, and the caller retains transaction control. Every V1 field
must be present, including explicit nulls and empty arrays. Stored decimal text
and currency minor units are decoded exactly, with no current-settings lookup,
coercion, default or rounding. Datetimes retain microsecond precision in both
wall-clock time and UTC offset, including positive/negative subsecond offsets.
The reader accepts the producer's full ISO datetime spelling, plus historical
`Z`, space separators and comma fractions; unsupported or higher-precision
forms fail closed with `billing.document_fact_corrupt` and generic diagnostics.
Digest verification fingerprints the original
stored JSON excluding `presentation_model_digest`, preserving the producer's
historical algorithm and textual representations.

This reader supplies Billing evidence only. The rendering adapter still needs
an explicit provenance mapping and reconciliation between Billing's presentation
digest and Rendering's projection digest. The Billing-owned official artifact
relation remains the existing `RecordDocumentArtifactV1`/reconciler boundary.

## Opening evidence preflight (source candidate)

`preview_opening_account_import` is a pure query over an immutable
`OpeningPreviewRequestV1`. It returns a typed preview and counted blockers;
`admissible` means only that this evidence passed preflight. It creates no
settlement, monetary fact, spendable funding, import permission or activation.
There is no database/network/file access or transaction boundary in the query.

The candidate manifest preserves the complete declared source population,
per-member eligible/excluded/blocking dispositions and exact counts, every
account/currency partition and explicit precision, signed integer minor-unit
lane controls, and immutable cash lots with ordered exact historical
allocations, refunds, reversals and reservations. It binds actual source
installation, repository/schema/snapshot capture coordinates, economic and
recorded cutoffs, source-authority reference and both mapping versions. The
versioned domain-separated fingerprint includes every identity and ordered
piece of provenance using Kernel's existing fingerprint helper. Equal balances
with different provenance are different candidates. Aware timestamps are
normalized to UTC only for fingerprinting, preserving their original contract
values. Structural integer inputs are limited to 4096 bits and are never
coerced or truncated. A hash does not seal or
prove a candidate, and declared snapshot coordinates alone do not establish a
complete trusted source capture.

The first supported selected partition contains only evidenced residual cash;
open AR and all other lane controls must be zero. This restriction is a source
implementation boundary, not a reopening of agreed WHT, VAT or noncash policy.
The controls retain the Sub census lanes (`collectible_receivable`,
`unapplied_customer_credit`, `prepaid_funding_reserved`,
`prepaid_funding_consumed`, `written_off_total`, `refunded_total`,
`adjustment_total`) alongside explicit cash/noncash credit and open AR controls.
Unsupported selected partitions are refused in their entirety. Other declared
partitions remain in the manifest and its fingerprint; selection does not
silently discard population or convert unsupported balances into cash.

For each supported cash lot, confirmed gross minus exact historical allocations,
refunds and reservations must equal its residual, without tolerance,
netting, negative clamps, proportional attribution or balance plugs. The lot
preserves typed confirmation owner/reference/version/digest, evidence kind,
confirmed cash amount/currency/precision, and real consumption references and exact live
`source_system` / `source_settlement_key` mappings. Duplicate cash sources are
checked across the full manifest in the actual installation/source namespace.
Every generic reversal is a counted blocker, including a reversal with a
resolved allocation reference. Any consumption with `reverses_ref` also blocks
admission regardless of its kind; relabeling inverse evidence as an allocation,
refund or reservation cannot admit it. Cash-source returns and inverses that restore
cash require explicit distinct semantics before admission. Reservation releases
and other inverse kinds are also unsupported and refused. Original ordered
evidence remains intact. The Sub census is input evidence and is not funded-cash proof.

A trusted application assembly must inject an `OpeningEvidenceVerifier`. There
is no built-in permissive verifier and no request field that substitutes a
caller boolean, actor permission or digest for trust. The verifier must qualify
complete source binding, cash confirmation and complete historical attribution,
live source namespace mapping, and migration authority for the precise preview
operation, scope, manifest, partition and target. The query checks the returned
binding against the request, including target expected-position fingerprint
and posting watermark. Missing/unsupported verification is a counted blocker;
`OpeningVerificationUnavailable` is the bounded expected failure type.
Programmer failures propagate. The verifier implementation used here must
itself be pure; obtaining authenticated evidence is an assembly responsibility.

The complete cash-lifecycle writer remains deferred until actual proof closure
and accepted predecessor sequencing. Such a writer must atomically claim the
shared live cash-source namespace, recheck current target position/watermark
and migration authority, and establish immutable backing understood by draw,
refund and reversal. This query does not call ordinary settlement acceptance or
invoice issuance and does not invent settlement IDs or repost historical cash
or revenue. No storage, schema, release, import or production switch is enabled
by this source candidate.


### Historical receivables opening (unpublished a8 candidate)

`ImportOpeningAccountV2` and `service.import_opening_account_v2` extend the
cash-only V1 lifecycle to one atomic authenticated AR-plus-cash account
partition. Original documentary total, exact historical discharges, opening
residual, party/tax/FX and collection provenance are sealed in immutable
invoice backing. No rated obligation, new invoice number, native issuance,
new revenue or historical cash acceptance is invented. Unsupported or
fractional-attribution evidence is refused rather than defaulted or clamped.

Current allocation, WHT, non-cash, eligible credit-note and cash-draw commands
use the same native/imported backing resolver and their existing accounting
facts. Opening AR recognition and full unwind use `AccountingFactV5`.
`ReceivableExposureV2` preserves unknown legacy collection provenance without
allowing automated collection. Permanent physical source claims prevent live
and historical WHT/non-cash/credit identities from being spent twice, across
accounts and transaction snapshots; claims survive every inverse.

Use `opening_receivable_queries` for exact immutable V2 receipts, bases,
positions, exposures and V5 accounting facts. Public V1–V4 shapes remain
unchanged. This is source implementation pending independent review, hosted
acceptance and owner approval; it does not publish/adopt a package or switch
production authority. The source inventory records qualifying Sub rules and
the explicit ERP importer exclusions.

### Cash-only opening lifecycle (unpublished a7 candidate)

`ImportOpeningAccountV1` admits verified residual cash into a financially pristine
account/currency through `service.import_opening_account`. The injected
`OpeningImportVerifier` qualifies the exact import operation, full manifest,
source namespace, command digest and target fingerprint/watermark. A preview
attestation, actor label or content hash cannot authorize import.

Shared permanent receipt claims prevent later settlement callbacks from accepting
imported sources again. Existing credit drawing spends ordinary settlements and
opening lots; `ReverseCashCreditDrawV1` reverses a partial or full mixed draw
atomically. Qualified unused refunds and full linked refund corrections have
separate commands. Full import unwind requires fresh migration evidence and zero
net dependent consumption; claims remain occupied. Partial principal changes and
automatic reimport are unsupported.

`opening_queries` returns immutable receipt, lot, effect and V4 accounting facts,
with original digests, structural bindings, remaining room and posting watermark.
V4 distinguishes carried-in backing from new payment or invoice/revenue events;
existing V1–V3 contracts remain unchanged. Exact persistence supports currency
precision zero through six within `Numeric(20,6)`, without rounding.

All writes participate in the caller's transaction. Source acceptance, published
artifact verification, real Sub source-proof adapters, ERP mapping, other opening
lanes and production cutover remain separate gates. This candidate has not been
used to import operational balances.

### Tax-authored invoice evidence

`AcceptRatedObligationV2` and `IssueRatedInvoiceV2` reuse `issue_rated_invoice`.
V2 supplies `tax_snapshots=()` and complete Billing-owned
`InvoiceTaxEvidenceV1` copies of Tax's sealed public determination-set result.
Each wrapper explicitly declares `invoice_tax` or `expected_withholding`.
Invoice tax components sum to rated tax; expected withholding is display-only
and never reduces accounting receivable or supplies discharge proof. Billing
does not derive a base from subtotal or select/recalculate rates. The current
Tax evidence contract is tenant-only and must match the routed tenant.

`get_invoice_document_fact_v2` reads and verifies the exact original V1 or V2
fact. V2 includes ordered `tax_evidence` and the original `expected_withholding`
Money property; current balance remains the existing receivable-position owner.
V1 commands and the V1-only reader retain their original contract. `bi_0012`
adds immutable obligation JSON without rewriting historical evidence and refuses
downgrade when successor evidence exists. Assemblies map public Tax results at
the boundary; Billing imports no Tax implementation or sibling package.

`get_accounting_fact_v1(db, scope=..., fact_id=...)` reads an exact historical
V1 accounting fact and verifies its original payload, posting group and effect
rows. Linked reversals leave the original fact readable. Assemblies can bind
receipts to the original allocation without using mutable current capacity;
this read authorizes no new financial movement.
