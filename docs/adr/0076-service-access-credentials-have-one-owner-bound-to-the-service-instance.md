# ADR-0076: Service access credentials have one owner, bound to the service instance

- Status: Proposed (draft; build-once direction approved by Michael 2026-10-19, ADR text not yet accepted)
- Date: 2026-10-19
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0058 (adds an eleventh narrow owner), ADR-0040 (narrow independent
  owners), ADR-0038 (adds a disposition: the AccessCredential family leaves
  "excluded everywhere"; network-access keeps the projection)
- Exception to: ADR-0038's single-suite network cutover sequencing (ADR-0017's
  general moratorium was relaxed by its own later amendments; this is the
  operative sequencing exception)
- Related: ADR-0008 (declaration registries), ADR-0009 (secrets are held, never
  dereferenced), ADR-0011/0012 (settings resolution and inheritance), ADR-0014
  (at-most-once execution), ADR-0020 (billing owns receivables; a sibling
  decision on the same cutover), ADR-0024 (apps compose by synchronizing data;
  no sibling imports), ADR-0025 (imports own the run)

This is a documentation-only draft. It authorises no code, no migration and no
production change. Decision labels refer to the decisions Michael approved on
2026-10-19, each restated in one line where used:

- D2: a new owner package, adopted ahead of the ADR-0038 suite cutover as an
  explicit exception.
- D3: an ended service's credential may be inherited by the same customer only.
- D4: resting states issued, active, suspended (security hold only, fed to SAP)
  and revoked; rebind and rotate are transitions.
- D5: at replacement, inherit if the predecessor is unambiguous, otherwise ask;
  the unattended fallback is `inherit_if_unambiguous`.
- D8: `dotmac-services` gains `replaces_service_reference`.
- D9: move away from sending plaintext passwords by SMS or email, towards a
  reveal link or self-care.

Decisions about the temporary Sub bridge and separate security corrections of
existing Sub exposures are tracked outside this ADR. The one marked
"proposed; requires Michael's confirmation" in section 8 is not yet decided.

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
  replaced. Its only ended state is TERMINATED and its transition reason is free
  text, so it cannot by itself say why a service ended.
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
migration lineage. The namespace and prefix are allocated in the package's
first commit against the manifest collision test; nothing is reserved here.

It owns: credential identity, secret custody, lifecycle state, the
inherit/ask/new decision at service replacement, history, and the commands and
events that express them. It does not own: RADIUS user projection, session
control and delivery retry (network-access and Integrator), service lifecycle
(services), access-restriction decisions (SAP), or human, machine and
infrastructure credentials (see Reuse).

**Product-first source.** The qualifying source is Sub: its PPPoE credential
service, the access-credential secret handling and the PPPoE auto-generation
tests. The package's first commit carries an `EXTRACTION.toml` at
`audit-complete` naming these.

**Sequencing exception.** Adopting this owner before the ADR-0038 network suite
cutover is an explicit, owner-directed exception (D2), justified by live staff
impact and by the fact that until the suite cuts over Sub's own projection
still reads the mirrored credential rows, so early adoption creates no second
authority. It covers this one package and its adapter to Sub and is not a
precedent for the rest of the suite.

**Same-review obligations (ADR-0038 section 1).** The implementing change set
must, in the same review, add the `AccessCredential` family as a new row in the
`network-module-dispositions.toml` ledger (it is not listed there today, so
this is an addition, not the move of an exclusion), amend ADR-0038 and ADR-0058
with a pointer, and update the canary
`tests/architecture/test_network_module_decomposition.py`. This ADR alone does
not edit them.

### 2. The binding is to the service instance, not the subscription

`service_reference` is an opaque `dotmac-services` instance reference. A plan
change or commercial re-subscription on the same service instance never touches
the credential. Only a genuine replacement service instance needs the inherit
policy of section 4. A relocation owned by `dotmac-service-changes` that creates
a new service instance sets `replaces_service_reference`; an in-place relocation
leaves the credential untouched.

### 3. Model, facts and resting states (D4)

