# ADR-0031: An authority cutover is sealed by its own evidence

- Status: Accepted
- Date: 2026-08-15
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0017 (adoption is the scarce
  resource), ADR-0018 (an exemption states an enforceable premise), ADR-0024
  (apps compose by synchronizing data), ADR-0026 (approvals decide approval,
  never the transition)

## Context

Moving authority for a mutable resource from an existing writer to a new one is
the highest-risk change this fleet makes. Two writers for the same state is the
failure it creates, and it is not self-announcing: both writers keep working,
and the divergence is discovered later, in data.

The usual shape of a cutover is: run a comparison, read the report, decide, then
perform the switch. **That shape is unsound, and the reason is timing rather
than rigour.** A report describes the database at the moment it was taken. The
switch happens later. Between them the legacy writer is still authoritative and
still accepting writes, so the state the report authorized is not necessarily
the state the switch acts on. A comparison run to five decimal places the day
before proves nothing about the row inserted an hour after it.

The Vendor CP approvals cutover (`docs/adr/0004-approvals-authority-cutover.md`
in that repository) worked this problem through and arrived at a protocol. This
ADR records the protocol as a fleet standard. It does **not** extract an
implementation.

## Decision

### 1. Evidence is gathered inside the transaction that performs the cutover

**A previously produced report cannot authorize a cutover.** The observation,
the verification, and the authority switch occur in ONE transaction, in that
order, and any failure rolls back all of it.

This is the whole decision; everything below is what makes it true rather than
merely stated.

A separate earlier run is still useful — as a *rehearsal*, to discover that the
cutover would fail before attempting it, and as a *decision input* about whether
to attempt one at all. It has no authorizing power. A cutover that consumes a
stored report is a cutover authorized by a photograph.

### 2. The legacy tables are locked against writers for the duration

`LOCK TABLE <legacy tables> IN SHARE MODE` before observing. SHARE MODE permits
concurrent readers and blocks writers, which is exactly the asymmetry wanted:
nothing that reads is disturbed, and nothing can change the state between the
observation and the switch that the observation authorizes.

Without the lock, § 1's single transaction narrows the window without closing
it. The lock is what makes the observation and the switch describe the same
state rather than two states that were probably equal.

### 3. Digests are full-column, typed, and domain-separated

The evidence is a digest per table, over an envelope of:

```
[domain, encoding_version, table, field_names, sorted_typed_rows]
```

with each value **type-tagged**. Every requirement here was a defect first:

- **Full-column, not a row count or a sampled subset.** A count proves cardinality
  and nothing about content.
- **Sorted**, so the digest is a function of the data rather than of the plan the
  planner happened to choose.
- **Type-tagged**, so `1`, `"1"`, and `1.0` are distinguishable. Untyped
  serialization makes distinct states hash identically.
- **Domain-separated** — the table name, the field names and the encoding version
  live INSIDE the hashed envelope, not merely alongside it. When they sit
  outside, the empty state of every table hashes to the same value, and "these
  two empty things match" is reported as a real comparison. This was a live
  defect, caught in review.
- **An explicit `encoding_version`**, so a change to the encoding is a visible
  change rather than a silent reinterpretation of stored evidence.

### 4. Privileges are verified as effective, never as issued

Issuing a `GRANT` or `REVOKE` is not proof of the resulting privilege: a grant
reaching the role through `PUBLIC` or through an inherited role survives a
targeted revoke. So the cutover asserts the **outcome** —
`has_table_privilege(...) OR has_any_column_privilege(...)`, the column-level
half included, because a column-scoped grant is invisible to a table-level
inquiry.

Both directions are asserted. The negative half alone passes for an over-broad
revoke that leaves the new authority unable to read what it now owns.

Where a privilege tuple is compared positionally, it is imported from its owner
rather than re-declared locally. A locally re-typed tuple with two positions
transposed reported "revoked" for a privilege that was granted — also a live
defect, also caught in review.

### 5. The protocol is fleet governance; the implementation is not extracted

**Adopt this protocol wherever authority moves.** Do **not** extract a shared
implementation yet.

