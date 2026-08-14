# Changelog — dotmac-integration

All notable changes to the `dotmac-integration` distribution. This package
follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl. this
alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

Version numbering note: `0.1.0a2` is #164's gate fix, which landed on `main`
while this train was in flight. The SPI and ingress work below was written as
`a2`/`a3` and renumbered to `a3`/`a4` on rebase rather than reusing a number
main had already published against.

The distribution version, `__version__` and `ModuleManifest.version` are in
step, and a gate keeps them there. #164 is the reason it exists: it bumped
`pyproject.toml` to `0.1.0a2` and left the other two on `0.1.0a1`, so a running
Integrator's health output could not distinguish the two.
`tests/architecture/test_module_version_sync.py` checks all three surfaces for
every module in `.github/release-modules.json` rather than for this one, and it
caught that drift the moment this branch rebased onto it.

## 0.1.0a4 — 2026-08-14

The ingress engine: a provider can now reach a connector through this module,
which SPI 1.1 made expressible in `0.1.0a3` and nothing yet implemented.

### Added

- **`dotmac_integration.ingress`** — the three-phase inbound seam, mirroring
  `dispatch.prepare/invoke/settle`:

  | phase | session | job |
  |---|---|---|
  | `prepare_ingress` | yes, short | resolve the minted endpoint, the manifest pin and the active config revision. Writes nothing. |
  | `verify_and_normalize` | **none, by signature** | materialize secrets, verify the RAW bytes, then shape them into `InboundEvent`s. |
  | `record_batch` | yes, short | record the WHOLE normalized tuple, atomically. Returns `(receipt_id, is_new)` VALUES, never ORM rows. |

  Two façades, `receive` and `answer_challenge`, return the same
  `IngressOutcome`, so a consuming assembly's two route handlers differ only in
  which they call.

