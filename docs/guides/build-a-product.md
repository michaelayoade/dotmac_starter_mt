# Choose features and brand your product

- **Goal:** understand what to select and customize when building on Starter.
- **Before you begin:** complete [Your first tenant](../tutorials/first-tenant.md).
- Use the [product vision](../PRODUCT_VISION.md) for the intended reuse model;
  cloning this checkout does not automatically deliver future fixes.

## Choose the application surface

- The reference selection lives in [app/assembly.py](../../app/assembly.py).
- Use `DISABLED_FEATURES` to omit applicable feature routes without deleting source.
- For example, to omit custom fields:

```bash
export DISABLED_FEATURES=custom_fields
```

- Restart the app after changing startup configuration.
- **Expected:** that feature's API and web routes are not mounted.
- Other features can depend on a selected feature; check the manifests before disabling it.
- Use `WEB_ENABLED=false` for the tenant feature portal's API-only mode.
- `DISABLED_FEATURES=web` only removes the dashboard feature, not all login or admin routes.
- The platform surface is separately composed; see the
  [full feature-switch reference](../reference/project-guide.md#disabling-a-feature).

## Apply your branding

- Set deployment defaults in [brand.json](../../brand.json) and the documented `BRAND_*` overrides.
- For one tenant, sign in as its owner and open `/admin/settings/branding`.
- Edit the supported name, tagline, logo URL and colors.
- **Expected:** the tenant's portal uses the resolved brand values.
- Tenant-authored raw CSS is not accepted; the app generates token CSS.

## Add business behavior

- Put application features under `app/features/` and register their manifests.
- Keep request adapters thin; services own business decisions.
- Use canonical Party identity for people and organizations.
- Give tenant-scoped storage the required constraints, grants and RLS policies.
- Declare settings with a real reader, rather than adding unused configuration.
- Follow [Contributing](../../CONTRIBUTING.md#adding-things-pointers-not-duplicates)
  and the [architecture reference](../ARCHITECTURE.md) before implementing a feature.

## Next step

- [Add a runtime custom field](custom-fields.md).
- [Review the module catalog](../MODULE_CATALOG.md) for reusable packages and their evidence state.