Vendor CP keeps its implementation local until a **second real cutover** needs
it, at which point both consumers migrate together in that change. One cutover
is a single instance, and generalising from it would violate ADR-0006's
product-first extraction rule — which requires a qualifying production
implementation and its parity tests, not a plausible abstraction.

Recording the rule now and the mechanism later is the same disposition taken in
ADR-0028 § 5, for the same reason.

## Consequences

- A cutover cannot be authorized by a report produced in an earlier session,
  an earlier deploy, or an earlier transaction. Reviewers should treat "the
  comparison passed last night" as a rehearsal result and nothing more.
- Cutovers require a maintenance posture: SHARE MODE blocks writers for the
  duration, so the transaction must be short and the window chosen.
- The evidence is reproducible. A digest over the same data yields the same
  bytes, which is what allows a rehearsal and a sealing run to be compared at
  all.
- A second cutover triggers the extraction conversation. It does not
  automatically win it — two instances are the minimum for extraction under
  ADR-0006, not a mandate.

## Alternatives rejected

**Compare, then cut over in a later transaction.** The window between them is
exactly where the legacy writer is still authoritative. This is the shape the
ADR exists to reject.

**Trust the report if it is recent enough.** Recency is not a bound on
correctness; it is a bound on how surprised you will be. A five-minute-old
report authorizes a state that no longer exists just as confidently as a
five-day-old one.

**Extract the sealing mechanism into the kernel now.** One production instance.
ADR-0006 forbids it, and a shared implementation shaped by a single adopter
becomes a constraint every later adopter inherits without having agreed to it.

**Compare row counts or a sampled subset.** Cheaper, and it answers a different
question. Cardinality equality is consistent with every row differing.

## Amendment, 2026-08-15 — the reference implementation retires; the protocol does not

§ 5 above says Vendor CP "keeps its implementation local until a second real
cutover needs it". That is no longer true, and this amendment records why rather
than editing the section, because the reasoning that produced § 5 was sound and
only its premise changed.

**What happened.** Vendor CP's approvals estate was measured and found ABSENT —
no Compose `db` service, no data volume, on the designated sole production
target. With no data to seal, compare or migrate, Vendor took a **greenfield
authority switch** instead of a sealed cutover: the legacy tables were verified
empty under `ACCESS EXCLUSIVE` and dropped in one transaction, and
`dotmac-approvals` became the authority.

**So the implementation and the inventory retire.** `approvals_cutover.py` and
the legacy inventory both query `approval_policies` and `approval_records`.
The switch drops those tables and removes their consumers, so both artifacts
reference a schema that no longer exists in any database the product can
produce. Retaining them would preserve code that cannot run — the appearance of
a reference implementation rather than one.

**`c3a0d1b` is preserved as immutable historical reference evidence.** Both
artifacts remain readable there, in full, with their tests and review history.
Retirement removes them from the working tree, not from the record.

**What a later cutover does.** It implements **locally**, from this protocol and
from that product's own current inventory — not by resurrecting Vendor's code,
which was shaped by a schema and a set of consumers that no longer exist.

**And the extraction bar is unchanged, which is the point most easily lost
here.** Shared code requires **two CURRENT consumers**. That Vendor once had an
implementation is not one of them: a retired artifact cannot be a party to a
contract, and counting it would let a single live adopter shape a shared
mechanism by pointing at history. The rule in § 5 was always "extract when reuse
is proven"; this amendment states that proof is measured against what exists,
not against what once did.

### A correction to the record

An earlier account credited Vendor CP's merged inventory tool with producing the
`TARGET_ABSENT` observation. **It did not — that tool never ran.** Its
contribution was to enforce the evidence boundary: presented with no mechanism
to run against the target, it refused to report absence, on the grounds that
"the tool cannot run" and "the database is absent" are different facts and only
the second licenses a greenfield path.

The decisive observation came from a **direct, authorized Docker-boundary check**
against the named target, which contacted the host, opened no database
connection, read no credential and changed nothing.

The distinction is the substance of the episode, not a footnote. The greenfield
path is valid because someone looked — not because looking proved difficult.
