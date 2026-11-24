# Contributing

Human developer rules. Agents follow the same canon via `AGENTS.md`
(the canonical hard-rules list — read it first; nothing here overrides it).

## Validation and merge gates

Tests run only on Git-hosted CI; do not run test commands or install
test/development dependencies on a workstation or any named test host.
Local static checks are allowed. Git-hosted PR CI is the merge owner
(`AGENTS.md`, "Validation before any commit").

```bash
# Local: pinned static checks only, without installing test dependencies.
# Git-hosted PR CI runs make check and make test-unit.
# For models, migrations, guards, or tenancy, CI also runs:
# make test-db-up && make test-integration && make test-db-down
```

All relevant Git-hosted CI gates must be green before merge.

## Test-first expectations

- Write the test before the behavior. For anything tenancy-affecting, the
  cross-tenant isolation canary comes FIRST (two tenants; B must get
  404/empty for A's rows even with the exact UUID) — copy the pattern in
  `tests/test_cross_tenant_isolation.py`.
- A new governance/architecture test needs a sensitivity proof: temporarily
  introduce the violation, watch the test go RED, revert.
- Unit tests (SQLite) never prove tenancy. RLS correctness only counts on
  Postgres (`make test-integration`).

## Migration discipline

- The SAME migration that creates a table creates its RLS
  (`ENABLE` + `FORCE` + policy) and its grants. No "add tenant_id later".
- Platform catalog tables (no `tenant_id`): GRANT `platform_api`/`app_admin`
  only, REVOKE from `app_user` — the catalog audit
  (`tests/test_rls_catalog.py`) fails otherwise.
- Migrations run as `app_admin` (`MIGRATION_DATABASE_URL`), never on
  container boot; `scripts/deploy.sh` is the only production migration path.
  New revisions stay backward-compatible with the previous release
  (deploy rolls the app back, not the schema).

## Adding things (pointers, not duplicates)

- **A feature package** — [Choose features and branding](docs/guides/build-a-product.md)
  and `CLAUDE.md` § Extension points: package + `feature.py`, register in
  `FEATURE_MODULES`, add to the import-linter independence contract,
  isolation test first.
- **A setting** — declare a `SettingSpec` in your feature's spec module and
  wire a real `resolve_value(...)` reader before shipping
  (`tests/architecture/test_no_orphan_settings.py` fails otherwise).
- **A custom-fields entity** — register the model in
  `app/features/custom_fields/registry.py::ENTITY_MODELS` and give it a
  `custom_fields` JSONB column (see `Party.custom_fields`).
- **A model** — placement rule and provenance table:
  `CLAUDE.md` § Model placement rule and `docs/ARCHITECTURE.md`'s model
  provenance + ownership tables (every model/resource names its owner).
- **A config knob** — overridable with a documented default
  (`Settings` + `.env.example`); prod-unsafe defaults go in
  `validate_settings`'s prod-fatal list. Never hardcode ports, hosts,
  image names, or paths.

## IP addresses in a public repository

This repository is public, so the `No new public IP literals` check
(`scripts/check_new_ip_literals.py`) fails a pull request that ADDS a globally
routable IPv4 or IPv6 address or network. Use role names in code and docs,
documentation ranges (RFC 5737 / RFC 3849) in tests and examples, and real
addresses only at deploy time. Private, loopback, link-local and CGNAT ranges
pass. A deliberate exception takes an inline marker with a reason:
`ip-literal-ok: <reason>`. Existing content is not rewritten.

## Pull requests

- Small and reviewable — one coherent slice per PR, not a grab-bag.
- CI must be green before merge (quality matrix, unit, Postgres
  integration, docker-build health gate — `.github/workflows/ci.yml`).
- Breaking changes are called out in the PR body and CHANGELOG entry.
- Docs move with the code: update `docs/ARCHITECTURE.md`'s tables and any
  affected ADR (dated amendment, never rewritten history) in the same PR.

## README policy

Every feature or runtime-behaviour PR updates root `README.md` in the same diff;
a package README or a page under `docs/` alone does not satisfy the rule.
Summarise the capability, relevant usage/setup changes and link to detailed docs.
Do not imply a package is composed, released or deployed without evidence.

The conservative gate covers `app/`, `templates/`, `static/`, `content/`,
`alembic/`, `deploy/`, `scripts/`, `native/` and `tools/`; package `src/`,
`migrations/` and `native/` trees; dependency manifests/locks; Dockerfile and
docker-compose files. Fixes and removals on those surfaces count too. Changes
limited to docs, tests or workflows are exempt, as are package docs-only edits.
Whitespace-only changes and README deletion fail.

`scripts/check_readme_update.py` owns this repository-local diff policy.
The CI `readme-policy` job compares immutable PR base/head SHAs at their merge
base, disables rename detection so removals cannot disappear, and runs the
sensitivity tests in `tests/unit/test_readme_policy.py`. The existing quality
matrix depends on this job. Keep the existing quality checks required for merge;
this change does not rewrite repository protection or the pinned fleet standards.

Tests run only on Git-hosted CI, including the gate's disposable Git fixtures.
Local work is limited to inspection, editing, formatting and permitted static
checks. Reviewers must verify semantic relevance; the gate proves a text change,
not that prose is accurate. A changed policy/workflow also requires review.


### Automatic draft with human review

Once this workflow lands on main, a new CI request on a same-repository PR can
trigger `Draft README for review`. Trusted default-branch code reads PR prose
and paths as API data, and appends a proposed section to README.md. It never runs
PR code with a write token. It only writes README.md, preserves any existing
README change, skips forks and refuses non-fast-forward branch updates.

Drafting quotes the author's first description paragraph and lists changed
implementation areas. It is templated, not AI analysis; no inference API key is
needed. A person must add accurate usage/setup details and place the text in the
appropriate README section. Drafting does not establish release, deployment or
adoption.

The bot includes a pending-human-review marker. The README check rejects that
marker even though the file changed. A human reviews, edits, removes the marker
and commits; that human commit triggers ordinary CI on the final source.
GITHUB_TOKEN commits do not trigger ordinary PR CI, so the bot's commit alone is
not merge evidence. There is no auto-approval or auto-merge.

Repository rules must permit the workflow token to write the PR branch; branch
protections are never bypassed. Fork PRs, PRs already editing README, stale runs,
missing PR associations and CI reruns without a new requested event need manual
documentation. Hosted tests cover quoting, review-marker refusal, exact one-file
publication, preservation, stale heads, forks and non-force ref publication.

