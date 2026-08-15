# dotmac-integration

The external connector control plane (ADR-0024). See the module docstring in
`src/dotmac_integration/__init__.py` and the dossier in `EXTRACTION.toml`.

The module names **no provider** — not one company, no provider enum and no
`if provider == …` anywhere (ADR-0024 § 7), enforced by a source scan in
`tests/architecture/test_integration_ingress_hygiene.py`. Provider knowledge
lives in a connector distribution, behind the SPI.

## Ingress — a provider reaching a connector through this module

`dotmac_integration.ingress` is the inbound half of the SPI, and it mirrors
`dispatch.prepare/invoke/settle` phase for phase:

| phase | session | job |
|---|---|---|
| `prepare_ingress` | yes, short | resolve the minted endpoint, the manifest pin and the active config revision. **Writes nothing.** |
| `verify_and_normalize` | **none, by signature** | materialize secrets, verify the RAW bytes, then shape them into `InboundEvent`s **and the `Acknowledgement`**. |
| `record_batch` | yes, short | record the **whole** normalized tuple, atomically. Hands back `(receipt_id, is_new)` **values**, never ORM rows. |

`verify_and_normalize` has no `db` parameter, so "a plugin never receives a
session" is enforced by what a caller *cannot pass* rather than by a comment
asking them not to. The bytes cross untouched: every provider worth verifying
signs the bytes and not a re-serialization of them.

### The two façades

Both take a `UnitOfWork` factory rather than a session, and both return the
same `IngressOutcome` — so a consuming assembly's two route handlers differ
only in which one they call:

```python
request = IngressRequest(raw_body=…, headers=…, params=…)   # one immutable envelope

receive(open_unit_of_work, endpoint_id=…, request=request,
        registry=…, resolve_secrets=…)           # a delivery   (assembly: POST)
answer_challenge(open_unit_of_work, endpoint_id=…, request=request,
                 registry=…, resolve_secrets=…)  # a handshake  (assembly: GET)
```

The deployment decides what a unit of work **is** (engine, pool, isolation
level, commit-on-clean-exit, roll-back-on-exception); the module decides how
**many** there are and where their boundaries fall. That split is not a
preference: *one collision must roll back the whole provider batch* is a
correctness property of ingress, and hand-composed by a caller it would hold
only as long as that caller got it right.

### Handshake and delivery are separate operations (SPI 1.2)

`receive` **never** answers a handshake, and `answer_challenge` never verifies
or records. SPI 1.1 inferred a handshake from an empty body; that inference was
wrong in both directions:

* a **bodyless POST is still a delivery** — a provider that signs an empty body
  (a "nothing changed" ping, a deletion whose content is in its headers) had its
  events dropped and was told "not a handshake" about a request that never was;
* a provider that confirms a subscription with a **bodied** request could not
  handshake at all.

The assembly mounts GET on `answer_challenge` and POST on `receive`, so the
operation is stated by the request line rather than guessed from a byte count. A
`challenge` returning `None` is a **refusal** (400), never a fall-through.

### One immutable envelope, and a typed acknowledgement (SPI 1.2)

`IngressRequest` is `frozen`, `slots=True`, `repr=False`, and carries the raw
body, the request headers and the query parameters. The **same object** reaches
`challenge`, `verify` and `normalize` — so what was authenticated is provably
what was interpreted, and no hook can edit what a later one sees. SPI 1.1 gave
each hook its own slice, which meant a provider identifying its handshake by a
header could not be served at all.

`Acknowledgement` is what the connector wants written back: `body: bytes` and an
optional, validated `media_type`. That is the **whole** response surface a
connector gets. There is deliberately no connector-selected status code — a
status is a retry instruction and only the engine knows whether the batch
committed — and no arbitrary response headers, which would be a
response-splitting surface and a route for request material to leave the
module's sight.

`normalize` returns `(events, acknowledgement)`, so the acknowledgement is built
**before** anything is persisted. The engine emits it only **after** the whole
batch commits, and calls **no** connector code after the commit: a plugin raise
on the far side of a durable write would answer 5xx for events that are safely
stored, and the provider would redeliver them forever.

### One collision rolls back the whole batch

A partial write leaves the provider believing the batch was accepted while some
events were never recorded, and it will not resend the ones that landed. So
`record_batch` takes the tuple and never one event — there is no loop for a
caller to put a transaction boundary inside — and `receive` catches the refusal
**outside** the `with` block. Catching inside would let the block exit cleanly
and commit the events recorded before the collision.

`receive_verified` is idempotent on `(capability_binding_id,
provider_event_id)`, which is what makes the provider's whole-batch retry
correct rather than duplicative.

### Outcomes

`refusal_outcome` is the only place a status is chosen, because an ingress
status **is a retry instruction to the provider** — the one place an invented
status silently destroys events.

