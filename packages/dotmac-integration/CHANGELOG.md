# Changelog — dotmac-integration

## Release state — read this before pinning

**Five versions have been released. Pin `0.1.0a5`.** Tags
`dotmac-integration-v0.1.0a1` … `-v0.1.0a5`, from `1b1d62b`, `aaa3b54`,
`b14f66e`, `306a40e` and `7828697`.

`0.1.0a5` is the version the first connector programme pins. It adds SPI 1.2's
provider-neutral verification evidence while retaining a4's declared
at-most-once dependency and persisted-exception hardening.

**Do not pin `0.1.0a1` or `0.1.0a2`.** Their discovery path renders a
connector's own exception message into `ModeContractError` and chains it as
`__cause__`, so any secret a connector interpolates into its error reaches the
operator's boot log, the traceback and any handler using `exc_info`. `0.1.0a3`
fixes it.

**`0.1.0a3` still carries the persisted-exception-text defect**, which `a4`
fixes: `dispatch.invoke` wrote a connector's exception message into
`error_detail`, a column `execution` persists — so a connector that
interpolated a materialized credential into its own exception stored it, where
it outlives the request, the process and the credential's rotation. Prefer a4.

`0.1.0a5` was published, installed back from the private index, composed and
tagged on 2026-08-17 from `7828697`. Its publication-ledger row was retired in
the immediately following release-record change.

This section exists because the `0.1.0a2` heading previously carried a date and
read exactly like a release entry while being unreleased — and a changelog that
misdescribes what is installable is how a consumer comes to pin something that
does not exist, or something it should not. It has since been wrong in the other
direction twice: a2 and then a3 were each tagged while this preamble still called
them unreleased. The table of tags and commits above is what a reader should
trust, because `git ls-remote --tags` checks it.

Everything under a `## 0.1.0a2` heading below shipped in that tag. There are
four such headings because a2's content landed across four merges (SPI 1.1,
receipt delivery, retention, the type gate) and each wrote its own section
before the version was cut. They are not four releases.

Nothing in this file is a publication claim except this section.

## 0.1.0a6 — unreleased

### SPI 1.2 and approval-bound provisioning

- Raises the kernel floor to `>=0.1.0a69`; PROVISION admission consumes the
  canonical product-owned capability contract introduced there and cannot run
  against a kernel that does not parse and verify that contract.
- Adds the closed `PROVISION` mode. Its typed handler owns
  `plan`/`apply`/`observe`/`cancel`; provider-specific operation names and
  arbitrary process execution are not expressible through the SPI.
- Requires each PROVISION capability to carry its product owner's exact kernel
  capability-contract snapshot, including all four input/output schema
  identities and canonical Draft 2020-12 schema bytes. The connector manifest
  digest now binds the complete canonical contract and schemas (configuration,
  endpoints and checks included), while the <=1.1 digest path remains
  byte-for-byte compatible.
- Re-exports `CapabilityContractSnapshot` and `CapabilitySchemaDocument` from
  the SPI surface. Connector source imports Integration plus its exact owner
  catalogue, never Kernel directly; Kernel remains the transitive value-grammar
  dependency owned by those two contracts.
- Adds the provider-neutral `verify_capability_configuration` gate for binding
  activation and provisioning intake. It validates typed values, semantic
  formats, endpoints, required/unknown fields and held secret references without
  a product/provider catalogue or branch, returning only value-free evidence.
- Pins the exact Release Catalog connector artifact digest on installations.
  Draft/adoption validate an explicit canonical digest; PROVISION activation
  refuses a missing pin, while legacy non-PROVISION rows remain nullable.
- Adds durable, collision-detecting provisioning commands, dependency-ordered
  steps, bounded retries and explicit `reconciliation_required` outcomes for
  ambiguous provider effects. Plan/apply/observe/cancel each carry an
  independently replayable command id; effect idempotency keys stay stable
  across retry attempts.
- Adds required provider-neutral `capability_instance_ref` identity for new
  bindings and all four commands, permitting several instances of one
  capability while binding template/body fingerprints, durable operations and
  immutable receipts to the exact instance. Legacy binding rows are left
  unassigned and fail closed until explicitly assigned while disabled.
