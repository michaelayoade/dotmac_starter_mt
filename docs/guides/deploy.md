# Deploy to production

- **Goal:** deploy a published image to the supplied production Compose stack.
- **Audience:** operators with access to the deployment host, registry and database.
- The production Compose file uses an external PostgreSQL database; it does not build
  the image or provide a database service.

## Before you begin

- Choose an authorized published image tag and authenticate Docker to its registry.
- Install Docker Compose v2, Bash, `curl`, `gzip` and PostgreSQL `pg_dump`
  compatible with your database server.
- Provision the database and the required roles/grants securely.
- Confirm the database is reachable from the backup host and app container.
- Prepare TLS, tenant DNS and trusted proxy handling separately.
- Confirm that backup restoration works before relying on a production upgrade.

## Prepare configuration

- Use [.env.example](../../.env.example) to prepare the root `.env`.
- Set `ENVIRONMENT=prod` and all three database URLs.
- Set the real `PLATFORM_ROOT_DOMAIN` and `TRUSTED_HOSTS`.
- Supply strong `JWT_SECRET`, `SESSION_HASH_SECRET` and `CSRF_SECRET` values.
- Keep CSRF enabled. Its secret must be at least 32 bytes and different from the other two.
- Set registry-qualified `IMAGE_NAME` and the initial `APP_IMAGE` pin.
- Match `APP_PORT` with the listener and probe; the default is 8000.
- Use shell-compatible assignments: the script sources `.env`, overriding same-named
  values already exported in the shell.
- Keep the default root `.env` for this Compose setup. Changing `ENV_FILE` alone does
  not change Compose's declared container `env_file: .env`.

## Run the deployment

- From the repository root on the deployment host, replace the placeholder tag:

```bash
./scripts/deploy.sh <published-image-tag>
```

- The script checks the image, creates a compressed database backup, updates the
  image pin, pulls, migrates with `alembic upgrade heads` and recreates the app.
- It then probes `/health`, a liveness endpoint that does not check the database.
- **Expected:** the script finishes successfully and the intended image is running.

## Verify the result

- Check `/health`, logs and the running image tag.
- Sign in through the intended tenant hostname and exercise a representative workflow.
- `/health/live` and `/health/ready` are not mounted in this main-branch application.
- Complete an authenticated workflow to check more than process liveness.

## If something fails

- **Image lookup fails:** check the tag, registry-qualified name and registry access.
- **Migration fails:** inspect the failure and owner-role connection; do not stamp or skip it.
- **Probe fails:** check logs, database access, startup seeds and the probe's Host header.
- **Application rollback occurs:** the script can restore a recorded previous image,
  but does not undo migrations or automatically restore the backup.
- First deployment has no previous running release to recover automatically.

## Next step

- Read the [detailed deployment notes](../reference/project-guide.md#deployment).
- Review [security requirements](../SECURITY.md) and [migration discipline](migrations.md).
