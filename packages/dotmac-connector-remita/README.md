# dotmac-connector-remita

First-party Remita transport plugin for the independently deployed Dotmac
Integrator. It polls the authenticated RRR status endpoint and emits
`payments.reference.status.observation.v1` facts, and it carries one outbound
command — `payments.reference.issuance.v1`, which asks Remita to mint one RRR
for one product-owned order.

The connector carries the provider status verbatim. It does not call a status
“paid”, “pending”, or “failed”; the receiving product owns that decision. It
also owns no RRR lifecycle, biller policy, source linkage, ledger, journal,
checkpoint, retry engine, database session, or product row.

## Configuration

- `merchant_id`: provider merchant identifier.
- `environment`: exactly `demo` or `live`; both hosts are declared in the
  manifest and no configured host is accepted.
- `rrrs`: one to 100 provider references whose current facts are polled
  (status capability only).
- `settlement_currency`: the ISO-4217 code this merchant settles in, with no
  default (issuance capability only). Remita's issuance payload carries no
  currency field, so a command in any other currency is refused rather than
  minted in this one.
- `api_key`: held secret binding; the value is materialized by Integrator for
  one call and never stored by this package.

## Issuance

`payments.reference.issuance.v1` takes `order_id`, `service_type_id`, `amount`,
`currency`, `currency_minor_units`, `payer_name`, `payer_email` and the optional
`payer_phone` and `description`. The amount is exact `Decimal`, quantized to the
exponent the product declares and refused rather than rounded when it does not
fit; the single formatted string is used for both the payload and the SHA-512
request hash, because they must be byte-identical.

`order_id` is the PRODUCT's stable opaque string and the connector never mints
one — Remita's only natural key is `orderId`, so a stable one is what makes a
retry safe. A `021` duplicate order comes back as `RECONCILIATION_REQUIRED`
rather than a retry or a dead-letter, because it proves an earlier attempt
minted a reference that the status capability can now find.

A decline is terminal and never retried. A timeout after the request was sent is
`RECONCILIATION_REQUIRED`, because Remita may have minted a reference the
product has never seen. Only a connect-phase failure is retryable.

Payment status stays POLL. Remita has no push channel, so there is no
status-push path to add.
