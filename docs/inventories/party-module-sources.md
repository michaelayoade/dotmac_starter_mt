# Party module extraction audit — CRM, ERP, Sub

**As of:** 2026-08-11
**Starter:** `c8237bd` · **Sub:** `9f6f9f36b` (`dev`) · **ERP:** `d8e5bcc0` ·
**CRM:** `c64b5aa0` (last commit 2026-07-10)

> **Disposition update — 2026-08-19.** ADR-0019 superseded this audit's
> recommended name: kernel RBAC is `party_role_grants`, and `party_roles` is
> reserved for business capacities. Michael then directed an unreleased,
> audit-complete implementation. `packages/dotmac-party` now owns the five
> extension tables in `mod_party`; kernel `parties` and its subtype tables stay
> the identity owner. The four vocabularies are open declaration registries,
> every module table carries direct tenant identity and forced RLS, and the
> recorded relationship deviation is resolved as PartyRole-to-PartyRole.
> Sub's backfill/reader/writer cutovers remain binding: the package is neither
> released nor adopted, and the historical findings below remain the source
> evidence rather than a claim about current package state.

This is an ADR-0006 § "product-first extraction" dossier for one candidate: a
composable, reusable **party identity module** for the starter. It answers the
qualification questions in
[`module-extraction-sources.md`](module-extraction-sources.md) — name the
contract, inventory every product, pick the production-proven source — and
nothing else. **A candidate row is not permission to extract.** Under ADR-0017
adoption, not scope, is the scarce resource; the last section states what would
have to be true before any code moves.

---

## Finding

**Sub is the only qualifying source.** It is the only repository in the fleet
that has separated *identity* from *role*, *relationship*, *membership*, and
*reachability* as independently-owned facts, and the only one that carries
behavioural proof of that separation (21 party test modules, 8 migrations, 11
approved SOT documents, one named writer).

**ERP and CRM are requirement inputs, not implementation sources.** Both model
a person as a single wide table that fuses identity, contact detail, postal
address, lifecycle status and external-system IDs. Neither can be ported; both
tell you which columns a real product ends up needing.

**The starter already owns the right shape and almost none of the behaviour.**
`dotmac_kernel.models` has `Party`/`PartyPerson`/`PartyOrganization` — correctly
tenant-scoped, RLS-enforced, subtype-split — and the `parties` feature is 400
lines of CRUD over it. Everything Sub proved beyond "a party exists and has a
name" is absent.

**There is a hard blocker that is not a porting detail:** `parties` and
`party_roles` already exist in both repos with *incompatible semantics under
identical names*. See "The party_roles collision" below. This must be resolved
by decision before the first line of an extracted module is written.

---

## What each repository actually has

| Concern | Starter (kernel) | Sub | ERP | CRM |
|---|---|---|---|---|
| Identity table | `parties` (tenant-scoped, RLS) | `parties` (no tenancy) | `people` (org-scoped) | `people` (no tenancy) |
| Person/org split | `party_persons` / `party_organizations` subtypes | `party_type` + profile bindings to existing domain tables | none — `Person` only; `core_org.organization` is the *tenant* | none — `Person` + separate `organizations` B2B account |
| Business roles | **none** (`party_roles` is an RBAC grant) | `party_roles`: 8 role types, `role_key` contract, temporal `valid_from`/`valid_until`, independent status | `person_roles` (RBAC only) | `person.party_status` — a single enum, lead→contact→customer→subscriber |
| Party↔party relationships | none | `party_relationships`: 12 directional types, explicitly non-authorizing | none | `organizations.parent_id`, `primary_contact_id`, `owner_id` — three ad-hoc FKs |
| Org membership | none | `party_memberships`: 7 types, bounded `access_scope` JSON, temporal | none | `organization_memberships` (owner/admin/member, no scope, no time) |
| Contact points | `parties.email` (one nullable column) | `party_contact_points`: 9 channels, provider + provider-account + immutable subject ID, verification *and* consent as separate facts, partial-unique primary per (party, channel, scope) | `person.email`/`phone` columns + `preferred_contact_method` | `person_channels`: 7 channels, `is_primary`/`is_verified`, no consent, no provider scoping |
| Identity lifecycle | `is_active` bool | `status` (active/quarantined/merged/archived), `merged_into_party_id` with two CHECKs making merge state self-consistent, `data_classification` (production/test/imported_unverified) | `PersonStatus` enum + `is_active` | `PersonStatus` + `person_status_logs` + `person_merge_logs` (log tables, no constraint enforcement) |
| External IDs | none | `party_external_references` — provenance only, doubly-unique, explicitly never lookup authority | none | `erp_customer_id`, `erp_person_id`, `erpnext_id` as unique columns *on* `people`; `splynx_id`/`selfcare_id` read out of a JSON blob by hybrid property |
| Named writer | `app/features/parties/service.py` (13 functions, CRUD) | `app/services/party.py` (1,647 lines, 30+ commands, declared sole native writer) | scattered across `services/people/*`, `services/finance/ar/*` | `services/crm/contacts/service.py` + 6 others |
| Behaviour proof | isolation canaries only | 21 test modules, incl. per-migration tests | `test_ar_customer_web_transactions.py` and friends — customer, not identity | none identity-specific |
| Tenancy | `tenant_id NOT NULL` + RLS on every table | **none** — 564 tables, no tenant discriminator | `organization_id`, partial (15/372 migrations), no RLS | **none** — single-tenant by declared design |

