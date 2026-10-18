# Human credential lifecycle — product-first source inventory

Date: 2026-08-30

This inventory records the product-first evidence (hard rule 24) for
`dotmac_kernel.credential_lifecycle`: a stateless, storage-neutral owner for the
HUMAN password credential lifecycle — provisioning, verification, individually
authorized reset completion, and approved cohort force reset.

It is repository-local evidence for implementation. It is **not** a release,
adoption, deployment, or production-state claim. Nothing here asserts that any
product composes the facility; nothing here authorizes a production account
reset.

## Audited revisions

Every count below was measured at an exact commit, never at a branch.

| Repository | Revision |
| --- | --- |
| `dotmac_starter_mt` | `946cede1d894238c0582c8de0da438cdf37b85ea` |
| `dotmac_sub` | `7b5a7220a90a3c86b4afd339a1e3950dbcfddf2b` |
| `dotmac_erp` | `01b1d6758ea2d5926eaf68f4bf40e3a67a15f2e8` |
| `dotmac_vendor_control_plane` | `36ff215be5a80bd84d31b6642b68f52097e9aeec` |

Sibling repositories were read through `git ls-tree` / `git show` at those
commits. No sibling working tree was touched, and `dotmac_sub` in particular is
held by another lane — a read of an immutable object is not a write, and it is
also the only measurement that stays true after that lane commits.

## Why `dotmac_sub` qualifies as the product-first source

It is the only Dotmac product running the complete lifecycle in production:

- reset-required (`must_change_password`) is enforced at every verification
  path — a 428 at primary login, a portal refusal, and `change_password`
  unreachable without a session;
- lockout state exists and is evaluated (`auth_flow.py`,
  `web_customer_auth.py`, `staff_authentication_shadow.py`);
- recovery intents exist, and
  `auth_flow.request_principal_password_reset` already accepts
  `principal_type="reseller_user"`, so a reset one-off is never needed; and
- per-principal session revocation exists and has many callers
  (`staff_provisioning.py`, `reseller_portal.py`, `auth_flow.py`,
  `entitlement_revocation.py`, `customer_portal_session.py`).

`dotmac_erp` has the same shape at smaller scale and is a later adopter, not
the source. `dotmac_vendor_control_plane` has **zero** `must_change_password`:
it has no reset-required concept at all, which is recorded here rather than
smoothed over — a facility designed only against Platform CP would not have the
verdict that matters most.

## The measured call graph

`scripts/credential_lifecycle_sweep.py` counts direct **calls** (AST) to
`hash_password`, `verify_password` and `password_needs_rehash` across every
Python entry-point family, excluding tests and the two owner files. The frozen
result is `docs/inventories/credential-lifecycle-baseline.json`; the gate is
`tests/architecture/test_credential_lifecycle_ratchet.py`.

### Verification has FOUR owners in Sub, not one

| File | `verify_password` calls |
| --- | --- |
| `app/services/auth_flow.py` | 3 |
| `app/services/web_system_db_inspector.py` | 2 |
| `app/services/web_system_profiles.py` | 1 |
| `app/services/web_customer_auth.py` | 1 |

Four files. `auth_flow.py` is the de facto canonical owner on every axis and is
the natural first adopter. `web_system_db_inspector.py` verifying a password at
all is the least expected of the four and should be understood before it is
moved rather than mechanically rewritten.

The requirement that verification have ONE owner returning a typed verdict is
therefore a real displacement of three existing owners, not a formality.

### `hash_password` has eleven caller files, two of them scripts

| File | calls |
| --- | --- |
| `scripts/seed/seed_test_fixtures.py` | 4 |
| `app/services/staff_provisioning.py` | 3 |
| `scripts/seed/seed_admin.py` | 2 |
| `app/services/web_customer_user_access.py` | 2 |
| `app/services/auth_flow.py` | 1 |
| `app/services/credential_recovery.py` | 1 |
| `app/services/customer_credential_enrollment.py` | 1 |
| `app/services/reseller_onboarding.py` | 1 |
| `app/services/vendor_user_provisioning.py` | 1 |
| `app/services/web_subscriber_forms.py` | 1 |
| `app/services/web_system_user_mutations.py` | 1 |

