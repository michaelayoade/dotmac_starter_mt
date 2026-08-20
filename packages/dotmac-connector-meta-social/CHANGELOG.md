# Changelog

## 0.1.0a1

- Add exact-byte Meta app signature verification with bounded secret rotation.
- Add subscription challenge handling.
- Normalize Facebook Messenger, Instagram DM, Facebook comment and Instagram
  comment batches into independent provider events.
- Keep echoes, unsupported changes and malformed items as record-only transport
  evidence instead of silently dropping them.
- Declare exact secret bindings and deny all external egress.
