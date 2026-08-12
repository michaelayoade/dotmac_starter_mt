# The `user_credentials` principal decision

**Status:** DECISION REQUESTED — this recommends one model. Michael rules.
**Date:** 2026-08-12
**Sub:** `9f6f9f36b` (`feat/kernel-pin-a40`) · **Kernel:** `0.1.0a40` (Sub is pinned to it)

This decision gates two workstreams that were being planned as independent:

- **kernel revision `0001`** cannot be dispositioned until `user_credentials`
  converges, and no collision after `0001` can be reached until it does
  (`docs/inventories/sub-lineage-dispositions.md` § "Where the gate actually sits");
- **`PARTY_PRINCIPAL_CONTEXT_BINDING`** needs the same answer to finish its
  backfill, parity and cutover.

---

## Correction before the decision: three principals, not four

The dispositions inventory says Sub "links to four different principal kinds
(`subscriber_id`, `system_user_id`, `reseller_user_id`, `radius_server_id`)".
That is wrong, and it matters because it makes the problem look harder and
points the design at the wrong column.

`ck_user_credentials_exactly_one_principal` (`app/models/auth.py:48-57`) sums
**three** columns and requires exactly one:

```
(CASE WHEN subscriber_id   IS NOT NULL THEN 1 ELSE 0 END
 + CASE WHEN system_user_id  IS NOT NULL THEN 1 ELSE 0 END
 + CASE WHEN reseller_user_id IS NOT NULL THEN 1 ELSE 0 END) = 1
```

`radius_server_id` is outside that constraint. Its only read is
`app/services/auth_flow.py:1109-1117`, inside `if resolved_provider ==
AuthProvider.radius:` — it selects *which RADIUS server verifies the password*.
It is a **provider qualifier, not a principal**, and it should never have been
counted as an identity kind.

**So the decision is: three human principal kinds, plus one provider-scope
column that is not part of it.**

---

## Recommendation

> ### ⚠️ SUPERSEDED, 2026-08-12 — the `context` key below is not the target
>
> This dossier was written before ADR-0019 was accepted. The ADR rules against
> the key recommended here, in two places:
>
> - **§2** — a credential may repeat per **authentication mechanism**, "never per
>   *principal kind*";
> - **§5c** — *identity does not multiply with relationships*: one login per
>   person **not** per account, and a person in two resellers keeps **one**
>   credential.
>
> `context ∈ {customer_portal, staff, reseller_portal}` is a portal vocabulary,
> and a portal is a relationship surface. Keying the credential on it does
> exactly what §5c forbids, one indirection removed — and it hardcodes Sub's
> product topology into an identity table, so adding a portal would become a
> schema change. **The target is `(tenant_id, party_id, <authentication mechanism
> binding>)`.** See "The composable target" below.
>
> The rest of this dossier stands: the three-principals correction, the
> per-principal migration effects, the organization-party canary, the `api_keys`
> defect, and the three-release rollout are unaffected by the key change.

Superseded target shape, kernel-owned and product-neutral:

| column | note |
|---|---|
| `tenant_id` | kernel contract; Sub has one operator tenant, so the backfill is a constant |
| `party_id` | **person** Party — the identity |
| ~~`context`~~ | ~~which access surface this credential authenticates for~~ — **superseded**, see the banner above |
| `password_hash`, `provider`, `username` | unchanged |
| lockout/rotation columns | Sub's six; the kernel adopts them (a40 direction) |
| `radius_server_id` | stays, Sub-local, renamed — see below |

~~`UNIQUE (tenant_id, party_id, context)`~~ — superseded. What it replaces is
unchanged: Sub's exactly-one-principal CHECK and the kernel's implicit
one-row-per-party assumption both still have to go.

## The composable target

The discriminator is **which authentication mechanism proves you are this
party** — never which portal, role, account or membership you reach afterwards.
Those are resolved from PartyRoles and memberships *after* authentication, which
is ADR-0019 §2's whole point.

Composable means the mechanism vocabulary is **open and declared**, not a kernel
enum or a fixed CHECK: a product names `local`, `oidc`, `radius`, or something
not yet imagined, without a kernel migration (ADR-0008). Typed means the *code*
is open while the *contract behind it* is closed — the kernel types the
verifier interface, not the list of names:

