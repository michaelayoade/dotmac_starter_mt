# Compatibility — dotmac-document-rendering

## 0.1 contract

- Python: `>=3.11,<3.14`.
- V1 hashes retain the persistence-free canonical `fingerprint_of` contract.
- The V2 source successor requires Kernel `>=0.1.0a103` for shared
  `dotmac_kernel.semantic_encoding` cv1 domain encoding.
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

## 0.1.0a2 source successor

V2 is additive and versioned independently; V1 golden, validation and hash
behavior remain fixed. The original Billing presentation digest is a producer
semantic identifier, V2 projection digests use cv1 domain separation, and
rendered byte checksums retain `sha256:`. These values are not interchangeable.

The producer-compatible contract accepts absent series version and due date,
opaque string profile versions, complete structured parties and all line
financial/provenance fields. Verified due-date bases retain frozen payment-terms
code/version; unknown/unverified bases may leave that evidence absent. Actual
Billing V1 mapping supports bank and narrative instructions with no link. Future linked facts require an explicitly
frozen owner-qualified HTTPS value; live product settings cannot fill it.

Kernel103 publication does not establish Sub compatibility: Sub's Kernel98
qualification and the candidate renderer release/install-back remain pending.
The source candidate adds no product adoption or migration.

V2 PDF qualification parses bytes strictly in memory with patched `pypdf >=6.19`,
checks structural/page/object/decoded-content/text budgets and matches extracted
page text to the complete ordered projection after whitespace normalization.
The bounded profile limits payload to 16 MiB, total decoded streams to 32 MiB,
extracted text to 2 MiB, pages to 256, traversed objects to 20,000 and nesting
depth to 64. Both uncompressed and supported compressed content must pass; marker-only,
blank, truncated, encrypted, active or external content fails closed. PDF
annotations are outside this bounded profile, including payment links: a frozen
URL remains visible text in PDF. HTML permits only the qualified frozen link
and self-contained safe presentation.

This proof is for a trusted explicitly composed engine, not arbitrary uploaded
PDFs. Text correspondence cannot establish visual fidelity or accessibility.
Decoded-content budgets and patched upstream decompression limits do not
guarantee a hard CPU deadline or eliminate transient allocations. No production
PDF engine qualification, install-back or Sub adoption is claimed.

V2 isolates Decimal canonicalization and financial display from the caller's
precision, rounding, traps and exponent settings. A private precision-16,384
context retains the published Kernel103 cv1 wire algorithm without changing
the caller's context. Finite Decimal coefficients and absolute exponents are
bounded at 4,096; scaled Money integers are bounded at 4,096 digits before the
shared encoder runs. Oversized values produce a typed permanent shape refusal.
The existing document currency scale remains zero through nine minor units.
Equivalent Decimal scales, instant offsets and signed zero Money retain the
same semantic digest and displayed value; no amount is rounded or recomputed
by the renderer.