- Makes all four owner operation schema pairs executable. PLAN validates every
  step input before command mutation and binds the validated, repr-suppressed
  result-evidence digest into its immutable receipt. OBSERVE and CANCEL derive
  immutable targets solely from input-schema-declared fields on the original
  durable APPLY step, validate them before claim/I/O, validate successful
  output evidence (including `CANCELLED`/`NOT_FOUND` cancellation outcomes),
  and receipt only the canonical evidence digest plus its schema-declared
  public, non-secret projection.
- Enforces declared standard JSON Schema formats during every operation-input
  and successful-evidence validation. A declared email, URI or date-time format
  is a runtime contract rather than an annotation invalid data can pass before
  provider I/O.
- Refuses unverified, expired, stale or hash-mismatched approvals before
  connector I/O. Invocation is the middle of a prepare/invoke/settle boundary,
  so no database session crosses provider I/O.
- Settled PLAN commands produce an immutable module receipt. APPLY locks and
  corroborates that local receipt against the verified approval evidence, the
  exact connector installation/artifact/configuration revision, the complete
  product-owned capability contract, and the module-owned execution-policy
  digest before it creates an operation.
- Binds cross-capability dependencies without predicting future hashes: the
  approved command template carries sorted prerequisite binding ids plus exact
  public-source/declared-target evidence mappings, while the later signed
  command must pin each upstream operation's exact latest succeeded receipt.
  The module locks and verifies those operations before it creates downstream
  state. Apply preparation re-locks the exact operations and receipt chains,
  injects only schema-declared `public_non_secret` evidence into a copied target
  input, validates it against the target schema and records its digest without
  changing the signed command. Connector output is validated against the held
  schema; receipts retain its public projection and full-value digest, never
  arbitrary values.
- Adds `ig_0008_provisioning`: five platform-only tables and immutable,
  hash-chained receipts carrying exact connector, manifest, artifact,
  configuration, approval, plan and capability-instance provenance. Step
  receipts snapshot the step key and provider operation reference;
  operation-level receipts leave both null rather than inventing a fake step
  identity.

## 0.1.0a5 — released 2026-08-17

### SPI 1.2 — provider-neutral verification evidence

- Adds `VerificationResult`, containing only an acceptance bit and zero or more
  positions in a connector's ordered active-secret set. No secret name,
  reference or value is representable on the type.
- Adds a provider-neutral verification observer to the ingress engine so an
  assembly can count secret-rotation traffic without importing a connector or
  branching on a provider. Observer failure is non-fatal telemetry failure.
- Keeps SPI 1.0/1.1 ingress plugins source-compatible: a boolean verification
  result is adapted to `VerificationResult` with no secret positions.
- Refuses any other truthy object. Before this release, a plugin accidentally
  returning an object instead of `bool` authenticated the request because the
  engine relied on Python truthiness.
- Raises `CURRENT_SPI_VERSION` from 1.1 to 1.2. No persistence or migration
  changes are included.

## 0.1.0a4 — released 2026-08-16

Written against a declared-but-unreleased `0.1.0a3`, which was cut from
`b14f66e` while this change was open. The ledger work moved up to a4 rather than
being back-dated into a release it was never in — and `ig_0007` stayed unreleased
rather than joining a3's digest entry in
`tests/architecture/test_released_migrations.py`.

### `idempotency_ledger.v1` is declared, and verified at deploy

Declares the kernel prerequisite this module has consumed since `0.1.0a1`.

`dotmac_integration.idempotency.run_effect_once` delegates at-most-once
execution to `dotmac_kernel.idempotency.execute_once_platform` (hard rule 21,
ADR-0014 — the ledger has exactly one owner and it is not this module), so
`public.platform_idempotency_records` is written at REQUEST time. Nothing in
`ig_0001`..`ig_0006` creates it, so the dependency existed only inside a
function body: an adopter running its own lineage that never ran the kernel's
— ERP hosts `public.tenants` itself and structurally cannot run kernel `0001` —
installs this module, passes every gate it has, migrates cleanly, and raises
`UndefinedTable` on the first guarded delivery. No test could have caught it
before kernel `0.1.0a66`, because there was no name to declare. Same defect,
same week, as `dotmac-numbering` `0.1.0a1`.

#### Changed

- `ModuleManifest.requires` gains `idempotency_ledger.v1`. COMMON rather than
  `platform_requires`: this module owns one plane, installed atomically, so
  there is no selection under which the requirement lapses — and a plane list
  is unresolvable for an atomic module anyway, since `resolve_depends_on` reads
  one only via `module=`, which needs a `ModulePlaneSelection` such a module may
  not have.
