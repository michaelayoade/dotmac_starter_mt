# ADR 0065 — Native mobile clients are composed applications, not a shared framework

**Status:** Proposed
**Date:** 2026-08-26
**Extends:** ADR-0024 (applications compose by synchronizing data), ADR-0006
(white-label product foundation — the extraction rule and its 2026-08-12
second-consumer amendment), ADR-0003 (composable deployment profiles)
**Owns:** the versioned client contracts a Dotmac native mobile application
implements — session context, data scope, queued mutation, push intent, the
logout/wipe participation contract, and the authentication state machine — plus
the proposed package boundaries and the release/adoption rules that would govern
them
**Does not own:** any server-side authority (every domain decision stays with
its module under the Dotmac source-of-truth standard); the browser/Jinja portal
surface; store release mechanics; the choice of authorization server, which is
an open owner decision recorded in § 9

---

## Context

Three Flutter applications exist in the fleet. None of them was designed against
a written client contract, and the resulting drift is measured, not assumed, in
[`docs/inventories/mobile-application-sources.md`](../inventories/mobile-application-sources.md)
(audited at `dotmac_sub` `1a3edf0eb`, `dotmac_crm` `a922decf`, starter
`531f7f8c`). The four facts from that inventory that force this ADR:

1. **`dotmac_sub/field_mobile` is a July 2026 directory copy of
   `dotmac_crm/mobile`.** Their `pubspec.yaml` and `pubspec.lock` are
   byte-identical, including the package name `dotmac_field` and the version
   `1.0.1+2`. Fifty-one files under `lib/` now differ and nine paths exist on
   only one side. Seventeen of the fifty-one differ on **contract** — different
   backend host, different outbox routing vocabulary, different rules about who
   owns the job-completion gate, different rules about whether a transport
   failure ends a session.
2. **Drift runs in both directions.** CRM's most recent mobile commit
   (`50beb0cb`, 2026-08-18) added a missing `await` in `login` and `verifyMfa`
   so that a secure-storage failure inside `_handleTokens` is caught rather than
   escaping the `try` frame. Sub's copy still has the unfixed form at both call
   sites. A correctness fix in the authentication path landed on the copy that
   is being deleted and never reached the copy that is being kept.
3. **The invariants a mobile client needs are implemented inconsistently or not
   at all.** The field database is opened with a plain `NativeDatabase` and is
   entirely unencrypted; logout clears the token store and leaves the database,
   the photo directory and the pending-ping file intact; exactly one of nine
   cached tables is partitioned by principal; the self-care on-disk response
   cache has no principal component in its key at all; the self-care push router
   accepts any raw internal path the server puts in the payload and otherwise
   guesses the destination by substring-matching the notification body.
4. **Neither field application has ever been released from a tag**, and Sub's
   field app has no release pipeline: its `field_mobile/docs/RELEASE.md` documents a workflow
   file that exists only in `dotmac_crm`, and its Xcode Cloud post-clone hook
   points at `$REPO/mobile`, which in `dotmac_sub` is the *self-care* app.

Two decisions Michael has already taken frame what may follow from this:

- **CRM mobile is a retirement target**, following the standing approved goal to
  retire `dotmac_crm` entirely. It gets no security or offline-sync work, and it
  **cannot be the second independent adopter** of any shared package.
- **Sub's two applications count as ONE product** for adoption purposes. The
  CRM/Sub copied field implementation is not two independent consumers.

Three failure modes have to be designed out before any mobile code moves:

1. **Extraction from a copy.** Two trees that share a `pubspec.lock` because one
   was `cp -r`'d from the other are not two consumers of a contract. Extracting
   from them produces a package whose "shared" shape is simply the first
   implementation's accidents, with two owners and no authority.
2. **A client that decides.** An offline client that has to render a disabled
   button will invent a rule to decide when to disable it. The field app did
   exactly this — `checklistDone && hasPhoto && hasSignOff` — and Sub then had to
   replace it with a server-supplied `JobCompletionRequirements`. A client
   caches decisions; it never makes them.
3. **Carrying browser assumptions onto a device.** A cookie, a CSRF token, a
   Jinja context and a session that dies when the tab closes are all artifacts of
   one transport. A mobile client has a keychain, a process that is killed and
   restored, a filesystem that outlives a logout, and a user who switches
   accounts on a shared device. Reusing the browser's frame produces contracts
   that are silently wrong on a phone.