Sources: `dotmac_sub/app/models/party.py:158-753`,
`dotmac_sub/app/services/party.py`,
`dotmac_sub/docs/PARTY_ROLE_RELATIONSHIP_SOT.md`;
`dotmac_erp/app/models/person.py:49`,
`dotmac_erp/app/models/finance/ar/customer.py:46`;
`dotmac_crm/app/models/person.py:56,154,180,199`,
`dotmac_crm/app/models/subscriber.py:64`,
`dotmac_crm/app/models/organization_membership.py:18`;
`packages/dotmac-kernel/src/dotmac_kernel/models.py:129-289`,
`app/features/parties/service.py`.

---

## Why Sub qualifies and the others do not

### Sub — the qualifying source

Sub's identity work is not a bigger version of the starter's. It is a different
and better-founded decomposition, and its ten invariants
(`PARTY_ROLE_RELATIONSHIP_SOT.md` § "Core invariants") are the actual reusable
artefact. Four are worth naming because a naive extraction would lose them:

- **A relationship is never a grant.** `contact_for` does not let you log in;
  only an active `PartyMembership` with an explicit bounded `access_scope`
  does. CRM conflates these — `organization_memberships` *is* the access path
  and carries no scope. The starter has neither table and would otherwise
  reinvent the conflation.
- **Contact detail is not identity proof.** Shared emails and phones are
  legitimate (a reseller managing many customers), so `party_contact_points`
  has no global unique on value — only `(party, channel, normalized_value,
  scope_key)`. Both the starter (`uq_parties_tenant_lower_email`) and ERP/CRM
  (`people.email UNIQUE`) currently assume the opposite.
- **Verification and consent are separate facts** with separate columns,
  timestamps and sources. Every other repo has at most `is_verified`.
- **Social identity is scoped by provider account and immutable subject ID**,
  never by display handle.

The adoption mechanics are as reusable as the schema. Every binding Sub added
to an existing domain table is **additive, nullable, and provenance-carrying**,
guarded by an all-or-nothing CHECK — e.g.
`subscriber.party_id`/`party_bound_at` at `app/models/subscriber.py:237-256`,
same shape on `Reseller`, `SystemUser`, `SubscriberContact`, `ResellerUser`.
Nothing reads through the new link until a separate cutover slice says so. That
is the pattern the starter needs for any product adopting a party module over
existing rows, and it is already proven in production.

### ERP — requirements only

`Person` (`app/models/person.py:49`) is one 30-column table carrying name,
email with a global `UNIQUE`, phone, DOB, gender, locale, timezone, a full
postal address, marketing opt-in, a Nextcloud user ID and a batch-operation FK.
`organization_id` points at `core_org.organization` — which is ERP's **tenant**,
not a party. Customer-as-party lives separately in `ar.customer`
(`customer_type` INDIVIDUAL/COMPANY/GOVERNMENT/RELATED_PARTY, `customer_code`,
risk category, parent-customer hierarchy).

So ERP splits identity across two tables along a *finance/HR* seam rather than
a *person/organization* seam. Nothing here ports. What it does supply, and what
any starter module must not omit:

- locale, timezone, and postal address are load-bearing for a real product, and
  none of them exist on the starter's `Party` or Sub's;
- a customer needs a **stable human-facing code** (`customer_code`, unique per
  org) distinct from its UUID;
- `RELATED_PARTY` as a customer type is a real accounting requirement.

### CRM — legacy; a source of requirements and of one warning

