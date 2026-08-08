# ADR-0011: Settings resolution reads rows and defaults, nothing else

- **Status:** Accepted (2026-08-08). The behaviour has shipped since `0.1.0a19`;
  this ADR makes it a property rather than a habit.
- **Scope:** Fleet-wide.
- **Relates to:** ADR-0008 (declaration registries), ADR-0009 (secrets are held)

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

Precedence, stated once:

```
scope chain (most specific row first) -> spec default
```

The environment does not appear, because it is not a source. It is a loader that
produces a row, and a row is indistinguishable from any other once written.

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
