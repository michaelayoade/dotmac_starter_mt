# Presentation-layer source census (1C)

**Status:** Evidence and boundary rulings. **Not** a decision to extract anything.
**Date:** 2026-08-31.
**For:** the white-label foundation programme, U1–U3, and whatever UI component
release follows it.
**Method:** read-only census of the ten presentation categories named in the
brief, across four assemblies, at the exact commits below. No test was executed;
every "tested" claim below names a checked-in test file and function that a
reader can open, not a run.

| Repository | Commit measured | Note |
|---|---|---|
| `dotmac_sub` | `5ffdb1a945b4c50b63d787579d619b12e062e6bb` | `origin/main`. Measured from a clean worktree at `7c1d271dad54ad178fb90083305363f855622175`; every file cited below is byte-identical at both, verified blob-by-blob, so the immutable `origin/main` commit is what this dossier cites. |
| `dotmac_erp` | `7b62974b366eead1b32bead380e47d9cf10ec4c7` | Clean worktree, an ancestor of `origin/main` (`182a138559492e1874c26268cc9bcb84f51271d5` at the time of writing). |
| `dotmac_platform_control_plane` (dir still `dotmac_vendor_control_plane`) | `d2bc50179e3abace11885f883daa0c3f4007babf` | `origin/main`. Renamed from `dotmac_vendor_control_plane`; same tree. |
| `dotmac_starter_mt` | `845e0a6265075fbbc58489527c0ad34eac239287` | Reference assembly. |
| `dotmac_workspace` | `4c97dda18f6f6a8000ba2ade64f59210508b1d46` | Secondary; included because it is the one other assembly that renders HTML. |

## 0. A naming correction the brief needs

The census was commissioned against a repository called
`dotmac_platform_control_plane`. That repository **does exist** — it is the
repository this dossier censused under its former name.
`michaelayoade/dotmac_vendor_control_plane` was RENAMED to
`michaelayoade/dotmac_platform_control_plane` (repository id
`R_kgDOTofgNA`); only the local working directory still carries the old name,
because a clone directory does not follow a rename. Verified at census time
against the remote URL and the canonical name, not inferred from the directory.

An earlier draft of this section asserted that no such repository existed. That
was wrong, and the mistake is worth naming rather than silently fixing: a stale
clone directory is not evidence about a remote, and this dossier is exactly the
kind of checked-in record later work cites without re-deriving.

The correction does not move any finding below. The commit measured
(`d2bc50179e3abace11885f883daa0c3f4007babf`) is the same tree either way, so the
census covered the intended subject; only the name in the table was stale. Note
also that the GHCR package deliberately remains `dotmac_vendor_control_plane` —
the image coordinate is a frozen deployment identity, and the mismatch with the
repository name is intentional, not drift to be cleaned up.

Two distinct things carry the "platform control plane" label, and they are not
the same subject:

1. **The starter's own platform control plane** (ADR-0004) — five JSON routes
   under `/platform/*` in `dotmac_starter_mt`. `docs/inventories/starter-surfaces.md`
   § 1.2 records the decisive fact: *"The entire platform control plane is
   JSON-only. There is no `/platform/*` HTML surface, no platform login page,
   no platform template."* Two of the five are list routes.
2. **`dotmac_platform_control_plane`** (formerly `dotmac_vendor_control_plane`)
   — the vendor/product-lifecycle assembly, the closest existing standalone
   control plane.

Both were censused, and both confirm the brief's premise. Neither is a
harvestable source; see § 4.

## 1. The gate this census applies — and the misreading it must not repeat

ADR-0006 § 5, verbatim (`docs/adr/0006-white-label-product-foundation.md`):

> **Nothing is extracted into `dotmac-ui`, a shared module, or the kernel on the
> grounds that two implementations look similar.** A candidate for extraction
> must present:
>
> 1. **Two independent consumers of the same CONTRACT** — the same inputs, the
>    same semantics, the same failure behaviour. Similar markup is not a
>    contract.
> 2. **A named owner** for the extracted unit, per the Dotmac source-of-truth
>    standard.
> 3. **A migration and cutover path**: which consumer moves first, what the
>    shadow period is, and how drift is detected afterwards.
>
> Absent all three, the duplication is recorded in the inventory and left in
> place. Recording it is the deliverable; removing it is not.

**Point 1, read as a prerequisite, is superseded** — by the same ADR's
*"Decision amendment — 2026-08-12 (a second consumer is evidence, not
permission)"*:

> A second consumer proves reuse and constrains generalisation; it does **not**
> determine whether a coherent capability belongs in a module.

The amendment's own stated cause is that a two-consumer prerequisite *"does not
delay those modules; it **forbids** them."* So the reading this census must not
fall into — and which is easy to fall into, because § 5's original text is still
the more quotable paragraph — is **"only one product does this, therefore we
cannot extract."** That is not a valid reason. What survives § 5 untouched is
its first sentence: **similar markup is not a contract.** That sentence does
almost all the work below.

The three dossier states, which are the vocabulary this census uses:

| State | Means | Requires |
|---|---|---|
| `audit-complete` | The unit was drawn deliberately; nothing has adopted it. | An audited `source_mode`, **≥ 1 concrete** candidate consumer, **0** contract consumers |
| `adopted` | One real consumer is on the contract; first cutover complete. | **Exactly 1** contract consumer |
| `reuse-proven` | Two or more independent consumers exercise the same contract. | **≥ 2** contract consumers |

A *concrete* candidate is "an assembly that exists and will consume it." All
three states permit a shared module; the state records evidence, never
permission. And a decision to wait must stand on **readiness** grounds. The
sanctioned four, which this dossier uses by name:

1. the contract is **proposed, not ratified** — a release freezes an unagreed shape;
2. the **boundaries are unproven** — no second implementation has pushed on the seams;
3. **enforcement is missing** — a package nothing checks drifts on the first commit;
4. **defect remediation comes first** — extracting defective code exports the defects and gives them a version number.

Four further constraints, from the brief, each load-bearing:

- **`dotmac-ui` may receive only inert markup, semantic classes, accessibility
  behaviour and typed display parameters.** Query construction, permissions,
  cursors, counts, row projections and action eligibility stay with the owning
  service. They are not candidates, at any evidence level.
- **No universal "render any ORM/table" component.** A component that can render
  anything knows about everything.
- **A product or provider mode flag inside a shared component is a refusal, not
  a design option.**
- **A pin is installation, not adoption.** Per
  `tests/architecture/adoption_evidence.py`, an adoption claim needs a row
  proving composition or cutover at an immutable commit; `pinned_at` alone is
  declared debt, not adoption.

## 2. The headline finding

**Sub and ERP each run a large, mature, production presentation macro library.
They are not two consumers of one contract. They are two contracts.**

| | `dotmac_sub` | `dotmac_erp` |
|---|---|---|
| Library | `templates/components/ui/macros.html` (106 KB, ~90 macros) + `templates/components/ui/list_macros.html` | `templates/components/macros.html` (40 macros) |
| Importing templates | 332 | 739 |
| Status badge input | a server-owned `StatusPresentation` value object (`label`, `tone`, `icon`) | a raw domain status string, mapped inside the macro by a 79-entry hardcoded ERP vocabulary table |
| Sort header | a deep-linkable server-side sort URL derived from a typed `ListQuery` | a `data-sort-column` attribute for client-side JS sorting, no href |
| Pagination input | typed `ListQuery` + `PageMeta`; no caller assembles a query string | raw `page`/`total_pages`/`total_count`/`limit`/`search`/`filters` dict; the macro assembles the query string itself |
| Bulk actions | a server-projected `BulkActionContract` with permission filtering, membership scope tokens and preview requirements | a caller-supplied list of `{name,label,endpoint,class,confirm}` dicts, with no eligibility or permission concept |
| Token posture | role-named `status-tone-*` custom properties in the live `static/css/design-system.css` | value-named classes (`badge-paid`, `badge-overdue`) with raw hex in the live `src/css/components/_badges.css` |

