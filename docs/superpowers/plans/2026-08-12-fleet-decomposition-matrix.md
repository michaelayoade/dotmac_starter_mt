# Fleet decomposition matrix

**As of:** 2026-08-12
**Starter:** `c8237bd` (`origin/main`) + branch `docs/omni-inbox-sources`
**Sub:** `9f6f9f36b` · **CRM:** `c64b5aa0` · **ERP:** `766d4c0e` · **Vendor CP:** `eb667fa`

> **SUPERSEDED 2026-08-12 — the canonical artifact now lives in Governance.**
>
> `dotmac_governance:fleet_control/fleet-decomposition.json`, with its schema at
> `agent_control/schema/fleet-decomposition.schema.json`, validator/renderer at
> `tools/fleet_matrix.py`, sensitivity-tested gate at
> `tests/test_fleet_decomposition.py`, and generated rendering at
> `docs/fleet-decomposition.md`.
>
> This draft is kept as the **reasoning record** — how the rows were derived from
> the source repositories. It is NOT a second plan. Where it disagrees with the
> Governance artifact, Governance wins.
>
> Structural corrections applied there and not here: six entity types rather than
> one flat table; immutable semantic ids (`mod.conversations`,
> `cap.inbound.observation`) with `M01`/`I1`/`A1` demoted to display codes that
> are never foreign keys; installation is per assembly-capability **binding**;
> gates belong to a **source→target transition**, so one module has several;
> facts carry **machine keys** so duplicate ownership is detectable; and
> `extracted` is **computed** from the four gates, never assigned.

**Authority:** `docs/ARCHITECTURE.md` is as-built truth, `docs/adr/` holds
decisions, `docs/inventories/` holds the dated evidence. This is intent.

## The programme

> The programme is not "modernize three applications"; it is "decompose three
> source monoliths into one composable ecosystem."

```
dotmac_erp ─┐
dotmac_crm ─┼─ vertical extraction → kernel + UI + domain modules
dotmac_sub ─┘                              ↓
                                 thin product assemblies
```

- **ERP** supplies finance, workforce, inventory, procurement, assets and
  back-office modules.
- **CRM** supplies legitimate engagement/acquisition modules. **Duplicate
  operational state is retired, not extracted.**
- **Sub** supplies authoritative ISP customer, subscriber, service, provisioning,
  network, outage, ticket and work-order modules.

Product names survive as **profiles/SKUs**, not repositories: Dotmac Backoffice
(selected business modules), Dotmac ISP (subscriber/network + shared), Dotmac
Engagement (CRM capabilities), Dotmac Academy (learning + shared people/finance).

## Two rules that govern every row

### 1. Decomposition does not automatically transfer authority

Kernel `Party`, CRM lead, Sub subscriber and finance customer remain **distinct
concepts with explicit mappings**. One module owns each fact; others receive
observations or rebuildable projections. § "Adjudication" holds the open ones.

### 2. Extraction is not "the package exists"

> A module is not "extracted" merely because its package and tables exist.
> Extraction completes only when **authority has moved deliberately**, the
> **source product consumes the module**, **parity is proven**, and the
> **previous writer is retired**.

Applied honestly, that rule says something uncomfortable:

| | package | tables | authority moved | source consumes | parity proven | old writer retired | **extracted?** |
|---|---|---|---|---|---|---|---|
| template-studio | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **no** |
| ticketing | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **no** |
| conversations (prototype) | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **no** |

**Zero modules in the fleet are extracted.** Three have packages. That gap is the
programme's actual state, and any plan that counts packages as progress is
measuring the wrong thing (ADR-0017: adoption is the scarce resource).

## The unit of decomposition

**Externally, a module owns the capability contract; internally, named services
own the individual decisions and transitions.** (An earlier draft said flatly "a
module is not an owner" — too absolute. A module *is* the external owner of what
it publishes; it is not a single internal owner, and it is not a bag of unowned
code either.)

Sub has already decomposed itself into **426 named SOT owners** across 31 domain
files; ERP has 408 tables and no owner registry; CRM's 73 web modules are
classified in its retirement ledger. A module *aggregates* those owners behind
one version, one schema, one migration lineage.

