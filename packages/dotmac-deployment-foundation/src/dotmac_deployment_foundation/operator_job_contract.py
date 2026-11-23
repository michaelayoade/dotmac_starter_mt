"""Cross-repository names the operator-job entry point depends on.

EVERY value in the PROVISIONAL section is a name owned by ANOTHER repository
whose contract was not final when this module was written:

* Control's v2 contract (branch ``control-a18-20261122``): the
  ``dispatched_job`` shape, ``inputs[{flag, path, sha256}]``, ``io`` and
  ``apply_class``;
* Platform CP 5b: the resolve-context key names and the ``run_job`` bindings
  methods (OQ-7 in the design);
* the deploy agent (#1143): the ``DOTMAC_DEPLOY_JOB*`` environment names and the
  render-unit naming.

They live here, and only here, so a final contract is a one-file re-pin. Nothing
else in Foundation spells these strings. ``PROVISIONAL_KEYS`` lists them for
the report and for the test that proves no other module hard-codes them.

The facility-owned half (labels, refusal codes, flags) is not provisional.
"""

from __future__ import annotations

from typing import Final

#: True until every name below is re-pinned against the final contracts.
PROVISIONAL: Final = True

# ── PROVISIONAL: Platform CP 5b resolve context (OQ-7) ──────────────────────
OPERATION_RUN_JOB: Final = "run_job"
RESOLVED_OPERATION: Final = "operation"
RESOLVED_DISPATCH_ID: Final = "dispatch_id"
RESOLVED_DISPATCHED_JOB: Final = "dispatched_job"
RESOLVED_IMAGE: Final = "image"
RESOLVED_JOB_BINDING_NAMES: Final = "job_binding_names"
RESOLVED_SECRETS_NAMESPACE: Final = "secrets_namespace"

# ── PROVISIONAL: Control v2 ``dispatched_job`` ──────────────────────────────
JOB_NAME: Final = "name"
JOB_ARGV: Final = "argv"
JOB_ROLE: Final = "role"
JOB_MATERIALS: Final = "materials"
JOB_PARAMS_ENV_DIGEST: Final = "params_env_digest"
JOB_INPUTS: Final = "inputs"
JOB_TIMEOUT_SECONDS: Final = "timeout_seconds"
JOB_OUTPUT_DIR: Final = "output_dir"
JOB_APPLY_CLASS: Final = "apply_class"
INPUT_FLAG: Final = "flag"
INPUT_PATH: Final = "path"
INPUT_SHA256: Final = "sha256"

# ── PROVISIONAL: Control plan image (planned_repository@planned_digest) ─────
IMAGE_REPOSITORY: Final = "planned_repository"
IMAGE_DIGEST: Final = "planned_digest"
IMAGE_SOURCE: Final = "source"
IMAGE_SOURCE_PRESENT: Final = "present"

# ── PROVISIONAL: deploy agent environment (#1143) ───────────────────────────
ENV_JOB: Final = "DOTMAC_DEPLOY_JOB"
ENV_ARGV_DIGEST: Final = "DOTMAC_DEPLOY_JOB_ARGV_DIGEST"
ENV_PARAMS_ENV: Final = "DOTMAC_DEPLOY_JOB_PARAMS_ENV"
ENV_SECRETS_DIR: Final = "DOTMAC_DEPLOY_JOB_SECRETS_DIR"
ENV_SECRET_FILES: Final = "DOTMAC_DEPLOY_JOB_SECRET_FILES"
ENV_RENDER_INVOCATION: Final = "DOTMAC_DEPLOY_JOB_RENDER_INVOCATION"
#: Absolute deadline, epoch seconds (P-SEC R6-1). Optional.
ENV_DEADLINE: Final = "DOTMAC_DEPLOY_JOB_DEADLINE"
#: The render unit name the agent started; must equal the one Foundation derives
#: from secrets_namespace, role and materials. Required when a role is set.
ENV_RENDER_UNIT: Final = "DOTMAC_DEPLOY_JOB_RENDER_UNIT"

#: PROVISIONAL (agent #1143 JOB_ONLY_NAMES): never in the params env or the
#: container environment, whatever the resolved context says.
JOB_ONLY_NAMES: Final[frozenset[str]] = frozenset(
    {"MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"}
)

