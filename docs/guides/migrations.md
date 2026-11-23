# Apply database migrations

- **Goal:** bring a database up to the schema required by this checkout.
- **Before you begin:** use the matching application revision and an owner-role
  database URL. Back up important data before upgrading.
- Run local commands from the repository root.

## Local development

- Start PostgreSQL and export the three URLs from
  [Your first tenant](../tutorials/first-tenant.md#2-configure-and-migrate).
- Apply all composed migration lineages:

```bash
poetry run alembic upgrade heads
```

- **Expected:** Alembic finishes without errors.
- `make migrate` runs the same upgrade.
- Use `MIGRATION_DATABASE_URL` with `app_admin`; do not migrate as the request role.
- Do not export a blank migration URL.
- `heads` is plural because kernel, assembly and installed modules have separate lineages.

## Production

- Use [the deployment script](deploy.md); application startup does not run migrations.
- Keep migrations compatible with the previous app version.
- Rolling back an image does not roll back the database.
- Do not use `alembic stamp` to disguise an unapplied migration.

## If something fails

- **Permission denied:** check that the migration URL uses the owner role.
- **Multiple heads:** use `upgrade heads`, not `upgrade head`.
- **Missing prerequisite:** inspect the composed migration bindings and the real database;
  do not skip the failing migration.
- **Existing database behaves differently:** compare its applied revisions and catalog
  with the intended release before attempting repair.

## Next step

- New migration authors: read [migration discipline](../../CONTRIBUTING.md#migration-discipline).
- For lineage ownership and ordering, see [the architecture reference](../ARCHITECTURE.md#module-database-namespaces-and-migration-lineage-adr-0006-d1).
