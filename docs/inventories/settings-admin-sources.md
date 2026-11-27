# Settings admin: source inventory

Inventory for the `dotmac-settings-admin` dossier
(`packages/dotmac-settings-admin/EXTRACTION.toml`).

## Qualifying source: the Starter reference feature

`dotmac_starter_mt@9c016f99`: `app/features/settings` (router, web, service, spec,
seed; contract 1). The package re-homes it on module contract 2 and deletes the
feature in the same change.

## Kernel adjacency (not an equivalent)

`dotmac_kernel.settings_admin` / `settings_resolver` own the rows, resolution, the
history writer and `validate_spec_value`. The package consumes them; it adds the
surface, the actor/reason/audit rule, restore, export/import and maker-checker.

## ERP v2: inventoried

`dotmac_erp_v2` `src/erp_v2/settings_specs.py` copies the feature because no wheel
carried it (its own header says so). Its AR prerequisite keys
(`src/erp_v2/ar/settings.py`, domain `ar_producer`) are the composition proof: the
package must make all seven required keys settable without per-module code.

## Sub and ERP legacy writers

Both settings stores are inventoried at the pins in `EXTRACTION.toml`
(`dotmac_sub` `app/models/domain_settings.py`, `domain_setting_history.py`;
`dotmac_erp` `app/models/domain_settings.py`, `app/services/domain_settings.py`,
`app/services/admin/settings_web.py`). Each is a `legacy_writer` that retires
when its product composes this module on the kernel settings tables.