CRM has been quiet since 2026-07-10, is declared single-tenant by its own
`CLAUDE.md`, and Sub's approved decision states plainly that *"CRM has no
runtime role in party identity or lifecycle. Legacy CRM identifiers may be
retained only as import provenance."* Sub already carries
`scripts/migration/import_crm_phase3.py`. CRM is being drained, not extracted
from.

It is still worth reading, because it is the fleet's clearest example of the
failure mode a party module exists to prevent. `Person` (213 lines) carries a
`party_status` enum that fuses four *different* facts — pipeline stage, identity
verification, commercial conversion and billing state — into one linear ladder,
so a party cannot be a churned subscriber and a fresh lead at once. Around it
sit `person_status_logs` and `person_merge_logs`, which record transitions and
merges but enforce nothing: `PersonMergeLog.source_person_id` is a bare UUID
with no FK, and nothing stops a merged person from staying active. Sub's two
`ck_parties_merged_*` CHECK constraints are the corrected version of exactly
this. And `splynx_id` — a `hybrid_property` reading a key out of a JSON blob,
falling back to `selfcare_id` — is what "external IDs are provenance" prevents.

CRM's one genuine contribution: `organizations` is a real B2B account model
(hierarchy, industry, employee count, annual revenue, account owner, commission
rate). If the starter's `PartyOrganization` ever needs to be more than
`legal_name`, this is the column list to argue from — not to copy.

---

## The `party_roles` collision — decide before extracting

`parties` and `party_roles` exist in **both** the starter kernel and Sub, with
the same names and incompatible meanings. This is already recorded in
[`migration-collisions.md`](migration-collisions.md) (lines 261–262) as
different-shape, but the semantic half matters more than the column diff:

| | starter `party_roles` | Sub `party_roles` |
|---|---|---|
| Means | an **authorization grant** — `(tenant_id, party_id, role_id)` into `roles` | a **business role** — prospect / customer / subscriber / reseller / vendor / partner / staff / agent, temporal, independently suspended |
| Columns | 3 | 11 |
| Shared | `party_id` only | |
| Owner | `dotmac_kernel.models:261` | `dotmac_sub/app/models/party.py:236` |

These are not two implementations of one contract. They are two contracts
wearing one name, and Sub's SOT explicitly separates them ("Relationships never
grant permissions… only the authorization owner may grant access"). An
extracted module cannot own both under this name.

Three ways out, and the choice is Michael's:

1. **Rename in the extracted module.** Business roles become
   `party_role_assignments` (or the module's own `mod_party` schema per ADR
   rule 14, which makes the bare-name clash moot); the kernel keeps
   `party_roles` as the RBAC grant. Cheapest for the starter, renames Sub's
   production table.
2. **Rename the RBAC grant.** Kernel `party_roles` → `party_role_grants`;
   business roles take the name Sub already uses. Truer to the vocabulary,
   touches every consumer of the kernel's RBAC.
3. **Schema-namespace only.** `mod_party.party_roles` vs `public.party_roles`.
   Satisfies the migration gate but leaves two identically-named tables meaning
   different things in one database — the exact failure mode ADR rule 14 exists
   to stop being *quiet*, not to make acceptable.

Recommendation: **(1)**, with the module in its own `mod_party` schema. The
kernel's `party_roles` is the older, more widely-consumed name and the one
whose meaning is unambiguous within the kernel's own RBAC vocabulary; Sub's
table is younger, is renamed once, and gains a name that says what it holds.

---

## The tenancy delta — the real porting cost

This is the single largest gap and it runs the *wrong way* for a straight port.

Sub's party tables have **no tenant discriminator at all** — no `tenant_id`, no
RLS, 564 tables in one flat namespace, consistent with Sub being one ISP's own
deployment. The starter's contract (AGENTS.md hard rule 11) is the opposite and
non-negotiable: `tenant_id NOT NULL` + composite uniques + RLS **in the same
migration that creates the table**.

Concretely, porting each Sub table means:

- adding `tenant_id` and RLS to `party_roles`, `party_relationships`,
  `party_memberships`, `party_contact_points`, `party_external_references`;
- re-scoping every unique constraint. Most of Sub's uniques lead with
  `party_id` and are therefore transitively tenant-safe once `parties` is
  scoped — they still need `tenant_id` added to match the starter's composite-FK
  pattern (`uq_party_roles_member` at `models.py:264` is the template), but they
  are not leaks. **One is:**
  `uq_party_external_refs_source_entity_external` (`source_system`,
  `entity_type`, `external_id`) has no party column at all, so two tenants
  importing from the same upstream system would collide on each other's IDs.
  That one is a correctness bug if carried over verbatim, not a style fix;