**A note on scope and honesty.** At the base revision of this ADR (`531f7f8c`)
this repository contains **no** `WebFacetMount` and **no** ADR-0006 § 14; the
web facet runtime is in flight on an unmerged branch. This ADR therefore states
the mobile boundary on its own terms rather than deferring to text that is not
yet on `main`. If the facet amendment merges, the two must agree: a mobile
application is an independent client assembly, never a facet.

---

## Decision

### 1. A mobile application is a composed client assembly

A Dotmac native mobile application is an **independent application** in the sense
of ADR-0024. It owns its navigation, its local persistence, its platform
lifecycle, its device integrations and its release packaging. It composes:

- one or more **server products**, reached only through versioned HTTP APIs and
  push; never a shared database, never a shared cache, never a second writer;
- the **client contracts** in §§ 3–8 of this ADR, which it implements locally;
- product-owned screens, brand assets and copy.

Server modules remain authoritative for permissions, entitlements, prices,
workflow transitions, evidence policy and every other domain decision. **A mobile
client is a consumer of decisions and never a second decider.** Where a client
must render a decision offline it renders a *cached server decision with its
provenance and staleness*, not a locally recomputed one. `JobCompletionRequirements`
in `field_mobile` is the reference shape: the server states the requirement, the
client projects it, and the client's own advisory checklist is explicitly barred
from creating a completion gate.

**Native mobile is a separate client-assembly programme.** It does not extend
`WebFacetMount`, and no browser concept may enter a mobile contract:

| Browser concept | Mobile equivalent | Rule |
|---|---|---|
| session cookie | token pair in the platform keychain | never a cookie jar; never `Set-Cookie` for auth |
| CSRF token / double-submit | bearer credential + no ambient authority | **CSRF has no meaning here.** A mobile client sends no ambient credential, so a CSRF token would protect nothing and would create a false sense of coverage |
| Jinja request context | `MobileSessionContextV1` (§ 3) | a client has no server-rendered context to inherit |
| facet URL prefix / mount | application identity + `MobileDataScopeV1` (§ 4) | audience is an artifact identity, not a path prefix |
| "logged out when the tab closes" | explicit wipe participation (§ 7) | a device keeps everything until something deletes it |

### 2. Version markers are part of every contract

Each contract below carries an explicit `contract_version` string of the form
`<Name>V<major>`. The rules:

- **A field is never repurposed.** Changing a field's meaning is a new major.
- **Adding an optional field is not a new major.** Removing one, or making an
  optional field required, is.
- **A persisted record stores the version it was written under.** A reader that
  encounters an unknown major must fail closed — refuse the record, surface it —
  never coerce it. Silent coercion of a stale queued mutation is how a client
  replays an obsolete command against a changed server.
- **The wire version and the persisted version are the same string.** A client
  that queues `QueuedMutationV1` and later negotiates `V2` with the server must
  drain or migrate the `V1` queue explicitly; it may not send `V1` records under
  a `V2` contract.

### 3. `MobileSessionContextV1`

The authenticated identity a client holds, and the *only* thing that may be used
to derive a data scope. It is **not** an authorization decision and carries no
permission list: permissions are evaluated per request by the server.

| Field | Type | Required | Definition |
|---|---|---|---|
| `contract_version` | string, literal `"MobileSessionContextV1"` | yes | — |
| `principal_id` | opaque string | yes | Stable server-issued identifier for the authenticated subject. Opaque to the client: it is compared for equality and used as a scope component, never parsed, and never displayed. |
| `principal_kind` | enum: `subscriber` \| `staff` \| `vendor` \| `service` | yes | Which population the principal belongs to. Drives which application shell may run, not what the principal may do. |
| `tenant_id` | opaque string | yes | The tenant the session is bound to. Present even in a single-tenant deployment, where it is that deployment's one tenant. Never inferred from a hostname. |
| `session_generation` | integer, monotonically increasing per `principal_id` | yes | Bumped by the server on any event that invalidates prior sessions — password change, credential revocation, forced sign-out, account switch server-side. A client that sees a *higher* generation than it holds must wipe (§ 7) before continuing. A client never invents or increments it. |
| `issued_at` | RFC 3339 UTC timestamp | yes | When the server issued the session. |
| `access_expires_at` | RFC 3339 UTC timestamp | yes | When the access credential stops being accepted. Used for proactive refresh (§ 8). The client treats it as advisory for scheduling and authoritative for nothing: only a 401 is authoritative. |
| `refresh_expires_at` | RFC 3339 UTC timestamp | no | When the refresh credential stops being accepted, when the server bounds it. Absent means the server does not publish a bound. |
| `scope` | ordered list of opaque strings | yes | The coarse capability set the credential was issued for (e.g. an audience or client-scope list). **Advisory for UI affordance only.** A client may hide a control whose scope is absent; it may never conclude that an action is *permitted* because a scope is present. |
| `device_id` | opaque string | yes | Stable per-install identifier the client generates once and keeps across logout, so the server can maintain one session per device. Never derived from a hardware identifier. |
| `deployment_id` | opaque string | no | Which deployment the session belongs to, where a client can be pointed at more than one. Part of the data scope when present. |

