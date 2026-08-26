# dotmac-domains-contracts compatibility

## Public surface

Only names in `dotmac_domains_contracts.__all__` are public.

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact `dotmac-domains` release and `dns.authoritative.v1` declaration |
| `CAPABILITY_CONTRACTS` | immutable tuple containing the authoritative-DNS snapshot |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; suite catalogues own cross-owner dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `DNS_AUTHORITATIVE` | `dns.authoritative` at schema version 1 |
| `__version__` | installed catalogue version |

The wheel has no connector entry point or `ModuleManifest`.

## Compatibility rule

The contract code is unversioned and `schema_version` produces the public
capability id. Changing an operation, resource/requirement kind, record shape,
check, endpoint, schema byte or evidence classification requires a new schema
version. Consumers pin exact contract and schema digests.

Version one fixes these provider-neutral obligations:

- `zone`, `recordset`, and `observation` remain schema resource kinds;
- desired requirements can name MX, SPF, DKIM, DMARC, autodiscover,
  autoconfig, MTA-STS, TLS-RPT and PTR where applicable;
- observations preserve provider-assigned nameservers and exact RRset facts;
- partial application names outstanding resources instead of claiming success;
- TLS evidence covers HTTPS reachability/redirect, certificate validity and
  hostname, MTA-STS policy and TLS-RPT URI validity; and
- endpoints and held credentials are installation configuration, never
  duplicated into signed operation input; and
- all result evidence is public/non-secret.

Provider-specific resource identifiers remain opaque facts. Provider choice,
DNS intent, drift decisions and repair policy do not belong to this catalogue.
