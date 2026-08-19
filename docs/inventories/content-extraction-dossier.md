# Editorial content extraction dossier

**As of:** 2026-08-19
**Starter base:** `c6ef6cd7b13105bd95c3faf354ffee9032077625`
**Audited Mkt source:** `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`

This is the product-first contract for `dotmac-content`, the first executable
slice of the decomposed marketing suite. Gate 1 recorded the deliberately
absent distribution as RED on Observer at exact commit
`85dc9bc24b169dab7bbcd38b0cd67b1a3d058881`. Gate 2 is green at exact local
commit `6665569b41c0afa112784100ef4912fed9ffb9ce`; the package remains an
unpublished, unallowlisted and unadopted candidate.

The Mkt remote was rechecked directly on 2026-08-18 and `refs/heads/main`
resolved to the revision above. It is one commit after the audit's original
`1a185b47164e34601769c84976e95578996c4523` pin. The exact delta adds
delivery-backed external-reference presentation in `app/web/campaigns.py` and
its web test; none of the selected content models, schemas or services changed.
That new behavior is publishing evidence and does not alter this content
contract.

## 1. One owner and the names it owns

Mkt calls its editorial planning container a `Campaign`, but the same suite
also needs a real outbound campaign owner for audiences, recipients, sequences,
suppression and attempts. Carrying both forward under one name would recreate
the overlap the decomposition is intended to remove.

`dotmac-content` therefore owns:

| Target fact | Source fact | Authority |
|---|---|---|
| `ContentPlan` | Mkt `Campaign` | name, description, editorial plan lifecycle and optional date range |
| `ContentItem` | editorial fields of Mkt `Post` | canonical title/body, editorial readiness and calendar placement |
| `ContentVariant` | generalized from delivery `content_override` | provider-neutral editorial alternatives keyed by an open `variant_key` |
| plan/item creative relation | Mkt `campaign_assets` / `post_assets` | role, caption, alt text and order around an opaque `file_ref` |

The word **campaign** is not exported by this package. `dotmac-campaigns` owns
outbound marketing campaign execution and may retain an opaque content
identifier or snapshot. It never imports this module's ORM or service.

## 2. Revision-1 contract

The module is tenant-plane only. Its permanent proposed allocation is owner
`content`, short code `content`, migration prefix `ct`, branch label `content`,
and schema `mod_content`. The allocation lands only with the manifest and first
migration; this dossier does not reserve an empty namespace.

The five initial tables are:

| Table | Required shape |
|---|---|
| `content_plans` | `tenant_id NOT NULL`; id; name; description; status; `starts_on`/`ends_on`; opaque `created_by_ref`; timestamps; unique `(tenant_id, id)` |
| `content_items` | `tenant_id NOT NULL`; id; composite plan FK; title; body; state; optional `planned_for`; opaque `created_by_ref`; timestamps |
| `content_variants` | `tenant_id NOT NULL`; id; composite item FK; open `variant_key`; optional title/body overrides; sort order; unique per tenant/item/key |
| `content_plan_creatives` | `tenant_id NOT NULL`; composite plan FK; opaque `file_ref`; role; caption; alt text; sort order |
| `content_item_creatives` | `tenant_id NOT NULL`; composite item FK; opaque `file_ref`; role; caption; alt text; sort order |

Every table is created with forced RLS and grants in the same migration. Every
same-module relationship includes `tenant_id`. `file_ref` and
`created_by_ref` are UUID-shaped opaque references with **no foreign key** to a
sibling module or product table. The assembly authorizes the actor and resolves
files before asking this owner to mutate its own state.

### Lifecycle

`ContentPlanStatus` preserves Mkt's five useful terms, but adds the transition
guard the source lacks:

- `draft -> active | archived`
- `active -> paused | completed | archived`
- `paused -> active | completed | archived`
- `completed -> archived`
- `archived` is terminal
- reasserting the current value is an idempotent no-op

`ContentItemState` is deliberately editorial: `draft`, `ready`, `archived`.
`draft <-> ready`; either live state may archive; archived is terminal. Mkt's
`planned` maps to `ready`. Mkt's `published` does **not** map to a content state:
publication is a release/outcome owned by `dotmac-publishing`.

`starts_on` must not follow `ends_on`. `planned_for` is an editorial calendar
fact, not a transport request. The publishing command supplies its own desired
delivery instant and records an immutable snapshot, so editing a content item
cannot silently rewrite an already requested release.

## 3. Source parity and intentional corrections