**Scripts count.** A guard scoped to `app/` sees nine of the eleven and calls
the other two absent. That is exactly the defect hard rule 25 names, which is
why the sweep enumerates entry-point families (`app`, `packages`, `src`,
`scripts`/`bin`, `alembic`/`migrations`, `tasks`, `workers`/`jobs`, `cli`,
`cron`, plus repository-root modules) and reports which a repository lacks.

### Reconciling with the hand census

The Knowledge census `credential-lifecycle-product-first-census-2026-08-30`
recorded **10** `verify_password` sites and **25** `hash_password` sites in Sub;
the AST sweep records **7** and **18** at the same commit. The FILE SETS agree
exactly — four and eleven — and the difference is entirely measurement
definition: the census counted grep lines, which include the import statement
and other mentions; the sweep counts calls.

Calls are the number the ratchet freezes, because a mention-counter cannot show
progress: retire a caller and its import lingers, the count barely moves, and
the gate stops being believed. Both numbers are recorded here so a reader can
tell which is which rather than discovering a silent 25→18 "improvement".

One census figure did not reproduce: it recorded three `verify_password` sites
in `dotmac_vendor_control_plane`. At `36ff215be` that repository contains no
`verify_password` mention at all — grep and AST agree on zero. Its only password
surface is `src/vendor_cp/platform_admin.py` (two `hash_password` calls plus its
import). The measured figure supersedes the census figure for that repository.

### `needs_rehash` is NEW behaviour, not extracted behaviour

`password_needs_rehash` returns **zero** hits across all three products. No
Dotmac product upgrades a legacy hash on successful login today. The facility's
`CredentialVerificationResult.replacement_hash` is therefore built and tested
here as new behaviour; describing it as ported would be a false product-first
claim.

## Behaviours the facility must preserve

Ported from Sub's production lifecycle and asserted in
`tests/unit/test_credential_lifecycle.py`:

1. A correct password against a healthy credential is accepted.
2. A wrong password, and an unknown credential, are indistinguishable
   (`invalid` in both cases) — no account-existence oracle.
3. Reset-required is a *successful* password check that must not become a
   session. Sub enforces this at every verification path; the verdict type is
   what stops a fourth owner collapsing it back into a boolean.
4. Lockout and disabled state refuse a session even with a correct password.
5. Provisioned credentials are reset-required from the moment they exist.
6. Every reset revokes the principal's sessions.
7. Recovery is an intent, not a delivered password.

## Deliberate departures from Sub

Each is a change, not an oversight, and each is asserted in the test suite.

- **Lifecycle state is disclosed only after the password matches.** The engine
  always performs a verification (against throwaway material when no credential
  exists), then reads active/locked/reset-required. Reporting `locked` to a
  guesser is a valid-account signal.
