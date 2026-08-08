# Adopting the kernel's settings in an existing product

A product that already has a working settings subsystem does not switch to the
kernel's in one commit. ADR-0003 requires adapters, shadow tests,
expand/contract migrations and a one-writer cutover, and this is what that means
for settings specifically.

The kernel supplies the harness (`dotmac_kernel.settings_shadow`). It cannot
supply the migration, because your columns are yours.

---

## Before anything: decide what is a setting

ADR-0009: **a secret is held, never dereferenced.** If any of your settings
store a reference to a secret store as their VALUE (`bao://...`, an ARN, a
vault path), each one has to become one of two things before the cutover:

- **a real setting** — the value stored, encrypted at rest via
  `settings_crypto`; or
- **not a setting** — installed at boot through
  `dotmac_kernel.secret_sources.SecretSource`, and read with `get_secret`.

A key that protects data in the same database MUST take the second path.
Encrypting it at rest with another key that lives beside it achieves nothing.

Do this first. It changes which rows exist, and everything below compares rows.

---

## 1. Expand

Add the kernel's schema alongside your own. Nothing reads it yet.

- Run the kernel's settings migrations; keep your table.
- Declare your domains on your modules' manifests (`setting_domains=(...)`).
- Declare your specs — **in the module that reads them**, typed:
  `SettingSpec[int]`, not `SettingSpec`. A spec's reader must be able to import
  it, which is what makes `resolve(db, SPEC) -> int` possible at the call site.
- Backfill your rows into the kernel's table.

Your product still resolves everything its own way. Nothing has changed for a
request.

## 2. Shadow

Both resolvers run. Yours is served.

```python
from dotmac_kernel.settings_shadow import ShadowPhase, resolve_shadowed

value = resolve_shadowed(
    db, MAX_PER_ENTITY, lambda: legacy_resolve("custom_fields", "max_per_entity"),
    phase=ShadowPhase.LEGACY_AUTHORITATIVE,
)
```

Divergences are logged, never raised, and never contain a value. A legacy
reader that raises is recorded as a divergence rather than becoming a 500 —
during a shadow phase an adapter bug and a real disagreement are both things
you want to see, and neither is worth an incident.

## 3. Verify

`sweep` compares every registered spec; `sweep_scopes` compares across several.

```python
report = sweep_scopes(db, legacy_reader, [
    SettingScope.platform(),
    *(SettingScope.tenant(t.id) for t in tenants),
])
assert report.clean, report.describe()
```

**Platform-only agreement proves nothing.** Precedence differences between two
resolvers show up on tenant overrides, and a sweep that only checks the platform
scope will report clean right up until the cutover. There is a test pinning
exactly this trap.

Verify against **real data**, not fixtures. `clean` once on a seeded database
means the two agree about seed data.

Expect these, and treat them as findings rather than noise:

| Divergence | Usually means |
|---|---|
| `kernel=int legacy=str` | your resolver never coerced; the kernel does |
| `kernel=str legacy=NoneType` | a key the kernel has a spec default for and you did not |
| `@tenant` only | a precedence difference — the important one |
| `comparison failed` | your adapter is wrong, not your data |

## 4. Cut over

Flip to `KERNEL_AUTHORITATIVE`. The kernel's value is served; **yours still
runs and is still compared**, so a regression is visible immediately and the
phase steps back without a deploy.

Make the kernel the ONE WRITER at the same time: every write goes through
`upsert_by_key`/`ensure_by_key`/`clear_by_key`. Two writers with one reader is
the state that generates the drift this whole exercise exists to prevent.

Stay here long enough to cover your slowest cycle — a monthly job reads settings
a monthly job's worth of times.

## 5. Contract

Only now: `KERNEL_ONLY`, then delete your resolver, your table and your adapter.
Phase 3 stops calling the legacy reader at all, which is what makes deleting it
safe rather than hopeful.

---

## What tends to go wrong

**Serving before verifying.** The phases only move one way for this reason.
There is deliberately no "serve whichever is non-null" mode — that is a third
answer belonging to neither system, and it hides the disagreement instead of
surfacing it.

**Comparing only what is easy.** Platform scope, one tenant, no secrets. The
settings that diverge are the ones with overrides.

**Deleting the old path at step 4.** Phase 2 is worth as much as phase 1; it is
the only phase where you can undo a bad cutover by changing a variable.

**Treating representation as drift.** `Decimal("5")` vs `5` is not a
disagreement about the setting, and the harness does not report it. A report
that cries wolf gets ignored, which costs more than it saves.
