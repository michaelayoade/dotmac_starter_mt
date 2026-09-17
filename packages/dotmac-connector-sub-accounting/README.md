# dotmac-connector-sub-accounting

Sub invoice-accounting-sync feed, POLL-only observation connector for the
independently deployed Dotmac Integrator. It pages
`GET https://selfcare.dotmac.io/api/v1/invoices/accounting-sync/v2`
(`X-API-Key` authenticated) through an engine-owned cursor and translates
each item into a provider-neutral `invoices.accounting_sync.observation.v1`
fact.

It owns no database, product identity, allocation, delivery target or
checkpoint. In particular it never constructs the destination-bound request:
`organization_id` (the binding's configured destination org — an
operator-controlled mapping, never derived from Sub's data) and the delivery
`idempotency_key` are supplied downstream by the generic Integration engine
and the destination `ObservationPortClient`, never by this connector. Sub's
`account_id` is never mapped onto ERP's `organization_id` — conflating a
provider identifier with a destination scope is exactly the anti-pattern this
fleet's architecture forbids.

## Observation shape

| field | source |
|---|---|
| `source_invoice_id` | Sub feed item `id` (UUID, unmodified) |
| `source_account_id` | Sub feed item `account_id` (UUID, unmodified — never a destination scope) |
| `source_updated_at` | Sub feed item `updated_at`, exact string |
| `source_kind` | Sub feed item `status`, verbatim |
| `disposition` | Sub feed item `disposition`, verbatim |
| `source_total_amount` / `source_currency` | Sub feed item header money fields |
| `source_issued_at` / `source_due_at` | optional, when present |
| `issues` | Sub feed item `issues`, `line_id` renamed to `source_line_id` |
| `contract_version` | Sub feed item `contract_version`, must equal `invoice-accounting-sync.v2` |
| `projection_fingerprint` | computed here — see "Fingerprint risk" below |

## Cursor

Canonical compact JSON `{"after_id":"<uuid>","after_updated_at":"<iso datetime>"}`.
A cursor carries exactly both fields or neither; a partial cursor is a
connector-internal error. `limit` is always sent; `updated_since` only when
configured. Pages are validated for strictly-increasing
`(updated_at, source_invoice_id)` positions — a non-monotonic page is a
malformed-response error, not silently accepted. An empty page (including the
first poll) returns no events and leaves the cursor unchanged, mirroring
`dotmac-connector-nira`'s convention. One request per `poll()` call; no
internal retry or sleep.

## Business-rule mirroring

A blocked observation with no issues, or a non-blocked observation carrying
issues, is refused before it is ever emitted — mirroring the consistency rule
ERP's own `RecordInvoiceSyncOutcome` construction enforces, as defense in
depth rather than a replacement for it.

## Fingerprint risk — read before trusting `projection_fingerprint`

`projection_fingerprint` is meant to match, byte-for-byte, what ERP's own
shadow mapper independently computes from the same Sub source record. The
ERP worktree was not reachable from the session that built this connector, so
the algorithm in `mapping.py` (normalized typed record -> recursive
Decimal/datetime/enum-safe conversion -> sorted compact JSON -> SHA-256,
lowercase hex) is a best-effort reconstruction from a description, not a
verified port. See that module's docstring for the exact points of
disagreement risk (which fields are included, Decimal stringification,
datetime stringification). Reconcile this against ERP's real algorithm before
relying on the fingerprint for drift detection.

## Manifest

One capability, `invoices.accounting_sync.observation.v1`, POLL only. One
required secret binding, `sub_api_key` (an `ApiKey` scoped to
`integration:accounting_sync:read` on the `dotmac_sub` side; provisioning the
value is a separate operational step). Egress is exactly
`selfcare.dotmac.io` — Sub's registered production hostname. **This is a
production coordinate declared in code, not an activation**: building the
manifest correctly is still useful for review, but this connector must not be
bound or enabled without a separate explicit go-ahead.

`claims_contract_digest` is explicitly `None` on the one capability: the
parallel ERP-side slice (1b) has not yet published its capability contract,
so there is no real digest to pin. See the `TODO` in `plugin.py`.
