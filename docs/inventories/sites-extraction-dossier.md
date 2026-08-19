# Sites extraction dossier

**As of:** 2026-08-19  
**Source mode:** greenfield after six-repository inventory  
**Candidate allocation:** kernel a77, `si`, `sites`, `mod_sites`  
**Release/adoption:** not authorized

No qualifying site-builder exists at the exact coordinates recorded in
[`sites-sources.md`](sites-sources.md). This dossier therefore specifies the
smallest product-neutral owner rather than pretending a landing route is a
production implementation to port.

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

1. Commit this dossier, the source inventory, ADR, plan, package dossier and
   canaries while `dotmac-sites` is absent.
2. On Observer at that exact commit, prove the focused suite is RED only because
   the distribution is absent; record the command and counts.
3. Add service and PostgreSQL isolation canaries before runtime code.
4. Allocate a77 only in the same slice as the manifest and `si_0001_sites`.
5. Implement the five-table owner and local release contract; validate focused,
   full unit/architecture, full disposable PostgreSQL and live-catalog/RLS
   suites on Observer.
6. Keep release, allowlisting, Backoffice composition and remote deployment
   closed until separately authorized.
