# Sub's `messaging.receive.v1` port — specification

**Status:** specification only. Nothing in `dotmac_sub` is changed by this
document, and nothing here authorizes a connector. It says what Sub must build
to accept an observation from an independently deployed Integrator, and — just
as important — what it must NOT build, because Sub already owns most of it.

**Standards:** ADR-0024 (applications compose by synchronizing data; § 3
observations vs decisions; § 7 the connector SPI), ADR-0008 (declaration
registries), ADR-0018 (an exemption states an enforceable premise).

**Companion code (this repository, this slice):**
`dotmac_integration.capability_registry` and
`dotmac_integration.destination_binding`.

---

## 1. The three sentences this port has to satisfy

1. **Authenticate the Integrator** — not the provider, and not nothing.
2. **Deduplicate immutable observations** — the same provider event arriving
   twice is one observation.
3. **Delegate consequences to the existing Team Inbox owner** — the port records
   and delegates; it decides nothing itself.

Everything below is those three, made concrete against what Sub already has.

---

## 2. What Sub already owns, and must not grow a second copy of

The largest risk in this port is not a missing capability. It is a **second
writer**: a new inbound path that re-derives threading, routing or contact
resolution because the new caller looked different. It does not look different.
An observation from the Integrator is the same kind of fact as an observation
from a webhook Sub verifies itself; only the authentication differs.

| Concern | Existing owner in `dotmac_sub` | The port's obligation |
|---|---|---|
| Capability id `messaging.receive.v1` | already live — declared in `app/services/integrations/registry.py`, bound by the Meta-social and WhatsApp connectors, `modes=(CapabilityMode.inbound,)` | **reuse the id**. It is provider-neutral and already means "an inbound message observation". Minting a second id would fork the contract. |
| The immutable observation | `app/services/team_inbox_observations.py`, `OBSERVATION_OWNER = "communications.team_inbox_observations"`; entry `record_provider_observation(db, RecordProviderObservationCommand)` | **call it.** Do not insert into `inbox_provider_observations` directly. |
| Consequence coordination | `app/services/team_inbox_processing.py`, `OWNER = "communications.team_inbox_processing"`; entry `process_provider_observation(db, *, observation_id, context)` | **call it.** Do not call a receiver directly. |
| Thread, contact, routing, AI intake, automation | `app/services/team_inbox_channel_receive.py::receive_inbound_channel` and the routing/assignment/handoff owners around it | **never reached by the port.** The processing owner dispatches to it. |
| Transport receipt + binding-scoped dedup | `app/services/integrations/inbox.py` (`receive_and_claim_verified`, `complete_consequence`, `fail_consequence`), table `integration_inbox`, `uq_integration_inbox_binding_provider_event` | **call it**, exactly as `lead_capture_webhooks.py` does. |
| Ticket creation | `app/services/conversation_ticket_handoff.py`, owner `communications.conversation_ticket_handoff` | **never reached by the port.** |

**The port owns nothing but the boundary.** It is an adapter: authenticate,
normalize, record, delegate, answer. If a reviewer can point at a business
decision inside it, it is wrong.

---

## 3. Authenticate the Integrator

### 3.1 What is being authenticated, and why the distinction matters

Sub's existing inbound routes verify a **provider** signature — an HMAC over raw
bytes with a secret shared with Meta, a PSP, a panel. That is the right control
when Sub is the one talking to the provider.

Here it is the wrong control, because Sub is not talking to the provider. The
Integrator is. By the time bytes reach Sub they have already been verified once,
re-serialized into a provider-neutral capability envelope, and the provider's
signature no longer covers them. **Re-checking a provider signature at Sub's
door would be checking a signature over a body that is not the signed body.**

So the caller Sub authenticates is the Integrator, as a machine principal.

### 3.2 The mechanism

Sub already has the primitive and should not grow a third one. In order of
preference:

1. **`ApiKey` with `system_user_id` set** (`app/models/auth.py`, table
   `api_keys`) presented as `X-API-Key`, resolved by
   `app/services/auth_dependencies.py::_api_key_principal`, gated by a dedicated
   scope. `scopes` is already **fail-closed on empty**, which is the property
   this port needs most. A new scope — proposed `integration:observations.write`
   — is declared and required; no existing scope is widened.
2. If the fleet later issues short-lived service credentials, the port swaps the
   dependency and keeps everything else, because nothing downstream of
   authentication knows how the caller proved itself.

**Rejected:** an HMAC over the envelope shared between Sub and the Integrator.
It is a fourth credential shape for one caller, it has no revocation story
(`ApiKey.revoked_at` already has one), and it invites the same "verify the
provider" confusion this section exists to remove.

