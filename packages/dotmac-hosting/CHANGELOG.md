# Changelog

## 0.1.0a1 — Unreleased

- Add the tenant-only hosting-service aggregate, immutable specification and
  desired-state revisions, business command/outcome evidence, panel observations,
  reason-scoped suspension locks, retention holds and durable attention.
- Publish provider-neutral `hosting.account.v1` contracts, deterministic fake
  and conformance kit for its exact six-operation lifecycle family.
- Keep provider observations evidential; only reconciliation changes lifecycle
  state. Guard irreversible termination with exact-content approval and a fresh
  retention-hold check.
- Keep transport attempts, backoff and dead-lettering in Integrator. Defer
  mailbox lifecycle; V1 observes aggregate mailbox count only.
- Make provider deliveries closed, self-contained and secret-free; observation
  poll inputs are typed and uncorrelated facts remain immutable while local
  reconciliation derives their service correlation.
- Assign specification versions under serialization and enforce their previous
  digest plus all service/desired specification references with composite FKs.
- Treat suspension/restoration delivery as deferred. Independent observations
  confirm pending lifecycle states and append final outcomes for every pending
  reason; reason locks freeze their allowed restorers when opened.
- Make retention-hold placement/clear and termination replay-safe through the
  kernel idempotency owner. The online role cannot delete hosting aggregates.
- Publish structural package rank and closed current-version change rules;
  derive change direction and refuse review-required/incomparable changes.
- Freeze provider correlation as one assign-once binding/account pair and
  require command-owned operation correlation before that pair exists.
- Ingest the released final `approval.approved` event as immutable local evidence, keep approval
  internals out of provider payloads, and complete deferred termination only
  from an independent terminated observation.
- Pin service identity and legal transitions in PostgreSQL, receipt late
  retention holds after termination as urgent refusals, and audit every
  customer-impacting decision with a typed actor.
- Revoke direct aggregate updates from the online role and route every service
  mutation through one tenant/version/evidence checked database function.
- Finalize package and inverse suspension/restoration commands with immutable
  applied, superseded or failed outcomes; keep earlier deferred evidence.
- Bind local approval evidence to a trusted source-event identity, receipt
  hold-blocked termination, and return audited typed refusals for missing or
  wrong-source retention-hold clear requests.