Same nouns, different inputs, different semantics, different failure behaviour.
That is the sentence of § 5 the 2026-08-12 amendment left standing: **similar
markup is not a contract.** The refusals below rest on that, and on readiness —
never on "only one product does this."

The consequence is specific and worth stating before the detail: a shared
component drawn from both of these would have to be parameterised over the
difference, and the difference *is* the contract. That is the mode flag the
brief names as a refusal.

There is one further trap, already documented and easy to fall into again:
`docs/inventories/ui-surface-inventory.md` § "Correction: only ERP runs its copy"
established on 2026-08-11 that **Sub's `src/css/` tree is dead code** and ERP's
identical-looking tree is live. Re-verified at this census's commits: Sub's
`src/css` was last touched 2026-02-16 (`3d795f1af176dbaaf1c014cf9f353899854c028d`),
ERP's on 2026-07-24 (`959ed67c1cb3efbe8d03820a21be4038a90f2355`). So
`_tables.css`, `_badges.css`, `_dashboard.css`, `_empty-states.css`,
`_loading.css`, `_bulk-selection.css`, `_responsive.css` and `_touch.css` look
like two-product evidence and are **one live consumer plus one fossil**. Do not
count them twice.

## 2a. This is a re-measurement, not a discovery

Before recording anything as new, the honest statement: **most of these
boundaries were already drawn, implemented, and deliberately parked.** Knowledge
carries them, and this census corroborates rather than discovers them.

| Recorded candidate | Where it lives | State |
|---|---|---|
| `dotmac_kernel.listing` — `ListFieldDefinition`/`ListDefinition`/`ListQuery`/`PageMeta`, ported from Sub with the generic half of its parity tests | branch `feat/kernel-listing-contract` @ `90438e8`, kernel `0.1.0a43` | implemented, **never merged** |
| `dotmac-ui.components.LIST_SURFACE` — token-native inert table/filter/sort/pagination, display-only values, **at most one pre-eligible row action** | branch `feat/dotmac-ui-complete` | implemented, never merged |
| `dotmac_kernel.ui_projection` — transport-neutral status, state/freshness, KPI cohort and backend-decided action values | same branch | implemented, never merged |
| `RECENT_ACTIVITY` — markup/CSS plus a display-only `ActivityItem` | same branch | audit-complete, unadopted |
| `CATALOG_GRID` (UI-08) | — | authorized, **never committed**; rebuild required |

None of `packages/dotmac-kernel/src/dotmac_kernel/listing.py`,
`.../ui_projection.py`,
`packages/dotmac-ui/.../components/list_surface.html` or
`docs/inventories/ui-components-2026-08-19.md` exists on the starter's default
branch today; `feat/dotmac-ui-complete` and `salvage/wip-dotmac-ui-complete` do.

**The live blocker on the kernel half is ADR-0017, not ADR-0006.** The recorded
ruling: `dotmac_kernel.listing` does not qualify for ADR-0017's demand-pulled
exception because *no independently existing product is blocked today* — Sub
runs its local implementation happily, ERP has not adopted the kernel on its
default branch, and the programme would create the consumers used to justify
itself. That ruling is about **readiness and demand**, and it is untouched by
the second-consumer amendment.

One artefact of that parked work showed up in this census as an unexplained
oddity, and now has an explanation: the starter's `apply_ordering` is present in
`dotmac_kernel/query.py`, has zero callers, and is missing from the module's
`__all__`. The parked branch moved `apply_pagination`, `apply_ordering` and
`escape_like` **out of** `query` and into `listing`, precisely so the list
surface would not arrive with two owners, leaving `query` as a behaviourless
re-export shim. That reconciliation was designed and never landed. The orphan in
`query.py` is its shadow.

**What this census adds** to the parked record: measurements at fresh commits
(2026-08-25 to 2026-08-31 rather than 2026-08-13/19); a first characterisation
of ERP's presentation contracts as *contracts* rather than as CSS; the vendor
control plane, which no earlier inventory covered; the badge call-site split
inside Sub; and a set of dead-shared-code findings that change how adoption
should be counted in future passes.

## 3. Verdicts at a glance

`PRODUCT-LOCAL` = the implementation stays where it is and the duplication is
recorded. `TRACKED` = already drawn as a parked candidate; do not re-open it as
new work. `CANDIDATE` = a new unit worth an owner decision, `audit-complete`,
unadopted. No verdict below rests on how many products do the thing.

| # | Category | Mature source? | Verdict | Why it does not move |
|---|---|---|---|---|
| 1 | Bounded list/page requests | Sub (`ListDefinition`/`ListQuery`) | **TRACKED** — `dotmac_kernel.listing` | ADR-0017: no product blocked today |
| 2 | Cursor pagination | none, in any presentation surface | **PRODUCT-LOCAL — unresolved** | nothing exists to extract |
| 3 | Offset pagination | Sub (`PageMeta` + `list_pagination`); ERP (`pagination`) | **TRACKED** — `LIST_SURFACE` | depends on the barred kernel half |
| 4 | Semantic tables | Sub and ERP, two contracts | **PRODUCT-LOCAL — refused** | similar markup is not a contract |
| 5 | Sorting / filtering | Sub and ERP, two contracts | **PRODUCT-LOCAL — refused** | the two disagree on what a header *does* |
| 6 | Empty / loading / error / permission states | empty-state already `reuse-proven` | **PARTLY DONE; rest PRODUCT-LOCAL** | three unrelated approaches to loading; error pages carry product copy |
| 7 | Summary / stat cards | Sub and ERP, most-drifted file in the fleet | **PRODUCT-LOCAL — refused** | value-named colour parameters; 439 differing lines |
| 8 | Status badges | Sub's tone contract | **CANDIDATE (`status-badge`), `audit-complete`** | readiness grounds 2 and 4 — see § 6.4 |
| 9 | Timeline / event history | no shared contract in scope | **PRODUCT-LOCAL — unresolved** | three event shapes; `RECENT_ACTIVITY` already tracked out of scope |
| 10 | Bulk eligibility, row actions, responsive | eligibility is never extractable | **PRODUCT-LOCAL — refused** | the decision, not the evidence, is what is being placed |

**One new candidate, two categories already tracked, seven product-local.** The
seven are the deliverable — ADR-0006 § 5's closing sentence is that recording
the duplication *is* the output, and removing it is not.

## 4. Sources examined

