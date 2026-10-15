# ADR-0074: Device desired configuration is composed from layered documents

- Status: Accepted (owner decisions of 2026-10-14; effective when this record merges)
- Date: 2026-10-14
- Decider: Michael
- Related: ADR-0006 (product-first extraction, dossier states), ADR-0017
  (adoption is the scarce resource; amended here), ADR-0024 (applications are
  independent), ADR-0038 (network suite; amended here); AGENTS.md rules 24, 29,
  30, 31, 32 and 33; inventory
  [`device-config-composition-sources.md`](../inventories/device-config-composition-sources.md)

## Context

Dotmac Sub needs the desired configuration of a router fleet to be composed from
reviewed layers (global, role, site, router), with provenance for every
effective value, typed conflicts, dated exceptions and a drift vocabulary,
before any change is pushed to a device. Sub drafted that as a dependency-free
engine (draft PR 3625, `dotmac_sub@ab2d08e0e`), but Sub's feature freeze
(`docs/FEATURE_FREEZE.md` in that repository) blocked it, and Michael directed on
2026-10-14 that it is built in Starter.

The checked-in inventory records what exists. In short:

- No Starter module or kernel facility composes layered desired configuration.
  The kernel settings resolver is scalar per key; `dotmac-brand-profiles` is
  brand-specific first-non-null-wins; `dotmac-network-control` owns command
  intent, dispatch and evidence, not composed desired state; none of the nine
  ADR-0038 modules owns it.
- No qualifying production-used composition engine exists in Sub, ERP or any
  other product. Sub's scope ladders (captive access policy, router-health
  dated exceptions) are domain-specific and are not generic implementations.
- One component does have a qualifying production source: Sub's RouterOS
  `/export` parser (`app/services/router_health/routeros_export.py`), deployed
  with Sub PR 3570 and tested.

## Decision

All eight answers below are Michael's, given on 2026-10-14.

### 1. Three distributions, and not `dotmac-network-control` (Q1, Q8)

| Distribution | Owns | Shape | Delivered by |
|---|---|---|---|
| `dotmac-config-layers` | The generic layered-composition engine: tiers, layers, merge strategies, conflicts, provenance, exceptions, derived-field DAG, widening, drift vocabulary, plug-in protocol | Stateless optional module (`db_schema: null`); imports only the standard library and pydantic | first slice |
| `dotmac-routeros-config` | The RouterOS plug-in: router tiers and binding schema, modules, pure report, JSON input parsing, and the RouterOS `/export` parser | Stateless; no I/O, no credentials, no transport | second slice, stacked on the first |
| `dotmac-network-config` | The desired-state owner: layer revisions, profiles, tags, subject bindings, assignments, exceptions, owner commands, blast radius, assurance projection | Stateful tenant-plane module with its own `mod_netcfg` namespace and lineage | later slice, after a kernel allocation |

The engine is not placed inside `dotmac-network-control`. That module's owner
statement is command intent and execution evidence; its dossier says it is not
to be published or adopted early (ADR-0038 section 3); RouterOS grammar is a
wire mapping that ADR-0038 section 6 keeps out of provider-neutral modules; and
it is stateful, so pinning it for pure code would install a lineage Sub must not
run before the suite cutover. `dotmac-network-control` keeps change-set
execution.

The engine is also not a kernel module: that would force Sub's kernel pin
upward to read a library and put the engine on the contended kernel release
train, and it needs no kernel invariant. The name avoids "composition", which in
Starter already means assembly and module composition.

### 2. ADR-0017 owner-directed exception

Decision 2 of ADR-0017 (the supply-push moratorium) is lifted for exactly these
three named units: `dotmac-config-layers`, `dotmac-routeros-config` and
`dotmac-network-config`. See the dated amendment in ADR-0017. It is not evidence
that a product is independently blocked; Sub's own freeze is context only.

### 3. ADR-0038 amendment (Q3)

`dotmac-network-config` is recorded as the owner of composed device desired
configuration, the tenth network module, and stays outside the `network-suite-v1`
cohort. See the dated amendment in ADR-0038.

