# `audit_events` — the Group E disposition, measured

**As of:** 2026-08-12 · **Sub:** `638c7f8bb` (`origin/dev`) · **Kernel:** `0.1.0a40`
**Gates:** kernel revision `0001_initial_tenant_schema`, which creates `audit_events`
**Production measured:** `selfcare.dotmac.io`, read-only, 767,769 rows — see [Production](#production-measurement-767769-rows)
**Status:** one blocker dissolved by the data, one sharpened.

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
| `request_id` | promote to a kernel column, **nullable** | exposed filter; only 0.23% populated |
| `status_code` | promote, **`NOT NULL` viable** | exposed filter **and** order-by key; 100% populated |
| `is_success` | promote | exposed filter in two services |
| `actor_label` | promote, nullable | indexed, ILIKE-searched, stored-not-derived on purpose; 2.12% populated |
| `metadata_` | maps to the kernel's `details` | same role |
| `ip_address`, `user_agent` | **keep as columns through expansion, and dual-populate `details`** | 96.6% / 23.4% populated; see below |
| `occurred_at` **and** `created_at` | **keep both** | see below |
| `is_active` | **remove, with the `DELETE` endpoint** | zero redacted rows in production |

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

## The two hard questions

Both looked like policy decisions about the kernel's audit contract. Production
resolved the first and sized the second — see
[Production measurement](#production-measurement-767769-rows).

### 1. `is_active` — Sub's audit rows are mutable, the kernel's are not · **RESOLVED**

> **Resolved by the data: zero of 767,769 rows are redacted.** The recommendation
> is to delete the endpoint and the column rather than author a redaction policy.
> The analysis below is why it mattered, and why removal — not preservation — is
> the right answer.

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

The original requirement was an explicit redaction/suppression policy — who may
redact, what is recorded, whether the row is hidden or tombstoned. **Production
makes that unnecessary.** A policy for a capability with zero invocations is
speculative design, and carrying `is_active` forward to preserve it would import
mutable audit into the kernel for every future consumer. Remove both.

The unaudited-mutation defect goes away with the endpoint. If a redaction
capability is ever genuinely wanted, it should be designed then, against a real
requirement, and it must record itself.

### 2. Actor identity — two identifiers, no defined precedence · **STILL OPEN**

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

Production sizes it: **97.7% of rows have a non-party actor**, so
`actor_party_id` would be NULL for the overwhelming majority. This is the one
genuine blocker left in the slice, and it is a kernel-contract decision, not a
Sub measurement.

## What R1 may and may not do

Per the three-release convergence: R1 is nullable/additive columns, dual-write,
backfill reports and GUC observability — no RLS, no drops, no renames.

For this slice that means R1 **may**: add `created_at` to Sub; add the promoted
columns to the kernel's model and lineage; begin dual-populating `details` from
`ip_address`/`user_agent`.

Removing the `DELETE` endpoint and `is_active` is a **separate Sub-side change,
not part of R1** — it is a product correction that happens to unblock the
disposition, and it should land on its own evidence (zero redacted rows,
confirmed against non-production databases too) rather than riding a
convergence release.

R1 **may not**: drop or rename `ip_address`/`user_agent`; collapse `occurred_at`
into `created_at`; introduce `is_active` into the kernel contract; or pick an
actor identifier. The last two are blocked above, and a roles-or-audit R1 is
preparatory work — **it must not be described as lineage adoption**, because
revision `0001` is atomic and cannot be reached until every table it creates has
a settled disposition.

## Production measurement (767,769 rows)

Run 2026-08-12 against `selfcare.dotmac.io` (`dotmac_pg_local` / `dotmac_sub`),
read-only at the server (`default_transaction_read_only=on`), aggregates only —
no rows retrieved, because this table holds IPs, user-agents and actor labels.

### `is_active` — redaction has never been used

| `is_active` | rows |
|---|---:|
| `true` | **767,769** |
| `false` | **0** |

**Zero redacted rows in the entire table.** The `DELETE` endpoint exists, is
authorized, hides rows from the default query, records nothing about itself —
and has never once been used in production.

That dissolves the blocker rather than answering it. The disposition is no
longer "design a redaction/suppression policy so `is_active` can enter the
kernel contract". It is:

> **Delete the endpoint and the column.** Do not build a policy for a capability
> with zero production usage, and do not carry a mutable-audit semantic into a
> kernel contract to preserve a feature nobody has invoked.

Cost of removal is three call sites, all of which become no-ops when every row
is `true`: the default `is_active IS TRUE` filter in `AuditEvents.list`, the
exposed `is_active` filter parameter, and `billing_automation.py`'s two
`is_active.is_(True)` predicates. This should be confirmed against any
non-production database before the column is dropped.

### `actor_type` — 97.7% of rows have a non-party actor

| `actor_type` | rows | share |
|---|---:|---:|
| `system` | 747,431 | 97.35% |
| `user` | 17,728 | 2.31% |
| `service` | 2,452 | 0.32% |
| `api_key` | 158 | 0.02% |

Only `user` can resolve to a Party. **`actor_party_id` would be NULL for 97.7%
of production audit rows**, which settles the direction: the kernel's actor
cannot be a party reference with the others as exceptions. Either the kernel
actor is polymorphic, or `actor_party_id` is a sparse adjunct to a retained
`actor_type`/`actor_id` pair — and the pair stays authoritative.

(`actor_id` is populated on 38,186 rows, more than the 20,338 non-`system`
rows, so some `system` actors do carry an identifier.)

### `occurred_at` — the available test is negative, and cannot settle it

| total | `occurred_at > now()` | earliest | latest |
|---:|---:|---|---|
| 767,769 | **0** | 2026-01-27 | 2026-08-12 (now) |

No future-dated rows. A future timestamp would have *proved* caller-supplied
domain time; its absence proves nothing about the past direction, because Sub
has no `created_at` to compare against — a historical backfill is
indistinguishable from persistence time when only one timestamp exists.

So the code evidence stands unchanged (all three write paths accept a
caller-supplied `occurred_at`) and the production evidence is simply silent.
Both columns still survive, and **adding `created_at` is what makes the
distinction measurable at all** — today the question cannot be asked of the data.

### Population rates

| column | populated | share | consequence |
|---|---:|---:|---|
| `status_code` | 767,769 | **100%** | `NOT NULL` is viable |
| `ip_address` | 741,711 | 96.6% | substantial real forensic data |
| `user_agent` | 179,929 | 23.4% | partial |
| `actor_id` | 38,186 | 4.97% | must stay nullable |
| `actor_label` | 16,282 | 2.12% | must stay nullable |
| `request_id` | **1,788** | **0.23%** | see below |

Two readings:

- **`ip_address` at 96.6% strengthens the decision not to fold it.** That is
  741,711 rows of forensic data. Whatever reads it — and nothing in `app/` does —
  moving it into a JSON blob is a real loss, not a tidy-up.
- **`request_id` is an exposed filter over an almost-empty column.** 0.23%
  populated. The filter works and is nearly useless in practice. It still gets
  promoted (it is genuinely queryable and correlates a request across rows when
  present), but the sparsity is worth knowing before anyone treats it as a
  primary correlation key, and it suggests the write path populates it on only
  one of several routes.

## What production could not answer

Whether anything **outside** the application reads `ip_address`/`user_agent` —
exports, SIEM, incident-response queries. A database cannot report its own
external consumers. Those columns therefore stay through expansion regardless of
the population rates above.