- deciding, per table, between a `tenant_id` column and the starter's existing
  `EXISTS`-join RLS policy used for `party_persons`/`party_organizations`. The
  join policy is right for strict 1:1 subtypes; anything queried directly by a
  guard or middleware needs its own column;
- re-expressing Sub's CHECK-constraint enums. Sub uses `String` + `CheckConstraint`
  with the values inlined in SQL (see `ck_party_roles_type`); the starter uses
  `sa.Enum(..., native_enum=False)`. Under ADR-0008 a vocabulary is a
  declaration registry, not an enum — a `PartyRoleType` fixed at 8 values in a
  CHECK constraint means a product cannot name its own role type without a
  kernel migration, which is precisely what ADR-0008 forbids.

That last point is not a mechanical conversion. **Sub's role, relationship,
membership and channel vocabularies are all closed CHECK constraints, and all
four must become open registered vocabularies before they can ship in a
reusable module.** Budget it as design work, not porting.

---

## Proposed module boundary

If this proceeds, the contract to name first — per the qualification procedure
— is:

> **`dotmac-party`** owns the answer to "who is this, what are they to us, how
> do we reach them, and how do they relate to each other" for a tenant. It owns
> no commercial, billing, network, or authorization decision.

The implemented candidate narrows that historical proposal at the identity
seam: kernel `Party` remains the fleet identity root. `dotmac-party` consumes
`party_person_catalog.v1` and owns the five business-context extensions only.
This avoids moving credentials, sessions, audit actors and every existing
Party FK merely to make the optional context installable.

**In** — the five facts Sub separated, each with one writer:

| Table | Owns |
|---|---|
| `parties` | one identity per real-world person/organization; type immutable; merge state |
| `party_role_assignments` | concurrent, temporal business roles from an **open registry** |
| `party_relationships` | directional party↔party facts; never a grant |
| `party_memberships` | a person's org context + bounded `access_scope` |
| `party_contact_points` | reachability, with verification and consent as separate facts |
| `party_external_references` | import provenance; never lookup authority |

**Out**, explicitly:

- authorization. `roles`/`party_roles` stay in the kernel; the module never
  decides access. A `PartyMembership.access_scope` is an input the
  authorization owner reads, not a grant the module issues.
- credentials and sessions. `UserCredential`/`AuthSession` stay in the kernel.
- every domain object — subscriber, lead, invoice, ticket, customer. Those bind
  *to* a party by nullable FK; they never live in the module.
- ERP's finance-customer concerns (`customer_code`, risk category, AR
  hierarchy). Those belong to whatever owns receivables.
- merge/repoint execution. Sub deliberately deferred the complete merge command
  until every target domain has a declared reconciler (invariant 4); an
  extracted module must inherit that deferral, not quietly implement it.

**Deliberately unresolved:** whether locale, timezone and postal address (ERP's
real-product columns, absent from both starter and Sub) belong on
`PartyPerson`, on a `party_addresses` table, or in tenant display settings. The
starter already resolves timezone and date format per-tenant through the
`display` setting domain, which is a different scope from a *person's* locale.
This needs a decision; it is not obvious.

---

## What must be true before code moves

Per ADR-0006 § 5 and ADR-0017, and honestly: **none of these hold today.**

1. **The `party_roles` name collision is decided.** Blocks everything; the
   recommendation above is a recommendation, not a decision.
2. **The four closed vocabularies are redesigned as ADR-0008 registries.**
   Design work, not porting.
3. **Tenancy is designed per table** — column vs `EXISTS` policy, and all six
   global uniques re-scoped. The external-reference unique is a cross-tenant
   leak if carried over as-is.
4. **A second consumer is named.** Extraction requires two consumers on the
   same contract. Sub is the source, so the second is the starter itself or a
   new product — and the starter is a template, which is a weaker consumer than
   a running deployment. If the honest answer is "there is no second consumer
   yet", the correct outcome is to record this dossier and stop, not to extract
   speculatively.
5. **Sub's cutover position is understood.** Sub's own bindings are additive
   and *not cut over* — no read path uses them yet. Extracting an identity
   module out of a product that has not itself finished adopting it inverts the
   product-first rule: the source must be the one owner before a second
   consumer arrives.

**Assessment:** gate 5 is currently the binding one, and gate 4 is close
behind. The right next slice is not extraction — it is finishing Sub's own
cutover, and using this dossier's decisions (1–3) to shape it so that what Sub
ends up owning is already extractable.