| Source | Decomposition already done | Evidence |
|---|---|---|
| Sub | **426 named SOT owners**, declared `owns`/`depends_on`/`inputs` | `app/services/sot_registry/domains/` |
| CRM | **73 web modules** classified (29 partial, 17 covered, 11 surface-gap, 9 retirement, 7 owner-gap) | `docs/audits/crm_web_retirement_ledger.json` |
| ERP | **none** — 408 tables, no kernel, no import boundary | `tenancy-characterization.md` |

Sub's rows are a *re-homing* of named owners. ERP's are a *first* decomposition.
The same words describe very different jobs.

---

## Worked decomposition: "inbound" is eight rows, not one

The 2026-08-11 inbox audit treated inbound as a single capability and prototyped
a single kernel module. **That was the monolith error at smaller scale.** The
capability decomposes as follows, and the rows have different target boundaries:

| ID | Capability | Likely target boundary | Current authority | Depends on |
|---|---|---|---|---|
| **I1** | Provider clients and signature verification | provider adapters / integration modules | Sub `integrations/connectors`, CRM `meta_webhooks` (3,282 LOC) | I2 |
| **I2** | Connected accounts and endpoint configuration | integration module **or product-owned config** | Sub `team_inbox_email_routes`/`_channel_routes`; CRM `connector_configs` + `integration_targets` | kernel secrets |
| **I3** | Idempotent admission | **existing kernel idempotency primitive** — no new owner | `dotmac_kernel.idempotency` ✔ already owned | — |
| **I4** | Authenticated receipt and normalized observation | shared ingress module **only after product-first proof** | Sub `team_inbox_observations` (only implementation; CRM has none) | I1, I3 |
| **I5** | Contact matching and routing decisions | **owning domain module** | Sub `team_inbox_contact_links` + `_routing` (~340 LOC ISP identity policy) | subscriber, workforce |
| **I6** | Conversations and messages | conversation/engagement module | Sub `team_inbox_threads` | I4, channels |
| **I7** | Ticket / work-order consequences | Sub-derived operational modules | Sub `conversation_ticket_handoff` | ticketing, workorders |
| **I8** | Replies and external delivery | delivery/outbox capability | `dotmac_kernel.messaging` + `delivery_providers` ✔ already owned | consent, channel policy |

**What this reveals that the single-module framing hid:**

- **I3 and I8 need no new owner at all.** They already exist in the kernel. A
  single `inbound` module would have re-decided at-most-once (a fourth
  idempotency implementation) and could easily have grown a second send path.
- **I5 is not shared.** Contact matching is ISP identity policy; it belongs to the
  owning domain module, not to any ingress.
- **I4 is the only genuine shared-ingress candidate**, and it has **one**
  implementation and **zero** other consumers — so under the product-first
  amendment it is not yet extractable, only characterizable.
- **I2's boundary is genuinely open**: integration module versus product-owned
  configuration. The prototype assumed kernel; that assumption is withdrawn.

**Disposition of the prototype branch:** `docs/omni-inbox-sources` stays as
**audit evidence**. Kernel remains at `0.1.0a40` — a41 is not published, no
kernel owner is declared, and the rename is not prioritized. The design findings
that survive adjudication are recorded in
`docs/inventories/inbox-sources.md` and the two `candidate` Knowledge entries.

---

## Matrix A — placement

Status uses the § 2 definition: `▣` package exists (**not** extracted) · `◈`
prototype/audit evidence · `○` proposed, dossier required · `✔` owned in kernel
today.

Namespaces are `mod_<short>`/prefix per hard rule 14. **Only three are allocated
in `MIGRATION_OWNER_LEDGER`**; the rest are proposals, allocated in the same
change as the module's manifest.

