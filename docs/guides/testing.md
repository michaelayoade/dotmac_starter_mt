# Testing and checks

- **Goal:** understand how to validate a change and read its results.
- Tests and Python test/development dependency installation run only on Git-hosted CI.
- Local inspection, editing, formatting and available pinned static checks are allowed.
- Static checks are not evidence that runtime behavior passed.

## Submit a change

- Keep the change focused and include relevant documentation.
- Write regression tests before adding new behavior.
- Push your branch and open a pull request.
- Inspect the checks for that exact head and base revision.
- Fix failures and wait for every required check to finish successfully before merging.

## What CI checks

- **Unit tests:** behavior using lightweight fixtures; SQLite does not prove RLS.
- **Architecture tests:** boundaries, registries and repository contracts.
- **PostgreSQL integration tests:** migrations, grants and cross-tenant isolation.
- **Quality checks:** lint, formatting, typing, import rules and other configured gates.
- **Artifact and runtime checks:** the applicable consumer, package and Docker checks.
- The [workflow](../../.github/workflows/ci.yml) owns the exact setup and job inventory.

## Avoid misleading results

- Starting a test database alone does not reproduce the hosted integration environment.
- Integration needs the additional identity databases and URLs prepared by CI.
- Skipped database tests do not prove isolation.
- Do not set `CI=true` locally to bypass the hosted-only rule.
- If the PR head or base changes, use results for the new combination.

## Next step

- Read [Contributing](../../CONTRIBUTING.md) for the full development rules.
- Read [AGENTS.md](../../AGENTS.md) for canonical controls and exceptions.
