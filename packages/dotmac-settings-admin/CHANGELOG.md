# Changelog - dotmac-settings-admin

## 0.1.0a1 - unreleased

Source only; no release is requested by this change.

- New stateless package promoted from the reference app's `settings` feature
  (`app/features/settings`, deleted in the same change).
- Contract-2 manifest: surface `staff` on `staff_admin`, `api_routers`, permissions
  `settings_admin.read|manage|approve`, audit actions `settings_admin.*`, domains
  `branding`, `display`, `organisation`, `settings_admin`, and the platform-default seed.
- Actor and reason required on every write; undeclared keys refused; restore is a new
  audited write; secrets only ever set/unset; export omits secrets; import preview with
  a drift digest; optional maker-checker (default OFF).

### JSON API contract change (reference app only)

The `/settings` API moved with the feature and is NOT backward compatible: every
write needs a `reason` in the body; `PUT` returns `{applied, proposal_id, setting}`
(the old flat setting is under `setting`); a secret reports `is_set` with no
`value`; and the audit action `settings.update` became
`settings_admin.setting.update`. Acceptable here because the reference app is the
only consumer; an adopter composing this module gets the new contract from day one.

### Review round (architect, 2026-11-25)

- Maker-checker proposals are serialised per tenant with `pg_advisory_xact_lock`; a
  staged value is refused if the spec has since become secret; the approver sees the
  proposed value beside the current one (non-secret only); `pending_changes` is
  `inherits=False`.
- `list_history` shows no values and is never restorable for a secret spec.
- A validation failure on a secret spec is generic and never echoes the input.
- `may_approve` is a required keyword on every public write function.
- The platform-default seed is no longer on the manifest; the reference assembly
  binds it in `startup_hooks` (gated on `SEED_ON_STARTUP`).

### Deferred

- Feature-flag writes (read-only page now): the kernel has no flag-override writer.
- Integrations hub index: needs the application-directory contract.
- Registering the surface with the lane A `surface_selection` and running the lane A
  conformance kit over this module (TODO markers in `tests/architecture/test_settings_admin_module.py`).
- A dedicated `settings` navigation region: the entry link lives in `primary` for now.