| ID | Module | Source → owner today | Namespace | Binding | Evidence |
|---|---|---|---|---|---|
| **Band 0 — foundation (the host, not modules)** |
| `K` | `dotmac-kernel` ✔ | starter | `public` | local, always | ARCHITECTURE.md |
| `U` | `dotmac-ui` ✔ | starter | none | local, always | `ui-surface-inventory.md` |
| **Band 1 — shared capability** |
| `M01` | `dotmac-template-studio` ▣ | ERP doc + Sub notification templates | `mod_tstudio`/`ts` | local | `template-studio-source-audit.md` |
| `M02` | `dotmac-ticketing` ▣ | Sub `support_tickets` (CRM copy retiring) | `mod_tkt`/`tk` | local | `ticket-sources.md` |
| `M03` | `dotmac-conversations` ◈ | Sub team-inbox (CRM copy retiring) — row **I6** | `mod_ibx`/`ib` | local | `inbox-sources.md` |
| `M04` | shared ingress ◈ | row **I4** — Sub `team_inbox_observations` only | TBD | TBD | `inbox-sources.md` |
| `M05` | integration/connectors ○ | rows **I1**, **I2** | TBD | local | — |
| **Band 2 — shared business (ERP-sourced, all unaudited)** |
| `M10` | `dotmac-ledger` ○ | ERP `finance` (subset of 134 tables) | `mod_gl`/`gl` | local | **A1** |
| `M11` | `dotmac-billing` ○ | ERP finance AR + Sub `financial_access` | `mod_bill`/`bl` | local | **A1** |
| `M12` | `dotmac-payments` ○ | ERP + Sub `payment_intents` (10 owners) | `mod_pay`/`py` | local + **remote provider** | **A1** |
| `M13` | `dotmac-procurement` ○ | ERP `procurement` (12 tables) | `mod_proc`/`pr` | local | — |
| `M14` | `dotmac-inventory` ○ | ERP `inventory` (21 tables) | `mod_inv`/`iv` | local | — |
| `M15` | `dotmac-assets` ○ | ERP `fixed_assets` (15) + `fleet` (7) | `mod_ast`/`as` | local | — |
| `M16` | `dotmac-people` ○ | ERP `people` (136 tables) | `mod_hr`/`hr` | local | **A2** |
| `M17` | `dotmac-workforce` ○ | ERP people-ops + Sub `workforce_operations` + service teams | `mod_wf`/`wf` | local | **A3** |
| `M18` | `dotmac-projects` ○ | ERP `pm` (10 tables) | `mod_prj`/`pj` | local | — |
| `M19` | `dotmac-expenses` ○ | ERP `expense` (14) + CRM expense_requests | `mod_exp`/`ex` | local | — |
| `M20` | `dotmac-forms` ○ | ERP `forms` (7 tables) | `mod_frm`/`fm` | local | **D2** |
| `M21` | `dotmac-knowledge` ○ | ERP `help` (4) + Sub help centre | `mod_kb`/`kb` | local | — |
| **Band 3 — engagement (CRM-sourced; duplicates retired, not extracted)** |
| `M30` | `dotmac-acquisition` ○ | CRM sales + Sub `sales_referrals` (19 owners) | `mod_acq`/`aq` | local | **A4** |
| `M31` | `dotmac-campaigns` ○ | CRM campaigns + Sub `comms_campaigns` | `mod_cmp`/`cm` | local | — |
| `M32` | `dotmac-widget` ○ | CRM chat widget (`crm.chat_session.v1`) | `mod_wgt`/`wg` | local | dated exception |
| `M33` | `dotmac-surveys` ○ | CRM surveys/CSAT | `mod_svy`/`sv` | local | — |
| **Band 4 — ISP vertical (Sub-sourced)** |
| `M40` | `dotmac-subscriber` ○ | Sub `customer_context` (23 owners) | `mod_sub`/`sb` | local | **A2** |
| `M41` | `dotmac-catalog` ○ | Sub `service_intent_control_plane` (11) | `mod_cat`/`ct` | local | — |
| `M42` | `dotmac-provisioning` ○ | Sub `provisioning_operations` (27) | `mod_prv`/`pv` | local | — |
| `M43` | `dotmac-network` ○ | Sub `network/*` (**75 owners**) + `geospatial` | `mod_net`/`nw` | local | **split first** |
| `M44` | `dotmac-access` ○ | Sub `network_access_control_plane` (16) + `vpn` (4) | `mod_aaa`/`aa` | local | — |
| `M45` | `dotmac-outages` ○ | Sub outages (12 owners) | `mod_otg`/`og` | local | — |
| `M46` | `dotmac-workorders` ○ | Sub work orders + dispatch + field | `mod_wo`/`wo` | local | **D5** |
| **Band 5 — other SKUs** |
| `M50` | `dotmac-learning` ○ | `dotmac_academy` | `mod_lrn`/`ln` | local | ADR-0015 |
| `M51` | licensing ▣ | starter feature + vendor CP | — host schema | see note | ADR-0007 |

