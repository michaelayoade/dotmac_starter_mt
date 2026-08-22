# ADR 0019 — Party identity follows the archetype, and the archetype's names

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-12
**Applies to:** every Dotmac repository that models people, organizations,
customers, staff, resellers, vendors or logins.
**Owns:** the four-way separation of Party / PartyRole / Account / Principal,
which concept each name refers to, and the rule that authentication binds to a
Party while authorization binds to a PartyRole
**Does not own:** any product's role vocabulary (ADR-0008 governs that), the
merge/deduplication policy, or the schedule on which a product adopts this

## Context

Dotmac has re-litigated the same modelling question once per table.

`user_credentials` carries three mutually-exclusive principal FKs in Sub and a
single `party_id` in the kernel. `party_roles` exists in both repositories, as a
temporal business capacity in one and an RBAC grant in the other. `subscribers`
binds to a Party directly. Each was decided on its own terms, by different
people, at different times, and each decision was locally reasonable.

The cost is visible in the adoption gate: five of the ten kernel/Sub table-name
collisions sit inside a single kernel revision, and two of them cannot be
dispositioned without first answering a modelling question that was never asked
at the level it needed to be asked
(`docs/inventories/sub-lineage-dispositions.md`).

**Party is not a Dotmac invention.** It is an established analysis pattern —
Fowler's *Analysis Patterns*, Arlow & Neustadt's *Enterprise Patterns and MDA*,
and, in the dialect that matters most for an ISP, the TM Forum SID. The pattern
already answers most of what has been re-argued here, and its answers are
load-bearing rather than stylistic.

The strategic value of adopting an established model is that the boundaries do
not have to be discovered. But they do have to be adopted **before** they are
packaged: a shared distribution freezes whatever boundaries it ships with, and
every consumer then pins them.

## Decision

### 1. Four concepts, four names, four owners

| Concept | Answers | Is not |
|---|---|---|
| **Party** | who exists — a person or an organization | a customer, a login, or an account |
| **PartyRole** | in what capacity a party relates to us — customer, subscriber, reseller, vendor, partner, staff, agent. Concurrent and temporal. | a permission set |
| **Account** | what commercial or service relationship exists — subscriber account, billing account | an identity |
| **Principal** | how someone proves they are a party — credential, session, API key, MFA method | an authorization decision |

A model that fuses any two of these is non-conforming and must record a
migration path, not an exception.

### 2. Authenticate the Party. Authorize the PartyRole.

A credential proves **which party**. It never encodes what that party may do.
Access follows from the party's roles, which are concurrent, temporal and
independently suspended.

Two consequences that were previously argued as structural questions become
policy questions, which is where they belong:

- whether privileged access needs its own credential is a **policy on the staff
  role**, changeable without a migration;
- whether one login reaches several accounts is answered by the cardinality
  already in the model — one party, many accounts — not by adding a
  discriminator to the credential.

A credential row may still be repeated per **authentication mechanism**
(`provider`: local, SSO, RADIUS), because mechanism, password hash, lockout and
rotation state are properties of a credential, not of a party. It is never
repeated per *principal kind*.

### 3. Two parties for one human is dedup debt, not a modelling error

Party identity is asserted from evidence, and merging is a separate, evidence-
gated operation. A staff party and a customer party that happen to be the same
human are a **deduplication backlog item**, not a violation. Shared email,
phone, address or handle never suffice to merge; ambiguous records quarantine.

This is what makes adoption incremental: a product may bind its principals to
parties long before it has deduplicated them.

### 4. `party_roles` means PartyRole. The RBAC grant is renamed.

The kernel's `party_roles` — `(tenant_id, party_id, role_id)` into a `roles`
table of permission bundles — is an **RBAC grant**, not a PartyRole. Sub's
`party_roles` — typed, temporal, with status and validity window — is the
archetype's PartyRole, and Sub separately and correctly keeps its authorization
tables in `app/models/rbac.py`.

Sub holds the correct semantics; the kernel holds the correct name for the wrong
table.

**Ruling: the kernel's table and model are renamed to `party_role_grants` /
`PartyRoleGrant`.** The name `party_roles` is reserved fleet-wide for the
archetype's PartyRole.

This was decided the other way in
`docs/inventories/party-module-sources.md` (2026-08-11), which proposed renaming
Sub's table instead. That recommendation is **superseded**: it optimised for the
kernel's blast radius at the cost of propagating a misnomer into every product
that adopts the kernel from here. Three consumers pin the kernel today. That
number only grows, and so does the cost of the rename.

### 5. Capacity is a row, never a column — and the multi-capacity case is the conformance test

*Added by amendment, 2026-08-12.*

A single Lagos company can, at the same moment, buy connectivity from Dotmac
(**subscriber**), resell Dotmac service to its own customers (**reseller**), and
supply Dotmac with fibre and installation labour (**vendor**). One legal entity,
one Party, three concurrent capacities. This is ordinary in an ISP, not an edge
case.

It is therefore **the conformance test for this ADR**, and a conforming product
must be able to represent it without a workaround:

> One Party holds `subscriber`, `reseller` and `vendor` capacities concurrently,
> each independently suspendable, each with its own validity window; and it
> carries three Accounts of three different kinds — a **receivable** (they owe
> us, as subscriber), a **payable** (we owe them, as vendor), and a
> **settlement/commission** balance (as reseller).

Two rules fall out of it.

**5a. A capacity is a row, never a column.** A single-valued type or status
column on an identity table cannot express concurrency, and every such column in
the fleet is the same defect:

| repo | column | why it fails |
|---|---|---|
| Sub | `Organization.account_type` | forces one capacity; already flagged for retirement in the SOT sequence |
| CRM | `Person.party_status` | a linear ladder (lead→contact→customer→subscriber) — cannot be a churned subscriber and a fresh lead |
| CRM | `Organization.account_type` | as Sub's |
| ERP | `Customer.customer_type` | conflates legal form (individual/company) with commercial capacity |

`Party.party_type` (person | organization) is **not** one of these and stays: it
is the archetype's subtype discriminator — what kind of thing this is — not what
capacity it holds toward us.

**5b. An Account attaches to the PartyRole, not to the Party.** With accounts on
the Party, "what does Acme owe us as a subscriber" is not expressible, and
whether to net it against what we owe them as a vendor cannot even be asked.
Netting is a real finance question that only exists if the roles are distinct,
and answering it is a policy decision that requires per-role accounts to have
been modelled first.

This reprioritises the account-to-role rebinding: it was sequenced last on
data-risk grounds, and it is the slice that unlocks a question the business will
actually ask.

**5c. Identity does not multiply with relationships.** The multi-capacity case
settles two questions that were open when this ADR was drafted:

- **One login per person, not per account.** An account is a relationship, not
  an identity. Acme's administrator would otherwise need one credential per
  account and more as the relationships grow.
- **A person with memberships in two resellers keeps one credential.** One
  Party, one credential per authentication mechanism, two `party_memberships`,
  and a switcher in the portal. The credential's mechanism key must **not**
  include the membership — that reintroduces login-per-relationship.

Both follow from the one principle, which is the evidence it is a principle
rather than two judgement calls. The migration consequence is a many→one
credential merge, which is harder than a split and requires production backfill
under the existing approval controls; it is not authorised here.

**5d. Recorded deviation — relationships link parties, not roles.** Sub's
`PartyRelationship` connects `subject_party_id` → `object_party_id`. That is
Fowler's party-to-party Accountability; Arlow & Neustadt and TM Forum SID connect
**PartyRole to PartyRole**, which is stronger precisely when a party holds
several concurrent capacities — "Jane is `account_manager_for` Acme" cannot
distinguish Acme-as-reseller from Acme-as-vendor, which plausibly have different
account managers. Sub's `relationship_key` is the escape hatch and it works, but
it is an unstructured string doing a structural job. Recorded as a known
deviation; not scheduled, and to be resolved before any extraction.

### 6. No shared party module before this holds

No `dotmac-party` distribution, and no extension of the kernel's identity
surface beyond this rename, until §1–§2 hold in at least one product. Packaging
the current boundaries would freeze authentication inside identity and accounts
on the wrong end of a role, in a distribution three products pin.

## Consequences

**Immediate and unblocked.** The §4 rename needs no production data, no
backfill and no approval. It is a kernel-local change plus its consumers.

**Breaking for kernel consumers.** `dotmac_kernel.models.PartyRole` becomes
`PartyRoleGrant` with no alias. An alias would preserve exactly the ambiguity
this ADR removes. ERP, Sub and the vendor control plane adopt it on their own
schedule when they next move their pin; the kernel is `0.x` and
`COMPATIBILITY.md` permits it.

**Gated.** §2/§5c's credential convergence and §5b's account-to-role rebinding
both require production backfill under the existing approval controls, and both
sit behind the kernel-lineage gate. This ADR fixes the target; it does not
authorise the migration.

**Reprioritised by §5b.** Account-to-role rebinding was sequenced last on
data-risk grounds. The multi-capacity case makes it the slice that unlocks a
question the business will ask — per-role balances, and whether they net.

**On the lineage gate.** Converging `party_roles` and `user_credentials` on the
archetype rather than on a negotiated column union makes the kernel `0001`/`0003`
dispositions easier to write and to review, because both sides are then moving
toward a named external model rather than toward each other.

**On ADR-0008.** The role vocabulary stays an open registered vocabulary. This
ADR fixes what a PartyRole *is*; it does not fix the list of roles, and no
product needs a kernel change to name its own.

## Alternatives rejected

**Rename Sub's business-role table instead.** Cheaper today, wrong direction:
it teaches every future product that `party_roles` means an ACL row.

**Keep both names and disambiguate by schema** (`mod_party.party_roles` vs
`public.party_roles`). Satisfies the migration gate and leaves two identically
named tables meaning different things in one database — the failure mode hard
rule 14 exists to stop being quiet, not to make acceptable.

**Ship a compatibility alias for `PartyRole`.** Keeps the ambiguity alive in
consumer code indefinitely, which is the whole cost being paid down.

## Enforcement

- `tests/architecture/test_party_archetype_names.py` — the kernel declares no
  table or model named `party_roles`/`PartyRole`, so the name cannot be
  reintroduced for the grant concept.
- `tests/architecture/test_capacity_is_a_row.py` — §5a, enforced by **value**
  rather than by column name: no column on `parties` or its subtype tables may
  enumerate a commercial capacity, whether through a SQLAlchemy `Enum` or a
  CHECK constraint. Per ADR-0018 the detector carries a sensitivity proof — a
  synthetic offending table it must flag — so a detector that has quietly
  stopped detecting fails rather than passes.
  The remaining fleet columns in §5a's table live in Sub, CRM and ERP and are
  out of this repository's reach; they are named there so the rule is
  enforceable here and citable there.
- Kernel migration `0022_party_role_grants` performs the rename and carries the
  table, constraint, index and RLS-policy renames together.
- `docs/ARCHITECTURE.md`'s provenance table records the new owner and name.
