# Changelog

## 0.1.0a2 — unreleased

Declared as a2, not folded into a1. a1 IS published (peeled tag
`dotmac-connector-remita-v0.1.0a1` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`), and the issuance slice below adds
a capability id, per-capability modes and an SPI 1.4 floor — every one of them
inside the manifest digest an installation adopts by. Until this bump the
plugin carried TWO manifests both named `0.1.0a1`, which is worse than an
in-place edit: `accepts_manifest_digest` accepted both, so nothing could see the
collision.

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
- The published SPI-1.3 poll-only manifest is retained as `POLL_ONLY_MANIFEST`,
  so an installation pinned to digest
  `84e651b5d41ded929e7ef96717c17174383b6ca4f05763f9954e2c79a081e3fc` resolves to
  a known contract. `docs/inventories/released-manifest-digests.json` records
  that digest and `make manifest-digest-check` fails if it ever moves.

## 0.1.0a1 — released

Peeled tag `dotmac-connector-remita-v0.1.0a1` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`.

- Adds authenticated RRR status polling using the provider's SHA-512 request
  contract and exact declared demo/live hosts.
- Accepts JSON and the provider's JSONP wrapper without logging response
  content.
- Emits stable, deduplicable provider observations while preserving status,
  amount, reference, transaction and date evidence verbatim.
- Deliberately carries no payment-state mapping or product consequence.
