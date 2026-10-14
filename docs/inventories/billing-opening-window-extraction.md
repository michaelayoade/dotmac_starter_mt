# Billing opening-window inventory and implementation boundary

## Inspected source and ownership — 2026-10-14

This inventory precedes the opening-window implementation. Starter source is
accepted commit `f75c69e6586732b14a0eab6b55422c71e4ddefdf`; the inspected Sub
integration is `c65237a9f06c5833ef18800db5a65b566a8bc8de`. These are source
coordinates, not production installation or database evidence.

Billing already owns immutable opening cash, evidenced historical receivable
residuals, permanent source claims, account admission, and linked inverses.
The existing inventory in `billing-opening-cash-import-extraction.md` and
`billing-opening-receivables-extraction.md` remains authoritative for those
algorithms and their ERP/Sub source disposition. This slice extends that owner;
it does not port a product repair importer or create another money ledger.

| Existing source | Qualifying behavior retained | Missing scope-wide control |
|---|---|---|
| Billing `opening_contracts.py` and `opening_validation.py` | Complete-population candidate identity, explicit source provenance, exact account/currency controls, trusted evidence-verifier seam | Preview is expressly not a seal or financial authority |
| Billing `opening_receivable_contracts.py` and `opening_receivable_validation.py` | Atomic V2 cash/AR evidence and separate residuals, original document and discharge provenance | No permanent admission-window closure |
| Billing `service.py`, V1/V2 import owners | Caller-owned transaction, exact idempotency, account serialization, pristine admission, trusted operation-specific authority | A different pristine account can still seek a new import after an earlier account imported |
| Billing `bi_0007_opening_cash.py` | Physical account-row touch protects admission against stale repeatable-read snapshots | Account serialization does not serialize scope-wide import versus seal |
| Billing `bi_0008_opening_receivables.py` | Database checks of immutable opening documents, residual backing and historical claims | Ordinary role INSERT grants do not provide an import-specific principal boundary |
| Billing full-unwind owners | Fresh inverse authority, dependent-effect refusal, linked immutable inverses, permanent claims | Operational inverse authority must remain distinct from admission closure |
| Sub V2 opening export | Read-only complete snapshot, source observations, deterministic blockers and separate lane controls | It deliberately qualifies no cash; it supplies neither opening authority nor a trusted source-key mapper |

The missing durable window and restricted admission principal are explicit
PORT-DELTAs after this inventory. An existing per-account guard, application
flag, proof-shaped JSON or migration-role credential is not a qualifying
implementation of that scope-wide contract. ADR-0031 supplies the authority
sealing requirement; M8 supplies open items plus separately evidenced opening
lanes, with closed history archived read-only.

## Candidate implementation contract

The candidate adds one permanent window per Billing scope, immutable admission
bindings and one immutable seal. A platform window is a real platform
singleton, never a sentinel tenant. An epoch identifies this window; it cannot
create a second admission namespace. Missing window means new imports refuse.

Window admission and sealing physically touch the same row before deciding.
Opening creation locks window before account. This preserves stale-snapshot
refusal at repeatable-read/serializable isolation; an advisory lock or a row
lock without a new tuple version is insufficient. No service completes the
caller's transaction or creates a Session.

The window binds the actual product-provisioned restricted database role by
name and OID. There is no guessed production role, new LOGIN credential or
reuse of the BYPASSRLS schema migrator. Ordinary tenant/platform runtime roles
must not inherit or assume the opening principal. Database admission checks
must enforce the binding even when a caller bypasses Python or copies evidence
JSON. Ordinary post-import allocation, settlement, credit, refund and inverse
operations retain their existing owners and necessary privileges.

Existing V1/V2 command and fingerprint bytes remain unchanged. An authenticated
exact retry may read its existing immutable receipt after sealing, without
posting or admitting again. New keys, accounts, receipts and opening principal
after sealing refuse. A changed payload still conflicts. Sealing neither frees
a permanent source claim nor grants authority to unwind.

Two concurrent requests can both miss the initial Kernel retry lookup before
one waits on the window. If that wait ends in a changed-target or closed-window
refusal, Billing rechecks the same Kernel identity only after its savepoint has
restored the same active caller root and enclosing savepoint, with no
rollback-required latch. A visible winner replays or conflicts by fingerprint;
an absent winner raises the original refusal without repeating financial work.
Database and serialization failures still require the caller's normal recovery.

Full unwind remains a separately verified append-only inverse. Accounting facts
V1–V5 remain distinct. No legacy financial history is replayed as new cash,
revenue or native invoice issuance. No reopening, destructive evidence deletion
or automatic reimport is introduced.

## Planned source disposition and evidence

New typed window contracts/queries and additive `bi_0009` belong to Billing.
Existing models, V1/V2 import services and their both-plane canaries extend that
same owner. Migrations `bi_0001`–`bi_0008`, existing issued facts and command
wire identities stay unchanged. Schema/table/RLS inventories and architecture
guards must move with the additive declaration.

Hosted PostgreSQL acceptance must cover cross-tenant/plane refusal, effective
restricted-role grants, raw-SQL bypass refusal, post-seal admission on a new
history-less account, exact post-seal retry, complete population comparison,
import/seal races at RC/RR/serializable isolation, whole-transaction abort, and
ordinary post-import financial lifecycle including guarded full unwind.
Fresh and predecessor migration checks are both required. Local static checks
are not runtime acceptance.

The candidate source and both-plane canaries are now implemented and have
independent static review. Hosted runtime acceptance remains required. This
document claims no publication, production role provisioning, opening import,
activation, source qualification or Sub ledger authority switch. Actual source
evidence, trusted qualification, role provisioning and cutover authorization
remain separate operational gates. Downgrade must not erase custody records or
reopen a sealed window.

Compatibility: existing original receipts are preserved by the upgrade, but
they are not silently bootstrapped into a new verified window. Their exact
authenticated retry remains readable; new imports in such a scope fail closed
without separately designed custody evidence. The predecessor canary preserves
historical rows/digests/money and proves that refusal after the real upgrade.