- Kernel floor raised to `>=0.1.0a66`, the release that published the name.
  a58..a65 HAVE the tables — kernel `0018` created them — but do not know the
  name, so `validate_prerequisites` refuses the manifest at import. **This is a
  visible break for a consumer pinned to a released a1, a2 or a3 on an older
  kernel**, and deliberately so: every one of those installs against a kernel
  whose ledger it silently requires and cannot state.
- Every migration is now a required wheel content in
  `.github/release-modules.json`, not just the first two. `ig_0007` creates
  nothing, so a wheel that dropped it would ship a declared-but-never-verified
  prerequisite.

#### Added

- **`ig_0007_idempotency_ledger`** — a DDL-free revision whose whole body is
  `require_prerequisites`. Deploy is the last moment at which a missing ledger
  is a failed migration rather than a failed delivery.
- A NEW revision rather than an edit to `ig_0001`, which shipped in three
  published tags and whose bytes are therefore history.
  `tests/architecture/test_released_migrations.py` records the SHA-256 of every
  migration file in every released tag, cross-checks each digest against the
  blob git holds at that tag, and fails if one changes or disappears — the guard
  numbering did not need, because its `0.1.0a1` was never published.

## 0.1.0a3 — released 2026-08-15

### `ModeContractError` no longer repeats what a connector threw

`verify_plugin_modes` calls each declared mode's handler factory at DISCOVERY,
which happens after configuration has been resolved — so a plugin that
interpolates a resolved credential into its own exception (an ordinary connector
bug) handed that credential to this module, which put it in the error message
and chained the original as `__cause__`.

The type name now travels and nothing else, and the raise is `from None`.
Dropping `{exc}` alone would not have been enough: the chained cause renders in
every traceback and every `logging` call with `exc_info`, which is where an
operator would actually have read it.

This is the invariant `ingress.HandlerUnavailable` already held one layer down
for the REQUEST path. Discovery held the inverse. Proven on all four surfaces —
message, `repr`, rendered traceback and a log record — with a sensitivity proof
that the same value DOES reach all four when interpolated and chained the way
the defect did it.

An operator keeps what they need to act: the connector key, the capability, the
mode, the failing hook and the exception TYPE.

## 0.1.0a2 — released 2026-08-15 (see the warning above)

### SPI 1.1 — one frozen contract, mode-specific protocols

**SPI 1.1 ships in `0.1.0a2`**, and is published because that version is (this
paragraph said "not published either" while a2 was still unreleased; the tag was
cut on 2026-08-15). It is the ONLY SPI version after 1.0: two unpublished drafts existed
on an abandoned branch — one adding the mode protocols, one replacing the
ingress hooks' loose parameters with a single immutable envelope — and they are
collapsed into this one. Shipping them as two consecutive breaking SPI versions
inside one unreleased alpha would have been a fiction, because no consumer could
ever have pinned the intermediate one. Any note elsewhere describing "SPI 1.1"
and "SPI 1.2" as separate published contracts describes something that never
existed.

`CURRENT_SPI_VERSION` is therefore `1.1`, and 1.2 is unused and available.

#### Modes became obligations

`ConnectorMode` shipped in SPI 1.0 with three members and nothing consulting
them: `INGRESS` and `POLL` had no executable protocol at all, and `DELIVERY` was
invoked without checking the declaration, so a binding pointed at an
ingress-only connector reached `handler_for` and failed with an `AttributeError`
from inside a lookup.

- `ConnectorPlugin` is now the BASE — identity, metadata, `validate_connection`.
  `handler_for` moved to `DeliveryPlugin`, joined by `IngressPlugin`
  (`ingress_handler_for`) and `PollPlugin` (`poll_handler_for`). A base
  demanding `handler_for` is what made `modes` decorative: if every plugin must
  supply a delivery handler, declaring `DELIVERY` says nothing.
- `MODE_PROTOCOLS` binds each mode to its plugin protocol, its factory name and
  the protocol the factory's RETURN VALUE must satisfy — one frozen
  (`MappingProxyType`) table, asserted exhaustive at import, so a new mode
  cannot be added without deciding what makes it runnable.
- `verify_plugin_modes` checks the implication in BOTH directions and runs at
  DISCOVERY. One direction alone is not enough: declaring without implementing
  fails at the first dispatch, implementing without declaring never gets workers
  started and the connector looks installed and inert.
- It also checks the SHAPE of the handler, not merely that one came back. A
  factory returning a delivery handler where an ingress handler was promised
  satisfies "not None" perfectly and then fails on a provider's request.
