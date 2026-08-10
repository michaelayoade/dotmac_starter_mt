# Changelog — dotmac-template-studio

All notable changes to the `dotmac-template-studio` distribution. This package
follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl. this
alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

## Unreleased

## 0.2.0a1 — 2026-08-10

Re-based on the notification contract selected by the Template Studio source
audit (ADR-0006 § 5b; evidence in
`docs/inventories/template-studio-source-audit.md`). **Breaking across the whole
surface** — a `0.MINOR` bump, as this changelog's header warns pre-1.0.

### Changed — BREAKING
- **Placeholder syntax is single-brace `{variable}`.** The previous
  `{{variable}}` is now REJECTED at save time. This is not a style change: it is
  Sub's production contract, ported with its behaviour tests, and double braces
  are the syntax that leaked a literal `{{amount}}` to customers because the live
  renderer filled single braces only. Every existing body must be rewritten.
- **`kind` is gone, and `kind=document` with it.** ERP's document templates need
  Jinja control flow, filters and page geometry; this module substitutes rather
  than evaluates, deliberately. Documents are a separate foundation owner.
- **Identity is `(tenant_id, slug, channel)`.** `channel` is now NOT NULL and
  part of the unique key rather than a nullable attribute, so one message can
  exist as `email` and `sms` with different wording — the shape Sub's seeded rows
  actually need, which the old `kind`-discriminated key could not represent.
- **The render route is `POST /template-studio/render/{slug}/{channel}`** (was
  `/render/{kind}/{slug}`), and `service.render_published` /
  `service.get_by_slug` take `(slug, channel)`.
- **`TemplateUpdate` no longer accepts `channel`.** Slug, channel and context are
  identity or contract, not editable metadata — changing them is a
  delete-and-recreate.
- **`list_templates` filters by `channel=`, not `kind=`.**

### Added
- **`RenderContext` and `register_contexts()`** — the product declares which send
  paths exist and exactly which variables each can supply; this module owns the
  checking and knows none of the names (ADR-0008: a vocabulary is a declaration
  registry, never an enum). A deployment that registers no context can create no
  template, which is the intended fail-closed behaviour.
- **Save-time placeholder validation.** `create_version` / `update_version` reject
  double braces and any variable the template's context cannot supply, before
  anything is stored. This is the load-bearing rule: a template that saves cannot
  later produce a half-substituted message, because every name it uses is known
  to exist before it can be published.
- **`ts_0002_notify_identity`** — drops `kind` and its CHECK, adds `context`,
  makes `channel` NOT NULL, and swaps the unique constraint. Existing rows are
  development data only; see the revision's docstring for the backfill note.
- The admin editor now lists the variables the template's context supplies, and
  shows a warning instead of a form when that context is not registered.

### Migration notes
No product consumer has cut over (`EXTRACTION.toml` still reads
`audit-required`), so there is no upgrade path to write beyond `ts_0002`. An
assembly upgrading must register at least one `RenderContext` before boot — see
`app/assembly.py` for the reference registration.

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
