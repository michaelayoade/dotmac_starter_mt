# Run Starter and create your first tenant

- **Goal:** run the reference app, create an ACME workspace and add a person.
- **Audience:** developers trying this checkout for the first time.
- **Scope:** local development with disposable data, not production deployment.
- Run commands from the repository root in a Bash-compatible shell.

## Before you begin

- Install Python 3.12 or 3.13; the production image uses 3.12.
- Use Poetry 2.4.1, as pinned in `pyproject.toml`.
- Install Rust 1.90.0, Cargo and native compiler/linker tools. Some path dependencies
  build native extensions during installation.
- Install Docker with Compose v2, Node.js/npm, GNU Make, `curl` and `jq`.
- On Windows, use a suitable Bash environment, such as WSL, with these tools available.
- Start from a checkout you are authorized to access.
- The commands below use the default local database name, port and development passwords.
  If you change them, update the URLs to match.

## 1. Install dependencies and start PostgreSQL

```bash
poetry install --only main
npm ci
docker compose -f docker-compose.dev.yml up -d --wait postgres
```

- **Expected:** PostgreSQL is healthy and dependency installation finishes successfully.
- The database init hook creates the tenant, platform and migration roles on a fresh volume.
- Use this dev Compose file for the local database; the production file has no database service.
- Local test dependency installation and test execution are not allowed. The npm
  install above supplies the existing frontend build tools; Python tests run in hosted CI.

## 2. Configure and migrate

```bash
export ENVIRONMENT=dev
export TENANCY=multi
export PLATFORM_ROOT_DOMAIN=localhost
export DATABASE_URL=postgresql+psycopg://app_user:app_user@localhost:5432/starter
export PLATFORM_DATABASE_URL=postgresql+psycopg://platform_api:platform_api@localhost:5432/starter
export MIGRATION_DATABASE_URL=postgresql+psycopg://app_admin:app_admin@localhost:5432/starter
poetry run alembic upgrade heads
make css-build
```

- **Expected:** migrations and the CSS build finish without errors.
- Use `heads`, plural: the app combines multiple migration lineages.
- Export `ENVIRONMENT` explicitly so development API documentation is available.
- Existing `.env` values may affect other options; check for unexpected feature or host overrides.

## 3. Start the app

```bash
make dev
```

- Keep this terminal running. Open another Bash terminal at the repository root.
- Repeat the exports from step 2 in the new terminal; exports belong to each shell.
- Check liveness:

```bash
curl -fsS http://localhost:8000/health
```

- **Expected:** `{"status":"ok"}`.
- Liveness means the process responds. It does not prove the database is ready.
- This main-branch application does not expose `/health/ready`; verify database access
  through the authenticated workflow below.

## 4. Create a platform administrator

```bash
poetry run python scripts/create_platform_admin.py you@example.com
```

- Enter a password when prompted.
- This account manages tenants. It is separate from the ACME owner you create next.
- Sign in, replacing the example password with the one you entered:

```bash
PLATFORM_TOKEN=$(curl -fsS -X POST http://localhost:8000/platform/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"REPLACE_WITH_YOUR_PASSWORD"}' | jq -er .access_token)
```

- **Expected:** `PLATFORM_TOKEN` contains an access token. Keep it private.
- These examples use JSON literals; escape special characters if your password requires it.

## 5. Create the ACME tenant

```bash
curl -fsS -X POST http://localhost:8000/platform/tenants \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $PLATFORM_TOKEN" \
  -d '{"slug":"acme","name":"ACME","owner_email":"admin@acme.example","owner_password":"correcthorsebatterystaple"}'
```

- **Expected:** a tenant response for `acme`.
- Provisioning creates the tenant and its owner together.
- The owner has the admin role needed for this tutorial.
- The password above is for disposable development only.

## 6. Sign in as the owner and create a person

```bash
ACME_TOKEN=$(curl -fsS -X POST http://acme.localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@acme.example","password":"correcthorsebatterystaple"}' | jq -er .access_token)

PERSON=$(curl -fsS -X POST http://acme.localhost:8000/parties/people \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACME_TOKEN" \
  -d '{"email":"alice@acme.example","first_name":"Alice","last_name":"Example"}')
printf '%s\n' "$PERSON"

curl -fsS http://acme.localhost:8000/parties \
  -H "Authorization: Bearer $ACME_TOKEN"
```

- **Expected:** Alice appears in the tenant's party list.
- A **party** is a person or organization represented in the application.
- This walkthrough demonstrates usage; hosted PostgreSQL canaries prove cross-tenant isolation.

## 7. Open the admin portal

- Open `http://acme.localhost:8000/admin/login`.
- Sign in with `admin@acme.example` and the owner password from step 5.
- Open **Parties** and find Alice.
- Use **Settings → Branding** to explore the tenant's name, logo and colors.
- Browser login uses cookies and CSRF protection; the API examples use bearer tokens.

## If something fails

- **Install fails during a native build:** check Rust 1.90.0 and your compiler/linker.
- **Database connection fails:** check Docker health, port 5432 and all three role passwords.
- **CSS build fails:** run `npm ci` before `make css-build`.
- **Tenant host does not resolve:** try `curl --resolve acme.localhost:8000:127.0.0.1`
  with the same API request; configure local hostname resolution for the browser if needed.
- **Login or access fails:** use the platform account on `localhost`, and the tenant
  owner on `acme.localhost`. They are different accounts.
- **Tenant already exists:** use your existing owner credentials or a new tenant slug.
- **Health succeeds but tenant operations fail:** inspect startup logs and database access;
  liveness does not exercise the database.
- **Old database lacks roles:** the init hook only runs on a fresh volume.
  Back up anything important before deliberately recreating a disposable dev database.

## Next steps

- [Add a custom field](../guides/custom-fields.md).
- [Choose features and branding](../guides/build-a-product.md).
- [Understand the architecture](../concepts/overview.md).