- `discovery.discover` and `conformance.assert_plugin_conforms` both call that
  one function, so an author's suite and the host's boot cannot reach different
  verdicts.
- `dispatch.invoke` calls `require_mode(plugin, DELIVERY)` BEFORE the handler
  lookup, so a misconfigured binding says "this connector does not deliver".

#### An immutable raw request envelope

- `IngressRequest` carries the raw body, headers and query params, and the SAME
  object is handed to all three ingress hooks — so what was authenticated and
  what was interpreted are provably the same bytes.
- Nothing is normalised: names and values are preserved exactly as given, the
  mappings are copied and then wrapped in `MappingProxyType`, and the dataclass
  is `frozen`/`slots` so no hook can mutate what a later hook sees or smuggle a
  session on as an ad-hoc attribute.
- `repr=False`: it is a frame local in every traceback leaving the plugin phase
  and it holds the raw body, the signature header and any cookie a misconfigured
  proxy passed through.
- Known limit, stated rather than discovered later: `headers` and `params` are
  single-valued. A multi-valued view is purely additive and needs no break.

#### A constrained acknowledgement

- `Acknowledgement` gives the connector the response BODY (as `bytes`) and the
  media type, and nothing else. The engine keeps the status code.
- That split is the point: a connector must be able to satisfy a provider's
  exact handshake format without being able to lie about whether the engine
  accepted the request. A status code is a retry instruction, and only the
  engine knows whether the batch committed.
- `media_type` is validated against a strict `type/subtype` — it is a response
  header value, and an unvalidated one carrying CRLF is header injection.

#### SPI 1.0 compatibility

Minor rather than major, and held by a test rather than by prose:
`tests/unit/test_integration_spi_modes.py` carries a hand-written SPI 1.0
delivery-only connector — `handler_for` on the plugin, `modes` naming `DELIVERY`
only, `>=1.0,<2.0`, no knowledge of ingress or poll — and proves it still
discovers, still conforms, and is still actually dispatched to. A major bump
would have excluded every honest `>=1.0,<2.0` delivery connector in order to
protect a compatibility promise nothing ever consumed.

### Fixed (the type gate)
## 0.1.0a2 (continued) — RELEASED — receipt-to-product delivery

Receipt-to-product delivery: the half that turns a recorded observation into a
delivered one. **No version bump** — the persistence this slice specifies is
still blocked on a staged handoff (below), so nothing here is releasable yet.

### Added

- **`dotmac_integration.receipt_delivery`** — the typed, deeply immutable
  boundary contracts for landing a receipt on the product that owns it, plus
  `deliver_receipt`, the three-phase orchestrator: claim transaction → product
  call with **no session held** → conditional settlement guarded by the claim's
  attempt/lease identity. A network call inside a transaction holds a row lock
  for the duration of someone else's outage, so phase 2 touches no database at
  all, and the fake store in the unit suite checks that rather than trusting it.
- `ProductAcceptance` — five typed product answers, each mapped to an existing
  `retry.OutcomeStatus` through a table rather than an `if` chain, so a new
  member without a retry decision is a `KeyError` at the boundary instead of a
  silent fall-through to "retryable" (the default that duplicates consequences).
- `idempotency_key_for` — derived from the receipt and its DESTINATION, and
  deliberately **not** from the attempt number. That absence is the whole
  at-most-once property: a timeout on attempt 1 followed by a successful attempt
  2 presents the product with the same key, so it deduplicates and the
  consequence happens once. Had the key carried the attempt, the retry curve
  would be a duplication machine.
- `request_fingerprint_for` / `require_stable_fingerprint` — same identity plus
  same fingerprint is a safe replay; a changed fingerprint is a
  `FingerprintConflict`, raised BEFORE the product is contacted rather than
  silently overwriting a recorded consequence.

### Changed

- **`LostClaim` moved to `execution`, which owns claiming.** It was defined in
  `dispatch` for the outbox; the inbox now needs the same concept, and two
  classes sharing the name would have made `except LostClaim` catch a delivery's
  lost claim but not a receipt's, decided by nothing more than which module the
  caller imported from. `dispatch.LostClaim` still resolves and the package
  surface is unchanged.

### Blocked (deliberately, and ratcheted)