| Product | Surface | What it is |
|---|---|---|
| Sub | `app/services/list_query.py` (323 lines) | `ListFieldDefinition`/`ListDefinition`/`ListQuery`/`PageMeta` — a typed, stdlib-only, ORM-free list-request contract. 30 `ListDefinition(...)` declarations across 20+ owner services; 25 importing modules. |
| Sub | `app/services/bulk_actions.py` (248 lines) | `BulkActionDefinition`/`BulkResourceDefinition`/`BulkActionContract` + `membership_scope_token()` + `parse_bulk_selection()`. Registered in Sub's SOT registry as `sot_registry/domains/ui_bulk_actions.py`. 4 declaring owners, 7 consuming templates. |
| Sub | `app/schemas/status_presentation.py` + `app/services/status_presentation.py` (1,344 lines) | `StatusTone` (5) / `StatusIcon` (7) / `StatusPresentation` and ~50 domain mapping functions. |
| Sub | `app/services/ui_contracts.py` | `StateValue`, `ChartProjection`, `Kpi` (whose `cohort_url` must be app-relative — the "KPI-parity rule": headline number and drill-down come from one filtered query, enforced by construction). |
| Sub | `templates/components/ui/macros.html` (106 KB, ~90 macros) | The main library: `stats_card`, `status_badge`, `status_presentation_badge`, `data_table`, `table_head`, `table_row`, `row_actions`, `row_action`, `empty_state`, `pagination`, `timeline_item`, `search_input`, `filter_select`, `filter_bar`, `progress_bar`, `connection_status`. Imported by **331** templates. |
| Sub | `templates/components/ui/list_macros.html` | `sort_header` and `list_pagination` — the `list_query` migration's macros. **10** consuming templates for `list_pagination`; **5** for `sort_header` (12 call sites). |
| Sub | `templates/components/data/{data_grid,table_pagination,empty_state,recent_activity_panel}.html` | A third, parallel table/pagination/empty-state family. `empty_state.html` is a compatibility shim over the published `dotmac_ui` macro. |
| Sub | `static/css/design-system.css` (353 lines, live) | `.status-tone-{positive,info,warning,negative,neutral}` setting `--status-{surface,border,foreground,indicator}`. **This file is where `dotmac-ui`'s role vocabulary came from** (`packages/dotmac-ui/COMPATIBILITY.md` § "Where the vocabulary came from"). |
| ERP | `templates/components/macros.html` (40 macros) | `status_badge` (~390 call sites across 264 files), `empty_state` (~355), `compact_filters` (187), `pagination` (182), `stats_card` (177), `sortable_th` (50), `action_buttons`, `bulk_select_header`/`bulk_select_cell`/`bulk_action_bar` (12 each), `data_table` (6), `detail_error_state` (0), `search_filter_bar` (0), `pivot_table`. Imported by **739** templates. |
| ERP | `src/css/` (live tree, 23 files) | `_badges.css`, `_tables.css`, `_dashboard.css`, `_bulk-selection.css`, `_empty-states.css`, `_loading.css`, `_responsive.css`, `_touch.css`. Value-named classes with raw hex. |
| Starter | `packages/dotmac-kernel/.../templates/components/table_macros.html` | `icon`, `table_header`, `action_buttons`, `pagination`, `status_badge`. |
| Starter | `packages/dotmac-kernel/.../query.py` | `apply_pagination`, `escape_like`, `apply_ordering`. |
| Starter | `packages/dotmac-ui/` | `EMPTY_STATE` and `MAP_FRAME` contracts; 190 role-named tokens including the five `--dmui-status-<intent>-{surface,border,foreground,indicator}` sets. |
| Vendor CP | `src/vendor_cp/console/web.py:26-44` | A single hardcoded `<!doctype html>` placeholder string. No `templates/`, no `static/`, no Jinja import anywhere in `src/`. |
| Workspace | `src/.../operator/web.py` | Two `<table>` blocks hand-built as Python f-strings; no `<caption>`, no `scope="col"`; a reused `_empty_state()` helper over the published `.dmui-empty-state` classes. |

### The starter is not a source, and the control plane is not a source

Two negatives are as load-bearing as the positives.

**The starter's `table_macros.html` is not production-proven.** Its
`status_badge` has **1** call site (`admin/custom_fields/_table.html:51`); its
`action_buttons` has **2**; its `pagination()` macro has **zero** — every screen
that needs page controls (`_roles_table.html`, `_table.html`,
`_audit_table.html`) hand-rolled an inline duplicate of that macro's markup
instead of calling it. `apply_ordering` in `dotmac_kernel/query.py` likewise has
zero call sites and is absent from the module's `__all__`. No test anywhere
asserts `status_badge`, `action_buttons` or `pagination` markup. Two
independently written copies of "ceiling division floored at 1" compute
`total_pages` (`rbac/web.py:82` uses `-(-total // SIZE)`, `parties/web.py:152`
uses `math.ceil`). A contract that the assembly which authored it does not use
is not a source; it is a proposal nobody accepted.

**The vendor control plane confirms the brief's premise exactly.** Six JSON list
endpoints, every one an unbounded `select(...)` with a hardcoded `order_by`, no
client-controllable sort or filter, no page-size cap, no cursor, no offset, no
total. Five of the six have zero HTTP-level test coverage; the sixth
(`GET /platform/vendor/licences/targets`) has one test asserting a single-row
list. The one page-size-bounded reader in the repo
(`src/vendor_cp/contracts/adapter.py:545-562`, cursor-based) is never reachable
from a router — its only caller is a backfill walker. There is nothing here to
harvest. These endpoints are a statement of what a console will one day need to
list. **They are requirements. Do not read them as a reference implementation.**

## 5. Category findings and verdicts

### 1 — Bounded list/page requests: PRODUCT-LOCAL, unresolved

Sub's `ListDefinition` is the only real contract in the fleet. It declares
`per_page_options`, rejects an undeclared page size, an undeclared sort key and
an undeclared filter with a named `ValueError`, and it imports nothing but
`dataclasses`, `typing` and `urllib.parse`. It is covered by
`tests/test_list_query_contract.py` (13 functions, including
`test_list_definition_rejects_undeclared_query_state` and
`test_customer_query_defaults_stale_page_size_values`) and behaviourally by
`tests/test_network_ui_standards.py`.

Nothing else in the fleet is comparable. ERP bounds nothing centrally — the
clamps that exist (`min(limit, 100)` in `app/api/sync/dotmac_crm.py`,
`min(limit, 1000)` in `app/services/inventory/wac_valuation.py`) are inline
per-call-site literals. The starter has three unrelated variants: a fixed
`PAGE_SIZE = 20` module constant for web routes, `Query(..., ge=0, le=MAX_*)` in
JSON routers, and a silent in-service `max(1, min(limit, MAX_PAGE_SIZE))` clamp
in `tenants/service.py` — and no test exercises the `le=` rejection boundary.
Vendor CP bounds nothing at all.

**Verdict: keep it in Sub, and the reason is not the consumer count.** This is
a *request* contract, not display, so `dotmac-ui` is the wrong home at any
evidence level; its home is `dotmac_kernel.listing`, which **already exists**,
already ported it together with the generic half of
`tests/test_list_query_contract.py`, and is parked on
`feat/kernel-listing-contract` @ `90438e8`.

It is blocked by **ADR-0017**: no independently existing product is blocked
today on the absence of the shared facility. Sub runs its local copy; ERP has
not adopted the kernel on its default branch; the programme would supply its own
consumers, which is the exact shape ADR-0017's demand-pulled exception refuses.
That is a demand ruling, not a similarity ruling, and this census does not
disturb it. **Unresolved, and already tracked — do not re-open it as new
work.**

### 2 — Cursor pagination: PRODUCT-LOCAL, unresolved

**No presentation-layer cursor pagination exists anywhere in the fleet.** Every
cursor found is a backend drain checkpoint with no template on the other end:
Sub's `ExportCursor` (`app/migration_source/snapshot.py:203`,
`app/services/migration_source_export.py:713-793`), its prepaid-sweep and
ERP-domain-sync keyset cursors, vendor CP's `ContractPage`/`next_after`
(`src/vendor_cp/contracts/adapter.py:235,545`, reachable only from a backfill
walker), and the starter's connector polling cursors. Grepping Sub's templates
for `cursor` returns only `cursor-pointer` and `cursor-not-allowed`.

