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
