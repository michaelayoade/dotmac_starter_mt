# dotmac-numbering

Concurrency-safe allocation and formatting of **explicitly configured** document
series, on separate tenant and platform planes, with one immutable receipt per
allocation.

## What it owns

Given a `TenantScope` or `PlatformScope`, an open registered `series_code`, an
explicit business `reference_date` and an idempotency key: reserve the next
value of that date's period under a row lock, format it from validated
configuration, and record one append-only allocation receipt.

**One counter per `(scope, series, period)`.** A resetting series does not have
one counter, and collapsing them is unsound twice over: yearly reset reuses
value `1`, and — the subtler one — a counter already advanced into 2027 would
answer a *backdated* 2026 allocation, formatting a 2026 number that can collide
with one already issued.

**At-most-once belongs to the kernel.** Replay, fingerprint comparison and the
concurrent-key race are `dotmac_kernel.idempotency`'s (hard rule 23). The
receipt here is domain evidence, not a second replay mechanism — a hand-rolled
"look up the receipt, then allocate" lets two concurrent callers with the same
key both miss the lookup, and the loser then raises instead of replaying.

Allocation joins the caller's transaction and rolls back with it.

Configuration and repair are typed commands: a consumer never writes
`mod_numbering` tables directly, digit widths and reset/format coherence are
validated, and a repair names the period it moves and leaves immutable evidence
of who moved it and why.

## Locking, and what linearizes

**Lock order is series, then period counter — everywhere.** Configuration,
allocation and repair all take the series row `FOR UPDATE` before deciding
anything; allocation and repair then take the counter. Taking them in the other
order anywhere would deadlock against every path that takes them in this one.

Without the series lock a first allocation can read old configuration while a
concurrent `configure_series` commits new values, and the number is rendered
from a configuration that no longer exists. With it, exactly two orders are
possible and both are coherent: allocate-then-configure (history now exists, so
the reconfiguration is refused) or configure-then-allocate (the number uses the
new format).

## What may change after a series has allocated

**Identity-shaping fields freeze**: prefix, suffix, separator, digit width,
year and month inclusion, year width, reset policy. Changing one could
re-render a number already issued, so the service refuses the transition and a
database trigger refuses it again — the online roles can write the
configuration tables directly, so a rule living only in Python is one a psql
session walks straight past.

**`start_value` stays mutable, because it is a prospective seed and not part of
the rendered identity.** It seeds the counter of a period when that period
first opens, and it can never move a counter that already exists. So raising it:

- leaves an already-open period exactly where it is;
- seeds a period that has not opened yet;
- changes nothing at all for a non-resetting series, whose single `*` counter is
  always already open.

## What it does NOT own

What a number *means*, which documents require one, legal gaplessness policy,
fiscal periods, invoice issuance, document rendering, or any product's series
vocabulary. Those belong to the owner issuing the document.

## Five refusals, and the defect behind each

The sources are ERP and Sub; the audit is
[`numbering-sources.md`](../../docs/inventories/numbering-sources.md) with its
[2026-08-15 revalidation](../../docs/inventories/numbering-source-variance.md).
Each refusal below is a defect one of them still has.

| This module refuses to… | Because the source… |
|---|---|
| read a clock | ERP allocates against `date.today()` on 16 of its caller sites, so a backdated invoice takes today's period |
| invent a series | ERP auto-creates on first use with a guessed `DOC-` prefix, so a typo becomes a live series |
| rewind a counter | ERP's `should_reset` compares period *inequality*, so a backdated allocation restarts the sequence and reissues numbers |
| rewrite history | ERP's reset/update path rewrites counters with no allocation evidence. Receipts and repair evidence here are append-only by grant *and* trigger, so even the owning role cannot rewrite them |
| format twice | ERP has three formatters; its preview hardcodes four digits and disagrees with allocation for every other width |

`series_code` is an open registered string. ERP's `SequenceType` is a 27-member
PostgreSQL enum, so a new document kind is a migration in a shared module —
which ADR-0008 forbids.

## Planes

Both are declared, never inferred (ADR-0023). Tenant tables carry
`tenant_id NOT NULL` with FORCEd RLS; platform tables carry no tenant column and
are `REVOKE`d from the tenant app role, which is the isolation there. No foreign
key crosses the planes.

## Correctness evidence

Neither source contributes a single real-database test: ERP's are `MagicMock`
and Sub's run on SQLite, where `with_for_update()` is a no-op — which is how an
identical locking shape has gone unproven for years. This module's evidence is
therefore entirely new and lives in `tests/test_numbering_isolation.py`, on real
migrated PostgreSQL. Each race test carries a sensitivity proof (ADR-0018): a
companion that removes the guard and asserts the race test then fails.
