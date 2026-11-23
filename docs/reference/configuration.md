# Configuration reference

- **Goal:** find the right place to configure the app.
- [.env.example](../../.env.example) lists deployment variables and their notes.
- [Settings](../../packages/dotmac-kernel/src/dotmac_kernel/config.py)
  defines the application-process defaults and types.
- [Compose](../../docker-compose.yml) and [deploy.sh](../../scripts/deploy.sh)
  also own deployment-specific options. They do not all read configuration the same way.

## Database connections

- `DATABASE_URL`: tenant request connection, normally `app_user`.
- `PLATFORM_DATABASE_URL`: online platform connection, normally `platform_api`.
- `MIGRATION_DATABASE_URL`: migration and offline maintenance connection, `app_admin`.
- Use separate role credentials in production. See [database roles](project-guide.md#db-roles).

## Common choices

- `ENVIRONMENT`: export explicitly. Recognized development/test values publish API docs;
  staging, production, missing and unknown values use the restricted documentation policy.
- `TENANCY`: `multi` or `single`. Single mode requires exactly one existing tenant;
  provision it first. It retains tenant context and RLS.
- `PLATFORM_ROOT_DOMAIN`: the root host used in tenant resolution and platform access.
- `TRUSTED_HOSTS`: hosts accepted by the configured host middleware.
- `WEB_ENABLED`: selects whether feature web routes are mounted.
- `DISABLED_FEATURES`: comma-separated features to omit; it is not a permission system.
- `SEED_ON_STARTUP`: controls enabled feature seed hooks. Required-setting validation
  still seeds absent environment-backed settings, so disabling it does not make startup read-only.

## Process configuration versus business settings

- Process configuration comes from environment variables and the local `.env` settings loader.
- Business settings resolve from scope-specific rows, assembly profile defaults and spec fallbacks.
- A setting's declared environment variable seeds a missing row at startup.
- Changing that environment variable later does not overwrite or rotate an existing row.
- After creation, update the setting through its owning settings interface.

## Security and branding

- Production requires strong JWT, session-hash and CSRF secrets; see [deployment](../guides/deploy.md).
- The default rate limiter is bounded and process-local.
- `RATE_LIMIT_REDIS_URL` alone does not connect Redis; an assembly must supply a shared store.
- Deployment branding starts in [brand.json](../../brand.json); tenant overrides use the branding editor.
- For exact controls and limitations, read [Security](../SECURITY.md).
