# Changelog — dotmac-connector-mono

## 0.1.0a1 — unreleased

- Adds product-first Mono v2 transaction polling from ERP's production client.
- Preserves lowest-denomination amounts and provider direction without making
  banking or reconciliation decisions.
- Rejects off-origin or wrong-account pagination before the held API secret can
  follow it.