A credential carries: `id`, `tenant_id`; `service_reference`;
`customer_reference` (opaque, set at issue from a typed Services fact); an
optional opaque `access_point_reference`; `kind` (`pppoe`, `ipoe_option82`,
`mac_binding` in phase 2); `identifier`; `secret_ciphertext`;
`secret_fingerprint` (keyed HMAC, section 7); `secret_version`;
`last_converged_secret_version`; `state`; a `compromise_marker` (nullable, with
the `secret_version` at which it was set); `row_version`; `policy_version`; and
`previous_secret_ciphertext`, defined as the **last converged** version and
destroyed only when a newer version converges.

`identifier` is unique per projection namespace (realm), not merely per tenant.
After revoke an identifier is not reused for a quarantine period set by a
tenant setting.

**Owner-held Services facts.** The owner keeps a local projection of each bound
service's status and customer, fed by `dotmac-services` events (ADR-0024). Every
check in this ADR that depends on "same customer" or "predecessor ended" is made
against this owner-held copy, never against values supplied by the caller.

**Resting states are exactly four:** `issued`, `active`, `suspended` and
`revoked` (terminal). The legal transitions are declared as a table and every
other move is refused. `suspended` is a **security hold only** (compromise,
abuse investigation). Commercial and collections restriction stays with SAP.

**Rebind (inherit) and rotate are transitions, not resting states.** History
records them.

**Compromise is a fact the credential owner holds.** `SuspendCredential` with
reason `compromise` sets the compromise marker. Services' free-text end reason
is supplementary and may be used by policy only once it is a typed code.

### 4. Replacement and inheritance (D3, D5, D8)

**D8: `dotmac-services` gains `replaces_service_reference`** on `CreateService`
and emits `services.service.replaced`. The Services amendment must refuse a
reference to another customer's service. The change is made by the Services
implementing PR, not by this ADR.

**D3: an ended service's credential may be inherited by the same customer
only.** `RebindCredential` is refused unless, per the owner-held facts, the
predecessor service is in an ended state (a setting; default `[TERMINATED]`, the
only ended Services state) and its customer equals the new service's customer.
Rebind is also refused while the credential is `suspended` or while its
compromise marker is newer than the last converged rotation. Cross-customer reuse
is impossible, not merely discouraged.

**Secret on rebind.** The setting `access_credentials.secret_on_rebind`
(`keep` | `rotate`, default `keep` per D3) decides whether inheritance also
rotates the secret. `rotate` covers relocation, where a CPE left at the old
premises would otherwise still authenticate with the inherited identity.

**D5: at replacement the default is to inherit when the predecessor is
unambiguous, otherwise ask.** This is the legal setting value
`inherit_if_unambiguous_else_ask`. "Unambiguous" means: an explicit
`replaces_service_reference` whose predecessor holds exactly one credential. A
missing link is treated under `on_additional_service`. Single-candidate
inference without an explicit link is permitted only in the Sub adapter, where
no link exists. Each decision record stores its basis (`explicit` or
`inferred`).

When interactive asking is impossible (bulk, scheduled or automated
activation), the unattended fallback is `inherit_if_unambiguous`: inherit only
under the unambiguity rule above and otherwise issue a new credential. The
alternatives `new` and `hold_activation` stay selectable settings.

Credential continuity at activation is an invariant of the owner's policy, not
an automation. A fulfillment participant or product-assembly step (not a new
owner) calls the owner at activation: it obtains a decision, then rebinds, or
issues and activates. This works with the rules engine paused or absent. A rules
engine, if present, adds conditional overrides only and calls the same commands.

### 5. Governed commands, audit and idempotency

| Command | Transition | Notes |
|---|---|---|
| `IssueCredential` | none to issued | Identifier per scheme; secret generated or supplied per policy. |
| `ActivateCredential` | issued to active | Called by the activation step. |
| `RebindCredential` | active to active (service changes) | Preconditions in section 4, checked against owner-held facts. The decision and its basis are recorded in this command or in Issue. |
| `RotateSecret` | active or suspended, state unchanged | Generated or supplied. |
| `ChangeIdentifier` | active or suspended, state unchanged | Old identifier travels in the effect. |
| `SuspendCredential` | active to suspended | Security reasons only; may set the compromise marker. |
| `ResumeCredential` | suspended to active | After a compromise, requires a `secret_version` greater than at suspension, converged. |
| `RevokeCredential` | any to revoked | On expiry of the revoke grace (below), or by staff. |