`inbox_receipts` needs a lease, due scheduling, a fingerprint and typed product
outcome columns. Those are receipt-state model changes, and ownership is staged
behind Team 2 (`models.py` / `ig_0003`) and Team 3's trusted destination
(PR #184). Writing them now would collide with `models.py` mid-edit.

So `tests/test_integration_receipt_delivery_isolation.py` specifies the
behaviour FIRST, as 11 PostgreSQL canaries carrying
`xfail(strict=True, raises=ProgrammingError)` — they must fail, and must fail
*because the column is missing*. A twelfth test asserts those columns are still
absent, so the moment the handoff lands the suite goes red and names the markers
that must come off. The trusted destination is consumed through structural
protocols (`TrustedDestination`, `TrustedScope`) that Team 3's frozen
`DestinationBinding` already satisfies, so adopting it is a one-line import.
## 0.1.0a2 (continued) — RELEASED — payload retention

Payload retention. No version bump here on purpose: the release lane decides
when this ships, and a bump in the same change as the behaviour makes the two
decisions one.

### Added

- **`dotmac_integration.retention`** — a receipt's CONTENT ages out; its
  identity never does. Redaction rewrites `payload_json`, `headers_json` and
  the values inside `consequence_json`, and touches no column deduplication,
  ordering or outcome comparison reads. A provider redelivering a
  months-old event still gets "already received" rather than being processed a
  second time, and one event id arriving with different content is still a
  `ProviderEventIdentityCollision`.
- **No default retention period and no default legal-policy owner.**
  `resolve_retention_policy` refuses until `INTEGRATION_PAYLOAD_RETENTION_DAYS`
  and `INTEGRATION_RETENTION_LEGAL_POLICY_OWNER` are both set, in every
  environment. A period baked into a library becomes the deployment's
  data-retention posture without anyone deciding it.
- **`receipt_legal_holds`** (migration `ig_0006_retention`) — a held receipt is
  never redacted, and the refusal is named and counted. Platform plane, with a
  PARTIAL unique index enforcing at most one ACTIVE hold per receipt while
  keeping released holds as the record that they existed.
- **Four refusals, four ways to destroy in-flight work**: `legal_hold`,
  `leased` (a worker's claim), `unresolved` (including `dead_letter` and
  `retryable`, which `replay_receipt` may still move) and
  `reconciliation_required`. Enforced twice — a named Python refusal, and a
  conditional UPDATE that repeats every condition so a hold or a claim arriving
  mid-sweep still wins.
- `retention_backlog`, counted from the ledger at read time. No stored status
  column, for the same reason `health_report` has none.

## 0.1.0a2 (continued) — RELEASED — the type gate

Fixes a public function that could never have run, and the gate gap that let it
ship. `pyproject.toml` declares `dotmac_integration.*` under mypy's strict
settings, but the Makefile never passed the package to `mypy` or `bandit` — so
the strictness was declared and unenforced, and `0.1.0a1` published with 42 type
errors and one broken export.

### Fixed

- **`run_effect_once` raised `TypeError` on its first call.** It passed a
  `payload=` keyword `dotmac_kernel.idempotency.execute_once_platform` does not
  accept, and an `operation` taking no arguments where the kernel calls
  `operation(db)`. It is exported from `__init__` and had no caller and no test,
  which is why nothing noticed. Now a faithful adapter: every parameter is the
  kernel's, and the only addition is `mechanism` → `scope`. The old `payload`
  becomes `fingerprint`, the kernel's own column (ADR-0014).
- `assert_connector_conforms` used `try/except/pass` carrying a `# noqa: S110` —
  ruff's code, not bandit's `B110`, so the suppression named the wrong tool.
  Rewritten as a positive assertion with no swallowed exception.

### Changed

- Every JSON-shaped payload is annotated `dict[str, object]` in house style
  (32 sites). `secret_refs` is `dict[str, str]` downstream of validation and
  `dict[str, object]` in the validator itself, which exists to reject
  non-strings.
- `resolve_binding`'s rows are typed once, so its three returns stop escaping as
  `Any` from a declared `CapabilityBinding` return type.
- `_is_reference` narrows with `is not None` rather than `bool(match)`. The old
  form was safe at runtime — `and` short-circuits — but unprovable to a checker.

## 0.1.0a1 — 2026-08-14 — RELEASED (tag `dotmac-integration-v0.1.0a1`)

The connector control plane, as a Starter module (ADR-0024). Implements SPI 1.0.
(It was the only released version when this was written; `0.1.0a2` followed on
2026-08-15. See "Release state" at the top for the current list.)