# ── PROVISIONAL: bindings methods on the installed authority provider ───────
PROVIDER_RESOLVE_METHOD: Final = "resolve_run_job"
PROVIDER_CONSUME_METHOD: Final = "consume_run_job"
#: A consume refusal carrying either marker is a 409 already_consumed.
CONSUME_CONFLICT_STATUS: Final = 409
CONSUME_CONFLICT_CODE: Final = "already_consumed"
#: MANDATORY key of the mapping `consume_run_job` returns: the lease expiry
#: (epoch seconds) learned at consume (P-SEC rev7.1 R7-2). Absent refuses.
CONSUME_LEASE_EXPIRES: Final = "lease_expires_at"

# ── PROVISIONAL: the host-side, ROOT-OWNED enrolment file (never CP, never env) ─
#: Holds the job uid/user (RULING-agent-contract R2: the uid is never a
#: dispatch field) and the host root of the per-job input directories.
JOB_IDENTITY_PATH: Final = "/etc/dotmac/deploy-agent/job-identity.toml"
IDENTITY_JOB_UID: Final = "job_uid"
IDENTITY_JOB_USER: Final = "job_user"
IDENTITY_INPUTS_ROOT: Final = "inputs_root"

# ── PROVISIONAL: P-SEC render unit naming ───────────────────────────────────
RENDER_UNIT_TEMPLATE: Final = "openbao-agent@{ns}-{role}-{set_id}.service"
SECRETS_DIR_TEMPLATE: Final = "/run/dotmac/{ns}/{role}/secrets"

PROVISIONAL_KEYS: Final[tuple[str, ...]] = (
    "OPERATION_RUN_JOB",
    "RESOLVED_OPERATION",
    "RESOLVED_DISPATCH_ID",
    "RESOLVED_DISPATCHED_JOB",
    "RESOLVED_IMAGE",
    "RESOLVED_JOB_BINDING_NAMES",
    "RESOLVED_SECRETS_NAMESPACE",
    "JOB_NAME",
    "JOB_ARGV",
    "JOB_ROLE",
    "JOB_MATERIALS",
    "JOB_PARAMS_ENV_DIGEST",
    "JOB_INPUTS",
    "JOB_TIMEOUT_SECONDS",
    "JOB_OUTPUT_DIR",
    "JOB_APPLY_CLASS",
    "INPUT_FLAG",
    "INPUT_PATH",
    "INPUT_SHA256",
    "IMAGE_REPOSITORY",
    "IMAGE_DIGEST",
    "IMAGE_SOURCE",
    "IMAGE_SOURCE_PRESENT",
    "ENV_JOB",
    "ENV_ARGV_DIGEST",
    "ENV_PARAMS_ENV",
    "ENV_SECRETS_DIR",
    "ENV_SECRET_FILES",
    "ENV_RENDER_INVOCATION",
    "ENV_DEADLINE",
    "ENV_RENDER_UNIT",
    "JOB_IDENTITY_PATH",
    "IDENTITY_JOB_UID",
    "IDENTITY_JOB_USER",
    "IDENTITY_INPUTS_ROOT",
    "CONSUME_LEASE_EXPIRES",
    "JOB_ONLY_NAMES",
    "PROVIDER_RESOLVE_METHOD",
    "PROVIDER_CONSUME_METHOD",
    "CONSUME_CONFLICT_STATUS",
    "CONSUME_CONFLICT_CODE",
    "RENDER_UNIT_TEMPLATE",
    "SECRETS_DIR_TEMPLATE",
)

# ── facility-owned (not provisional) ────────────────────────────────────────
LABEL_DISPATCH_ID: Final = "dotmac.dispatch_id"
LABEL_JOB: Final = "dotmac.job"
#: Marks every operator-job container (value "1"); label-scoped removal
#: requires BOTH this and the dispatch id (P-SEC rev7).
LABEL_ROLE_JOB: Final = "dotmac.role_job"
RECEIPT_SCHEMA: Final = "dotmac.foundation.operator-job-receipt/1"

#: Compose network labels checked before connect (review ruling 5).
LABEL_COMPOSE_PROJECT: Final = "com.docker.compose.project"
LABEL_COMPOSE_NETWORK: Final = "com.docker.compose.network"