| Source behavior | Revision-1 disposition | Proof |
|---|---|---|
| Campaign create/get/list/count/update/archive; services flush but do not commit | **Port** as `ContentPlanService`; keep transaction authority outside the service | lifecycle unit canary plus service parity tests |
| Campaign status vocabulary | **Port terms, correct behavior** with the explicit transition graph above | exhaustive transition canary |
| Campaign date fields accept an inverted range | **Correct**, reject `starts_on > ends_on` | date-range canary |
| Post create/get/list/count/update and canonical title/body | **Port** as `ContentItemService` | service parity tests |
| Post `planned` | **Map** to editorial `ready` | mapping/backfill test |
| Post `published`, `published_at`, `external_post_id` | **Move** to publishing release/outcome records | total row-classification and publishing handoff test |
| Post `scheduled_at` | **Split**: editorial `planned_for`; requested remote time belongs to publishing | typed command boundary test |
| Per-delivery `content_override` | **Generalize** to an open editorial variant key only when the text is authored content; provider/target binding stays in publishing | variant uniqueness and snapshot tests |
| Campaign/post asset ordering | **Port meaning**, replacing the Asset FK with an opaque file reference and content-owned relation metadata | relation and cross-tenant canaries |
| Asset byte/provider metadata, Drive URLs/status/preview rules | **Reject** from content; files owns stored bytes and Integrator owns Drive transport | forbidden-surface architecture canary |
| Campaign members and `_can_edit_campaign` | **Reject** from content; these implement application authorization | no-person/member/role surface canary and Backoffice guard tests |
| Post delivery replacement, publish/update/delete remote post | **Move** to `dotmac-publishing` | no-delivery/provider surface canary |
| Generic campaign tasks | **Reject**; follow the independent project/work owner | forbidden-surface canary |
| Source hard delete of a post | **Correct** to archival in revision 1; retention and release evidence must remain addressable | terminal-state canary |
| Source service `ValueError` strings and web-layer direct queries/commits | **Correct** to typed domain errors, thin adapters and kernel transaction authority | architecture tests |

The source tests selected for behavioral porting are
`tests/test_marketing_models.py`, the campaign/post portions of
`tests/test_marketing_services.py`, and the editorial CRUD portions of
`tests/test_web_campaigns.py`. Publishing, analytics, Drive and provider tests
are preserved for their own module dossiers rather than copied into content.

## 4. Typed seams

The package accepts an opaque actor reference and editorial inputs. It emits no
provider-specific command. A product adapter may ask for a versioned
`ContentSnapshotV1` containing the content item id, title, body, ordered
variants and ordered creative references. That value is immutable and contains
no ORM object, callback, provider enum, credential, URL-fetching behavior or
database session.

The assembly then passes the snapshot to `dotmac-publishing` through its own
typed command. Content does not know whether a snapshot becomes a social post,
site fragment, email component or no release at all.

Backoffice remains the owner of who can create or edit a plan. It supplies the
authorized `actor_ref`; the module records that reference for audit but does not
interpret identity, membership or roles.

## 5. Cutover gate

The exact writers and their retirement proofs are frozen in
[`content-writer-retirement.toml`](content-writer-retirement.toml). Cutover 1
is Backoffice using a released package and its own `mod_content` rows.

For the source migration, every Mkt Campaign and Post row must enter a total,
reviewable classifier. A row may become content, publishing history, both via a
snapshot/correlation, or an explicitly archived/rejected source row. There is
no default bucket. Shadow comparison covers identity, text, dates, state,
creative order and actor reference for one complete planning cycle. A writer is
deleted only after its shadow is clean and the two-directional ratchet is
lowered in the same change.

Sub adoption comes later and independently. It pins the same published
contract, runs its own lineage and owns its own rows; no shared database,
filesystem, ORM model or service call crosses applications.

## 6. Gate 2 implementation evidence

On 2026-08-19 a fresh detached writable Observer checkout at exact commit
`6665569b41c0afa112784100ef4912fed9ffb9ce`, with full history and all 84 tag
refs, installed from the committed lock using Poetry 2.4.1 and passed:

- 100 focused content, product-first, source-coordinate and publication-ledger
  tests;
- `make check`: lock/toolchain, Ruff, nine import contracts, mypy across 314
  source files, Bandit, the composed migration gate, UI, module-catalog and
  format checks;
- the complete unit/architecture suite; and
- the complete disposable-PostgreSQL integration suite.

The focused PostgreSQL content canary then passed all four cases: exact live
catalog plus forced RLS, tenant-only reads across all five tables, cross-tenant
write refusal, and unscoped-role fail-closed behavior. The disposable database
container and network were removed; the test Compose file declares no volume.

This proves the candidate's local contract, not publication or adoption.
`dotmac-content 0.1.0a1` and kernel a73 remain unpublished; the module is absent
from the release allowlist and every Backoffice/Mkt writer-retirement row is
still `not-started`.
