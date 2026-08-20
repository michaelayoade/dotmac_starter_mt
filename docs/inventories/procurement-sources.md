# Procurement source inventory

- **Audit date:** 2026-08-19
- **Candidate:** `dotmac-procurement` — tenant-local requisition, sourcing,
  supplier-offer, award and purchase-commitment decisions

## Revision-pinned evidence

Every product fact below was read with `git show`/`git grep` at the named
revision.  The checked-out ERP, CRM, Vendor CP and Starter trees all had
unrelated local changes, so no citation relies on their working-tree content.
`dotmac_backoffice` has no configured `origin`; its clean local HEAD is recorded
as the only available revision and the failed refresh is not treated as zero
coverage.

| Repository | Revision | Procurement reading |
|---|---|---|
| `dotmac_erp` | `b969a889e8aba7255e32aa466960c22347c02fd8` | Qualifying source. Eleven `proc.*` pre-award/contract tables plus AP purchase orders/lines and production staff surfaces. |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` | Mature installation-project bidding and vendor-work decisions; not a generic procurement owner. ERP purchase orders and invoices are replaceable back-office projections. |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` | Predecessor copy of the project/vendor quotation workflow now consolidated toward Sub; not an independent reference implementation. |
| `dotmac_academy_app` | `a5e25e4e829350e503e66a03d73739529ba7da7f` | Course content about vendor procurement only; no runtime purchasing state or writer. |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` | Thin composition plan names expense/procurement as a future vertical slice; no implementation. Repository has no fetchable `origin`. |
| `dotmac_vendor_control_plane` | `2c4d88ab877aeae1c8d5aef0637bc013edf07aa9` | Vendor-platform accounts, offers and commercial contracts. Those are Dotmac-as-vendor sales contracts, not Dotmac-as-buyer procurement. No requisition/RFQ/purchase-order owner. |
| `dotmac_starter_mt` | `1c33910b2c3a20e7f75968b024970eadcf50babe` | Existing Approvals, Inventory, People, Projects, Money, idempotency and outbox boundaries; no procurement package at the audit pin. |

This inventory covers all six applications in the current fleet programme, not
only the older four-repository decomposition snapshot.  Academy's educational
content and Backoffice's intent are explicitly **not** counted as runtime
implementations.

## Qualifying ERP source

ERP is the only source with the complete generic buyer-side progression.

| Capability | Source paths | Preserved reading |
|---|---|---|
| Requisition | `app/models/procurement/purchase_requisition.py`, `purchase_requisition_line.py`; `app/services/procurement/requisition.py` | Draft-only editing; submit; optional budget verification; approval/rejection only while in flight; creator/approver segregation; caller transaction with `flush`, not commit. |
| Sourcing event | `rfq.py`, `rfq_invitation.py`; `app/services/procurement/rfq.py` | Draft-only editing; publish, invite and close progression; direct/selective/open-competitive method vocabulary. |
| Supplier offer | `quotation_response.py`, `quotation_response_line.py`; `app/services/procurement/quotation.py` | Supplier, price, delivery, validity and technical-proposal evidence received against one RFQ. |
| Evaluation/award | `bid_evaluation.py`, `bid_evaluation_score.py`; `app/services/procurement/evaluation.py` | Weighted criterion scores, selected response/supplier, completed then approved award decision. |
| Purchase commitment | `app/models/finance/ap/purchase_order.py`, `purchase_order_line.py`; `app/services/finance/ap/purchase_order.py` | Positive quantities, non-negative prices, totals derived from lines, draft-only replacement, guarded submit/approve/cancel/close, receipt projection and immutable post-approval content. |
| Adjacent only | procurement plans, vendor prequalification, procurement contracts, thresholds and `ap_integration.py` | Useful source evidence, but not all belong in the first coherent owner slice; see exclusions below. |

The executable source parity is uneven.  ERP has five focused requisition guard
tests in `tests/procurement/test_requisition_service.py` and forty purchase-order
tests in `tests/ifrs/ap/test_purchase_order_service.py`.  It has no equivalent
focused suite for RFQ publication windows, invitation uniqueness, offer
immutability or complete evaluation criteria.  Those missing guards are not
silently assumed; the Starter canaries make them explicit additions.

## Source defects that must not be ported

Product-first means porting proven behaviour, not copying every coupling or
gap.

- ERP stores `requester_id`, supplier ids, material requests, projects,
  accounts, cost centres, budgets, inventory items, invoices and journals as
  product foreign-key-shaped fields.  The module uses bounded opaque
  `*_ref`/`source_owner` values and has no product foreign keys.
- ERP's RFQ stores dates rather than timezone-aware opening/closing instants,
  allows an invitation in any state, and has no `(RFQ, supplier)` uniqueness.
  V1 uses an ordered aware window and one invitation per supplier reference.
- ERP accepts mutable quotation updates and does not freeze the offer when it
  is formally received.  V1 persists an immutable submitted snapshot and
  protects it in the database as well as the service.
- ERP's evaluation accepts arbitrary criterion names, duplicate/missing scores,
  weights that need not total 100 and a recommended response not proven to
  belong to the RFQ.  V1 fixes one criterion snapshot at publication, requires
  exact coverage and binds an award to a submitted offer from that event.
- ERP directly approves requisitions/evaluations/orders and its PO service
  renders PDFs and queues supplier email.  `dotmac-approvals` remains the sole
  generic approval owner; Procurement consumes a typed digest-bound approval
  fact and performs its own transition.  Delivery leaves through product-owned
  outbox/adapters; this module holds no template, address, provider or retry.
- `ProcurementAPIntegrationService` creates AP invoices and lines directly.
  That is a parallel finance writer and is not ported.  Payables consumes a
  typed approved-purchase fact and remains the invoice/match/payment owner.
- ERP's purchase order accepts over-receipt and compares received money with
  order money.  V1 consumes immutable line-quantity observations from the
  Inventory/service-receipt owner, refuses cumulative over-receipt, and treats
  its received quantities as a rebuildable projection rather than stock truth.

## Sub and CRM: product-work bidding, not a second generic owner

At the Sub pin, `app/models/vendor_routes.py` and
`app/services/vendor_project_records.py` own a mature project quote lifecycle:
`draft -> submitted -> under_review -> approved/rejected/revision_requested`.
Publication and award also move the authoritative installation project, while
the offer carries fibre route revisions, cable/splice attributes and work
eligibility.  `docs/SOT_RELATIONSHIP_MAP.md` names
`operations.vendor_project_records` and `operations.vendor_project_lifecycle`
as the command writers.  Tests under `tests/test_vendor_project_workspace.py`,
`tests/test_vendor_project_review.py`, `tests/test_vendor_action_eligibility.py`
and `tests/test_vendor_submission_proposals.py` prove that product boundary.

Those are real purchasing-like decisions, but copying their project/route/work
vocabulary into a shared module would transfer Sub authority by accident.  A
later Sub adoption may translate the generic offer/award portion through an
adapter and react to the resulting fact in the project owner.  Until that
cutover is separately approved and proven, Sub remains a candidate, not the
first source or a contract consumer.

CRM's `app/models/vendor.py`, `app/services/vendor.py` and
`app/services/field/vendor_quotes.py` contain the earlier near-equivalent
project quote flow.  The current fleet direction consolidates that authority
to Sub.  The CRM copy therefore supplies migration evidence, not permission to
create a third writer in this package.

## Accepted V1 ownership boundary

`dotmac-procurement` owns, per tenant:

1. a purchase requisition's immutable submitted content and its budget and
   approval fact bindings;
2. a sourcing event's method, immutable line/criterion snapshot, publication
   window and invited supplier references;
3. each formally received supplier offer and its immutable line/terms snapshot;
4. the complete evaluation score snapshot, selected offer and approved award;
5. the purchase order commitment, its exact approved content and line-level
   received-quantity projection; and
6. append-only transition/observation evidence sufficient to rebuild and
   explain those states.

It does **not** own:

- Party, employee/requester, supplier/vendor or contact identity;
- annual budget construction, funds availability or fiscal policy;
- approval policy, actor eligibility, quorum, MFA, delegation or escalation;
- procurement threshold/statutory policy (including Nigerian PPA limits);
- supplier prequalification/compliance policy or blacklisting;
- long-form procurement contract administration, bonds, retention or claims;
- catalogue/SKU, warehouse, stock movement, goods-receipt or asset state;
- supplier invoice, three-way match, tax, payment, journals or reporting;
- product projects, routes, work orders, milestones or delivery eligibility;
- numbering policy, rendering, files, notifications or provider transport.

Opaque references correlate those owners; they never become foreign keys or
inputs that let Procurement recompute another owner's decision.

## Cutover and retirement

ERP is first cutover because it is the qualifying generic source.  The adopter
must establish Organization-to-Tenant mapping, exact-pin the released kernel
and procurement package, compose `pc`, and replay requisitions, RFQs,
invitations, responses, evaluations, purchase orders and receipt projections.
Shadow comparison covers every state, line, amount/currency, criterion/score,
selected offer, approval/budget binding and cumulative received quantity.

The sealed authority switch follows ADR-0031: final comparison under source
write locks, stored typed evidence, one transaction, new owner enabled and old
writers disabled together.  ERP then deletes the procurement decision services
and PO owner path; its staff web/API remain thin adapters.  AP, Inventory and
product adapters remain, but may only consume facts or request a Procurement
transition.  A two-directional caller/table ratchet reaches zero before the
local tables are removed.

Backoffice is the first greenfield composition candidate after ERP's real
cutover.  Sub is a later, separate product-work adapter decision.  CRM first
retires its project-vendor copy into Sub and is not counted as reuse evidence.