Resolution is a read (`credential.resolve_preview`) that mutates nothing; the
decision is recorded inside the Rebind or Issue command, so no unrecorded
decision exists.

A new `RotateSecret` or `ChangeIdentifier` is refused while one is pending,
except retry and revert. Revert targets the last converged version.

Every command runs under `execute_once` (ADR-0014) keyed on tenant, scope
`access_credentials.<command>` and a caller idempotency key; checks
`expected_row_version`; stages an audit record (actor, reason, credential id,
service references, booleans for what changed, never the secret); and writes
exactly one outbox event in the same transaction. No external call occurs inside
that transaction.

**Revoke after grace** is a durable timer owned by this package
(`dotmac-durable-timers`); it does not depend on the rules engine.

### 6. Every policy is a tenant setting (nothing hard-coded)

Keys are declared through the settings registry (ADR-0008/0011/0012),
tenant-scoped, with narrower scopes only where a setting declares that it
inherits. Illustrative keys, finalised at build:

- `access_credentials.on_replacement_service`: `inherit` |
  `inherit_if_unambiguous_else_ask` | `ask` | `new` (Starter default:
  `inherit_if_unambiguous_else_ask`).
- `access_credentials.on_additional_service`: `new` | `ask`.
- `access_credentials.inherit_from_service_states` (default `[TERMINATED]`).
- `access_credentials.ask_fallback_unattended`: `inherit_if_unambiguous`
  (default) | `new` | `hold_activation`.
- `access_credentials.secret_on_rebind`: `keep` (default) | `rotate`.
- `access_credentials.username_scheme`, `access_credentials.password_policy`,
  `access_credentials.rotation`, `access_credentials.identifier_quarantine`.
- `access_credentials.change_disconnect`: `immediate_after_projection` |
  `after_cpe_readback` | `none`.
- `access_credentials.revoke_grace_after_termination`: a duration (default 30
  days). During grace the credential is not projected (section 8).

Retry and backoff settings are not declared here: delivery retry belongs to the
Integrator and the projection engine to network-access. Code contains no
tenant-specific literal; the code default is the declared setting default.

### 7. Secret storage: encrypted in the tenant database

Per-subscriber secrets are tenant data. They are stored as ciphertext in the
tenant database under the owner's **own keyring** (the kernel `Keyring`
primitive and `enc:<key_id>:` format, with key material separate from the
settings keyring). Decryption fails closed: it never returns plaintext as a
fallback and never degrades an unreadable value to a default. Fingerprints use a
**separate keyed HMAC key**; the kernel's unkeyed `fingerprint_of` must not be
used for secrets, since supplied weak passwords would be crackable from it. Both
keys are named as deployment material in the deployment descriptor (ADR-0070)
and loaded through `SecretSource`; only keys, never per-subscriber secrets, may
originate in OpenBao (ADR-0009). Re-encryption is an owner-provided, resumable
operation over its own table, including `previous_secret_ciphertext`; the
kernel's settings re-encryption does not cover it. Import and mirror code run
inside the adopting assembly, so no key crosses an application boundary.

**Plaintext exists in exactly three places:**

1. inside the owner, in the audited, step-up, rate-limited `reveal` read;
2. inside the owner, when it serves a projection fetch (below);
3. in transit only, on that fetch.

**Crossing to Integrator.** Integrator is a separately deployed application
(ADR-0038 section 6), so plaintext cannot cross an in-process port. The assembly
binds the owner's projection-fetch port (ADR-0024: no sibling import). The
secret reaches the connector only through a synchronous, mutually
authenticated fetch keyed by (credential_id, secret_version), authorised
per request and rate-limited. Alternatively it may be envelope-encrypted to the
connector's key for that version. In neither case does the secret appear in an
outbox row, a delivery, retry or dead-letter row, an event, a log, an
exception or a drift record on either side. Test obligation: persisted
Integrator-side payloads and all owner-side events, audit rows and logs
contain no secret, asserted on every command path.