| refusal | status | code | why that status |
|---|---|---|---|
| `EndpointUnknown` | 404 | `endpoint_unknown` | Malformed and unknown are one refusal; distinguishing them is a probing oracle. |
| `EndpointNotUsable` | 404 | `endpoint_not_usable` | Same status as unknown so live endpoints cannot be enumerated; a different code so an operator can tell a typo from a disabled binding. |
| `ConnectorUnavailable` | 503 | `connector_unavailable` | Our misconfiguration must be retryable — the provider's redelivery window is the only remaining copy. |
| `ManifestPinUnhonoured` | 503 | `manifest_pin_unhonoured` | Adopt the current manifest; the events survive in the provider's queue. |
| `ModeNotAvailable` | 503 | `mode_not_available` | A **stated** refusal, raised before any handler lookup — never an `AttributeError` from inside one. |
| `SignatureRejected` | 401 | `signature_rejected` | Providers do not retry 401, correctly: redelivering an unverifiable body fails identically. **Nothing is persisted.** |
| `NotAChallenge` | 400 | `not_a_challenge` | The connector does not recognise the handshake. |
| `ConnectorRaised` | 503 | `connector_raised` | A throw says nothing about what was normalized, so nothing may be written. |
| `ConnectorContract` | 503 | `connector_contract` | `normalize` returned something other than `(tuple[InboundEvent, …], Acknowledgement \| None)`, or `challenge` returned a non-`Acknowledgement`. The second half of that pair goes into a response body, so a bare tuple of events would otherwise be indexed and its first event written back. |
| `EventIdentityCollision` | 503 | `event_identity_collision` | Whole batch rolled back; recurs until an operator acts — the intended page. |
| `ReceiptWriteRaced` | 503 | `receipt_write_raced` | Whole batch rolled back; the retry finds the rows and answers 200 with `duplicates`. |
| `ReceiptWriteFailed` | 503 | `receipt_write_failed` | Any other write failure. Typed here because a driver error's `str` embeds `[parameters: …]` — the normalized payload and the provider event id — into whatever the edge logs. |
| `PayloadTooLarge` | 413 | `payload_too_large` | **Defined here, raised at the edge** (see below). |

Success is `ACCEPTED` (200, including a normalized count of zero) or
`CHALLENGE_ANSWERED` (200). Both carry a resolved `Acknowledgement`; every
refusal carries `None`, because a refusal body is the engine's classification
rather than something a connector that may never have run gets to shape. The
engine fills an unset `media_type` with `text/plain` for a handshake — providers
compare the raw echoed body, so wrapping it in JSON fails the handshake — and
`application/json` for a delivery.

### The request-size cap belongs to the edge

A byte cap that varied by connector would be a connector-specific limit, which
this module may not hold — so **no function here takes a `max_bytes`
parameter** and nothing here raises `PayloadTooLarge`. The assembly caps the
body (streaming, not `len(await request.body())`, which buffers the hostile
payload before deciding), constructs `PayloadTooLarge()` and passes it to
`refusal_outcome`, which is how it gets the status and the code without
authoring either.

### Nothing here logs, and nothing here can

The raw body, the signature headers, the query parameters and the materialized
secrets are ephemeral. Nine structural mechanisms keep them that way, none
relying on care:

1. `IngressRefused.__init__` takes **no arguments** — the message is a class
   constant, so there is nothing to interpolate a request fragment into.
2. `ConnectorRaised` is the one exception; it carries a
   `.isidentifier()`-validated **type name** and is raised `from None`, so a
   plugin's provider-built message cannot reach a traceback.
3. `IngressOutcome` has no **scalar** field able to hold request material — and
   deliberately does not echo the endpoint key, since whoever holds it can
   drive a connector's `verify`. Its one byte-carrying field is the typed
   `Acknowledgement`, which travels in the opposite direction: a connector built
   it deliberately for the provider that sent the request.
4. `record_batch` passes `headers=None` unconditionally. `headers_json` ends up
   in every backup; forwarding request headers would put the signature header —
   and any `authorization` or `cookie` a misconfigured proxy passed through —
   permanently in the database. A connector that needs a provider request id
   lifts it into the payload during `normalize`.
5. The module installs no logger.
6. Every database error is converted to a typed refusal **`from None`**.
   SQLAlchemy's `StatementError.__str__` appends `[SQL: …] [parameters: (…)]`
   unless the engine sets `hide_parameters=True`, which no engine in this fleet
   does — so an untyped driver error escaping to the edge is provider content in
   an ERROR log line. The catch is at `StatementError`, not `IntegrityError`: a
   payload the JSON serializer refuses raises a bare `StatementError`, and an
   over-long id raises `DataError`.
7. The materialized secrets local is cleared in a `finally`. Going out of scope
   is not enough — any exception leaving the plugin phase pins that frame, and
   an error reporter configured to capture frame locals uploads it. A
   `BaseException` (an ordinary `CancelledError` from a disconnecting client)
   walks past `except Exception`, and must.
8. `PreparedIngress.endpoint_key` is `repr=False`. The carrier is a frame local
   in every such traceback, and the key is a bearer secret for the same reason
   `IngressOutcome` refuses to echo it.
9. `IngressRequest` and `Acknowledgement` are `repr=False` for that same reason.
   The envelope holds the raw body and every header, including an
   `authorization` or `cookie` a misconfigured proxy passed through; the
   acknowledgement is assumed to hold request material because an echo handshake
   is *defined* as returning a slice of the request. Neither is ever persisted.

## The endpoint is minted, not implied

An ingress URL addresses **one** `capability_bindings.ingress_endpoint_key`,
from which the module derives installation, connector, capability, pinned
manifest, active config revision and secret references.

Never a `(connector_key, capability_id)` pair: several installations may serve
one such pair — a production and a test provider account, or two operators' —
so the pair names a set rather than an endpoint. And not the binding's primary
key, which is (a) not minted, so every binding in the fleet including
delivery-only ones would carry a live URL, (b) not rotatable, being
FK-referenced `ON DELETE CASCADE` from three tables, and (c) already disclosed
in operator-facing error text.

```python
mint_ingress_endpoint(db, binding, registry=…, actor=…)  -> str
rotate_ingress_endpoint(db, binding, actor=…)            -> str
revoke_ingress_endpoint(db, binding, actor=…)            -> None
```

Minting is gated on the connector declaring `INGRESS` — an endpoint for a
connector that cannot receive is a URL whose only possible answer is 503.
Re-minting is refused rather than treated as rotation: the two are different
intentions, and only "the published address is compromised" should silently
retire a URL that lives in a third party's console. Rotation keeps the entire
inbox history, because receipts are keyed on the **binding**.