### 3.3 What the credential does NOT grant

Authentication answers *who is calling*, never *where this lands*. The
Integrator's key does not name a conversation, a team, a subscriber or a
channel, and the port must not accept any of those from the caller as an
authoritative field. That is the same invariant
`dotmac_integration.destination_binding` enforces on the other side of the wire:
routing comes from a trusted binding, never from the message.

### 3.4 Failure shape

* No credential, unknown credential, revoked/expired credential, or a credential
  without the scope → **401**, no observation row, no receipt row.
* An authenticated caller sending an envelope for a capability Sub does not
  accept → **404**, not 403: an unauthenticated enumeration of what Sub accepts
  is itself information.

---

## 4. Deduplicate immutable observations

### 4.1 The identity

The unit of deduplication is the **provider event**, not the HTTP request and
not the envelope. The Integrator supplies:

| field | meaning | source |
|---|---|---|
| `provider` | which provider family produced the event | the Integrator's installation |
| `provider_account_scope` | which account/tenant of that provider | the Integrator's installation |
| `provider_event_id` | the provider's own immutable event id | the provider payload |
| `payload_fingerprint` | canonical-JSON SHA-256 of the normalized payload | computed by the Integrator, recomputed by Sub |

Sub already constrains exactly this tuple:

```
UniqueConstraint("provider", "provider_account_scope", "provider_event_id",
                 name="uq_inbox_provider_observations_identity")
```

**Use the existing constraint.** `record_provider_observation` already returns an
outcome distinguishing a fresh record from a replay; the port returns that
distinction to the caller rather than re-deriving it.

### 4.2 Same id, different content, is not a duplicate

A redelivery with the same `provider_event_id` and the same fingerprint is one
observation. A redelivery with the same id and a **different** fingerprint is a
provider identity collision: the port must **refuse and escalate**, never
silently treat it as a duplicate, because deduplicating it discards real content
on the assumption the provider is well-behaved. The Integrator raises
`ProviderEventIdentityCollision` for the identical case on its side; Sub's port
answers **409** and records the collision.

### 4.3 Two dedup layers, deliberately, and they are not redundant

* **Transport** — `integration_inbox`, keyed
  `(capability_binding_id, provider_event_id)`. Answers "have I already accepted
  these bytes on this binding?"
* **Domain** — `inbox_provider_observations`, keyed
  `(provider, provider_account_scope, provider_event_id)`. Answers "have I
  already recorded this fact?"

The first is scoped to a binding; the second is not. Two bindings legitimately
observing one upstream event are two receipts and **one** observation, and
collapsing the layers loses that.

### 4.4 Immutability

An observation is inserted and never updated. `processing_status` and
`processed_at` are the processing owner's to advance; the port writes neither
after the insert. A replay re-emits with a byte-identical identity, so the
owner sees a replay and not a second fact.

---

## 5. Delegate consequences

### 5.1 The exact chain

```
POST /integration/observations/{capability_binding_id}
  → authenticate the Integrator (§ 3)
  → resolve the binding; refuse unless capability_id == "messaging.receive.v1"
  → integrations.inbox.receive_and_claim_verified(...)        # transport receipt
  → team_inbox_observations.record_provider_observation(...)   # the FACT
  → team_inbox_processing.process_provider_observation(...)    # the CONSEQUENCE
  → integrations.inbox.complete_consequence(...)               # or fail_consequence
  → 202 with {observation_id, outcome, processing_status}
```

Every arrow is a call into an existing owner. The port contributes the ordering
and the boundary, and nothing else.

### 5.2 The line the port must not cross

Per ADR-0024 § 3: an importer records typed observations and **never assigns
authoritative status**; a resolver or reconciler then updates a rebuildable
projection or submits a command to the local owning service.

Concretely, the port may not:

* create or update an `InboxConversation`, or choose an `external_thread_id`;
* assign a team, a queue position or an agent;
* create a ticket, or call `conversation_ticket_handoff`;
* set any lifecycle or status field on any Team Inbox record;
* interpret `scope` from the envelope as a routing instruction.

The last one is worth stating plainly because it is the one that looks helpful.
The Integrator's binding carries a `LocalScope` — `inbox:support`. Sub receives
it as **provenance**: it records which stream the Integrator believes this
belongs to, and Sub's own routing owner decides where it actually goes. Sub is
authoritative for its own structure; a transport's opinion about a Sub team is
an observation like any other.

### 5.3 A new `InboxProvider` member

