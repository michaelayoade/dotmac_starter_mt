# Compatibility - dotmac-settings-admin 0.1.0a1

| Surface | Contract |
|---|---|
| Module contract | 2 (`web_surfaces`, no legacy `web_routers`/`nav`) |
| Facet | `staff_admin` (surface code `staff`), htmx 2, UI contract 1 |
| State | none: no table, no migration lineage, no namespace row |
| Kernel floor | `>=0.1.0a110`; uses `settings_admin`, `settings_resolver`, `audit`, `web_surfaces` |
| Permissions | `settings_admin.read`, `settings_admin.manage`, `settings_admin.approve` |
| Setting domains owned | `branding`, `display`, `organisation`, `settings_admin` |
| JSON API | `/settings...` (reads need `read`; writes need `manage` plus a `reason` in the body) |
