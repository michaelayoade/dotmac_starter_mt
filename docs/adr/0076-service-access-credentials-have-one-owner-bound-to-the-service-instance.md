# ADR-0076: Service access credentials have one owner, bound to the service instance

- Status: Proposed (draft; build-once direction approved by Michael 2026-10-19, ADR text not yet accepted)
- Date: 2026-10-19
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0058 (adds an eleventh narrow owner), ADR-0040 (narrow independent
  owners), ADR-0038 (adds a disposition: the AccessCredential family leaves
  "excluded everywhere"; network-access keeps the projection)
- Amends (as an explicit exception): ADR-0017 (adoption is the scarce resource;
  this owner is adopted ahead of the ADR-0038 suite cutover)
- Related: ADR-0008 (declaration registries), ADR-0009 (secrets are held, never
  dereferenced), ADR-0011/0012 (settings resolution and inheritance), ADR-0014
  (at-most-once execution), ADR-0020 (billing owns receivables; a sibling
  decision on the same cutover), ADR-0024 (apps compose by synchronizing data;
  no sibling imports), ADR-0025 (imports own the run)

This is a documentation-only draft. It authorises no code, no migration and no
production change. Decision labels D2-D9 refer to the decisions Michael
approved on 2026-10-19; D1, D6, D7 and D10 concern the temporary Sub bridge or
separate security corrections and are tracked outside this ADR.

## Context

Nothing in Starter owns the credentials a subscriber's access service
authenticates with. The surrounding owners all exclude them by name:

- `dotmac-network-access` is the projection, observation and session owner. Its
  extraction contract states it is not commercial eligibility, IPAM, generic
  inventory, provider clients or credentials.
- The kernel human-credential facility (`credential_lifecycle`) covers human
  passwords. Its source inventory lists `AccessCredential` as a device/service
  credential and out of scope.
- `dotmac-services` owns the service instance lifecycle but not a secret with its
  own lifecycle, and it carries no link from a service to the service it
  replaced.
- `dotmac-service-access-policy` (SAP) decides allow/restrict/deny per service.
  Credential suspension must not become a second access-suspension authority.
- The router control plane configures routers to accept CoA; it does not manage
  per-subscriber identities.

Sub, the only qualifying production source, keeps these credentials in an
`AccessCredential` table and changes them through several parallel writers.
Staff report two symptoms: a new service silently receives new PPPoE
credentials unless the old ones are copied over by hand, and credentials appear
non-editable afterwards. Analysis of the Sub source found the first is caused
by an inherit rule that is too narrow, a reactivation path that keeps the login
but overwrites the secret, and a typed login/password that is discarded at
activation. The second is mostly correctness: an edit can rename the wrong
service's credential, a RADIUS failure is swallowed, the old login keeps
authenticating until a sweep, and no session is disconnected. These are
symptoms of there being no single owner with governed commands and a defined
effect contract, which is precisely the gap this ADR closes.

## Decision

### 1. A new owner: `dotmac-access-credentials` (D2)

A new tenant-plane package, `dotmac-access-credentials`, owns the identity and
secret a service authenticates with. It has one `mod_*` namespace and one
migration lineage; the short code and prefix are chosen against the manifest
collision test at build time.

It owns: credential identity, secret custody, lifecycle state, the
inherit/ask/new decision at service replacement, history, and the commands and
events that express them. It does not own: RADIUS user projection, session
control (network-access), service lifecycle (services), access-restriction
decisions (SAP), provider transport (Integrator, ADR-0038), or human,
machine and infrastructure credentials (see Reuse).

**ADR-0017 exception.** Adopting this owner before the ADR-0038 network suite
cutover is an explicit, owner-directed exception to ADR-0017's supply-push
moratorium, justified by live staff impact and by the fact that until the suite
cuts over, Sub's own projection still reads the mirrored `AccessCredential`
rows, so early adoption creates no second authority. The exception covers this
one package and its adapter to Sub; it is not a precedent for the rest of the
network suite, whose sequencing under ADR-0038 is unchanged.

### 2. The binding is to the service instance, not the subscription

`service_reference` is an opaque `dotmac-services` instance reference. A plan
change or commercial re-subscription on the same service instance never touches
the credential. By construction this removes most of today's "new service"
cases; only a genuine replacement service instance (relocation, technology
change) needs the inherit policy of section 4.

### 3. Model and resting states (D4)