- **`capability_bindings.ingress_endpoint_key`** (migration `ig_0003`) — one
  nullable, uniquely indexed, rotatable column. An ingress URL addresses this
  ONE identifier and never a `(connector_key, capability_id)` pair, which is
  ambiguous: several installations may serve one such pair (a production and a
  test provider account, or two operators'). It is not the primary key because
  the PK is (a) not minted — every binding in the fleet would carry a live URL,
  delivery-only ones included — (b) not rotatable, being FK-referenced
  `ON DELETE CASCADE` from three tables, and (c) already disclosed in
  operator-facing error text. No new table; `platform_tables` stays at seven,
  and no GRANT or REVOKE, because table-level privileges cover columns added
  later.

- **`mint_ingress_endpoint`, `rotate_ingress_endpoint`,
  `revoke_ingress_endpoint`** in `lifecycle` — minting is gated on the
  connector declaring `INGRESS`, because an endpoint for a connector that
  cannot receive is a URL whose only possible answer is 503. Rotation keeps the
  entire inbox history, since receipts are keyed on the binding.

- **`IngressOutcome` and `IngressCode`** — typed outcomes with a decided status
  per refusal. An ingress status IS a retry instruction to the provider, so
  `refusal_outcome` is the only place one is chosen; the edge constructs
  `PayloadTooLarge()` and passes it there rather than authoring `413` itself.

- **A typed refusal hierarchy** — `EndpointUnknown` (404),
  `EndpointNotUsable` (404), `ConnectorUnavailable` /
  `ManifestPinUnhonoured` / `ModeNotAvailable` (503, grouped under
  `EndpointNotServiceable`), `SignatureRejected` (401), `NotAChallenge` (400),
  `ConnectorRaised` / `ConnectorContract` (503),
  `EventIdentityCollision` / `ReceiptWriteRaced` / `ReceiptWriteFailed` (503)
  and `PayloadTooLarge` (413). Every refusal but `ConnectorRaised` takes NO constructor arguments, so
  interpolating a request fragment into one is impossible rather than
  discouraged; `ConnectorRaised` carries a `.isidentifier()`-validated type name
  and is raised `from None`, so a plugin's provider-built message cannot reach a
  traceback.

- **`UnitOfWork`**, injected the same way ADR-0009 injects `SecretResolver`. The
  deployment decides what a unit of work IS; the module decides how many there
  are and where their boundaries fall — because "one collision rolls back the
  whole provider batch" is a correctness property of ingress, not a deployment
  preference. The phase functions still take `db: Any`, mutate, flush and never
  commit or roll back.

- **Conformance knobs** on `FakePlugin`: `normalized`, `challenged`,
  `configs_seen`, `secrets_seen`, `ingress_raises`, `normalize_raises`,
  `ingress_contract_broken`. `configs_seen`/`secrets_seen` exist because "no
  database session reached the plugin" asserted over the raw bodies alone
  reduces to "bytes are not a `Session`", while the route a session would
  actually take is a `dict[str, Any]` config VALUE that no field-type check
  inspects.

### Changed

- `record_batch` returns `tuple[tuple[UUID, bool], ...]` rather than the ORM
  rows. The carrier rule applied to the return path: the caller's unit of work
  has closed by the time it reads the result, and with `expire_on_commit` on —
  the default, and what the shipped `platform_session` uses — an attribute read
  off a committed row is a refresh against a session that no longer exists. That
  lands on the SUCCESS path: the batch is durably written and the caller is told
  it failed, so the provider redelivers into the same failure forever. It is the
  partial-write lie inverted, and unlike a collision it never converges.
- `IngressHandler.challenge`'s docstring said returning `None` means "not a
  handshake for me — the caller then treats the request as a delivery". The
  engine has never done that: it answers 400, and `verify` is never reached. The
  behaviour is the deliberate one — offering a bodied delivery to `challenge`
  would let a plugin that returns non-`None` by accident swallow a batch — so
  the docstring was corrected to match, and the limitation (a provider whose
  subscription confirmation carries a BODY is not yet serviceable) is now
  stated. Documentation only.
- `FakePlugin`'s fake `challenge` reads a neutral `"challenge"` parameter. It
  read a provider-specific one, inside a module that may name no provider
  (ADR-0024 § 7).
- `InboxReceipt`'s docstring and `ig_0002`'s said the dedup key was
  `(installation_id, provider_event_id)`. The constraint and
  `receive_verified`'s query are both `(capability_binding_id,
  provider_event_id)`, and always were. Documentation only.

### Security

- **Every database error is a typed refusal, raised `from None`.**
  SQLAlchemy's `StatementError.__str__` appends `[SQL: …] [parameters: (…)]`
  unless the engine sets `hide_parameters=True`, which no engine in this fleet
  does — so an untyped driver error escaping to the edge puts the normalized
  payload and the provider event id verbatim into an ERROR log line, from a
  module that installs no logger precisely so that cannot happen. The catch is
  at `StatementError`, not `IntegrityError`: a payload the JSON serializer
  refuses raises a BARE `StatementError` and an over-long id raises `DataError`,
  so the integrity subtree alone would have missed both. `ExecutionError` (a
  blank `provider_event_id`) is converted for the same reason, minus the leak —
  its message is a constant.
- **The materialized secrets local is cleared in a `finally`**, in both
  `verify_and_normalize` and `challenge_response`. Going out of scope is not
  enough: any exception leaving those functions pins the frame in its
  traceback, and an error reporter configured to capture frame locals uploads
  whatever is bound there. A `BaseException` — `asyncio.CancelledError` from a
  disconnecting client is the ordinary case on a webhook endpoint — walks past
  `except Exception` and must, so the binding is cleared rather than the
  exception caught.
- **`PreparedIngress.endpoint_key` is `repr=False`.** The carrier is a frame
  local in every escaping traceback, and the key is a bearer secret for exactly
  the reason `IngressOutcome` refuses to echo it: whoever holds it can drive the
  connector's `verify`. The value is still there — a rendering rule, not a
  removal.
- The raw body, the signature headers and the materialized secrets are
  ephemeral and are never logged, never interpolated into an exception, and
  never stored. `record_batch` passes `headers=None` unconditionally —
  `headers_json` ends up in every backup, and forwarding request headers would
  put the signature header, and any `authorization` or `cookie` a misconfigured
  proxy passed through, permanently in the database. A connector that needs a
  provider request id lifts it into the payload during `normalize`.
- `IngressOutcome` has no field able to hold request material, and deliberately
  does not echo the endpoint key: whoever holds it can drive a connector's
  `verify`, so returning it in a 404 would hand it to a scanner.
- A rejected signature persists nothing anywhere. There is no table for
  unverified bodies and there must not be one.
- A malformed endpoint key is refused against a fixed shape BEFORE any query,
  and is indistinguishable from an unknown one — distinguishing them would make
  the endpoint a probing oracle.

### Not in this release

- The HTTP surface. The routes, the STREAMING request-size cap and its
  configurable default belong to the `dotmac_integrator` assembly; a byte cap
  that varied by connector would be a connector-specific limit, which this
  module may not hold. Hence no `max_bytes` parameter anywhere here.
- An audit action for mint/rotate. `manifest.py`'s stated rule is that a
  declared code with no writer is dead vocabulary; it lands with the admin route
  and the guard that protects it.
- `config_revision_id` on `InboxReceipt`. The pin travels through
  `PreparedIngress` so verification and normalization cannot straddle a config
  change, but the receipt cannot record WHICH revision verified it. That is a
  genuine second migration and is flagged rather than invented.

## 0.1.0a3 — 2026-08-14

**Breaking.** A declared mode became a promise the kit verifies. `modes` was
decorative before this: `dispatch` called `handler_for` without asking whether
the plugin declared `DELIVERY` at all, so pointing a binding at an ingress-only
connector produced a confusing handler error instead of a refusal.

### Changed — BREAKING

- **`ConnectorPlugin` is now a BASE protocol**: `manifest`,
  `historical_manifests`, `modes`, `validate_connection`. `handler_for` moved
  off it. It moves DELIVERY data, and a base protocol demanding it forces every
  ingress-only connector to either lie or raise.
- `CURRENT_SPI_VERSION` is `1.1`, not `2.0`. The change is ADDITIVE: a delivery
  connector implementing all five original members still satisfies both
  `ConnectorPlugin` and `DeliveryPlugin`, so every plugin built against 1.0
  still resolves.

### Added

- **`DeliveryPlugin`** (`handler_for`), **`IngressPlugin`**
  (`ingress_handler_for`) and **`PollPlugin`** (`poll_handler_for`) — one
  executable protocol per mode.
- **`IngressHandler`** — three separable jobs with different inputs and
  different failure meanings: `challenge` answers the subscription handshake
  (`None` means "not a handshake for me"), `verify` decides authenticity from
  the RAW bytes, and `normalize` shapes verified bytes into events and is never
  called on an unverified body. `config` reaches `normalize`; `secrets`
  deliberately does not, because normalization that needs a secret is doing
  verification in the wrong place.
- **`PollHandler`** — takes the cursor the module persisted and returns the
  events plus the cursor to persist next. The handler never writes the
  checkpoint, so it cannot advance past events it failed to return.
- **`InboundEvent`** — exactly the triple `receive_verified` records.
- **`MODE_PROTOCOLS`** and **`ModeContract`** — one entry per mode, so a new
  mode cannot be added without deciding what makes it runnable. That omission is
  precisely how `POLL` became a label with no machinery behind it.
- **`require_mode` / `ModeNotDeclaredError`** — raised BEFORE the plugin is
  called, so an operator sees "this connector does not deliver" rather than an
  `AttributeError` from inside a handler lookup. `dispatch.invoke` calls it.
- `conformance.assert_plugin_conforms` now asserts the mode implication in BOTH
  directions: declaring a mode requires satisfying its protocol, and satisfying
  one requires declaring the mode. One direction alone catches only half — a
  declaration nothing verifies is how `modes` became decorative.
- `FakePlugin` gained an ingress implementation with `inbound`,
  `signature_valid` and `verified` knobs.

## 0.1.0a2 — 2026-08-14

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

## 0.1.0a1 — 2026-08-14

The connector control plane, as a Starter module (ADR-0024).
