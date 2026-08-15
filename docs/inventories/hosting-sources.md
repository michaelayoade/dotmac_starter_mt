# Hosting service lifecycle sources

**As of:** 2026-08-15
**Starter:** working tree at `d4f43a4` (branch `fix/appdir-import-safety`;
unrelated in-flight paths present, none read for this audit)
**Sub:** `27c76aaeebb7`
**ERP:** `0f4b1698ddbf` (revision-pinned reads; worktree had 67 local paths)
**Vendor CP:** `89848017d6b8` (revision-pinned; this is ahead of the repo's
checked-out `f9ca367c`, so every vendor citation below is a `git show` at the
pin, not a worktree read)
**CRM:** `c64b5aa0f790` (revision-pinned; 3 local paths present)
**Integrator:** `d014116e63ad`
**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
names `dotmac-hosting` as the sole owner of the Dotmac hosting-service lifecycle
and of the interpretation of panel observations (§1), authorizes it as the fifth
business owner in the build order (§5c, §6), and scopes it Cloud-only at first
(§7).

This audit settles one question: whether ADR-0030 §5c's `greenfield-after-inventory`
ruling survives a search that was actually performed rather than assumed.

## Verdict

`dotmac-hosting` is **greenfield-after-inventory**, and the ruling is now proven
rather than inherited.

Proven in two halves, because they have different answers:

- **The hosting domain has no source anywhere in the fleet.** Zero tracked files
  across six repositories implement a hosting account, a hosting package, a
  control-panel binding, a disk/bandwidth/inode quota observation, a hosting
  account suspension, or mailbox provisioning. The census is below.
- **Three of its mechanisms do have production precedent in Sub**, in the ISP
  access domain: reason-scoped multi-lock suspension with a reversible,
  first-class restoration; a receipted eligible/refused consequence record; and a
  desired-state / observed-state split written only by a reconciler. None is
  portable — each is welded to `subscriptions`, `subscribers`, RADIUS credentials
  or ONT hardware, and Sub keeps its copy as its own executor (see § "Sub"). They
  are **design references the fresh implementation must match or beat**, not code
  sources and not parity suites. Where `dotmac-hosting` deliberately diverges from
  one, its dossier must say why.

That distinction matters: "greenfield" here licenses new code, not new invention.
A hosting suspension engine weaker than `app/services/account_lifecycle.py` would
be a regression against a behaviour the fleet already knows how to get right.

### What was searched, and where

A per-term `git grep -IlE` census at each pinned revision, over all six
repositories, excluding `docs/`, vendored `leaflet` and `CHANGELOG`. Counts are
matching tracked files.

| Term family | Sub | ERP | Vendor CP | CRM | Integrator | Starter |
|---|--:|--:|--:|--:|--:|--:|
| `cpanel` `Plesk` `DirectAdmin` `WHM` `ISPConfig` `Virtualmin` `CyberPanel` `Webmin` `InterWorx` `Hestia` | 0 | 0 | 0 | 6\* | 0 | 0 |
| `hosting_account` `HostingAccount` `hosting_package` `HostingPackage` `hosting_plan` | 0 | 0 | 0 | 0 | 0 | 0 |
| `vhost` `VirtualHost` `public_html` `docroot` `document_root` `htaccess` | 0 | 0 | 0 | 0 | 0 | 0 |
| `ftp_account` `FTPAccount` `ftp_user` `sftp_user` | 0 | 0 | 0 | 0 | 0 | 0 |
| `addon_domain` `parked_domain` `subdomain_create` | 0 | 0 | 0 | 0 | 0 | 0 |
| `disk_quota` `disk_usage` `inode` `bandwidth_quota` `bandwidth_usage` `storage_quota` | 6† | 1† | 0 | 2† | 0 | 0 |
| `create_mailbox` `provision_mailbox` `mailbox_quota` `MailAccount` | 0 | 0 | 0 | 0 | 0 | 0 |
| `suspend_account` `unsuspend_account` `account_suspension` `SuspensionReason` | 12‡ | 0 | 0 | 0 | 0 | 0 |
| `retention_hold` `RetentionHold` `grace_deletion` `purge_after` | 0 | 0 | 0 | 0 | 0 | 0 |
| `panel_account` `control_panel` `ControlPanel` `panel_observation` | 0 | 0 | 0 | 0 | 0 | 0 |
| `webspace` `web_space` `shared_hosting` `vps_plan` `reseller_account` | 16§ | 0 | 0 | 1§ | 0 | 0 |
| `SpecificationVersion` `ServiceSpec` `spec_version` `specification_version` `TechnicalSpec` `PlanSpec` | 6¶ | 0 | 0 | 0 | 0 | 0 |
| case-insensitive `hosting`, *line* matches, `docs/` included** | 10 | 3 | 0 | 0 | 0 | 27 |