### 8. Command to effect contract with network-access and Integrator

1. The owner commits the state change and one outbox row in one transaction.
2. `dotmac-network-access` consumes the event and owns the RADIUS user
   projection (ADR-0038). It asks the Integrator RADIUS connector to apply the
   change, carrying identifiers, credential id and secret version only. The
   connector fetches the secret as in section 7, then per target performs
   delete-and-insert in one target transaction.
3. Convergence requires every target to report the expected fingerprint.
   Network-access emits its own `projection_converged`; the owner consumes it,
   marks the version converged (`last_converged_secret_version`), and emits
   `access_credential.version_converged`.
4. **Retries and the failed state belong to Integrator and network-access.**
   Delivery is at-least-once with consumers `process_once`; backoff is the
   Integrator's. After its configured limit, network-access exposes an explicit
   **failed-projection** state and emits `projection_failed`; the owner consumes
   it and surfaces it on `credential.effect_status`. It is a queryable state,
   never a swallowed log line.
5. **Mid-rotation failure.** The owner holds version N+1, RADIUS holds N, the
   CPE keeps working on N, and no disconnect is sent before convergence. Staff
   can retry or revert to the last converged version, so nothing is re-guessed.
   Partial multi-target success is not convergence.
6. **Identifier change.** The old identifier is deleted in the same target
   transaction that inserts the new one. Old sessions are disconnected only after
   convergence.
7. **Rebind** triggers projection of the new service's attributes, then
   disconnect per `change_disconnect`.
8. **Disconnect modes.** `immediate_after_projection` asks network-access to
   close sessions of the old identifier through Integrator CoA/Disconnect.
   `after_cpe_readback` waits for the CPE/ACS owner's typed readback (currently
   Sub). Residual risk named: an unplanned reconnect between RADIUS projection
   and the CPE push fails authentication.

