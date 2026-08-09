# ADR-0013: A module declares the question; the platform declares the answer

- **Status:** Accepted (2026-08-09)
- **Scope:** Fleet-wide.
- **Relates to:** ADR-0003 (deployment profiles), ADR-0008 (declaration
  registries), ADR-0011 (env is a loader), ADR-0012 (`inherits`)

## Context

A `SettingSpec` currently carries two different kinds of fact welded together:

- **What the setting is** — its key, type, constraints, whether it inherits,
  whether it is secret. These are properties of the code that READS it. A
  deployment declaring `jwt_algorithm` to be an integer would simply break
  `auth_flow`.
- **What its value should be when nothing else supplies one** — the `default`.
  This is not a property of the reader at all. It varies by region, compliance
  regime, tenancy topology and customer, and it is currently hardcoded in
  module source where a deployment cannot reach it.

Because the second is unreachable, deployments express it the only way left:
**a fallback at the call site.** `dotmac_erp` has eight of them in `auth_flow`
alone, and four disagree with their spec:

| key | spec default | caller fallback |
|---|---|---|
| `refresh_cookie_secure` | `False` | `True` |
| `refresh_cookie_samesite` | `"lax"` | `"strict"` |
| `refresh_cookie_path` | `"/auth"` | `"/"` |
| `jwt_access_ttl_minutes` | `15` | `60` |

Two answers per key. Which one runs depends on the code path taken; the admin
screen shows one and the running system uses the other. Three of the four are
security properties. Nobody decided this — it is what happens when the place a
deployment needs to state something does not exist.

## Decision

**A module declares the question. The deployment profile declares the answer of
last resort.**

`ProductAssemblySpec.setting_defaults` — keyed `"<domain>/<key>"` — supplies
per-deployment defaults. Resolution becomes:

```
scope chain  ->  profile default  ->  spec fallback
```

A profile default **loses to every stored row** and **wins over the module's
fallback**. That direction is not negotiable: the inverse is the defect ADR-0011
removed from `env_var`, where a deployment-level value beat an operator's stored
row and the settings screen stopped describing what was in effect.

The spec keeps `default` as the module's **safe fallback** — what the code can
cope with when nothing is configured — while the profile states the deployment's
**intent**. Two clearly-named levels, rather than one value with two owners.

Provenance gains `"profile"`, because "the deployment declared this" and "the
module fell back" are different answers to an operator deciding whether to
override.

### The field was named backwards

It was `settings_overrides`, documented as "applied on top of env/defaults" —
i.e. winning over stored values. Nothing read it, so the name and the semantics
are corrected together. A profile does not override; it answers last.

### What a profile may not do

- **Introduce a key no module declares.** Rejected at startup. That is how
  settings with no reader appear, which the no-orphan-settings rule exists to
  prevent. A profile supplies answers; it cannot invent questions.
- **Declare a value its own spec rejects.** Rejected at startup, rather than
  silently resolving to the module fallback while looking configured.
- **Tighten or alter constraints.** The module owns the contract.

## Tenancy is a declared fact, not a code path

`ProductAssemblySpec.tenancy` records whether a deployment is single- or
multi-tenant.

**Nothing branches on it.** ADR-0003 is explicit that a single-tenant
deployment "keeps `Tenant`, request tenant context, composite tenant
constraints, and RLS — it is a topology, not a second schema or code path", and
scattering `if tenancy == ...` through features is precisely what that forbids.

What a declaration buys is that the intent becomes *checkable* and
*answerable*:

- A deployment declaring `single` that grows a second tenant row is a
  misconfiguration someone should hear about. Today nothing would notice.
- Provisioning knows whether a second tenant is expected or a mistake.
- **Settings scope stops being guesswork.** `dotmac_erp` has six identifier
  settings (`paystack_*_account_id`, `payroll_rounding_account_id`,
  `inventory_default_warehouse_id`) that could not safely be marked
  `inherits=False` because nobody knows whether its rows are global or
  per-organisation. A declared topology answers that instead of leaving it to
  inspection of production data.

## Consequences

- **A caller fallback becomes bannable.** Today `or "strict"` is the only way to
  state a deployment intent; with a profile it is a second answer competing with
  a declared one, and a lint rule can say so.
- **Security-relevant defaults become reviewable.** `refresh_cookie_samesite`
  moves from a literal buried in a helper to a line in the deployment's profile,
  where the person who owns the deployment can see it.
- **The kernel can ship safe defaults without dictating them.** It declares what
  it reads; a deployment overrides where its context differs.
- **`env_var` belongs here too, eventually.** Which environment variable seeds a
  setting is a deployment fact, not a module one. Left on the spec for now —
  moving it is a separate change with its own migration.

## What this does not resolve

It does not decide ERP's four divergent keys. Those readers belong to ERP today,
so reconciling them is ERP's work; this ADR only ensures the next disagreement
has somewhere to be settled other than a call site.

## Enforcement

`tests/unit/test_setting_profile_defaults.py`: a profile default beats the spec
fallback and loses to a stored row; provenance reports `"profile"`; bulk and
single-key agree; a falsy default is still a declaration; and startup rejects a
default for an undeclared key, a default its spec rejects, and a malformed key.
