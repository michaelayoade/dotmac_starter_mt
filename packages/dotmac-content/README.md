# dotmac-content

`dotmac-content` owns tenant editorial plans, canonical content items,
provider-neutral authored variants, calendar placement, and ordered opaque
creative-file references.

It does not own audiences, recipients, publication requests or outcomes,
provider transport, credentials, stored bytes, actor authorization, people, or
generic tasks. Product assemblies authorize actors and resolve files before
calling this owner. Publishing consumes the immutable `ContentSnapshotV1`
value; it never receives an ORM object.

The package is a tenant-only installable module. Its independent `ct` lineage
owns five tables in `mod_content`, all with forced RLS. Services require an
explicit `TenantScope`, mutate and flush within the caller's transaction, and
never commit or roll back.

The product-first boundary and writer-retirement gates are recorded in
[`EXTRACTION.toml`](EXTRACTION.toml) and the
[content dossier](../../docs/inventories/content-extraction-dossier.md).