**Non-fields, stated so they are not added later.** No permission list, no role
list, no entitlement, no plan name, no feature flags, no display name, no email,
no avatar URL. Those are either server decisions (fetched per request) or
profile data (fetched and cached like any other resource) — putting them in the
session context turns a session into a stale authorization cache.

**Storage.** The session context and its credentials are written to the platform
secure store as **one atomic record** under a single key, serialized together
with `contract_version`. This is stated as a contract because the self-care app
today writes access and refresh tokens as two separate, non-atomic writes with
no version marker — an interruption between them leaves a new access token
beside an old refresh token, with nothing recording that it happened.

### 4. `MobileDataScopeV1`

The partition key that **every** persisted record and **every** file path is
partitioned by. This is the contract's load-bearing invariant: an account switch
on a shared device must not be able to show one principal another's data, and it
must not depend on a wipe having succeeded.

```
scope_key = MobileDataScopeV1(deployment_id, tenant_id, principal_kind, principal_id)
```

| Component | Source | Definition |
|---|---|---|
| `deployment_id` | `MobileSessionContextV1.deployment_id`, or the literal `"default"` when the client targets exactly one deployment | isolates two deployments of the same product on one device |
| `tenant_id` | `MobileSessionContextV1.tenant_id` | isolates two tenants |
| `principal_kind` | `MobileSessionContextV1.principal_kind` | isolates a staff login from a vendor login by the same human |
| `principal_id` | `MobileSessionContextV1.principal_id` | isolates two principals |

**Rules.**

1. **Derivation is one-way and total.** The scope key is computed from the
   session context and nothing else. It is never taken from a server response
   body, a push payload, a deep link, or a locally decoded token claim the client
   trusts for authorization. (A client may *derive* the key from a token claim it
   already holds; it may not *authorize* on that claim. Deriving a cache
   partition from an unverified `sub` is safe because a wrong value produces a
   cache miss, never an escalation. That distinction must be written at the call
   site.)
2. **`scope_key` is a leading component of every database primary key**, of
   every cache key, and of every on-disk path segment for evidence files. Not a
   filter applied at read time — a component of the key, so a missing filter is a
   miss rather than a leak.
3. **A record whose `scope_key` does not equal the current session's is not
   readable.** Not "is filtered out": the read path must be incapable of
   returning it. Where the storage engine cannot enforce that, the repository
   layer must, and the test must show the failure it prevents.
4. **`scope_key` is never displayed, logged, or sent to telemetry.** It contains
   a principal identifier.
5. **There is no unscoped store.** Device-level preferences that legitimately
   survive a logout (theme, biometric opt-in, `device_id`, "prompt seen" flags)
   live in a separate, explicitly enumerated **device-preferences store** that
   holds no customer data and is documented as such. Anything not in that
   enumeration is scoped.

**Encryption is separate from scoping and both are required.** Scoping prevents
one principal's records from being read by another principal *in the app*.
Encryption prevents them from being read by anyone *outside* the app. A copied
database file must reveal no plaintext customer data — which is why full-database
encryption was chosen over column-level encryption for the field app: column
encryption still leaks schema, row counts and timestamps.

### 5. `QueuedMutationV1`

One record in the offline outbox. It is the client's promise that a user's
intent survives process death and a week without signal, and the server's
guarantee that replaying it is safe.

