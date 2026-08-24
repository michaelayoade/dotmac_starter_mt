# ADR-0063: Treasury owns the payment instruction, not the provider batch

> **Number allocation, 2026-08-24.** `0059` and `0060` remain allocated on
> sibling branches that have not merged; `0061` and `0062` are allocated on this
> branch. This record takes `0063` under the same rule ADR-0061 recorded: the
> earlier record keeps the number.

- Status: Accepted. Amended 2026-08-24 (A1–A3) — see "Amendment — 2026-08-24"
  at the end of this record. A1 rules that ERP's `PROCESSING` state is SPLIT
  before extraction and that an unprobed row migrates to `ambiguous`, never
  `submitted`. A2 names payroll as a producer of `PaymentInstruction` rows and
  records the salary-component privacy boundary. A3 fixes the ordered
  dependency and makes ERP's payout authorization defect a hard RELEASE gate
  alongside § 7's construction gate. No earlier text is rewritten.
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

> **Extended 2026-08-24 — see Amendment A1.** This lifecycle is the
> DESTINATION. A1 rules what ERP must do to its own `PaymentIntent.status`
> BEFORE anything ports into it: `PROCESSING` conflates a durable intent with
> an attempted submission and is SPLIT first, worker claim/lease state moves
> off the domain status, and every existing `PROCESSING` row is PROBED — an
> unprobed row becomes `ambiguous`, never `submitted`.

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

> **First application of the closed-list rule, 2026-08-24 — see ADR-0061
> Amendment A9.** `payments.refund.v1` is KEPT as a capability (ADR-0061 A8)
> and a refund is NOT on this list. Treasury is therefore not automatically the
> refund owner: a refund reverses a receivable rather than discharging a
> payable, its consequence lands in Billing/AR and Accounting, and extending
> this list requires its own record with its own evidence.

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

> **A SECOND gate added 2026-08-24 — see Amendment A3.** This section gates
> CONSTRUCTION on the three-writer fix. A3 adds a gate on RELEASE and
> ENABLEMENT: an ERP READ permission (`payments:read`) currently satisfies the
> guard on `POST /transfers/{intent_id}/initiate`, which executes a real
> transfer. No payout release or enablement passes that blocker, whatever else
> is finished.

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


## Amendment — 2026-08-24 (accepted corrections A1–A3)

Three corrections accepted the same day this record was. Each names the section
it extends or corrects; nothing above is rewritten, and each superseded or
extended spot carries a short pointer.

### A1. ERP's `PROCESSING` is SPLIT before extraction, and an unprobed row is `ambiguous`

§ 1 gives the destination lifecycle and § 7 gates construction on
`PaymentIntent.status` having one writer. Neither says what that one writer's
STATE VOCABULARY must be, and there is a defect in it that would survive the
fix: **`PROCESSING` is ambiguous, and carrying it into the new module under a
better-sounding name would launder the ambiguity into a contract.**

The rule: **do not carry an ambiguous state into the new module under a
misleading name.** `PROCESSING` today answers "something is happening" and is
written by three writers who mean different things by it — the owning service
means "the provider call is running", and `poll_stuck_expense_transfers` means
"I have picked this row up" (`app/tasks/expense.py:900`, written directly and
then followed three lines later by `svc.poll_transfer_status(...)`). A state
that means both is a state that means neither.

ERP must distinguish, before anything is extracted:

| State | Means, precisely |
|---|---|
| `submission_requested` | a DURABLE INTENT exists and the provider outcome **has not been attempted yet** |
| `submitted` | the provider **conclusively accepted** it |
| `ambiguous` | the request **may have landed**; reconciliation is required |
| `settled` | the money conclusively moved, with settlement evidence |
| `failed` | terminal, provider-confirmed non-execution |
| `reversed` | a confirmed reversal of an executed movement |

**Worker claim/lease state belongs to the EXECUTION ENGINE, not to
`PaymentIntent`.** "A worker has picked this row up", "the lease expires at",
"this is attempt three" are properties of an execution attempt, and putting
them in a domain status column is how the third writer got there in the first
place: the task needed somewhere to record a claim and used the only column it
could see. They move onto the execution/idempotency record (`AGENTS.md` rule
21 / ADR-0014 — at-most-once execution has ONE owner), and no worker writes a
business state to announce that it has started work. This is not a separate
piece of work from § 7's one-writer fix; it is most of why the third writer
exists.

**Correspondence with § 1, stated rather than assumed.** These are ERP's state
names and § 1's are Treasury's; they are not renamed into each other.
`submission_requested` corresponds to the OPENING of § 1's `submitting` window;
`submitted`, `ambiguous`, `settled`, `failed` and `reversed` correspond
one-to-one. § 1's `authorized` has **no ERP counterpart today**, because ERP has
no separate authorization step in the payout path at all — that is the same gap
§ 3 records from the Approvals side, and it is a capability gain rather than a
migration.

