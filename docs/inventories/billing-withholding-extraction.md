# Invoice-bound withholding discharge successor

The unpublished Billing a3 source is stacked on PR #808 at
`fd1f2feed85dbd5fb3fde2786410e6f8838f6ce0`. It adds invoice-specific
`AcceptWithholdingDischarge` and `ReverseWithholdingDischarge` commands,
mirrored immutable effect storage, `AccountingFactV2` and the
`billing.accounting.fact.v2` event. Existing V1 contracts, payload algorithms and
emissions remain on their existing paths. No migration, publication, adopter
writer switch or production activation occurred.

## Product-first evidence and dispositions

The qualifying Sub source was read at
`3ff8ab17ee64954c973568e930d8cfbf5c97df5f` in
`dotmac_sub-billing-erp-facts`.

| Source | Disposition in this successor |
|---|---|
| `app/services/direct_transfer_intents.py:125`, `_invoice_withholding_tax_snapshot` | PORT-DELTA: preserve frozen invoice basis/provenance and fail-closed evidence; replace Sub's configured-rate arithmetic with an assembly-mapped exact Tax result. Partial acceptance is explicitly supported and leaves residual AR. |
| `app/services/tax_accounting.py:687`, `stage_withholding_tax_receivable` | Port exact account/source isolation, positive finite amounts, immutable money facts and conflicting-evidence refusal; the new owner has explicit tenant/platform planes and invoice-bound acceptance. |
| `app/services/tax_accounting.py:918`, evidence lifecycle | Port separated observation/financial consequence and appended correction; certificate observation is optional typed evidence and never changes money by itself. |
| `app/services/payment_proofs.py:1399,1547` | EXCLUDED: gross `Payment` substitution for net verified cash. The actual-cash `AcceptSettlementV1` and its allocation remain separate and retain their exact amount. |
| `tests/test_direct_transfer_intents.py`, `tests/test_tax_accounting.py`, `tests/integration/test_tax_accounting_concurrency.py`, reseller proof/double-credit canaries | Scenario references for the 107500 invoice / 102500 actual cash / 5000 WHT, replay, isolation, concurrency and duplicate-discharge checks. Starter tests exercise the new owner directly; no Sub writer is retired. |

## Stored meaning and identity

`WithholdingTaxEvidenceV1` retains the Tax owner, determination/component/rule
references, opaque source version/fingerprint, sealed `rv1:` result fingerprint,
recognition/fact/transaction basis, exact taxable basis/component amount and
ordered lines with nullable rates. Billing does not calculate rates or infer
statutory categories. The composing assembly binds the Tax evidence to the
Billing scope, account and invoice. The public Tax DTO mapping canary preserves
opaque versions, fixed-component `rate=None`, and noncontiguous strictly ordered
public line sequences.

Claim identity and claimed amount, accepted discharge, optional certificate
observation, actual settlement/allocation links and finance authorization audit
identity remain distinct. The service requires
`0 < accepted <= claimed <= determined component`, exact supported money and
`accepted <= invoice outstanding`. Only the receivable lane decreases; no
settlement, payment, allocation, available credit or prepaid funding is created.

Both scope/source-owner/source-reference uniqueness and an acceptance-only
Tax-owner/determination/component unique index are enforced. Retry keys, changed
claim labels and changed source versions cannot make the same immutable Tax
component acceptable twice. One partial acceptance leaves residual AR; another
acceptance requires a new owner determination/component. Reversing an acceptance
is terminal: it appends the exact inverse preserving original Tax/claim/certificate
and cash provenance. A replacement needs new owner determination evidence.
Exact retries resolve before outstanding, void, cash-backing or correction-state
admission. Scope/account/invoice binding is checked before replay.

`withholding_discharges` and `platform_withholding_discharges` are additive in
`bi_0004_withholding_discharge`; scoped composite FKs bind their invoice,
posting group, optional cash edges and correction. Invoice exposure, invoice
outstanding and all existing cash/credit-note admission use the same discharge
sum. Void refuses active withholding; generic reversal refuses withholding
posting groups so corrections pass through the dedicated owner.

## Evidence and limits

Initial contract canaries preceded implementation. Added hosted canaries cover
both planes, net-cash/WHT separation, partial acceptance, exact/full-AR replay,
changed retry/source evidence, Tax component duplication, tenant isolation,
rollback, cash/WHT competing admission, appended correction and owner bypass
refusal. Dedicated migrated SQL tests cover the database boundary. Pinned Ruff
0.6.9 and AST/diff checks are static evidence only; the successor's hosted result
is still owed at its eventual exact head/base.

Authorization actor/evidence strings retain audit inputs; they do not prove
finance permissions. A trusted adopter must authorize the action and map genuine
Tax owner evidence and certificate observations. The source has no finance UI,
Tax policy UI, certificate verifier, statutory accounting recognition, GL posting,
Sub cutover, heterogeneous invoice grouping or production gate. The user's settled
direction is immutable money facts with appended corrections and composable,
versioned rate/basis UI owned by Tax; numeric/statutory category choices are not
reopened by this successor.


## Invoice-specific issuance protection

A positive account balance does not prove that a particular invoice is still
collectible. WHT admission now requires exactly one canonical `invoice_issued`
group, linked through the scoped invoice exposure foreign keys and qualified by
the owner-defined issuance source reference, account/currency/precision and
exact issued receivable effect. An issuance with any reversal is refused.
New generic invoice-issuance reversal requests must use the document void or
invoice correction owner. Exact historical retries resolve before these mutable
admission checks. The additive bi4 SQL guards serialize raw reversal and WHT
admission on the account and enforce both orders, including when a second
invoice leaves aggregate AR positive. These successor canaries await hosted CI;
no published migration, V1 payload or historical hash changed.

Generic reversal is also refused by the private posting reversal helper for both
WHT kinds. SQL permits a reversal of accepted WHT only through the dedicated
`withholding_reversed` kind and its actual, exact linked inverse discharge row;
corrections themselves cannot be reversed. Modern persistence fixtures issue a
genuine 100-total invoice through Billing before inserting raw WHT evidence,
while predecessor and negative-account fixtures retain explicit raw setup.