\* All six are the substring `WHM` inside `WHMCS`, an external ISP billing
system named in CRM's subscriber-import vocabulary
(`app/models/subscriber.py:170`, `app/web/admin/subscribers.py:71`,
`templates/admin/subscribers/form.html:71`). A dropdown label for an import
source, not a panel client.
† `shutil.disk_usage("/")` host health probes (`dotmac_sub:app/services/system_health.py:89`,
`dotmac_erp:app/services/infrastructure_health.py:354`) and ISP *network*
bandwidth reporting (`dotmac_sub:app/services/usage_summary.py:778`,
`dotmac_crm:app/services/subscriber_reports.py:3990`). No storage or transfer
quota attached to any account.
‡ Sub's ISP access enforcement — analysed under "Sub" below.
§ Sub's and CRM's *reseller* (channel partner) portals — a sales channel, not a
hosting reseller account. `dotmac_sub:app/api/reseller.py`,
`app/services/reseller_portal.py`.
¶ `WanServiceSpec` in `dotmac_sub:app/services/network/olt_command_gen.py:71`, an
in-memory dataclass for generating OLT CLI, with no persistence and no version.
\*\* This last row is deliberately widened — line matches, `docs/` included — so
that no mention anywhere can be claimed as missed. Every one of the 40 is the
unrelated architectural idiom "the hosting layer" (the assembly that hosts a
module — e.g. `dotmac_sub:app/models/domain_settings.py:5`,
`dotmac_starter_mt:docs/adr/0008-...:56`), prose about vendor-managed hosting in
ADR-0003 and the starter README, an ERP sales proposal line item, or ADR-0030 and
`cloud-commerce-owner-sources.md` naming this very module. Not one is a hosting
product.

Additionally searched and found nil for hosting: `desired_state`/`observed_state`
(hits are ONT/OLT and UISP network control, plus ERP fixed-asset audits),
`terminate_service`/`TerminationApproval`/`destructive` (hits are RADIUS
connectivity backup and migration DDL), and the whole `dotmac_integrator`
repository, which at `d014116e63ad` is 24 tracked files of thin assembly — no
connector plugin of any kind, panel or otherwise.

## Sub source

Sub is not a code source for `dotmac-hosting`. It is the only repository holding
mechanism precedent worth measuring against, and the only one whose boundary
against hosting must be drawn explicitly, because both sides say "suspend".

**The boundary.** Sub's suspension is ISP network access enforcement on a
`Subscription` row.
`app/services/account_lifecycle.py` (2,215 lines) is the sole writer of
`Subscription.status`, `Subscription.access_state` and `Subscriber.status` —
`docs/FINANCIAL_ACCESS_ENFORCEMENT.md:52-53` names `access.subscription_lifecycle`
"sole writer of reason-scoped locks, account status, and child-service access
state in one transaction", and
`docs/inventories/collections-sources.md` § 2.3 confirms it by grep. Its
consequence reaches a RADIUS profile, a captive portal, an ONT.

`dotmac-hosting`'s suspension is a **hosting service aggregate transition** whose
consequence reaches a panel account through an Integrator connector. The two never
touch the same row, the same table or the same process. The boundary is not a
naming convention: Sub is not an adopter of `dotmac-hosting` (ADR-0030 §7 —
Cloud-only at first), so no deployment ever runs both writers against one subject.

