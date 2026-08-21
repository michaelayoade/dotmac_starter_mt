# dotmac-connector-remita

First-party Remita transport plugin for the independently deployed Dotmac
Integrator. The first slice polls the authenticated RRR status endpoint and
emits `payments.reference.status.observation.v1` facts.

The connector carries the provider status verbatim. It does not call a status
“paid”, “pending”, or “failed”; the receiving product owns that decision. It
also owns no RRR lifecycle, biller policy, source linkage, ledger, journal,
checkpoint, retry engine, database session, or product row.

## Configuration

- `merchant_id`: provider merchant identifier.
- `environment`: exactly `demo` or `live`; both hosts are declared in the
  manifest and no configured host is accepted.
- `rrrs`: one to 100 provider references whose current facts are polled.
- `api_key`: held secret binding; the value is materialized by Integrator for
  one call and never stored by this package.

The first slice intentionally excludes RRR issuance. Issuance is a
request/response command whose returned reference must be delivered durably to
the product; pretending it is a fire-and-forget delivery would lose the only
copy of that response.
