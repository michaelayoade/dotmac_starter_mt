# Treasury / payment-execution source audit — is there an owner to extract?

- **As of:** 2026-08-24
- **Starter:** `b0d9ed849668` (branch `feat/complete-outbound-ecosystem`)
- **ERP:** `0f4b1698ddbf` — measured against the **working tree** of branch
  `feat/kernel-ui-contract-alignment`, which carries 67 uncommitted
  modifications and is 170 commits behind `origin/main`. None of the payment,
  transfer-batch, AP-payment or AR-payment files cited below is among the
  dirty files; `app/models/expense/expense_claim.py` and
  `app/services/expense/service_claims.py` ARE dirty, so the expense-side line
  numbers in § 5 are working-tree numbers.
- **Sub:** `3ec14bbb748f` (branch `data/subscriber-retirement-audit`, dirty)
- **CRM:** `c64b5aa0f790`
- **Integrator assembly:** `3ecad2b8beed`
- **Vendor CP / Academy / Workspace / Cloud / Backoffice:** swept, negative
  (§ 6)

This is the product-first source audit that **gates** whether a reusable
Treasury / payment-execution owner may be extracted. Under AGENTS.md rule 24
(ADR-0006 § "Decision amendment — 2026-08-08") the inventory runs BEFORE any
module behaviour is proposed, and under ADR-0017/ADR-0020 § 6 an audit
authorizes a boundary, never an implementation start.

Read under the two standing cautions in [`README.md`](README.md): **facts go
stale**, and **a row here is not permission to extract anything**. No package
directory, namespace, `mod_*` schema or release-allowlist entry is created by
this document, and none may be created on its authority alone — see the
RECOMMENDATION in § 12 for what is and is not authorized.

---

## 0. The one question this document answers

Eleven released or audit-complete module dossiers hand payment execution away
to an owner that does not exist. Two connector dossiers block their own outbound
capabilities on that same missing owner. This document asks whether the thing
they are all pointing at is a **coherent domain with a qualifying production
implementation**, or a name that several dossiers use for different things.

**Short answer.** The hole is real and precisely shaped, but it is much
narrower than "Treasury", and only part of it has a qualifying source.

- The fleet contains **exactly one live outbound money-movement executor**:
  ERP's `PaymentService.initiate_expense_transfer` → Paystack
  `POST /transfer`. It serves **one** source document type — expense claims —
  gated by a direct `app.models.expense.expense_claim` import, and welded at
  the schema level in the batch table by a hard foreign key.
- The general batch payout engine that would make it reusable
  (`BatchTransferService`) is **dead code with zero tests**.
- Every other payout in the fleet — ERP AP supplier payments, ERP payroll,
  CRM reseller commissions — runs on a **manual rail**: a spreadsheet a human
  uploads to the bank, or a "mark paid" button.
- Sub executes only INBOUND capture. Its outbound refund executor exists, is
  unit-tested, and has **zero production callers**.

So the question "is there one coherent payment-execution lifecycle" has a
two-part answer, and § 12 splits the recommendation along that seam rather
than averaging it.

---

## 1. Where payment execution actually lives today

"Execution" here means **a program causes money to move**, as distinct from
recording that it moved, deciding that it should, or posting its accounting
consequence.

| Source | Executes money movement? | Direction | Rail | Evidence |
|---|---|---|---|---|
| `dotmac_erp` | **Yes** | OUTBOUND | Paystack `POST /transfer` | `app/services/finance/payments/payment_service.py:1054` |
| `dotmac_erp` | **Yes** | INBOUND | Paystack `POST /transaction/initialize` + verify | `payment_service.py:80`, `:257` |
| `dotmac_erp` (AP) | No | OUTBOUND | Excel bank-upload file, uploaded by a human | `app/services/finance/ap/payment_batch.py:689`, `:800-807` |
| `dotmac_erp` (AR) | No | INBOUND | recording only | `app/services/finance/ar/customer_payment.py` |
| `dotmac_sub` | **Yes** | INBOUND | Paystack `POST /transaction/charge_authorization` | `app/services/integrations/connectors/payment_gateway.py:198-219` |
| `dotmac_sub` | Built, **never called** | OUTBOUND | Paystack/Flutterwave `POST /refund` | `app/services/payment_gateway_adapter.py:338-382` |
| `dotmac_crm` | No | OUTBOUND | manual `mark_payout_paid` | `app/services/reseller_commissions.py:208-232` |
| `packages/dotmac-payments` | No | — | — | contract excludes provider transport (§ 3) |
| `packages/dotmac-banking` | No | — | — | contract excludes provider connectivity (§ 3) |
| `packages/dotmac-accounting` | No | — | — | contract excludes payment execution (§ 3) |
| `packages/dotmac-approvals` | No | — | — | decides approval, never the transition |
| `dotmac-connector-paystack` | **Wire only** | both | `payments.payout.v1` DELIVERY | `src/dotmac_connector_paystack/delivery.py:58`, `:69-71` |
| `dotmac-connector-flutterwave` | **Deliberately withheld** | — | — | `withheld_capabilities` (§ 4) |
| `dotmac_integrator` | No | — | — | zero occurrences of `payout` or `initiate_transfer` in the assembly |

### 1.1 ERP — the outbound transfer lifecycle (`payment_service.py`)

`class PaymentService` (`app/services/finance/payments/payment_service.py:49`)
carries **both directions** on one table. The OUTBOUND half is the only live
programmatic payout in the fleet.

| Step | Function | Line | External call | Writes |
|---|---|---|---|---|
| Prepare payee | `create_expense_payment_intent` | `:754` | `resolve_account` `:916`, `create_transfer_recipient` `:922` | intent `PENDING`, `expires_at = now+24h` `:954`; **commits** `:973` |
| Submit | `initiate_expense_transfer` | `:976` | `initiate_transfer` `:1054` | `PENDING → PROCESSING` `:1119`, `→ FAILED` `:1110`, `→ EXPIRED` `:1018`; `transfer_code` `:1071`; **commits** `:1136` |
| Resolve ambiguity | `_recover_transfer_initiation` | `:1140` | `verify_transfer` `:1158` | nothing — returns evidence to the caller |
| Settle | `process_successful_transfer` | `:1210` | none | `→ COMPLETED` `:1308`, `fee_amount` `:1305`, `paid_at` `:1309`, `fee_journal_id` (via `_post_transfer_fee` `:1462`) |
| Fail | `mark_transfer_failed` | `:1554` | none | `→ FAILED` `:1572` (**no status guard**) |
| Poll | `poll_transfer_status` | `:1606` | `verify_transfer` `:1637` | dispatches to the three above |
| Reverse | `process_transfer_reversal` | `:1666` | none | `→ REVERSED` `:1703` |