The Collections dossier leaves `app/models/enforcement_lock.py` unnamed — it is
neither in `source_paths` nor in the retirement inventory, appearing only in the
"Shared" column of § 3.7. **This dossier closes that gap explicitly:
`EnforcementLock` stays on the service-owner side of the line, in Sub, and is not
extracted by Collections, by Hosting, or by anything else.** `dotmac-hosting`
builds its own lock record for its own aggregate; the two are independent
implementations of a shared *shape*, which is what ADR-0030 §3 requires of peers.

**Precedent 1 — reason-scoped multi-lock suspension with first-class
restoration.** `app/models/enforcement_lock.py` (142 lines). A subscription is suspended iff it
has **any** active `EnforcementLock`; multiple locks coexist (`overdue` + `fup`);
restoration requires resolving all of them. A DB-level partial unique index
allows one active lock per `(subscription, reason)`, and a check constraint
forces `resolved_at`/`resolved_by` on any resolved lock.

`app/services/account_lifecycle.py` supplies the half that makes it a real
authority, not a flag:

- `ALLOWED_RESTORERS: dict[EnforcementReason, set[str]]` (`:80`) declares which
  trigger may clear which reason, and an import-time check (`:93-96`) raises
  `RuntimeError` if a reason is added without a restorer — the vocabulary cannot
  drift silently.
- `restore_subscription_detailed` (`:711`) locks the subscription row first
  ("prevent concurrent restore races", `:731`), then returns a
  `SubscriptionRestorationResult` carrying `RestorationOutcome`,
  `remaining_blockers` and separate `subscription_reactivated` /
  `access_restored` booleans, because "'nothing needed doing' is not 'I restored
  something'" (`:899`).

This is exactly invariants 4 and 5 of the hosting brief, already solved once: a
retention hold is a lock with a reason no delinquency trigger appears in, and
restoration is the named transition `restore_*`, not the absence of a lock. It is
proven by `tests/test_financial_access_restore.py`, `tests/test_bundle_enforcement.py`
and `tests/test_prepaid_balance_sweep.py`.

**Precedent 2 — the receipted, refusable consequence.**
`alembic/versions/299_financial_access_consequence_evidence.py` creates
`financial_access_consequences`, and it is the closest thing in the fleet to
invariant 2. Every consequence attempt — not every applied consequence — is a row:
`action`, `requested_reason`, `origin`, **`eligible` (boolean)**, `outcome`
(String 120), `preview_fingerprint`, `idempotency_key`, `decision_inputs` (JSON)
and `result` (JSON). The decision lives in
`app/services/collections/_core.py::preview_/confirm_financial_access_consequence`
(`:459-757`, `:813-1041`), and the applied write goes through
`account_lifecycle.suspend_subscription` (`:869-885`).

So the fleet already records "you asked, I evaluated, here is what I did or why I
did not". What it does **not** yet do is split the two sides across an owner
boundary: in Sub the requester and the decider are in the same process and the
same transaction. `dotmac-hosting` is the first place where the request crosses a
module boundary, and it must therefore add what Sub never needed — a typed
inbound command, a typed outbound receipt, and a refusal that is a first-class
returned value rather than an unraised branch.

**Precedent 3 — desired state and observed state are different rows.**
`app/models/ont_observation.py` is a separate 1:1 table holding last-seen reality,
"[w]ritten exclusively by `app.services.network.reconcile`. Other code may read
it; nothing else writes it", and explicitly "[n]ever authoritative for desired
state — the planner compares `OntDesiredState` to this row to decide what to
push, but proposed changes mutate the desired side only." `app/models/uisp_control.py:85`
carries the matching `desired_state` JSON column.

That is invariant 1, implemented, with a stated single-writer rule. Its limits are
recorded as defect D3 below.

**The seam Sub publishes and nobody consumes.**
`app/services/collections/lifecycle.py` emits `collections.consequence_requested`
(`:228`) and `collections.case_action_due` (`:282`). The Collections dossier
records that both "have producers and **no consumer**", making a real cross-owner
consequence "Impossible today". `dotmac-hosting` is the first module positioned to
close that seam. It must not do so by importing Collections — ADR-0030 §3 — but by
accepting a versioned command the Cloud assembly translates, and returning a
receipt the assembly records.

## ERP source

Nil for hosting. One near-miss worth naming so it is never mistaken for one.

