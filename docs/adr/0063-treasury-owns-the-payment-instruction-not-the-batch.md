# ADR-0063: Treasury owns the payment instruction, not the provider batch

> **Number allocation, 2026-08-24.** `0059` and `0060` remain allocated on
> sibling branches that have not merged; `0061` and `0062` are allocated on this
> branch. This record takes `0063` under the same rule ADR-0061 recorded: the
> earlier record keeps the number.

- Status: Accepted
- Date: 2026-08-24
- Deciders: Michael
- Supersedes: none
- Amends: ADR-0061 Amendment A1's blanket "`dotmac-treasury` is NOT to be
  created yet" — replaced by a NARROW, GATED authorization with a named
  precondition rather than a standing prohibition
- Extends: ADR-0042 § 3 (payment obligations are not payment instructions),
  ADR-0061 §§ 1–3 (a payout is ERP's decision; its provider is a binding)
- Related: ADR-0006 + its product-first extraction amendment (`AGENTS.md`
  rule 22), ADR-0017 (adoption is the scarce resource), ADR-0024 (applications
  compose by synchronizing data), ADR-0026 (approval is not the transition),
  ADR-0014 (at-most-once execution has one owner), ADR-0041 (Accounting),
  ADR-0044 (Banking), ADR-0046 (Payroll), ADR-0047 + Amendment A1 (the
  six-owner disbursement split), ADR-0050 (Procurement),
  `docs/inventories/treasury-payment-execution-sources.md`

## Context

ADR-0042 § 3 separated a liability from the act of paying it and left the
paying owner unnamed. ADR-0061 § 1 named it for payouts — ERP's
`PaymentService` — and its Amendment A1 then forbade allocating a shared
`dotmac-treasury` distribution or namespace until a product-first dossier
established what lifecycle such an owner would hold.

**That dossier is `docs/inventories/treasury-payment-execution-sources.md`**, a
five-source audit produced on a sibling branch
(`docs/treasury-product-first-dossier`, commit `2ca15a86`) and not yet merged
with this one. It is the evidence base for this record, and its § 12.3 **G5**
asks in terms for the decision this ADR makes: *"a new owner in this space needs
its own decision recording the boundary against Accounting, Banking, Payments,
Approvals and the Integrator. This document is evidence for that ADR; it is not
the ADR."* Section references below are to that file. It is facts, not mandates
— where its recommendation and this record differ, this record decides.

The finding that shapes everything below: **ERP does not have one
payment-execution path, it has two, and only one of them speaks to a provider.**
There is an individual, API-driven expense-transfer lifecycle (§ 1.1), and there
is a file-based AP/payroll process (§ 1.4) where a human produces a schedule
through `PaymentBatchService.generate_bank_file` → `BankUploadService`, a bank
moves the money, and a statement comes back days later — AP's `SENT` is *"a
human's promise that a bank transfer was made"*, with no external call anywhere
in that path. A Treasury owner that models only the first is useless for the
payroll run that actually pays people; one that models only the second cannot
carry an ambiguous provider answer. Both rails exist from the beginning or the
module is not worth extracting.

The second finding is a hazard rather than a shape: the batch engine is dead
code with a live `client.initiate_transfer` at `batch_transfer_service.py:409`,
zero tests, and an `approve_batch` with neither a separation-of-duties nor a
permission check (§ 1.3). Porting it would carry that defect into a shared
module and give it callers.

## Decision

### 1. The owner is `PaymentInstruction`, and it is narrow

A Treasury owner is authorized, scoped to ONE aggregate: the **payment
instruction** — an authorized, immutable order to move a specific amount of a
specific currency to a specific destination, and the record of what happened to
it.

Its lifecycle is exactly:

```
authorized → submitting → ambiguous | submitted → settled | failed | reversed
```

Read as prose: an instruction is AUTHORIZED before anything touches a rail;
SUBMITTING is the window in which provider or file I/O is in flight;
AMBIGUOUS is the terminal-for-now state of a submission whose outcome is not
known; SUBMITTED means the rail accepted it; SETTLED, FAILED and REVERSED are
resolutions carrying their own evidence.

This supersedes the sketch in the dossier's § 12.1
(`AUTHORIZED → INSTRUCTED → SUBMITTED → AMBIGUOUS → SETTLED | FAILED |
REVERSED`), which was evidence-side shorthand: `INSTRUCTED` and `SUBMITTED`
collapse into the authorization plus the `submitting` window, and `ambiguous`
is a sibling of `submitted` rather than a stage after it — an ambiguous
submission is precisely one we cannot say was submitted.

`ambiguous` is a first-class state and not an error code, for the reason
ADR-0061 § 1 already fixed in the Integrator: *a retry of a money-moving
command with an unknown outcome is a business decision about whether to pay
twice*. An instruction in `ambiguous` is resolved by reconciliation against the
rail it was submitted to — never by resubmission, and never by a transport.

Treasury does NOT own: what is owed (Payables), whether a claim may be
reimbursed (Expenses), what the payment means to the books (Accounting), what
the bank statement says (Banking), who the payee is (Party/People/Supplier), or
how a provider is authenticated and called (the Integrator). ADR-0047
Amendment A1 holds the full six-owner split and this record does not restate it.

### 2. TWO rails from the beginning

| Rail | What Treasury submits to | Evidence it must hold |
|---|---|---|
| **API rail** | a capability binding through the Integrator — `payments.payout.v1`, bindable to Paystack or Flutterwave v4 (ADR-0061 §§ 2–3) | the dispatch correlation, the connector's typed outcome including the ones that received no answer, and the provider evidence carried on it |
| **Manual bank-file rail** | a human, via an exported instruction file, for AP and payroll | the IMMUTABLE export (content plus its digest, who exported it, when), the operator's submission evidence (what was lodged with which bank, when, by whom), and the LATER bank settlement evidence that closes the loop |

Both rails produce the same instruction lifecycle. The manual rail's states are
reached by operator-supplied evidence instead of a provider response; they are
not a second, weaker lifecycle.

The file rail is not hypothetical and is not secondary: **ERP's AP and payroll
money does not move through a provider API at all.** It moves through an Excel
file, a human and a bank — one shared generator
(`app/services/finance/banking/bank_upload.py:58`, formats `zenith`, `access`,
`gtbank`, `generic`) serving `PaymentBatchService.generate_bank_file`
(`app/services/finance/ap/payment_batch.py:689`) for suppliers and the payroll
run's own handler (`app/services/people/payroll/web/run_web.py:1102`) for salary
slips — dossier § 1.4. A Treasury owner that modelled only the API rail could
not pay staff.

**Invariant — exporting a spreadsheet must not mark an instruction paid.** An
export is evidence that a file was produced. It is not evidence that a bank
accepted it, and it is emphatically not evidence that money moved. Producing an
export may move an instruction no further than `submitting`. `submitted`
requires operator submission evidence; `settled` requires bank settlement
evidence — Banking's observation, matched, not the exporter's optimism. A
Treasury implementation in which the export routine writes a settled/paid state
is refused at review, and the sensitivity test for this invariant is the export
path being driven and the instruction asserted to be unsettled afterwards.

This invariant is written down rather than invented: **ERP already honours it
on both file paths.** AP's export writes only `bank_file_generated`,
`bank_file_reference` and `bank_file_generated_at`
(`app/services/finance/ap/payment_batch.py:816-818`) and touches no payment
status, invoice or GL entry; the payroll export writes nothing at all. What ERP
does NOT have is the other half — no export digest, no record of who lodged
which file with which bank, and no settlement evidence closing the loop. Its AP
substitute for that evidence is a human's word: `SENT` is written with no
external call in the path, and `gl_impacting()` declares `CLEARED` while the
journal is actually written at `SENT` (dossier § 1.4). That gap is the work; the
invariant itself is the part already proven safe in live code.

### 3. `PaymentRun`, not a generic provider batch

Grouping is real and it is owned — but the unit is a **payment run**, not a
provider's batch endpoint.

A generic provider batch is the WRONG owner because **provider calls are not
atomic.** Ten transfers submitted as one call can come back as seven successes,
two failures and one ambiguous result. A batch aggregate whose state is the
provider's batch response is therefore a lie in the common case, and the
specific lie it tells is the expensive one: it marks nine instructions paid
because the batch "succeeded", including the one nobody knows about.

`PaymentRun` is defined by what it does, all of which the batch shape cannot:

- it groups instructions that were **individually authorized** — the run does
  not confer authorization on its members;
- it serves **both rails** — an API-rail run and a bank-file run are the same
  aggregate with different submission evidence;
- **run authorization goes through Approvals** (ADR-0026: approval is not the
  transition; Treasury owns the transition after validating the decision
  reference);
- it produces **immutable export and submission evidence** per § 2;
- **run progress is DERIVED from its instructions' outcomes**, never stored as
  an independent status that could disagree with them;
- it **never marks every instruction paid from a batch-level response**; and
- it allows **partial reconciliation and replay per instruction** — the two
  failures are re-authorized individually, the ambiguous one is reconciled
  against its original rail, and the seven successes are untouched.

**`BatchTransferService` is NOT the port source.** It is dead code — zero
callers, not exported, zero tests, no route (ADR-0061 A1; dossier § 1.3) — it is
a provider-batch shape welded to one document type at the schema level
(`TransferBatchItem.expense_claim_id` is a hard FK to
`expense.expense_claim.claim_id`), and its `approve_batch` has neither a
separation-of-duties check nor a permission gate while `_process_batch_item`
holds a live call path to `PaystackClient.initiate_transfer`. Its own batch
status is written by two files under two different rules, only one of which can
ever fire. The dossier reaches the same verdict from the evidence side: § 12.2
rules batching **INSUFFICIENT EVIDENCE** — no qualifying source, because the one
general batch engine is dead and AP's well-tested batch executes nothing.
`PaymentRun` is derived instead from the two
things that are actually live: the AP/payroll bank-file process, and the
individual expense-transfer lifecycle. A dead class's method names are not a
design.

The approval shape comes from the live AP path, which is the only place in the
fleet that gets it right: `PaymentBatchService.approve_batch` refuses the
creator as approver (`app/services/finance/ap/payment_batch.py:571-575`). Every
other path is weaker — `SupplierPaymentService.approve_payment` has the check
only behind `FEATURE_REQUIRE_SOD`, off by default, and `BatchTransferService
.approve_batch` has none at all (dossier § 5).

And the payout rail itself has none. The dossier's § 5 finding is blunt: **there
is no Approvals integration in any ERP payout path** — a grep for approval
references across every payment and AP module returns zero hits, and
`SupplierPayment.approval_request_id` is declared and never written. What
actually gates a live Paystack payout is an RBAC dependency that grants on any
one of seven things, **`payments:read` — a read permission — among them**.

So `PaymentRun`'s Approvals-based authorization is a capability GAIN rather than
an authority migration, exactly as the dossier concludes, and under ADR-0026 the
decision stays with `dotmac-approvals` while Treasury validates the supplied
reference and performs the transition itself. Generalising AP's block is what
makes this a legitimate product-first extraction under `AGENTS.md` rule 22
rather than an invention.

Two shapes it must NOT inherit, both measured in ERP: the **silent skip** — on
both file paths a payee with no account number is dropped from the export with a
log line (`app/services/finance/ap/payment_batch.py:770-779`;
`app/services/people/payroll/web/run_web.py:1066`), where an instruction that
cannot be submitted must be a STATE rather than an omission from a spreadsheet —
and the **per-intent recipient mint**, where `client.create_transfer_recipient`
is called unconditionally on every single intent with no lookup or reuse
(`app/services/finance/payments/payment_service.py:921-930`).

### 4. Rail routing is PRE-SUBMISSION only

The dangerous case, stated first because the rule only makes sense once it is
in view:

> A payout is submitted to Paystack. The request times out. **It may have
> succeeded.** A routing layer observes a failure, decides another provider is
> healthier, and retries the instruction through Flutterwave. The beneficiary
> is paid twice, and the second payment was authorized by nobody.

The rules that make that unreachable:

1. The rail and provider are selected **BEFORE** submission.
2. The choice is **stamped immutably on the authorized instruction** — it is
   part of what was authorized, not a runtime lookup.
3. Selection may consider currency, destination, the configured account, limits
   and rail availability. It may not consider the outcome of an attempt,
   because at selection time there is none.
4. **Once provider I/O begins, the instruction cannot change providers.** There
   is no failover inside `submitting`.
5. An **ambiguous** result is reconciled against the **ORIGINAL** provider.
   Only that provider can answer the only question that matters.
6. Rerouting requires a **conclusively unsubmitted or terminally failed**
   instruction, PLUS a new authorization and a new instruction version. It is
   an authorization event, not a retry.

Operator-controlled switching comes first: a human moves future traffic to the
other rail. An automated selection policy may be added later, but it uses the
same contract and is bound by the same six rules — automation is not a licence
to select after submission.

**Paystack ↔ Flutterwave interchangeability is safe CONFIGURATION, not
cross-provider retry.** ADR-0061 § 3's obligation — move payout traffic by
changing a binding, with no ERP release — is about which provider the NEXT
authorized instruction is stamped for. It is not, and must never be read as, a
licence for a router to re-aim an in-flight or unresolved payment.

### 5. Payee and destination are split, and a recipient code is neither

A provider's recipient code is a correlation the provider issues so it can find
its own record. It is not identity, it is not a bank account, and it must never
become either.

| Layer | Owner | Holds |
|---|---|---|
| **Payee** | Party / People / Supplier | who the beneficiary IS — the business identity |
| **Verified bank details** | Banking, or the appropriate directory owner | the account, verified, with its verification evidence |
| **`PayoutDestinationSnapshot`** | **Treasury** | an IMMUTABLE, VERSIONED copy of the destination as it stood at AUTHORIZATION — provider-neutral |
| **Provider recipient correlation** | the **Integrator** | the provider-side code, scoped to `(installation, destination fingerprint)` and to nothing else |

The starting point is better than it might have been: ERP never let a recipient
code become business identity. `transfer_recipient_code` lives on
`PaymentIntent` (`app/models/finance/payments/payment_intent.py:130-134`) and
`TransferBatchItem` — correlation records — while `Employee` and
`Supplier.bank_details` hold raw account and bank-code data and no recipient
code at all. So this section is a formalisation, not a migration away from a
wrong home. Today's cost is different and is provider churn: a fresh recipient
is minted per intent because there is no scoped correlation to look one up in.

The dossier reaches the same conclusion from the transport side and adds the
constraint that makes it urgent (§ 12.2): `resolve_bank_account` and
`create_transfer_recipient` exist in the released connector but **cannot be used
through DELIVERY at all**, because `dotmac-integration` has no durable
command-response seam and a returned recipient code has nowhere to land. That is
the dossier's gate **G1**, and it is why the correlation must live inside the
Integrator rather than be handed back for a product to keep.

Consequences, stated so they cannot be argued away later:

- Paystack may create a recipient code **internally**, behind `submit_payout`,
  exactly as ADR-0061 A3 requires. Flutterwave may use the destination
  directly. Neither difference is visible to ERP.
- ERP sees a provider-neutral destination and, where a correlation is needed at
  all, an **opaque Integrator correlation**. It never sees a recipient code.
- **`create_paystack_recipient` is never a product command.** Not a route, not
  a task, not a service method, not a step a product sequences. It is a
  connector internal or it does not exist.
- **Changing bank details creates a NEW destination version and requires
  reauthorization.** An authorized instruction points at the snapshot version
  it was authorized against. Editing a beneficiary's account must never silently
  re-aim money that a human already approved.

### 6. The complete Treasury surface — twelve items

The scope is closed. Anything not on this list is another owner's, and adding
to the list is an ADR, not an implementation detail:

1. `PaymentInstruction` and its lifecycle (§ 1).
2. `PaymentRun` grouping of individually authorized instructions (§ 3).
3. The manual bank-file rail (§ 2).
4. The Integrator API rail (§ 2).
5. Versioned `PayoutDestinationSnapshot`s (§ 5).
6. Pre-submission rail-selection policy (§ 4).
7. Paystack and Flutterwave bindings behind `payments.payout.v1` (ADR-0061 § 2).
8. Provider-recipient correlation held INSIDE the Integrator (§ 5).
9. Per-instruction reconciliation and reversal (§§ 1, 3).
10. Separation-of-duties approval, through Approvals (§ 3).
11. Immutable audit and export evidence (§ 2).
12. No provider switching after an ambiguous submission (§ 4).

### 7. The gate — nothing is built until ERP's three-writer defect is fixed

**None of the above may be constructed before ERP's `PaymentIntent.status`
three-writer violation is fixed in the ERP repository.**

This is not sequencing hygiene. Extraction copies a lifecycle, and a lifecycle
with three writers is a lifecycle with no owner — porting it would take a defect
that today lives in one product and make it a shared module's contract, at which
point every adopter inherits it and the fix requires a coordinated release wave
instead of one repository's diff. ADR-0006's product-first rule says the
qualifying production implementation is ported *with its parity tests*; a
three-writer state has no parity to test against, because the three writers do
not agree on what the state means.

The three writers, measured (dossier § 1.2, *"who is the single writer?
**Nobody is.**"*):

| # | Writer | Sites |
|---|---|---|
| 1 | `app/services/finance/payments/payment_service.py` — the intended owner | eighteen assignments across the transfer lifecycle |
| 2 | `app/services/finance/payments/batch_transfer_service.py` | `:394` PENDING (constructor), `:423` PROCESSING inside `_process_batch_item`, bypassing `PaymentService` entirely |
| 3 | `app/tasks/expense.py` (`poll_stuck_expense_transfers`) | `:827` EXPIRED, `:900` PENDING→PROCESSING written directly and THEN `svc.poll_transfer_status(...)` at `:903`, `:924` FAILED. **No tests** |

Writer 2 disappears with the deletion § 3 and ADR-0061 A7 already require — the
class is dead. Writer 3 is the actual work, and it is the clearest statement of
the defect available: a Celery task decides a state transition and then, three
lines later, asks the owning service to decide the same thing.

The gate is satisfied by a reviewable ERP diff that leaves `PaymentIntent.status`
with exactly ONE writer — the other two paths removed or converted to requests to
that writer — plus a test that fails if a second writer reappears. A concurrent
workstream owns the ERP fix; this record does not make it.

`SupplierPayment.status` has the same shape — three writers, one of them an
inbound Remita callback with no visible organization scoping (dossier § 1.2).
That is recorded, not gated on: it is AP's state, not the instruction's.

**Relationship to the dossier's own gates.** Its § 12.3 lists five evidence-side
preconditions: **G1** a durable command-response seam in `dotmac-integration`,
**G2** Accounting authority moved, **G3** Banking authority moved, **G4** an
obligation owner cut over, and **G5** an ADR. **G5 is satisfied by this record.**
G1–G4 stand where the dossier records them; this record neither adopts nor
discards them, because they gate different things — they are about whether the
extraction can be *completed*, while the writer gate above is about whether it
may *begin*. All of them are unsatisfied today, so nothing is allocated either
way.

Until the gate is satisfied: no `dotmac-treasury` distribution, no namespace,
no `mod_*` short code allocation, no migration lineage. ADR-0061 A1's
prohibition stands in full — this record replaces its *"until a dossier exists"*
precondition with a *"until the defect is fixed"* one, and narrows what may then
be built to §§ 1–6.

## Consequences

- ADR-0061 Amendment A1's standing prohibition becomes a gated authorization
  with a named, checkable precondition. Nothing may be allocated today.
- The extraction, when it happens, is narrower than the ERP surface it comes
  from: `PaymentInstruction` and `PaymentRun` only. AP invoice matching, GL
  posting, expense-claim lifecycle and statement ingestion stay with their
  existing owners.
- `BatchTransferService` is not a port source; it is a deletion (ADR-0061
  Amendment A7).
- Payables' obligation projection is fed by Treasury's settlement observation,
  as ADR-0042 §§ 3 and 5 already require — this record does not change that
  seam, it names the thing on the sending end of it.
- The manual bank-file rail means Treasury has a durable interest in export
  artifacts and their digests. That is evidence storage, not document
  management; `dotmac-documents` (ADR-0049) is not absorbed.
- ADR-0061 § 5's five preconditions for CLAIMING payout interchangeability are
  unchanged and unmet. This record authorizes design and a gated build; it
  claims no parity — and under ADR-0061 A7 the ERP payout evidence it rests on
  reads *"Implemented and tested; production enablement unconfirmed"*, which the
  dossier's § 12.4 states in the same terms.
- The dossier is on a sibling branch that has not merged with this one. Both
  touch `docs/inventories/` and neither rewrites the other; the index row the
  dossier's § 15 says it owes is left to that branch, deliberately, so the two
  do not collide.

## Alternatives rejected

**Port ERP's payment execution wholesale.** It would bring GL posting, expense
lifecycle, provider-named columns and the dead batch class into a shared module,
recreating in one distribution the aggregate ADR-0042 refused to create.

**Model a generic provider batch and let the run be its mirror.** § 3. Provider
calls are not atomic; the batch-level response is not an answer about any
individual instruction, and treating it as one marks unknown payments paid.

**Ship the API rail first and add the file rail later.** The file rail is the
one that pays staff and suppliers today. Adding it afterwards would mean
retrofitting operator submission evidence and export immutability into a
lifecycle designed around a synchronous provider answer — which is precisely
how an export ends up being allowed to mark something paid.

**Let a routing layer fail over between providers.** § 4's opening case. A
timeout is not a failure, and the only owner who can tell them apart is the
provider that received the request.

**Keep the recipient code on the payee record because it is convenient.** It
makes a provider correlation into business identity, breaks the moment a second
provider is bound, and turns "change your bank account" into a silent re-aiming
of authorized money. § 5.

**Wait for the ERP fix and then decide the scope.** The scope decision is the
part that is safe to make now, and making it now is what stops the ERP fix from
being designed around a shape nobody has agreed to. The GATE stops construction;
it does not stop deciding.
