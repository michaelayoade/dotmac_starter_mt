# Compatibility — dotmac-document-rendering

## 0.1 contract

- Python: `>=3.11,<3.14`.
- Kernel: `>=0.1.0a86`, for the persistence-free canonical `fingerprint_of`
  surface and typed tenant/platform scope values.
- Accepted input: `InvoiceDocumentFactV1.contract_version == 1`.
- Semantic output: `DocumentProjectionV1.projection_contract_version == 1`.
- Built-in media types: `text/html`; `application/pdf` when a local `PdfEngine`
  is explicitly supplied.

The package has no persistence plane and composes unchanged in tenant and
platform contexts. Scope affects the surrounding transaction and storage
owners, not the semantic projection digest.

Template codes and versions are immutable identifiers. Assemblies may add new
template artifacts and profile bindings, but must never change an existing
artifact in place. A renderer version must change when its semantic behavior
changes; layout-only variation may change bytes while preserving the canonical
projection.

The package does not guarantee byte-for-byte PDF identity across different PDF
engines. It guarantees complete bytes, checksum and length integrity, declared
renderer provenance, and invariant semantic projection.
