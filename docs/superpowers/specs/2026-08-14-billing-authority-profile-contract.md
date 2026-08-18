# `dotmac-billing` — the authority profile and the published contracts

> **Review status: FROZEN FOR BILLING V1 — 2026-08-17.** ADR-0020's 2026-08-17
> amendment resolves the obligation name, receivable-position shape,
> due-date-basis evidence and official-artifact relation. `AcceptSettlementV1`
> and `InvoiceDocumentFactV1` are frozen as described here. Allocation and
> coverage remain internal Billing contracts and are deliberately not published.
>
> **Status:** specification of intent. Non-authoritative
> (`docs/superpowers/specs/`); the accepted decision is ADR-0020 and its
> 2026-08-14 and 2026-08-17 amendments. ADR-0030 § 6 grants the named
> owner-directed implementation exception for `dotmac-billing`; P11 remains an
> adoption/cutover gate rather than permission to weaken PostgreSQL, migration
> or plane proofs.
> **Date:** 2026-08-14
> **Governed by:** ADR-0016 (coverage is derived), ADR-0014 (at-most-once has one
> owner), ADR-0008 (declaration registries), ADR-0011/0012 (settings),
> ADR-0018 (an exemption states an enforceable premise), ADR-0020 (billing owns
> operational receivables) + amendment A1–A6, ADR-0022 (`dotmac-files` owns
> stored bytes), ADR-0023 (dual-plane persistence), ADR-0024 (apps compose by
> synchronizing data; the Integrator owns provider transport).
> **Companion documents:** `docs/inventories/billing-extraction-dossier.md`
> (what is ported and from where),
> `docs/inventories/billing-parity-tests.md` (the proofs that must keep
> passing), and the focused execution plan
> `docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md` (the
> cutover sequence, which this spec does not restate).

This document answers two questions the ADR states but does not specify:

1. **How does an assembly bind exactly one commercial authority, and how does it
   mechanically refuse two?** (ADR-0020 § 3, C6.)
2. **What exactly does billing publish and accept, such that no consumer ever
   needs to import it or read its tables?** (ADR-0024 § 2, ADR-0020 A1.)

---

## Part 1 — The commercial authority profile

### 1.1 The three authorities, restated as capability sets

ADR-0020 § 3 names three mutually exclusive authorities. The specification-level
content is *which writers exist* under each, because that is the property a boot
check can test and a database grant can enforce.

| | `internal` | `provider_owned` | `external_finance` |
|---|---|---|---|
| Invoice/credit-note writer | **billing** | none (projection only) | none |
| Operational receivables subledger writer | **billing** | none (projection only) | none |
| Rated-obligation acceptance | **billing** | billing (still accepted; produces no local invoice) | refused |
| Settlement acceptance | **billing** | projection of a provider-owned settlement | refused |
| Allocation / reversal / refund | **billing** | none — the provider allocates | none |
| Positions (`ReceivablePositionV1`) | derived from billing's own posting groups | derived from the labelled projection, marked `derived_from = "projection"` | not published |
| `AccountingFactV1` emission | yes | yes, with `source_authority = provider` | no |
| `InvoiceDocumentFactV1` emission | yes | no (the provider renders) | no |
| Number series | bound P4 numbering owner | provider's own | external |
| Drift detection + repair required | no (it is the authority) | **yes, mandatory** | n/a |

`external_finance` is the name this spec uses for ADR-0020's "manual/ERP
invoicing". It is one word rather than a slash because it is one binding value,
and a vocabulary member that reads as two options invites a fourth.

The vocabulary is closed and is an enum — three members, exhaustive, identical in
every product. That is the case ADR-0008 says an enum is right for, and it is
deliberately not a declaration registry: a fourth commercial authority is an
accounting decision, not a product extension point.

### 1.2 What replaces `if deployment_mode == ...`

C6 forbids the branch. The replacement is **construction, not comparison**: the
authority selects which objects the composition root builds, and the built object
graph carries no knowledge of why it was built that way.

```text
assembly (composition root)
  bind_commercial_authority(CommercialAuthority.INTERNAL, ...)
     └─ constructs InternalInvoiceAuthority (writer + subledger + numbering port)
  bind_commercial_authority(CommercialAuthority.PROVIDER_OWNED, ...)
     └─ constructs ProjectedInvoiceAuthority (reconciler + drift report; NO writer)
  bind_commercial_authority(CommercialAuthority.EXTERNAL_FINANCE, ...)
     └─ constructs NoInvoiceAuthority (every write method raises AuthorityNotBound)
```

Three consequences follow, and each is separately testable:

- **No billing service reads the authority value.** Services depend on the
  `InvoiceAuthority` port. An architecture test asserts that
  `CommercialAuthority` is imported by exactly two modules — the binding module
  that defines it and the assembly-facing `bind_*` entry point — and by no
  service, repository, model, or router. A third importer fails the build.
- **`NoInvoiceAuthority` is a real object, not `None`.** A `None` authority
  produces `AttributeError` at the first call site that forgot to check, which is
  a branch by another name. `AuthorityNotBound` is a typed refusal with the
  bound authority named in the message.
- **The projection authority cannot become a writer by accident**, because it is
  not the same class. `ProjectedInvoiceAuthority` has no `issue_invoice`
  method to call.

### 1.3 The boot-time check

`bind_commercial_authority` is the only entry point, it is called by the
assembly, and it enforces four things at startup — before the first request, and
in a fail-closed direction.

1. **At most one binding.** The binder holds a module-level slot. A second call
   raises `DuplicateCommercialAuthority(existing, attempted)` — including a
   second call with the *same* value, because two assemblies each believing they
   own the binding is exactly the condition being refused, and idempotent
   re-binding would hide it. Re-binding for tests goes through an explicit
   `reset_commercial_authority()` that is private to the test seam and refuses
   to run when the process has served a request.
2. **At least one binding, if any billing surface is mounted.** Zero bindings
   with a mounted billing router or a registered billing task is
   `CommercialAuthorityUnbound`. Unbound is a legitimate *state* only for an
   assembly that composes billing's contracts but mounts no surface — which is
   the shadow-runner shape in § 1.5.
3. **The adopter premise stays outside Billing.** The binder deliberately has
   no `legacy_financial_writer` flag. A module cannot prove whether a separately
   deployed application still has a writer, and accepting its boolean claim
   would manufacture a second cross-application authority registry. Vendor CP
   proves absence before activation; Sub proves the coupled watermark switch
   and retirement through adopter-owned two-directional ratchets (§ 1.5).
4. **The plane premise.** The binder takes exactly one of a tenant repository
   factory or a platform repository factory, never both (ADR-0023). Supplying
   both is `AmbiguousPlane`. Supplying neither is `NoPlaneBound`.