- a declared mechanism carries a **typed provider binding**, not a JSON blob;
- provider-specific state lives behind that binding, so **the kernel never
  acquires a `radius_server_id`** — Sub's RADIUS verifier is binding config,
  which is exactly why it was never a principal;
- the credential column stays a plain string, validated against the registry at
  the use boundary — the same shape as `SettingDomain` (ADR-0008) rather than a
  native enum.

**One thing this still gets wrong and needs evidence for.** The key cannot be
the mechanism *code*. Two OIDC issuers, or two RADIUS verifiers, are two
mechanisms of the same code, and `(tenant, party, 'radius')` would forbid a
party holding a credential against each. The key is the **binding** — the
installed, configured instance — not its type. Confirming Sub's real
cardinality here (how many verifiers, whether any party authenticates against
more than one) is a prerequisite for R1, and it is measurable today.

### Why not the alternatives

**Collapse to `party_id` alone** (the kernel's current shape). Rejected: it
destroys a distinction Sub built deliberately. The comment at
`app/models/auth.py:49-52` is explicit that a reseller portal login is *"its own
identity, not a fake subscriber"*. One person who is both a staff member and a
subscriber holds two credentials today, legitimately, with different passwords
and different lockout state. Collapsing them merges two logins into one and
silently changes who can sign in to what.

**Kernel adopts Sub's three FKs.** Rejected: it puts `subscribers`,
`system_users` and `reseller_users` — three Sub domain tables — into the kernel.
That is not a union, it is exporting Sub's domain model into the shared
distribution, and it fails the product-neutrality the kernel exists for.

**Split into two tables** (kernel credential + Sub-owned context row). Rejected
as first choice, though it is the closest runner-up. It splits one
authentication decision across two rows read on every login, and it forces the
lockout/rotation columns to pick a side — lockout is a property of *this
credential in this context*, so it would end up on the Sub row, leaving the
kernel owning a table that cannot answer "is this login locked?".

### Why the kernel must change, not only Sub

`app/features/auth/service.py:188-193` resolves the credential with:

```python
select(UserCredential)
  .where(UserCredential.tenant_id == tenant.id)
  .where(UserCredential.party_id == party.id)
).first()
```

`.first()` on a non-unique predicate. Today that is safe because the starter
creates one credential per party. After Sub's cutover, a person who is both
staff and a subscriber has two rows with the same `party_id`, and `.first()`
returns **an arbitrary one** — authenticating against the wrong context's
password and lockout state, with no error. This is the a40 "make the kernel
adopt the product's stronger table" direction applied to behaviour rather than
columns: Sub's three-principal model is the more correct one, and the kernel's
cardinality assumption is the thing that has to yield.

---

## Migration effects, per principal kind

### `system_user_id` — staff. Cleanest; do this one first.

`SystemUser.person_party_id` already exists (migration 353), is `UNIQUE`, and
carries the all-or-nothing evidence CHECK (`app/models/system_user.py:28-40`).
The mapping is total and unambiguous: `credential.system_user_id →
system_users.person_party_id`, `context = 'staff'`.

*Residual risk:* rows where `person_party_id IS NULL` cannot be migrated. Those
are staff with no reviewed Party yet — they block the cutover for this context
and are the measurable backlog.

### `reseller_user_id` — reseller portal. Clean, and it validates the design.

`ResellerUser` carries both `person_party_id` and `party_membership_id`
(`app/models/subscriber.py:797-805`). This is the case the recommendation is
shaped for: the *identity* is the person Party, the *context* is the
membership. Map to `context = 'reseller_portal'` and keep the membership
reference as the scope the authorization owner reads.

*Residual risk:* a person holding memberships in two resellers would need two
credentials in the same context. `(tenant_id, party_id, context)` forbids that.
Either the context key includes the membership, or this is declared
out-of-scope. **Open sub-question — see below.**

### `subscriber_id` — customer portal. The hard one, and not for the obvious reason.

`Subscriber.party_id` exists (migration 350), but **`bind_subscriber_account`
does not constrain `party_type`** (`app/services/party.py:257-299`) and its
docstring states *"One Party may own several subscriber accounts."* Two
consequences:

1. **An organization-owned account resolves to an organization Party.** The
   kernel is explicit that *"organization parties have no login of their own"*
   (`auth/service.py:152-154`). So `credential.subscriber_id → subscriber.party_id`
   can yield a party that structurally cannot hold a credential. The migration
   must detect these and refuse, not coerce. This is the largest unknown in the
   whole disposition and it is measurable today with a single query.
2. **One person, several accounts, one context.** If a person holds three
   subscriber accounts and a credential per account,
   `(tenant_id, party_id, 'customer_portal')` collapses them to one. That may
   be correct (one login, then choose an account) but it is a **product
   behaviour change**, not a schema migration, and it must be decided rather
   than discovered during backfill.

### `radius_server_id` — not a principal; keep and rename

Stays exactly as it is, Sub-local, out of the kernel. Recommend renaming to
`radius_verifier_id` (or moving it beside `provider`) in the same slice, purely
so the "four principal kinds" reading cannot recur — it has already cost one
round of analysis.

---

## The same triple is on three more tables

Whatever is decided applies to **four tables, not one**:

| table | principals | exactly-one CHECK |
|---|---|---|
| `user_credentials` (`auth.py:41`) | 3 | yes |
| `mfa_methods` (`auth.py:114`) | 3 | yes (`:139-141`) |
| `sessions` (`auth.py:215`) | 3 | yes (`:218-220`) |
| `api_keys` (`auth.py:268`) | **2** — no `reseller_user_id` | **none** |

`api_keys` is a defect on both counts: a reseller cannot hold an API key, and
nothing enforces that a key has exactly one principal — a row with both
`subscriber_id` and `system_user_id`, or neither, is currently legal. Fix it in
this slice; it is the same decision and the same backfill shape.

---

## Deployment order — the part that must be explicit

**Sub's deploy applies migrations while the previous image is still serving
production traffic.** `scripts/deploy.sh` order:

1. verify OpenBao boot secrets, pre-migration identity checks
2. pin `APP_IMAGE`/`GIT_SHA`
3. **`alembic upgrade heads`** ← line 684
4. verify schema contracts, manifest pins, CRM readiness
5. start warm candidate on `127.0.0.1:${CANDIDATE_PORT}`, health-gate it
6. `docker compose up -d` — recreate the primary with the new image
7. health gate; on failure `restore_prev`

The old image serves from step 3 to step 6 — through four verification
subcommands and a health-gated candidate start. That is minutes, not seconds.
And the script states the rule outright at lines 658-660: *"Migrations are NOT
reverted; new revisions must stay backward-compatible with the previous
release."* The rollback log says the same: *"Rolled back to ${PREV_IMAGE}.
NOTE: migrations from ${TAG} were NOT reverted."*

So a migration that FORCEs RLS and a new image that sets the GUC **cannot ship
in one revision**, even in one commit. At step 3 the old image — which sets no
GUC anywhere in Sub (`app_current_tenant_id`, `set_config('app.tenant_id')`:
zero occurrences under `app/`) — would lose all `roles`, `user_credentials` and
`audit_events` visibility and stop authenticating. A failed health gate then
rolls the *image* back and leaves the *migration* applied: permanent auth
outage until manually repaired.

### Therefore: three releases, in this order

| # | Migration | Application | Invariant that must hold |
|---|---|---|---|
| **R1** | add `tenant_id`/`party_id`/`context` as **nullable**, backfill, add the tenant function, add `UNIQUE (tenant_id, party_id, context)` — **no RLS, no NOT NULL** | ships GUC-setting session middleware; still reads the legacy principal FKs | old image reads exactly as before; new image sets the GUC but nothing depends on it yet |
| **R2** | none | readers cut over to `party_id`/`context`; legacy FK reads retire behind a flag | both images can set the GUC; parity measurable in production |
| **R3** | `NOT NULL`, drop legacy FKs, **then** `ENABLE`/`FORCE` RLS + policy | unchanged | every image that can reach the database already sets the GUC — proven in R2, not assumed |

The rule to state in the disposition: **RLS activation may only land in a release
whose predecessor already sets the GUC in production.** R1 and R2 are what make
that true; R3 is the only one that may touch RLS. If a single-release version of
this is ever attempted, the migration must stage activation — create the policy
but leave RLS disabled, and enable it in a later, separately deployable step.

---

## Failure canaries

Each must fail loudly, and R1's must exist *before* R1 ships.

**Pre-migration gates** (run as `deploy.sh` step 1 does today, `--check` style):

1. **Organization-party credentials.** `COUNT(*)` of credentials whose
   `subscriber_id → subscribers.party_id` resolves to a `party_type =
   'organization'` party. **Must be 0**; a non-zero count blocks R1 and is the
   first thing to measure — it is knowable today.
2. **Unbound principals.** Credentials whose principal has a NULL
   `person_party_id`/`party_id`. Non-zero is the migration backlog; R3 cannot
   run until it is 0.
3. **Context collisions.** Rows that would violate
   `(tenant_id, party_id, context)` after mapping. Non-zero means the
   one-login-per-context question above was answered wrongly.

**Post-migration, pre-cutover (R1):**

4. **GUC presence canary.** A request-path assertion that
   `app_current_tenant_id()` is set and non-NULL, logged as a counter. R3 is
   gated on this being 100% for a sustained window — this is the *proof* that
   replaces the assumption.
5. **Zero-row canary.** A read of `roles`, `user_credentials` and
   `audit_events` that asserts a non-zero count. This is the specific detector
   for the fail-silent RLS mode: RLS returns an empty set rather than an error,
   so only an explicit "this must not be empty" assertion catches it.

**Cutover parity (R2):** legacy-principal resolution and Party resolution run
side by side on every login; any divergence is recorded and alerts. Sub already
has the shape for this in its 17 parity/shadow test modules.

**Rollback boundary:** R1 and R2 are revertible by image alone. **R3 is not** —
once RLS is forced, rolling back the image reintroduces the outage. R3 therefore
needs an explicit `DISABLE ROW LEVEL SECURITY` runbook step, not an image
rollback, and that must be written before R3 ships.

---

## What this does not decide

Flagged rather than assumed, because guessing any of these would be worse than
asking:

1. ~~**One login per person per context, or per account?**~~ **DECIDED** by
   ADR-0019 §5c: per person. An account is a relationship, not an identity.
2. ~~**A person with memberships in two resellers.**~~ **DECIDED** by §5c: one
   credential, two `party_memberships`, a switcher after login. The key must not
   include the membership.
3. **Mechanism-binding cardinality — NEW, and now the blocker.** The key is the
   authentication *binding*, not the mechanism code, or a party could not hold
   credentials against two OIDC issuers or two RADIUS verifiers. How many
   verifier/issuer bindings does Sub actually have, and does any party
   authenticate against more than one? Measurable today; see "The composable
   target".
4. **Many-to-one merge policy.** §5c fixes the target cardinality but not which
   password, MFA methods, sessions and lockout state survive when several
   credentials collapse onto one party. That is a security-sensitive policy
   needing production evidence, not a schema choice.
5. **Do Sub's `sessions` and `api_keys` cut over in the same slice** as
   `user_credentials`, or follow? They share the triple, so the schema answer is
   the same, but the blast radius differs.
6. **`audit_events`** is the other `0001` union and is untouched here. Its
   request-forensic columns (`ip_address`, `user_agent`, `request_id`,
   `status_code`) need measuring against real audit queries before the kernel
   folds or promotes them.

**Items 3 and 4 now block R1.** Items 1 and 2 no longer do — §5c settled them.
R1's additive half (nullable columns, backfill, collision reporting, start
setting the GUC) is draftable without 3 and 4; the uniqueness constraint and any
credential merging are not.

---

## Evidence

Read at the commits above; **no test was run** — Sub's suite and the lineage
rehearsal run on the Git-hosted CI/Observe runners, not locally.

- `dotmac_sub/app/models/auth.py:41-112` (credential), `:114-160` (MFA),
  `:215-265` (sessions), `:268-297` (API keys)
- `dotmac_sub/app/services/auth_flow.py:1109-1117` (RADIUS verifier)
- `dotmac_sub/app/services/party.py:257-299` (`bind_subscriber_account`)
- `dotmac_sub/app/models/system_user.py:24-50`,
  `dotmac_sub/app/models/subscriber.py:797-805` (existing bindings)
- `dotmac_sub/scripts/deploy.sh:645-730` (deploy order, backward-compat rule)
- `packages/dotmac-kernel/src/dotmac_kernel/models.py:292-333` (kernel credential)
- `app/features/auth/service.py:148-196` (the `.first()` cardinality assumption)
- `packages/dotmac-kernel/.../20260504_0001_initial_tenant_schema.py:402-443`
  (`_apply_rls`, `_grant_roles`)