`app/services/mailcow/client.py` is an 88-line HTTP client used by
`app/services/people/hr/offboarding.py` for **employee** offboarding. It exposes
`get_mailbox`, `list_mailboxes` and `update_mailbox_password`. There is no create,
no delete, no quota, no persisted mailbox row, no lifecycle state, and no tenant
scope. `_run_mailcow_steps` (`offboarding.py:161`) rotates the password of a
departing employee's mailbox and sets an inactive-forward, deliberately passing
`active=True`.

It is a transport for an HR consequence, and it is not a mailbox-provisioning
source. It is also an anti-pattern reference: its failures are caught, logged and
appended to `result.errors` (`:180-181`), so the offboarding run reports success
with a swallowed error — the opposite of a receipted refusal.

ERP has no offer, hosting, service-lifecycle or panel surface at all; the
`subscriptions-sources.md` audit already recorded "ERP has no offer,
subscription-contract, cadence, or recurring-obligation" implementation (`:57`).

## Vendor CP source

Nil for hosting, and the reason is worth stating because Vendor CP's vocabulary
overlaps hosting's at three points.

- **Contract suspension is a commercial posture, not a service transition.**
  `src/vendor_cp/contracts/models.py:56-58` holds `Contract.status` as a single
  `String(20)`; `contracts/service.py::suspend()` (`:432-443`) moves
  `active → suspended`, writes that one column, and emits `contract.suspended`.
  Its own docstring: "Projects to allocation RESTRICTION only (never data
  deletion)", and the module header (`:11-13`) states it "never synchronously
  mutates allocation / entitlement / deployment state, and it NEVER writes a
  product data plane's WS2 grants." Nothing in the repository consumes
  `contract.suspended`. `reinstate()` (`:446`) makes it reversible; `terminate()`
  (`:459-488`) requires `impact_acknowledged` and an `effective_date`.
- **Entitlement allocation has no suspension concept whatsoever.**
  `src/vendor_cp/allocations/models.py:28-32` gives `AllocationStatus` exactly one
  member, `STAGED`; the service is insert-only with no update, delete or revoke
  path. The extracted `packages/dotmac-entitlement-allocation/` in this repository
  is the same shape — platform-plane, `REVOKE ALL ... FROM app_user`
  (`migrations/versions/ea_0001_allocations.py:262-263`), no status transition.
  `entitlement-allocation-sources.md` does not use the words suspend, revoke or
  lifecycle anywhere in its 107 lines.
- **Licence revocation is deliberately irreversible and therefore not a hosting
  suspension.** `src/vendor_cp/licensing/revocation_models.py:10-15`: published
  snapshots are "permanently cumulative", and "[r]ecovery from a mistaken
  revocation is re-issuance under a NEW lineage, never quiet removal from the
  list." Hosting suspension is the opposite by contract (invariant 5).

Every vendor table is platform-plane: no `tenant_id`, no RLS, `GRANT ... TO
platform_api`/`app_admin`, `REVOKE ALL ... FROM app_user`, across migrations
`v001`–`v010`. `src/vendor_cp/provisioning/service.py:1-13` is an in-memory
laboratory driver that "FAILS for any non-fake mode".

**The boundary sentence:** Vendor CP suspends *an agreement it sells under*;
`dotmac-hosting` suspends *a service it operates*. Vendor CP is not an adopter of
`dotmac-hosting` (ADR-0030 §7) and the two never co-write a row.

## CRM and Integrator sources

CRM: nil. Its only matches are the `WHMCS` import-source label and reseller
(channel-partner) vocabulary. ADR-0030 §7 retires CRM's commercial writers; it
gains no hosting role.

Integrator: nil, and that is the expected answer. At `d014116e63ad` the repository
is 24 tracked files — `assembly.py`, `lineage.py`, `migrate.py`, `worker.py`,
`operations.py`, `health.py`, `settings.py` and three test packages. It pins and
runs `dotmac-integration`; it ships no connector distribution. The hosting-panel
connector ADR-0030 sequences at build-order step 15 does not exist and must not be invented inside
`dotmac-hosting`.

## Do not port

Named because each already exists in the fleet and each would be an easy,
plausible mistake.