**States** (`app/models/finance/payments/payment_intent.py:21-30`):
`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `REVERSED`, `ABANDONED`,
`EXPIRED`. `direction` (`:33-37`) is `INBOUND` | `OUTBOUND` on the same enum
column set.

**Ambiguity handling is real and is the strongest asset in the inventory.**
`_recover_transfer_initiation` recovers only on `"Request timed out"`
(`:1149`) or `"duplicate_transfer_reference"` / `"Reference already exists on
a transfer"` (`:1150-1152`), and resolves by verifying the **merchant
reference**, not the `TRF_` code (`:1634-1635`). That is the behaviour
`packages/dotmac-connector-paystack` cites BY NAME as its prior art:

> That is exactly what `dotmac_erp`'s `payment_service._recover_transfer_initiation`
> does after a timeout or a duplicate-reference refusal, and what `dotmac_sub`'s
> `autopay._recover_charge` does after any charge exception.
> — `packages/dotmac-connector-paystack/src/dotmac_connector_paystack/operations.py:38-40`

> The two Paystack phrasings are the ones ERP's `_recover_transfer_initiation`
> matches in production.
> — same file, `:446-447`

**Test coverage.** `dotmac_erp:tests/finance/test_expense_transfer_lifecycle.py`
— 1,463 lines, 49 tests, covering initiation, immediate success/failure,
timeout recovery, duplicate-reference recovery, idempotent completion,
poll dispatch, reversal, webhook idempotency, and
`test_webhook_and_poll_cannot_double_complete` (`:1289`). This is a genuine
parity suite.

### 1.2 ERP — who is the single writer? **Nobody is.**

`PaymentIntent.status` has **three live writers in three files**:

| Writer | Sites | Note |
|---|---|---|
| `app/services/finance/payments/payment_service.py` | 18 assignments (`:142`, `:222`, `:463`, `:560`, `:582`, `:604`, `:621`, `:714`, `:717`, `:801`, `:952`, `:1018`, `:1086`, `:1110`, `:1119`, `:1308`, `:1572`, `:1703`) | the intended owner |
| `app/tasks/expense.py` | `:827` `EXPIRED`, `:900` `PENDING → PROCESSING`, `:924` `FAILED` | Celery `poll_stuck_expense_transfers` writes status **directly**, bypassing `PaymentService`; **no tests** |
| `app/services/finance/payments/batch_transfer_service.py` | `:394` ctor `PENDING`, `:423` `PROCESSING` | dead code (§ 1.3) |

`app/services/finance/payments/webhook_service.py` is a well-behaved caller:
it writes only `PaymentWebhook.*` rows and delegates every intent mutation to
`PaymentService` (`:304`, `:329`, `:369`, `:394`, `:432`).

`SupplierPayment.status` has **three live writers**:
`app/services/finance/ap/supplier_payment.py` (`:432`, `:520`, `:611`, `:838`,
`:887`), `app/services/finance/ap/payment_batch.py` (`:604` cascade to
`APPROVED`, `:677` cascade to `REJECTED`), and
`app/services/remita/source_handler.py:139` (`SENT → CLEARED` from an inbound
RRR callback, with **no visible organization scoping** in
`_handle_ap_payment_paid` `:118-152`).

`CustomerPayment.status` has an out-of-service writer at
`app/services/dotmac_sub/sync/_payments.py:728` (`→ REVERSED`).

### 1.3 ERP — the batch engine is dead code

`class BatchTransferService`
(`app/services/finance/payments/batch_transfer_service.py:57`) implements the
full batch payout lifecycle — `create_batch` `:68`, `submit_for_approval`
`:190`, `approve_batch` `:214`, `process_batch` `:238`, `_process_batch_item`
`:335` — and contains a live `client.initiate_transfer` at `:409`.

It is **never referenced anywhere**: not in `app/`, `tests/`, `scripts/` or
`tools/`, and it is not exported from
`app/services/finance/payments/__init__.py`. It has **zero test coverage**.
`approve_batch` (`:214`) has no separation-of-duties check and no permission
check. `create_batch` reads the `paystack_transfer_bank_account_id` setting
without passing `organization_id=` to `resolve_value` (`:93`), unlike the
equivalent call at `payment_service.py:1413-1418`.

