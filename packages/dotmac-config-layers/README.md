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
ADR-0078. The code was ported from Dotmac Sub draft PR 3625; `PORT_MAP.json`
lists each file's source blob and all declared deltas, including the approval
classification corrections for default-restoring deletions and new containers. Nothing here is
published or adopted until a release record and a Sub adoption commit say so.

## Integration checklist

- Pin `dotmac-config-layers` in the consuming assembly and register `dotmac_config_layers.module` there.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: distribution is a stateless optional module with no namespace](../../tests/architecture/test_config_layers_module.py#L108). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_config_layers_module.py::test_distribution_is_a_stateless_optional_module_with_no_namespace -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