---

## Matrix B — authority and gates

Owned facts are stated **only** where evidence exists. A `○` row's facts are a
sketch to be replaced by its dossier, not a decision.

| ID | Owned facts and state transitions | Assembly consumers | Depends on | Cutover / retirement gate |
|---|---|---|---|---|
| `M01` | template identity, versioning, publication, placeholder contract | all | K | vendor CP adopts; ERP + Sub retire local template tables |
| `M02` | ticket identity/number, guarded lifecycle over 9 closed statuses, assignment, comments, merge | ERP, Sub, vendor CP | K | vendor CP first (greenfield); Sub retires `TicketStatus` + 6 membership sets; CRM cutover lands **into the module** |
| `M03` | conversation identity + threading, message record, status lifecycle, dedup rule | Sub, ERP (candidate) | K, M04, channels | **no first cutover chosen**; Sub dedup widens — shadow first |
| `M04` | normalized provider observation, admission outcome | TBD | K (`idempotency`), M05 | **one implementation, zero other consumers** — characterize only |
| `M05` | connected account identity, endpoint config, credential *reference* | Sub, CRM(retiring), ERP | K (`secret_sources`) | boundary open: shared module vs product config |
| `M10` | accounts, journal entries, periods, posting rules | Backoffice, ISP | K | **A1 first** |
| `M11` | invoice lifecycle, AR balance, dunning state | Backoffice, ISP | M10, M12 | **A1 first** |
| `M12` | payment intent lifecycle, provider reconciliation, refunds | Backoffice, ISP, Engagement | K (`idempotency`, `outbox`) | **A1**; remote-provider/local-decision split |
| `M13` | requisition → PO → receipt | Backoffice | M10, M14 | — |
| `M14` | stock item identity, movements, valuation | Backoffice, ISP | K | — |
| `M15` | asset register, depreciation schedule, custody | Backoffice, ISP | M10 | — |
| `M16` | employee record, employment lifecycle, org position | Backoffice, all | K (`parties`) | **A2** |
| `M17` | team identity, membership, capability, scheduling, dispatch assignment | ISP, Backoffice | M16, K | **A3** — Sub cutover in flight, do not start |
| `M18` | project/task lifecycle, effort | Backoffice | M16 | — |
| `M19` | claim lifecycle, approval chain | Backoffice | M16, M10 | — |
| `M20` | form definition, submission record | all | K | **D2** — may already be `custom_fields` |
| `M21` | article identity, versioning, publication | all | M01? | — |
| `M30` | lead identity, qualification lifecycle, pipeline stage, conversion | Engagement, ISP | M03, K | **A4** |
| `M31` | campaign lifecycle, audience snapshot, send attribution | Engagement | K (`consent`, `delivery`, `outbox`), M01 | consent is kernel-owned — campaigns must **not** re-decide eligibility |
| `M32` | visitor session, widget config | Engagement, ISP | M03 | CRM authoritative under a dated exception |
| `M33` | survey definition, response, CSAT | Engagement, ISP | K | — |
| `M40` | subscriber identity, subscription state, suspension/termination | ISP | M41, K (`parties`) | **A2** |
| `M41` | service/product definition, tariff, service intent | ISP | K | — |
| `M42` | provisioning intent → delivery → activation | ISP | M40, M41, M43 | — |
| `M43` | fiber plant, device, ONT assignment, IP allocation, topology | ISP | K | **split into sub-modules before extracting** |
| `M44` | AAA session, RADIUS policy, VPN grant | ISP | M40, M43 | — |
| `M45` | outage lifecycle, impact set, notification trigger | ISP | M43, M03, K (`delivery`) | — |
| `M46` | work order lifecycle, scheduling, field completion | ISP | M17, M02, M40 | **D5** — M02 owns the *request*, M46 the *job* |
| `M50` | course, enrolment, progress, certification | Academy | M16, K | ADR-0015 |
| `M51` | licence envelope verification (local, offline); grant projection (decided remotely by the vendor CP, projected locally as a rebuildable cache) | all | K | **corrected**: classified a host-facility, not a stateful module — its tables live in the host lineage (a002/a003), so it cannot also claim a `mod_*` namespace |