### 4. Boundary clarification (Q2)

The 2026-09-02 boundary ruling (Knowledge record
`starter-boundary-reusable-in-process-only`) says Starter holds reusable
in-process capabilities and that provider-specific connector plugins leave.
`dotmac-routeros-config` is provider-specific but is not a connector. The
ruling is clarified, narrowly, as: **no I/O, no credentials, no transport in
Starter.** On that basis `dotmac-routeros-config`, and only that package, may
live in Starter. It must import no network, subprocess, socket or
persistence module, hold or name no credential, and open no connection. A
connector, session, SSH/REST/SNMP client or credential holder is still outside
Starter. This clarification is specific to the named package; it is not a
general admission route for provider code.

The package must still pass the adoption test: a product-neutral contract for
the engine; a named owner and public surface; a real first adopter (Sub, by
pinning after publication); no product branches (check ids, timezone and router
names stay in Sub); and a cutover that retires a local implementation (Sub's
parser copy). The adoption test bears on publication and is checked at release
time, not by this record.

### 5. Product-first treatment (AGENTS rule 24)

- `dotmac-config-layers`, and the plug-in modules of `dotmac-routeros-config`,
  are `greenfield-after-inventory`: the inventory shows no qualifying source.
  Sub's draft PR 3625 is cited as reviewed initial code and is not a qualifying
  production source because it was never merged or used in production.
- The RouterOS `/export` parser is product-first: Sub's deployed implementation
  and its tests are the initial code and `preserved_tests`. A dossier carries one
  `source_mode`; the mixed provenance of `dotmac-routeros-config` is explained in
  its single `source_mode_note`.
- Copying is a one-time extraction. Sub deletes its parser copy once Sub's
  existing parser tests pass against the package parser (Q7); the Sub tests stay
  and run as parity tests.

### 6. Release and consumption (Q4)

- The router packages are released by the same release captain as billing.
  Billing releases go first; router packages are released only in gaps between
  billing releases and are never bundled with a billing release, and the two
  trains never run concurrently (AGENTS.md rule 32).
- Order is fixed: `dotmac-config-layers`, then `dotmac-routeros-config`, whose
  release smoke installs the first wheel.
- `dotmac-network-config` is published only after its kernel namespace
  allocation is merged and a kernel release is authorized by Michael.
- Agents do not dispatch a release. Michael approves the exact head and every
  merge.
- Packages start at `0.1.0a1` with status `audit-complete`. Status moves to
  `adopted` only on an `adoption_evidence` row naming an immutable Sub commit
  (rule 24, amendment of 2026-08-29); a pin alone is installation.

### 7. Sub consumption (Q5, Q6, Q7)

- Sub consumes the packages through its CLI only for now (Q5). No Sub web UI or
  new `app/` module is requested under this decision.
- Activation approval for the stateful owner is Sub's approver allowlist plus
  step-up evidence, carried into the owner command as a typed approval bound to
  the blast radius (Q6). Sub decides who may approve (ADR-0038 section 5);
  `dotmac-approvals` is not used.
- Sub may delete its parser in the same change that re-points `router_health`
  to the package parser, once the `router_health` tests pass against the package
  parser (Q7).

### 8. What this decision does not do

It does not publish, release, pin or adopt anything. It does not move command
execution out of `dotmac-network-control`, relax any security control, or allow
credentials, transport or any live device interaction in Starter. Starter is a
public repository: its tests use synthetic data and documentation address ranges
only; real-fixture parity stays in Sub.

## Consequences

- Three new rows will join the module release allowlist, which changes the
  release-authority digest; each lands with its own slice.
- `dotmac-network-config` needs a kernel allocation of `netcfg`/`mod_netcfg`
  merged to `main` before its source (rule 14), then a kernel release.
- The engine's `kernel_floor` stays at or below the kernel Sub pins, so Sub does
  not raise its kernel pin to read a library.
- Governance overhead is accepted in exchange for a single shared owner.
