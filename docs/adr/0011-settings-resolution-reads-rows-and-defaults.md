# ADR-0011: Settings resolution reads rows and defaults, nothing else

- **Status:** Accepted (2026-08-08), amended 2026-08-20 (see "Amendment").
  The behaviour has shipped since `0.1.0a19`; this ADR makes it a property
  rather than a habit. The central decision is unchanged and the ADR is not
  superseded: the environment bootstraps authoritative rows and never
  participates in runtime resolution.
- **Scope:** Fleet-wide.
- **Relates to:** ADR-0008 (declaration registries), ADR-0009 (secrets are
  held), ADR-0013 (the platform declares deployment facts — added the profile
  default this ADR's precedence block now names)

## Context

`resolve_value` once consulted three sources: the stored row, the environment,
and the spec's default — with the environment ranked between them. That put a
second authority inside the owner. A value the settings screen showed, an
operator could not change, because a variable in a unit file outranked the row
they were editing.

Michael ruled in favour of the stored row on 2026-08-08 (`dotmac_sub` reached
the rule first). The kernel was changed to match in `0.1.0a19`, published in
`0.1.0a21`, and `seed_settings_from_env` became the single consumer of
`env_var`.

**So the behaviour is not in question. What was missing is enforcement.**
Nothing prevented its reintroduction: a developer debugging a configuration
problem adds a fallback to `_finish`, every existing test still passes, and the
guarantee is gone with no failure anywhere. That is the difference ADR-0009 drew
between a policy the kernel argues for and a property it has, and this rule was
on the wrong side of it.

## Decision

**Nothing on the settings resolution path reads the environment.** Resolution
answers from stored rows, walking the declared scope chain, falling back to the
spec's `default`.

`env_var` remains on `SettingSpec` as a declaration. Its only consumer is
`seed_settings_from_env`, which runs once at startup, creates the platform row
when none exists, and never overwrites one that does.

Precedence, stated once (corrected 2026-08-20 for ADR-0013):

```
scope chain (most specific row first) -> assembly profile default -> spec fallback
```

The environment does not appear, because it is not a source. It is a loader that
produces a row, and a row is indistinguishable from any other once written.
ADR-0013 later inserted the deployment's declared answer between the rows and
the module's fallback; it did not give the environment a rank, and nothing here
depends on which of the two non-row levels answers.

## Why not a per-product knob

The rejected alternative is a setting — or a spec field — letting each product
choose whether the environment outranks a stored row.

It fails for the reason ADR-0009 gives almost verbatim: **it makes an
operational property negotiable.** Whether an operator's stored value can be
silently overridden is not a matter of taste to be configured per deployment; it
determines whether the settings screen tells the truth. A product that answered
"yes" would have an admin UI that lies under conditions no one can see from the
UI.

It also cannot be tested. "The environment does not outrank a row *unless
configured otherwise*" has no failing case, so the check degrades to asserting
that the knob is read — which is not the property anyone cares about.

## Consequences

- **A value in effect is a value someone can see.** Every resolved value comes
  from a row an operator can inspect and change, or from a default declared in
  code and visible on the settings screen.
- **Two processes agree.** Resolution depends on data, not on the environment a
  particular worker happened to start with.
- **A database restore reproduces the system.** An env-only override did not
  travel with a backup; a seeded row does.
- **`env_var` still works, at a different time.** A deployment configuring a
  setting by environment gets a real row on first start, with history and an
  owner. `validate_required_settings` runs *after* the seed for exactly this
  reason, so a required setting configured by environment counts as configured.
- **One behaviour change, already shipped and released:** a deployment relying
  on an environment variable to override a *missing* row now gets a visible,
  editable row instead. Recorded in the `0.1.0a19` changelog as breaking.

## Enforcement

- `tests/unit/test_settings_resolution_ignores_env.py` — resolution of a spec
  that *declares* an `env_var`, with that variable set to a distinct value and
  `os.environ.get` / `os.getenv` patched to raise. Covers the single-key and
  bulk paths, asserts a stored row wins, asserts the **spec default** wins over
  the environment, and asserts the environment reaches a value only by seeding.
  Includes a sensitivity proof that the patch fires.
- `tests/architecture/test_settings_env_is_bootstrap_only.py` — no function in
  the resolver touches the environment except `seed_settings_from_env`, which is
  a named, single-entry allowlist. It also asserts the bootstrap *still* reads
  it: an allowlist that stops describing reality would mean `env_var` had
  quietly become inert and every spec declaring one was lying. Includes a
  sensitivity proof against a planted read.

Neither is sufficient alone, for the reason ADR-0009 gives: the runtime proof
catches any spelling but only on paths a test drives; the static check covers
every path but only the spellings it knows.

## Migration

None. No consumer is affected: the behaviour shipped in `0.1.0a19` and no
product had adopted kernel settings at that point — `dotmac_erp` pins `a13` and
`dotmac_sub` imports none. This ADR adds documentation and two tests.

## Amendment — 2026-08-20

Four corrections. The central decision is unchanged; each of these is either a
statement this ADR made that has since stopped being true, or a gap between
what it claims and what actually holds it up.

### 1. The precedence block was stale

ADR-0013 inserted the assembly profile default between the scope chain and the
module's fallback. The block above now reads:

```
scope chain -> assembly profile default -> spec fallback
```

`docs/ARCHITECTURE.md` carried the same stale order and is corrected in the same
change. `source` gained a `"profile"` value with ADR-0013 and the architecture
document had never listed it.

**An invalid stored row degrades to the SPEC fallback, not the profile
default.** `_finish` takes `spec.default` directly when
coercion/`allowed`/range/`validator` rejects a row
(`packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py`), and reports
`source="default"`. This is recorded here as current behaviour, deliberately
and without blessing it: ADR-0013 says a profile default "loses to every stored
row and wins over the module's fallback", and a row that fails its spec is
arguably not a stored row at all — so the deployment's declared answer is
bypassed in precisely the case it was declared for. **This amendment does not
settle it.** Whoever changes it owns a third decision and should record it here.

### 2. The static guard was narrower than the rule it claims to hold

`tests/architecture/test_settings_env_is_bootstrap_only.py` is the only thing
covering paths no test drives, and three specific evasions get past it:

- **One file.** It scans `settings_resolver.py` alone. A helper in any other
  module — `settings_cache`, `setting_scopes`, an assembly-side reader — is
  invisible to it, and delegation is the obvious shape a reintroduced env read
  would take.
- **Substring matching on source segments.** It looks for `"environ"` /
  `"getenv"` in each function's source text, so `from os import environ as _e`
  at module scope followed by `_e["X"]` inside the function matches nothing.
  Mapping access through an alias defeats it entirely.
- **`ast.FunctionDef` only.** `ast.AsyncFunctionDef` is a separate node type and
  is never visited, so an `async def` on the resolution path is unscanned.

The replacement is a syntax-aware sweep over the whole settings-resolution
surface rather than one file, resolving aliases and attribute chains instead of
matching text, and visiting sync and async definitions alike. Its sensitivity
proof must plant one case per evasion above — a passing suite has to mean the
rule holds, not that the detector never looked.

The runtime half stays as it is and stays necessary: the two cover different
halves of the same rule, for the reason the Enforcement section already gives.

### 3. Startup fails open on configuration defects

`_required_setting_errors` in
`packages/dotmac-kernel/src/dotmac_kernel/app_factory.py` wraps the seed and
`validate_required_settings` in `except Exception`, logs a warning, and returns
no errors. Its docstring justifies exactly one case — an unreachable store —
but the handler catches every case: a keyring/crypto failure, a missing column,
a permission error, an integrity violation, a defect in the seed itself. Each of
those is a configuration defect, and each currently skips required-setting
validation and lets production start.

The handler narrows to genuine database unavailability. A configuration defect
fails production startup. Liveness and readiness during real database outages
are a separate concern and stay separate: `/health` is DB-free by design and
must not acquire a dependency on this path.

### 4. "Once at startup" means idempotently, on every process start

The Decision says `seed_settings_from_env` "runs once at startup". That reads as
once per deployment. It is once per application-process start, every time, and
it is safe because it creates a row only when none exists. The operational
consequences follow from that and were never written down:

- **Changing the variable later changes nothing.** The row already exists, so
  the seed skips it. The environment does not update, rotate, or correct a
  setting after first creation, and no restart will make it.
- **After first creation, the settings owner is the only way in.** Operators
  change the value through the settings API or admin surface. Editing the unit
  file and restarting is a no-op that looks like an action.
- **Restoring a deployment needs more than the database.** Reproducing resolved
  values requires the same release and the same assembly profile (the profile
  default is code, not a row) and, for secrets, the encryption keys held under
  ADR-0009. A database backup alone is not sufficient.
- **Bootstrap history records provenance, never the value.** A seeded row's
  history entry names the canonical system actor and the variable NAME it came
  from. It must never record the variable's value — ADR-0009's rule that a
  secret is held and never copied applies to the audit trail too.

### Landing

Two slices. This amendment and the `docs/ARCHITECTURE.md` correction are the
first; the guard replacement and startup hardening are the second, because they
change behaviour and need their own tests. Bootstrap history provenance may
travel with the second slice or follow it.
