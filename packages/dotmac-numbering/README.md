# dotmac-numbering

Concurrency-safe allocation and formatting of **explicitly configured** document
series, on separate tenant and platform planes, with one immutable receipt per
allocation.

`0.1.0a4` is an unpublished replacement-contract candidate. The retained a3
release tag carries its own source/artifact evidence; this candidate does not
reissue it. Hosted CI, publication/install-back verification, and consumer
acceptance remain separate gates. Kernel floor remains `>=0.1.0a66`.
See [the changelog](CHANGELOG.md) and the
[source audit](../../docs/inventories/numbering-replacement-sources-2026-10-28.md).

## Replacement contracts (a4 candidate)

The existing allocator is extended, not replaced. `create_series` creates an
identity frozen before first issuance, including its default start value and
collision domain. `enroll_series` freezes an existing series only if its
configuration still equals the caller's exact observed configuration under
lock; it changes neither its rendering nor counters. A new format requires a
new code. A code is not printed unless the configured formatter includes it,
so a different code alone does not prevent a collision.

`CollisionDomain(code, partitioned=False)` defines exact-string uniqueness
across replacement codes. A partitioned domain requires a stable partition
identity; an unpartitioned domain uses no caller partition. Domains are
immutable within a scope, so two series cannot give the same domain conflicting
partition policies. The implementation uses PostgreSQL `C` collation with no
normalization. A product must verify that this equals its real document
constraint's semantics before enrollment; different collation/normalization
semantics are not silently treated as covered.

For an enrolled series, `allocate` requires a stable `document_reference` and,
for partitioned domains, `partition_key`. It inserts the exact rendered claim
and the allocation receipt in the same caller transaction. Claims uniquely
bind domain/partition/string and domain/partition/document identity, across
series codes. `NumberCollision` refuses another claim without skipping values,
rewinding counters, or consuming a committed number. Kernel idempotency remains
the sole replay owner, including concurrent same-key conflict handling.

`reserve_number` enrolls a `historical` or `external` exact string, stable
`document_reference` and nonempty `provenance`, without allocating or creating
a normal allocation receipt. Unknown numeric grammar can still reserve its
exact string. An identical durable claim may be reconciled under another
command key; changing its number/document/provenance is refused. Neither
reservations nor series enrollment attest census completeness: products must
inventory all past numbers and future external writers before switching.

`read_series` returns an immutable `SeriesDefinition`. `read_period_state`
returns the period's next counter value, whether a counter exists, and its last
actual allocation separately. Repairs can move the counter beyond the last
receipt; counter-minus-one must not be displayed as last number used. Reads
suppress autoflush and return stored scalar state, not a pending ORM edit.

Unenrolled legacy series retain their existing configuration/allocation
behavior and original request fingerprints. Old exact replays remain valid
after enrollment; a new enrolled allocation cannot omit document identity.
The product still needs outer business-command replay that resolves its active
binding only on first execution. A module allocation key alone cannot replay
a complete document command across a changed active series.

`nu_0002_replacement_domains` adds the selected tenant/platform domain and
claim tables plus strict-series metadata, forced tenant RLS, platform isolation,
append-only enforcement and same-plane identity links. Released `nu_0001`
bytes are unchanged. Downgrade must refuse destruction of immutable claims or
strict enrollment state; rolling an active consumer back is a forward binding
transition preserving issued evidence, not a schema rewind.

This package does not decide active business-series bindings, business dates,
configuration-change permissions, document column limits, gap policy or the
operational cutover seal. Products derive domain/partition identity from trusted
business policy, never a client-selected namespace. No example or source
change authorizes production enrollment, counter repair or a writer switch.

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

## Legacy unenrolled configuration behavior

**For legacy unenrolled series, identity-shaping fields freeze after history**: prefix, suffix, separator, digit width,
year and month inclusion, year width, reset policy. Changing one could
re-render a number already issued, so the service refuses the transition and a
database trigger refuses it again — the online roles can write the
configuration tables directly, so a rule living only in Python is one a psql
session walks straight past.

**For legacy unenrolled series, `start_value` stays mutable because it is a
prospective seed, not part of the rendered identity.** It seeds the counter of a period when that period
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
