# Changelog

## Unreleased

- Adds one DELIVERY capability, `payments.reference.issuance.v1`: ask Remita to
  mint one RRR for one product-owned order. Payment status stays POLL; Remita
  has no push channel, so no status-push path is invented.
- Ported from `dotmac_erp:app/services/remita/client.py`: the SHA-512 request
  contract `sha512(merchantId + serviceTypeId + orderId + amount + apiKey)`, the
  `remitaConsumerKey=…,remitaConsumerToken=…` header form, the `paymentinit`
  path, and the seven-field string body whose optional fields degrade to `""`.
  The issuance order is deliberately kept in its own function, separate from the
  status order (`rrr + apiKey + merchantId`), and both are pinned by tests —
  ERP ships no test for either.
- The amount is formatted once and that single string is used for both the
  payload and the hash, matching the source; hashing a differently formatted
  amount is the failure this pins.
- Money is exact `Decimal` quantized to the exponent the product declares. A
  binary floating-point amount is refused, and an amount finer than the currency
  is refused rather than rounded.
- The settlement currency is declared by the installation and stated by the
  command; a mismatch is refused. Remita's payload carries no currency field, so
  the alternative is assuming naira by omission.
- No idempotency header is sent, because Remita accepts none. Its only natural
  key is `orderId`, which the connector carries from the product verbatim and
  never mints — so a retry cannot issue a second reference for one obligation.
- Five typed outcomes over the engine's four statuses: `025` with an RRR is
  success; `025` without one and a `021` duplicate order are
  RECONCILIATION_REQUIRED; every other provider code is a TERMINAL decline that
  never retries; a timeout after send is ambiguous and only a connect-phase
  failure is retryable. The RRR is captured as evidence wherever the provider
  returned one.
- `validate_connection` performs no provider call on an issuance binding: the
  only issuance call there is mints a reference.
- The published SPI-1.3 poll-only manifest is retained as a historical manifest
  so an installation pinned to it still resolves. **The release lane must cut a
  new version before publishing:** this changes the manifest of an
  already-tagged version, and a published manifest digest must not move.

## 0.1.0a1 — released

- Adds authenticated RRR status polling using the provider's SHA-512 request
  contract and exact declared demo/live hosts.
- Accepts JSON and the provider's JSONP wrapper without logging response
  content.
- Emits stable, deduplicable provider observations while preserving status,
  amount, reference, transaction and date evidence verbatim.
- Deliberately carries no payment-state mapping or product consequence.