---

## Adjudication — every overlapping fact, before target assignment

Programme step 2. Each is a **distinct concept with a mapping**, never a merge.

**Order corrected 2026-08-12** (authoritative version in the Governance artifact):

| Order | Decision | State | Why |
|---|---|---|---|
| 1 | `dec.identity.principal-mapping` (A2) | **open** | blocks Sub's atomic revision 0001 lineage adoption and the Party principal cutover |
| 2 | `dec.finance.posting-contract` (A1) | **open** | high-level disposition already settled (separate accounting and ISP billing authorities); only the invoice/AR/posting contract remains |
| 3 | `dec.inbound.connected-account-boundary` (I2) | **open** | `account_scope` has no source without it |
| 4 | `dec.inbound.shared-ingress` (I4) | **open** | needs product-first proof; one implementation, zero other consumers |
| — | `dec.workforce.service-team-destination` (A3) | **blocked** | *not open* — finish and retire the current Sub service-team cutover first |
| — | `dec.acquisition.lead-scope` (A4) | **resolved** | generic acquisition and ISP qualification recorded as **distinct capabilities** |

A5 is not one decision: I3, I5 and I8 are **resolved dispositions** (existing
kernel owners; owning domain module), and only I2 and I4 remain open.

### A1 — "Finance" is two systems that share vocabulary and almost no semantics

ERP `finance` = **134 tables** of business accounting (GL, AP, AR, tax, periods).
Sub `financial_access` = **74 SOT owners** of *subscriber* billing (prepaid
balances, FUP windows, payment intents, collections, arrears suspension).

ADR-0003 already rules **ERP and ISP remain separate data planes**. Mapping, not
merge: `M10` owns the journal; `M11` owns subscriber invoice lifecycle and
*posts* to it; neither reads the other's tables. An ISP operator is a *tenant*;
its subscribers are product-domain parties, never ERP customers.

### A2 — Four identity models, one Party

| Concept | Owner | Actually is |
|---|---|---|
| kernel `Party` | `dotmac_kernel.models` | the fleet identity anchor |
| ERP `people` (136 tables) | ERP | **employment**, not identity |
| Sub `subscriber` (23 owners) | Sub | a **service relationship** |
| CRM `person`/lead | CRM (retiring) | an **engagement record** |

A subscriber is not a Party; it *has* one. Sub already knows this —
`party.registry` is its canonical Person Party owner and `party_contact_points`
binds observed endpoints with evidence. The extraction must carry that
discipline, not flatten it.

### A3 — Service teams are mid-cutover and CRM's copy is being deleted

Sub's `service_team_lifecycle`/`_composition` cutover is **in flight**; CRM's
11-route `service_teams` is an `owner_policy_gap` row headed for retirement. A
`M17` extraction now would be a **third** destination while two are moving.
**Sequence after Sub's cutover completes.**

### A4 — Leads exist twice with different meanings

CRM's generic B2B pipeline vs Sub's ISP acquisition (12 + 4 + 2 owners) wired to
`inbox_conversation_lead_links` and service qualification. `M30` takes the
generic contract; ISP qualification stays in the vertical modules.

### A5 — Inbound (rows I1–I8)

Adjudicated above. Two rows (I3, I8) resolve to **existing kernel owners**; one
(I5) resolves to the **owning domain module**, not a shared one.

---

## "Duplicates retired, not extracted" — in row counts

CRM's ledger classification *is* the instruction:

| Classification | Count | Matrix treatment |
|---|---|---|
| `replacement_retirement` | 9 | **no row** — delete after cutover |
| `partial_capability` | 29 | **no new row** — finish the Sub cutover |
| `covered_candidate` | 17 | **no row** — already covered |
| `owner_policy_gap` | 7 | row only where genuinely CRM's (A3, A4) |
| `usable_surface_gap` | 11 | the **real** engagement source: M31–M33 |

Of CRM's 73 modules roughly **11 justify a row**; 62 are retirement work. A plan
reading CRM as a third of the extraction surface has mis-sized it by ~6×.