**Failure mode: the process does not start.** Not a warning, not a degraded
mode, not a fallback to `external_finance`. A money-domain module that starts
with an ambiguous authority is worse than one that does not start, and the
kernel already takes this position for a failing `KeyProvider`
(`CLAUDE.md` § "Supply encryption keys from a secret store": "there is
deliberately no degraded-start knob"). The same reasoning applies with more
force here.

**Database-level enforcement for `external_finance`.** "The local writer is
disabled entirely" is only enforceable if the database says so. Under
`external_finance` the assembly's migration composition **revokes `INSERT`,
`UPDATE` and `DELETE` from the application role on billing's invoice, posting
group and settlement tables**, leaving `SELECT` for archive reads. The Python
`NoInvoiceAuthority` is then a fast, well-typed refusal on top of an enforceable
premise rather than the premise itself — ADR-0018's rule, applied to this
guard's own claim. Under `provider_owned` the same revoke covers the decision
tables while the labelled projection tables keep DML, which is what makes
"observation, not authority" a structural property rather than a naming
convention.

### 1.4 The sensitivity proof

A guard that cannot fail is not a guard. The check ships with proofs that it
bites, in both directions:

| Proof | Plants | Asserts |
|---|---|---|
| `test_a_second_binding_is_refused` | two `bind_commercial_authority` calls | `DuplicateCommercialAuthority`, message names both values |
| `test_rebinding_the_same_authority_is_still_refused` | two identical calls | still refused — sameness is not an exemption |
| `test_two_simultaneously_active_billing_bindings_are_refused` | two authority bindings, even with the same value | `DuplicateCommercialAuthority` |
| `test_a_mounted_surface_with_no_binding_is_refused` | router mounted, no bind | `CommercialAuthorityUnbound` |
| `test_both_planes_bound_is_refused` | tenant + platform factories | `AmbiguousPlane` |
| `test_the_authority_enum_has_exactly_one_reader_module` | a planted `from ... import CommercialAuthority` inside a service module | the import scan fails the build |
| **`test_the_authority_scan_bites`** | temporarily removes the binding module from the scan's allow-set | the scan **fails** — proving the passing result is not vacuous |
| `test_external_finance_cannot_write_invoices` (Postgres) | `external_finance` grants, then a direct `INSERT` as the app role | `InsufficientPrivilege` from the database, not from Python |
| **`test_the_grant_canary_bites`** (Postgres) | the same `INSERT` after deliberately re-granting `INSERT` | the canary **fails** — proving it tests the grant and not an unrelated error |
| `test_provider_owned_has_no_issue_method` | `hasattr(authority, "issue_invoice")` | `False` — the class, not a flag, is the difference |

The two bolded rows are the sensitivity proofs proper: without them, a scan whose
allow-set silently swallowed every module, or a canary that passed because the
table did not exist, would read as green.

### 1.5 The coupled authority switch, and what happens mid-cutover

The focused cutover plan
(`docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md`,
"Recommendation" and S1/S3) decides that **calculations may shadow slice by
slice, but invoices, settlements and allocations switch production authority
together, in one deployment-wide change.** That decision is a direct input to
this contract, and the binding model above is what makes it enforceable rather
than procedural.

The states a Sub-shaped deployment passes through are exactly three, and none of
them is "both":

| Phase | Production assembly | Shadow assembly | Why two writers are impossible |
|---|---|---|---|
| **Pre-switch (S1 shadow)** | legacy production writer remains sole authority; Billing surfaces are unmounted | a *separate process* against a *separate shadow database*, bound `INTERNAL` | The shadow process cannot reach the production database and no financial command is dual-written. |
| **Switch (S3)** | one deployment-wide watermark disables the legacy invoice, settlement and allocation writers and activates Billing | retired | The cutover lock proves no command crossed the watermark twice; any partial switch fails the adopter's startup gate. |
| **Post-switch** | bound `INTERNAL`, legacy rows retained read-only | — | S4's two-directional ratchets drive all displaced writer/import/caller paths to zero before retirement is declared complete. |

Three points the boot check makes explicit that the prose plan leaves implicit:

- **The shadow runner is a separate binding, not a mode.** There is no
  `shadow=True`. A shadow is an assembly bound to `INTERNAL` whose database is
  not the production database. That is why S1's "do not dual-write financial
  commands" is a property of the deployment topology rather than a discipline
  someone must remember.
- **A partially applied cutover is a refused adopter boot.** The adopter owns
  the coupled watermark and proves its legacy routes/jobs are disabled before
  it mounts Billing mutation surfaces. Billing separately refuses a duplicate
  module binding; neither mechanism claims to inspect the other's database.
- **Rollback becomes roll-forward after the first post-watermark fact.** The
  runbook may restore the old deployment only while reconciliation proves zero
  accepted Billing facts beyond the watermark. Afterwards correction appends
  Billing evidence; re-enabling the old writer would create two authorities.

### 1.6 What the profile is *not* allowed to select

Configuration binds a transport or a writer set. It never selects a business
owner (ADR-0024 § 4). Concretely, there is no profile value that:

- moves general-ledger authority into billing;
- turns a `provider_owned` projection into a decision authority;
- lets collections write service or entitlement state;
- selects a currency, a jurisdiction, or a tax treatment as a default; or
- names a payment provider.

---

## Part 2 — The published contracts

Billing has five contract surfaces. Three it **produces**, two it **accepts**.
Every one of them travels as an assembly-wired outbox event or a typed command
(ADR-0020 A1); none of them is a Python import of another business module, and
none of them permits a consumer to read billing's tables (ADR-0024 § 1/§ 2).

```text
subscriptions.RatedObligationOutputV1 ──assembly──> billing.AcceptRatedObligationV1
integrator.SettlementObservationV1    ──assembly──> billing.AcceptSettlementV1
billing.ReceivablePositionV1          ──assembly──> collections.ReceivablesReader
billing.AccountingFactV1              ──assembly──> ERP accounting intake
billing.InvoiceDocumentFactV1         ──assembly──> rendering owner ──> dotmac-files
```

### 2.0 The eight facets, defined once

Each contract below states all eight. Definitions, so the tables stay short:

1. **Identity and version** — the stable key a consumer stores, plus the
   contract version in the message name (`...V1`), following
   `dotmac-integration`'s `domain.noun.vN` convention (`spi.py`): the version is
   part of the identity, not a header field.
2. **Idempotency key and fingerprint** — the `(scope, key)` pair and optional
   `fingerprint` passed to `dotmac_kernel.idempotency.execute_once` /
   `execute_once_platform`. Per ADR-0014, key identity is
   `(tenant_id, scope, key)`, the fingerprint is its own nullable column and
   never a reused id, and a differing fingerprint under the same key is a
   **conflict**, not a replay. Nothing is reserved before the effect.
3. **Scope** — tenant or platform, per ADR-0023. Every message carries exactly
   one, structurally: a tenant message has `tenant_id`; a platform message has
   no tenant field at all. There is no nullable tenant and no `scope_kind`
   discriminator (both refused by ADR-0023's rejected-workarounds list).
4. **Currency and amount** — `dotmac_kernel.money.Money` on the wire as
   `{ amount: decimal-string, currency: ISO-4217 }`. Never float, never a bare
   number, never an implicit currency. `Money.allocate()` is the only splitter.
5. **Source authority and provenance** — who decided the fact, its source
   system identity, source version/fingerprint, and observed/occurred time.
6. **Correction semantics** — how a wrong fact is fixed. Never by editing.
7. **Errors and retry classification** — the typed refusals, and for each
   whether the sender should retry.
8. **Compatibility** — what may change inside `V1` and what forces `V2`.

The compatibility rule is the same for all five and is stated once:
**additive-optional within a version** (a new optional field, a new member of an
open declaration registry, a widened but still-exact amount). **A new version is
required** for: removing or renaming a field, narrowing a type, changing the
meaning of an existing field, changing the idempotency key composition, changing
the amount representation, or adding a member to a closed vocabulary (coverage,
authority, effect kind). Producers may emit two versions concurrently during a
consumer's migration; consumers reject an unknown version rather than
best-effort parsing it. Version negotiation is a binding declared at the
assembly, exactly as `dotmac-integration` binds a capability version.

---

### 2.1 `AcceptRatedObligationV1` — accepted, from Subscriptions

Subscriptions publishes `RatedObligationOutputV1`; the assembly translates it
into billing's accepted command. Billing owns acceptance, tax/FX application,
receivable creation and resolution. Subscriptions owns the recurrence occurrence
and never acquires receivable or settlement state (ADR-0020 A4).

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.obligation.accept.v1`. The obligation's business identity is C10's tuple: `contract line + contract version + charge component + source fact + source fact version + period_start + period_end + currency`. Billing mints its own surrogate `obligation_id`; the tuple is the natural key. |
| **Idempotency** | scope `billing.obligation`, key = a stable digest of the C10 tuple. `fingerprint = fingerprint_of(payload)` over the rated amounts and versions — **non-`None` deliberately**, because a rerated period arriving under the same natural key must surface as `IdempotencyConflict` rather than silently replay the first amount. |
| **Scope** | tenant plane: `tenant_id` required. Platform plane: a separate command type with no tenant field, dispatched through the platform repository. |
| **Money** | pre-tax line amounts only, each exact `Money`, all in one `currency`. Mixed-currency obligations are refused (`MixedCurrencyObligation`). |
| **Provenance** | `source_system`, `source_kind` (a declared `obligation_sources` registry code per C3 — never an enum in the shared module), `source_fact_id`, `source_fact_version`, `rated_at`, `price_version_id`. |
| **Correction** | Never edited. A superseding obligation carries `supersedes_obligation_id` and is refused unless the superseded obligation is still `open` and unallocated. A resolved obligation is corrected downstream by credit note, not by re-acceptance. |
| **Errors** | `UnknownObligationSource` (no retry — a declaration is missing), `MixedCurrencyObligation` (no retry), `ObligationIdentityConflict` = ADR-0014 `IdempotencyConflict` (no retry; escalate), `AuthorityNotBound` under `external_finance` (no retry), `TenantScopeMissing` (no retry), transient DB/serialization failure (retry with backoff). Every no-retry error is a poison message the assembly parks; billing does not own the retry ladder. |
| **Compatibility** | § 2.0. Note specifically: **the C10 tuple's composition is the idempotency key, so changing it is a `V2`** — this is the field most likely to be "just extended". |

**The database constraint is the contract.** C10 is a `UNIQUE` constraint on the
tuple, composite with `tenant_id` on the tenant plane and control-plane-wide on
the platform plane. It is not a query convention, and this is the direct
correction of `billing-sources.md` § 4 item 5 (Sub dedupes on a single
`subscription_id`). Duplicate *invoices* are prevented by a second constraint —
unique `(scope, issued number series, number)` plus unique
`(scope, obligation_id)` on the invoice-line link — so a replayed billing run
cannot produce two invoices for one accepted obligation either. Both constraints
are required; the obligation one alone permits double invoicing of a single
obligation, and the invoice one alone permits two obligations for one period.

---

### 2.2 The settlement command — accepted, from the Integrator

The Integrator's payment connector plugin publishes a provider-neutral
`SettlementObservationV1`. The assembly translates it into billing's
`AcceptSettlementV1` command. **Billing never learns which PSP produced it**
(ADR-0020 A3).

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.settlement.accept.v1`. Business identity is `(source_system, source_settlement_key)` — an opaque string the connector supplies. Billing mints `settlement_id`. |
| **Idempotency** | scope `billing.settlement`, key = `f"{source_system}:{source_settlement_key}"`, `fingerprint = fingerprint_of({amount, currency, occurred_at, source_version})`. Reuse of one source key with a different fingerprint is a **conflict**, matching the cutover plan's "Settlements and allocations" rule. |
| **Scope** | as § 2.1. |
| **Money** | exact `Money`. A settlement carries exactly one currency; cross-currency allocation is refused in revision 1 (`CrossCurrencyAllocation`). |
| **Provenance** | `source_system`, `source_settlement_key`, `source_version`/fingerprint, `occurred_at`, `confirmation_evidence` (a declared code naming *how* it was confirmed — connector-verified, finance-reviewed manual confirmation, bank statement match), and `observed_at`. **Only an independently confirmed settlement creates money.** A pending checkout, an uploaded proof, an unverified provider acknowledgement and a UI click each carry an evidence code that the acceptance policy refuses. |
| **Correction** | A settled amount is **immutable**. Refund, chargeback, reversal, deallocation and reallocation append typed effects linked to the fact they offset. There is no update path, which is the corrected shape for `billing-sources.md` § 4 item 6 (Sub's editable settled `Payment.amount`). |
| **Errors** | `SettlementConflict` (no retry; a human decides), `UnknownConfirmationEvidence` (no retry), `CrossCurrencyAllocation` (no retry), `AccountNotFound` (no retry — the assembly's mapping is wrong), `AuthorityNotBound` (no retry), transient (retry). The connector's own transport retries are the Integrator's and are invisible here. |
| **Compatibility** | § 2.0, plus: `confirmation_evidence` is an **open declared registry** (ADR-0008), so a new evidence kind is additive; the *acceptance policy* naming which codes create money is a product `SettingSpec`, not a module constant. |

Allocation itself is a separate command (`billing.allocation.apply.v1`) with a
preview/apply split: the preview names exact settlement→invoice edges and
amounts and carries a fingerprint; apply locks the billing account, verifies the
preview fingerprint, and commits the posting group and the idempotency row in
**one** transaction owned by `dotmac_kernel.db` (hard rule 8). Nothing is
reserved before the effect (ADR-0014).

---

### 2.3 `ReceivablePositionV1` — produced, consumed by Collections

Collections' `ReceivablesReader` consumes this. Collections never imports
billing and never queries `mod_<billing>` tables (ADR-0024 § 2).

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.receivable.position.v1`. Identity is `(scope, source_owner, exposure_ref, billing_account_id, currency)` plus `source_version` — a monotonic per-account counter incremented by each posting group, so a consumer can discard a stale message without comparing timestamps. `exposure_ref` is opaque to Billing consumers; it does not become a cross-application foreign key. |
| **Idempotency** | scope `billing.position.read`, key = a stable digest of identity plus `source_version`, `fingerprint = None` — the producer-generated version alone identifies the snapshot. |
| **Scope** | tenant message carries `tenant_id`; platform message has no tenant field. |
| **Money** | **three separate `Money` values per currency, and no fourth field that combines them**: `collectible_receivable`, `available_credit`, `prepaid_funding`. |
| **Provenance** | `derived_from` = `"posting_groups"` under `internal`, `"projection"` under `provider_owned`; `source_authority`; `completeness`; `state_fingerprint`; `observed_at`; `posting_group_watermark`. A collections ladder refuses an incomplete or stale projection rather than inferring missing money. |
| **Service-period evidence** | `service_period_status` is exactly `not_applicable`, `verified`, or `unknown_unverified`. Start/end are required and ordered only for `verified`; absent otherwise. This is immutable source evidence needed to prevent a future-period consequence, not cadence or recurrence logic in Billing. |
| **Due-date evidence** | `due_at` plus immutable `DueDateBasisV1`: payment-term source/version, source authority, issued/effective instant and timezone, derivation policy/version, and typed override/correction evidence. Native collectible issuance requires `verified`; legacy/imported `unknown_unverified` remains reportable but is never automatically collectible. |
| **Correction** | Positions are **rebuildable projections of immutable posting groups**, never stored balances with writers. A correction is a replay: recompute from effects and re-emit at a higher `as_of_version`. A replay that produces a different value at the same watermark is a defect, and the position rebuild hash-compare (cutover plan V3/S2) is what detects it. |
| **Errors** | `Ok`, `Unavailable(retryable)`, `Unknown` and `AuthorityMismatch` are typed `ReceivablesReader` outcomes around this same snapshot, not a second contract. Event consumers additionally use `UnknownContractVersion` (no retry) and `StalePosition` (drop). A position that cannot be rebuilt is a loud reconciliation failure, never an invented balance. |
| **Compatibility** | § 2.0. **Adding a fourth money field is a `V2` and requires an accounting decision**, because the whole point of the shape is that three quantities do not collapse. |

**C9 is the reason this contract exists in this shape.** A single `balance`
field is forbidden in the shared read model, and `billing-sources.md` § 4 item 4
records the concrete defect it prevents: Sub's
`current_balance = balance_due + available_credit` adds credit to debt. There is
no representation of that sum in `ReceivablePositionV1`, so a consumer that
wants it must write the addition itself, in its own code, where a reviewer can
see it.

Coverage travels alongside and is likewise derived: `balance_due` is a generated
column (`GENERATED ALWAYS AS (total_amount - amount_paid) STORED`), the
`UNPAID | PARTIAL | PAID | OVERPAID` vocabulary is computed by one owning
function, and the dust tolerance is a `SettingSpec` read at the point coverage
is derived and applied in the query (`WHERE balance_due > :dust`) — **never a
money literal at a decision site** (ADR-0016 § 3/§ 4). `PAID` and
`PARTIALLY_PAID` appear in no lifecycle enum.

The precise form of the money-literal rule, taken from the ported
implementation rather than invented: ERP's
`app/services/finance/coverage.py` declares `PAYMENT_DUST_DEFAULT =
Decimal("0.01")` **once**, as the declared default of the
`payments.payment_dust` `SettingSpec`, and every decision site takes `dust` as a
parameter resolved from that spec. So the architecture check is not "the string
`0.01` never appears" — it is **"a money literal appears only in a
`SettingSpec` default declaration, and never in a comparison, a `CASE`, or a
service body."** Stating it as a blanket ban would fail the very
implementation being ported, which is how a guard gets weakened to nothing on
its first real contact. The sensitivity proof plants a `Decimal("0.01")` in a
comparison and asserts the scan fails, and plants one in a spec default and
asserts it passes.

---

### 2.4 `AccountingFactV1` — produced, consumed by ERP

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.accounting.fact.v1`. Identity is `(source_system, fact_id, fact_version)` where `source_system` identifies the billing installation. Immutable once emitted. |
| **Idempotency** | scope `erp.accounting.intake` on the consumer side, key = `f"{source_system}:{fact_id}:{fact_version}"`, `fingerprint = None`. ERP consumes idempotently and may replay the full stream. |
| **Scope** | as above. ERP maps scope to its own organization/entity dimension; billing does not know ERP's dimensions. |
| **Money** | exact `Money` per typed effect. Where an FX observation was used, the **immutable snapshot** travels with the fact: source/target currency, exact rate, rate type/purpose, observation identity and version, observed-at and effective-at, rounding policy, provenance. |
| **Provenance** | the fact's originating billing event (invoice issued, settlement accepted, allocation applied, credit note issued, reversal, refund), plus `occurred_at` and `committed_at`. **Emitted after billing's own transaction commits**, through `dotmac_kernel.messaging`'s outbox. There is no synchronous cross-database transaction, and a failure to deliver never rolls back a billing decision. |
| **Correction** | append-only. A wrong fact is offset by a reversing fact referencing it; `fact_version` increments only for a re-emission of the *same* fact after a producer-side defect, and ERP treats a higher version as superseding. A policy change never rewrites history. |
| **Errors** | ERP-side `UnmappedEffect` and `NoOpenFiscalPeriod` are **ERP's** refusals and are reported back as reconciliation results, not as billing retries. Billing has no fallback journal and no compensating write: an unmapped fact stays in ERP's exception queue where a human resolves it. |
| **Compatibility** | § 2.0. The typed effect vocabulary is closed within a version — a new effect kind is a `V2`, because a consumer that silently ignores an unrecognized accounting effect under-posts. |

**What billing must never contain**, enforced as an architecture test over model,
service and schema names: chart of accounts, account code/mapping, journal,
journal entry, journal line, fiscal period, period close, statutory return, tax
return, trial balance, treasury, GL reconciliation. ERP maps billing's typed
operational effects to its own accounts and periods; billing emits the effects
and enough allocation detail for ERP's cash-basis recognition, and stops.
Nigerian cash-basis VAT recognition is ERP policy and is named in the cutover
plan as explicitly out of the shared module.

---

### 2.5 `InvoiceDocumentFactV1` — produced, consumed by the rendering owner

**Ownership boundary, stated first.** Document generation is a separate
workstream with its own owner and its own contract spec
(`docs/superpowers/specs/2026-08-14-document-rendering-contracts.md`). Billing
owns and produces `InvoiceDocumentFactV1`. Billing does **not** own rendering,
template selection, presentation input, locale/currency display formatting,
PDF/HTML generation, renderer or template version provenance, or
`RenderedDocumentV1`. Three owners, one chain:

```text
billing (invoice meaning)  ──InvoiceDocumentFactV1──>  rendering owner (P8a)
rendering owner (bytes)    ──RenderedDocumentV1─────>  dotmac-files (storage)
```

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.invoice.document.fact.v1`. Identity `(scope, invoice_id, fact_version)`. `fact_version` increments only when billing's own facts change (a credit note, a correction) — **never** for a re-render, which is the rendering owner's `RenderedDocumentV1` revision and not billing's business. |
| **Idempotency** | scope `billing.document.fact`, key = `f"{invoice_id}:{fact_version}"`, `fingerprint = None` — the key is producer-generated and alone identifies the state (ADR-0014's stated correct reading). A correlation id links the fact to the rendering attempt without either side owning the other's identity. |
| **Scope** | tenant message carries `tenant_id`; platform message has no tenant field. |
| **Money** | every amount is exact `Money`, **already computed**. The renderer performs no arithmetic; if it must add two numbers to draw a row, the fact is missing a field and the fix is an additive field, not a calculation in the renderer. |
| **Provenance** | `source_authority` (the billing installation), `issued_at`, `frozen_at`, plus the applied price/source/tax-policy/FX observation versions that produced the amounts. |
| **Correction** | an issued document's snapshot is **immutable**. A correction is a credit note or a superseding document with its own identity, never a rewrite. `cancelled` is a state on the fact, not a deletion. |
| **Errors** | billing has none — its obligation ends when the fact is committed to the outbox. Rendering failures are the rendering owner's and are retried there. |
| **Compatibility** | § 2.0. A renderer needing a field billing does not emit gets it added as an optional field on request, **never** by querying billing's tables or importing billing. |

**Fields the fact carries.** This is billing's committed side of the boundary,
anticipating the rendering owner's request list. Every item below is a *billing*
fact; nothing here is a presentation decision.

| Field | Owner note |
|---|---|
| invoice/document identity and `fact_version` | billing |
| `document_number` and its series identity | allocated by billing at issuance through the bound `NumberingProvider` (P4); frozen thereafter |
| `document_state` — `issued`, `corrected`, `cancelled` | billing lifecycle only; **no `paid`, no `partially_paid`** (ADR-0016) |
| seller identity snapshot (legal name, address, registered/tax identifiers) | billing, frozen at issuance |
| customer identity snapshot (legal name, address, tax identity) | billing, frozen at issuance; an imported tax identity never silently overwrites a locally verified one |
| line snapshots — description, quantity, unit, exact unit amount, exact line total, applied price/source version | billing |
| discount snapshots — exact amounts, never a percentage the renderer must apply | billing |
| tax snapshots — treatment, jurisdiction, rate components, taxable basis, exact tax amounts, tax policy identity and version | billing, via the `TaxProvider` seam |
| totals — exact subtotal, tax total, grand total, and the FX observation snapshot if one was used | billing |
| `currency` (ISO-4217) **plus `minor_units`** | billing. **The code and its fraction digits only** — how it is *displayed* is the renderer's. `minor_units` is already on `dotmac_kernel.money.Currency`; emitting it stops rendering needing a currency table, which is P9's and does not exist (Team 4 R6). |
| `due_at` plus immutable `DueDateBasisV1` | billing; new native collectible invoices require verified basis. `unknown_unverified` is legacy/import-only, reportable but non-collectible by automation. |
| **`payment_instructions` snapshot** — bank name, account name, account number, sort code, as at issuance | billing, frozen at issuance (Team 4 R1). **Not resolved live.** Sub resolves these at render time (`billing_invoice_pdf.py:266`, `:865`), so re-rendering a two-year-old invoice prints today's bank account — a money-misdirection defect rendering cannot fix, because rendering must not read settings. |
| **brand/presentation-asset reference** — an opaque `dotmac-files` UUID, or an explicit "no asset" | billing, frozen at issuance (Team 4 R3). *Which* asset the document was issued with is a snapshot decision even though a logo is presentation. Billing holds the opaque UUID and never dereferences it — the same ADR-0022 § 2 shape as Part 5. |
| `document_kind` — `invoice`, `credit_note`, `receipt` | billing (Team 4 R4). `statement` is deferred: it spans a period, so its immutable fact is a period fact with its own producer contract (Part 6, Q3). |
| `locale` and `timezone` | billing passes through the values resolved for the customer; it performs no formatting with them |
| `document_profile_code` and its version | **see the boundary note below** |
| `scope` — a required, explicitly typed `TenantScope \| PlatformScope` | billing (adopted from Team 4 § 3.9). Never a nullable `tenant_id`: that would reintroduce all three of ADR-0023's rejected shapes at the wire. |
| `source_authority`, provenance, `correlation_id`, idempotency identity | billing. **Vocabulary divergence open** — see Part 6, D2. |

**On `document_profile_code`.** Billing carries a declared registry code
(ADR-0008) naming *which kind of document this is as a commercial fact* — tax
invoice, proforma, credit note, receipt. It does **not** carry a template id, a
template version, a layout, or a renderer selection: those are the rendering
owner's and appear on `RenderedDocumentV1`, not here. The distinction matters
because a template change must be able to re-render an old invoice unchanged,
which is impossible if billing froze the template into the invoice's own
immutable snapshot. If the rendering owner needs a template binding it is an
assembly declaration keyed on `document_profile_code`, not a billing column.

**Frozen 2026-08-17.** The field names are `document_profile_code` and
`document_profile_version`. A `template_` prefix would reintroduce at the wire
the coupling both sides removed; concrete template identity stays on the
rendering owner's contract.

**Two invariants that constrain billing specifically:**

1. **Rendering failure never rolls back the issued invoice.** Issuance commits
   the number, snapshots, receivable effect, audit row, idempotency row and the
   outbox message in one `dotmac_kernel.db` transaction; the fact leaves through
   the outbox *after* that commit. The renderer is a downstream consumer with its
   own retries. An invoice exists, is legally numbered, and is collectible
   whether or not a PDF was ever produced — and a rendering outage must not
   become a billing outage. The test is
   `test_issuance_commits_with_a_failing_renderer_bound`.
2. **A stored file never becomes the only copy of invoice truth.** The fact must
   be sufficient to re-render the document **semantically equivalent under the
   canonical semantic projection** (`DocumentProjectionV1`) at any later time
   from billing's own immutable snapshot, so the object store is a cache of
   a derivable artifact and never an authority.

   *Corrected 2026-08-14, at the Documents workstream's request.* An earlier
   draft said "byte-for-byte-equivalent". That is not achievable — PDF
   timestamps, object IDs and compression defeat it — and it is not the right
   test even where achievable, because it would fail on a layout-only change
   that alters no meaning, and a guard that fires on every stylesheet edit gets
   silenced. Determinism is asserted on the projection; byte checksums serve
   storage integrity, which is a different job, and a repair is verified against
   the projection digest rather than the checksum.

   The test is a replay:
   re-emit the fact for a historical invoice and assert field-level equality with
   the original fact — `test_a_historical_invoice_fact_replays_identically`. A
   field that cannot be replayed (anything read live rather than snapshotted) is
   a defect in this contract, not in the renderer.

**Billing does not import `dotmac-files`** (ADR-0020 A5, ADR-0022) and does not
render. `DocumentStorageProvider` and `DocumentRenderer` are explicitly **not**
billing seams — C5's original port list is superseded on this point by the
2026-08-14 revision. Import-linter contracts asserting `dotmac_billing` imports
neither `dotmac_files` nor the rendering distribution are the checks.

Credit notes follow the accepted Dotmac rule the cutover plan states:
`subtotal = total`, `tax_total = 0`, no tax lines.

**Billing does, however, own the record of which stored artifact is OFFICIAL for
a given fact version.** That is a domain statement, not a byte-lifecycle one, and
`dotmac-files` may not hold it (ADR-0022 § 2). It is the subject of **Part 5**,
including the typed command that records it, the reconciler that converges it,
and why an invoice with no artifact at all is a legal and queryable state rather
than an error.

---

## Part 3 — The PSP boundary, enforced

ADR-0024 § 6/§ 7 and ADR-0020 A3 put every provider concern in an Integrator
connector plugin. Restated as an inclusion/exclusion list, because the
architecture test needs one:

**Billing owns payment *meaning*:** payment-intent as a domain fact; acceptance
of a typed settlement observation; the accepted/rejected result; allocation,
deallocation, reallocation, reversal and refund behaviour; the derived positions
and coverage; and the `PaymentProvider` **domain port** with its fake.

**Billing contains none of these:**

| Forbidden | Detector |
|---|---|
| A provider name as an identifier, string literal, default, enum member, settings key, route path, table name, or migration name — `paystack`, `flutterwave`, `remita`, `stripe`, and any name added to the list | case-insensitive token scan over every `.py`, `.sql`, `.toml` and migration file under the package |
| A currency name as an identifier or default — `NGN`, `naira`, and the same for any other currency | same scan; ISO codes are permitted **as data supplied by a caller**, never as a module-level default or a hardcoded fallback |
| A provider SDK or HTTP client | import scan for `httpx`, `requests`, `aiohttp`, `urllib.request`, `http.client`, and any distribution not in the package's declared dependency floor |
| Provider credentials or secret material | the existing `test_publishable_packages_ship_no_secret_shape.py` marker scan, extended to the package |
| Signature verification | scan for `hmac`, `hashlib.*digest` used against a request body, `verify_signature`, `X-Signature`-shaped header names |
| A provider webhook route | scan for any route decorator in the package at all — billing ships **no** web framework import; ADR-0010's adapters live in the assembly |
| A provider retry/checkpoint/scheduling engine | scan for `backoff`, `tenacity`, `celery`, `apscheduler`, `while True` retry loops, and any `checkpoint`/`cursor`/`watermark` table in the module's declared `tables`/`platform_tables` |
| A float or an implicit currency | AST scan for `float(`, float literals in money context, and `Decimal` money without an accompanying currency |

**The sensitivity proof.** Each row above is parametrized over a planted
violation: the test writes the forbidden token into a temporary module inside the
package tree, asserts the scan **fails**, and removes it. A scan whose
allow-list, path glob or file-extension filter had drifted would pass its real
assertion vacuously and fail this one. The `| safe`-guard precedent in
`tests/architecture/test_web_conventions.py::test_the_safe_filter_guard_still_bites`
is the shape: the guard is about the next violation, not the last one, so a check
over an empty set must be proven to still bite. Additionally,
`test_the_scan_covers_every_package_file` asserts the scanned file count equals
the package's file count minus a named, commented exclusion set — so adding a
file type does not silently create an unscanned region (ADR-0018: an unmonitored
region is unmonitored, not exempt).

**Billing also imports neither sibling.** Import-linter contracts *Modules are
independent of each other* and *Modules must not import the assembly* cover
`dotmac_billing → dotmac_subscriptions`, `dotmac_billing → dotmac_collections`,
`dotmac_billing → dotmac_files`, and `dotmac_billing → app`. A companion test
asserts billing's models reference no table outside its own `mod_<short>` schema
and that no FK crosses the tenant/platform planes (ADR-0023 § 4).

---

## Part 4 — Ports, fakes and contract suites

C5's requirement, narrowed by the 2026-08-14 revision to **provider-neutral
domain ports only**. Four ports, each shipping a protocol, typed results, a
stable error taxonomy, an in-memory fake, and one parametrized contract suite
every implementation must pass. `dotmac_files.providers.StorageProvider` is the
shape to copy — a `Protocol` with a `code`, typed exceptions, no SDK import, and
a docstring that says vendor SDKs belong in adapter packages.

| Port | Owns | Fake | Contract suite proves |
|---|---|---|---|
| `PaymentProvider` | intent creation, refund request, and the typed settlement/refusal results a connector's capability message maps onto. **No transport.** | `InMemoryPaymentProvider` — deterministic ids, injectable outcomes, replay/conflict scenarios | replay returns the first result; a differing fingerprint conflicts; a refusal is typed; no method reaches a network (a socket-blocking fixture proves it, as `test_secret_sources_no_network.py` does for the settings path) |
| `TaxProvider` | rate resolution; inclusive/exclusive/exempt/reverse-charge treatment; the **immutable applied-policy snapshot** (policy identity/version, jurisdiction, treatment, rate components, taxable basis, exact tax amount, rounding policy, decision time, provenance) | `InMemoryTaxProvider` seeded from caller-supplied rules | header/line arithmetic reconciles exactly; a snapshot replays a document without consulting current policy; **no jurisdiction default exists** — an unconfigured provider raises rather than returning zero-rated |
| `FxProvider` | an observation snapshot: source/target currency, exact rate, rate type/purpose, observation identity/version, observed-at/effective-at, rounding, provenance | `InMemoryFxProvider` | a snapshot is immutable and replayable; conversion never silently crosses currencies in allocation; **no default rate source** |
| `NumberingProvider` | the P4 numbering contract billing *binds* at issuance and does not implement | `InMemoryNumberingProvider` | gapless-or-not is declared per series; a concurrent double-issue produces one number; a replayed issuance returns the same number |

`DocumentRenderer` and `DocumentStorageProvider` are **removed** from billing's
port list: rendering is P8a's own owner and storage is `dotmac-files`. Billing's
side of that boundary is `InvoiceDocumentFactV1` and nothing else.

**The developability claim, stated as a test:** a product team boots the
reference assembly with all four fakes bound, issues an invoice, accepts a
manually confirmed settlement, allocates it, and reads three positions — with
**no PSP, tax, FX, numbering or storage credential present in the environment**.
`test_the_reference_assembly_boots_with_only_fakes` asserts exactly that, and
its sensitivity proof asserts it fails when a port is left unbound rather than
falling back to a default implementation.

---

## Part 5 — The official-artifact relation

> **Decision gate.** Michael has named Team 2/Team 4 agreement on this relation,
> plus the final `InvoiceDocumentFactV1`, as a principal gate for this batch.
> This Part is Billing's position and the contract it would own. Team 4's
> matching position is in
> `docs/superpowers/specs/2026-08-14-document-rendering-contracts.md` § 5 Q2.
> **Both teams independently recommend the same owner** — see Part 6.

### 5.1 The statement that has to have an owner

> *"This stored file is the official artifact of invoice X at fact version Y."*

That is a **domain** statement, and the whole point of ADR-0022 is that
`dotmac-files` does not make domain statements. It owns opaque bytes and their
repairable physical lifecycle; its own contract says NOT "domain attachment
meaning or authorization". A `stored_files` row must never be where the fleet
learns which PDF is the official invoice, and ADR-0022 § 2 forecloses it
structurally: *"`stored_files` has no polymorphic entity columns, public flag,
domain ID, or generated public URL."*

So the relation needs a home. Three candidates, and only one survives.

| Candidate | Verdict |
|---|---|
| **`dotmac-files`** | **Refused by ADR-0022.** Officialness is domain meaning; the table has no column to hold it and must not grow one. |
| **The Documents module** | **Refused by its own design.** It is stateless by construction (Team 4 § 9), and that statelessness is the *enforcement mechanism* for the invariant that a rendering failure cannot roll back an issuance — a module with no session cannot join the issuance transaction. Giving it a durable relation destroys the guarantee that makes the whole boundary safe. It also holds *render* provenance, not *legal* identity: it does not know what `issued` means, cannot see a supersession, and has no lifecycle to hang "official" on. |
| **The assembly** | **Refused as unowned.** An assembly-local table has no module owning its migration, its tests, its drift detection or its repair. Team 4 makes the same objection independently. |
| **`dotmac-billing`** | **Owner.** ADR-0022 § 2 names the case literally: *"A ticket, **invoice**, subscriber, work order, message, or import run stores an opaque file UUID and **owns its relation**, visibility, permissions, legal hold, retention rule, and audit vocabulary."* Billing owns invoice legal identity, the document number, and `issued`/`corrected`/`cancelled` — and "official" is a predicate over exactly those. |

**On the apparent conflict with ADR-0020 A5** (*"billing emits immutable document
facts and stops"*, *"billing does not import `dotmac-files`"*): there is no
conflict, and it is worth being precise about why rather than waving at it.
A5 is about **deciding presentation** — billing does not render, does not pick a
template, does not hold bytes, does not import the storage module. Storing an
opaque UUID is none of those things; it is *precisely* the shape ADR-0022 § 2
designs, and that ADR says so in the next paragraph: *"a domain module does not
import this module merely to make a foreign key."* Billing holds a `UUID` column
with no FK and no import. It never learns a provider, a key, or a byte.

The residual risk is a documentation one, not a design one: A5's "and stops"
reads absolutely. **Recorded for Michael in Part 7 — ADR-0020 is not this
document's to edit.**

### 5.2 Where the relation lives

Billing-owned, on both declared planes (ADR-0023), with **no FK crossing them**
and no FK to `dotmac-files` in either direction:

| | tenant plane | platform plane |
|---|---|---|
| table | `document_artifacts` | `platform_document_artifacts` |
| model | `TenantDocumentArtifact` | `PlatformDocumentArtifact` |
| tenant column | `tenant_id NOT NULL`, RLS ENABLEd **and** FORCEd | absent; `REVOKE ALL` from the tenant app role, `USAGE` + row DML for the online platform role |
| current-artifact uniqueness | partial unique `(tenant_id, fact_id, media_type) WHERE superseded_at IS NULL` | partial unique `(fact_id, media_type) WHERE superseded_at IS NULL` |
| replay uniqueness | unique `(tenant_id, fact_id, media_type, file_id)` | unique `(fact_id, media_type, file_id)` |

Columns (the durable form of Team 4's R5, plus what officialness needs):

| Column | Why it is here |
|---|---|
| `fact_id`, `fact_version` | **Both.** The artifact is official for *a version*, not for a document. This is the column that stops a re-render of a superseded version becoming current (§ 5.4). |
| `document_id`, `document_number` | Denormalized from the fact so an operator can find an artifact without joining a superseded chain. |
| `media_type` | One official artifact **per media type**. A PDF and an HTML copy of the same invoice are both official *in their medium*; collapsing them would force an arbitrary winner. |
| `file_id` (`UUID`, **no FK**, opaque) | The `dotmac-files` handle. Billing never dereferences it. |
| `checksum_sha256`, `byte_length` | Bind the relation to exact bytes. A repair that produces different bytes is detectable without reading them. |
| `renderer_code`, `renderer_version`, `template_version`, `presentation_model_digest` | Team 4's R5. `presentation_model_digest` is the load-bearing one — it is what makes "the same document" checkable (§ 5.4). |
| `rendered_at`, `recorded_at` | Distinct: when the bytes were produced, and when billing accepted the relation. |
| `superseded_at`, `superseded_by_artifact_id`, `supersession_reason` | Append-only supersession. A declared registry code, never an enum (ADR-0008). |
| `withdrawn_at`, `withdrawal_reason` | For a cancelled document. **Never a delete** — see § 5.5. |
| `correlation_id`, `idempotency_key`, `request_fingerprint` | Provenance of the recording command. |

`supersession_reason` is an **open declared registry**, seeded with
`repair_missing_bytes`, `repair_corrupt_bytes`, `renderer_defect`,
`checksum_mismatch`. A product may declare another; what it may not do is
supersede with no reason.

### 5.3 `RecordDocumentArtifactV1` — accepted, from the assembly's reconciler

The typed command billing owns. All eight facets, per § 2.0.

| Facet | Specification |
|---|---|
| **Identity + version** | `billing.document.artifact.record.v1`. The **artifact** is identified by `(scope, fact_id, fact_version, media_type, file_id, checksum_sha256, byte_length, renderer_code, renderer_version, template_version, presentation_model_digest)` — the full identity Michael named. Billing mints `artifact_id`. |
| **Idempotency** | scope `billing.document.artifact`, key = `f"{fact_id}:{fact_version}:{media_type}:{checksum_sha256}"`, `fingerprint = fingerprint_of(render_provenance)` over `(renderer_code, renderer_version, template_version, presentation_model_digest, byte_length, file_id)`. **The checksum is in the key deliberately**: a retry that produced byte-identical output replays to the same `artifact_id` and cannot create a second official artifact, while a retry that produced *different* bytes is a distinct key and is evaluated by § 5.4 rather than silently replayed. A same-key attempt with a differing fingerprint — same bytes, different renderer or template — is an `IdempotencyConflict`: two renderers agreeing on bytes by accident is not evidence they agree on meaning. |
| **Scope** | required, explicitly typed `TenantScope \| PlatformScope` (`dotmac_kernel.cache`), adopted from Team 4 § 3.9 — a stronger mechanism than "structurally one or the other", and it refuses ADR-0023's three rejected shapes at the wire. |
| **Money** | **none, and that is a check.** The command carries no amount, no coverage, no lifecycle field. An architecture test asserts the command's field set intersects billing's money vocabulary at zero. |
| **Provenance** | `renderer_code`/`renderer_version`/`template_version`/`presentation_model_digest`/`rendered_at` (render provenance), `correlation_id`, and `issued_by = "reconciler"` (§ 5.6). |
| **Correction** | Never an update. Supersession appends a row and sets `superseded_at` + a declared reason on the previous current row, in **one** transaction (`dotmac_kernel.db`, hard rule 8). There is no `update_artifact` command — the guard is the absence of the API. |
| **Errors** | see the refusal table below. |
| **Compatibility** | § 2.0. Note specifically: **the idempotency key composition includes the checksum, so changing it is a `V2`** — that is the field most likely to be "simplified" by someone who thinks the checksum is redundant with `file_id`. It is not: two `file_id`s can hold identical bytes after a repair. |

**What billing rejects**, and whether the caller should retry:

| Refusal | Retry? | Why |
|---|---|---|
| `UnknownFact` — no such `(fact_id, fact_version)` | no | The reconciler is ahead of, or behind, billing's own state. |
| `FactNotIssued` — the fact exists but the document was never issued | no | A draft has no official artifact. |
| `SupersededFactVersion` — `fact_version` is not the document's current version | **no — and this is the important one** | § 5.4. Recorded as a non-current row; never current. |
| `ArtifactContentMismatch` — `presentation_model_digest` differs from the current row's for the same `fact_version` | no | Different content is a different document. § 5.4. |
| `MissingSupersessionReason` — a current row exists and no declared reason was given | no | Supersession without a reason is the drift ADR-0008 exists to stop. |
| `UnknownSupersessionReason` | no | Undeclared registry code. |
| `NotTheReconciler` — the caller is not the declared recorder | no | § 5.6. |
| `IdempotencyConflict` | no — escalate | Same key, different render provenance. |
| transient DB / serialization failure | **yes**, with backoff | The reconciler owns the ladder; billing owns no retry engine. |

### 5.4 Re-rendering, correction and supersession — the three rules

**Rule 1 — a re-render of the same fact version with the same
`presentation_model_digest` is a REPAIR, and becomes current only with a declared
reason.** Byte-identical or semantically-identical output replays to the same
`artifact_id` via the idempotency key and changes nothing. Output with a new
`file_id` (a repair after the bytes were lost) supersedes the current row **only**
when `supersession_reason` is supplied from the declared registry. A re-render
with no reason is recorded as a non-current row and the customer-delivered
artifact stays official.

**Rule 2 — a re-render whose `presentation_model_digest` DIFFERS may never
become the official artifact of that fact version.** It is refused with
`ArtifactContentMismatch`. Different content is a different document: it needs a
new fact version, or a credit note. This is the enforceable form of "an issued
document is immutable", and it is the direct correction of the defect Team 4
measured in Sub — `_is_export_fresh` (`billing_invoice_pdf.py:945-955`)
invalidates the stored PDF whenever `invoice.updated_at` moves, and
`INVOICE_PDF_TEMPLATE_REFRESHED_AT` (`:51`) invalidates every artifact rendered
before a hand-edited constant, so the stored artifact tracks the *current invoice
row* rather than the issued document. Under Rule 2 a template change cannot
redefine what was official; it can only produce a new, non-current rendering.

**Rule 3 — supersession of the FACT does not un-official the artifact of the
superseded version.** Two distinct questions, two distinct answers:

| Question | Answer |
|---|---|
| "What is the official artifact of invoice X **at fact version Y**?" | The current row for `(fact_id, Y, media_type)`. **Immutable in content, repairable in bytes.** Superseding the fact does not touch it. |
| "What is the official artifact of invoice X **now**?" | The current row for its **current** fact version. This pointer moves when billing issues a new fact version. |

The first is why `fact_version` is in the key. A superseded invoice version was
genuinely sent to a customer, and destroying the record of what they were sent
would destroy evidence. Historical rows stay current *for their own version*
forever.

**A race the reconciler will actually hit.** A render begins against fact
version N; billing issues N+1 while the renderer is working; the reconciler
arrives with an artifact for N. Billing refuses it as current with
`SupersededFactVersion` and records it as a non-current row for N. It is not
discarded — it is a true artifact of a version that was superseded — and it is
not promoted. The reconciler then finds N+1 lacking an artifact on its next pass
and renders it. **No lock, no coordination, no lost work.**

### 5.5 Absence is a first-class state, and cancellation is not a delete

This is the direct consequence of § 2.5's invariant that **rendering failure
never rolls back an issued invoice.** An invoice can be legally issued,
numbered, collectible, and have **no artifact at all**. The relation must
therefore represent that without it being an error:

- **Zero rows is legal and expected.** There is no `NOT NULL` artifact column on
  any invoice, no FK from a document to an artifact, and no lifecycle state that
  requires one. Issuance commits with zero artifact rows and always has.
- **Artifact state is DERIVED, never stored on the invoice.** A read model
  publishes `artifact_state` per `(document, media_type)`:
  `none` (no row) → `pending` (a row exists, byte availability unconfirmed) →
  `available` → `degraded` (current row, bytes reported missing or mismatched) →
  `withdrawn`. `none` and `degraded` are the reconciler's work queue, which is
  what makes the absence **queryable rather than exceptional** — the whole point
  of representing it.
- **No billing decision reads it.** Issuance, settlement acceptance, allocation,
  coverage derivation, position derivation and the receivable fact are all
  computed with zero artifact rows. *Check:* an architecture test asserts no
  module in billing's decision layer imports the artifact repository.
  *Sensitivity proof:* a planted read of the relation from the issuance path
  must fail the check — a check that only sees correct wiring proves nothing.
- **Cancellation withdraws; it never deletes.** A cancelled or voided document
  sets `withdrawn_at` + a reason on the current row and keeps it. The artifact
  is evidence of what was sent, and statutory retention is the domain's
  (ADR-0022 § 2 assigns retention and legal hold to the domain, not to files).
  Deleting the relation would also orphan the `dotmac-files` object, since
  billing's row is the only thing that knows the object is an invoice.
- **The relation survives its bytes.** `file_id` is a column, not the key. When
  `dotmac-files` reports `FileState.MISSING`, the relation is untouched and
  becomes `degraded`; the repair appends a row with a new `file_id` and
  supersedes the old one with `repair_missing_bytes`. **The link is never lost,
  because losing the object and losing the link are different events with
  different owners.**

### 5.6 The reconciler is the writer, and the event is only a wake-up

**Michael's ruling, restated as the contract's premise:** the "invoice issued"
event is a wake-up signal, not the convergence mechanism. Convergence needs a
named reconciler, and it is required before Vendor CP cutover.

**`InvoiceArtifactReconciler` is assembly-owned** (Team 4's spec § 11 shows the
same wiring). Billing agrees, and the reason is structural rather than
territorial: the reconciler must call the renderer *and* `dotmac-files` *and*
billing, and no module may import two of those three. Only the composition root
may. It writes **no module tables directly** — it reads billing's work queue,
renders, stores, and then records the relation through
`RecordDocumentArtifactV1`. Its loop:

```text
for each issued (fact_id, fact_version, media_type) whose artifact_state
    is `none` or `degraded`:
        render(fact)                          # stateless, pure
        prepare_upload + stage via dotmac-files
        verify checksum and byte length match the render result
        RecordDocumentArtifactV1 -> billing   # the ONLY write to the relation
```

Billing's side of that boundary is narrow and enforced:

- **Billing publishes the work queue**, as a read model over its own rows —
  issued fact versions whose `artifact_state` is `none` or `degraded`. This is
  what lets the reconciler converge with no event at all.
- **`issued_by` must be the declared recorder.** The assembly declares exactly
  one recorder principal at binding time; billing refuses any other with
  `NotTheReconciler`. Without this the command is an open door for a UI button
  to declare an artifact official.
- **Billing never calls the renderer or `dotmac-files`.** It has no client for
  either and imports neither (import-linter). Byte-state observations arrive as
  arguments on the command, never by billing looking.
- **Billing owns no retry engine.** The reconciler retries on transient refusals;
  every other refusal in § 5.3 is permanent and is a repair signal, not a loop.

**The required canary: suppress the event and still converge.** A test disables
the `invoice.issued` outbox delivery entirely, issues invoices, runs the
reconciler on its schedule, and asserts every issued fact version reaches
`available`. **Its sensitivity proof:** the same test with the *work queue*
deliberately returning empty must FAIL — otherwise it would pass for a system
that converges by luck or by an event that was not actually suppressed. A canary
that cannot fail is not a canary (ADR-0018).

Three further reconciler cases, each with a billing-side refusal already
specified in § 5.3: a checksum mismatch between the render result and the stored
object (`ArtifactContentMismatch` if content differs, `repair_corrupt_bytes`
supersession if only bytes do); a stale render version (recorded, not current,
unless a declared reason promotes it); and a partial failure where bytes were
stored but the relation was never recorded — which is the reconciler's *normal*
case, because the object exists, billing shows `none`, and the next pass records
it idempotently against the same checksum key.

---

## Part 6 — Agreement with the Documents workstream

Read against
`docs/superpowers/specs/2026-08-14-document-rendering-contracts.md` in full.
**Agreement on every substantive question. Two naming divergences and one
scope-vocabulary divergence, recorded rather than resolved unilaterally.**

### 6.1 Team 4's Q1–Q3

| | Question | Billing's position |
|---|---|---|
| **Q1** | Does billing accept the profile/template split? | **Accepted, and it was already this spec's position** (§ 2.5, and its open question on `document_profile_code`). Billing stamps what kind of document this legally is; rendering owns the concrete template artifact and version. The reason both specs reached it independently is the same one: freezing a template into an immutable invoice snapshot makes an old invoice un-re-renderable after a template change. **This question is closed by agreement**, and Part 7 records it as resolved rather than open. |
| **Q2** | Who owns the document→file relation? | **Agreed: `dotmac-billing`.** Both teams recommend the same owner, from the same ADR-0022 § 2 reading, and each independently rejected the assembly option as unowned. Part 5 is the contract. |
| **Q3** | Does the first slice include statements? | **Agreed: defer statements.** A statement spans many documents and a period; its immutable fact is a *period* fact with a different producer contract. Stretching `InvoiceDocumentFactV1` over a period roll-up would be the mega-contract version of the mega-module ADR-0020 rejects. |

### 6.2 Team 4's R1–R6 — billing's producer-side answers

| | Request | Answer |
|---|---|---|
| **R1** | `payment_instructions` snapshot frozen at issuance | **Accepted, and it is the highest-value item on the list.** Sub resolves bank details live at render time (`billing_invoice_pdf.py:266`, `:865`), so re-rendering a two-year-old invoice prints today's bank account — a customer-money-misdirection defect. Rendering cannot fix it because rendering must not read settings. It is billing's to freeze, and it joins § 2.5's frozen issuance snapshot. |
| **R2** | `seller`/`customer` snapshots incl. tax identity | **Accepted; already committed** in § 2.5 and in the cutover plan's "issuance freezes number, seller/buyer identity snapshots, line amounts…". Confirmation, not a new field. |
| **R3** | Brand/presentation-asset reference frozen at issuance | **Accepted**, as an opaque `dotmac-files` UUID or an explicit "no logo" — never a settings key resolved at render time. Billing holding an opaque UUID is the same ADR-0022 § 2 shape as Part 5 and is not an import. |
| **R4** | `document_kind` covering `receipt` and `statement` | **Split.** `receipt` **accepted** — billing owns confirmed settlement facts, so a receipt is a billing document. `statement` **deferred**, per Q3. First slice: `invoice`, `credit_note`, `receipt`. |
| **R5** | The relation carries render provenance | **Accepted, and owned** — the eight fields are columns in § 5.2, and they are exactly what makes the repair path in Team 4's I5 executable. |
| **R6** | `minor_units` on every amount | **Accepted, at no cost.** `dotmac_kernel.money.Currency` already carries `minor_units` (2 for NGN/USD/EUR, 0 for JPY, 3 for BHD). The wire form in § 2.0 gains it: `{ amount: decimal-string, currency: ISO-4217, minor_units: int }`. Without it rendering would need a currency table, which is P9's, which does not exist. |

### 6.3 Divergences — recorded, not resolved here

Neither team's appearance in the tree settles these. The contract matrix or
Michael does.

| # | Divergence | Billing's position and evidence | Documents' position |
|---|---|---|---|
| **D1** | Profile field name | `document_profile_code` / `document_profile_version`. A `template_`-prefixed name reintroduces at the wire exactly the template coupling both specs just agreed to remove, and billing never learns what a template is. | `template_profile_code` / `template_profile_version` (§ 3.6). **Substance is identical; only the name differs.** Trivial to reconcile, and worth reconciling before either is cited. |
| **D2** — **RESOLVED 2026-08-14** | `source_authority` vocabulary | `internal` / `provider_owned` / `external_finance` (§ 1.1). One value, one word; ADR-0020's "manual/ERP" slash reads as two options. | ~~`internal` / `provider_owned` / `manual_erp` (§ 3.7)~~. **Ruled in billing's favour (ADR-0020 § A7): `external_finance`; `manual_erp` retires.** The rendering spec's § 3.7 now cites `external_finance`. |
| **D3** — **RESOLVED 2026-08-14** | ADR-0020 A5's wording | A5's *"emits immutable document facts and stops"* reads absolutely and is the only textual basis for putting the relation anywhere but billing. Both teams read it as scoping *presentation decisions*, not knowledge of which artifact was issued. | Same reading (§ 5 Q2). Neither team could fix it, because ADR-0020 belongs to the integration owner — **who has now amended A5**: emitting the fact ends billing's part in *producing* the document, not its part in the document, and the official-artifact relation is invoice-domain meaning belonging to billing. Part 5 is no longer in tension with the ADR. |

### 6.4 What billing adopted from Team 4

Recorded so the influence is traceable rather than silent:

- **`scope` as a required, explicitly typed `TenantScope | PlatformScope`**
  (`dotmac_kernel.cache`) on every contract, replacing this spec's weaker
  "structurally one or the other". It refuses ADR-0023's three rejected shapes
  at the wire rather than by convention.
- **`presentation_model_digest` as the identity of "the same document"**, which
  is what makes § 5.4's Rule 2 checkable at all. Without it, "same document"
  would have to mean byte equality, and PDF byte equality is defeated by
  `/CreationDate`, `/ID`, xref offsets and font-subset prefixes.
- **`source_fact_fingerprint`** binding a render result to the exact input
  rather than to a version number.
- **The `sha256:<hex>` checksum form**, identical to
  `dotmac_files.PreparedFile.checksum_sha256`, so the assembly's binding check
  is a string equality and not a format negotiation.

---

## Part 7 — Open questions for Michael

These are specification choices this document takes a position on but which are
properly his to accept or overrule; each is flagged where it appears above.

1. ~~**`external_finance` as the binding name**~~ — **RULED 2026-08-14
   (ADR-0020 § A7): `external_finance`. `manual_erp` retires.** Closed, kept
   here as the record of how it was decided. This spec proposed
   `external_finance` for ADR-0020's "manual/ERP invoicing" (§ 1.1) — one value,
   one word, where the ADR's slash reads as two — while the Documents workstream
   independently proposed `manual_erp` for the same third member of
   `InvoiceDocumentFactV1.source_authority` (Part 6, D2). Two specs cannot each
   ship a different third member, and the ruling settled it in this spec's
   favour. Both contracts now cite `external_finance`.
2. **Database-level revokes as the enforcement of `external_finance` and
   `provider_owned`** (§ 1.3). This makes the profile a migration-composition
   input, not only a runtime binding — which is stronger, and also means
   changing profile is a migration.
3. ~~**`legacy_financial_writer` as a required binder argument**~~ — **REMOVED
   2026-08-17 (ADR-0020 A13).** Billing cannot verify a writer in another
   application. Adopters enforce their watermark and retirement ratchets; the
   module enforces only its one binding slot.
4. **A non-`None` fingerprint on obligation acceptance** (§ 2.1) — it makes a
   rerate a loud conflict rather than a silent replay, at the cost of requiring
   the assembly to park poison messages.
5. **Three positions and no fourth field, forever** (§ 2.3) — accepting this
   means every consumer that wants a single number writes the arithmetic itself.
6. ~~**`document_profile_code` is billing's, template identity is the rendering
   owner's**~~ — **RESOLVED 2026-08-17 (ADR-0020 A12).** The field names are
   `document_profile_code` and `document_profile_version`.

### Raised by the artifact-relation gate (Part 5)

7. **The relation's owner: `dotmac-billing`** (§ 5.1). Both teams recommend it,
   from ADR-0022 § 2's literal naming of "invoice" and its rule that a domain
   module does not import the file module merely to make a foreign key. The
   alternatives were each refused for a structural reason, not a preference:
   `dotmac-files` may not hold domain meaning, the Documents module's
   statelessness is what enforces "rendering failure never rolls back an
   issuance", and an assembly table would have no owner for its migration,
   tests, drift detection or repair. **This is the gate Michael named; it needs
   his ruling, not two agreeing teams.**
8. **ADR-0020 A5's wording needs a clarifying amendment** (Part 6, D3). *"Billing
   emits immutable document facts and stops"* reads absolutely and is the only
   textual basis for placing the relation elsewhere. Both teams read it as
   scoping *presentation decisions*. Neither team can fix it — ADR-0020 is the
   integration owner's — and without a one-line clarification Part 5 will read
   as a contradiction to every later reader.
9. **`ArtifactContentMismatch` is a hard refusal** (§ 5.4, Rule 2). A re-render
   whose `presentation_model_digest` differs may never become the official
   artifact of that fact version — it needs a new fact version or a credit note.
   This is the strictest available reading of "an issued document is immutable",
   and it means a purely cosmetic template correction cannot be applied to an
   already-issued invoice at all. That is the intended cost; it is worth Michael
   confirming he wants it, because the alternative — a "cosmetic-only" exemption
   — has no enforceable premise (ADR-0018) since nothing can verify a diff is
   cosmetic.
10. **`receipt` is in the first slice, `statement` is not** (Part 6, R4/Q3).
    Receipts follow from billing owning confirmed settlement facts. Statements
    span a period and need their own producer contract.