A credential carries: `id`, `tenant_id`; `service_reference`; an optional opaque
`access_point_reference`; `kind` (`pppoe`, `ipoe_option82`, later `mac_binding`,
`hotspot`, `cpe_management`); `identifier` (username, circuit/remote id or MAC;
unique per tenant among live credentials); `secret_ciphertext`;
`secret_fingerprint` (keyed HMAC); `secret_version`; `state`; `row_version`
(optimistic lock); `policy_version`; and `previous_secret_ciphertext`, held only
until the new version converges and then destroyed (this enables a non-guessing
revert).

**Resting states are exactly four:** `issued` (created, not yet usable),
`active`, `suspended` and `revoked` (terminal). `suspended` is a **security
hold only** (compromise, abuse investigation). Commercial and collections
restriction stays with SAP. `suspended` is fed to SAP as an input signal (a
blocker kind such as `credential_security_hold`, resolving to DENY), so access
state keeps one decider (SAP) and one enforcer (network-access). This depends
on the SAP blocker-kind registry being available; until then the signal is
emitted but not consumed.

**Rebind (inherit) and rotate are transitions, not resting states.** A
credential that was rebound is simply `active` on a new service; history
records the transition. This keeps the state machine small and the effect
pipeline uniform.

### 4. Replacement and inheritance (D3, D5, D8)

**D8: `dotmac-services` gains `replaces_service_reference`** on `CreateService`,
and emits `services.service.replaced`. This is the only non-guessing predecessor
signal. Inferring a predecessor from "exactly one ended credential" is guessing
and is permitted only as the unattended fallback below. The change to
`dotmac-services` is a contract amendment made by the implementing PR for that
package, not by this ADR.

**D3: an ended service's credential may be inherited by the same customer
only.** The owner refuses a rebind if the predecessor service is not in an
ended state (a setting, section 6) or if the customer differs. Cross-customer
reuse is impossible, not merely discouraged. Accepted consequence: a credential
from a service that ended for any reason, including cancellation or expiry, can
return to use on the same customer's next service with the same secret. If a
service ended because the credential was compromised, inheriting it is wrong;
such cases must use `new` or a rotation, and the owner must expose enough
history (end reason carried from the predecessor, where known) for the policy
and for staff to see this.

**D5: the default at replacement is to inherit when the predecessor is
unambiguous, otherwise ask.** `ResolveCredentialForService` is a pure decision
read that returns `inherit(credential_id)`, `new`, or `ask_required(candidates)`
under policy and records a decision. When interactive asking is not possible
(bulk, scheduled or automated activation), the unattended fallback is
`inherit_if_unambiguous`: inherit only when exactly one ended candidate exists
and no other live service of the customer holds an active credential; otherwise
issue a new credential. The alternatives `new` and `hold_activation` remain
selectable settings but are not the default.

Credential continuity at activation is an invariant of the owner's policy, not
an automation. The service-activation coordinator calls
`ResolveCredentialForService` and then `RebindCredential` or
`IssueCredential` plus `ActivateCredential`, and this works with the rules
engine paused or absent. A rules engine, if present, adds conditional
overrides only and calls the same commands; the owner re-validates at
execution.

### 5. Governed commands, audit and idempotency

All state change happens through commands:

| Command | Transition | Notes |
|---|---|---|
| `IssueCredential` | none to issued | Identifier per username scheme; secret generated or supplied per password policy. |
| `ActivateCredential` | issued to active | Normally called by the activation coordinator. |
| `ResolveCredentialForService` | decision only | No mutation beyond the decision record. |
| `RebindCredential` | active to active (service changes) | Carries from/to service references and predecessor evidence. Refused if the predecessor is not ended or the customer differs. |
| `RotateSecret` | active to active (version + 1) | Generated or supplied. Previous ciphertext retained until convergence. |
| `ChangeIdentifier` | active to active | Tenant-wide uniqueness. The old identifier travels in the effect so it is deleted downstream. |
| `SuspendCredential` / `ResumeCredential` | active to/from suspended | Security reasons only. |
| `RevokeCredential` | any to revoked | On service termination not inherited within a grace window (setting). |

Every command runs under `execute_once` (ADR-0014) keyed on tenant, scope
`access_credentials.<command>` and a caller-supplied idempotency key; checks
`expected_row_version`; stages an audit record (actor, reason, credential id,
service references, `identifier_changed` and `secret_changed` booleans, never
the secret); and writes exactly one outbox event in the same transaction. No
external call occurs inside that transaction.

