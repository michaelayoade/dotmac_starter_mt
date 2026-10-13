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
development successor is `0.1.0a2` and requires
`dotmac-kernel>=0.1.0a89` plus
`alembic>=1.13` for its published migration/linking surface. Its wheel contains
`py.typed`, the manifest and the complete `bi` migration lineage.


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