1. **`EnforcementReason` as a closed enum.**
   `dotmac_sub:app/models/enforcement_lock.py:32` is a seven-member `enum.Enum`
   behind a PostgreSQL native `Enum` column. Hosting suspension reasons are an
   open registered vocabulary (ADR-0008); a new reason must not require a release
   or a migration, and `retention_hold` must be declarable by a product.
2. **`FinancialAccessAction` as a closed enum** — `suspend | reject | throttle |
   restore`, hard-coded in migration `299` as a native type. The Collections audit
   already lists this as non-conformance #3. The consequence vocabulary is a
   registry.
3. **Product-specific vocabulary.** `Subscriber`, `Subscription`, `AccessState`,
   `RadiusProfile`, `radius_profile_id`, `pre_throttle_radius_profile_id`,
   `AccessCredential`, `NasDevice`, `OntUnit`, `walled garden`, `captive`,
   `fup`, `prepaid`. None of it is a hosting concept.
4. **Provider and panel names.** No `cpanel`, `plesk`, `directadmin`, `mailcow`,
   `whmcs` or `blesta` token in any table, column, code, setting, error class or
   manifest declaration, and no `if provider ==` branch. A panel is an Integrator
   connector binding. The fleet is currently clean of panel brands (census above);
   it must stay clean.
5. **A foreign-key into another owner's aggregate.**
   `dotmac_sub:app/models/collections.py:146-151`'s
   `FinancialAccessConsequence.subscriber_id → subscribers.id` is, in the
   Collections dossier's own words, "exactly the FK a module may not have". A
   hosting service references its customer, order line and offer version by
   opaque `UUID` with no FK — provenance, not referential integrity.
6. **Second writers.** Collections wrote `credential.radius_profile_id` directly
   (`collections/_core.py:918-921`, `:1475-1476`) and is being ratcheted out of it.
   `dotmac-hosting` has exactly one writer of hosting service state — its own
   lifecycle engine — and a panel observation collector that is structurally
   incapable of reaching it.
7. **Silent fallbacks.** `dotmac_erp:app/services/people/hr/offboarding.py:180-181`
   swallows a mailbox failure into a result list; `dotmac_sub:app/services/connectivity_backup.py`
   states as a design rule that "[c]apture never breaks the mutation it guards"
   and "swallows its own errors and returns `None` on failure". A hosting
   consequence that could not be applied is a recorded refusal or a recorded
   durable failure with an attempt count, never a success with a note.
8. **Host coupling.** `organization_id`, FastAPI `HTTPException` (Sub's
   `app/services/enforcement.py` imports it directly), service-level `commit()`,
   product schemas, and settings lookups performed from inside the lifecycle owner.
9. **Panel status as Dotmac status.** ADR-0030 §1: "A provider callback never
   assigns a Dotmac service status directly." No panel enum value is ever stored
   in a Dotmac state column, and no observation row is ever the input to a state
   machine without passing through a resolver.

## Known defects/deltas

Numbered defects in the precedent implementations. Each is a thing the fresh
implementation must not reproduce.

1. **The consequence request and the decision are the same transaction in Sub.**
   `collections/_core.py` previews eligibility and then calls
   `account_lifecycle.suspend_subscription` in-process. There is no proof anywhere
   in the fleet that a *cross-owner* consequence request can be refused, receipted
   and observed by the requester — the topics that would carry it
   (`collections.consequence_requested`) have no consumer. Invariant 2 has no
   existing proof and must be proven fresh.
2. **A failed consequence is swallowed, not recorded.** Collections
   non-conformance #6: "no attempt count, no `next_retry_at`, no failed status".
   Hosting's outcome record carries all three or the outbox cannot converge.
3. **`OntObservation` is a mutable 1:1 upsert, not an immutable observation
   stream.** Each reconcile pass overwrites the previous last-seen row, so there
   is no dedupe identity, no ordering evidence and no history to replay. ADR-0030
   §1 requires "a typed, deduplicated observation"; the hosting collector appends
   immutable observations with a provider-supplied dedupe key and derives
   last-seen, rather than storing only last-seen.
4. **`QuotaBucket` is a mutable counter, not a resource observation.**
   `dotmac_sub:app/models/usage.py:52` holds `used_gb` as a `Numeric` updated in
   place, keyed by `subscription_id` FK, with no tenant column and no provenance
   for the number. A hosting disk/bandwidth observation is an immutable typed fact
   with a source, an observed-at instant and a dedupe key; derived
   over-quota state is computed, never stored as the only copy.
