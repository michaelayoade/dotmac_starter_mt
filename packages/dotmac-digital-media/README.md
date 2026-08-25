# dotmac-digital-media

Tenant-only reusable digital asset management for images, video, audio and rich
media.

The package owns stable asset identity, immutable source revisions, descriptive
metadata, extractor observations, libraries/collections/classification,
versioned rights evaluation, rendition provenance, media grants/review, usage
observations and append-only evidence. It stores opaque file UUIDs and supplied
checksums; `dotmac-files` remains the byte owner.

It deliberately does not own provider clients, scanners/transcoders, editorial
context, document embeds, publication outcomes, external advertising metrics,
search indexes, consent validity or Records retention/disposition.

The reference Starter builds and proves this package but does not compose its
lineage. A product becomes a consumer only after pinning a released version,
installing the tenant plane in its own database and retiring its prior reusable
asset writer.