**The migration rule, and it is the important part.** Existing `PROCESSING`
rows must be **PROBED** against the provider, one row at a time:

| Probe result | Maps to |
|---|---|
| the provider conclusively holds an accepted transfer | `submitted`, with the provider evidence recorded |
| the provider conclusively holds none, and the intent is intact | `failed` — **not** back to `submission_requested`. Re-submission is a new authorized decision, not a migration handing the row back to a worker as though nothing happened |
| anything else — provider unreachable, no conclusive answer, a row too old to probe, an inconclusive record | **`ambiguous`** |

**Without conclusive provider evidence a row maps to `ambiguous`, never
`submitted`.** `ambiguous` is the DEFAULT, and it is the default in the
direction that costs a human a reconciliation rather than the direction that
costs a beneficiary. Assuming success on migration is how a **double payment**
or a **silently lost disbursement** enters the new module — a row wrongly
marked `submitted` is never looked at again, and neither the person who was
paid twice nor the person who was not paid at all appears in any queue.

Two constraints on performing it:

- **The probe is READ-ONLY.** A probe reads the provider's own record for the
  reference. A probe that initiates, re-sends or "repairs" is not a probe, and
  it is § 4's failover hazard wearing a migration's clothes.
- **QUIESCE the worker during the migration.** `poll_stuck_expense_transfers`
  writes the very column being migrated. Probing while it mutates rows means
  the probe answers about a state that has already changed. Stop the schedule,
  drain what is in flight, migrate and probe, then restart the worker against
  the new vocabulary — with its claim/lease state on the execution record, per
  the paragraph above.

*Extends: § 1 (the destination lifecycle) and § 7 (the one-writer gate, whose
scope now includes the state vocabulary the one writer writes). Corrects
nothing; it names a precondition § 7 left implicit. Related: ADR-0061 A1
(`PaymentService` as sole interim owner), ADR-0014, `AGENTS.md` rule 21.*

### A2. Payroll produces `PaymentInstruction` rows, and Treasury never sees a salary component

§ 2 establishes the manual bank-file rail and observes that ERP's payroll money
moves through a spreadsheet, a human and a bank. § 3 establishes `PaymentRun`.
Neither says how a payroll run and Treasury actually meet, and ADR-0046 says
only that *"payment execution and GL posting are downstream adapters over
finalized liabilities"*. This makes that seam concrete.

**Payroll owns calculation, approval and the net-pay obligation. Treasury owns
disbursement.** Therefore:

1. **One authorized net-pay obligation produces ONE `PaymentInstruction`.** Not
   one per component, not one per run, not one per bank file. The obligation
   Payroll finalizes is the unit that gets paid, and it is the unit Treasury
   can reconcile, fail and retry on its own.
2. **A payroll run MAY group those instructions into a `PaymentRun`.** Grouping
   is convenience and evidence; it confers no authorization (§ 3), so a run
   that is approved does not thereby authorize an instruction that was not.
3. **Treasury's manual rail produces the bank-upload artifact.** The file
   format is Treasury's, not Payroll's — today ERP's payroll run calls the
   shared generator itself (`app/services/people/payroll/web/run_web.py:1102`
   over `app/services/finance/banking/bank_upload.py:58`), which is one domain
   owning another's rail.
4. **Exporting the file does NOT mark payroll paid.** § 2's invariant, applied
   where it matters most. ERP's payroll export writes no status at all today;
   that behaviour is correct and must survive the port intact.
5. **Settlement evidence returns to Payroll through a TYPED OBSERVATION.**
   Treasury does not write a Payroll status; Payroll's own owner consumes the
   observation and advances its coverage, exactly as ADR-0046 already requires
   (*"settlement observations advance coverage but do not execute payment"*)
   and ADR-0024 requires of every importer.

**The privacy boundary, and it is a privacy boundary as much as an ownership
one.** Treasury receives ONLY:

- the **net amount**,
- the **currency**,
- the **payee / destination reference**, and
- the **payroll obligation reference**.

**Never salary components.** Not gross pay, basic, allowances, overtime,
bonuses, deductions, tax, pension, loan repayments, garnishments, grade or
band — and nothing from which any of them can be derived. Two consequences that
are easy to violate by accident:

- **The instruction's narration/memo field must not carry a breakdown.** The
  obligation reference is the pointer; resolving it is Payroll's authorization
  decision, made by Payroll, for a reader Payroll has decided may see it.
