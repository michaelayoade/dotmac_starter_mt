# Changelog

## 0.1.0a1 — UNRELEASED

- Adds the tenant-only `mod_inbox` lineage with forced RLS from revision 1.
- Owns conversation threads, ordered messages, lifecycle transitions, channel
  traits, provider-neutral message identity, and per-operator read cursors.
- Makes thread creation and exact message redelivery idempotent through the
  kernel conflict savepoint; a reused message key with different content is a
  typed conflict rather than a replay.
- Keeps connector transport, delivery, identity resolution, assignment,
  routing, attachments, realtime, templates, and product consequences outside
  the module.