| Field | Type | Required | Definition |
|---|---|---|---|
| `contract_version` | string, literal `"QueuedMutationV1"` | yes | — |
| `scope_key` | `MobileDataScopeV1` | yes | Leading component of the primary key (§ 4). A mutation queued under one principal is never flushed under another. |
| `sequence` | integer, monotonic per `scope_key` | yes | Assigned at enqueue; defines total flush order within a scope. Never reused, never renumbered. |
| `operation_code` | string from a **declared registry** | yes | Names the server operation. A registered string, not an enum — a product names its own operations without changing shared code, the same rule ADR-0008 sets for setting domains and permission codes. An unregistered code fails at enqueue, naming the registry file as the fix; it must never fail for the first time at flush, a week later, on a technician's phone. |
| `idempotency_key` | UUIDv4 string, unique per `scope_key` | yes | Generated once at enqueue and **never regenerated on retry**. This is the value the server deduplicates on; the field app's existing `clientRef` / `client_event_id` / `client_ref` is this field under three names, and the contract collapses them to one. |
| `request_fingerprint` | lowercase hex SHA-256 of the canonical serialization of `(operation_code, payload)` | yes | Detects a payload edited after enqueue and detects two distinct intents colliding on one idempotency key. A server that receives a repeated `idempotency_key` with a *different* fingerprint must reject rather than deduplicate — that is a client bug, and silently returning the first result would hide it. |
| `aggregate_key` | opaque string | yes | Names the server-side aggregate this mutation orders against — typically the entity's identifier (a work-order id, an expense-request id). **Ordering is guaranteed within one `aggregate_key`, not globally.** Two mutations on different aggregates may flush concurrently or out of order; two on the same aggregate never do. This replaces the current all-or-nothing FIFO, where one parked entry stalls every unrelated mutation behind it. |
| `payload` | JSON object | yes | The operation's arguments. Carries no client-computed decision: no derived price, no computed permission, no locally decided lifecycle state. |
| `enqueued_at` | RFC 3339 UTC timestamp | yes | When the user's intent occurred, not when it was sent. The server records the *intent* time from this field and its own receipt time separately. |
| `expires_at` | RFC 3339 UTC timestamp | yes | After this instant the mutation must not be sent. An expired mutation moves to `expired` and is surfaced to the user as an action that did not happen — **never silently dropped and never silently sent late.** A month-old "arrived on site" is misinformation, not evidence. |
| `attempts` | integer | yes | Incremented per delivery attempt. |
| `state` | enum: `pending` \| `in_flight` \| `sent` \| `parked` \| `expired` \| `failed` | yes | `parked` is a conflict or an exhausted retry budget awaiting a human; it is **never** a delete. |
| `last_error` | string | no | Human-readable failure detail for the operator surface. Never a raw response body, which may contain customer data. |
| `correlation_id` | UUIDv4 string | yes | Generated at enqueue, sent as a request header, and echoed in client logs and server logs. Lets one queued action be traced end to end. Distinct from `idempotency_key`: the idempotency key is stable across retries so the server deduplicates; the correlation id may be per attempt if the client chooses, and it is the value that appears in telemetry — the idempotency key never is. |

**Rules.**

1. **Nothing is reserved before the effect.** A mutation is enqueued, then
   flushed; the client never pre-allocates a server-side identifier. This is
   ADR-0014's rule for the kernel's idempotency owner, applied on the client.
2. **A conflict parks; it never drops.** An HTTP 409 moves the record to
   `parked` with its payload intact and surfaces it. The current field app does
   this and the behaviour is preserved verbatim.
3. **A 5xx or a transport failure stops the flush for that `aggregate_key`
   only** and preserves order within it. A `429` honours `Retry-After`.
4. **A permanent 4xx fails the record and does not retry it**, with `last_error`
   set. Retrying a 422 forever is not resilience.
5. **Evidence files flush before the mutations that reference them.** A
   completion that names a photo must not arrive before the photo.
6. **Retention is the product's policy**, not the contract's: the contract
   requires that `sent` records be removable and that `parked` and `expired`
   records are not removed automatically.

### 6. `PushIntentV1`

What a push notification is allowed to say. **A push payload never contains a
client route, path, URL or deep link.** It names a typed intent and its
subject; the client alone decides what screen that maps to.

