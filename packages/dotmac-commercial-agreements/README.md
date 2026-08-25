# dotmac-commercial-agreements

The one owner of the **durable commercial agreement** between the platform
operator and a counterparty: its identity, its immutable accepted snapshot, its
evidence-bound lifecycle, and the append-only record of every transition.

Extracted product-first from the vendor control plane's `contracts/` service
under [ADR-0057](../../docs/adr/0057-the-vendor-control-plane-composes-existing-owners.md) § 1.
Source inventory: [`vendor-cp-gap-sources.md`](../../docs/inventories/vendor-cp-gap-sources.md) § 1.
Ownership record: [`EXTRACTION.toml`](EXTRACTION.toml).

## The one rule to know

**This module is handed evidence; it never infers that something happened
elsewhere.**

There is no `approved: bool` parameter and no settable status. `approve()` takes
an `ApprovalEvidence` whose `content_digest` must equal the digest the module
froze at `propose()`. Change the terms after approval and the digests differ,
which makes the prior approval **stale rather than transferable** — ADR-0026
§ 2's binding, enforced where it is checkable.

`activate()` requires that evidence again, alongside separate `ActivationEvidence`
naming the satisfied rule and its reference. So an auditor reading one history
row can verify the activation without trusting an earlier row.

## Lifecycle

```
        ┌──────── reject ────────┐
        ▼                        │
     draft ──── propose ───► proposed ──── approve ───► approved
        │                        │                          │
     cancel                   cancel                     activate
        │                        │                          │
        ▼                        ▼                          ▼
    cancelled                cancelled                   active ◄──┐
                                                            │      │
                                                    suspend  │  reinstate
                                                            ▼      │
                                                        suspended ─┘
                                                            │
                              terminate / expire  ◄─────────┤
                                                            │
                                        terminated | expired ▼
```

`amend` supersedes any non-terminal agreement with a new `draft` version of the
same family. The predecessor becomes `superseded` and keeps every line and
history row it had — an amendment is a new version, never an edit.

## Usage

```python
from dotmac_commercial_agreements import (
    AgreementPeriod, ApprovalEvidence, ActivationEvidence,
    CommercialTerms, DraftCommand, LineInput,
    ProposeCommand, ApproveCommand, ActivateCommand,
    open_draft, propose, approve, activate, list_agreements,
)

view = open_draft(
    db,
    DraftCommand(
        command_id="cmd-1",
        reference="AGR-2026-0001",
        counterparty_ref="acme-operator",          # opaque; never resolved here
        agreement_type="oem_reseller",
        period=AgreementPeriod(date(2026, 9, 1), date(2027, 8, 31)),
        lines=(
            LineInput(
                product_code="dotmac_sub",
                capability_code="subscriber.manage",
                quantity=500,
                terms=CommercialTerms(unit_amount="12.50", currency_code="NGN"),
                release_ref="dotmac_sub@7.187.1",  # opaque; release-catalog owns it
            ),
        ),
    ),
    catalogue=my_catalogue_reader,
)

view = propose(db, ProposeCommand("cmd-2", view.id, "commercial.oem", 3), catalogue=...)
# ... the assembly opens an approval against view.content_hash and awaits it ...
view = approve(db, ApproveCommand("cmd-3", view.id, ApprovalEvidence(
    policy_code="commercial.oem", policy_version=3,
    decision_ref="apr-9f2…", content_digest=view.content_hash, decided_at=...,
)))

# expiry_date remains inclusive; this is the first date outside the term.
assert view.end_exclusive == date(2027, 9, 1)

# Enumerate the owner estate without leaking ORM rows or using moving offsets.
page = list_agreements(db, limit=100)
while page.items:
    consume(page.items)
    if page.next_after is None:
        break
    page = list_agreements(db, after=page.next_after, limit=100)
```

## Period boundary

`expiry_date` is inclusive and remains so for compatibility with a1 and the
existing lifecycle guard: an agreement ending on 31 August is live throughout
31 August. `AgreementPeriod.end_exclusive` and `AgreementView.end_exclusive`
derive 1 September for consumers using half-open ranges. The inclusive value is
the stored authority; `end_exclusive` is derived and adds no column or writer.

`date.max` can still be stored as an inclusive expiry. Since the following date
cannot be represented, deriving its exclusive boundary raises
`AgreementBoundaryError` rather than wrapping or turning a finite agreement
into an open-ended one.

## Estate inspection

`list_agreements` is the public owner read for bounded full-estate inspection.
It orders by agreement UUID, accepts the preceding page's `next_after`, probes
at most one row beyond the requested limit, and caps pages at
`MAX_AGREEMENT_PAGE_SIZE`. The result contains frozen values with their lines
already materialized; it carries no session, ORM row, or lazy loader. The
cursor is a traversal boundary, not a snapshot watermark: a backfill that races
new writes still needs an assembly-owned sealed-watermark/parity gate before
cutover.

## Composition

- **Platform plane only.** `tables=()`; the vendor control plane is the one
  consumer that exists today (ADR-0023, ADR-0057 § 7). No tenant plane is
  declared for later use.
- **Imports no sibling module.** `dotmac-approvals` and
  `dotmac-entitlement-allocation` are consumed and produced as *values* through
  the assembly (ADR-0024).
- **No foreign key leaves `mod_agreements`.** Counterparty, release, offer and
  approval-decision references are opaque and unconstrained, so an executed
  agreement outlives a superseded release, a retired policy and a merged
  counterparty record.
- **Transaction authority is the caller's.** The module only `add`s and
  `flush`es (hard rule 8).

## Published facts

`agreement.{proposed,approved,activated,amended,suspended,reinstated,terminated,expired,rejected,cancelled}.v1`
— read `PUBLISHED_EVENT_TYPES` rather than keeping a hand-written list.

The version is in the type so a `v2` can be emitted alongside `v1` during a
migration window; a consumer that never migrated keeps working instead of
silently mis-parsing.

## Status

**a1 is published and adopted by the Vendor control plane; a2 is source-only
and unreleased.** The a2 reader and derived boundary do not move authority,
change Vendor's exact pin, or claim that Vendor has adopted them. See
`EXTRACTION.toml` for the immutable a1 adoption evidence and next gate.