## Local installation versus remote capability binding

Almost every row is **local** — ADR-0003 treats plugins as trusted in-process
code installed through the supply chain. Two exceptions:

- **`M51` licensing** binds remotely to the vendor control plane; the
  request-time check stays local and explainable (ADR-0007).
- **`M12` payments**: the *provider* is remote, the *entitlement decision* is
  local. ADR-0003 is explicit that a request-time access check never calls a
  payment provider.

**No module binds remotely to another module.** Cross-module composition is
assembly wiring, an htmx fragment or a service call — never an import, and never
a network hop a request-time decision depends on.

---

## Programme sequence

1. **Build the canonical fleet matrix** across ERP, CRM and Sub. ← *this artifact,
   in draft; canonical home below*
2. **Adjudicate every overlapping fact and transition** before assigning target
   modules. A1–A5 opened; A1, A2, A3 unresolved.
3. **Establish the dependency DAG**, including kernel lineage and identity
   prerequisites. Not started — Matrix B's "depends on" is the raw input.
4. **Select one complete vertical extraction** — not a horizontal collection of
   new packages.
5. **Characterize the source implementation** and port its behaviour and tests.
6. **Cut the source monolith over** through shadow, reconciliation and rollback
   gates.
7. **Adopt it in the intended assemblies.**
8. **Delete the displaced local implementation.**

Steps 4–8 are the definition of "extracted". The fleet is at step 1, with three
packages built ahead of the sequence — a horizontal collection, which is
precisely what step 4 warns against.

## Where this artifact belongs

Canonical home: **`dotmac_governance`**, which already carries the pattern —
JSON artifacts under `.dotmac/` and `agent_control/`, JSON Schemas in
`agent_control/schema/`, validators in `tools/`, tests in `tests/`.

Proposed shape:

| Piece | Path | Purpose |
|---|---|---|
| data | `fleet_control/fleet-decomposition.json` | the matrix, **stable domain IDs** (`M01`…, `I1`…, `A1`…) |
| schema | `agent_control/schema/fleet-decomposition.schema.json` | machine-checkable: required columns, enum'd status, unique IDs, namespace uniqueness |
| renderer | `tools/render_fleet_matrix.py` | human-readable Markdown from the JSON |
| gate | `tests/test_fleet_decomposition.py` | IDs unique/stable, no two rows claim a namespace or a fact, every `EXTRACTION.toml` reference resolves |

Each repository's `EXTRACTION.toml` then gains a `fleet_row` key naming its
stable ID, and CI checks the reference resolves — so a package cannot exist
without a matrix row, and a row cannot silently change owner. That replaces
competing per-repo plans with one referenced source.

Governance is pinned by exact commit through `.dotmac/standards-profile.json`
(hard rule 15), so a matrix revision becomes a reviewable, versioned fleet event.

## Open decisions

| # | Decision | Why it blocks |
|---|---|---|
| D1 | Does ERP adopt the kernel before or during its first module extraction? | ERP has no kernel, no import boundary, no measurable RLS. Extracting into a product that cannot compose is a plan with no adopter. |
| D2 | Is `M20 forms` distinct from the starter's `custom_fields`? | May be one capability; extracting both creates a second field-definition owner on day one. |
| D3 | A1 — the finance boundary | Blocks M10–M12, the largest ERP surface. |
| D4 | One repo per module, or all in the starter's `packages/`? | Three in-repo today; 28 rows is a different question and sets release mechanics for the programme. |
| D5 | M02 ticketing vs M46 work orders boundary | Request vs job; Sub's agent workqueue coordinates both. |
| D6 | Which **one complete vertical** is step 4? | The whole sequence waits on it. Candidates: ISP subscriber+catalog+provisioning, or Backoffice inventory+procurement+assets. |

## What this artifact is not

Not a mandate to extract 28 modules. `docs/inventories/README.md`'s caution
applies at full force: **recording that two products implement the same-looking
capability does not authorise extracting a shared component.** ADR-0006 §
"The extraction rule" governs that, and most of these rows have not been through
it. The matrix's job is to make the *shape* visible and the *conflicts* explicit,
so each extraction is argued on evidence rather than assumed from a diagram.