Its batch states (`app/models/finance/payments/transfer_batch.py:37-46`) are
`DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `PROCESSING`, `COMPLETED`,
`PARTIALLY_COMPLETED`, `FAILED`; item states (`:49-55`) are `PENDING`,
`PROCESSING`, `COMPLETED`, `FAILED`.

`TransferBatchItem.expense_claim_id` carries a hard FK to
`expense.expense_claim.claim_id` (`transfer_batch.py:263-267`). The batch
engine is **welded to one document type at the schema level**.

Batch status is written from two files by two different rules —
`BatchTransferService.process_batch` (`:288-314`) from a local counter, and
`PaymentService._update_batch_item_status` (`payment_service.py:1483`,
`:1534-1541`) via `batch.update_totals()`. Because the former is dead, the
latter is in practice the only live batch writer, and it can only ever fire
for items no live code creates.

### 1.4 ERP AP — a payout lifecycle with no execution in it

`APPaymentStatus` (`app/models/finance/ap/supplier_payment.py:37-59`):
`DRAFT → PENDING → APPROVED → SENT → CLEARED`, plus `VOID` and `REJECTED`.

`SupplierPaymentService.post_payment` (`app/services/finance/ap/supplier_payment.py:563`)
posts the GL journal (`:597`) and writes `→ SENT` (`:611`). **No external
call occurs anywhere in this path.** `SENT` is a human's promise that a bank
transfer was made; `mark_cleared` (`:857`) flips `SENT → CLEARED`. The model
has **no provider-reference column at all** — `remita/source_handler.py:135`
writes `payment.remita_payment_reference` behind a `hasattr` guard (`:134`)
that is always false, so that write is a silent no-op.

`APPaymentStatus.gl_impacting()` returns `{CLEARED}` (`:47`) while the journal
is actually written at `SENT` (`:611`) — the declared and actual GL boundary
disagree.

The real AP payout rail is `PaymentBatchService.generate_bank_file`
(`app/services/finance/ap/payment_batch.py:689`) → `BankUploadService`
(`app/services/finance/banking/bank_upload.py:58`), formats `zenith`,
`access`, `gtbank`, `generic` (`:801`). Payroll uses the same service
(`app/services/people/payroll/web/run_web.py:1102`).

### 1.5 Sub — inbound execution, and a dead outbound executor

Sub executes **inbound** capture from four production call sites:
`app/services/autopay.py:379` (scheduled, via Celery
`app/tasks/autopay.py:17-24`), `app/services/customer_portal_flow_payments.py:1072`
and `:1722`, `app/services/reseller_portal_billing.py:233`. The wire client is
`app/services/integrations/connectors/payment_gateway.py:198-219`
(`charge_authorization`, Paystack only).

`PaymentGatewayAdapter.refund` (`app/services/payment_gateway_adapter.py:338-382`)
is a real outbound-money executor with its own state vocabulary
(`PaymentGatewayRefundState` `:116-120`: `pending | succeeded | failed |
needs_attention`), unit-tested in
`tests/test_payment_gateway_refunds.py`. **It has zero production callers.**
Book-level `Refunds` (`app/services/billing/payments.py:5287`) contains no
reference to `payment_capability`, `httpx` or any gateway client — a refund
recorded in Sub debits the ledger with no corresponding money leaving the
PSP unless a human acts in the provider dashboard.

Sub has **no** outbound transfer/payout of any kind: no `POST /transfer`, no
`/transferrecipient`, no `/balance`, no payout batch, no settlement sweep.

### 1.6 CRM — a payout record, not a payout

`ResellerPayout` (`app/models/reseller_commission.py:46`) has three states
(`:39-43`): `draft`, `paid`, `void`. `mark_payout_paid`
(`app/services/reseller_commissions.py:208-232`) is a human action recording
an out-of-band transfer; `method` and `reference` are free-text
(`reseller_commission.py:55-56`). No provider, no ambiguity, no reversal.

---

## 2. What the fleet's own dossiers say is missing

ADR-0042 § 3 states the gap in so many words:

> Payables says what is owed, in what currency and by which date. It does not
> choose a bank account, payment rail, provider, batch or execution time and
> does not perform network I/O. **A Treasury/payment owner performs
> disbursement** and submits a typed observation containing source
> identity/version/fingerprint, amount and occurrence time.
> — `docs/adr/0042-payables-owns-supplier-liabilities-not-payment-execution.md:45-52`

and, in its Consequences:

> The existing ERP payment/batch implementation is not ported into this module;
> **it becomes a Treasury/payment candidate with a versioned settlement adapter.**
> — same file, `:74-75`

and, in Alternatives rejected:

> **Let Payables execute payments.** An obligation and a bank movement have
> different controls, security boundaries and failure modes. Combining them
> would put provider I/O inside a domain transaction and **recreate a second
> Integrator/Treasury engine.**
> — same file, `:93-96`

`docs/inventories/accounting-payables-sources.md:41` records the same
boundary from the inventory side:

> A treasury/payment owner executes disbursement and bank movement. Payables
> records a typed, deduplicated settlement observation; it stores no bank
> account, provider credential, payment batch or transport retry state.

**One inconsistency to record.** ADR-0047 (2026-08-18) § Context says
"Finance/Payables owns obligations, journals, **disbursement** and settlement"
(`docs/adr/0047-expenses-own-spend-evidence-not-payment.md:18`). ADR-0042
(2026-08-19) is the later and more specific ruling and takes disbursement away
from Payables. The residual prose in ADR-0047 should be read against ADR-0042,
not the other way round. **This document does not edit either ADR.**

---

## 3. What each existing module explicitly EXCLUDES — verbatim

The claim motivating this audit is that the existing modules exclude payment
execution. Verified. Each quote is the tail of the `contract` string in that
package's `EXTRACTION.toml`, transcribed exactly.

**`dotmac-accounting`** (`packages/dotmac-accounting/EXTRACTION.toml:7`):

> NOT receivables/payables documents, supplier/customer identity, procurement,
> assets, inventory, tax calculation/filing, **banking/payment execution**,
> budgets, exchange-rate sourcing, numbering, approvals, report presentation,
> or provider I/O.

**`dotmac-payments`** (`packages/dotmac-payments/EXTRACTION.toml:7`) — note it
excludes both transport and refunds, and owns only the *inbound* correlation:

> Own the payment intent lifecycle, submitted transfer proof and its review,
> and the append-only correlation between an intent and an external settlement
> fact. Payer, target receivable and document identifiers are opaque. NOT
> receivables, settlement allocation, invoice or credit application, bank
> accounts, general-ledger posting, **refunds, provider credentials or provider
> transport**.

**`dotmac-banking`** (`packages/dotmac-banking/EXTRACTION.toml:7`):

> NOT **provider connectivity, credentials, polling**, bank-specific formats,
> **payment meaning, collection-account routing**, GL accounts, journal
> entries, fiscal periods, or cash-ledger balances.

**`dotmac-payables`** (`packages/dotmac-payables/EXTRACTION.toml:7`):

> NOT supplier identity/compliance/bank details, procurement commitments/receipts,
> tax policy, inventory valuation, approval decisions, numbering, journal
> posting, **bank/payment execution, provider I/O or retry**.

**`dotmac-payroll`** (`packages/dotmac-payroll/EXTRACTION.toml:7`):

> NOT employee identity/lifecycle, compensation authority, time/attendance,
> leave, statutory tax policy, **bank/payment execution**, GL accounts, journal
> entries, fiscal periods, **provider exports, or remittance transport**.

**`dotmac-expenses`** (`packages/dotmac-expenses/EXTRACTION.toml:7`):

> NOT Party/employee identity, approval quorum, stored bytes, numbering,
> projects/work orders, advances/cards, AP/GL/tax, **bank details,
> disbursement, settlement or payment coverage**.

**`dotmac-finance`** (`packages/dotmac-finance/EXTRACTION.toml:7`):

> NOT physical asset identity/lifecycle/custody/maintenance/disposal approval,
> chart of accounts, fiscal periods, journals, tax/statutory books,
> foreign-exchange policy, **payments or cash**.

**`dotmac-billing`** (`packages/dotmac-billing/EXTRACTION.toml:7`):

> NOT offers/contracts/cadence/proration, collections policy/cases/consequences,
> **PSP clients/credentials/webhook verification/retry/checkpoints**,
> numbering-series state, document rendering/bytes, product access
> consequences, GL/journals/chart of accounts/fiscal periods/statutory
> accounting/**treasury**/tax returns.

**`dotmac-tax`** (`packages/dotmac-tax/EXTRACTION.toml:7`):

> NOT a country-specific rate/calendar/code enum, taxpayer identity validation
> transport, invoice/order/payroll ownership, government portal client, GL
> account, journal entry, fiscal period, **payment, or remittance transport**.

**`dotmac-procurement`** (`packages/dotmac-procurement/EXTRACTION.toml:7`):

> NOT Party/requester/supplier identity, annual budgets, approval policy,
> statutory thresholds, supplier prequalification, contract administration,
> Inventory, Assets, **AP invoices/three-way match/payments/journals**, product
> work, numbering, files/rendering, notifications or provider transport.

**`dotmac-reseller-management`** (`packages/dotmac-reseller-management/EXTRACTION.toml`)
excludes "commissions, **payouts**, invoices or catalog decisions" and adds:
"CRM commission/**payout** state remains outside this module pending its own
commercial ownership decision."

**Finding.** Eleven dossiers exclude payment execution. **None claims it.**
The exclusion is unanimous and consistent — it is a genuine hole, not a
disagreement about who fills it.

---

## 4. The connectors already name the missing owner as a blocking gate

`packages/dotmac-connector-paystack/EXTRACTION.toml`, `next_action`:

> The four outbound capabilities remain blocked on two named gates: (1)
> **product-owned command contracts for payment intent, refund, payout and
> customer synchronization — the connector performs commands, and nobody may
> issue one until a product owner is named for the decision behind it**; and
> (2) a durable command-response seam in dotmac-integration, because DELIVERY
> returns an Outcome and cannot preserve a reply, so a checkout URL, a resolved
> account name and a new recipient code have nowhere to land. Until (2) exists,
> only commands whose reply is pure evidence (charge, refund, transfer) are
> usable through DELIVERY at all.

`packages/dotmac-connector-flutterwave/EXTRACTION.toml:64-68`:

```toml
# HELD BACK, deliberately: v4 transfers/payouts. No product consumer exists.
# `tests/unit/test_flutterwave_outbound.py::test_no_transfer_or_payout_command
# _ships_in_this_release` makes adding one a visible decision rather than a
# quiet capability.
withheld_capabilities = ["payments.transfer.v1", "payments.payout.batch.v1"]
```

The Paystack connector's payout capability is fully built:
`PAYMENT_PAYOUT_CAPABILITY = "payments.payout.v1"`
(`src/dotmac_connector_paystack/delivery.py:58`) mapping to
`{"resolve_bank_account", "create_transfer_recipient", "initiate_transfer"}`
(`:69-71`), with outcome classification in
`operations.py::_transfer_result` (`:861-892`).

`dotmac_integrator` at `3ecad2b8beed` contains **zero occurrences** of
`payout` or `initiate_transfer` — the capability is published and unconsumed.

**Finding.** The wire leg of payment execution is already extracted, released
and unconsumed, and its own dossier states that the decision owner behind it
does not exist. That is the most direct evidence in this audit that the gap is
real and is blocking work already done.

---

## 5. Authorization — what actually gates a payout today

**There is no Approvals integration in any ERP payout path.** A grep for
`approval_request_id|require_approval|ApprovalService|ApprovalWorkflowService|
authorized_by|approval_id` across `app/services/finance/ap/supplier_payment.py`,
`app/services/finance/ap/payment_batch.py`, `app/services/finance/payments/`,
`app/api/finance/payments.py` and
`app/api/finance/ap_routes/payment_batches.py` returns **zero hits**.
`SupplierPayment.approval_request_id`
(`app/models/finance/ap/supplier_payment.py:194`) is declared and never
written by any code in the repository.

What gates a live Paystack payout is a four-layer stack, none of which is an
approval decision record:

1. **RBAC dependency** `require_expense_reimburse_access`
   (`app/api/finance/payments.py:68`, applied at `:452`, `:482`, `:524`,
   `:592` and on the execution route at `:631`). It grants on **any one of**
   role `admin` (`:73`), scope `finance:access` (`:78`), or any of
   `payments:read`, `payments:expense:initialize`, `payments:transfer:initiate`,
   `expense:claims:reimburse`, `expense:claims:approve:tier1..3` (`:82-91`).
   **`payments:read` — a read permission — authorizes executing a transfer.**
2. **Feature flag** `paystack_transfers_enabled`, checked at
   `app/api/finance/payments.py:539` and `:637`, and in the service at
   `payment_service.py:815-822`.
3. **Document state** — `ExpenseClaim.status == APPROVED`, checked at
   `payment_service.py:838` and re-checked under `SELECT … FOR UPDATE` at
   `payment_service.py:1039`. The real multi-tier authority lives upstream in
   `app/services/expense/service_claims.py` — `_validate_approver_authority`
   (`:717`), self-approval block (`:735`), chain completion via
   `ExpenseApprovalService.process_approval_decision` (`:739-745`). *(These
   line numbers are from the dirty working tree.)*
4. **Intent state** — must be `PENDING`, checked at
   `app/api/finance/payments.py:659` and `payment_service.py:1004`.

Separation of duties is inconsistent: `PaymentBatchService.approve_batch` has
a hard creator-cannot-approve block
(`app/services/finance/ap/payment_batch.py:571-575`);
`SupplierPaymentService.approve_payment` has one only behind feature flag
`FEATURE_REQUIRE_SOD` (`app/services/finance/ap/supplier_payment.py:508-517`),
so it is off by default; `BatchTransferService.approve_batch`
(`batch_transfer_service.py:214`) has none at all.

**Finding.** A Treasury owner would not be *taking* authorization from
anything — it would be the first payout path in the fleet to have one. That is
a capability gain, not an authority migration, and under ADR-0026 the decision
stays with `dotmac-approvals` while Treasury validates a supplied reference
and performs the transition itself.

---

## 6. Fleet census — negative results

Swept for `initiate_transfer|payout|disbursement|batch_transfer|transfer_status`
outside tests, at the revisions in the header:

| Repository | Result |
|---|---|
| `dotmac_vendor_control_plane` `e6b2bbe` | none |
| `dotmac_academy_app` `40423a0` | none |
| `dotmac_workspace` `c72fe30` | none |
| `dotmac_cloud` `53768c9` | none |
| `dotmac_backoffice` `fcdd827` | none |
| `dotmac_crm` `c64b5aa0` | `ResellerPayout` only — manual record (§ 1.6) |
| `dotmac_sub` `3ec14bbb` | inbound execution + dead refund executor; **no outbound transfer** (§ 1.5) |
| `dotmac_integrator` `3ecad2b8` | none |

Sub-side adjacent hits that are **not** execution, checked individually:
`app/services/vendor_supply_views.py:444-456` ("operator **records** the
disbursement"), `app/services/vendor_advances.py:217` ("what this project has
**authorised** for disbursement"), `app/services/vendor_payment_status.py`
(read projection of ERP-observed payables),
`app/web/customer/routes.py:2480-2557` (inbound transfer-proof upload),
`payment_gateway_adapter.py:469-476` (`disburse_status` is a Flutterwave
*refund* sub-status).

---

## 7. Is there one coherent lifecycle, or several?

### 7.1 The state machines that exist

| # | Machine | States | Executes? | Tests |
|---|---|---|---|---|
| 1 | ERP `PaymentIntent.status` (OUTBOUND) | `PENDING → PROCESSING → COMPLETED \| FAILED \| REVERSED`, + `EXPIRED`, `ABANDONED` | **yes** | 49 |
| 2 | ERP `PaymentIntent.status` (INBOUND) | same enum, different meanings | yes | shared |
| 3 | ERP `TransferBatch` / `TransferBatchItem` | 7 / 4 states | dead | **0** |
| 4 | ERP `APPaymentStatus` | `DRAFT → PENDING → APPROVED → SENT → CLEARED`, `VOID`, `REJECTED` | no | 22 |
| 5 | ERP `APBatchStatus` | `DRAFT → APPROVED → PROCESSING → COMPLETED \| FAILED` — **no `PENDING_APPROVAL`**, unlike #3 | no | 42 |
| 6 | ERP AR `PaymentStatus` | `PENDING → APPROVED → CLEARED \| BOUNCED \| REVERSED \| VOID` | no | yes |
| 7 | Sub `Payment.status` | `pending \| succeeded \| failed \| refunded \| partially_refunded \| reversed \| canceled`, with an enforced transition table (`app/services/billing/payments.py:466-493`) | inbound | many |
| 8 | Sub `TopupIntent.status` | `pending \| submitted \| completed \| expired \| canceled \| failed \| abandoned` — **every transition permitted** (`app/services/topup_intents.py:594-630`) | inbound | yes |
| 9 | Sub `PaymentGatewayRefundState` | `pending \| succeeded \| failed \| needs_attention` | dead | yes |
| 10 | CRM `PayoutStatus` | `draft \| paid \| void` | no | — |
| 11 | Integrator `DeliveryAttempt.state` | `pending \| retryable \| delivered \| reconciliation_required \| dead_letter` (`packages/dotmac-integration/src/dotmac_integration/models.py:504`) | transport | yes |

Eleven machines. **Not one coherent lifecycle as implemented.**

### 7.2 But there IS one coherent lifecycle as designed

Three independent implementations, in three codebases, converged on the same
five-step shape — and one of them wrote it down as a total, import-time-checked
mapping:

| Step | ERP | Sub | Integrator + connectors |
|---|---|---|---|
| authorize | `ExpenseClaim.APPROVED` + RBAC + flag | `AutopayMandate.is_active` + `failure_count` circuit breaker (`app/models/autopay.py:33`, `:38`) | out of scope by contract |
| submit with a provider-enforced at-most-once key | `paystack_reference` UNIQUE (`payment_intent.py:64-68`) | `AUTOPAY-{invoice}-{amount}[-A{n}]` (`autopay.py:199-212`) | `DispatchRequest.idempotency_key` → provider reference/header |
| **ambiguous** | `_recover_transfer_initiation` (`:1140`) | `autopay._recover_charge` | `OutcomeStatus.RECONCILIATION_REQUIRED` (`retry.py:97`) |
| resolve by provider probe | `verify_transfer(reference)` (`:1158`) | `verify` | `prepare_reconciliation` / `reconcile_with_evidence` (`outbound_repair.py:950`, `:1009`) |
| settle / fail / reverse | `process_successful_transfer` / `mark_transfer_failed` / `process_transfer_reversal` | `mark_status` transition table | connector `_transfer_result` classification (`operations.py:861`) |

The connector module states the invariant explicitly:

> `AMBIGUOUS` is the one people leave out, and leaving it out is the
> duplicate-charge bug. A read timeout on a charge or a transfer means the
> bytes were sent and the answer was lost — the money may already have moved.
> — `packages/dotmac-connector-paystack/src/dotmac_connector_paystack/operations.py:31-35`

**Finding.** The lifecycle is coherent. What is incoherent is the *persistence*
of it: eleven machines, three money scales, no single writer of any of them,
and six of the eleven never execute anything — two of those six being dead
code.

### 7.3 What is left unowned after subtracting existing owners

| Concern | Already owned by |
|---|---|
| wire format, provider status parsing, exact-money conversion, outcome classification | `dotmac-connector-*` |
| dispatch, idempotency key, retry curve, dead-letter, `reconciliation_required`, `reconcile_with_evidence` | `dotmac-integration` |
| approval quorum, SoD, MFA, digest binding | `dotmac-approvals` |
| what is owed, to whom, by when | `dotmac-payables` / `dotmac-payroll` / `dotmac-expenses` |
| bank-account masters, statement observations, matching, reconciliation snapshots | `dotmac-banking` |
| journals, periods, ledger evidence | `dotmac-accounting` |
| inbound intent + settlement-fact correlation | `dotmac-payments` |
| effective FX observation and selection | `dotmac-fx-policy` |
| exact `Money`/`Currency` value objects | `dotmac_kernel.money` |

**Residual, unowned:** the **payment instruction** — the authorized, funded,
routed order to move a specific amount out, on a named rail, to a named
destination; its own business state machine; its batch grouping; the rule that
turns one transport `Outcome` into one business verdict; and the typed
settlement observation it emits back to the obligation owner.

That residue is narrow, and it is a decision surface, not a mechanism. It is
also the exact thing ADR-0042 § 3 describes: "does not choose a bank account,
payment rail, provider, batch or execution time."

---

## 8. Consumer analysis

The fleet standard is explicit that consumer count decides reuse evidence, not
placement:

> ADR-0006's 2026-08-12 amendment separates the two: a second consumer proves
> reuse, it does not decide placement.
> — `tests/architecture/test_product_first_extraction.py:8-12`

So this section is recorded as a fact, not as a veto.

**Module-level consumers that have already declared the dependency by
excluding it** — three, each of which would have to obtain disbursement from
somewhere: `dotmac-payables` (supplier payments), `dotmac-payroll` (net-pay
and statutory remittance), `dotmac-expenses` (reimbursement). A fourth,
`dotmac-reseller-management`, parks commission payouts explicitly.

**Product-level consumers:** `dotmac_erp` (live outbound, plus AP and payroll
on the manual rail), `dotmac_sub` (would gain a real refund executor,
retiring the ledger/cash divergence in § 1.5), `dotmac_crm` (reseller payouts,
manual today).

**A design consequence follows from that mix.** Only one of these consumers
has a PSP rail; the rest run on a bank-upload file or a human. A Treasury
owner that models only the provider rail would serve exactly one caller. The
**manual/out-of-band rail must be a first-class rail** in the contract —
instruction, evidence reference, and settlement observation — with no provider
leg, or the module collapses back into "the ERP expense-transfer feature with
a new package name."

---

## 9. Boundaries a Treasury owner must not cross

### 9.1 Against `dotmac-accounting` (GL consequence)

Treasury must **never write a journal**. ERP today does the opposite:
`PaymentIntent.fee_journal_id` (`payment_intent.py:195`) is a GL journal id on
the payment row, written by `_post_transfer_fee` (`payment_service.py:1396`,
`:1462`), and `process_successful_transfer` calls
`ExpensePostingAdapter.post_expense_reimbursement` directly
(`payment_service.py:1338-1347`). Both failures are **logged and swallowed**
(`:1364-1369`), so a completed transfer can carry no accounting consequence
and nothing detects it.

The corrected shape is the one ADR-0042 § 4 already accepted for Payables: a
typed balanced consequence with opaque account/dimension references, an
assembly seam that translates it, and an immutable, repairable receipt.

### 9.2 Against `dotmac-banking` (statements and reconciliation)

Treasury must **never match a statement line or write a reconciliation**.
Its terminal fact is *"the provider says this instruction settled"*. Banking's
terminal fact is *"this cash movement appeared on the statement and was
matched"*. **These are two different facts and ERP collapses them into one**:
`APPaymentStatus.CLEARED` is set both by `mark_cleared`
(`supplier_payment.py:857`) and by an inbound Remita callback
(`remita/source_handler.py:139`), and `SupplierPayment.bank_reconciliation_id`
(`:141`) puts a banking foreign key on a payment row.

`dotmac-banking`'s contract already forbids the reverse crossing — "NOT
provider connectivity, credentials, polling … payment meaning,
collection-account routing" — so the seam is a typed observation flowing
Treasury → Banking, not a shared column.

### 9.3 Against the Integrator and its connectors (wire execution)

Treasury must open **no HTTP client, hold no provider credential, parse no
provider status string, and run no retry curve**. It issues a command through
`dotmac_integration.dispatch` and consumes an `Outcome`.

The sharpest risk here is **becoming a second reconciler**.
`dotmac-integration` already owns ambiguity resolution end to end:

> This module deliberately REFUSES to replay a `reconciliation_required`
> delivery … It is resolved by `prepare_reconciliation` /
> `reconcile_with_evidence` against evidence the PROVIDER supplies, and only a
> provider-proven `NOT_LANDED` returns it to the queue.
> — `packages/dotmac-integration/src/dotmac_integration/outbound_repair.py:58-65`

So the division must be stated as: **the engine resolves whether the COMMAND
landed; Treasury decides what the INSTRUCTION now is.** Treasury never probes
the provider itself and never re-implements `verify_transfer` — which is
precisely what ERP does today at `payment_service.py:1158` and `:1637`.

The second risk is **provider I/O inside a domain transaction**. ERP holds a
`SELECT … FOR UPDATE` row lock on `ExpenseClaim`
(`payment_service.py:1029-1033`) across the Paystack round-trip at `:1054`.
`dotmac_integration.dispatch` and `outbound_repair` are both three-phase for
exactly this reason ("a transaction open across a provider round-trip holds
row locks for the duration of someone else's" — `outbound_repair.py:68-70`).

### 9.4 Against `dotmac-payments`

`dotmac-payments` is **inbound only** — extracted from Sub's `topup_intents`,
with statuses `PENDING | CONFIRMED | EXPIRED | CANCELLED`
(`packages/dotmac-payments/src/dotmac_payments/contracts.py:27-32`) and no
`SUBMITTED`, `AMBIGUOUS`, `FAILED` or `REVERSED`. Its contract excludes
refunds and provider transport outright.

Widening it to carry outbound instructions would make one module the owner of
two directions with two different failure models — which is exactly the shape
of ERP's bidirectional `PaymentIntent`, the thing this audit is proposing to
take apart. **Note the name collision explicitly:** ERP's
`payments.payment_intent` and `mod_payments.payment_intents` share a name and
are not the same concept.

---

## 10. Money handling — inconsistent, and the inconsistency is inside one flow

| Source | Representation |
|---|---|
| `dotmac_kernel.money` | `Currency` (ISO-4217 + minor units) and exact `Money` over `Decimal`; `Amountable = int \| str \| Decimal` — **`float` is excluded by type** (`packages/dotmac-kernel/src/dotmac_kernel/money.py`) |
| `dotmac-payments` | `MONEY = Numeric(20, 6)`, every amount carries its own ISO-4217 code, `CheckConstraint("length(currency_code) = 3")` (`models.py:43`, `:63-68`) |
| `dotmac-banking` | typed `Money` on every command input (`contracts.py:11`, `:43`) |
| ERP `PaymentIntent` | `Numeric(19, 4)`; `currency_code` **defaults to `settings.default_functional_currency_code`** (`payment_intent.py:81-90`) |
| ERP `TransferBatch` / `Item` | `Numeric(19, 4)` (`transfer_batch.py:118`, `:123`, `:284`, `:334`) |
| ERP `SupplierPayment` / `CustomerPayment` / `APPaymentBatch` | `Numeric(20, 6)`; `exchange_rate` `Numeric(20, 10)` |
| ERP `ExpenseClaim` | `Numeric(12, 2)` (`app/models/expense/expense_claim.py:297-299`) |
| Sub | `Numeric(12, 2)` throughout (`app/models/billing.py:1323`, `:1458`, `:1546`, `:1992`) |
| CRM | `Numeric(12, 2)` (`app/models/reseller_commission.py:52`, `:85-87`) |

**Three scales inside a single ERP money flow**: a claim carries 2 dp, the
intent that pays it stores 4 dp, and the AP payment for the same supplier
stores 6 dp.

**Float leakage.** ERP serialises transfer amounts as `float` at the HTTP
boundary — `app/api/finance/payments.py:571`, `:684`, `:700`. Sub writes
Flutterwave outbound amounts as `float` on the wire
(`app/services/integrations/connectors/payment_gateway.py:331`, `:371`); the
Flutterwave connector's `port_deltas` already records this as a defect it
refuses to inherit.

**Minor-unit conversion is in the wrong layer.** ERP converts to kobo inside
the domain service — `payment_service.py:1046-1050`,
`batch_transfer_service.py:404-408`, `webhook_service.py:218-222` — and once
computes the kobo value and discards it (`payment_service.py:894-898`, a bare
expression statement). The released connector already owns this conversion as
provider protocol.

**Tolerances disagree.** ERP's webhook amount check allows 1 kobo INBOUND and
**5 kobo OUTBOUND** (`webhook_service.py:231-235`), and
`test_expense_transfer_lifecycle.py:1146` pins a 1-kobo tolerance. The Paystack
connector deliberately tightened the equivalent gate:

> A success whose money does not add up is not a success. Ported from Sub's
> evidence gate, **tightened from "within a kobo" to "exactly"**.
> — `operations.py:855-857`

**Answer to the question:** money is `Decimal` in every persistence layer in
the fleet (no `float` columns anywhere), so the *type* is consistent. The
*scale*, the *currency default*, the *conversion layer*, the *tolerance* and
the *serialisation boundary* are not.

---

## 11. Defects that must not be carried forward on port

Recorded so a future extraction answers them rather than inheriting them.
Numbering is this document's own.

**Ownership**

1. Three live writers of `PaymentIntent.status` in three files (§ 1.2).
2. Three live writers of `SupplierPayment.status`, one of them an inbound
   Remita callback with no visible org scoping
   (`app/services/remita/source_handler.py:118-152`).
3. `app/services/dotmac_sub/sync/_payments.py:728` writes AR
   `CustomerPayment.status = REVERSED` from outside the AR service.
4. Batch status derived by two different rules in two files
   (`batch_transfer_service.py:288-314` vs `payment_service.py:1534-1541`).

**Transaction and transport**

5. Provider HTTP call inside a domain transaction while a `SELECT … FOR UPDATE`
   lock is held (`payment_service.py:1029-1054`).
6. `_commit_and_refresh` commits inside the service
   (`payment_service.py:60`, called at `:1136`, `:973`, `:332`) — the kernel's
   one-transaction-authority rule (AGENTS.md rule 8) forbids this shape.
7. Reversal is counted as a batch item `FAILED`
   (`payment_service.py:1726-1730`) — a reversal is a movement, not a failure.

**Guards that do not bite**

8. `mark_transfer_failed` has **no status guard** (`payment_service.py:1572`)
   and will overwrite a `COMPLETED` intent.
9. `process_transfer_reversal` logs a warning and **silently returns** on an
   inadmissible status (`:1692-1699`) instead of refusing.
10. GL posting failures are logged, not raised, with no repair path
    (`:1364-1369`, `:1734` and `_post_reversal_entries` `:1745`).
11. `ExpenseService.mark_paid` failure is swallowed while the intent still
    completes (`:1286-1292`).
12. `set_topup_intent_status` in Sub permits **every** transition
    (`app/services/topup_intents.py:594-630`).

**Authorization**

13. `payments:read` — a read permission — authorizes executing a transfer
    (`app/api/finance/payments.py:82-91`).
14. `SupplierPayment.approval_request_id` declared and never written
    (`app/models/finance/ap/supplier_payment.py:194`).
15. SoD present on one approval path, feature-flagged on a second, absent on a
    third (§ 5).

**Schema and vocabulary**

16. `TransferBatchItem.expense_claim_id` hard FK to
    `expense.expense_claim.claim_id` (`transfer_batch.py:263-267`) — one
    document type welded into the payout engine.
17. `PaymentIntent.source_type` is a free-form `String(30)`
    (`payment_intent.py:118-122`), not a declared vocabulary (AGENTS.md rule 12).
18. `TransferBatchItem.transfer_reference` is **not unique**
    (`transfer_batch.py:300`).
19. `APPaymentStatus.gl_impacting()` returns `{CLEARED}` while the journal is
    written at `SENT` (§ 1.4).
20. `APBatchStatus` has no `PENDING_APPROVAL`; `TransferBatchStatus` does.
21. `currency_code` defaults from a settings value
    (`payment_intent.py:86-90`) — the identical defect the Flutterwave
    connector's `port_deltas` already refuses.

**Money** — items in § 10: three scales in one flow, float at the API and
Flutterwave wire boundaries, kobo conversion in the domain layer, 1-vs-5 kobo
tolerance split.

**Coverage**

22. Zero tests for `BatchTransferService`,
    `app/tasks/expense.py::poll_stuck_expense_transfers`,
    `app/services/remita/source_handler.py::_handle_ap_payment_paid`,
    `app/tools/fix_stuck_paid_expense_claims.py` (which hard-codes an `ORG_ID`
    module constant at `:44-46`), `app/tools/reset_expense_payment_intent.py`,
    and `app/services/finance/payments/webhook_service.py`.
23. Sub's outbound refund executor is unit-tested and production-dead, so a
    ledger refund has no matching cash movement (§ 1.5).

---

## 12. RECOMMENDATION

The verdict splits, because the evidence splits. Averaging it would produce a
single answer that is wrong about both halves.

### 12.1 The single payment instruction — **EXTRACT, gated**

**Verdict: extract a narrow Treasury owner of the single payment
instruction.** Scope: an authorized, routed instruction to move one amount out
on one named rail to one named destination; its state machine
(`AUTHORIZED → INSTRUCTED → SUBMITTED → AMBIGUOUS → SETTLED | FAILED |
REVERSED`); the rule that turns one transport `Outcome` into one business
verdict; and the typed, deduplicated settlement observation it emits to the
obligation owner.

Because:

1. **The hole is unanimous and named.** Eleven module dossiers exclude payment
   execution and none claims it (§ 3); ADR-0042 § 3 names the missing owner in
   terms; two connector dossiers block released capabilities on it (§ 4).
2. **The lifecycle is coherent**, and three independent implementations
   converged on the same five steps including the ambiguous state that is the
   whole difficulty (§ 7.2).
3. **A qualifying source exists for this slice**: ERP's
   `initiate_expense_transfer` / `_recover_transfer_initiation` /
   `process_successful_transfer` / `mark_transfer_failed` /
   `poll_transfer_status` / `process_transfer_reversal`, with 49 tests in
   `tests/finance/test_expense_transfer_lifecycle.py`. The released Paystack
   connector already cites this code as its prior art (§ 1.1), which is
   independent corroboration that it is the fleet's reference behaviour.
4. **The residue after subtracting existing owners is a decision surface, not
   a mechanism** (§ 7.3) — which is the shape the fleet's module standard
   asks for.
5. **More than one consumer** (§ 8): three module dossiers and three products.

### 12.2 Batching, rail routing and payee binding — **INSUFFICIENT EVIDENCE**

**Verdict: do not extract these slices now. There is no qualifying source.**

- **Batching.** The only general batch payout engine, `BatchTransferService`,
  is dead code with zero tests and no SoD or permission check (§ 1.3). ERP's
  AP batch (`payment_batch.py`) is well tested but executes nothing. Under
  AGENTS.md rule 24 neither qualifies as "a production-used, tested
  implementation", and a greenfield claim would need checked-in evidence that
  none exists — which is what § 1.3 and § 6 provide, but that makes the slice
  `greenfield-after-inventory` at best, not `product-first`.
- **Rail routing** (which account, which provider, which rail). No
  implementation anywhere in the fleet. ERP reads one setting,
  `paystack_transfer_bank_account_id`.
- **Payee / bank-destination binding.** `resolve_bank_account` and
  `create_transfer_recipient` exist in the released connector
  (`delivery.py:69-71`) but **cannot be used through DELIVERY at all**,
  because `dotmac-integration` has no durable command-response seam and the
  returned recipient code has nowhere to land (§ 4, quoted verbatim). A
  Treasury owner therefore cannot complete its own lifecycle through the
  sanctioned transport today.

### 12.3 Blocking gates before any package, namespace or `mod_*` schema

None of these is satisfied at the revisions in the header:

- **G1 — durable command-response seam** in `dotmac-integration`. Named by the
  Paystack dossier; recorded as an `open_questions` entry by this change.
- **G2 — Accounting authority moved.** ADR-0041/ADR-0042 order Accounting
  before Payables; Treasury consumes the same consequence seam and is
  downstream of both.
- **G3 — Banking authority moved**, so that "provider says settled" and
  "cleared the bank" are two owned facts rather than one `CLEARED` column
  (§ 9.2).
- **G4 — an obligation owner in place** (`dotmac-payables` or
  `dotmac-expenses` cut over), so the FK at `transfer_batch.py:263-267`
  becomes an opaque reference rather than a schema dependency.
- **G5 — an ADR.** A new owner in this space needs its own decision recording
  the boundary against Accounting, Banking, Payments, Approvals and the
  Integrator. This document is evidence for that ADR; it is not the ADR, and
  it does not amend ADR-0042, ADR-0047 or any decision under
  concurrent revision.

### 12.4 The specific evidence that would change these answers

| If this were established | Then |
|---|---|
| An authoritative oracle (`deployment_run` / `adoption_evidence`, AGENTS.md rule 30) shows `paystack_transfers_enabled` true in a live ERP tenant with executed transfers | § 12.1 gains its production-use leg; today the claim is "implemented and tested", not "production-used" |
| The same oracle shows it is **false everywhere** | § 12.1 weakens to `greenfield-after-inventory`: the reference behaviour would be untested-in-anger, and the 49 tests would be the whole evidence base |
| `dotmac-integration` ships a durable command-response seam | G1 clears; the payee-binding slice in § 12.2 becomes auditable |
| A second product commits to consuming the instruction contract on the **manual rail** | § 8's design consequence is validated; without it the module risks serving one caller |
| Rows are found in `payments.transfer_batch` in any live database | the batch slice gains a source; today it is dead in code but its tables may hold history from an earlier revision |
| ERP's `payments` schema is shown to carry RLS | the tenancy port cost is bounded; `docs/inventories/tenancy-characterization.md` records ERP RLS coverage as unmeasurable from source |

### 12.5 What is authorized by this document

**Nothing but the audit itself.** No package directory, no namespace, no
`mod_*` schema, no `MIGRATION_OWNER_LEDGER` allocation, no
`.github/release-modules.json` entry, no `EXTRACTION.toml` at a package root.
Following the precedent of
[`collections-extraction-dossier.md`](collections-extraction-dossier.md) § "Why
this is a markdown file", creating a real `EXTRACTION.toml` would create the
package, which is the thing the gate forbids.

---

## 13. If the extraction is later approved — the migration shape

Recorded now because ADR-0006's extraction rule requires a named owner and a
migration path, and because writing it down is how § 12.4's evidence gets
tested against something.

- **Old owner:** ERP `PaymentService` (OUTBOUND half),
  `app/tasks/expense.py::poll_stuck_expense_transfers`, and
  `BatchTransferService` (deleted, not migrated — dead code with no rows to
  preserve is retired, not ported).
- **New owner:** the Treasury instruction service, importing only
  `dotmac-kernel`, composing `dotmac-integration` through the assembly.
- **Shadow phase:** read-only replay of historical outbound intents on an
  independently migrated database. Compare per instruction: terminal state,
  provider reference, settled amount and currency, fee, settlement time, and —
  the one that matters — the **verdict each ambiguous case resolved to**.
  Prove retry no-op, changed-fingerprint conflict (ADR-0014), and synthetic
  second-tenant isolation. A rehearsal report never authorizes a switch
  (ADR-0031).
- **Cutover gate:** the ERP writer ratchet reaches zero for all three
  `PaymentIntent.status` writers on the OUTBOUND direction — including the
  Celery task — in one sealed change. `paystack_transfers_enabled` becomes the
  routing switch, not a second code path.
- **Fallback retirement:** delete `BatchTransferService`; remove the Celery
  task's direct status assignments; replace `fee_journal_id` and the direct
  `ExpensePostingAdapter` calls with a typed consequence plus an immutable
  accounting receipt; leave `SupplierPayment` and `CustomerPayment` where they
  are — they are Payables and Billing concerns, not Treasury's.
- **Tests proving the boundary:** port all 49 cases from
  `dotmac_erp:tests/finance/test_expense_transfer_lifecycle.py` (with the
  1-kobo tolerance at `:1146` tightened to exact, per § 10); add architecture
  tests asserting the module imports no `httpx` or provider client, writes no
  journal/ledger table, holds no bank-statement or match row, and performs no
  provider probe of its own; add a cross-tenant isolation canary.

---

## 14. Questions this audit could not answer from the code

1. **Is ERP's expense-transfer path live in production?**
   `paystack_transfers_enabled` is a runtime setting resolved per organization
   (`payment_service.py:815-822`); its value is not in the repository. Under
   AGENTS.md rule 30 a production-adoption claim needs an authoritative
   external oracle carrying immutable coordinates, and none was consulted —
   **tests were not run and no host was contacted**, per this task's
   constraints. Everything in § 1.1 is therefore "implemented and tested",
   never "production-proven".
2. **Has any tenant ever executed a batch transfer?** `BatchTransferService`
   is unreachable at `0f4b1698`, but `payments.transfer_batch` rows could
   exist from an earlier revision. Needs a live-catalogue read.
3. **Does ERP's `payments` schema carry RLS?**
   `docs/inventories/tenancy-characterization.md` records ERP's RLS coverage
   as unmeasurable from source.
4. **Is CRM's reseller payout used at all?** No usage evidence is derivable
   from code.
5. **Are Sub's book-only refunds manually matched to provider-dashboard
   refunds?** § 1.5 establishes the divergence *surface*; whether it has
   produced actual divergence is an operational question.
6. **Is the `dotmac-integration` command-response seam planned or scheduled?**
   The Paystack dossier names it as a gate but no plan or spec in this
   repository schedules it.
7. **What is on ERP `main`?** ERP was measured on a branch 170 commits behind
   `origin/main` with 67 uncommitted files. The payment files cited are clean,
   but `main` may differ; re-measure before acting on § 13.
8. **Which rail do live AP and payroll payouts actually use?** The bank-file
   formats `zenith | access | gtbank | generic`
   (`app/services/finance/ap/payment_batch.py:801`) are implemented; which are
   configured is deployment state.

---

## 15. Index entry owed

Not added here, because `docs/inventories/README.md` is being edited
concurrently. The row to add to its Index table:

```markdown
| **Treasury / payment-execution sources** | the five-source audit gating a reusable payment-execution owner: ERP's single live Paystack payout path and its dead batch engine, ERP AP/payroll's bank-file rail, Sub's inbound-only execution and production-dead refund executor, CRM's manual payout record, the eleven module contracts that unanimously exclude payment execution, the connector capabilities blocked on a missing decision owner, and the split recommendation — extract the single instruction, insufficient evidence for batching/routing/payee binding | `treasury-payment-execution-sources.md` |
```