| Field | Type | Required | Definition |
|---|---|---|---|
| `contract_version` | string, literal `"PushIntentV1"` | yes | — |
| `intent_code` | string from a **declared registry**, per product | yes | e.g. `work_order.assigned`, `work_order.commented`, `invoice.due`, `ticket.replied`. A registered string, not an enum. An **unknown** `intent_code` resolves to the application's inbox — never to a guessed destination and never to a crash. |
| `subject_kind` | string from the same registry | yes | The kind of entity the intent is about (`work_order`, `invoice`, `ticket`). |
| `subject_id` | opaque string | yes | The entity's identifier. The client resolves it through its own router. |
| `tenant_id` | opaque string | yes | Used to decide whether the notification belongs to the currently signed-in scope. A push whose `tenant_id`/`principal_id` do not match the active session is **not** actioned; it is discarded or held, never used to navigate. |
| `principal_id` | opaque string | yes | Same purpose. |
| `session_generation` | integer | no | When present and *higher* than the client's, the client treats the push as a revocation signal (§ 8) rather than a navigation. |
| `issued_at` | RFC 3339 UTC timestamp | yes | For staleness: an intent older than the client's freshness bound opens the inbox rather than a detail screen. |
| `collapse_key` | string | no | Transport-level coalescing hint. Carries no meaning to the application. |

**Non-fields.** No `route`, no `path`, no `deep_link`, no `url`, no `link`. No
notification title or body used for routing. The self-care app today reads six
such keys, accepts any string starting with `/` verbatim as an in-app route, and
where none is present matches substrings of the notification prose against
hardcoded word lists. Under this contract both behaviours are contract
violations: **the server states an intent, the client owns navigation.**

Notification *display* text may of course be sent; it is rendered and never
parsed.

### 7. Logout and wipe participation

Local state does not disappear when a session ends unless something deletes it.
Today `field_mobile`'s logout is `_store.clear()` — the token store, and nothing
else. The database, the photo directory and the pending-ping file survive.

**The contract.** Every component that persists anything derived from a session
registers as a **wipe participant** at application composition. A participant
implements exactly one operation:

```
Future<void> wipe(MobileDataScopeV1 scope, WipeReason reason);
```

with these obligations:

1. **Total for its own storage.** A participant deletes every record, file and
   directory it owns for that scope. "Best effort" is not a permitted
   implementation; a participant that cannot delete something must raise.
2. **Idempotent.** Calling `wipe` twice is not an error. Calling it for a scope
   with nothing stored is not an error.
3. **Registered, not discovered.** Participants are an explicit list built at
   composition. A new store that is not registered is a defect the composition
   test must catch — the failure mode here is silent, and a store nobody
   remembered is exactly how customer data survives a logout.

**Atomicity guarantee.** A wipe is **journalled and resumable**, not
transactional — a filesystem and a keychain cannot participate in one
transaction, and pretending otherwise produces a wipe that half-succeeds and
reports success. Concretely:

1. A `wipe_pending` marker for the scope is written to the device-preferences
   store **first**, before any deletion.
2. Credentials are destroyed **second**, so an interrupted wipe leaves an
   unusable session rather than a usable session with partially deleted data.
3. Participants run in registration order; failures are collected, not thrown
   away.
4. The marker is cleared **only** when every participant has completed
   successfully.
5. **A `wipe_pending` marker found at startup blocks the application at launch
   until the wipe completes.** No screen renders, no session is restored, and no
   new login proceeds while a marker for any scope is outstanding.

The observable guarantee is therefore: *once a wipe begins, no data from that
scope is ever reachable again, even across process death, and the application
will not run until that is true.*

**The three triggers, and only these three.**

| Trigger | `WipeReason` | Scope wiped | Notes |
|---|---|---|---|
| Explicit user logout | `user_logout` | the active scope | Queued mutations that have not been sent are surfaced to the user **before** the wipe proceeds, with an explicit choice; a technician's unsent completion evidence is not discarded by a stray tap. |
| Account switch | `account_switch` | the outgoing scope | Runs to completion before the new session is admitted. A device-preferences entry may survive; nothing scoped does. |
| Authoritative revocation | `revoked` | the revoked scope | An HTTP 401 that survives one refresh attempt, or an observed `session_generation` higher than the held one. Not interruptible by the user, and not deferred. |

**Not triggers.** Network loss. A 5xx. A timeout. An expired access token whose
refresh has not yet been attempted. A failed background sync. App backgrounding.
Each of these has, in some client somewhere, been treated as a sign-out; under
this contract none of them is (§ 8).

### 8. The authentication and token-refresh state machine

States:

