# dotmac-domains-contracts

Immutable, provider-neutral authoritative DNS and TLS requirement contracts.
The wheel owns one independently bindable capability:
`dns.authoritative.v1`.

The underlying `CapabilityContractSnapshot.capability_code` is the unversioned
`dns.authoritative`; `schema_version=1` produces the public id declared by the
`dotmac-domains` Product Manifest. The contract exposes Integration SPI 1.2's
`plan`, `apply`, `observe`, and `cancel` operations with exact canonical Draft
2020-12 schemas.

`zone`, `recordset`, and `observation` are resource kinds inside those schemas,
not replacement engine operations. One desired-state request can state the
applicable MX, SPF, DKIM, DMARC, autodiscover, autoconfig, MTA-STS, TLS-RPT and
PTR requirements. Evidence reports assigned nameservers, exact observed
recordsets, outstanding resources, partial completion, and per-host TLS facts
including reachability, redirects, certificate validity and the MTA-STS/TLS-RPT
checks.

The catalogue never decides which record should exist, merges provider state
into desired state, or deletes an undeclared record. It contains no DNS client,
provider name, dynamic plugin loading, network I/O, persistence, migration,
retry engine or secret value. API credentials are held installation
configuration supplied separately by Integrator and never appear in signed
operation inputs; no output contains a credential or secret reference.

## Published data

- `PRODUCT_MANIFEST` — owner `dotmac-domains`, release and public capability id.
- `CAPABILITY_CONTRACTS` — the immutable DNS contract tuple.
- `CAPABILITY_SCHEMAS` — exact self-contained schema documents.
- `CAPABILITY_COMPOSITIONS` — empty; cross-owner dataflow belongs elsewhere.
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty for this owner catalogue.
- `DNS_AUTHORITATIVE` — the named contract snapshot.

See `COMPATIBILITY.md` and `EXTRACTION.toml` for the supported surface and the
greenfield-after-inventory ruling.
