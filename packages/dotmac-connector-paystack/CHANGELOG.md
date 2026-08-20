# Changelog

## 0.1.0a1

- Verify Paystack HMAC-SHA512 signatures over exact request bytes with a bounded
  previous-secret rotation slot.
- Translate `charge.success` into exact amount, fee, currency and raw provider
  status observations without product metadata or financial consequences.
- Keep unsupported and malformed verified events as record-only transport
  evidence.
- Declare ingress-only execution and deny all external egress.