5. **Idempotency keys embed truncated fingerprints or `uuid4()`.** Collections
   non-conformances #4 and #5. `dotmac_kernel.idempotency` already separates key
   from fingerprint (rule 23 / ADR-0014); hosting consumes it and does not restate
   it.
6. **No concurrency proof for the restoration lock.** Sub takes a row lock in
   `restore_subscription_detailed` and has no test that two concurrent restores
   race it. Its correctness is by reading, not by evidence.
7. **`preview_fingerprint` has no immutability guard.** Nothing in migration `299`
   prevents an operator path from rewriting a `financial_access_consequences` row.
   Hosting's outcome records are append-only, enforced in the migration.
8. **No retention-hold concept exists anywhere in the fleet** (census: 0/0/0/0/0/0).
   Invariant 4 has no precedent at all and is entirely fresh design.
9. **`AccountStatus.SUSPENDED`/`CLOSED` in Vendor CP have zero writers**
   (`accounts/service.py` is 155 lines of create/get/list). Declared vocabulary
   with no consumer is exactly what rule 12 forbids in a manifest; hosting declares
   nothing it does not consume.

## Shared contract

Version one **owns**:

- **technical hosting specification versions** — an immutable
  `(specification_code, version)` describing what a hosting specification
  technically delivers: resource allowances, included artefacts, panel-neutral
  capability codes, and the change rules between versions;
- the **hosting service aggregate** — one service per purchased line, its identity,
  its customer reference and its bound specification version reference;
- **desired account and package state** — what Dotmac intends the panel to hold;
- **observed panel account state** — immutable typed, deduplicated observations
  recorded by a collector, and the resolver that derives drift from them;
- the **creation decision** and the **package-change decision**, including refusal;
- **suspension and restoration** — reason-scoped locks, the rule for which trigger
  may clear which reason, and restoration as a named transition;
- **retention holds**, which outrank a delinquency-driven suspension request and
  block termination;
- **manually approved termination** — the transition, gated on an approval this
  module does not itself decide;
- **usage and resource observations** attached to a hosting service;
- **reconciliation** that rebuilds derived state from observations and repairs
  missed delivery, and the **operator repair** path over it.

It does **not** own:

- **pricing or saleability.** `dotmac-subscriptions` owns the stable offer, the
  immutable offer/price versions and whether something may be sold.
  `subscriptions-sources.md:48` already rules that product meaning — "service/access
  type, region, usage, SLA, policy, RADIUS, speed" — is not in the generic offer
  tables. **The split is: `dotmac-subscriptions` owns price and saleability;
  `dotmac-hosting` owns what a hosting specification technically delivers.** A
  commercial offer version references a technical specification version by opaque
  reference — never a Python import, never a shared table, never an FK;
- **collections policy.** A dunning case, a grace ladder and a delinquency
  consequence request are `dotmac-collections`. Hosting receives a request,
  locks, revalidates its own facts, applies or **refuses**, and returns a
  receipted outcome carrying the refusal reason. A request is not permission and
  is never a state write by the requester;
- **panel credentials or wire calls.** Endpoints, secrets, signatures, retries,
  checkpoints and payload shapes are an Integrator connector distribution. Hosting
  publishes a provider-free port and ships the fake/conformance kit for it;
- **the approval decision.** `dotmac-approvals` (installed here as
  `packages/dotmac-approvals`, `0.1.0a3`, schema `mod_approvals`, tenant plane
  supported) answers "has the required set of eligible actors approved this exact
  content under this exact policy revision" and emits an event. Per ADR-0026 it
  "decide[s] approval, never the transition that follows" — hosting declares the
  termination subject type, performs its own guarded transition on an approved
  decision, and remains free to refuse afterwards if a retention hold has landed
  in the meantime;
- **domain lifecycle** — registration, transfer, renewal, nameservers and DNS
  zones are `dotmac-domains`. A hosting service may reference a domain by opaque
  reference;
- **any website-design or site-builder workflow**;
- **invoicing, receivables, settlement, orders, or the fulfillment saga.**

