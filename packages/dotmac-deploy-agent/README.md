# dotmac-deploy-agent

The host-side launcher of the build-once generic deploy design (DESIGN-rev3, L5).
It **launches only**. Control owns desired state and approvals; Foundation and the
CP bindings are the sole runner; OpenBao owns values.

Per cycle, outbound only: fetch signed materials, verify, render
`/run/dotmac/<product>/deploy/params.env` (0400, tmpfs) and refuse unless its
digest is the plan's, deliver wrapped `secret_id`s and OpenBao Agent configs,
apply the M11 legacy copy when policy allows, run Foundation with an allow-listed
environment, classify the file afterwards, delete it, and return an observation.
Observations are kept in a durable outbox until Control acknowledges them.

## Boundaries (pinned by `tests/architecture/test_deploy_agent_package.py`)

- Stdlib plus `cryptography`; no database driver, kernel, Foundation or Control import.
- No CP database credential, lease DSN or `PG*` in the environment (refused at start).
- No registry pull credential, no registry egress: images arrive by governed digest transfer.
- Secrets arrive only as systemd credentials; values are never logged or observed.

## Contract assumptions to align with CP (WP5b) and Control (WP2)

`wire.py`, `materials.py` and `params.py` hold every name and byte layout the
design leaves open (header names, signed-message layout, endpoint paths, the
materials schema, the sort rule that places `APP_IMAGE` among the other lines).
Each is pinned by a test vector so alignment is a one-file change.

## Packaging

`deploy/systemd/dotmac-deploy-agent@.service` (instance = product, no
capabilities, tmpfs RuntimeDirectory, credentials only), `deploy/launcher.sh`
(root-owned release, pinned digest and Sigstore identity read from root-owned
files) and `deploy/config.example.toml`. The signed build and the pinned
registry binary and skopeo checksums are the separate WP9 release gate.

Nothing here is enabled, published or enrolled.
