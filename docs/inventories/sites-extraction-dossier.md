# Sites extraction dossier

**As of:** 2026-08-20
**Source mode:** greenfield after six-repository inventory
**Candidate allocation:** kernel a81, `si`, `sites`, `mod_sites`
**Gate 2 code/evidence revision:**
`8bf12ddc5cb938714d090fc0b0e69b83fa78f2d2`
**Release/adoption:** a1 released 2026-08-20; adoption not authorized

No qualifying site-builder exists at the exact coordinates recorded in
[`sites-sources.md`](sites-sources.md). This dossier therefore specifies the
smallest product-neutral owner rather than pretending a landing route is a
production implementation to port.

Gate 1 is RED by design at exact commit
`9f735b4ba5d7fd6c529c9d1d289aa0e245af2541`. A fresh detached writable
Observer checkout using Poetry 2.4.1 produced 13 passing source/evidence cases
and 40 controlled failures, all naming the absent `dotmac_sites` distribution.
Ruff lint passed; its two formatting-only corrections are recorded in the next
commit. Gate 2 may now add the smallest implementation.

Gate 2 is GREEN at exact revision
`8bf12ddc5cb938714d090fc0b0e69b83fa78f2d2`. A fresh, detached, writable and
tag-complete Observer checkout using Poetry 2.4.1 passed:

- all 124 focused Sites, numbering-regression and quality-coverage cases;
- `make check`, including nine import contracts, mypy over 365 source files,
  Bandit, the migration gate, generated-catalogue and format checks;
- all 4,028 collected unit/architecture cases; and
- all 509 collected disposable-PostgreSQL integration cases, including all
  five dedicated Sites RLS, same-tenant and immutability cases.

The PostgreSQL 16 container used a unique loopback port and Compose project;
its container and network were removed after the run. This was candidate
evidence only at that milestone: no package had yet been published,
allowlisted, composed or adopted.

The complete-gate run also proved a reusable guard defect. Four typed packages
were missing from one or both explicitly enumerated mypy/Bandit recipes. The
new discovery-driven architecture test now requires every distribution that
ships `py.typed` to appear in both recipes and includes a planted-omission
sensitivity proof. Enabling the omitted `dotmac-numbering` scan exposed and
fixed its previously unchecked strict typing and an ineffective Ruff-only SQL
suppression; the migration now enforces its static identifier premise before
using the narrow Ruff/Bandit annotations.

## Revision-1 persistence contract

| Table | Owned facts |
| --- | --- |
| `sites` | tenant site identity, stable slug/name, active/archived state and opaque creator reference |
| `pages` | stable page identity and key within one site |
| `page_revisions` | append-only revision number, title, body, SEO and opaque ordered file/form refs |
| `site_revisions` | immutable complete snapshot payload/digest, navigation, redirects, site SEO and guarded readiness state |
| `site_revision_pages` | append-only exact page-revision membership, route and ordering for one site revision |

All five tables are tenant-only, schema-qualified and forced-RLS. Every
same-module relation includes the tenant and site where needed, so a page
revision from another tenant or site cannot enter a snapshot. File/form/actor
references are opaque and never foreign keys. The root lineage requires only
the kernel tenant-scope catalog and module database roles.

Page revisions have no update/delete service. A site revision is created from a
complete tuple of exact page revisions and cannot accept another member later.
Database triggers refuse update/delete of page revisions and membership rows,
and refuse changes to a site revision's snapshot columns. Only its readiness
state and state timestamps may move through `draft -> ready -> retired`.

## Typed release contract

`SiteReleaseV1` is a frozen, self-contained value containing:

- site and site-revision opaque identifiers;
- ordered `SitePageSnapshotV1` values with exact page/page-revision ids, route,
  title, body, SEO and ordered opaque file/form references;
- typed navigation entries and redirect rules;
- site SEO metadata; and
- a canonical SHA-256 digest over the complete ordered payload.

Validation refuses an absent home route, duplicate routes/pages, unsafe paths,
navigation to a missing route, a redirect that shadows a page or points to
itself, unsupported redirect status and an unknown schema version. The digest
is deterministic and order-sensitive.

This local release value is not a publication attempt. A local Backoffice
renderer may consume it directly. A later typed Backoffice adapter may pass it
to `dotmac-publishing`, which alone records scheduling, attempts, outcomes and
the outbox. Integrator alone performs remote hosting I/O. If the publishing
contract cannot carry the structured artifact without overloading unrelated
fields, that contract must be extended explicitly before remote cutover.

## Lifecycle and concurrency

- `SiteState`: `active`, `archived`; archive is terminal.
- `SiteRevisionState`: `draft`, `ready`, `retired`.
- Only a draft revision may become ready; only a ready revision may retire.
- Reasserting the same state is idempotent.
- Selecting a ready revision locks the owning site, retires the previous ready
  revision and marks the selected revision ready in one caller-owned
  transaction.
- A partial unique index on `(tenant_id, site_id)` for `state = 'ready'` closes
  the race at the database boundary.

## Gate sequence

1. **Complete:** commit this dossier, the source inventory, ADR, plan, package
   dossier and canaries while `dotmac-sites` is absent.
2. **Complete:** on Observer at exact commit `9f735b4`, the focused suite was
   RED only because the distribution was absent: 13 passed, 40 failed.
3. **Complete:** service canaries preceded the runtime service; the PostgreSQL
   isolation canary was already present in the Gate 1 commit.
4. **Complete:** a81 was allocated in the same slice as the manifest and
   `si_0001_sites`.
5. **Complete:** the five-table owner and local release contract passed focused,
   full unit/architecture, full disposable PostgreSQL and live-catalog/RLS
   suites on Observer at exact revision `8bf12dd`.
6. **Complete for release only:** PR #284 passed all sixteen required checks;
   exact protected-main revision `8f99413` published and registry-verified
   kernel a81 plus Sites a1. Backoffice composition, adoption and remote
   deployment remain closed until separately proven.

## 2026-08-20 restack and release

Published kernel a77 owns the vendor cohort and cannot be repointed. Sites now
follows media observations a78, content a79 and publishing a80 at a81. Because
the kernel workflow publishes one tip, a81 is also every cohort module's first
installable floor. The earlier `8bf12dd` proof remains valid historical
behavior evidence. Michael directed GitHub CI to validate the restacked
release: PR #284 passed all sixteen required checks and merged as exact
protected-main revision `8f99413`. Release runs `32346291258` and `32350030557`
published, registry-verified and tagged kernel a81 and Sites a1 from that
revision. The Sites registry verification installed the complete
media/content/publishing/Sites composition. That proves artifact and manifest
compatibility, not product adoption; Gate 3, Backoffice composition and remote
publication remain closed.