### 6. Every policy is a tenant setting (nothing hard-coded)

Policy keys are declared through the settings registry (ADR-0008/0011/0012),
tenant-scoped, with narrower scopes (for example site or POP) only where the
setting declares that it inherits. Illustrative keys, to be finalised at build:

- `access_credentials.on_replacement_service`: `inherit` | `ask` | `new`
  (Starter default: inherit-if-unambiguous-else-ask per D5).
- `access_credentials.on_additional_service`: `new` | `ask`.
- `access_credentials.inherit_from_service_states`: the ended service states
  that qualify as a predecessor.
- `access_credentials.ask_fallback_unattended`: `inherit_if_unambiguous`
  (default) | `new` | `hold_activation`.
- `access_credentials.username_scheme`: subscriber number, sequence
  (prefix/padding/start) or template.
- `access_credentials.password_policy`: length, alphabet, whether supplied
  passwords are allowed, minimum.
- `access_credentials.rotation`: none, every N days, on compromise only.
- `access_credentials.change_disconnect`: `immediate_after_projection` |
  `after_cpe_readback` | `none`.
- `access_credentials.notify_on` and the delivery mode (see D9, section 11).
- `access_credentials.revoke_grace_after_termination`: a duration.
- Retry/backoff bounds for the effect pipeline (section 8).

Code contains no tenant-specific literal for any of these; the code default is
the declared setting default, and a tenant row overrides it.

### 7. Secret storage: encrypted in the tenant database

Per-subscriber secrets are tenant data, not settings and not deployment
secrets. They are stored as ciphertext in the tenant database under a key
supplied by the kernel settings-crypto `KeyProvider`/`SecretSource`, loaded at
startup and held in memory (ADR-0009). Only the key may originate in OpenBao.
Per-subscriber `bao://` references are rejected: thousands of dereferences on
the request path would violate ADR-0009 and turn OpenBao into a hot-path
database.

Plaintext exists in exactly two places: inside the owner's in-process
`materialize_for_projection(credential_id, secret_version)` port, callable only
by the projection connector role; and in a step-up `reveal` read that is
rate-limited and audited. It never appears in events, audit, logs, exceptions
or drift records. Comparison uses the keyed fingerprint. Key rotation is the
kernel's re-encryption concern, not a credential-lifecycle command.

### 8. Command to effect contract with network-access and Integrator

1. The owner commits the state change and an outbox row in one transaction.
2. `dotmac-network-access` consumes the event and owns the RADIUS user
   projection (ADR-0038 lists RADIUS users under network-access). It issues a
   projection request to the Integrator RADIUS connector carrying credential id,
   secret version, identifier and the identifiers to delete. The connector pulls
   plaintext through `materialize_for_projection`, then per target performs
   delete-and-insert in one target transaction.
3. Convergence requires every target to report the expected fingerprint.
   Network-access then emits `projection_converged(version)`; the owner marks
   the version converged and destroys `previous_secret_ciphertext`.
4. Disconnect follows `change_disconnect`: `immediate_after_projection` asks
   network-access to close sessions of the old identifier via Integrator
   CoA/Disconnect; `after_cpe_readback` first waits for the pon-access CPE
   readback; `none` sends nothing.
5. **Retries.** Outbox delivery is at-least-once; consumers are `process_once`.
   Exponential backoff to a configured cap. After the configured number of
   failures the effect enters an explicit **failed-projection** state, the owner
   emits `projection_failed`, and an operational escalation is opened. A failed
   projection is a visible, queryable state, never a swallowed log line.
6. **RADIUS fails mid-rotation.** The owner holds version N+1, RADIUS holds N.
   The CPE keeps working on N and **no disconnect is sent before convergence**.
   Staff see "pending on target X" and can retry or revert; revert is
   `RotateSecret` supplied from the retained previous ciphertext, so nothing is
   re-guessed. Partial multi-target success is not convergence, and the
   fingerprint drift check flags the mismatched target.
7. **Identifier change.** The old identifier is deleted in the same target
   transaction that inserts the new one; on failure both remain as they were.
   Old sessions are disconnected only after convergence.

### 9. Capability-registry entries (ADR-0008 vocabulary)

