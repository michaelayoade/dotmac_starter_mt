# Changelog

## 0.1.0a1 (unreleased)

- Initial SMTP delivery adapter (S11, P-1), extracted product-first from
  Academy's `email.py` SMTP send. Never published; no release authorized.
- S8 (K-4): `SmtpAdmissionRefused` is now a kernel `DeliveryDeferred`, so an
  admission refusal (for example a mail gate holding sends) reschedules the
  outbox event without charging a relay attempt, bounded by the kernel's
  `RelayPolicy.max_deferrals`, instead of being retried as a failure. Still no
  receipt. Needs the kernel release containing S8; declared floor unchanged.
- S10 (K-8): `SmtpOutcomeUnknown` is now a kernel `DeliveryOutcomeUnknown`, so a
  reply lost after DATA is never retried by the relay transport (it could
  double-send): the dispatch goes `dead`, the outbox event is dead-lettered at
  once and an audit row records it for the operator. Needs the kernel release
  containing S10; declared floor unchanged.
