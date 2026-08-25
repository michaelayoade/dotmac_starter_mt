# Changelog — dotmac-inbox

## 0.1.0a1 — 2026-08-11

The first release: the channel trait registry, the lifecycle, the threading and
deduplication rules, and the three tables. No routers yet — those land with the
first adopter's surface, so the FastAPI dependency arrives with the code that
needs it.

### Added
- `channels` — `ChannelSpec` and the four traits the core reasons about
  (`AddressForm`, `Transport`, `ThreadIdentity`, `MessageIdScope`), as a
  declaration registry (ADR-0008 applied to a transport vocabulary). Replaces
  the hardcoded channel-name sets both source products keep in Python and, in
  CRM's case, inside a partial unique index predicate.
- `lifecycle` — `Status` (4, closed), `Direction`, a transition table, the
  `is_open` predicate, and the product-declared status-reason registry.
- `threading` — `thread_key` and `dedup_key`, the two rules that read the
  traits. Pure functions over a declaration and a payload: no session, no
  models, exhaustively testable across every trait combination.
- `models` — `InboxConversation` and `InboxMessage` in `mod_ibx`, tenant-scoped
  with composite `(tenant_id, id)` references throughout. `InboxMessage.observation_id`
  points back at the kernel observation an inbound message was derived from.
- Migration `ib_0001_conversations`, the lineage root.
- Ledger allocation `mod_ibx` / prefix `ib` / branch `inbox` in the kernel
  (0.1.0a41, which is this package's floor).

### Notes
- `MessageIdScope.ACCOUNT` deliberately diverges from Sub, which deduplicates
  every channel globally. Declaring a Meta or WhatsApp channel as ACCOUNT scope
  — which is what it is — will ADMIT messages Sub currently rejects. That is a
  correctness fix, and `EXTRACTION.toml`'s `shadow_and_drift` requires it be
  measured in shadow rather than discovered in production.
- The observation ledger and the connected-account registry are **not here**.
  They are `dotmac_kernel.inbound` / `.inbound_models` (kernel 0.1.0a41), because
  admitting a provider fact needs `dotmac_kernel.idempotency` (ADR-0014, hard
  rule 21) and a registry that consent, delivery and any conversation module all
  sit beside. The pointer runs consequence → fact: the kernel may not reference a
  module's schema.
- Status `resolved_to_ticket` is deliberately absent. CRM models it as a fifth
  status; it is a *reason* for `resolved`, and products declare it as one.