| State | Meaning |
|---|---|
| `signed_out` | No credentials held. The only entry to `authenticating`. |
| `authenticating` | A login or MFA exchange is in flight. |
| `authenticated` | A usable session context is held; requests carry the access credential. |
| `refreshing` | A single refresh exchange is in flight; requests queue behind it. |
| `locked` | An authenticated session held behind a device gate (biometric/PIN). Credentials are intact; no request is issued. |
| `degraded` | Authenticated, but the last refresh failed for a **non-authoritative** reason. Credentials are retained; reads serve from the scoped local store; queued mutations accumulate. |
| `wiping` | A wipe is running (§ 7). Terminal for the scope; the next state is `signed_out`. |

Transitions:

| From | Event | To | Rule |
|---|---|---|---|
| `signed_out` | credentials accepted | `authenticated` | Session context and credentials are written as **one atomic record** (§ 3). |
| `authenticated` | `access_expires_at` within the refresh skew | `refreshing` | Proactive; does not wait for a 401. |
| `authenticated` | HTTP 401 on any request | `refreshing` | At most **one** refresh-and-replay per originating request. |
| `refreshing` | new credentials issued | `authenticated` | Every request queued behind the refresh is released with the new credential; the originating request is replayed exactly once. |
| `refreshing` | refresh returns **401 or 403** | `wiping` → `signed_out` | **Authoritative refusal.** The refresh credential is dead; keeping it would loop. |
| `refreshing` | timeout, connection failure, DNS failure, or **5xx** | `degraded` | **The refresh credential is retained.** A server outage must not sign out a technician holding unsent evidence. |
| `degraded` | any successful request or refresh | `authenticated` | Recovery needs no user action. |
| `degraded` | HTTP 401 on a request, surviving one refresh attempt | `wiping` → `signed_out` | |
| any authenticated state | observed `session_generation` > held | `wiping` → `signed_out` | Server-driven revocation, from a response header, a session-context refresh, or a `PushIntentV1` carrying the field. |
| `authenticated` | device gate armed and app resumed | `locked` | |
| `locked` | gate satisfied | `authenticated` | An in-flight bootstrap must not be able to re-lock a session the user has just unlocked. |
| any | explicit logout | `wiping` → `signed_out` | Unsent mutations surfaced first (§ 7). |

**Four rules stated separately because each has already been violated somewhere
in the fleet:**

1. **Single-flight refresh.** At most one refresh exchange per scope is ever in
   flight. Concurrent 401s share it and every caller receives the same outcome.
   Both Sub apps implement this today; the contract makes it non-optional.
2. **Network failure and 5xx do NOT log the user out. An authoritative 401/403
   or a session-generation bump DOES.** `dotmac_crm/mobile` gets this wrong —
   it calls `onSessionExpired` on *any* `DioException`, so a timeout signs a
   technician out. `dotmac_sub/field_mobile` fixed it and its test
   "transient refresh failure preserves the session for retry" is the parity
   test any implementation must carry.
3. **Session-generation fencing.** Every response may carry the server's current
   generation for the principal. A client that observes a higher one wipes
   before its next request. This is what makes a server-side "sign out
   everywhere" real on a device that is offline at the time: the fence trips on
   the next successful contact, not on a push the device may never receive.
4. **The refresh transport is separate from the request transport.** A refresh
   must not pass through the client's own authentication interceptor, or a
   failing refresh recurses. Both Sub apps do this; it is contract, not style.

**Nothing in this section is an authorization decision.** A 403 on a *business*
request means the server refused an action; it does not end a session. Only a
401/403 on the **refresh exchange itself**, or a generation bump, is
authoritative for session lifetime.

### 9. Proposed package boundaries — proposed, not authorized

**No shared package exists today and none is authorized by this ADR.** ADR-0006
§ "The extraction rule" requires two independent consumers of the same contract,
a named owner, and a migration path; the inventory shows the fleet has none of
the three. Michael's 2026-08-26 rulings remove CRM from the adopter pool and
count Sub's two applications as one product. **The correct state today is
therefore: contracts published as a specification, implemented locally by each
application, with the duplication recorded and left in place.**

If and only if a second independent adopter is approved, these are the boundaries
a package split would have to respect. They are recorded now so that a future
extraction is a decision against a stated shape rather than a harvest of
whatever the first implementation happened to do.

