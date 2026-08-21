# dotmac-imports

The durable record of a bulk import: which document, which rows, what happened
to each one — and nothing about what any of it meant.

Decision: [ADR-0025](../../docs/adr/0025-imports-own-the-run-not-what-a-row-means.md).
Product-first evidence: [`imports-sources.md`](../../docs/inventories/imports-sources.md).

## What it owns

- the run lifecycle `pending → running → dry_run_ready → completed | failed`;
- one-shot promotion of a validated dry run into an apply run;
- one minimised row record per input line: `ok | error | skipped`, a row
  fingerprint, bounded safe error detail, and an opaque result;
- CSV decoding, alias-based column resolution, auto-mapping and preview;
- crash-durable resumability through one locked, caller-transaction-owned chunk
  per call; and
- a large-CSV lane with streaming bounded partition creation, immutable
  per-partition file/checksum/range descriptors, expiring atomic worker claims,
  and a partition completion checkpoint committed with its row outcomes.

## What it does not own

The importing domain owns the field vocabulary, the validation, the mutation,
and any reversal of it. `dotmac-files` owns the bytes. This module never writes
a domain row, never resolves a foreign entity, and holds no foreign key into a
domain table — if a domain needs the back-reference it stores the run and row
ids on its own row.

## The two ports

```python
class RowValidator(Protocol):
    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]: ...

class RowApplier(Protocol):
    def apply(self, row: Mapping[str, str]) -> Mapping[str, object]: ...
```

`validate_next_chunk(...)` takes the validator. It has **no applier parameter**,
so a dry run cannot reach a mutation — there is no branch to write backwards
and no call site to guard. Both source products needed discipline (and, in
ERP's case, an AST guard whose own docstring notes it does not check polarity)
to hold the same property.

## Shape of a use

```python
columns, rows = decode(payload, source=source)       # bytes from dotmac-files
plan = preview(columns, rows, fields)                # operator confirms
run = create_dry_run(db, tenant_id=t, kind="payments",
                     source=source, mapping=plan.mapping)
db.commit()
while True:
    progress = validate_next_chunk(
        db, tenant_id=t, run_id=run.id, data=payload,
        fields=fields, validator=my_validator,
    )
    db.commit()                                      # caller owns the transaction
    if progress.is_complete:
        break

applied = promote(db, tenant_id=t, run_id=run.id, source=source)
db.commit()
while True:
    progress = apply_next_chunk(
        db, tenant_id=t, run_id=applied.id, data=payload,
        fields=fields, validator=my_validator, applier=my_applier,
    )
    db.commit()
    if progress.is_complete:
        break
```

Promotion re-checks the file identity and recorded SHA-256. Both chunk entry
points then hash the raw bytes they decode. There is no caller-supplied row
sequence at apply time, so apply cannot mutate from content unrelated to the
validated file.

## Large CSVs and parallel workers

The original lane remains deliberately simple for ordinary imports. For a
large CSV, `iter_csv_partitions(...)` first streams and verifies the original
file, then emits bounded `PartitionPayload` values. The application stores each
payload through `dotmac-files` and calls `register_partition_plan(...)` with
the returned immutable file ids, byte sizes and checksums. The module never
learns a storage path or provider.

Workers call `claim_partition(...)` in a short transaction. A conditional
`SKIP LOCKED` update returns one opaque lease; an expired lease may be replaced,
and the old token cannot settle. The worker opens only that partition and calls
either `validate_claimed_partition(...)` (which has no applier parameter) or
`apply_claimed_partition(...)`. The engine verifies byte size, SHA-256 and row
count before any domain call, then commits the bounded row effects, outcomes
and completion checkpoint together. Promotion clones the same descriptors into
the apply run, so validated partitions cannot be silently replaced.

The default partition is 200 rows and 8 MiB; both are explicit caller choices.
The package supplies claims, not a worker-count policy. Deployments can add
workers without putting scheduling or product vocabulary into this module.

## Transactions

No function here commits or rolls back. `dotmac_kernel.db` stays the one
transaction authority. A call locks the run and advances at most one chunk;
the caller commits the checkpoint and invokes it again. A new worker resumes a
`running` run from that checkpoint, and re-delivery of a completed run is a
no-op.

Each applied row runs inside a `SAVEPOINT`. A domain raises `RowRejected` with
a typed, bounded, persistence-safe code and message for an expected refusal.
Every other exception escapes and rolls back the attempted chunk; raw exception
text is never copied to the ledger. Row values are not retained either — only a
canonical SHA-256 fingerprint — so `mod_imports` does not become a second store
for content owned by `dotmac-files`.

## Persistence

One tenant plane, schema `mod_imports`, lineage prefix `im`, kernel floor
`0.1.0a56` (the first published release carrying the prerequisite contract).
Assemblies compose the installed lineage through the public `versions_dir()`
locator. All three tables carry `tenant_id NOT NULL`, tenant-composite identity
and FORCEd RLS. No platform plane is declared: the audit found no control-plane
import capability anywhere in the fleet, and declaring a plane no product uses
would be speculative.

## Not in this release

XLSX/XLS decoding. `SourceLayout.XLSX` is declarable so a run records its true
layout, but `decode` refuses it. ERP's spreadsheet readers are the source to
port, and they arrive with ERP's cutover — with the library and the parity tests
that make them provable — rather than as an untested optional extra shipped
ahead of any consumer.

Database extraction, PostgreSQL COPY and automatic partition-worker scaling are
also not in this release. The durable lane is ready for a real ERP pilot; those
optimisations require their own source evidence and measured cutover.
