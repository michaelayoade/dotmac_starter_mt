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

### Deferred

- Feature-flag writes (read-only page now): the kernel has no flag-override writer.
- Integrations hub index: needs the application-directory contract.
- Registering the surface with the lane A `surface_selection` and running the lane A
  conformance kit over this module (TODO markers in `tests/architecture/test_settings_admin_module.py`).
- A dedicated `settings` navigation region: the entry link lives in `primary` for now.