**Projection invariant (proposed; requires Michael's confirmation).** A RADIUS
user exists only for a credential in `active` that is bound to a live service.
Network-access withdraws the user otherwise (for `suspended`, `revoked`,
`issued`, a terminated bound service including the revoke grace window, and a
withdrawn identifier). This is identity existence, not an access decision; it
narrows "SAP is the only path to deny" to "SAP decides for valid identities".
For suspend, revoke and termination the order is: **withdraw, converge (absent
on every target), then disconnect.** SAP additionally receives the hold as an
input signal. `SuspendCredential` does not ship until an enforcing consumer of
this invariant exists; a hold that nothing acts on is not shipped.

### 9. Capability-registry entries (ADR-0008 vocabulary)

Declared through the manifest registries. Each member has exactly one owning
module and a consumer; a declared member with no consumer fails CI, so
vocabulary for consumers that do not yet exist is listed as future and not
declared.

- **Capabilities/permissions:** `access_credentials.manage`,
  `access_credentials.reveal`; permissions `access_credentials:read`,
  `access_credentials:change`, `access_credentials:reveal` (step-up, audited),
  `access_credentials:suspend`.
- **Reads:** `credential.by_service`, `credential.history`,
  `credential.resolve_preview(service_ref, predecessor_ref?)`,
  `credential.effect_status`.
- **Signals (owner namespace):** `access_credential.issued`, `.activated`,
  `.rebound`, `.secret_rotated`, `.identifier_changed`, `.suspended`,
  `.resumed`, `.revoked`, `.version_converged`, `.rotation_due`. Payloads carry
  ids, kind, versions and keyed fingerprints, never secrets.
- **Commands:** the table in section 5.
- **Owned by network-access, consumed here:** `projection_converged`,
  `projection_failed`, `session_disconnect_requested`.
- **Future, not declared yet:** rules-engine triggers, facts and actions
  (`services.service.replaced`, rotation-due and projection-failed triggers;
  rebind, rotate, notify actions) and the SAP blocker kind for the security
  hold; declared when their consumers land.

### 10. Import of existing Sub credentials, as-is, nothing guessed

Import is a governed `dotmac-imports` run (ADR-0025): preview, approve, run. The
source id is kept as `source_ref`; the secret is decrypted with Sub's key and
re-encrypted under the owner's keyring inside the import worker, never logged,
with fingerprints on both sides. Re-keying the opaque Sub subscription
reference to a Services instance reference is a governed 1:1 re-key; ambiguous
rows go to `needs_review`.

State mapping is a table keyed on (source active flag, Sub subscription
status). A state is never inferred.

| Source active flag | Bound subscription | Result |
|---|---|---|
| active | live service | `active`, bound to that service |
| active or inactive | ended service, end time recorded | `active`, bound to the ended service, revoke grace clock starts at the recorded end time (not projected during grace) |
| active or inactive | ended service, end time missing | `needs_review` |
| any | unbound | `needs_review` |
| any other combination | any | `needs_review` |

Inactive rows are not blanket-mapped to `revoked`: revoked is terminal and would
make cancel-first credentials, the core of the reported symptom, permanently
uninheritable. Also routed to `needs_review`: one-way-hash secrets, decrypt
failures, ambiguous kind derivation (both or neither of the PPPoE and option-82
fields), and duplicate active identifiers. The cutover gate requires an empty
exceptions queue, each item resolved by an explicit command.

Adoption proceeds by shadow, then cutover. Sub remains the writer during a
dual-run in which the owner computes decisions for each Sub activation and a
drift report compares decisions and row fingerprints. Expected explained-drift
classes are declared before shadow starts: the revoke grace, the Sub status map
(below), and one-way-hash rows. The gate is zero unexplained drift against those
classes over at least 30 days, with a minimum observed count for each
disposition including inherit and decline (thresholds to be set by Michael). At
cutover Starter becomes the authority and Sub's table becomes a read projection
with exactly one writer (the Starter sync adapter). At suite cutover,
network-access consumes the owner's events directly.

### 11. Sub's `ensure_pppoe_credential` becomes a thin call; no secret is ever sent

After cutover, `ensure_pppoe_credential` is reduced to: obtain a decision, then
rebind or issue-and-activate, then return the same outcome dataclass. All
decision logic leaves Sub. Every other Sub credential writer is retired or
redirected to commands, and an architecture ratchet test pins the writer set so
it can only shrink.

**D9: Starter never sends a secret.** Notifications state that a credential
changed and link to an authenticated, expiring reveal or to customer self-care,
and these ship in phase 1. There is no transitional plaintext SMS or email mode
in Starter. Sub's existing plaintext sending remains Sub-only debt with a
register entry and an end condition (retired at cutover).

### 12. The Sub bridge is a temporary mapping

Before the owner exists, a minimal Sub-side bridge may carry corrections and a
setting-gated inherit rule, governed by Sub's own feature-freeze process. It is
not a one-to-one copy of the Starter policy, so the mapping is explicit:

| Sub bridge | Starter equivalent |
|---|---|
| new-service mode `new` | `on_replacement_service=new` |
| new-service mode `inherit` | single-candidate inference plus the `inherit_if_unambiguous` fallback; not Starter `inherit` |
| inherit-from statuses {disabled, canceled, expired} | no direct equivalent: Services has one ended state (TERMINATED), so an explicit Sub-to-Services status map is required; if `disabled` maps to a non-ended state, Starter refuses what Sub allows |
| change-disconnect | `change_disconnect` (without `after_cpe_readback`) |
| (none) | revoke grace: no Sub equivalent |

The Starter-only value `ask` is rejected by the bridge. The bridge is deleted
when `ensure_pppoe_credential` becomes the thin call.

## Reuse

| Candidate | Disposition |
|---|---|
| PPPoE credential | In scope, phase 1 |
| IPoE / DHCP option-82 identity | In scope, phase 1 (no secret) |
| MAC binding | In scope, phase 2, kept in this package |
| Hotspot/voucher login identity | Later; voucher issuance is a distinct commercial concern |
| ONT web-management credentials and Wi-Fi keys | Out of scope: ONT/ACS management stays in Sub for now |
| At-rest key rotation of desired-config credentials | Not a candidate: kernel crypto rotation |
| ACS/TR-069 provider credentials | Not a candidate: machine credentials, Integrator connector plus OpenBao |
| Router, NAS, OLT API/SSH, SNMP secrets | Not candidates: Integrator / router control plane |
| Staff device login | Not a candidate: human identity via IdP and RBAC |
| Portal/customer logins | Not a candidate: kernel `credential_lifecycle` |

No kind is declared before it has a consumer. A sweep keyed on a `*Credential`
name must not pull the non-candidates in.

## Security considerations

- Secrets are encrypted at rest under the owner's own keyring; plaintext exists
  only in the three places in section 7 and never in any persisted event,
  outbox, retry, dead-letter, audit or log row.
- Fingerprints are keyed HMAC; unkeyed hashes of secrets are forbidden.
- D3 widens reuse (an ended service's credential can return to the same
  customer). D5 makes that widening automatic, with no staff action, when the
  predecessor is unambiguous. It is bounded by the owner-held same-customer and
  ended-state checks, the compromise marker, and `secret_on_rebind`.
- Relocation: a CPE left at the old premises keeps the old identity if the
  secret is kept. Tenants should use `secret_on_rebind=rotate` for relocation.
- A compromised credential can be rotated while suspended without being
  re-enabled, cannot be rebound, and can resume only on a newer converged secret.
- Suspension must be enforced: under the proposed projection invariant a
  suspended credential is withdrawn from RADIUS, and nothing ships that merely
  signals.
- Disconnect never precedes convergence on rotate; on suspend or revoke it
  follows withdrawal and convergence.
- Credential `suspended` is a security hold, not a billing lever.
- Reveal and change are distinct permissions. Until Sub's existing exposures
  are fixed (plaintext password rendered into an edit page; reveal gated by a
  broad read permission), the mirror period inherits them, so their correction
  is a precondition of cutover and the claim that reveal is independent of
  customer read access is not true until then.
- Import quarantines one-way-hash and undecryptable secrets; nothing is
  reconstructed.

## Consequences

- One owner, one declared state machine and one event vocabulary replace several
  Sub writers.
- Network-access gains an event consumer and owns the failed-projection state;
  Integrator owns delivery retry.
- `dotmac-services` gains a field and an event (with a same-customer guard); a
  SAP signal and blocker kind follow when their consumers land.
- Until Sub cutover two representations exist; the shadow gate and single-writer
  rule keep that safe.
- The same-review obligations of section 1 apply to the implementing change.

## Alternatives rejected

- **Widen `dotmac-network-access`.** Contradicts its extraction contract and
  mixes secret custody with projection.
- **Widen `dotmac-services`.** The credential has its own lifecycle and secrets.
- **Per-subscriber OpenBao references.** Breaks ADR-0009 and the request path.
- **Rebound and rotated as resting states.** They are transitions.
- **Trusting caller-supplied evidence for D3.** Unenforceable; checked against
  owner-held facts instead.
- **Fix only inside Sub.** Re-thickens a frozen product.

## Open questions

1. Resolved. Michael's lifecycle direction: money and access lifecycles are
   explicit state machines, settings hold policy values, the rules engine
   composes reactions, and transitions are governed commands. The transition
   table in section 3 refuses illegal moves.
2. Namespace and prefix are allocated in the package's first commit, not
   reserved.
3. Resolved by design: Suspend does not ship unconsumed (section 8). Open:
   Michael to confirm the projection invariant.
4. Resolved: reveal link and self-care ship in phase 1; Starter never sends a
   secret.
5. Default ended-state list is `[TERMINATED]`; grace defaults to 30 days and the
   credential is not projected during grace. Michael to confirm the 30 days.
6. Resolved: the end reason is not available as a typed fact, so the control
   moves to the owner's compromise marker.
7. Shadow window of at least 30 days, a minimum count per disposition, and zero
   unexplained drift; Michael to set the thresholds.
8. `mac_binding` stays in this package in phase 2; `cpe_management` and Wi-Fi
   keys are deferred; no kind is declared before it has a consumer.
9. The explicit Sub-to-Services status map, especially for `disabled`.
10. The Sub exposures correction as a cutover precondition: owner and timing.
