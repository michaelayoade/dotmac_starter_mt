# dotmac-config-layers

`dotmac-config-layers` composes a subject's effective configuration from
weighted, tiered layers. It is a stateless optional module: it owns no tables,
migrations, sessions, clocks, network calls, credentials or provider vocabulary.

Given a subject, immutable layer revisions, dated exceptions and a plug-in, it
returns one `Effective` state: per-module effective parameters, provenance for
every effective leaf, typed conflicts, exception statuses (active, pending
approval, lapsed), lapsed-exception reports and stable hashes. The same inputs
give the same output in any input order. The caller supplies `today`; nothing is
read from a clock.

## Ownership

- This package owns deterministic composition only.
- A plug-in (for example `dotmac-routeros-config`) owns its domain's tiers,
  field specs, derived rules and module schemas, through `DomainPlugin`.
- `dotmac-network-config` (a separate, stateful module) owns durable layer
  revisions, assignments and commands.
- The consuming assembly owns storage, approval decisions and the product
  vocabulary.

## Public surface

Everything importable from `dotmac_config_layers` is listed in `__all__`:
`compose`, `evaluate_module`, `widening`/`params_widening`/`mode_widening`,
the layer and exception types, `Effective`/`ModuleResult`, provenance and
conflict types, the drift vocabulary, and the plug-in protocol (`DomainPlugin`,
`Module`, `ModuleBase`, `SurfaceClaim`, `DerivedRule`). The package ships
`py.typed` and is held to Starter's strict typed-contracts standard.

The engine imports only the standard library, pydantic and itself. The manifest
is the one kernel import.

## Status

Audit-complete and unadopted (see `EXTRACTION.toml`). The inventory is
`docs/inventories/device-config-composition-sources.md` and the decision is
ADR-0074. The code was ported from Dotmac Sub draft PR 3625; `PORT_MAP.json`
lists each file's source blob and the lint-only deltas applied. Nothing here is
published or adopted until a release record and a Sub adoption commit say so.
