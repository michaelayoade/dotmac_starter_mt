# Sales authority migration and retirement ledger

**As of:** 2026-08-17
**Authority target:** `dotmac-sales` through accepted Quote
**Current authority:** Sub at
`f64946fc451ba94a1d4c8f0a61b7831367d5b598`
**Retirement source:** CRM at
`57e112f0757edcee6b9ad625ee3e13ebff5c7d71`
**Control:** ADR-0031, ADR-0033 and Sub's
`docs/audits/crm_web_retirement_ledger.json`

This ledger is a cutover plan and state record, not proof by assertion. `MET`
means the named durable evidence exists at the pinned revision. A row cannot be
advanced by a branch name, similar route, passing unit suite or old report.

## Checkpoints

| ID | Checkpoint | State | Evidence / exit condition |
| --- | --- | --- | --- |
| S0 | Clean canonical worktrees and pins | **MET** | dedicated Starter `origin/main`, Sub `origin/dev` and CRM `origin/main` branches; ERP was read by pinned Git object only; dirty roots untouched; pins above and in `sales-sources.md` |
| S1 | Sales ownership contradiction reconciled | **MET** | ADR-0033 plus Sub `SALES_TO_SERVICE_LIFECYCLE_SOT.md` amendment: Sub current owner, module target ends at acceptance, Orders separate |
| S2 | Missing CRM owner map repaired | **MET** | Sub `MARKETING_SALES_SOT.md`; campaign rows explicitly unverified, retention unresolved; architecture guard |
| S3 | Product-first source/caller/parity audit | **MET** | `sales-sources.md`, `sales-caller-inventory.md`, `sales-parity-and-canaries.md`, `sales-extraction-dossier.md` |
| S4 | P11 accepted production-lineage gate | **UNMET — EXTERNAL BLOCKER** | pinned `p11-adoption-status.md` says no product has run the kernel lineage in production. Only platform/adoption checked-in accepted evidence can advance this row |
| S5 | Module package and red-first canaries | **NOT STARTED; BLOCKED BY S4** | package manifest/namespace/lineage/EXTRACTION plus C-SALES-01..11 red, then green |
| S6 | Released module installed in Sub | **NOT STARTED** | pinned release, composed lineage, migration gate and full prescribed validation green |
| S7 | Backfill and report-only reconciliation | **NOT STARTED** | repeatable backfill; count/key/full-column typed digest equality; no source writes in report mode |
| S8 | Shadow verification | **NOT STARTED** | named window; read/command-result/money/state/handoff parity; observed drift zero or dispositioned |
| S9 | Sealed Sub authority switch | **NOT STARTED / PRODUCTION AUTHORIZATION REQUIRED** | ADR-0031 same-transaction lock→digest→effective-privilege verification→switch; mismatch rollback |
| S10 | Sub local-writer retirement | **NOT STARTED** | all callers module-backed; local writer ratchet zero; fallbacks removed; reconciliation clean |
| S11 | CRM writer/caller migration | **NOT STARTED** | all primary/secondary writer rows below redirected or removed; no dual writes |
| S12 | CRM route/traffic retirement | **NOT STARTED / PRODUCTION EVIDENCE REQUIRED** | route ledger parity/data/caller/cutover/fallback gates; healthy 30-day Loki+metrics zero traffic; route/source deletion |
| S13 | Production data retirement | **NOT AUTHORIZED** | separate retention/legal/backup approval and explicit production operation |

S4 is a stop condition, not completion. S5–S13 must remain unchanged until the
gate and their own prerequisites are actually met.

## Sub writer retirement rows

| Slice | Old writer(s) | New path | State |
| --- | --- | --- | --- |
| Pipelines/Stages | `sales.service::Pipelines/PipelineStages` | module commands | blocked by S4 |
| Staff Leads | `sales.lead_authoring` plus legacy `sales.service::Leads` | module Lead commands with Party/actor adapters | blocked by S4 |
| Captured origin | `sales.lifecycle`, `sales.capture`, `sales.lead_intake` | module origin command invoked by Sub intake adapter | blocked by S4 |
| Quote authoring/lines/discount | `sales.quote_authoring` plus legacy `sales.service` CRUD | module Quote commands | blocked by S4 |
| Quote send/status | `sales.quote_delivery` and service update paths | delivery adapter calls module lifecycle command | blocked by S4 |
| Acceptance | `sales.quote_acceptance` | module acceptance + accepted-Quote handoff; Sub downstream consumer outside transaction | blocked by S4 |
| Mirrors/out-of-domain writes | `quotes_mirror`, Inbox merge metadata writer | delete projection writer or call typed module command | blocked by S4; no Inbox behavior change authorized |

No row advances to cut over until its source writer is gated against new calls
and the complete caller inventory is verified. A dual-write phase is forbidden;
shadowing compares reads/results without creating a second authority.

## CRM writer retirement rows

| Slice | CRM writers/callers | State / required evidence |
| --- | --- | --- |
| Pipeline/Stage | CRM sales service; 10 admin settings routes; CRUD API; Kanban/stage API | assessed only; module/Sub adoption then data/caller/shadow/traffic/deletion |
| Leads | CRM sales service; 8 admin routes; CRUD API | assessed only; exact owner known, all operational gates open |
| Lead secondary writers | contacts, referrals, SERP targets, ERPNext importer | inventoried; per-caller migration/removal required |
| Campaign/Meta/Inbox Lead sources | campaigns, Meta webhook, Inbox resolve gate | **owner unverified/out of scope** for source domain; eventual call into sales cannot advance campaign/connector/Inbox retirement |
| Quotes/lines | CRM sales service; 13 admin routes; CRUD API | assessed only; split query/authoring/acceptance at exact owner |
| Portal acceptance | portal quote service/API and deposit path | inventoried; accepted-Quote contract migration plus separate downstream owners |
| Quote events/consequences | best-effort Selfcare notification and customer event handler | replace with durable handoff consumption; Orders/projects stay separate |
| SalesOrders mixed into CRM sales web | six admin routes plus downstream writers | **excluded**; owned by the orders workstream, not this ledger |

The Sub CRM web retirement ledger remains the route-level authority. Its CRM
source baseline `87f6273d040a3c3cc27213801da80ee91d278673` is an ancestor of
the CRM pin and the sales paths are unchanged. Its old Sub target is 1,461
commits behind the sales source pin and materially different, so this work does
not falsely advance the full 813-route target revision without reviewing every
route.

## Cutover evidence bundle

The eventual review bundle must contain:

1. exact released module and consumer pins;
2. migration-gate and live-catalog output;
3. backfill run identity, parameters, counts and typed digests (no PII);
4. shadow window and per-invariant results;
5. sealed-cutover transaction evidence under ADR-0031;
6. effective grant/revoke checks, including column-level privileges;
7. writer-ratchet and sensitivity-test output for Sub and CRM;
8. reconciliation report before/after with every repair owner-named;
9. rollback/fallback removal proof; and
10. route-level healthy two-source zero-traffic and deletion evidence.

Secrets, customer values and raw payloads never belong in this ledger or its
reports.
