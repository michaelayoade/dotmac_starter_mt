# Changelog — dotmac-template-studio

All notable changes to the `dotmac-template-studio` distribution. This package
follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl. this
alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

## Unreleased

## 0.1.0a1 — 2026-08-06

First alpha, and the first INSTALLABLE STATEFUL MODULE in the fleet (ADR-0006 M1).

### Added
- **`mod_tstudio` namespace and the `ts` migration lineage.** Allocated in the
  kernel's `MIGRATION_OWNER_LEDGER` (kernel `0.1.0a13`) and declared by this
  module's manifest; `NamespaceRegistry` refuses the composition if the two ever
  disagree. Lineage root `ts_0001_templates` carries branch label
  `template_studio` and orders itself after the kernel's `tenants` table with
  `depends_on`, never `down_revision`.
- **Two tenant-scoped tables**, `templates` and `template_versions`, both with
  `tenant_id NOT NULL`, composite uniques including `tenant_id`, RLS ENABLEd and
  FORCEd, a tenant-isolation policy, and online-role grants — all in the one
  migration that creates them. `template_versions` references its parent through
  a COMPOSITE `(tenant_id, template_id)` foreign key, so a version row can never
  point at another tenant's template even with RLS bypassed.
- **`service`** — the module's one decision owner: version allocation,
  publication, published-revision immutability, variable extraction, and
  rendering. Placeholder substitution is a regex, deliberately not a Jinja
  environment: a tenant-authored body is untrusted input.
- **JSON API** under `/template-studio`, guarded by four declared permissions
  (`read`, `manage`, `publish`, `render` — publish is separate from manage
  because authoring a draft and changing what customers receive are different
  decisions). Six declared audit actions; rendering writes none, since one row
  per outbound message would flood a tenant's log with traffic rather than
  decisions.
- **Admin surface** at `/admin/templates`, mounted through the kernel's
  `packaged_template_dirs` slot. Screens style themselves with `--dmui-*` tokens
  rather than the host's utility classes — see the README for why that is
  structural rather than a preference.

### Requires
- `dotmac-kernel >= 0.1.0a13` — earlier kernels have neither the
  `packaged_template_dirs` composition slot nor the `mod_tstudio` ledger row, so
  they cannot register this module at all.
