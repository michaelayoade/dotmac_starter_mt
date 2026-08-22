# ADR-0035: Sites own local revisions, not hosting

**Status:** Accepted for candidate implementation; release and adoption gated  
**Date:** 2026-08-19  
**Decision owner:** Michael  
**Evidence:** [`sites-sources.md`](../inventories/sites-sources.md) and
[`sites-extraction-dossier.md`](../inventories/sites-extraction-dossier.md)

## Context

The marketing suite needs editable websites and a durable artifact that can be
served locally or deployed remotely. A six-repository audit found landing
routes, welcome-page settings and ordinary application templates, but no
production-used site builder with versioned pages, navigation, redirects and
immutable releases. Product-first extraction therefore has no qualifying code
source. Greenfield implementation is permitted only because that absence is
recorded at exact repository revisions and guarded by a Backoffice adopter
canary.

The nearest owners must remain separate. Files own stored bytes. Forms own form
definitions and submissions. Publishing owns publication requests, schedules,
attempts and outcomes. Integrator and hosting connector plugins own credentials,
provider identity, DNS/certificates, remote execution, retries, checkpoints and
transport evidence. A remote deployment cannot become the only copy of a site.

## Decision

`dotmac-sites` is the one owner of tenant-scoped site/page identity, immutable
page revisions, immutable composed site revisions, navigation, SEO, redirects
and which site revision is eligible for release.

Revision 1 has exactly five tenant tables:

1. `sites` — site identity and its active/archived lifecycle;
2. `pages` — stable page identity within a site;
3. `page_revisions` — append-only title/body, SEO and opaque file/form refs;
4. `site_revisions` — one immutable composed snapshot and its readiness state;
5. `site_revision_pages` — append-only membership of exact page revisions and
   routes in a composed site revision.

Every row carries `tenant_id UUID NOT NULL`; all same-module relationships are
tenant-composite; every table has RLS enabled and forced. Page revisions and
site-revision membership are append-only at the database boundary. A site
revision's content and digest are immutable; only the guarded
`draft -> ready -> retired` readiness transition may change. The service locks
the owning site while replacing a ready revision, and a partial unique index
permits at most one ready revision per site.

The package exports a versioned, deterministic `SiteReleaseV1` containing the
exact site revision, ordered page snapshots, navigation, SEO, redirects and
digest. It is usable by a local renderer without a network dependency. It is
also the only permitted input to a future remote-deployment adapter.

`dotmac-sites` does **not** write a publication outbox or record delivery
outcomes. A Backoffice assembly adapter will pass the release value to
`dotmac-publishing`, which owns intent and outcomes and writes the durable
outbox. Integrator then selects the bound hosting connector and owns transport.
That remote path is a separate gate: it may not serialize the site artifact
into unrelated title/body fields or otherwise launder an untyped payload.

## Contract rules

- Paths are local absolute paths: no scheme, authority, query, fragment,
  duplicate slash, `.` or `..` segment.
- Every composed release contains exactly one `/` route, has unique routes and
  page identities, and references exact page revisions from the same site.
- Navigation destinations must name a route in the snapshot.
- Redirect sources are unique, cannot shadow a page route, cannot redirect to
  themselves and use only `301`, `302`, `307` or `308`.
- File and form references are opaque UUIDs with no sibling-module foreign key.
- The release digest uses canonical serialization and covers ordered pages,
  content, navigation, redirects and SEO. Reordering therefore changes the
  digest; rebuilding the same revision does not.
- Site archive is terminal. A ready site revision may only retire; a retired
  revision is terminal. Idempotent state reassertion is allowed.

## Consequences

The first free kernel allocation after the published vendor a74-a77 cohort is
media observations a78, content a79 and publishing a80, followed by Sites a81:
owner/short code/branch `sites`, prefix `si`, schema
`mod_sites`. The allocation lands only with the manifest and root migration.

Backoffice is the first candidate adopter, but it is not a contract consumer
until it exact-pins a release, repeats the empty-owner census, composes its own
lineage and serves the module-owned ready snapshot. Sub remains a later,
independent candidate. Neither publication nor adoption is authorized by this
ADR.

### Amendment — 2026-08-20: verified publication before adoption

Michael authorized the restacked media/content/publishing/Sites cohort for
release. Sites is therefore release-allowlisted at the first installable cohort
kernel, a81. Publication does not satisfy the Backoffice composition, local
snapshot, typed publishing-adapter or Integrator evidence gates above and does
not make Backoffice or Sub a contract consumer.
