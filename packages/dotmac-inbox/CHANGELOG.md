# Changelog

## Unreleased — 0.1.0a2+dev

- Adds an explicit typed snooze-until-reply lifecycle state.
- Adds conflict-safe late binding for transport observation references.
- Adds opaque product-supplied thread and message identities without making
  product principals or provider transport part of Inbox authority.
- Adds typed tenant-scoped conversation and message reads with closed filters,
  bounded opaque keyset cursors, and deterministic UUID tie-break ordering.

## 0.1.0a2 — 2026-08-23

- Adds typed `import_conversation`, `import_message` and `import_read_state`
  adoption seams that preserve source UUIDs and timestamps without replaying
  live lifecycle consequences.
- Makes only an exact historical replay idempotent. Reusing a source id,
  canonical thread, message key or actor cursor for different facts fails as a
  typed conflict.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32479035261`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Adds the tenant-only `mod_inbox` lineage with forced RLS from revision 1.
- Owns conversation threads, ordered messages, lifecycle transitions, channel
  traits, provider-neutral message identity, and per-operator read cursors.
- Makes thread creation and exact message redelivery idempotent through the
  kernel conflict savepoint; a reused message key with different content is a
  typed conflict rather than a replay.
- Keeps connector transport, delivery, identity resolution, assignment,
  routing, attachments, realtime, templates, and product consequences outside
  the module.
