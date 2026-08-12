# `audit_events` — the Group E disposition, measured

**As of:** 2026-08-12 · **Sub:** `638c7f8bb` (`origin/dev`) · **Kernel:** `0.1.0a40`
**Gates:** kernel revision `0001_initial_tenant_schema`, which creates `audit_events`
**Status:** partly settled. **Two questions block the slice** and are policy, not measurement.

[`sub-lineage-dispositions.md`](sub-lineage-dispositions.md) put `audit_events`
in Group E and refused to plan it mechanically:

> `audit_events` carries request-forensic columns the kernel folds into a
> `details` JSON. Flattening Sub's `ip_address`/`user_agent`/`request_id`/
> `status_code` into `details` loses queryability that Sub's audit surface may
> depend on; promoting them into the kernel is the safer direction and **should
> be measured against real audit queries first.**

This is that measurement, plus what it does *not* settle.

## The two tables

Kernel `dotmac_kernel/audit.py`: `id`, `tenant_id`, `actor_party_id`, `action`,
`entity_type`, `entity_id`, `details` (JSON), `created_at`. **No `is_active`. No
`occurred_at`.** The model is immutable by design — there is one writer,
`write_audit_event`, and nothing mutates a row.

Sub `app/models/audit.py`: 15 columns, including `occurred_at`, `actor_type`,
`actor_id`, `actor_label`, `status_code`, `is_success`, `ip_address`,
`user_agent`, `request_id`, `metadata_`, `is_active`. **No `created_at`.**

## What the query surface actually uses

Sub's read surface is `AuditEvents.list` in `app/services/audit.py`. Every one of
these becomes a SQL `WHERE`:

`actor_id`, `actor_search` (→ `actor_label ILIKE` **or** exact `actor_id`),
`actor_type`, `action`, `entity_type`, `entity_id`, `request_id`, `is_success`,
`status_code`, `is_active`. `status_code` and `occurred_at` are also members of
the order-by map; `occurred_at` is the default sort.

Second and third readers: `web_customer_user_access.py` filters
`is_success.is_(True)` twice; `billing_automation.py` filters
`is_active.is_(True)` twice.

Reads of `ip_address` / `user_agent`: **none in `app/`.** They appear only at the
write site (`audit.py` 169–170 capturing from the request, 217–218 persisting).

## Settled

| Sub column | Disposition | Evidence |
|---|---|---|
| `request_id` | promote to a kernel column | exposed filter |
| `status_code` | promote | exposed filter **and** order-by key |
| `is_success` | promote | exposed filter in two services |
| `actor_label` | promote | indexed, ILIKE-searched, stored-not-derived on purpose |
| `metadata_` | maps to the kernel's `details` | same role |
| `ip_address`, `user_agent` | **keep as columns through expansion, and dual-populate `details`** | see below |
| `occurred_at` **and** `created_at` | **keep both** | see below |

### `ip_address` / `user_agent` are kept, not folded

My first reading was "zero reads, safe to fold into `details`". That is too
strong, and the correction is the important part: **zero checked-in reads is not
zero consumers.** Direct SQL, scheduled exports, a SIEM pipeline and
incident-response queries all read a database without leaving a reference in
`app/`. Folding them during expansion would be an irreversible loss discovered
by an incident, which is the worst time to discover it.

They stay columns through R1 and are dual-populated into `details`. Retiring the
columns is a later, separate decision that needs consumer evidence from outside
this repository — not the absence of evidence inside it.

### `occurred_at` and `created_at` are not aliases

`occurred_at` is **domain event time and is caller-supplyable**: all three write
paths in `app/services/audit.py` (lines 34–35, 51–52, 74–75) pop it from the
payload *only when the caller left it `None`*, falling back to the model
default. A caller that supplies a historical or scheduled timestamp gets it.