Declared through the manifest registries; inert where the consuming registry
(for example the kernel owner-command registry or the rules-engine vocabulary)
does not yet exist.

- **Capabilities/permissions:** `access_credentials.manage`,
  `access_credentials.reveal`; permissions `access_credentials:read`,
  `access_credentials:change` (rebind, rotate, identifier),
  `access_credentials:reveal` (step-up, audited) and
  `access_credentials:suspend`.
- **Reads:** `credential.by_service`, `credential.history`,
  `credential.resolve_preview(service_ref, predecessor_ref?)`,
  `credential.effect_status`.
- **Signals (outbox event types):** `access_credential.issued`, `.activated`,
  `.rebound`, `.secret_rotated`, `.identifier_changed`, `.suspended`,
  `.resumed`, `.revoked`; effect results `.projection_converged`,
  `.projection_failed`, `.session_disconnect_requested`; and
  `.rotation_due` (durable timer). Payloads carry ids, kind, versions and
  fingerprints, never secrets.
- **Commands:** the table in section 5.
- **Proposed rules-engine vocabulary:** triggers `services.service.replaced`,
  `access_credential.rotation_due`, `access_credential.projection_failed`;
  facts `credential.state`, `credential.age_days`, `credential.kind`,
  `service.technology`; actions `access_credentials.rebind`, `.rotate`,
  `.notify` (record/notify effects only, none that impose).

Both directions are CI-governed per ADR-0008: a declared member with no consumer
fails, and a consumed member nothing declares fails.

### 10. Import of existing Sub credentials, as-is, nothing guessed

Import is a governed `dotmac-imports` run (ADR-0025): preview, approve, run.
For each Sub `AccessCredential` row: the source id is kept as `source_ref`;
`identifier` is the username; `kind` derives from the connection type and
option-82 fields; `state` is `active` if the source row is active, else
`revoked`; `service_reference` is an opaque Sub-subscription reference until
Services adoption maps it. The secret is decrypted with Sub's key and
re-encrypted with the Starter key inside the import worker, never logged, with a
fingerprint recorded on both sides.

Rows that cannot be imported faithfully go to an exceptions queue as
`needs_review` and nothing is inferred: one-way-hash secrets, decrypt failures,
rows with no service binding where more than one active row exists, and
duplicate active identifiers.

Adoption proceeds by shadow, then cutover. Sub remains the writer during a
dual-run in which the owner computes `ResolveCredentialForService` for each Sub
activation and a drift report compares decisions and row fingerprints; the gate
is zero unexplained drift over an agreed window. At cutover Starter becomes the
authority and Sub's table becomes a read projection with exactly one writer (the
Starter sync adapter), so Sub's existing RADIUS population keeps working
unchanged until the network suite cutover. At suite cutover, network-access
consumes the owner's events directly.

### 11. Sub's `ensure_pppoe_credential` becomes a thin call

After cutover, `ensure_pppoe_credential` is reduced to: resolve for the service,
then rebind or issue-and-activate, then return the same outcome dataclass
(credential id, username, disposition). All decision logic leaves Sub. Every
other Sub writer of credentials (admin create/edit, bulk activation, REST
PATCH/DELETE, binding helpers, enforcement deactivation, reseller onboarding,
restore tooling) is retired or redirected to commands, and an architecture
ratchet test pins the writer set so it can only shrink.

**D9: move away from sending plaintext passwords by SMS or email**, towards a
reveal link or customer self-care. The `notify_on` setting selects the delivery
mode; notifications state that a credential changed and link to an
authenticated, expiring reveal, rather than carrying the secret. Plaintext
delivery may remain as a tenant-selectable transitional mode only until the
reveal/self-care path ships, and is recorded as debt, not as a design option.

### 12. The Sub bridge is a temporary mapping

Before the owner exists, a minimal Sub-side bridge may carry corrections plus
a setting-gated inherit rule. Its settings map one-to-one onto the Starter keys
(`on_replacement_service`, `inherit_from_service_states`, `change_disconnect`),
its default preserves current behaviour, it does not accept the Starter-only
value `ask`, and it is deleted when `ensure_pppoe_credential` becomes the thin
call. The bridge is a Sub change, governed by Sub's own feature-freeze
exception process; this ADR neither authorises nor depends on it beyond keeping
the key names aligned.

## Reuse