**Verdict: nothing to extract, because nothing exists.** The first product that
needs a cursor-paginated screen builds it locally. **Unresolved.**

### 3 — Offset pagination: PRODUCT-LOCAL, unresolved

Five independent implementations, three of them inside Sub alone:

- Sub `list_pagination` (`list_macros.html`) driven by `ListQuery` + `PageMeta`;
  no caller assembles a query string. 10 templates. Tested for real
  (`tests/test_list_macros.py::test_list_pagination_preserves_query_state_and_announces_results`
  asserts `role="status" aria-live="polite"`, the literal "Showing 26 to 50 of
  80 referrals", and that filter/sort/per-page hidden inputs echo back).
- Sub `data_grid.html`'s built-in pagination (8 callers), on an explicit
  allowlist in `tests/architecture/test_ui_no_template_derived_totals.py` — a
  recognised *second* authority for the same math.
- Sub `table_pagination.html` (`offset`/`limit`, 2 callers) plus a one-off
  `_pagination.html` and a file-local `radius_card_pagination` macro. No tests.
- ERP `pagination(page, total_pages, total_count, limit, search, filters, ...)`
  — **182 call sites**, and the macro assembles its own query string from a raw
  `filters` dict. It is genuinely tested
  (`tests/test_pagination_macro.py::test_pagination_links_preserve_limit_on_navigation`,
  `tests/test_template_macros.py::test_pagination_preserves_search_and_filter_query_params`,
  `::test_pagination_renders_nothing_for_empty_results`).
- The starter's `pagination()` macro: zero call sites.

ERP additionally carries a literal copy-paste pair: `admin/web.py:74-110` and
`admin/web/common.py:44-90` each define an identical `Pagination(TypedDict)` and
`_build_pagination()` page-window helper, neither importing the other, neither
tested. Two products cannot share a contract that one of them has not managed to
share with itself.

Sub's and ERP's are not the same contract. Sub's takes two typed objects and
forbids the caller from touching the query string; ERP's takes six loose
primitives and builds the query string itself. Sharing them would require a flag
saying which caller you are, which is a refusal.

**Verdict: record the duplication; leave both in place.** Even the accessible
page-nav markup — `aria-current="page"`, the ellipsis run, the polite result
announcement — cannot be lifted without also fixing the href, and the href is
`ListQuery.url(...)` in one product and a hand-built string in the other. The
already-designed `LIST_SURFACE` component resolves this correctly by taking the
URLs as display-only values from a `ListQuery` the *kernel* owns — which is why
the UI half cannot land ahead of the kernel half, and why both are parked
together. **Unresolved, and already tracked.**

### 4 — Semantic tables: PRODUCT-LOCAL, refused

Sub has `<table>` in **292** templates and `scope="col"` in **39** of them;
`<caption>` appears in **3**. Three shared table shells coexist
(`macros.html`'s `data_table`/`table_head`, `list_macros.html`'s `sort_header`,
`data_grid.html`'s inline markup) and 250+ tables use none of them. `table_head` reaches 34 templates, `sort_header` only 5.

ERP is the opposite shape and the contrast is instructive: `<table>` in **430**
templates, `scope="col"` in **360** of them, `<caption>` in **1**. Its
accessibility hygiene is far better than Sub's — and it is achieved with **no
shared markup component at all**. ERP's `data_table` macro has **6** call sites,
all in `templates/fleet/reports/`; the other ~424 tables are hand-written `<table>`
markup that shares only CSS class names from `src/css/components/_tables.css`.
So the thing ERP actually reuses for tables is a stylesheet, not a component.

The starter splits 5 macro / 5 hand-rolled across ten `<table>` templates, and
its platform-tier tables introduce a third convention again — inline
`style="…var(--dmui-*)…"` attributes.

**Verdict: refused, and specifically the refusal the brief names.** The only
thing these have in common is the tag name. A component general enough to render
all of them is the "render any table" component that knows about everything.
What might one day be portable is much smaller than a table: a `<th>` that
carries `scope="col"` and a correct `aria-sort`, and a horizontally scrollable
wrapper. Neither has two consumers of one contract today. **Refused; the
duplication is the deliverable.**

### 5 — Sorting / filtering: PRODUCT-LOCAL, refused

Sub's `sort_header` emits a deep-linkable server-side sort URL derived from
`ListQuery`, with `aria-sort`, an accessible label, and a non-colour direction
indicator; sort keys are allowlisted by `ListDefinition.sortable_keys`, which
raises on anything undeclared. It is tested at the markup level
(`tests/test_list_macros.py::test_active_descending_column_toggles_to_ascending_and_marks_aria_sort`,
`::test_inactive_column_is_aria_sort_none_and_links_ascending`).

ERP's `sortable_th(label, column, current_sort, current_dir, ...)` emits
`<th scope="col" data-sort-column="…" aria-sort="…">` with **no href at all** —
it is a hook for client-side JavaScript sorting. 50 call sites. ERP's sort-key
allowlists are hand-rolled per route (`app/services/operations/inv_web.py:908-919`,
`app/services/people/recruit/web/report_web.py:170,299`); a repo-wide grep for
`ALLOWED_SORT|SORTABLE_COLUMNS|sort_allowlist` returns zero hits, so ERP has no
shared allowlist construct at all.

Both emit `aria-sort`. They do not share a contract: one is a server round-trip
that changes the URL, the other is a DOM attribute that changes nothing. Their
failure behaviour differs completely — Sub rejects an undeclared sort key with a
named error, ERP has no server-side rejection to fail.

The starter contributes nothing: `apply_ordering` (its allowlist-checked
ordering helper) has zero call sites, is not exported, and no screen in the repo
lets a user change sort order at all.

**Verdict: refused.** Sort-key allowlists are query capability and stay with the
owner in every case. The `aria-sort` markup is genuinely inert and genuinely
duplicated, and it is still not extractable, because the two products disagree
about what a column header *does*. **Refused.**

### 6 — Empty / loading / error / permission states: split verdict

**Empty state: already done, and it is the fleet's worked example.**
`dotmac_ui.components.EMPTY_STATE` is `reuse-proven` in
`packages/dotmac-ui/EXTRACTION.toml`'s `components` slice, with two independent
consumers — ERP at `462b6fa5458ce443cb3b1b1dca499644fd68ed0d` and Sub at
`73c35f49aebb3c15b87dc33e75136bc63cc8248e`, both pinning `0.1.0a7`, both
resolving the package template through their real Jinja loaders, both reducing
their local renderer to a thin argument adapter that owns no markup. Sub's
`templates/components/data/empty_state.html` is that shim.
`dotmac_workspace` independently uses the published `.dmui-empty-state` classes
in `operator/web.py:76-97`.

Two things are worth carrying forward from this slice, because they are the only
worked example the fleet has.

First, **the retirement gate is a two-directional ratchet, not a promise.** ERP's
`tests/architecture/test_dotmac_ui_adoption.py` pins
`LEGACY_EMPTY_STATE_BASELINE = (25, 22)` — 25 occurrences of the pre-migration
inline `class="empty-state` pattern across 22 files — and fails if that count
moves in *either* direction without the baseline being lowered deliberately. The
adapter macro is at ~355 call sites and the legacy pattern still survives in 23
files. Adoption and retirement are separate, and the second one is measured.

Second, **the extraction is guarded against re-forking.** The same file carries
`test_installed_ui_release_and_component_contract_are_exact`,
`test_ui_composition_boundary_uses_public_package_paths`,
`test_ui_package_is_not_vendored_into_erp`, and a live-route assertion that the
`.dmui-empty-state` CSS is actually served
(`test_ui_asset_has_a_dedicated_mount_before_erp_static`). Any future component
must arrive with the same four.

Note also that Sub *still* carries a second, unrelated `empty_state` macro in
`macros.html` (table-row shaped, `colspan` parameter) and that `data_grid.html`
hand-rolls a third. Retirement of the local copies is unfinished even where the
extraction succeeded.

**Loading: no shared implementation exists.** Sub has a CSS convention only
(`.htmx-indicator` defined once in `static/css/src/main.css:125-127`, applied by
hand in 34 templates), no macro, no test. ERP has a live `_loading.css`. The
starter has no `hx-indicator` convention at all — only a global toast
(`base.html:56-59`, `role="status" aria-live="polite" aria-atomic="true"`).
Three unrelated approaches. **Unresolved.**

**Error pages: three separate families, and the starter's are the best.**
Kernel-owned `errors/{400,401,403,404,409,422,500,csrf}.html` in the starter,
genuinely tested — `tests/unit/test_errors.py::test_html_client_gets_branded_500_page`
asserts the raw exception message is *not* leaked into the page, and
`::test_htmx_request_accepting_html_gets_html_not_json` asserts content
negotiation. Sub has `templates/errors/*` plus per-portal overrides for admin,
customer and reseller. ERP renders one central `templates/errors/403.html`
driven from three call sites in `app/errors.py`, with no per-screen duplication
anywhere — and no template test; its 403 coverage asserts status codes, not
markup. ERP's `detail_error_state` macro, which looks like a fourth
implementation, has **zero** call sites.

These are exception-handler pages carrying product-specific copy and
product-specific recovery links. **Unresolved; no candidate.**

**Permission-denied: not a presentation problem.** Sub's
`action_permitted(request, action)` (`app/services/auth_dependencies.py:738-747`)
combines an action's eligibility with the caller's held permissions, and is used
in 14 templates; it is tested behaviourally
(`tests/test_ui_permission_gating.py::test_action_permitted_combines_eligibility_and_permission`).
The starter renders `errors/403.html` uniformly from the error middleware and
has no inline denial block. Vendor CP authors no denial UI at all. Eligibility
and authorization are named in the brief as never-extractable, and this is why:
the interesting behaviour is the decision, and the decision belongs to the
owner. **Unresolved for the inline presentation; the decision is not a
candidate at any maturity.**

### 7 — Summary / stat cards: PRODUCT-LOCAL, refused

Sub has at least five parallel stat-card macros — `stats_card` (69 templates),
`kpi_tile`, `hero_stat`, `summary_card`, `status_filter_card` — plus a typed
`Kpi` contract in `ui_contracts.py` whose `cohort_url` must be app-relative so
the headline number and its drill-down come from one filtered query
(`tests/test_ui_contracts.py::test_kpi_carries_value_state_and_cohort_url`). ERP
has `stats_card` with **177** call sites and a different signature (`subtitle`,
`trend`, `variant`, `sparkline`, `active`) — and its only coverage is a render
smoke assertion (`tests/test_template_macros.py::test_macro_render_smoke_for_broad_component_set`);
no test asserts a variant class, a colour mapping or the trend direction. Sub's
five markup macros have no focused render test either.

`docs/inventories/ui-surface-inventory.md` already measured the CSS underneath:
`components/_dashboard.css` is the **most drifted file in the fleet** — 537
lines in ERP against 552 in Sub with **439 differing** — and recorded the
conclusion that a file that divergent "is unlikely to be one component wearing
two skins." That measurement stands, with the added correction that Sub's copy
is a fossil, so the drift is one live file against one dead one.

Both `stats_card` macros also take **value-named** colours — `color="blue"`,
`color="teal"`, interpolated straight into `from-{{ color }}-500` Tailwind class
names. That is the same anti-pattern
`tests/architecture/test_ui_public_surface.py::test_no_token_is_named_by_value`
forbids one layer down, and the practical consequence is concrete: the design
system has no value-named colour to hand such a macro, so porting either one
would mean redesigning its whole parameter surface, not moving it.

**Verdict: refused.** This is the category where similarity is most superficial
and the underlying disagreement is largest. **Refused.**

### 8 — Status badges: the one CANDIDATE — see § 6

### 9 — Timeline / event history: PRODUCT-LOCAL, unresolved

Sub's most rigorous timeline is `app/services/customer_timeline.py`
(`CustomerTimelineItem`, `CustomerTimelineActorKind`, `CustomerTimelineResult`,
`build_customer_timeline()`), tested by
`tests/test_customer_timeline_projection.py` and guarded by
`tests/architecture/test_customer_timeline_boundary.py` — and it is rendered by
hand inline in one large detail template, not through any macro. The macro that
does exist, `timeline_item` in `macros.html`, has 8 callers, one of which is Sub's
own design-system gallery page. Elsewhere Sub has a
partial with 2 callers (`_operations_history.html`), several one-off partials,
and `recent_activity_panel.html` (11 callers) taking a loosely typed
`recent_activities` list with free-form `.message`/`.detail`/`.time`. Sub's own
`_audit_table.html` hand-rolls hardcoded green/red "Success"/"Failed" pills
rather than calling either badge macro. The starter's only audit surface,
`admin/rbac/_audit_table.html`, is a plain five-column table with no timeline
rendering at all. Vendor CP returns an issuance array as raw JSON with no test.

ERP is the one product with a genuinely shared activity partial:
`templates/partials/_recent_activity.html`, 20 direct includes plus a five-caller
AP wrapper — **25 effective call sites** — fed by a named owner,
`app/services/recent_activity.py` (`get_recent_activity`,
`get_recent_activity_for_record`), which reads `AuditEvent`/`AuditLog` and
resolves a record's primary key generically through `sa_inspect`. It has **no
template test**; its coverage is service-level only. ERP also carries an
orphaned sibling, `templates/components/_change_history.html`, with zero
includes anywhere.

ERP's event shape (`action_label`, `actor_name`, `occurred_at`,
`changed_fields_label`, `reason`, `correlation_id`, `ip_address`) and Sub's
(`.message`, `.detail`, `.time`) are different shapes for the same idea, and
Sub's rigorous one — `CustomerTimelineItem` with actor-kind and result
attribution — is a third shape again. There is no shared "one timeline event"
data shape across any two screens, let alone across two products. The portable
half would be the audit-event projection, and that is a domain projection over
each product's own audit subsystem, not markup.

**One important qualification, from outside this census's four repositories.**
Sub's `recent_activity_panel.html` and CRM's are **byte-identical** (blob
`5319b6d`) with 11 and 10 live references respectively, and a `RECENT_ACTIVITY`
candidate — markup, CSS and a display-only `ActivityItem` — is already
audit-complete on the parked `feat/dotmac-ui-complete` branch. That is real and
should not be re-derived. It is also **fork evidence, not independent reuse**:
`docs/inventories/map-ui-sources.md` established that CRM is a related fork of
Sub and therefore counts as requirement evidence, never as a second consumer.
Within the four repositories this census was asked about, the finding stands.

**Verdict: within scope, no contract exists to extract; the `RECENT_ACTIVITY`
candidate outside it is already recorded and unadopted. Unresolved.**

### 10 — Bulk eligibility, row actions, responsive behaviour: PRODUCT-LOCAL, refused

**Eligibility is never a candidate, and Sub's implementation shows exactly why.**
`app/services/bulk_actions.py` filters unauthorized actions out of the
projection entirely rather than sending them and hiding them
(`BulkResourceDefinition.project()`), derives `selection_enabled` from whether
any action survived that filter, fingerprints a selection with an
order-independent `membership_scope_token()` so a changed cohort is caught
before a mutation runs, and refuses to treat an empty selection as filtered
scope. Its tests assert each of those
(`tests/test_bulk_action_contracts.py::test_bulk_contract_omits_unauthorized_actions_and_permission_vocabulary`,
`::test_membership_scope_token_is_order_independent_and_scope_specific`,
`::test_bulk_selection_never_treats_an_empty_selection_as_filtered_scope`).
Permission, eligibility and scope are the whole of the behaviour. None of it may
move.

**The markup is two contracts.** ERP's `bulk_select_header`/`bulk_select_cell`/
`bulk_action_bar` (14 call sites) take a caller-supplied list of
`{name,label,endpoint,class,confirm}` dicts and have no permission or
eligibility concept whatsoever. Sub round-trips its server-projected contract
into the DOM (`data-bulk-contract="{{ … | tojson | forceescape }}"`) so the
client re-derives nothing. Sub's own `data_grid.html` tracks `selectedIds` and
has no bulk-action toolbar consuming it — selection state with nowhere to go.
The starter has no bulk selection at all; both `type="checkbox"` occurrences in
its whole template tree are form fields.

**Row actions and accessibility.** Sub's exemplar tables carry real per-row
labelling (`aria-label="Select all customers on this page"`,
`aria-label="Select {{ customer.name }}"`, `:indeterminate.prop` for partial
selection, `aria-current="page"`). ERP's bulk macros carry generic
`aria-label="Select all rows"` / `"Select row"`. The starter's `action_buttons`
icon-only controls carry `title=` and **no `aria-label`** — `title` alone is not
an accessible name for assistive technology, and no test covers it.

**Responsive behaviour barely exists.** In Sub, exactly **two** templates use
the `sm:/md:/lg:table-cell` progressive-disclosure idiom. In the starter, **no**
`<table>` is wrapped in `overflow-x-auto` — every one sits inside
`overflow-hidden`, which clips rather than scrolls on a narrow viewport — and
the compiled `dotmac-ui` stylesheet contains exactly one `@media` rule in the
whole file (`prefers-reduced-motion`). ERP is the only product with a real
convention: `.table-responsive` in `src/css/layout/_responsive.css:34-45`
(horizontal scroll with `min-width: 600px`) plus per-screen Tailwind
`hidden sm:table-cell` column hiding in 40 files chosen independently. Nobody
anywhere implements the stacked-card mobile fallback — the
`content: attr(data-label)` technique has zero hits in ERP. Per § 2's
correction, `_responsive.css` and `_touch.css` have **one** live consumer, not
two.

ERP's bulk markup is, to be fair, the best-tested accessibility markup found in
this census — `tests/test_template_macros.py::test_bulk_macros_include_accessibility_attributes`
asserts the `aria-label` values, and
`::test_action_buttons_escape_text_and_skip_actions_without_href` asserts that
an untrusted `title`/`aria_label` is escaped and an action without an `href`
does not render as a link. ERP additionally ships a repo-wide accessibility
scanner heuristic (`scripts/audit_template_a11y.py`) with unit tests for the
detector itself (`tests/test_audit_template_a11y.py`). None of that changes the
verdict, because ERP's bulk macros still carry no eligibility concept and Sub's
contract still cannot be expressed in them.

**Verdict: refused for the markup, permanently excluded for the eligibility.**
The genuinely missing thing — a responsive-table wrapper and a correctly
labelled row-action group — has one live implementation, no shared contract, and
no test. **Refused.**

## 6. The one candidate: `status-badge`

This is the only unit in the ten categories that is **not already covered by the
parked list-surface programme** and still survives the similarity test. It is
recorded here at `audit-complete` — deliberately drawn, zero contract consumers,
one concrete candidate. This census does not publish it, does not add it to
`dotmac_ui.components.COMPONENTS`, and does not call it `adopted` or
`reuse-proven`.

Note the reason for waiting, because it is *not* the consumer count: § 6.4
below states it as readiness grounds 2 and 4, which is the only kind of
justification the 2026-08-12 amendment leaves available.

### 6.1 Why this one and not the others

Every other category fails on input shape: Sub and ERP pass different things and
mean different things by them. Status badges fail that test too **at the macro
level** — and pass it one layer up, because Sub has already separated the two
concerns and `dotmac-ui` has already taken half of the result.

`app/schemas/status_presentation.py` (Sub) draws the line explicitly in its own
docstring: *"Lifecycle services own the status values and transitions. This
projection owns their human label, semantic tone, and icon key so web and mobile
clients do not create competing interpretations. Clients still own concrete
colors, spacing, and platform-native rendering for each semantic tone."*

That sentence is the contract. `StatusTone` is five values — `positive`, `info`,
`warning`, `negative`, `neutral`. `StatusIcon` is seven. `StatusPresentation` is
`(value, label, tone, icon)`. Everything domain-specific lives on the far side
of it, in the 1,344-line mapping module.

And the tokens for exactly those five tones **are already published**. The
package's first release (0.1.0a1) shipped a `status` token category of 20 —
`--dmui-status-{positive,info,warning,negative,neutral}-{surface,border,foreground,indicator}`
— and its changelog records where they came from: *"the `status-{surface,border,foreground,indicator}`
quartet … promoted from class scope to `:root`"*, taken from `dotmac_sub`'s
`design-system.css`. `COMPATIBILITY.md` § "Where the vocabulary came from" says
the same. So the placement question is not open: the design system already owns
this vocabulary, and owns it *from this source*. What is missing is a component
that uses it and evidence that two products want it.

That same release also wrote down the discipline this census is applying, and it
is worth quoting because it was written before anyone had a candidate:
*"Deliberately NOT in this release: the Jinja/HTMX component library, layouts,
and navigation primitives that ADR-0006 § 2 assigns to this package. U1 lays the
foundation later slices extend, and ADR-0006 § 5 forbids harvesting components
from the fleet on the grounds that they look similar."* Two components have
arrived since — `empty_state`, which earned two independent consumers, and
`map_frame`, which is still audit-complete with none. This candidate would be
the third, and it starts where `map_frame` is, not where `empty_state` is.

### 6.2 The boundary

**The ownership is a split, and half of it is already drawn.** The tone/icon
*vocabulary* is a transport-neutral projection value, which is exactly the
capability the parked `dotmac_kernel.ui_projection` candidate was drawn to own
("transport-neutral status, state/freshness, KPI cohort and backend-decided
action values"). The *markup* is `dotmac-ui`'s. These cannot be merged:
`dotmac-ui` imports no kernel and never will, so the component must accept a
plain string tone and the enum must live on the other side of the seam. Any
proposal that puts `StatusTone` inside `dotmac-ui` is wrong on the dependency
direction alone.

**Inside the `dotmac-ui` half.** An inert Jinja macro rendering one
already-decided status: a caller-supplied `label`, one of the five `tone`
strings, one of the icon keys, and a `size`. Token-native `.dmui-status-badge*` classes resolved
from the compiled stylesheet. Non-colour differentiation (the icon) so the badge
is not colour-alone — the accessibility property Sub's macro comment already
claims and Sub's test already asserts. An accessible name of the shape Sub
proves today: `aria-label="Suspended status: warning"`. An unknown or absent
tone renders **neutral**, never a domain default.

**Outside it, permanently.** Every status name in the fleet.
`app/services/status_presentation.py` — all 1,344 lines and ~50 mapping
functions — stays in Sub. ERP's 79-entry `badge_classes` dict and its
26-entry `badge_symbol` dict stay in ERP until ERP replaces them with its own
projection.
The component must not contain the string `PAID`, `ACTIVE`, `REACHED_REORDER`,
`delinquent`, or any other product noun. A `status → tone` decision is a
lifecycle decision and belongs to the owning service, exactly as
`docs/inventories/map-ui-sources.md` kept provider runtime and geography out of
`map_frame`.

There is no product flag. If a product needs the badge to behave differently
because it is that product, the answer is no.

### 6.3 The required per-candidate record

| Field | Value |
|---|---|
| **Proposed name** | `status-badge` (a fourth `[[slices]]` entry in `packages/dotmac-ui/EXTRACTION.toml`) |
| **Proposed owner** | Split. Markup and classes: `dotmac-ui` — presentation is this package's job and it already owns the five tone token sets. Vocabulary (`StatusTone`, `StatusIcon`, `StatusPresentation`): `dotmac_kernel.ui_projection`, the already-drawn parked candidate, or Sub until that lands. Never both in one package. |
| **Source commits** | `dotmac_sub:5ffdb1a945b4c50b63d787579d619b12e062e6bb` (qualifying source); `dotmac_erp:7b62974b366eead1b32bead380e47d9cf10ec4c7` (legacy writer, requirement evidence only) |
| **Source paths** | `dotmac_sub:app/schemas/status_presentation.py`; `dotmac_sub:templates/components/ui/macros.html` (`status_badge` L249-343, `status_presentation_badge` L346-355); `dotmac_sub:static/css/design-system.css` (`.status-tone-*`, L99-145); `dotmac_erp:templates/components/macros.html` (`status_badge` L13-149) |
| **Preserved behaviour** | five tones, seven icon keys, label carried not derived, neutral fallback for an unknown value, non-colour differentiation, an accessible name naming both the label and the tone |
| **Preserved tests** | `dotmac_sub:tests/test_status_presentation.py` (842 lines, ~35 functions, exhaustive per-enum coverage, `test_presentation_fallback_is_neutral_and_legacy_inactive_is_explicit`, `test_work_order_legacy_spelling_is_explicit_and_unknowns_are_neutral`); `dotmac_sub:tests/test_customer_list_ui_contract.py::test_semantic_status_macro_renders_owner_label_tone_and_icon` (asserts the label, `status-tone-warning`, the icon path and `aria-label="Suspended status: warning"`); `dotmac_sub:tests/test_field_status_ui_contract.py::test_field_mobile_has_no_work_order_status_label_or_color_dictionary` (a second client holding no dictionary of its own — the strongest single proof that the tone contract travels); `dotmac_erp:tests/test_template_macros.py::test_status_badge_unknown_status_falls_back_to_draft_style` (the fallback ERP must keep, corrected to neutral) |
| **First adopter** | `dotmac_sub`. Its cutover replaces `status_presentation_badge`'s body with the package macro at its **126 call sites across 73 templates**, leaving `status_presentation.py` untouched. |
| **Second-adopter proof** (for `reuse-proven`, **not** a precondition for extracting) | `dotmac_erp` — and only after ERP does product work nobody has scheduled: replace its 79-entry `badge_classes` and 26-entry `badge_symbol` in-template dicts with an ERP-owned Python projection to the five tones, then adopt the package macro at its ~390 call sites across 264 templates. The evidence required is the same shape the `components` slice already carries: a `pinned_at` row at an immutable commit **plus** a product-owned architecture test that resolves the package template through ERP's real Jinja loader, asserts the declared contract signature, and rejects a vendored copy — the four assertions `dotmac_erp:tests/architecture/test_dotmac_ui_adoption.py` already makes for `empty_state`. A pin alone is installation. A second consumer that only *looks* similar is not a second consumer of this contract. |
| **Retirement gate** | Sub: `status_badge`'s 28-variant domain table deleted and its **366** raw-string call sites across 144 templates migrated, under a two-directional ratchet on the model of ERP's `LEGACY_EMPTY_STATE_BASELINE = (25, 22)`. ERP: `badge_classes`, `badge_symbol` and the `badge-*` rules in `src/css/components/_badges.css` deleted, under the same ratchet. Neither product may keep a second markup owner; the local macro becomes a thin argument adapter or nothing. |

### 6.4 Why it waits — stated as readiness, not as a consumer count

"Only Sub does this" is **not** a reason, and must not be written down as one.
Two readiness grounds are, and both are measurable.

**Ground 4 — defect remediation comes first. The qualifying source does not run
its own contract on most of its own screens.** Counted at `5ffdb1a9`:

| | templates | call sites |
|---|---:|---:|
| `status_presentation_badge(...)` — the tone contract | 73 | 126 |
| `status_badge(...)` — a bare string into the macro's own table | 144 | 366 |
| both, in the same template | 9 | — |

The 366 rely on `status_badge`'s own 28-entry variant table, which carries
`paid`, `overdue`, `delinquent`, `suspended`, `canceled`, `blocked`, `disabled`,
`snoozed`, `resolved`, `closed`, `decommissioned` and `maintenance` as
first-class keys — Sub's own domain vocabulary, sitting inside the markup the
candidate would replace. Extracting now would lift a component out of a file
whose *other* macro is the thing the component exists to abolish, and would give
that arrangement a version number.

**Ground 2 — the boundaries are unproven.** No second implementation has ever
pushed on this seam. ERP has 79 statuses and no tone concept at all, so the
five-tone reduction has never been tested against a vocabulary that did not
produce it. A generalisation checked only by its own source is not checked.

Grounds 1 and 3 do **not** apply and should not be borrowed: the contract is
ratified in Sub and enforced by an 842-line test suite, and the enforcement
mechanism for a published component already exists
(`test_no_component_class_is_published_without_its_contract` plus the four
adoption assertions ERP already makes for `empty_state`).

So the shape of a decision to proceed is narrow and concrete: land a declining
two-directional ratchet on Sub's 366 in the same change that adopts the
component, and characterise ERP's 79 statuses against the five tones before
claiming the reduction holds. If that characterisation fails — if ERP's finance
and inventory vocabularies do not reduce with their meaning intact — the correct
outcome is that this candidate is **withdrawn**, and status badges join the
other categories as product-local.

## 7. What was refused, and what was tempting

Nine of the ten categories produce no component. Three of those refusals were
close enough to be worth recording, because the next reader will feel the same
pull.

**The sortable `<th>` was the most tempting thing in the census.** Sub's
`sort_header` and ERP's `sortable_th` both emit `<th scope="col" aria-sort="…">`
with a non-colour direction indicator, both get the three-state
`ascending`/`descending`/`none` logic right, and both are the kind of small
accessible fragment a design system exists to own. Sub's is even tested at the
markup level. The evidence refuses it anyway: Sub's header is an `<a href>` that
changes the URL and re-queries the server against an allowlist that raises on an
undeclared key; ERP's has **no href** and is a `data-sort-column` hook for
client-side JavaScript. A component serving both would need a parameter saying
which kind of caller you are — and a mode flag distinguishing who is calling is
a refusal, not a design option. What is shared here is a WCAG rule both teams
read, not a contract either could adopt from the other.

**The accessible pagination nav was second.** Both products emit
`aria-current="page"`, an ellipsis-compacted page run and a polite result
announcement; Sub's is tested down to the literal "Showing 26 to 50 of 80
referrals". But the hrefs come from `ListQuery.url(...)` in Sub and from string
concatenation inside the macro in ERP, and the nav is inseparable from its
hrefs. Extracting the markup without the URL contract yields a component that
cannot render a link.

**The status-tone stat card was third.** Sub's `stats_card` already accepts a
`tone` and renders `status-tone-*` classes when given one, which looks like the
badge candidate wearing a different shape. It is not: the same macro's *other*
branch interpolates value-named Tailwind colours (`from-{{ color }}-500`), ERP's
`stats_card` has a different signature entirely, and
`components/_dashboard.css` is the most drifted file in the fleet at 439
differing lines. `test_no_token_is_named_by_value` would reject the vocabulary
at the boundary regardless.

For the record, the three refusals that were never close: a generic table
component (the brief's named prohibition, and the fleet has at least six
independent table shells), bulk-action eligibility (permissions, scope tokens
and preview requirements — owner-only, at any evidence level), and cursor
pagination (no presentation implementation exists in any of the four
repositories, so there is nothing to harvest and nothing to keep local either).

None of these three refusals turns on how many products do the thing. Two of
them would be refused if all four products did it identically, because what is
being refused is the *placement* of a decision, not the strength of the
evidence. That distinction is the one the 2026-08-12 amendment exists to
protect, and it is worth restating at the end of a document whose easiest
misreading is "not enough adopters."

## 8. What this census does not settle

- **What happens to the parked branches.** `feat/kernel-listing-contract`
  (`90438e8`, kernel `0.1.0a43`) and `feat/dotmac-ui-complete` hold finished,
  unvalidated work that is 40 commits behind an `origin/main` which has long
  since claimed a higher kernel version. The recorded release-order fact is that
  `a43` must never be published from that branch: it has to be rebased onto the
  then-current main and renumbered. Nothing in this census changes the ADR-0017
  ruling that parked it, but the branches are decaying, and "still parked" and
  "still rebasable" are not the same claim. Someone should decide whether they
  are evidence to keep or work to redo.
- **Whether ERP's 79-entry status vocabulary maps cleanly onto five tones.**
  Nobody has tried. Finance's `POSTED`/`RECONCILED`/`VOIDED`/`REVERSED` and
  Inventory's `REACHED_REORDER`/`UNDER_REPAIR` may or may not survive the
  reduction with their meaning intact. That question, not the markup, is the
  real cost of the second adoption, and it must be answered inside ERP before
  the second-adopter proof can be attempted.
- **Whether Sub's three parallel table/pagination/empty-state families are
  convergeable at all.** `docs/designs/LIST_QUERY_MIGRATION.md` treats them as
  an in-progress, screen-by-screen migration. This dossier measured that the
  migration is roughly a third complete (11 of Sub's list templates on
  `list_macros`, 8 on `data_grid`, the rest on neither) but did not evaluate
  whether the remainder can move.
- **Whether ERP's live `src/css/` tree should retire.** `dotmac-ui`'s
  `EXTRACTION.toml` already records it as outstanding for the `tokens` slice.
  This census adds that `_tables.css`, `_badges.css`, `_responsive.css` and
  `_touch.css` are the parts a presentation extraction would eventually need,
  and that Sub's identical-looking copies are fossils that must not be counted
  as a second consumer.
- **Whether the starter's dead presentation code should be deleted or wired.**
  `pagination()` (zero call sites), `apply_ordering` (zero call sites, not
  exported), `status_badge` (one), `action_buttons` (two), and no test asserting
  any of their markup. That is a starter question, not a fleet one, but it does
  mean the reference assembly cannot be cited as evidence for any of the ten
  categories.
- **The accessibility floor nobody owns.** Sub has `scope="col"` on 39 of 292
  table templates; ERP on 360 of 430; the starter's icon-only row actions carry
  `title` and no `aria-label`; only ERP has a horizontal-scroll wrapper, and
  nobody has a stacked-card mobile fallback. This is a real, measurable,
  fleet-wide gap. It is *not* an argument for a shared table component — it is
  an argument for each product's own a11y ratchet, of the kind ERP already
  built in `scripts/audit_template_a11y.py`.

## 9. If this is acted on: the slice sketch

For whoever writes the fourth `[[slices]]` entry, the shape it must take — and
the three fields it may **not** be given today:

```toml
[[slices]]
name = "status-badge"
contract = "An inert, token-native badge for one already-decided status: caller-supplied label, one of five tones, one icon key, one size. Non-colour differentiation and an accessible name. It decides nothing and names no domain status."
status = "audit-complete"            # NOT reuse-proven. NOT published.
source_paths = [
  "dotmac_sub:app/schemas/status_presentation.py",
  "dotmac_sub:templates/components/ui/macros.html",
  "dotmac_sub:static/css/design-system.css",
  "dotmac_erp:templates/components/macros.html",
]
preserved_tests = [
  "dotmac_sub:tests/test_status_presentation.py",
  "dotmac_sub:tests/test_customer_list_ui_contract.py",
  "dotmac_sub:tests/test_field_status_ui_contract.py",
  "dotmac_erp:tests/test_template_macros.py",
]
contract_consumers = []              # empty, and it stays empty until a cutover lands
reference_consumers = []
candidate_consumers = ["dotmac_sub", "dotmac_erp"]
local_copy_retirement = "No local copy retires in this change; adoption is out of scope. The first adopter replaces only status_presentation_badge's body and must add a product-owned test resolving the package template through its real loader, asserting the declared contract signature, and rejecting a vendored copy. Sub's 28-variant in-macro table and its 366 raw-string call sites, and ERP's 79-entry badge_classes / 26-entry badge_symbol dicts and its badge-* CSS rules, retire under two-directional ratchets on the model of ERP's LEGACY_EMPTY_STATE_BASELINE. A second independent adopter must complete the same cutover before this slice becomes reuse-proven."
```

`adoption_evidence` is deliberately absent — there is nothing true to put in it.
Per `tests/architecture/adoption_evidence.py`, an `adopted` row needs a field in
a structured file at an immutable commit, and a Jinja-loader composition is
Python; so the first cutover will most likely land as a `pinned_at` row plus an
entry in `PIN_ONLY_ADOPTION_DEBT`, exactly as the `tokens` and `components`
slices did. Say that out loud in the change rather than writing a stronger row
than the evidence supports.

## 10. Method, for whoever re-runs this

- Counts are `grep` over checked-in templates and Python at the commits in the
  header. Where a template can call a macro more than once, both a **file** count
  and a **call-site** count are given, because they differ by a factor of three
  in places and quoting one as the other overstates or understates adoption.
- **A macro definition is not adoption.** Four ERP macros
  (`search_filter_bar`, `detail_error_state`, the `_change_history.html` partial,
  `ListParams`) and three starter units (`pagination()`, `apply_ordering`,
  effectively `status_badge`) are fully formed and have zero or near-zero
  callers. Grepping for `{% macro` over-counts shared surface badly. Always
  count the call sites.
- **Check whether the CSS you are reading is built.** `ui-surface-inventory.md`'s
  correction is the standing example: two byte-identical trees, one live, one
  fossilised since 2026-02-16. Confirm against the build (`package.json`
  scripts, `Dockerfile` `COPY`, the compiled output's actual custom properties)
  before treating a file as evidence of a second consumer.
- **Distinguish a compile test from a behaviour test.** Several categories here
  look covered and are not: a route returning 200 with a template rendered says
  nothing about `aria-sort`, a badge's tone mapping, or a clamp. The strong
  suites in this census —
  `dotmac_sub:tests/test_status_presentation.py`,
  `tests/test_list_query_contract.py`, `tests/test_list_macros.py`,
  `tests/test_bulk_action_contracts.py`,
  `dotmac_erp:tests/test_template_macros.py`,
  `tests/test_filter_template_targets.py` — assert rendered strings, exhaustive
  enum coverage, escaping, and rejection. The weak ones assert non-empty output.
