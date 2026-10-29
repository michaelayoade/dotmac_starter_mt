# Numbering replacement contract source inventory — 2026-10-28

This is a bounded product-first audit for extending the existing numbering
owner. It is not adoption, a release, production verification, or test evidence
from this session. No test workload or dependency installation was run locally.

## Inspected inputs

| Repository | Local source revision | Scope |
| --- | --- | --- |
| Starter | `aca35172aeafdcce816e1ecae939fb4fdfb1805c` | Existing numbering package, exports, lineage, behavioral/structural tests |
| ERP | `0e9ab87022b835ebc502624b17bf765f15c5ea4f` | Current module bridge, settings, models, cutover tests and accepted replacement plan |
| Sub | `c7707880232c9298ef89c24a1aea66d35bd3a1e6` | Numbering service, sequence model and defaults tests only; unrelated working changes were not used |

Source inspection and scoped Knowledge discovery were read-only. GitHub DNS
was unavailable. These are local Git facts, not a claim that remote main has
not moved or that a newer release cannot exist. Refresh owner before PR/release:
the numbering implementation coordinator.

Before PR submission the GitHub connector revalidated remote main at
`7a9bfbef6a24070bea97b4490d7e6074de714552`. Its only change since the Starter
input above is the independent Integration a20 allocation; the numbering source
is unchanged. The PR incorporates that main change, preserving both its lock
and publication-baseline entries. Shell Git transport remains unavailable.

## Reuse decision

Extend `packages/dotmac-numbering`, not a new module or ERP-side allocator.
Its `service.py` already owns `configure_series`, `allocate`, `preview`,
`period_for`, `format_number`, `advance_to_at_least` and kernel idempotency
composition. `models.py` already separates tenant/platform series, per-period
counters, immutable allocation receipts and repair evidence.
`migrations/versions/nu_0001_numbering.py` already owns the schema, RLS, grants,
append-only enforcement and post-history configuration freeze. Preserve its
released bytes; extend through a new revision.

Existing behavioral sources to preserve are
`tests/test_numbering_isolation.py` (PostgreSQL tenancy, configuration freeze,
locking, replay, period/counter/repair behavior and sensitivity companions),
`tests/unit/test_numbering_formatting.py`, and
`tests/architecture/test_numbering_module.py`. Their presence is a source fact;
this inventory does not claim they passed at this new branch head.

ERP's current bridge lives in
`app/services/finance/common/numbering_module.py` and
`app/services/finance/common/numbering.py`, with
`tests/ifrs/common/test_numbering_cutover.py` and
`tests/architecture/test_numbering_composition.py`. It still configures/seeds
from `NumberingSequence`, mirrors results and creates a fresh UUID replay key.
Keep this source for adapter parity and retirement inventory, not as a second
owner of the new contract. Sub's `app/services/numbering.py`,
`app/models/sequence.py` and `tests/test_numbering_defaults.py` contain mutable
legacy sequence behavior; no immutable cross-series claims/enrollment owner
was found there.

## Observed gaps and product requirements

| Requirement | Existing evidence | Extension boundary |
| --- | --- | --- |
| Immutable configuration at creation | Numbering freezes identity only after a counter/receipt; ERP refuses identity edits for cut-over types | Strict creation/enrollment mode inside existing owner; preserve unenrolled legacy behavior |
| Cross-series rendered collision protection | Current receipt unique keys include `series_code` on both planes | Exact-string claim domain across replacement codes; no ERP registry |
| Explicit partition semantics | ERP `ar.invoice` unique `(organization_id, invoice_number)`; `ar.quote` unique `(organization_id, quote_number)`; bank statement unique `(bank_account_id, statement_number)` | ERP supplies trusted domain/partition identity; module enforces declared immutable domain policy |
| Historical/external enrollment | Legacy/imported document strings live in business tables, not an immutable module claim API | Owner reserves exact strings with document/provenance identity without fabricating allocation receipts |
| Settings/state reads | `preview` returns a rendering; exported ORM models exist, but no immutable read DTO facade | Owner reads configuration and period state; last actual receipt is distinct from counter-minus-one |

ERP model references:
`app/models/finance/ar/invoice.py`, `app/models/finance/ar/quote.py`, and
`app/models/finance/banking/bank_statement.py`. INVOICE and CREDIT_NOTE share
the invoice document constraint even though their series codes differ.
A new series code therefore does not prove collision safety. The extension
must test shared-domain collisions without importing those product vocabularies
into module implementation.

No qualifying production-used cross-series claim/enrollment implementation was
found in these numbered surfaces of ERP, Sub or the existing Starter owner.
The new domain/claim seam is a focused extension of that owner driven by these
concrete constraints; allocation, formatting, counter locking, transaction
ownership and replay continue using existing code. This is not evidence of an
exhaustive search across unrelated products.

## Release identity and migration limits

ERP's checked-in lock pins `dotmac-numbering==0.1.0a2` with its wheel hash.
The local `dotmac-numbering-v0.1.0a3` tag peels to
`62ecb6b0e198ba08bef9a096037194a9dfca4da0` and carries
`ModuleReleaseTagEvidence.v1` naming verification run `39010380319` and wheel
coordinates. The prior README/changelog description of a3 as unpublished is
stale relative to that retained release evidence. No remote registry or run was
queried in this session; this inventory does not assert current installability.
The a3 source changes typed ORM construction/results; it adds no cross-series
claims, strict creation or new migration compared with a2.

The a4 extension also raises its kernel floor to `>=0.1.0a98`: the retained
a98 tag contains `dotmac_kernel/transactions.py` exporting `conflict_savepoint`,
while a97 does not. Strict create/enroll uses that engine-free public seam.
Hosted floor acceptance must prove the positive and previous-release refusal.

The replacement contract uses a new declared version, not a reissue of a3.
ERP may consume it only after exact artifact publication, hosted acceptance,
and an updated immutable dependency pin/lock. No production enrollment,
backfill, repair, namespace adoption or authority flip is authorized by a
source change or this inventory. Data faults remain manual exceptions.

## Verification and retirement

Write cross-tenant and cross-plane canaries first, then strict pre-issuance
freeze, same-domain replacement collisions, partition independence,
historical/external provenance/replay, transaction rollback, and concurrent
claim/enrollment cases. Execute tests only on hosted CI per current AGENTS.md.
Static checks are not test acceptance. Keep old caller fingerprints and
unenrolled series behavior unchanged.

Do not run two live allocators. Rehearse with pure offline replay/read-only
preview, preserve attributable disagreement independently of failing caller
transactions through the product's existing audit owner, and seal final
observation/verification/authority switch under the declared lock protocol.
The ERP binding/replacement workflow, stable business-command replay,
production continuity and all-27-series retirement are separate consumer gates;
this source inventory does not count them as completed.