| Proposed package | Would own | Would NOT own | Dependencies |
|---|---|---|---|
| `dotmac_mobile_contracts` | the types in §§ 3–6 and their serialization, the `operation_code` and `intent_code` registries, version negotiation | any I/O, any storage engine, any HTTP client, any Flutter import | none — pure Dart |
| `dotmac_mobile_session` | the § 8 state machine, single-flight refresh, generation fencing, atomic credential storage | which authorization server is used; login UI; MFA UI | `dotmac_mobile_contracts`, a secure-storage port |
| `dotmac_mobile_sync` | the `QueuedMutationV1` outbox, aggregate-ordered flush, conflict parking, evidence-file ordering | operation semantics; endpoint paths; any product's DTOs | `dotmac_mobile_contracts`, a database port |
| `dotmac_mobile_wipe` | the § 7 participant registry, journal and resumable wipe | what each participant stores | `dotmac_mobile_contracts` |

Layering is one-way and mirrors the server side's
`assembly → module → dotmac-ui → dotmac-kernel`:

```
application assembly → product feature packages → dotmac_mobile_sync
                                                → dotmac_mobile_session
                                                → dotmac_mobile_wipe
                                                → dotmac_mobile_contracts
```

Four rules bind any such package:

1. **No package may import a product's domain types**, and no package may import
   another product's package.
2. **No package may contain a product switch.** No `if (product == 'field')`, no
   `if (tenant == …)`, no per-deployment branch. A difference between products is
   a parameter or a port, or it does not belong in shared code — ADR-0024's rule.
3. **No package may import a UI framework** except where its stated purpose is
   UI. `dotmac_mobile_contracts` in particular imports nothing at all, for the
   same reason `dotmac-ui` is dependency-free: a product can adopt the contract
   without adopting a stack.
4. **No package may reach a network at rest-resolution time**, and none may hold
   a secret it did not receive from the application. The kernel's ADR-0009 rule —
   a secret is held, never dereferenced — applies unchanged on a device, where
   it is stronger: there is no trusted secret store to dereference *to*.

### 10. Release and adoption

These rules apply to any package created under § 9, and to the contract
specification itself.

1. **Immutable release.** A published version's content is frozen. A contract
   change is a new version, never an edit to a published one. This is the same
   rule ADR-0006 and the connector-manifest ledger apply on the server side, and
   for the same reason: a consumer pinned to a version must be able to identify
   what it pinned.
2. **Exact pin.** A consuming application pins an exact version. No caret, no
   range, no `git` ref, no path dependency in a shipped build.
3. **Conformance tests run without a path override.** A package's conformance
   suite must pass against the **published artifact**, resolved the way a real
   consumer resolves it. A suite that only passes against a local path override
   proves the working tree, not the release — which is exactly the class of
   evidence the starter's own product-first rule rejects.
4. **Adoption is claimed only with an oracle.** A version present in a
   `pubspec.yaml` on a default branch is not evidence that it is published,
   pinnable, or adopted. A reuse or adoption claim requires an external oracle
   carrying immutable coordinates — a release run, a peeled tag, or a store
   submission record. This is the starter's existing governance rule applied to
   Dart artifacts.
5. **Local-owner retirement gate.** An application may not both consume a
   package's contract and keep its own implementation of that contract. Adoption
   is complete only when the local implementation is **deleted**, not merely
   bypassed. A shadow period is permitted and must be time-boxed, with the
   removal commit named in the adoption record. Two implementations of one
   contract in one application is the same defect as two writers of one row.
6. **Reuse is never claimed before (4) and (5) hold.** Until then the honest
   statement is "the contract is specified and implemented independently", and
   this ADR requires that wording.

---

## Enforcement status

Honest as of 2026-08-26. **Where no check exists, the row says `none yet`.** No
row names a guard that has not been written.