- **Provisioning cannot accept material.** `reseller_onboarding
  ._create_credential` took a caller-supplied `password` that no supported
  caller ever passed; that unused parameter is how one value reached 24 external
  organisations. Sub removed it (PR #2826). Removal is absence; the facility's
  signature makes the shape unrepresentable.
- **Generated material never leaves.** No return value, log line, exception
  message, `repr`, receipt field or audit row can carry it. The subject reaches
  a usable credential only through the product's recovery channel.
- **No password policy ships in the kernel.** `PasswordPolicyPort` is
  product-installed; a default here would quietly become everyone's policy.
- **A policy that refuses the generator's own output fails loudly**, rather
  than being retried until it passes — an intermittent misconfiguration is
  worse to diagnose than a permanent one.
- **The legacy-hash rehash is a REQUEST, not a write.** The engine holds the
  raw material only during verification, so it is the only place an upgrade can
  be computed; the write stays with the product adapter, in the caller's
  transaction.

## Product-owned seams

The engine knows no product vocabulary. `application`, `principal_kind`
(`reseller_user`, `subscriber`, `system_user`, every ERP principal),
`principal_ref`, `credential_ref`, `reason_code`, `approval_policy_code` and
`approval_decision_ref` are opaque strings it stores, sorts and returns and
never parses, splits, case-folds or branches on.

| Port | The product supplies |
| --- | --- |
| `CredentialStorePort` | credential rows, row locking and writes, in the CALLER's transaction |
| `SessionRevocationPort` | a durable revocation intent for the external session store |
| `RecoveryIntentPort` | durable recovery intents, and atomic single-use spending of a proven recovery authorization |
| `PasswordPolicyPort` | the product's password rules |
| `CredentialAuditPort` | append-only evidence, and the receipt that doubles as the idempotency record |

Database effects use the caller's transaction — the engine never commits, never
rolls back and never opens a session, so "all targets atomically or none" is a
property of the caller's boundary, which is the only place it can honestly live.
External session stores and recovery delivery are durable outbox intents with
idempotent reconciliation, never in-line calls.

The engine also has no ORM, no web framework, no HTTP status code, no provider
client, no secret-store client and no network import; the boundary tests assert
each of those as an absence from the import graph rather than as a convention.

## The plan digest

`CredentialResetPlanDigestV1` is a typed value owned by this module. It is not a
universal kernel digest and it is not `dotmac-deployment-control`'s plan digest;
two digests that both happen to be SHA-256 are two contracts. The plan sorts its
targets canonically, so input order cannot change an approved plan's identity,
and `apply_force_reset` recalculates the digest from the plan in hand rather
than trusting the authorization's copy.

Authorization is a separate record (`plan_digest`, `approval_policy_code`,
`approval_policy_version`, `approval_decision_ref`, `approved_at`) and carries
**no** `approved_by`: the approval decision is a product record with its own
actor, quorum and audit trail, and `approval_decision_ref` points at it. A
copied actor name would be a second, weaker claim about who approved.

**This is product security authority.** `dotmac-deployment-control` owns fleet
deployment intent and must not authorize an account mutation.

## Explicit exclusions

- `AccessCredential` (`dotmac_sub:app/models/catalog.py:1445`) and
  `SnmpCredential` (`dotmac_sub:app/models/snmp.py:29`) are DEVICE/SERVICE
  credentials and are out of scope. A sweep keyed on `*Credential` pulls them
  in and hands the facility responsibilities it must not have. The human
  credential model is `UserCredential` (`dotmac_sub:app/models/auth.py:132`)
  alone.
- Machine credentials: owned by `dotmac_kernel.machine_auth`, a separate
  extraction with its own dossier rows.
- OIDC and federated identity: `dotmac-auth-oidc`, ADR-0069.
- Provider delivery (email/SMS transport): the product's own delivery stack;
  this facility emits intents.
- Party-schema convergence: unrelated and not attempted here.

## First adopter and the retirement gate

**First adopter: `dotmac_sub`**, starting with `app/services/auth_flow.py` — the
de facto canonical owner on every axis. Adoption is sequenced and has not
started:

1. Sub's containment lane releases the Sub writer lease.
2. Michael authorizes a kernel release; the version is derived from immutable
   tags at that time and is **not** assumed. No version is allocated by this
   change.
3. Sub pins the released kernel and routes `auth_flow.py` verification through
   the facility, keeping its own HTTP mapping and session issuance.
4. The other three verification owners follow, then the eleven
   `hash_password` callers, seed scripts included.

**Local-copy retirement gate.** A product-local owner is removed only when all
of the following hold, and each lowers the baseline in the same change:

- every verification path in that product returns a facility verdict, and no
  path re-derives active/locked/reset-required from raw fields;
- every credential-creating path calls `provision` and none accepts caller
  material — the parameter is gone from the signature, not merely unused;
- reset completion runs behind a single-use recovery authorization;
- the product's own behaviour suite passes against the facility with the
  DEPARTURES above asserted rather than skipped (a run that passes Sub's suite
  unchanged would prove the extraction reacquired the shape it exists to
  prevent); and
- `docs/inventories/credential-lifecycle-baseline.json` is lowered in the same
  commit, so the retirement is reviewable as a diff.

Until then the baseline rows are FROZEN DEBT. They are not a review verdict and
not an approval.

## Non-claims

- No shared kernel version is released, allocated or pinned by this change, and
  no product composes the facility.
- No production credential, account or session was inspected, and no production
  reset is authorized or performed by this lane.
- The tests were not executed on this workstation; CI is the acceptance owner.
- `dotmac_sub` was read at an immutable commit only. Its working tree was not
  modified, and no adoption work was started there.
- The sibling rows in the baseline are enforced only where the fleet is beside
  the checkout AND the recorded commit is present locally. In CI they abstain by
  design; the `dotmac_starter_mt` row is the always-enforced half.

## Scope amendment — 2026-10-18: Academy authorized supplied-password paths

Status: product source audit and bounded SAME-owner contract review complete.
Assignment-engine source is prepared; composed source/catalogue review, hosted
qualification, future release and installed product adoption remain pending. This
amendment supplements, rather than rewrites, the 2026-08-30 Sub rationale,
deliberate departures, measured baselines and adoption gates above. In particular,
"every credential-creating path calls provision" now means every supported path
uses the SAME owner: secret-free unknown-material recovery `provision`, or the
explicitly authorized supplied-password assignment contract in ADR-0006's dated
2026-10-18 amendment. Merely renaming a product writer is insufficient. The old
`provision` signature still accepts no secret and yields no secret-bearing field.
No hash-call baseline is changed here; lowering requires actual retirement.

### Immutable audit scope and evidence

Academy source: `a5ffe6316e97bf33e17ef9c1f00788796f5e5a5a`, tree
`3e5678114a65ad760fa85f3367719a087bbf3b07`; locally supplied exact source closure
`/private/tmp/academy-a5-python-closure-20261017`. All 460 Python files parsed
with standard-library AST; no import/runtime/test execution. This closure covers
application, scripts and tests. The current protected Starter baseline for ADR,
inventory and EXTRACTION is `43aeb79ff481a8927c38e681fc43ec0314037a14`, fetched
through the dedicated GitHub read connector at that exact ref. AGENTS/ARCHITECTURE
use the prepared unpublished identity-history-full-22-v2 source and preserve its
account-reset/history amendments. This does not rerun the historical Sub/ERP/CP
production audit or claim Academy production runtime was inspected. Academy is
the concrete product-first behavior source for this bounded extension; actual
production privileges and installed parity remain to be qualified.

Knowledge scoped searches succeeded for project:dotmac_starter_mt and
project:dotmac_academy_app. Canonical records retrieved:
`learning-academy-credential-adapter-plan-2026-10-18` v2,
`academy-learning-core-extraction-goal-2026-10-16` v168 and
`learning-academy-credential-reset-consolidated-source-2026-10-18` v1.
An initial miss used an accidental root-prefixed slug; corrected GET succeeded.
No scope denial. Knowledge describes plans/source status, not runtime proof.

### All actual assignment entrypoints and transitive callsites

| Entrypoint / source | Existing behavior at immutable Academy revision | Required installed seam |
| --- | --- | --- |
| `app/web/accounts.py:492 users_create` → `:514 create_user`; `app/services/accounts.py:29`, hash `:84` | Actual request cookie/session via require_web_user; tenant and role-grant query; instructor/admin create students, admin alone creates instructor/admin. Normalized email, duplicate check, ensured roles, Person + credential + grant, flush without commit. NO strength restriction. create_user has NO actor/context argument. | Request-bound authority re-resolves cookie HMAC, fresh session/tenant/activity/status/grants and locks real rows; thread actual context into service. Settings-backed role policy preserves matrix and validates exact granted tenant role. Savepoint encloses all Person/role/credential work. |
| `app/cli.py:61 _bootstrap` → platform_session `:67` → `bootstrap_tenant :68`; `app/services/bootstrap.py:42`, hash `:84` | Creates tenant, three roles, initial admin Person/credential/grant with supplied password and no strength restriction; caller owns commit. | SAME Connection current_role/session_user/catalog/effective privileges + exact tenant-create context. Initial migration explicitly allows platform_api tenant INSERT and identity CRUD; do not require app_admin alone. Installed settings-backed runtime/role/strength policy. |
| `scripts/seed_academy_demo.py:49 main` → absent admin `create_user :67`; existing admin missing credential hash `:92`; selected credential hash `:97` | Seed resolves password before DB, minimum 12 plus sentinel refusal with no secret default. Uses migration_database_url; selects tenant, then Person by tenant/email, separately credential by tenant/email via unordered first(). If present, reassigns credential.person_id to selected admin and replaces hash. Adds admin/instructor grants. | Real same-Connection runtime privileges and exact tenant/admin/credential selection context; URL/env/password are not authority. Missing credential uses create-absent; selected credential uses CAS replace with explicit expected previous subject/version, preserving source selector. Prior Party MAY differ from target admin. |

The web cookie path is actual source, not invented authorization:
`app/services/web_auth.py:96 _current_person` hashes the cookie token and selects
tenant session with no revocation and future expiry, then requires active Person;
`:117 require_web_user` establishes tenant/request context. `_role_slugs` is
`app/web/accounts.py:52`. These existing reads are NOT a fresh locked assignment
authority today. Future adapter must lock/revalidate real rows, retain actual
Session/root/actor/command context and coordinate revoke/delete/grant writers.

Bootstrap initial grants are source-visible in
`alembic/versions/20260504_0001_initial_tenant_schema.py:419-430`: platform_api
can mutate tenants and identity rows. Its docstring's narrower superuser/migration
example does not override executable grants. Actual Connection privilege proof
is hosted work still owed; no production role privileges inspected.

Seed selected credential retains credential ID, email/alias values, failure
counters, lockout and sessions. There is no counter clear/unlock/revoke/alias
fanout in its overwrite branch. Existing target Person name/status and intended
grants remain product effects inside the same whole-account savepoint. Explicit
previous-subject binding preserves real independently selected prior principal
reassignment, rather than treating it as impossible or using create-or-overwrite.

### Complete non-test primitive call census (AST calls, not grep mentions)

At this exact Academy source revision: six hash_password calls in four files,
two verify_password calls in two files, zero password_needs_rehash calls.
Four hashes are assignment paths above. Two others remain distinct:
`app/services/lifecycle.py:126 set_account_password` and `:266 accept_invite`.
Verification is `app/services/web_auth.py:51` and `app/web/account.py:140`.
Account reset/authenticated password change/invite/login retirement and genuine
subject proof remain required; assignment does not replace their authorization.
No other non-test calls to these primitive names were found across the 460-file
AST closure. This is a direct-name call census, not a claim to detect arbitrary
reflective/dynamic invocation. Test fixtures are excluded from runtime debt.

### Existing product tests to preserve (read, not run)

- `tests/services/test_accounts.py`: Person/credential/grant creation, duplicate
  email, normalization and invalid role.
- `tests/web/test_accounts.py`: instructor student creation, instructor refusal
  for admin, admin creation and immediate login/learner access, existing role
  management and invitation behavior.
- `tests/services/test_bootstrap.py`: tenant/roles/admin/credential/grant.
- `tests/test_seed_admin_password.py`: missing/weak refusal, stronger accepted
  password and no hardcoded password default scan.
- `tests/services/test_lifecycle.py`, `tests/web/test_lifecycle_web.py`,
  `tests/web/test_account.py`, `tests/web/test_web_auth.py`,
  `tests/test_auth_lockout.py` and `tests/services/test_account_invitations.py`:
  related reset, invitation, session and lockout regression surfaces.

There is no claim these old tests cover the new authority/CAS/audit contract;
the seed script's prior-party-different behavior needs a new explicit canary.
Kernel's existing owner unit/ratchet/boundary suites remain. The 25 assignment
unit-test functions are authored, not run; composed public compatibility and
installed product/CAS/concurrency qualification remain pending.

### Owner extension, audit binding and policy composition

ADR-0006's 2026-10-18 amendment defines closed create-absent/replace-selected
commands; installed authority obtains actual material-free actor evidence, new
assignment store binds old Party/tenant/credential/version CAS, one installed
policy and one hash persist explicit reset_required=False. No recovery intent,
unknown intermediate, failure clear, session revoke or alias sync. Existing
store.write_hash and old audit entry/port stay unchanged; optional three
assignment ports append AFTER clock without moving old arguments.

The separate CredentialAssignmentAuditEntryV1 has exactly command, EXACT issued
authority evidence, previous_credential_version (None for creation),
credential_version and occurred_at. Its action is fixed as
credential.password.assigned; prior/target identity and correlation are already
bound by the command. No credential snapshots or hashes are included.
CredentialAssignmentAuditPort.record_assignment returns a real
current audit ID carried in the material-free receipt. Authority and audit share
one bound adapter with private Session/root/command/actual actor; copied, stale,
cross-root evidence and changed outcomes refuse. Current audit actor derives from
private actual context using canonical user/service/api_key/system kinds; no fake
human, new database kind or missing-identity system fallback. Opaque subject refs
are product-resolved; Kernel cannot parse a tenant from them.

User requirement: "nothing hardcoded, composable policy settings only".
Strength, role matrix and runtime-authority policy compose installed typed
effective settings-backed ports. Existing web/bootstrap no-strength behavior,
seed stronger minimum 12/sentinels and reset-only documented default 8 stay
separate settings defaults. Exact sorted roles refuse duplicates and must match
actual tenant roles/grants. No engine policy default, arbitrary product hasher,
new permission, external approval ceremony or provider redirection. Stable schema/
action IDs and identity/version constraints are contracts, not policy knobs.

### Remaining installation, qualification and recovery gates

No engine/public exports/catalogue/compatibility changes were made. Independent
review and coordinator approval must close this dossier before implementation.
Then owe engine and material-containment/old-signature/constructor/CAS/evidence
unit canaries; product settings-backed ports and real web/CLI/seed wiring; exact
seed prior-party same/different and unchanged side-effect tests; canonical Party/
PartyPerson/tenant and product policy state; current action declaration/audit;
whole-account savepoint and real caught-response rollback; coordinated session/
grant/Party lock order and participating revocation/membership/session writers.

Hosted tests must prove actual privileged platform bootstrap/seed and ordinary
role refusal on the same Connection; genuine migrated Postgres tenant isolation,
absence/CAS/concurrent grant/principal/version races; fresh cookie/session/status
refusals, one policy/one hash/False/current audit ID, copied/stale/cross-transaction
evidence refusal, failure after every write/audit and failed-rollback fatality.
Installed future wheel/release qualification is pending; published 106 must not
be republished and no successor is allocated. Current production privileges,
accounts, passwords and sessions were not inspected. No local runtime/import/
tests/build/install/SQL compilation/DB/Docker/SSH/network shell was used.

Preserve the separately reviewed reset-state/adapter/settings source and all
history-import/account-reset requirements. Full installed Academy auth/provision/
invite/admin/bootstrap/seed retirement, canonical FK/catalog and complete Learning
retirement remain pending. Populated recovery must retain NEW accepted facts,
events/deletes, identity, credentials/versions/assignments/audits, references,
grants and Integration ACK/confirmed ERP receipts with one writer. An old backup
or binary restore alone does not close recovery. No release, adoption, deployment
or production readiness is asserted by this five-file source amendment.

### Source file byte seals (SHA-256, immutable Academy revision above)

| Source path | SHA-256 |
| --- | --- |
| `app/services/accounts.py` | `e337a114fa282558d4db8d776e39b26fb2075563cb9b093375af6e42f77987f2` |
| `app/web/accounts.py` | `1aa300795aa53b47b83a302411a00bea0d34477fc751f8b5a950f2fbd24919e8` |
| `app/services/bootstrap.py` | `60a1c0747f3d353e514ac676e1a2e9daee2c820ed6e54df9338704e9275d3be2` |
| `app/cli.py` | `f0cff4e51ff5024d95f9af0ca91287559296ee47852136b3df9275bf8a125d1c` |
| `scripts/seed_academy_demo.py` | `35274d98cc8b93799835a3598ab5519e8bc7fdcd0f62ce36bfdbfc21f1f93e1e` |
| `app/services/web_auth.py` | `245c036f6fda2367a9a5e3393e6cd0574944f2cb0816cfd00584a9f1497f2856` |
| `app/services/lifecycle.py` | `fcb2e97af8e92859adc5d76899c9c2226271669085fd92109c280d93cef9e7d7` |
| `app/web/account.py` | `fd06ec72ff3c5ca7cb097debc9a16ad6537cee9dc0c91831e4b09c9f80c7dcb1` |
| `alembic/versions/20260504_0001_initial_tenant_schema.py` | `33f9569bccc1562a2973df75e597332bb13b3003bab4d1815943e6cbee3eba3e` |
| `tests/services/test_accounts.py` | `6cad9f220cf4fae5231bab041848dbb105560fce094f6f458dffd328adb17418` |
| `tests/web/test_accounts.py` | `8ee0c123afc1760c1b62757c09aca24faed4c09b872e2d8f857ca1dab3b0ec89` |
| `tests/services/test_bootstrap.py` | `5300f0573ce57909a02b71345a85865cc7305721eed1b499e38ee3bf25d9ded7` |
| `tests/test_seed_admin_password.py` | `394144b01977aaed53fcd35795db9e1ea55578825a8583758ea4004389915e78` |
