# Reference application install and upgrade qualification

**Baseline selected:** 2026-11-04, by the product owner in the M1 qualification
request. **Scope:** source compatibility, not a supported released-image upgrade.

No complete-reference-application release was identified in the repository's
release records or GitHub Releases. Package release tags do not release the
whole reference application. `VERSION` is a source declaration, and the image
digest in `deploy/product.toml` is a placeholder, not an installable artifact.

The owner selected the immutable pre-hardening PR #968 base,
`eec5d6bc765649a79947e30ae71b611c5d9764bb`, for the first compatibility test.
The selection is pinned in
[reference-upgrade-baseline.json](../../.github/reference-upgrade-baseline.json).
Neither this selection nor a successful test designates it a supported release.

## Required hosted exercise

The existing `docker-build` job in [CI](../../.github/workflows/ci.yml) builds the
candidate final image, then runs
[verify_reference_upgrade.py](../../scripts/verify_reference_upgrade.py) on the
same GitHub-hosted runner. The harness refuses other runner environments.

It builds the baseline from its exact archived Git tree, creates a private
run-owned Docker network and PostgreSQL instance, precreates the cluster roles,
and migrates as `app_admin`, never the online roles or PostgreSQL superuser.
Only the application port is published, on loopback with a random host port.
The online process receives separate tenant and platform credentials and no
migration credential. Synthetic passwords and signing keys are generated for
the run and excluded from the receipt.

The baseline image bootstraps a platform operator through its existing CLI
owner, creates two tenants and contacts through HTTP, and proves both tenants
work and remain isolated. The candidate then migrates the same retained
database and verifies readiness, exact contact data, fresh logins, existing
sessions, foreign-ID reads/deletes, wrong-tenant tokens and tenant-scoped reads
as the real `app_user`. New tenant provisioning survives a candidate restart.
Stopping PostgreSQL must produce readiness 503 while liveness stays 200;
restarting it must restore readiness 200. Finally the candidate installs on a
second empty database and repeats the tenant checks. Fresh and upgraded
databases must match the candidate's actual Alembic heads.

Migration execution is required even when the candidate adds no revisions.
The receipt records whether schema heads actually changed; a no-op upgrade
does not prove a future schema transformation or data backfill.

## Acceptance and retained evidence

Qualification is pending until the new hosted gate succeeds. Each run uploads
`reference-upgrade-<run id>-<attempt>` containing
`reference-upgrade-evidence.json`, including the baseline/candidate revisions,
PR head/base, Docker image IDs, applied heads, individual checks and cleanup
result. Qualification and cleanup must both pass. A failed exercise still
retains its partial receipt. Only resources generated for that run are removed.

Successful source-to-source qualification can close M1's initial reference
install/upgrade exercise under the owner's selected scope. It cannot close
compatibility with a previous published application image. Once a real image
release and support policy exist, add digest-bound released-image qualification
and retain this distinction. Production rollout, backup restoration and the
Foundation/Control execution programme remain separate gates.
