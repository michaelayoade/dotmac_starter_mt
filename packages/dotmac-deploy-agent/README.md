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

## The target API contract is Control's

Deployment Control owns the wire bytes, paths, materials v1, env names and
vectors (`contract/target_api_v1.json`, ruling R1). This package vendors that file
byte-identical under `src/dotmac_deploy_agent/contract/` and pins its sha256;
`tests/unit/test_deploy_agent_conformance.py` fails until it is vendored. Keys and
signatures are unpadded base64url; the target path is three plain segments.

The agent acts only on a Control-signed, plan-bound dispatch (`dispatch.py`): the
params digest, images, legacy policy and lease TTL are read from the signed plan
binding, never from CP-supplied fields.

Secrets: no chown, no capability. ONE naming key, `secrets_namespace` (pinned in the
root-owned config; the plan must equal it), names every per-role artefact: the
`/run/dotmac/<secrets_namespace>/<role>` directory, the `openbao-agent@` unit, the
authored HCL and the drop-in. The agent's own tmpfs state has its own
RuntimeDirectory and never touches tmpfiles.d's role directories. `secret_id` is written 0400 as the agent uid and
read by the per-role OpenBao Agent unit through `LoadCredential`; the authored
OpenBao configs are root-installed and only checked and reported.

## Packaging

`deploy/systemd/dotmac-deploy-agent@.service` (instance = product, no
capabilities, tmpfs RuntimeDirectory, credentials only), `deploy/launcher.sh`
(root-owned release, pinned digest and Sigstore identity read from root-owned
files), `deploy/config.example.toml` and the enrolment kit templates under
`deploy/enrolment/` (per-role `openbao-agent@` unit and drop-in, `tmpfiles.d`,
and a polkit rule limited to enrolled instance names). The signed build and the pinned
registry binary and skopeo checksums are the separate WP9 release gate.

Nothing here is enabled, published or enrolled.