- **The bank-file rail means a spreadsheet leaves the building.** A payments
  operator, a bank portal and an email attachment are a payments-operations
  audience, not an HR one. A column headed "pension deduction" in an exported
  file is a payroll disclosure to everyone who touches that file, and the file
  is the artifact § 2 requires Treasury to retain immutably — so a component
  that reaches Treasury is a component that is retained, digested and archived
  where HR cannot reach it to correct or redact it.

**The silent skip is refused with particular force here.** § 3 already forbids
inheriting it; on the payroll path it is an unpaid employee. ERP drops a payee
with no account number from the export with a log line
(`app/services/people/payroll/web/run_web.py:1066`), so nobody is told and no
queue shows it. An instruction that cannot be submitted is a STATE — under A1's
vocabulary it never leaves `submission_requested` and is visibly blocked — never
an omission from a spreadsheet.

*Extends: §§ 2 and 3, and makes concrete ADR-0046's "payment execution … are
downstream adapters over finalized liabilities". Related: ADR-0046 Amendment A1
(the same seam recorded on the Payroll side), ADR-0044 (Banking supplies the
settlement observation), ADR-0026 (approval is not the transition).*

### A3. The ordered dependency, and the authorization blocker is a HARD gate

§ 7 gates construction on one defect. It does not say what order the rest
happens in, and the order is not free: three of the steps reduce blast radius
and none of them depends on a design decision, so they come first.

**The dependency order:**

| # | Step | Where | Why here |
|---|---|---|---|
| 1 | ERP execute-permission containment | ERP | An ERP READ permission executes a real transfer today. Nothing else matters more, and it depends on nothing |
| 2 | ERP authorization-log audit on a named target | ERP | Once containment lands, find out what the old guard actually admitted. An audit against a NAMED target, under `AGENTS.md` rule 30 |
| 3 | Delete dead `BatchTransferService` | ERP | Security-sensitive dead code with a live `initiate_transfer` path (ADR-0061 A7, § 3). Deleting it also removes writer 2 of step 4 |
| 4 | Reduce `PaymentIntent.status` to ONE writer | ERP | § 7's construction gate |
| 5 | Split `PROCESSING` from `ambiguous` | ERP | A1. After 4, because one writer is what makes the split reviewable |
| 6 | Add capability request/result schemas | `dotmac-integration` + connectors | ADR-0061 A2, ADR-0024 § 10. Before 7, because the API rail speaks through the contract |
| 7 | Build Treasury `PaymentInstruction` and `PaymentRun` | new module | §§ 1–3. Gated on 4 and 5 |
| 8 | Add expense, AP and payroll producers | ERP → Treasury | A2. After 7, because a producer needs something to produce into |
| 9 | Complete Paystack and Flutterwave payout bindings | connectors | ADR-0061 §§ 2–3 |
| 10 | Build refund through its NAMED owner | Billing / the named owner | ADR-0061 A8 keeps the capability; A9 refuses to assume Treasury owns it. The owner is named before the build, not during it |
| 11 | Provider sandbox proof, CI and cutover | all | ADR-0061 § 5 step 5 — the binding swap exercised in shadow, with evidence |

**The hard gate.** Step 4's digest/version work may continue in parallel — it
is internal to ERP and touches no rail. But:

> **No payout release and no payout enablement passes the authorization
> blocker.**

The blocker, stated so it can be checked: **an ERP read permission
(`payments:read`) currently satisfies the guard on
`POST /transfers/{intent_id}/initiate`, which executes a real transfer.** A
permission whose name says "read" authorizes moving money to a beneficiary.
That is not a hardening opportunity; it is the containment failure that makes
every other control on the path decorative, because the weakest admitted
credential is the one that defines the path's real authorization. A dedicated
ERP security change is in progress on
`fix/payout-execute-permission-containment`.

Until that lands: no payout capability is enabled in any environment, no payout
release is cut, and no claim of payout readiness is made — irrespective of how
much of steps 4–11 is finished. This gate is about RELEASE and ENABLEMENT and
sits alongside § 7's gate on CONSTRUCTION; they are independent, and satisfying
either does not satisfy the other.

Consistent with ADR-0061 A7, this changes no evidentiary claim in either
direction: *"Implemented and tested; production enablement unconfirmed"* remains
the required wording, and the blocker is a reason enablement must not be sought,
not evidence about whether it has happened.

*Extends: § 7, which gated construction and was silent on release. Related:
ADR-0061 A7 and § 5, `AGENTS.md` rules 28 (m)–(o) and 30,
`docs/inventories/treasury-payment-execution-sources.md` § 5 (the seven-way RBAC
dependency that admits `payments:read`).*
