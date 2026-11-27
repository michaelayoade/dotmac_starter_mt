# dotmac-settings-admin

Generic, **stateless** administration of declared settings for DotMac products
(Starter parity programme, Wave 0 lane C; programme sections 1.2 and 2.5).

It promotes the reference app's `settings` feature to an installable module on
kernel module contract 2: surface `staff` on the `staff_admin` facet, a JSON API,
three permissions and an audit vocabulary. It owns no table, migration lineage or
namespace; its data is the kernel's `domain_settings` / `domain_setting_history`
rows and audit events.

## What it does

- Renders every declared setting domain generically (no per-module code), grouped by
  the owning module.
- Every write needs an **actor and a reason**, validates through the kernel's
  `validate_spec_value`, records both on the history row and writes an audit event.
  An **undeclared domain or key is refused**.
- **History and restore.** Restore is a *new* audited write of the value that preceded
  a recorded change; history is never edited.
- **Secrets** (secret-typed specs) are never rendered, echoed, logged, exported or
  restored: they appear only as `set` or `unset`, by name. The form is write-only.
- **Export/import** of non-secret tenant overrides as JSON, with a preview whose digest
  covers the current values (drift refuses the apply).
- Branding editor (no tenant CSS), organisation profile (a declared domain), read-only
  view of declared feature flags.
- **Optional maker-checker, default OFF** (`settings_admin/maker_checker`): when on, a
  writer without `settings_admin.approve` can only propose; a different approver applies.

## Composing it

Put `dotmac_settings_admin.module` in `ProductAssemblySpec.modules`, and grant
`settings_admin.read|manage|approve` to a role through the assembly's
`role_grant_profiles`. It declares the domains `branding`, `display`, `organisation`
and `settings_admin`; a product composing it must not also declare those.

## Not in this release

Feature-flag writes (the page is read-only), the integrations hub index, a
`surface_selection` entry (lane A) and the conformance kit (lane A); see `CHANGELOG.md`.