| Candidate | Disposition |
|---|---|
| PPPoE credential | In scope, phase 1: the subject of this ADR |
| IPoE / DHCP option-82 identity | In scope, phase 1 (`ipoe_option82`; no secret) |
| MAC binding | In scope, phase 2 (`mac_binding`); today an observation, not an authority |
| Hotspot/voucher login identity | Later; voucher issuance is a distinct commercial concern |
| ONT web-management credentials | Partial, phase 2 (`cpe_management`); lifecycle fits, projection is TR-069 via pon-access, so a second effect consumer |
| Wi-Fi keys | Partial; customer-owned and self-care changeable, inherit policy irrelevant, effect via pon-access |
| At-rest key rotation of desired-config credentials | Not a candidate: re-encryption belongs to kernel crypto rotation |
| ACS/TR-069 provider credentials | Not a candidate: machine credentials, Integrator connector plus OpenBao (ADR-0009) |
| Router, NAS, OLT API/SSH, SNMP secrets | Not candidates: infrastructure secrets, Integrator / router control plane |
| Staff device login | Not a candidate: human identity tied to IdP and RBAC; shares RADIUS transport only |
| Portal/customer logins | Not a candidate: kernel `credential_lifecycle` (human) |

A sweep keyed on a `*Credential` name must not pull the non-candidates in.

## Security considerations

- Secrets are encrypted at rest under a held key; plaintext is confined to the
  projection port and an audited, step-up, rate-limited reveal.
- Secrets never reach events, audit, logs, exception text or drift output; a
  regression test asserts this on every path.
- The widening in D3 (an ended service's credential can be reused) is accepted
  deliberately and bounded: same customer only, ended-state list is a setting,
  compromise cases use `new` or rotate.
- Credential `suspended` is a security hold and is not a billing lever; SAP
  remains the sole decider of access restriction.
- Disconnect never precedes convergence, so a failed projection cannot lock out
  a working CPE; TR-069-managed CPE may need `after_cpe_readback` to avoid an
  outage window.
- Reveal and change require distinct permissions; reveal is not implied by
  customer read access.
- Import re-encrypts inside the worker; one-way-hash and undecryptable secrets
  are quarantined, never reconstructed.
- Existing Sub exposures (plaintext password rendered into an edit page, reveal
  gated by a broad read permission) are separate corrections tracked outside
  this ADR (D10) and are not fixed by it.

## Consequences

- One owner, one state machine and one event vocabulary replace several Sub
  writers; the credential edit failures become structurally impossible rather
  than patched.
- Network-access gains a new event consumer and a failed-projection state it
  must expose.
- `dotmac-services` gains a field and an event; SAP gains a blocker kind.
- ADR-0058, the ADR-0038 dispositions ledger and ADR-0017 each need a short
  pointer amendment when this ADR is accepted.
- Until the Sub cutover, two representations exist (Sub table, Starter owner);
  the shadow gate and single-writer rule are what keep that safe.

## Alternatives rejected

- **Widen `dotmac-network-access`.** Contradicts its extraction contract and
  mixes custody of secrets with projection.
- **Widen `dotmac-services`.** Violates the narrow-owner rule; the credential has
  its own lifecycle and secret handling.
- **Per-subscriber OpenBao references.** Breaks ADR-0009 and the request path.
- **Rebound and rotated as resting states.** Multiplies states for what are
  transitions recorded in history.
- **Infer the predecessor from credential history alone.** Guessing; allowed
  only as the bounded unattended fallback.
- **Fix only inside Sub.** Re-thickens a frozen product and leaves a second
  credential feature to retire.

## Open questions

1. Where is the written lifecycle state-machine direction this model should
   follow? It was not found; the model follows the owner-declared closed states,
   typed transition commands and `expected_version` pattern visible in Starter.
2. Short code and table prefix for the manifest collision test.
3. Whether the SAP blocker-kind registry lands before this owner, or the
   `credential_security_hold` signal is emitted unconsumed in the interim.
4. Whether the reveal link and self-care path (D9) ship with phase 1, and how
   long a transitional plaintext mode is tolerated.
5. Default grace period before revocation after termination, and the default
   ended-state list.
6. Whether the predecessor's end reason is available from `dotmac-services` for
   the compromise case in D3.
7. Shadow-window length and the drift threshold for the Sub cutover gate.
8. Phase-2 kinds (`mac_binding`, `cpe_management`) and whether they share this
   package or become siblings under the same pattern.
