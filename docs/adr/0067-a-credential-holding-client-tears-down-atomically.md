# ADR 0067 — A client that persists a refreshable bearer credential tears down atomically

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-26
**Decision owner:** Michael
**Scope — read this before applying it.** This ADR binds **clients that persist a
refreshable bearer credential**: a native application, a device agent, a daemon,
a CLI that caches a token, a background worker holding a service credential. It
does **not** bind an ordinary server-side or browser cookie session, where the
server already owns the session record, teardown is a row deletion inside one
transaction, and the client holds nothing it can fail to delete. Applying these
four invariants to a cookie session would be ceremony without a hazard. The
scoping sentence is the ruling, not a summary of it.
**Extends:** ADR-0014 (at-most-once execution has one owner — the same "one
coordinator" reasoning applied to destruction rather than to effects)
**Owns:** the four teardown invariants below, for every Dotmac client in scope
**Does not own:** how any particular client stores credentials; what a server's
session model is; any authorization decision; the mobile client contracts, which
**implement** these invariants (ADR-0065 §§ 3, 7, 8) rather than restating them

---

## Context

A server session dies when a row is deleted. A client that **persists a
refreshable bearer credential** does not have that property, and every failure
this ADR exists to prevent comes from assuming it does:

- The credential outlives the process. A kill, a crash, a battery death or a
  forced update happens *between* the two writes that were supposed to be one.
- The credential outlives the account. A device is shared; a subscriber is
  replaced by another subscriber on the same handset.
- Deletion is spread across storage engines that cannot share a transaction — a
  keychain, a SQLite file, a photo directory, a preferences store. Half of them
  succeeding and reporting success is the normal failure, not the exotic one.
- The client is offline at exactly the moment the server decides the session is
  over, so "sign out everywhere" reaches a device that is not listening.

Three measured instances in the fleet, all at the audited revisions in
`docs/inventories/mobile-application-sources.md`:

1. **A logout that clears one of four stores.** `dotmac_sub/field_mobile`'s
   logout is `_store.clear()` — the token store. The Drift database, the photo
   directory and the pending-ping file all survive, on a device that is handed
   between technicians.
2. **A non-atomic credential write.** The self-care app writes its session
   across two keys, so an interruption between them leaves a credential without
   its context or a context without its credential — a state no reader has a
   correct branch for.
3. **A transport failure treated as a revocation.** `dotmac_crm/mobile` calls
   `onSessionExpired` on *any* `DioException`, so a timeout signs a technician
   out; and `field_mobile`'s own `auth_state.dart:82-86` clears the token store
   when `ensureFreshToken()` returns `null`, discarding a valid 30-day refresh
   token because the network was down at launch.

`dotmac_sub` draft PR #2717 — *"fix(mobile): atomic credentials, fenced sessions
and an encrypted scoped cache"* — is the change that repairs (1) and (2) in one
application and is the evidence base for this ADR. It is **open, not merged**, at
the time of this decision; that is recorded rather than glossed, because a rule
justified by an unmerged change must say so.

---

## Decision

**A client that persists a refreshable bearer credential tears down atomically,
under four invariants.** Each is stated with the failure it prevents, because a
teardown rule with no named failure is a rule nobody can argue with and nobody
applies.

### 1. The credential record is ATOMIC

The credential and everything a reader needs to interpret it — the principal, the
scope, the session generation, the expiry — are written and destroyed as **one
record**, in **one operation**. Never as several keys written in sequence.

*Prevents:* a process killed between two writes leaving a credential with no
context, or a context with no credential. There is no correct branch for that
state, so implementations invent one, and the invented one is usually "treat it
as signed out and clear everything" — which silently destroys unsent work.

A store that cannot write compound values atomically is given a single
serialized value to write. Serializing is cheap; a partially written session is
not.

### 2. Generation fencing, with a DURABLE half

The server maintains a monotonic **session generation** per principal and
publishes it on responses. A client that observes a generation higher than the
one it holds tears down before issuing its next request.

**The durable half is the invariant, not the fence.** The client's held
generation is persisted alongside the credential (§ 1) and compared on **cold
start**, before any request is issued, not only in memory during a live session.
A fence that lives only in process memory is defeated by the most ordinary event
on a device: the process being killed.

*Prevents:* a server-side "sign out everywhere" that never reaches a device which
was offline when it was issued. The fence trips on the next successful contact,
which is a guarantee; a push notification is not.

### 3. ONE wipe coordinator, and no subset clears

Every component that persists anything derived from the session **registers** as
a wipe participant, and teardown runs through **one coordinator**. Three rules
follow:

- **Registered, never discovered.** The participant list is explicit and built at
  composition. A store nobody remembered to register is exactly how data
  survives a logout, and the failure is silent.
- **No subset clear.** No component clears its own storage on a session
  transition on its own initiative. `_store.clear()` at a call site is the defect
  this invariant names; a component that wants its data gone asks the
  coordinator, which then wipes **all** of it.
- **Journalled and resumable, not transactional.** A keychain and a filesystem
  cannot join one transaction. A marker is written **before** any deletion,
  credentials are destroyed **second** so an interruption leaves an unusable
  session rather than a usable session over half-deleted data, participants run
  in registration order with failures collected rather than discarded, and the
  marker clears only when every participant has succeeded. **A marker found at
  start-up blocks the client until the wipe completes.**

*Prevents:* the "logout" that clears one of four stores, and the wipe that
half-succeeds across a process death and reports success.

### 4. Transport failure is NOT revocation

A timeout, a connection failure, a DNS failure, a TLS failure and a **5xx** are
transport facts. **None of them ends a session, and none of them destroys a
credential.** Only an **authoritative refusal** does: a 401 or 403 **on the
refresh exchange itself**, or an observed generation bump (§ 2).

Two corollaries that are violated more often than the rule itself:

- **A 403 on a business request is not a session event.** It means the server
  refused an action. Session lifetime and authorization are different questions
  with different owners.
- **A failed *restore* is not a failed *authentication*.** A client that cannot
  reach the server at cold start is offline, not signed out. Clearing the
  credential store there discards a valid long-lived refresh token and converts a
  coverage hole into a lockout — and, once authentication is federated
  (ADR-0069), into a lockout only a reachable identity provider can end.

*Prevents:* a server outage or a coverage hole signing out a workforce that is
holding unsent evidence.

---

## Relationship to ADR-0065

ADR-0065 §§ 3, 7 and 8 state these invariants **for the native mobile client**,
in that client's own vocabulary — `MobileSessionContextV1`'s atomic record, the
wipe-participant registry with its journal and its three triggers, and the
authentication state machine's `degraded` state.

**This ADR is their owner; ADR-0065 is a consumer.** The mobile sections are the
mobile expression of these four invariants, not a parallel statement of them. If
the two ever disagree, this ADR wins and ADR-0065 is corrected — one owner per
rule, per the Dotmac source-of-truth standard.

Any other in-scope client — a device agent, a daemon, a token-caching CLI —
implements these four invariants directly and does not inherit ADR-0065's
mobile-specific vocabulary.

---

## Enforcement

**`none yet` in this repository, and the reason is structural.** Every in-scope
client in the fleet today is a Flutter application, and `dotmac_starter_mt` is a
Python repository holding no Dart: it cannot run an analyzer or an application
test over any of them. A guard over a client tree can only live in the repository
that holds it.

| Invariant | Enforced by | Status |
|---|---|---|
| §1 atomic credential record | — | **none yet.** `dotmac_sub` draft PR #2717 implements it for one application; no test asserts the rule as a rule. |
| §2 generation fencing with a durable half | — | **none yet.** No application held the concept before PR #2717. |
| §3 one wipe coordinator; registered participants; no subset clears | — | **none yet.** A composition test that fails when a persisting component is unregistered is the shape this needs, and it has to live where the composition does. |
| §4 transport failure is not revocation | `dotmac_sub/field_mobile/test/auth_flow_test.dart::"transient refresh failure preserves the session for retry"` — **for the refresh path only** | partial, in one application. The *restore* path (`auth_state.dart:82-86`) violates the same rule and nothing catches it. |

Naming an intended guard here would be the "enforceable premise" failure ADR-0018
forbids. Each row moves off `none yet` when a check exists in the repository that
holds the client.

---

## Consequences

- **Teardown becomes a composition concern, not a call-site one.** Registering a
  participant is now part of adding any store that persists session-derived data,
  and `_store.clear()` at a call site becomes a reviewable defect.
- **Three open defects are now deviations from a stated rule** rather than
  undiscovered properties: the one-of-four logout, the two-key credential write,
  and the restore path that clears on a network failure.
- **The rule stops at the scope line.** A browser cookie session, a server-side
  session row and a request-scoped credential are explicitly out. Widening this
  ADR to them would be a new decision with its own evidence, and there is none.
- **Nothing here is implemented by this ADR.** It changes no runtime behaviour in
  this repository, which holds no in-scope client. It constrains what every
  in-scope client must do.

---

## References

- `docs/adr/0065-mobile-clients-are-composed-applications.md` §§ 3, 7, 8 — the
  mobile expression of these four invariants
- `docs/adr/0069-mobile-authentication-federates-to-the-existing-identity-provider.md`
  — why § 4's restore corollary gets more expensive under federated login
- `docs/adr/0014-at-most-once-execution-has-one-owner.md` — the one-coordinator
  reasoning this ADR applies to destruction
- `docs/adr/0018-an-exemption-must-be-enforceable.md` — why the enforcement table
  says `none yet`
- `docs/inventories/mobile-application-sources.md` — the measured defects
- `dotmac_sub` draft PR #2717, *"fix(mobile): atomic credentials, fenced sessions
  and an encrypted scoped cache"* — **open, not merged** as of 2026-08-26
