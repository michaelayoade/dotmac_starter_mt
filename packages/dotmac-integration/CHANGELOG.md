# Changelog — dotmac-integration

## Release state — read this before pinning

**One version of this package has ever been released: `0.1.0a1`** (tag
`dotmac-integration-v0.1.0a1`), which implements **SPI 1.0**.

`0.1.0a2` is declared in `pyproject.toml`, `manifest.py` and `__init__.py` and
is **not released**: no tag, nothing on the index. Everything below its heading
is on `main` and awaiting the programme's single alpha, which is cut after
destination binding and receipt delivery land. This section exists because the
`0.1.0a2` heading previously carried a date and read exactly like a release
entry — and a changelog that describes an unreleased version as released is how
a consumer comes to pin something that does not exist.

Nothing in this file is a publication claim except this paragraph.

## 0.1.0a2 — UNRELEASED (on `main`; no tag, not on the index)

### SPI 1.1 — one frozen contract, mode-specific protocols

**SPI 1.1 is not published either.** It ships inside the unreleased `0.1.0a2`
above. It is also the ONLY SPI version after 1.0: two unpublished drafts existed
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
## Unreleased

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

## 0.1.0a1 — 2026-08-14 — RELEASED (tag `dotmac-integration-v0.1.0a1`)

The connector control plane, as a Starter module (ADR-0024). Implements SPI 1.0.
This is the only released version of this package.