The kernel's `created_at` is persistence time. Sub has no such column today.
Collapsing one onto the other destroys the distinction in whichever direction it
is done. Both survive: Sub gains `created_at` (additive, server-defaulted), the
kernel gains `occurred_at`.

## Unsettled — these block the slice

Both are policy decisions about the kernel's audit contract. Neither is
resolvable by measuring Sub.

### 1. `is_active` — Sub's audit rows are mutable, the kernel's are not

`DELETE /audit-events/{event_id}` → `AuditEvents.delete` →
`event.is_active = False; db.commit()`. The route is authorized
(`require_audit_auth` at router level, so this is not an access-control hole),
and the default list query filters `is_active IS TRUE` whenever the caller does
not ask otherwise.

So an authorized actor can hide an audit event from the audit surface, and:

> **the redaction writes no record of itself.** `AuditEvents.delete` mutates the
> row and commits. No audit event is written for the deletion, so the audit
> trail cannot show that an audit entry was hidden, by whom, or why.

That is a product defect worth fixing regardless of extraction, and it is
exactly why `is_active` must not be promoted into the generic kernel contract
yet. Promoting it would import "audit rows are mutable and hideable" into a
model that is deliberately immutable, for every future consumer.

**Required first:** an explicit redaction/suppression policy — who may redact,
what is recorded when they do, whether the row is hidden or tombstoned, and
whether redaction is representable without making the base model mutable.

### 2. Actor identity — two identifiers, no defined precedence

| | Kernel | Sub |
|---|---|---|
| Shape | `actor_party_id: UUID \| None`, indexed | `actor_type` enum + `actor_id: String(120)` + `actor_label: String(160)` |
| FK | to a party | **deliberately not a foreign key** — the model comments that `actor_id` is not an FK so the row *survives deletion of the referenced actor* |

`AuditActorType` has four members: `system`, `user`, `api_key`, `service`.
**Three of the four are not parties.** So `actor_party_id` cannot simply replace
the pair, and carrying both without a rule leaves two identifiers whose
precedence is undefined — the ambiguity the source-of-truth standard exists to
prevent.

Sub's non-FK choice is also a real constraint, not an oversight: an audit row
must remain readable after its actor is deleted, which an FK to `parties` would
undermine.

**Required first:** a decision on whether the kernel's actor is polymorphic, and
if it stays `actor_party_id`, what a `system`/`api_key`/`service` actor resolves
to and which identifier wins when both are present.

## What R1 may and may not do

Per the three-release convergence: R1 is nullable/additive columns, dual-write,
backfill reports and GUC observability — no RLS, no drops, no renames.

For this slice that means R1 **may**: add `created_at` to Sub; add the promoted
columns to the kernel's model and lineage; begin dual-populating `details` from
`ip_address`/`user_agent`.

R1 **may not**: drop or rename `ip_address`/`user_agent`; collapse `occurred_at`
into `created_at`; introduce `is_active` into the kernel contract; or pick an
actor identifier. The last two are blocked above, and a roles-or-audit R1 is
preparatory work — **it must not be described as lineage adoption**, because
revision `0001` is atomic and cannot be reached until every table it creates has
a settled disposition.

## What still needs a live database

The remaining measurements need real rows, and none of them is answerable from a
disposable rehearsal database:

| Question | Why it matters |
|---|---|
| How many rows have `is_active = false`? | Is redaction actually used, or is the endpoint dead weight that can simply be removed? |
| `actor_type` distribution | How many audit rows have a non-party actor, sizing the polymorphism problem |
| Rows where `occurred_at` diverges materially from insertion time | Confirms caller-supplied domain time in production, not just in principle |
| Null/population rates on `request_id`, `status_code`, `ip_address`, `user_agent` | Whether a promoted column can be `NOT NULL`, and whether the forensic columns hold real data worth preserving |

Observe hosts the disposable rehearsal, but it has no production rows. These
counts need a read-only query against a database that has them, on a host named
explicitly.
