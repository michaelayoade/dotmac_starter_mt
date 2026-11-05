# Dotmac Starter 0.9.0 RC1 handoff

This bundle uses the published Linux amd64 Starter image pinned in
`.env.example`. The platform administrator bootstrap script is from Starter
commit `5406b7381118be73f05ac818db0a2e5e5b10b9ba`.

## Fresh installation

1. Use Docker with Compose and a fresh PostgreSQL 16 instance reachable from
   the containers. On a dedicated instance, connect to the `postgres`
   database with a provisioning account and run `database-roles.sql`.
   Set the psql variables `admin_password`, `app_password`, and
   `platform_password` to distinct secrets before including the file.
   On a managed instance, ask the administrator to provision the equivalent
   roles and database.
2. Copy `.env.example` to `.env`. Set `DATABASE_URL` and
   `PLATFORM_DATABASE_URL` to the reachable database using the corresponding
   online roles. Set the platform root domain, trusted hosts and independent
   secrets as described in the template. Use port 8000 for this image. Set
   `ENVIRONMENT=prod` for customer deployments and configure HTTPS ingress.
   Keep the migration URL out of `.env`. Export it in the operator shell as
   `MIGRATION_DATABASE_URL` for the following two one-off commands.
3. Pull, migrate and create the first platform administrator from this
   directory. The bootstrap command prompts for the password twice.

   ```sh
   docker compose pull
   docker compose run --rm -e MIGRATION_DATABASE_URL app alembic upgrade heads
   docker compose run --rm -e MIGRATION_DATABASE_URL \
     -v "$PWD/scripts/create_platform_admin.py:/srv/app/scripts/create_platform_admin.py:ro" \
     app python scripts/create_platform_admin.py admin@example.com
   unset MIGRATION_DATABASE_URL
   docker compose up -d
   ```

4. On the configured platform root host, check `/health/ready`. Sign in at
   `/platform/login` or POST the email and password as JSON to
   `/platform/auth/login`. A successful API login returns an access token.

## Upgrades

Back up the database and follow the release-specific upgrade instructions
before changing the image digest. Apply migrations in a one-off container
before starting the updated application. RC1's hosted upgrade qualification
used the selected pre-hardening source baseline.

See `THIRD_PARTY_NOTICES.md` and the bundled static notices. This review
bundle awaits the final customer terms and dependency notice package.