## Kernel floor

Capabilities `dotmac-hosting` consumes from `dotmac_kernel` (verified present at
the starter's working tree unless marked):

| Capability | Module | Why hosting needs it |
|---|---|---|
| Transaction authority | `dotmac_kernel.db` (rule 8) | the lock-revalidate-apply-receipt sequence is one transaction |
| Idempotency | `dotmac_kernel.idempotency` — `execute_once`, `IdempotencyConflict`, `fingerprint_of` (rule 23 / ADR-0014) | replayed creation, package-change and consequence requests |
| Durable outbox / inbox | `dotmac_kernel.messaging` (`outbox`, `inbox`, `relay`, `worker`) | every panel command and every emitted outcome is a non-transactional effect |
| Planes | `dotmac_kernel.planes.ModulePlane` | declares TENANT, and only TENANT (ADR-0023 / rule 27) |
| Prerequisites | `tenant_scope_catalog.v1`, `module_database_roles.v1` (`dotmac_kernel.prerequisites`) | tenant catalogue and the `app_user`/`app_admin` roles the RLS canaries assert against |
| Namespace + lineage | `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (rule 14) | one `mod_<short_code>` schema and one lineage, allocated in the same change that creates the stateful package — not before |
| Audit | `dotmac_kernel.audit.write_audit_event` + declared `audit_actions` (rule 12) | operator repair, manual suspension, approved termination |
| Settings | `dotmac_kernel.settings_resolver` + a declared `SettingDomain` (ADR-0008/0011/0012) | reconcile cadence, drift tolerance, retention window defaults |
| Declaration registries | `ModuleManifest` permissions/capabilities/audit_actions/setting_domains | suspension reasons, consequence kinds and observation kinds are registered strings, never enums |
| Money | `dotmac_kernel.money.Money` | **only if** a resource observation ever carries an amount; hosting holds no price, so this is expected to stay unused — if it is unused, it must not be declared |

Two floor gaps are real and must be named now so the "sufficient and necessary"
proof is possible later:

- **`dotmac_kernel.durable_timers` does not exist yet.** It is authorized by
  ADR-0017 P3 and ADR-0030 §4 and is a prerequisite of the build order (step 6).
  Hosting needs generation-safe wake-ups for retention-window expiry, reconcile
  scheduling and consequence retry. It must not invent a second scheduler ledger,
  and it must not schedule inside its own schema.
- **No panel connector SPI has a consumer.** `dotmac-integration` exists and the
  Integrator assembly runs it, but no connector distribution exists. Hosting's port
  must therefore be published with a fake, and "the fake passes" is not evidence
  that a real panel does.

Hosting consumes all of the above; it restates none of them. Restating kernel
idempotency, outbox, audit or settings inside this module is a review failure.

## Fresh proof required

Everything here is fresh, because there is no source suite to preserve.

1. **Tenant RLS isolation.** Live PostgreSQL: every hosting table carries
   `tenant_id UUID NOT NULL` with FORCEd RLS (rule 11), and a cross-tenant read,
   write, update and delete canary proves a second tenant sees nothing. Hosting is
   **tenant plane only** — `platform_tables` is empty and that emptiness is
   declared, not inferred (rule 27). No nullable `tenant_id`, no sentinel tenant,
   no polymorphic scope column, and no FK crossing into any other module.
2. **Desired state and observed state cannot be conflated.** A structural canary
   proving they are separate tables, that the observation table has exactly one
   writer (the collector), and that no code path lets a panel observation assign a
   Dotmac desired-state or lifecycle column. This is invariant 1 and it is a test,
   not a comment.
3. **A consequence request is refusable, and the refusal is receipted.** Drive a
   delinquency consequence request against a service under a retention hold;
   assert the transition does **not** occur, that a typed refusal outcome is
   returned carrying the refusal reason, and that the outcome row is append-only.
   Then repeat without the hold and assert the transition and its receipt.
   Invariants 2 and 4 in one suite.
4. **Restoration is a transition, not an absence.** Suspend under two reasons,
   clear one, assert still suspended with the remaining blocker named; clear the
   second with a trigger the reason permits, assert restored; attempt to clear a
   reason with a trigger it does not permit and assert refusal. Invariant 5.
5. **Concurrency.** Two concurrent suspension requests, two concurrent
   restorations, and a suspension racing a package change — all against live
   PostgreSQL, proving the row lock holds and no lost update occurs. Sub has no
   such test; this is a gap being closed, not a port.
6. **Rollback with the consuming transaction.** A consuming transaction that fails
   after hosting has decided leaves no lock, no receipt and no outbox row.
7. **Idempotent replay and fingerprint conflict.** Same key + same fingerprint
   replays the original outcome exactly; same key + changed fingerprint raises
   `IdempotencyConflict`. Applies to creation, package change, suspension,
   restoration and termination.
8. **Out-of-order delivery.** An older panel observation arriving after a newer
   one must not overwrite the newer; a duplicate observation must dedupe on its
   identity rather than append twice.
9. **Lost callback.** A panel command whose acknowledgement never arrives is
   repaired by reconciliation from observation, not by assuming success — and the
   repair is idempotent when the acknowledgement finally arrives late.
10. **Drift and reconciliation.** Given a divergent observed state, the resolver
    derives drift, the lifecycle owner decides the consequence, and reconciliation
    converges. Reconciliation must be able to rebuild derived state from
    observations alone.
11. **Operator repair cannot rewrite evidence.** An operator may request a
    transition or a re-reconcile; no operator path may edit an observation, an
    outcome receipt or a specification version.
12. **Termination requires an approval and still revalidates.** An approved
    termination whose service acquired a retention hold after approval must be
    refused, proving ADR-0026's boundary from the consuming side: approval is
    input to the guard, not the guard.
13. **Import independence and public surface.** Import-linter contracts proving
    `dotmac_hosting` imports no sibling business module and no assembly; a public
    surface test; a manifest/lineage/migration gate; and a live-catalog check that
    the declared tables are the tables that exist.

## Adoption and retirement

**First adopter: Dotmac Cloud, and only Cloud.** ADR-0030 §7 makes hosting
Cloud-only at first because no other application has the lifecycle. Sub may render
a customer's Cloud portfolio from a rebuildable projection or link to the Cloud
portal; it does not install this module to draw navigation, and it never reads
Cloud's tables.

**No local writer retires, because there is none.** This is the honest and
unusual consequence of a greenfield ruling: the retirement column of the adoption
matrix is empty for hosting. Sub's `account_lifecycle.py` and `EnforcementLock`
are **not** displaced writers — they own a different subject in a different
application and stay exactly where they are. Anyone who later proposes routing a
Sub subscription suspension through `dotmac-hosting` is proposing a new
authority migration and needs its own ADR.

**How the cutover is sliced.** Because there is no writer to displace, the risk is
inverted: the danger is shipping contract surface no real service exercises. Slice
by transition, and let each slice reach a real panel through a real connector
before the next opens:

1. specification versions and the service aggregate, with creation refused —
   proves the aggregate and the opaque reference to a subscriptions offer version;
2. creation, desired state and the panel command port with a fake — proves the
   outbox path and the receipt shape;
3. the observation collector and the drift resolver against a real panel through
   the first Integrator connector — the first point at which "observed" means
   anything;
4. suspension, restoration and the consequence-request seam, which is also the
   first real consumer of `collections.consequence_requested`;
5. retention holds, then approved termination via `dotmac-approvals`;
6. reconciliation and operator repair, last, because it must repair everything
   above it.

**Gates that are not negotiable.** The namespace, migration prefix and lineage are
allocated in the same change that creates the stateful package (rule 14, ADR-0030
§6) — not reserved now. `dotmac-hosting/EXTRACTION.toml` carries
`source_mode = "greenfield-after-inventory"` with this audit as its evidence, and
names the mechanism precedents above as *references* with an explicit
`not_ported` disposition each, so a later reader cannot mistake silence for
absence.

A green test suite is not a cutover. `dotmac-hosting` is complete when its
declared contract passes its own gates; it is **adopted** only when Dotmac Cloud
runs the exact released version against a real panel connector and a real
customer's hosting service transitions through it. A reference-assembly migration
test, a passing fake-connector conformance run, and an installed empty schema are
none of them adoption.