| Rule | Enforced by | Status |
|---|---|---|
| §1 mobile is not a facet; no browser concept in a mobile contract | — | **none yet.** No `WebFacetMount` exists at this ADR's base revision and no Dart code is governed by this repository's tests. Review discipline only. |
| §2 version markers, fail-closed on unknown major | — | **none yet** |
| §3 `MobileSessionContextV1` field set; atomic credential record | — | **none yet.** The self-care app's non-atomic two-key write is a known open defect (inventory D3). |
| §4 `MobileDataScopeV1` is a key component everywhere | — | **none yet.** One table in `field_mobile` and one in-memory Riverpod test (`cache_for_identity_test.dart`) assert a related property; nothing asserts the rule. |
| §5 `QueuedMutationV1` shape; idempotency key stable across retries | `field_mobile/test/sync_service_test.dart` asserts FIFO, duplicate-`clientRef` no-op, 409 parking, 429 `Retry-After`, poison-entry capping and photo-before-mutation ordering — **for the current implementation, not for this contract** | partial, and not a conformance suite |
| §5 aggregate-scoped ordering | — | **none yet.** Both field apps flush globally FIFO; one parked entry stalls unrelated aggregates. |
| §6 `PushIntentV1`; no raw route in a payload | — | **none yet.** `field_mobile`'s `routeForMessage` already has the right shape (typed `type` + `work_order_id`); the self-care app is in direct violation (inventory D6). |
| §7 wipe participation; journal; three triggers | — | **none yet.** `field_mobile` logout clears only the token store (inventory D4). |
| §8 network failure and 5xx do not sign out | `field_mobile/test/auth_flow_test.dart::"transient refresh failure preserves the session for retry"` | asserted in one application; absent in `dotmac_crm/mobile`, which violates the rule |
| §8 single-flight refresh | `field_mobile/test/auth_flow_test.dart::"concurrent refreshes share one in-flight request and all get the new token"`; `mobile/test/api_client_test.dart` | asserted in both Sub applications |
| §8 session-generation fencing | — | **none yet.** No application has the concept. |
| §9 package boundaries, no product switches | — | **none yet.** No package exists. |
| §10 immutable release, exact pin, conformance without a path override | — | **none yet.** No package exists; **neither field application has ever been released from a tag** (inventory D11). |

Two of these will remain `none yet` for a structural reason worth stating: this
repository is Python and contains no Dart. A guard over a Flutter tree can only
live in the repository that holds it — Sub's `.github/workflows/mobile.yml`
today — or in a shared package's own suite once one exists. Writing an
aspirational guard name here would be exactly the "enforceable premise" failure
ADR-0018 forbids: an exemption or a claim whose premise nothing can check.

---

## Consequences

- **Three applications acquire a written target and none of them meets it
  today.** Twelve defects are already recorded against them in the inventory. This
  ADR does not fix any of them; it makes each one a deviation from a stated
  contract rather than an undiscovered property.
- **No package is authorized, and that is the deliverable.** ADR-0006 § 5 says
  recording duplication is the deliverable and removing it is not. A reviewer who
  expects this ADR to end in a `dotmac_mobile_*` package should read § 9 and the
  inventory's § 5.5 instead.
- **The second-adopter question is escalated, not answered.** The inventory
  nominates ERP "DotMac Frontline" with reasoning and three caveats; it is
  Michael's decision, and until he takes it § 9 stands at "no package".
- **CRM mobile is out of the picture and will keep drifting until it is
  deleted.** That is the accepted cost of the retirement decision. The `await`
  fix on `50beb0cb` will not be back-ported by this ADR; whether Sub's field app
  should take that one-line fix is a separate, narrow change with its own
  evidence.
- **The contracts commit to behaviour that is more expensive than what exists.**
  Aggregate-scoped ordering is harder than global FIFO. A journalled resumable
  wipe that blocks launch is harder than `_store.clear()`. Scoping every key is
  harder than clearing a directory on logout. Each is chosen because the cheaper
  version has a silent failure mode, and every one of those failure modes is
  present in the fleet today.
- **Nothing here is implemented by this ADR.** It changes no runtime behaviour in
  this repository, which contains no mobile code. It constrains what MOB-02 and
  later steps may build.

---

## References

- `docs/inventories/mobile-application-sources.md` — the measured evidence base
- `docs/adr/0006-white-label-product-foundation.md` — the extraction rule, and
  the 2026-08-12 amendment ("a second consumer is evidence, not permission")
- `docs/adr/0024-apps-compose-by-synchronizing-data.md` — applications are
  independent and compose over versioned APIs; no product/provider switches in
  shared behaviour
- `docs/adr/0008-manifest-declaration-registries.md` (the declaration-registry
  rule applied here to `operation_code` and `intent_code`)
- `docs/adr/0009-secrets-are-held-not-dereferenced.md`
- `docs/adr/0014-at-most-once-execution-has-one-owner.md` — the idempotency
  rules § 5 mirrors on the client
- `docs/adr/0018-an-exemption-must-be-enforceable.md` — why the
  enforcement table says `none yet`
