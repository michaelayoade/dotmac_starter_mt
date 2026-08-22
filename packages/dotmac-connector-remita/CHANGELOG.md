# Changelog

## 0.1.0a1 — unreleased

- Adds authenticated RRR status polling using the provider's SHA-512 request
  contract and exact declared demo/live hosts.
- Accepts JSON and the provider's JSONP wrapper without logging response
  content.
- Emits stable, deduplicable provider observations while preserving status,
  amount, reference, transaction and date evidence verbatim.
- Deliberately carries no payment-state mapping or product consequence.
