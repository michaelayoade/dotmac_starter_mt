# Opening receivables source qualification

Candidate source inventory for the Sub-first M8 opening-receivables slice,
2026-10-13. This records implementation evidence, not release, adoption,
production capture or authority-transfer approval.

## Observed coordinates

- Starter cash-lifecycle predecessor: `29356f6ab58c214edf3e8ac9f3c627571663e1d3`
  (PR 822, hosted acceptance pending when this inventory was written).
- Sub source: `211131024bd70f07d3f15562839019f5a6b9d60f`.
  The qualifying `_common.py`, `invoices.py` and `credit_notes.py` bytes are
  unchanged from the architect's `89360003fad9c261823b9d41fa24bd3340d3584f` audit.
- ERP source: `93d7e111d9278b8377bac311e78308c99f51da79`.
- Vendor CP and Integrator ownership inventory: the immutable coordinates in
  `packages/dotmac-billing/EXTRACTION.toml`; these are historical source
  coordinates, not assertions about current deployment.

## Qualifying behavior and parity scenarios

| Product source | Behavior retained at the typed boundary | Qualifying test/scenario |
|---|---|---|
| Sub `app/services/billing/_common.py:resolve_invoice_settlement_amounts` | Separate applied payments, credits and prepaid opening funding, including proportional refund reopening. One receipt may fund several invoice applications. Capture canonical results and actual supporting identities; do not recalculate Sub's source semantics independently. | `tests/test_payment_settlement_allocation_evidence.py`, `tests/test_partial_refund_invoice_state.py`, `tests/test_refund_money_correctness.py` |
| Sub `app/services/billing/invoices.py` | Original invoice identity, immutable issued time, lifecycle guards, exact closure evidence and truthful unknown due-date provenance. | `tests/test_invoice_issued_at_invariant.py`, `tests/test_invoice_transition_guards.py`, `tests/test_invoice_closure_evidence.py` |
| Sub `app/services/billing/credit_notes.py` | Invoice affinity, credit application/inverse and concurrency restrictions. Imported original gross cannot manufacture fresh credit capacity. | `tests/test_credit_notes.py`, `tests/integration/test_credit_note_issue_concurrency.py` |
| Sub `app/services/billing/opening_export.py` | Complete population, original totals and separate historical discharge components, canonical residuals, currency partitions and counted evidence gaps. | `tests/test_billing_opening_export.py`, `tests/integration/test_billing_opening_export.py` |
| Starter predecessor Billing | Caller-owned transactions, trusted import authority, permanent cash receipt claims, zero-residual lots, deterministic mixed draws/inverses, refund/unwind, actual-row fact verification and separate current/historical capacity guards. Extend these owners rather than add a second posting or arithmetic engine. | `tests/test_billing_opening_cash_import.py`, `tests/unit/test_billing_opening_cash_contracts.py` |

The new persistence and authenticated historical-backing contract are
PORT-DELTAs. Sub's existing repair importer is not a qualifying source for
that contract: `subledger_opening.py` has zero production callers in the
recorded extraction inventory. Mutable balance repair, metadata identity
guesses, tolerance/clamping and historical status as monetary authority
remain excluded.

## ERP importers: production-used, with different guarantees

ERP **does have production importers**; their existence is not denied.

`app/services/finance/import_export/opening_balance.py:OpeningBalanceImporter`
is called by `app/api/finance/opening_balance.py`. It constructs a
`JournalType.OPENING` GL journal against the chart of accounts and fiscal
period. The period-lock guard has a real test in
`tests/ifrs/import_export/test_opening_balance_period_lock.py`, refusing a
closed period before adding rows. That remains ERP-owned statutory/GL
behavior, not an invoice-grained operational residual importer. Its
sub-cent balancing tolerance and internal commit/rollback are not ported
into Billing. The period-lock scenario belongs to eventual ERP accounting
fact acceptance, not a fabricated GL balancing entry in Billing.

`app/services/finance/import_export/invoices.py:InvoiceImporter` is called
by `app/api/finance/import_export.py` and the import/export web owner. Its
grouped multiline document shape is relevant source evidence. It creates
ordinary ERP invoices with mutable `amount_paid`/status, defaults missing
dates and currency/FX from current context, derives paid amount from total
minus balance with a negative clamp, and skips an existing organization /
invoice-number pair without a content fingerprint. The located
`legacy_construct_only` tests protect importer authority refusals; they do
not qualify an immutable historical monetary lifecycle. Retain those
near-miss/refusal scenarios and documentary field inventory; do not copy
the defaults, clamp, duplicate skip or mutable status algorithm.

Thus neither ERP importer supplies the authenticated immutable operational
AR backing, exact replay binding and full inverse lifecycle required here.
Existing ERP coverage and tax/FX structure remain references under the
parent dossier; this successor does not move GL, fiscal period, treasury,
tax-return or statutory-accounting authority into Billing.

The remote pinned inventory also checked Vendor repository ID `1317527604`,
now named `michaelayoade/dotmac_platform_control_plane`, at
`f8f8c3fd636e663e4a17275c19e82fc1667aa52a`. Its untruncated tree and inspected
contract models/services/tests contain commercial contract lifecycle and
frozen price snapshots, with no operational opening-AR importer. Integrator
at `35167813c83ab0ec29c683259ad31479503d812f` likewise has no financial-importer
paths; `product_port.py` provides generic authenticated destination
transport. These pinned owner dispositions do not assert current production
state or authorize moving transport into Billing.

Sub's canonical payment component rounds its summed proportional refund
attribution. A bounded exact-minor import must refuse fractional-attribution
evidence it cannot preserve; it must not independently round application
edges or assign invented pennies. Historical inverse evidence must preserve
ordering, backing affinity and cumulative bounds. Non-cash grant replay
identity is the existing owner/ref pair; its version belongs to immutable
content rather than providing another spendable identity.

## Complete successor boundary

One V2 account/currency import admits evidenced open AR and supported cash
atomically into a pristine target. Each imported invoice keeps original
documentary gross, individually evidenced historical discharges and exact
opening residual in immutable backing, without a rated obligation, fresh
invoice number, native issuance, new cash acceptance or new revenue.
Unknown collection subject/timing/due provenance remains explicit and
excludes automated Collections admission while retaining AR/reporting.

Historical application identities are distinct from parent money receipt
identities. Fully consumed historical cash receipts use authenticated
zero-residual lots and the same permanent claim namespace as live cash.
Historical WHT, non-cash and credit issuance/application identities require
exact authenticated mappings to their live owner identities; absent mapping
blocks import. Never fabricate Kernel success receipts or current monetary
effects to mark historical consumption.

All allocation, credit draw, WHT, non-cash and eligible credit-note commands
resolve native issuance versus imported residual through one backing owner.
Historical discharge is never subtracted twice. Generic document void and
reversal refuse imported backing. Full import unwind appends exact inverses
only after subsequent effects are reversed by their owners; source claims
remain permanent. Partial principal amendment and automatic reimport remain
outside this slice.

## Acceptance and retirement

Both persistence planes need scoped affinity, append-only and permanent
identity constraints, RLS/platform isolation, deferred completeness, current
capacity and source-version historical exposure checks. Hosted PostgreSQL
canaries must prove exact controls, source replay refusal, all supported
discharges/inverses, atomic cash-plus-AR rollback, multi-command outer
transactions, truthful unknown collection provenance and full unwind.

No local test execution or live database work qualifies this inventory.
Source CI and owner approval, released-artifact verification, a real Sub
documentary/cash verifier, complete-population control closure, rehearsal
and the coupled old-writer retirement remain separate gates.
