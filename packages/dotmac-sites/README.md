# dotmac-sites

`dotmac-sites` owns tenant site/page identity, immutable page revisions,
immutable composed site revisions, navigation, SEO, redirects and which
revision is ready for release.

It does not own stored bytes, form submissions, publication schedules or
outcomes, hosting, provider identity, credentials, domains, DNS, certificates,
analytics or Leads. File and form references are opaque. A local renderer
consumes the immutable `SiteReleaseV1`; a later assembly adapter may pass that
same value to `dotmac-publishing`, while Integrator alone performs remote I/O.

The package is tenant-only. Its independent `si` lineage owns five tables in
`mod_sites`, all with forced RLS. Revision evidence is protected by database
immutability triggers. Services require an explicit `TenantScope`, mutate and
flush within the caller's transaction, and never commit or roll back.

The greenfield proof and adoption gates are recorded in
[`EXTRACTION.toml`](EXTRACTION.toml) and the
[sites dossier](../../docs/inventories/sites-extraction-dossier.md).
