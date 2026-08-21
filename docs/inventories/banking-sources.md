# Banking extraction audit — statements, matching and reconciliation

- **As of:** 2026-08-19
- **Starter:** `f7d69f7d3db6`
- **ERP:** `0f4b1698ddbf`
- **Sub:** `91c1ec477b3a`
- **CRM:** `c64b5aa0f790`
- **Backoffice:** `fcdd8270262d`
- **Vendor control plane:** `e6b2bbee815c`

This is the product-first source audit for `dotmac-banking`. It authorizes a
candidate package boundary, not publication, composition, deployment, or an
authority switch.

## Finding

ERP is the only qualifying banking implementation. Its finance/banking models,
services and tests provide statement-period/balance behavior, exact and
multi-observation matches, the rule that a suggestion is not confirmation, and
submit/approve/reject reconciliation behavior.

Sub contains two adjacent but different concerns. `payment_reconciliation.py`
reconciles operational subscriber top-ups; it is not the general cash-account
owner. `invoice_bank_details.py` publishes Sub's collection-account directory;
those collection mappings remain Sub/product policy rather than becoming the
banking module's universal bank list. CRM and Backoffice have no competing
implementation. The vendor control plane has no tenant banking data plane.

## Target owner

`dotmac-banking` owns:

- tenant-configured institutions and bank accounts, each mapped to one opaque
  cash-account reference;
- immutable, source-versioned statement headers and lines;
- immutable, source-versioned cash-account observations supplied by product or
  accounting adapters;
- configurable amount/date/reference matching policy and read-only suggestions;
- accepted match decisions and exact one-to-many allocations; and
- prepared/approved reconciliation snapshots with closing-balance difference.

It does not own provider connectivity, secrets, polling, webhook verification,
bank-specific file formats, payment meaning, subscriber collections, GL
accounts, journal entries, fiscal periods, or cash-ledger balances.

## Qualifying ERP surface

- `app/models/finance/banking/*`
- `app/services/finance/banking/*`
- `app/schemas/finance/banking.py`
- banking migrations and the focused finance/banking service tests

Behavior ports; the aggregate does not. Specifically retained are source
identity/version, line/date/amount validation, statement balancing, deterministic
suggestion scores, exact allocation totals, multi-match behavior, and separation
between preparation and approval.

## Couplings and defects not ported

1. Provider names and clients (including bank/API-specific fetchers) do not
   enter the owner. Integrator connector plugins or product adapters perform
   I/O and submit normalized statements/observations.
2. Fixed upload column maps do not enter the owner. Parsing is an adapter over
   the typed statement contract.
3. ERP GL/account/fiscal-period foreign keys do not enter the schema. The bank
   account carries one opaque `cash_account_ref`; an adopting assembly validates
   and projects it through its accounting owner.
4. A suggestion never confirms a match. Only the explicit acceptance service
   writes a decision/allocation and marks the statement line matched.
5. Reconciliation evidence is immutable after approval and cannot be a mutable
   cache of today's ledger balance.
6. Product collection-account routing remains with the product. A collection
   mapping may reference a banking account through an API-level opaque id, but
   banking does not decide which account appears on an invoice.

## Composition and cutover

The package is tenant-only and imports no assembly or sibling domain. Each
application owns its database, sessions, authorization, migration composition,
and adapter mappings. A provider observation travels through Integrator/API,
never by reading another application's database.

ERP remains the banking authority until the shared GL/accounting owner exists
and a separately authorized cutover completes backfill, read-only shadow,
zero-drift comparison, a sealed writer switch, and local-writer retirement.
Backoffice is a later clean consumer. Creating `mod_banking` moves no authority.

The implementation branch's kernel allocation is provisional because other
open alpha-train branches also target the next kernel version. Rebase and
renumber are an integration gate, not a reason to reserve or publish early.