`InboxProvider` is currently a closed `StrEnum` of five. The port needs an
Integrator-sourced member. It is a **closed enum on the owning side** because it
names providers Sub's own code branches on — that is not in tension with
ADR-0008, which governs vocabularies a *host* offers to others. Sub owns this
one; adding a member is Sub's decision, recorded in Sub.

---

## 6. The envelope

Provider-neutral by construction. The Integrator has already translated the
provider's wire format into the owning module's published contract; this is that
contract's transport shape.

| field | type | notes |
|---|---|---|
| `capability_id` | string | must equal `messaging.receive.v1`; a mismatch is 404 |
| `contract_version` | int | from the id's `vN`; a version Sub has not deployed is 409, never a best-effort parse |
| `provider` | string | provider family |
| `provider_account_scope` | string | account/tenant within the provider |
| `provider_event_id` | string | the provider's immutable id; empty is 400 |
| `observed_at` | RFC-3339 | when the provider says it happened |
| `payload_fingerprint` | hex sha256 | recomputed by Sub over the canonical form; a mismatch is 400 |
| `scope` | `{kind, ref}` | the Integrator's binding scope. **Provenance only** (§ 5.2) |
| `observation` | typed object | normalizes to Sub's existing `InboundMessageObservation` family |

`scope` is deliberately last and deliberately labelled, because a future reader
adding routing to it would be repeating on Sub's side the mistake
`CapabilityBinding.scope_json` records on the Integrator's.

---

## 7. Idempotency and ordering, stated as expectations

* **Duplicate delivery**: same id, same fingerprint → one observation, the
  recorded outcome returned, no second consequence.
* **Collision**: same id, different fingerprint → 409, escalation not retry,
  original content preserved.
* **Out-of-order delivery**: each observation is an immutable fact with its own
  identity. Nothing is buffered or reordered, and the processing owner produces
  the same end state regardless of arrival order.
* **Rollback**: a batch producing one collision rolls back the whole batch, so a
  retry is correct and no partially-recorded batch exists that the Integrator
  believes it delivered.
* **Concurrency**: two Integrator workers delivering one event → exactly one
  observation; the loser sees the replay outcome, not an error.

---

## 8. Tests Sub must add

Named so a reviewer can check them off. Each is a property, not a smoke test.

1. **Unauthenticated and wrongly-scoped calls change no row count** — 401, and
   `inbox_provider_observations` / `integration_inbox` / `inbox_messages` are all
   unchanged. Includes the revoked-key and expired-key cases.
2. **A provider signature is not accepted in place of Integrator
   authentication** — a request carrying a valid-looking provider HMAC and no
   Integrator credential is 401. This is the sensitivity proof for § 3.1.
3. **Replay is one observation** — same identity twice → one row, the second
   call returns the first outcome, and the Team Inbox side records exactly one
   consequence.
4. **Collision escalates** — same identity, different fingerprint → 409, the
   original `normalized_payload` is byte-identical afterwards.
5. **The port assigns no authoritative status** — after a successful call, the
   only rows the *port* wrote are the receipt and the observation; every Team
   Inbox mutation is attributable to the processing/receive owners. Assert by
   owner, not by table, so a future direct write is caught.
6. **Envelope `scope` cannot select a team** — deliver two identical
   observations differing only in `scope.ref`, one of them naming a real Sub
   team the routing rules would not choose, and prove routing is identical. This
   is Sub's half of the destination invariant.
7. **An undeployed `contract_version` refuses** — `v2` against a `v1`-only
   deployment is 409 and writes nothing.
8. **Unknown capability is 404, not 403** — an authenticated caller cannot
   enumerate what Sub accepts.

---

## 9. Open items for the programme

1. **`messaging.receive.v1` is already bound in Sub by two Meta-family
   connectors.** Reusing the id is correct — it is provider-neutral and already
   means "inbound message observation" — but it means Sub will have Integrator
   and non-Integrator producers of one capability at once. The migration
   sequence (which producer is authoritative during the overlap, and how the
   older path is retired) is a Sub decision this specification does not make.
2. **Sub still owns the seven integration-platform tables** that
   `dotmac-integration` was extracted from. Until Sub retires them, both sides
   hold a control plane for the same concept. That is a known duplication, not a
   contradiction, but the port must be built against Sub's existing services so
   it survives the retirement without a rewrite.
3. **The capability declaration itself.** Slice A puts declarations in the
   assembly's hands via `install_capability_registry(...)`. The mechanism by
   which Sub *publishes* its declaration to the Integrator's assembly — a
   checked-in manifest, a build-time artifact, or a `ModuleManifest` field — is
   still open (`provider-capability-sources.md` § 7.2 leaves it open). Nothing in
   this slice presumes an answer; the registry accepts data from any of them.
