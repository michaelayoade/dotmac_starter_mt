"""The operator-job entry point: one dispatched job, one container, one run.

``dotmac-deploy run-job --execute`` runs ONE operator job that Control already
planned, authorized and dispatched. It is deliberately not a second executor
for deployments: it has no fence, backup, migration, switch, exposure or health
step, it never runs a compose service, and it never touches the deploy, rollback
or recovery paths. ``tests/architecture/test_foundation_operator_job_isolation``
proves those bytes unchanged.

Invariants (DESIGN S2 section 0):

* **Authority is the RESOLVED, CONSUMED dispatch.** The installed authority
  provider resolves the Control pair into a context, and this module requires
  ``operation == "run_job"`` and a ``dispatched_job`` in it. The agent's
  environment is a cross-check only: each value must EQUAL the resolved one.
* **One run per dispatch.** The dispatch is consumed before any container is
  created. A ``409 already_consumed`` is a REFUSAL (``job_already_consumed``),
  deliberately unlike deploy's continuation.
* **Exact container shape**, read back before start (``compose_operator_job``).
* **Never root, never a secret value.** This module reads no secret value and
  never lists a role's ``secrets/`` directory; the render unit's own verifier
  checks those files (DESIGN section 1).
* **Exit cleanup.** The container is removed on every exit path after creation.

Names that another repository owns are in ``operator_job_contract`` and are
marked PROVISIONAL there.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from . import operator_job_contract as contract
from .engine.plan import StepKind
from .errors import DeploymentFoundationError

if TYPE_CHECKING:
    from .engine.run import CommandResult
    from .execution_bindings import ExecutionBindings
    from .spec import ComposeJob, ProductDeploymentSpec

__all__ = [
    "OperatorJobAuthority",
    "OperatorJobEffects",
    "OperatorJobPair",
    "OperatorJobPlan",
    "OperatorJobReceipt",
    "OperatorJobRefusal",
    "RunJobConsumptionRequest",
    "WaitResult",
    "plan_operator_job",
    "run_operator_job",
]

EXIT_SUCCEEDED: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2
EXIT_TIMEOUT: Final = 3

MAX_ARGV: Final = 64
MAX_TIMEOUT_SECONDS: Final = 3600
MAX_INPUT_BYTES: Final = 256 * 1024 * 1024

_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JOB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MATERIAL = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ROLE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_FLAG = re.compile(r"^--[a-z0-9][a-z0-9-]{0,62}$")
_INPUT_PATH = re.compile(r"^/job/in/([A-Za-z0-9][A-Za-z0-9._-]{0,127})$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_INVOCATION = re.compile(r"^[0-9a-f]{32}$")
_PARAM_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")
_IMAGE_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._\-/:]*$")
_USER = re.compile(r"^(\d+):(\d+)$")


class OperatorJobRefusal(DeploymentFoundationError):
    """A stable-coded refusal; the code is the contract, the prose is not."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


# ── authority seam (PROVISIONAL, see operator_job_contract) ─────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorJobPair:
    """The exact Control pair presented to ``--authorization``, as bytes."""

    authorization_material_json: bytes
    dispatch_material_json: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class RunJobConsumptionRequest:
    dispatch_id: str
    job: str
    target: str
    authorization_material_json: bytes
    dispatch_material_json: bytes


@runtime_checkable
class OperatorJobAuthority(Protocol):
    """What the installed bindings' provider adds for ``run_job`` (PROVISIONAL).

    ``resolve_run_job`` verifies the pair and returns the resolved context.
    ``consume_run_job`` commits the dispatch; a 409 already_consumed surfaces as
    an exception carrying ``status == 409`` or ``code == "already_consumed"``.
    """

    def resolve_run_job(
        self,
        *,
        authorization_material: Mapping[str, Any],
        dispatch_material: Mapping[str, Any],
        target: str,
    ) -> Mapping[str, Any]: ...

    def consume_run_job(self, *, request: RunJobConsumptionRequest) -> None: ...


@dataclasses.dataclass(frozen=True, slots=True)
class WaitResult:
    exit_code: int | None
    timed_out: bool
    interrupted: bool = False


class OperatorJobEffects(Protocol):
    """Everything that touches the host. The engine below decides, never acts."""

    def environ(self) -> Mapping[str, str]: ...

    def now(self) -> datetime: ...

    def read_params_env(self, path: str) -> bytes: ...

    def docker_group_ids(self) -> tuple[frozenset[int], frozenset[int]]:
        """(member uids, group gids) of the host's docker group (P-SEC C-29)."""
        ...

    def systemctl_show(self, unit: str) -> Mapping[str, str]: ...

    def image_present(self, ref: str) -> bool: ...

    def inspect_image(self, ref: str) -> Mapping[str, Any]: ...

    def hash_input(self, path: str) -> str: ...

    def volume_exists(self, name: str) -> bool: ...

    def create_volume(self, name: str, *, dispatch_id: str) -> None: ...

    def create_container(
        self, plan: OperatorJobPlan, *, env_file: str | None
    ) -> CommandResult: ...

    def connect_networks(self, plan: OperatorJobPlan) -> CommandResult: ...

    def inspect_container(self, name: str) -> Mapping[str, Any]: ...

    def start_and_wait(self, name: str, *, timeout_seconds: int) -> WaitResult: ...

    def sigterm_trap(self) -> AbstractContextManager[None]:
        """Trap SIGTERM, SIGINT and SIGHUP; the handler only records it."""
        ...

    def terminate_requested(self) -> bool: ...

    def host_uid(self, account: str) -> int | None: ...

    def read_job_identity(self) -> Mapping[str, Any] | None:
        """The parsed root-owned enrolment file, or None when it is absent."""
        ...

    def stat_image_dir(
        self, image_ref: str, path: str, *, probe_name: str, dispatch_id: str
    ) -> tuple[int, int, int] | None:
        """(uid, gid, mode) of a directory in the IMAGE, via a throwaway container."""
        ...

    def network_labels(self, name: str) -> Mapping[str, str] | None: ...

    def remove_container(self, name: str, *, dispatch_id: str) -> bool: ...

    def list_dispatch_containers(self, dispatch_id: str) -> tuple[str, ...]: ...

    def remove_dispatch_containers(self, dispatch_id: str) -> tuple[str, ...]: ...


# ── the plan (pure) ─────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorJobStep:
    kind: StepKind
    name: str
    description: str


@dataclasses.dataclass(frozen=True, slots=True)
class SecretMount:
    source: str
    target: str


@dataclasses.dataclass(frozen=True, slots=True)
class InputMount:
    flag: str
    source: str
    target: str
    sha256: str


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorJobPlan:
    product: str
    dispatch_id: str
    name: str
    job_code: str
    argv: tuple[str, ...]
    role: str
    materials: tuple[str, ...]
    scoped_names: tuple[str, ...]
    secret_files: tuple[str, ...]
    params_env_digest: str
    timeout_seconds: int
    image_ref: str
    image_digest: str
    namespace: str
    secrets_dir: str
    render_unit: str
    secret_mounts: tuple[SecretMount, ...]
    input_mounts: tuple[InputMount, ...]
    outputs_target: str
    container_name: str
    volume_name: str
    networks: tuple[str, ...]
    user: str
    read_only_root: bool
    no_new_privileges: bool
    cap_drop: tuple[str, ...]
    tmpfs: tuple[str, ...]
    entrypoint: tuple[str, ...]
    job_uid: int | None = None
    job_user_name: str = ""
    network_codes: tuple[str, ...] = ()

    @property
    def steps(self) -> tuple[OperatorJobStep, ...]:
        kind = StepKind.OPERATOR_JOB
        return tuple(OperatorJobStep(kind, name, text) for name, text in _STEP_TEXT)


_STEP_TEXT: Final[tuple[tuple[str, str], ...]] = (
    ("consume", "consume the dispatch once; a replay is job_already_consumed"),
    ("cross_checks", "agent env must equal the resolved dispatched_job"),
    ("render_check", "render unit Result/ExecMainStatus/InvocationID, read-only"),
    ("input_digests", "recompute every input sha256 (no symlinks)"),
    ("output_volume", "create the per-dispatch output volume"),
    ("create", "docker create from the declared shape only"),
    ("inspect", "read back the container; refuse any other shape"),
    ("start_wait", "start, wait at most timeout_seconds, then stop and kill"),
    ("remove", "docker rm -f on every exit path"),
    ("receipt", "one-line JSON receipt of names, digests and ids"),
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def argv_digest(argv: Sequence[str]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(list(argv)).encode()).hexdigest()


def material_set_id(materials: Sequence[str]) -> str:
    names = sorted(m.lower() for m in materials)
    return hashlib.sha256(canonical_json(names).encode()).hexdigest()[:12]


def _norm_digest(value: str) -> str:
    return value.strip().lower().removeprefix("sha256:")


def _sanitise(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)


def _text(mapping: Mapping[str, Any], key: str, code: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise OperatorJobRefusal(code, f"{key} must be a string")
    return value


def _str_list(mapping: Mapping[str, Any], key: str, code: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, str) for item in value
    ):
        raise OperatorJobRefusal(code, f"{key} must be a list of strings")
    return tuple(value)


def _select_job(spec: ProductDeploymentSpec, name: str) -> ComposeJob:
    topology = spec.compose_topology
    found = (
        [
            job
            for job in topology.jobs
            if job.operator is not None and job.operator.command_for(name) is not None
        ]
        if topology is not None
        else []
    )
    if len(found) != 1:
        raise OperatorJobRefusal(
            "job_unknown", f"{len(found)} operator jobs declare {name!r}"
        )
    return found[0]


def plan_operator_job(
    spec: ProductDeploymentSpec,
    resolved: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
) -> OperatorJobPlan:
    """Pure. Every refusal here happens BEFORE the dispatch is consumed.

    ``identity`` is the parsed ROOT-OWNED host enrolment file (job uid, job
    user, inputs root). It never comes from the resolve context or the agent.
    """
    identity = identity or {}
    if resolved.get(contract.RESOLVED_OPERATION) != contract.OPERATION_RUN_JOB:
        raise OperatorJobRefusal(
            "job_operation_refused", "the resolved operation is not run_job"
        )
    dispatched = resolved.get(contract.RESOLVED_DISPATCHED_JOB)
    if not isinstance(dispatched, Mapping) or not dispatched:
        raise OperatorJobRefusal(
            "job_operation_refused", "run_job without a dispatched_job"
        )
    dispatch_id = _text(resolved, contract.RESOLVED_DISPATCH_ID, "job_context_invalid")
    if not _DISPATCH_ID.match(dispatch_id):
        raise OperatorJobRefusal("job_context_invalid", "dispatch_id shape")
    name = _text(dispatched, contract.JOB_NAME, "job_context_invalid")
    if not _JOB_NAME.match(name):
        raise OperatorJobRefusal("job_context_invalid", "job name shape")
    job = _select_job(spec, name)
    operator = job.operator
    prefix = operator.command_for(name) if operator is not None else None
    if operator is None or prefix is None:
        raise OperatorJobRefusal("job_unknown", "the selected job has no command")

    argv = _str_list(dispatched, contract.JOB_ARGV, "job_argv_refused")
    if not argv or len(argv) > MAX_ARGV or argv[: len(prefix)] != prefix:
        raise OperatorJobRefusal(
            "job_argv_refused", "argv is not the declared command plus arguments"
        )

    role = dispatched.get(contract.JOB_ROLE, "")
    if not isinstance(role, str) or (role and not _ROLE.match(role)):
        raise OperatorJobRefusal("job_context_invalid", "role shape")
    materials = _str_list(dispatched, contract.JOB_MATERIALS, "job_context_invalid")
    if any(not _MATERIAL.match(m) for m in materials) or len(set(materials)) != len(
        materials
    ):
        raise OperatorJobRefusal("job_context_invalid", "material names")
    binding_names = frozenset(
        _str_list(resolved, contract.RESOLVED_JOB_BINDING_NAMES, "job_context_invalid")
    )
    bound = tuple(sorted(m for m in materials if m in binding_names))
    scoped = tuple(sorted(m for m in materials if m not in binding_names))
    if set(scoped) & (contract.JOB_ONLY_NAMES | binding_names):
        raise OperatorJobRefusal("job_env_mismatch", "a job-only name is scoped")
    if set(job.materials) - binding_names - set(scoped):
        raise OperatorJobRefusal(
            "job_materials_mismatch", "the spec declares env the dispatch lacks"
        )
    secret_files = tuple(sorted(m.lower() for m in bound))
    if secret_files and not role:
        raise OperatorJobRefusal("job_context_invalid", "secrets without a role")

    output_dir = dispatched.get(contract.JOB_OUTPUT_DIR)
    if output_dir is not None and output_dir != operator.outputs_target:
        raise OperatorJobRefusal(
            "job_context_invalid", "output_dir is not the declared outputs target"
        )
    params_digest = dispatched.get(contract.JOB_PARAMS_ENV_DIGEST, "")
    if not isinstance(params_digest, str):
        raise OperatorJobRefusal("job_context_invalid", "params_env_digest")
    timeout = dispatched.get(contract.JOB_TIMEOUT_SECONDS)
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise OperatorJobRefusal("job_context_invalid", "timeout_seconds")

    image = resolved.get(contract.RESOLVED_IMAGE)
    if not isinstance(image, Mapping):
        raise OperatorJobRefusal("job_image_refused", "no plan image")
    repository = _text(image, contract.IMAGE_REPOSITORY, "job_image_refused")
    digest = _text(image, contract.IMAGE_DIGEST, "job_image_refused")
    if (
        image.get(contract.IMAGE_SOURCE) != contract.IMAGE_SOURCE_PRESENT
        or not _IMAGE_REPOSITORY.match(repository)
        or not _SHA256.match(digest)
    ):
        raise OperatorJobRefusal("job_image_refused", "image must be present by digest")

    _require_declared_shape(job)
    pinned_uid = identity.get(contract.IDENTITY_JOB_UID)
    job_user_name = identity.get(contract.IDENTITY_JOB_USER, "")
    if not isinstance(job_user_name, str):
        raise OperatorJobRefusal("job_context_invalid", "job_user shape")
    if role and (pinned_uid is None or not job_user_name):
        raise OperatorJobRefusal(
            "job_context_invalid",
            "a role job needs job_uid and job_user from the host identity file",
        )
    job_uid: int | None = None
    if pinned_uid is not None:
        if (
            isinstance(pinned_uid, bool)
            or not isinstance(pinned_uid, int)
            or job.security.user != f"{pinned_uid}:{pinned_uid}"
        ):
            raise OperatorJobRefusal(
                "job_uid_mismatch", "the spec user is not the pinned uid:uid"
            )
        job_uid = int(pinned_uid)
    if job_user_name and job_uid is None:
        raise OperatorJobRefusal("job_context_invalid", "job_user without job_uid")
    network_codes = tuple(job.placement.networks)
    networks = tuple(f"{spec.product}_{code}" for code in network_codes)

    namespace = ""
    secrets_dir = ""
    render_unit = ""
    if role:
        namespace = _text(
            resolved, contract.RESOLVED_SECRETS_NAMESPACE, "job_context_invalid"
        )
        if not _NAMESPACE.match(namespace):
            raise OperatorJobRefusal("job_context_invalid", "namespace shape")
        secrets_dir = contract.SECRETS_DIR_TEMPLATE.format(ns=namespace, role=role)
        render_unit = contract.RENDER_UNIT_TEMPLATE.format(
            ns=namespace, role=role, set_id=material_set_id(bound)
        )

    secret_mounts = tuple(
        SecretMount(
            source=f"{secrets_dir}/{m.lower()}",
            target=f"{operator.secrets_target}/{m}",
        )
        for m in bound
    )
    input_mounts = _plan_inputs(identity, dispatched, name)

    sid = _sanitise(dispatch_id)
    return OperatorJobPlan(
        product=spec.product,
        dispatch_id=dispatch_id,
        name=name,
        job_code=job.code,
        argv=argv,
        role=role,
        materials=materials,
        scoped_names=scoped,
        secret_files=secret_files,
        params_env_digest=params_digest,
        timeout_seconds=min(job.timeout_seconds, timeout),
        image_ref=f"{repository}@{digest}",
        image_digest=digest,
        namespace=namespace,
        secrets_dir=secrets_dir,
        render_unit=render_unit,
        secret_mounts=secret_mounts,
        input_mounts=input_mounts,
        outputs_target=operator.outputs_target,
        container_name=f"dotmac-job-{_sanitise(namespace or spec.product)}-{sid}",
        volume_name=f"dotmac-job-out-{sid}",
        networks=networks,
        network_codes=network_codes,
        user=job.security.user,
        read_only_root=job.security.read_only_root,
        no_new_privileges=job.security.no_new_privileges,
        cap_drop=job.security.cap_drop,
        tmpfs=job.security.tmpfs,
        entrypoint=job.entrypoint,
        job_uid=job_uid,
        job_user_name=job_user_name,
    )


def _require_declared_shape(job: ComposeJob) -> None:
    """Refuse every spec shape this entry point cannot build exactly."""
    match = _USER.match(job.security.user)
    if match is None or int(match.group(1)) == 0 or int(match.group(2)) == 0:
        raise OperatorJobRefusal("job_shape_refused", "an operator job is never root")
    if job.security.exceptions:
        raise OperatorJobRefusal(
            "job_shape_refused", "security exceptions are not supported"
        )
    if job.volumes or job.placement.mounts:
        raise OperatorJobRefusal(
            "job_mount_unsupported", "spec-declared mounts are not supported"
        )
    if len(job.entrypoint) > 1:
        raise OperatorJobRefusal(
            "job_entrypoint_unsupported",
            "the Docker CLI cannot express a multi-word entrypoint exactly",
        )
    if not job.placement.networks:
        raise OperatorJobRefusal("job_shape_refused", "networks must be declared")


def _plan_inputs(
    identity: Mapping[str, Any],
    dispatched: Mapping[str, Any],
    name: str,
) -> tuple[InputMount, ...]:
    raw = dispatched.get(contract.JOB_INPUTS, [])
    if not isinstance(raw, list | tuple):
        raise OperatorJobRefusal("job_input_refused", "inputs must be a list")
    if not raw:
        return ()
    root = identity.get(contract.IDENTITY_INPUTS_ROOT)
    if (
        not isinstance(root, str)
        or not root.startswith("/")
        or ".." in PurePosixPath(root).parts
    ):
        raise OperatorJobRefusal(
            "job_input_refused", "inputs root is not an absolute path"
        )
    mounts: list[InputMount] = []
    flags: set[str] = set()
    targets: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise OperatorJobRefusal("job_input_refused", "input shape")
        flag = _text(item, contract.INPUT_FLAG, "job_input_refused")
        path = _text(item, contract.INPUT_PATH, "job_input_refused")
        sha = _text(item, contract.INPUT_SHA256, "job_input_refused")
        found = _INPUT_PATH.match(path)
        if (
            not _FLAG.match(flag)
            or found is None
            or not _SHA256.match(sha)
            or flag in flags
            or path in targets
        ):
            raise OperatorJobRefusal(
                "job_input_refused", "input outside /job/in or malformed"
            )
        flags.add(flag)
        targets.add(path)
        mounts.append(
            InputMount(
                flag=flag,
                source=f"{root.rstrip('/')}/{name}/in/{found.group(1)}",
                target=path,
                sha256=sha,
            )
        )
    return tuple(sorted(mounts, key=lambda m: m.flag))


# ── the receipt ─────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class OperatorJobReceipt:
    dispatch_id: str
    job: str
    role: str
    exit_code: int | None
    outcome: str
    reason: str
    started_at: str
    finished_at: str
    inputs: tuple[tuple[str, str], ...]
    output_volume: str
    render_invocation: str
    container_removed: bool

    @property
    def exit_status(self) -> int:
        if self.outcome == "succeeded":
            return EXIT_SUCCEEDED
        if self.outcome == "failed":
            return EXIT_FAILED
        if self.outcome in ("runner_timeout", "interrupted"):
            return EXIT_TIMEOUT
        return EXIT_REFUSED

    def to_document(self) -> dict[str, Any]:
        return {
            "schema": contract.RECEIPT_SCHEMA,
            "dispatch_id": self.dispatch_id,
            "job": self.job,
            "role": self.role,
            "exit_code": self.exit_code,
            "outcome": self.outcome,
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "inputs": [{"flag": f, "sha256": s} for f, s in self.inputs],
            "output_volume": self.output_volume,
            "render_invocation": self.render_invocation,
            "container_removed": self.container_removed,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_document(), sort_keys=True, separators=(",", ":"))


# ── the run ─────────────────────────────────────────────────────────────────


def _authority(bindings: Any) -> Any:
    """The installed provider, only if it implements EVERY run_job method."""
    provider = getattr(bindings, "authorization_v3_provider", None)
    for method in (contract.PROVIDER_RESOLVE_METHOD, contract.PROVIDER_CONSUME_METHOD):
        if provider is None or not callable(getattr(provider, method, None)):
            raise OperatorJobRefusal(
                "job_authority_absent", f"the installed bindings lack {method}"
            )
    return provider


def _consume(
    plan: OperatorJobPlan,
    bindings: ExecutionBindings,
    pair: OperatorJobPair,
    target: str,
) -> int | None:
    """Consume once. Returns the lease expiry learned at consume, if any."""
    provider = _authority(bindings)
    request = RunJobConsumptionRequest(
        dispatch_id=plan.dispatch_id,
        job=plan.name,
        target=target,
        authorization_material_json=pair.authorization_material_json,
        dispatch_material_json=pair.dispatch_material_json,
    )
    try:
        learned = getattr(provider, contract.PROVIDER_CONSUME_METHOD)(request=request)
    except OperatorJobRefusal:
        raise
    except Exception as exc:
        if (
            getattr(exc, "status", None) == contract.CONSUME_CONFLICT_STATUS
            or getattr(exc, "status_code", None) == contract.CONSUME_CONFLICT_STATUS
            or getattr(exc, "code", None) == contract.CONSUME_CONFLICT_CODE
        ):
            raise OperatorJobRefusal(
                "job_already_consumed", "this dispatch was already consumed"
            ) from exc
        raise OperatorJobRefusal(
            "job_consume_refused", f"consume refused ({type(exc).__name__})"
        ) from exc
    if isinstance(learned, Mapping):
        expiry = learned.get(contract.CONSUME_LEASE_EXPIRES)
        if isinstance(expiry, int) and not isinstance(expiry, bool):
            return expiry
    return None


def _cross_check(plan: OperatorJobPlan, effects: OperatorJobEffects) -> str | None:
    """Agent env must EQUAL the resolved values. Returns the params env path."""
    uids, gids = effects.docker_group_ids()
    user = _USER.match(plan.user)
    if user is None or int(user.group(1)) in uids or int(user.group(2)) in gids:
        raise OperatorJobRefusal(
            "job_uid_in_docker_group", "the job uid or gid can reach the docker socket"
        )
    if plan.job_user_name and effects.host_uid(plan.job_user_name) != plan.job_uid:
        raise OperatorJobRefusal(
            "job_uid_mismatch", "the host account uid is not the pinned uid"
        )
    env = effects.environ()

    def need(key: str, wanted: str, code: str = "job_env_mismatch") -> None:
        if env.get(key, "") != wanted:
            raise OperatorJobRefusal(code, f"{key} differs from the resolved dispatch")

    need(contract.ENV_JOB, plan.name)
    need(contract.ENV_ARGV_DIGEST, argv_digest(plan.argv))
    need(contract.ENV_SECRET_FILES, ",".join(plan.secret_files))
    if plan.role:
        need(contract.ENV_SECRETS_DIR, plan.secrets_dir)
        if not _INVOCATION.match(env.get(contract.ENV_RENDER_INVOCATION, "")):
            raise OperatorJobRefusal(
                "job_env_mismatch", "render invocation id is absent or malformed"
            )
    if not plan.params_env_digest and not plan.scoped_names:
        return None
    if not plan.params_env_digest:
        raise OperatorJobRefusal("job_env_mismatch", "scoped params without a digest")
    path = env.get(contract.ENV_PARAMS_ENV, "")
    if not path:
        raise OperatorJobRefusal("job_env_mismatch", "no params env file")
    data = effects.read_params_env(path)
    if hashlib.sha256(data).hexdigest() != _norm_digest(plan.params_env_digest):
        raise OperatorJobRefusal("job_env_mismatch", "params env digest differs")
    names: list[str] = []
    for line in data.decode("utf-8", "strict").split("\n"):
        if not line:
            continue
        found = _PARAM_LINE.match(line)
        if found is None:
            raise OperatorJobRefusal("job_env_mismatch", "malformed params env line")
        names.append(found.group(1))
    if sorted(names) != list(plan.scoped_names) or set(names) & contract.JOB_ONLY_NAMES:
        raise OperatorJobRefusal("job_env_mismatch", "params env names differ")
    return path


def _render_check(plan: OperatorJobPlan, effects: OperatorJobEffects) -> str:
    if not plan.role:
        return ""
    env = effects.environ()
    if env.get(contract.ENV_RENDER_UNIT, "") != plan.render_unit:
        raise OperatorJobRefusal(
            "job_material_set_unknown",
            "the agent's render unit is not the one derived from the material set",
        )
    invocation = env.get(contract.ENV_RENDER_INVOCATION, "")
    shown = effects.systemctl_show(plan.render_unit)
    if shown.get("Result") != "success" or shown.get("ExecMainStatus") != "0":
        raise OperatorJobRefusal("job_render_failed", "render unit did not succeed")
    if shown.get("InvocationID") != invocation:
        raise OperatorJobRefusal(
            "job_render_stale", "render invocation is not this run's"
        )
    return invocation


def _iso(effects: OperatorJobEffects) -> str:
    return effects.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Terminated(Exception):
    """A trapped signal was received; unwind to the cleanup and write the receipt."""


def _check_terminated(effects: OperatorJobEffects) -> None:
    if effects.terminate_requested():
        raise _Terminated


#: Seconds the stop grace and the rm may take, and the least run time worth
#: starting. A deadline with less than the sum of the three is `invalid`.
STOP_GRACE_SECONDS: Final = 60
RM_BUDGET_SECONDS: Final = 30
MIN_RUN_SECONDS: Final = 30
DEADLINE_HEADROOM_SECONDS: Final = STOP_GRACE_SECONDS + RM_BUDGET_SECONDS


def _agent_deadline(effects: OperatorJobEffects) -> int:
    """The agent's absolute deadline. Missing, malformed, past or short refuses."""
    raw = effects.environ().get(contract.ENV_DEADLINE, "")
    if not raw.isdigit():
        raise OperatorJobRefusal(
            "job_deadline_invalid", "the agent deadline is missing or malformed"
        )
    deadline = int(raw)
    if deadline - int(effects.now().timestamp()) < (
        DEADLINE_HEADROOM_SECONDS + MIN_RUN_SECONDS
    ):
        raise OperatorJobRefusal(
            "job_deadline_invalid", "the agent deadline is past or too short"
        )
    return deadline


def _effective_timeout(
    plan: OperatorJobPlan, deadline: int, effects: OperatorJobEffects
) -> int:
    """min(spec, signed timeout, deadline - stop grace - rm budget)."""
    remaining = deadline - int(effects.now().timestamp()) - DEADLINE_HEADROOM_SECONDS
    if remaining <= 0:
        raise OperatorJobRefusal("job_deadline_invalid", "no time left to run safely")
    return min(plan.timeout_seconds, remaining)


def remove_dispatch(effects: OperatorJobEffects, dispatch_id: str) -> tuple[str, ...]:
    """Label-scoped removal of ONE dispatch's containers. Never creates or starts."""
    if not _DISPATCH_ID.match(dispatch_id):
        raise OperatorJobRefusal("job_context_invalid", "dispatch_id shape")
    return effects.remove_dispatch_containers(dispatch_id)


def _check_networks(plan: OperatorJobPlan, effects: OperatorJobEffects) -> None:
    """Each network must be the compose project's own (review ruling 5)."""
    for code, name in zip(plan.network_codes, plan.networks, strict=True):
        labels = effects.network_labels(name)
        if (
            labels is None
            or labels.get(contract.LABEL_COMPOSE_PROJECT) != plan.product
            or labels.get(contract.LABEL_COMPOSE_NETWORK) != code
        ):
            raise OperatorJobRefusal(
                "job_shape_refused", f"network {code} is not the compose project's"
            )


def _check_out_dir_owner(plan: OperatorJobPlan, effects: OperatorJobEffects) -> None:
    """The IMAGE's /job/out must be owned by the job uid:gid, mode 0700 (R6-3)."""
    match = _USER.match(plan.user)
    if match is None:
        raise OperatorJobRefusal("job_shape_refused", "unreadable job user")
    owner = effects.stat_image_dir(
        plan.image_ref,
        plan.outputs_target,
        probe_name=f"{plan.container_name}-probe",
        dispatch_id=plan.dispatch_id,
    )
    if owner != (int(match.group(1)), int(match.group(2)), 0o700):
        raise OperatorJobRefusal(
            "job_shape_refused", "the image's /job/out is not owned by the job user"
        )


def run_operator_job(
    plan: OperatorJobPlan,
    effects: OperatorJobEffects,
    bindings: ExecutionBindings,
    *,
    pair: OperatorJobPair,
    target: str,
) -> OperatorJobReceipt:
    """Consume once, then run the nine steps. Never raises a refusal."""
    from .providers.compose_operator_job import verify_container_shape

    started = _iso(effects)
    outcome = "refused"
    reason = ""
    exit_code: int | None = None
    invocation = ""
    create_attempted = False
    create_ok = False
    removed = True
    with effects.sigterm_trap():
        try:
            deadline = _agent_deadline(effects)
            # Pure or read-only checks first: a mis-invocation must not spend
            # an approved dispatch (review F-E).
            env_file = _cross_check(plan, effects)
            invocation = _render_check(plan, effects)
            lease_expiry = _consume(plan, bindings, pair, target)
            if (
                lease_expiry is None
                or lease_expiry <= int(effects.now().timestamp())
                or deadline > lease_expiry
            ):
                raise OperatorJobRefusal(
                    "job_deadline_invalid",
                    "no usable lease expiry at consume, or the deadline is later",
                )
            _check_terminated(effects)
            for mount in plan.input_mounts:
                if effects.hash_input(mount.source) != _norm_digest(mount.sha256):
                    raise OperatorJobRefusal(
                        "job_input_digest_mismatch", f"input {mount.flag} differs"
                    )
            if not effects.image_present(plan.image_ref):
                raise OperatorJobRefusal("job_image_absent", "image is not on the host")
            _check_networks(plan, effects)
            _check_out_dir_owner(plan, effects)
            if effects.volume_exists(plan.volume_name):
                raise OperatorJobRefusal("job_output_exists", plan.volume_name)
            _check_terminated(effects)
            effects.create_volume(plan.volume_name, dispatch_id=plan.dispatch_id)
            create_attempted = True
            created = effects.create_container(plan, env_file=env_file)
            create_ok = created.ok
            if not created.ok:
                raise OperatorJobRefusal("job_create_failed", "docker create refused")
            connected = effects.connect_networks(plan)
            if not connected.ok:
                raise OperatorJobRefusal("job_create_failed", "network connect refused")
            verify_container_shape(
                plan,
                effects.inspect_container(plan.container_name),
                effects.inspect_image(plan.image_ref),
            )
            _check_terminated(effects)
            waited = effects.start_and_wait(
                plan.container_name,
                timeout_seconds=_effective_timeout(plan, deadline, effects),
            )
            exit_code = waited.exit_code
            if waited.interrupted:
                outcome, reason = "interrupted", "interrupted"
            elif waited.timed_out:
                outcome, reason = "runner_timeout", "runner_timeout"
            elif exit_code == 0:
                outcome, reason = "succeeded", ""
            else:
                outcome, reason = "failed", "job_exit_nonzero"
        except _Terminated:
            outcome, reason = "interrupted", "interrupted"
        except OperatorJobRefusal as refusal:
            outcome, reason = "refused", refusal.code
        except (OSError, DeploymentFoundationError):
            outcome, reason = "refused", "job_effect_failed"
        finally:
            if create_attempted:
                removed = (
                    effects.remove_container(
                        plan.container_name, dispatch_id=plan.dispatch_id
                    )
                    or not create_ok
                )
    return OperatorJobReceipt(
        dispatch_id=plan.dispatch_id,
        job=plan.name,
        role=plan.role,
        exit_code=exit_code,
        outcome=outcome,
        reason=reason,
        started_at=started,
        finished_at=_iso(effects),
        inputs=tuple((m.flag, m.sha256) for m in plan.input_mounts),
        output_volume=plan.volume_name if create_attempted else "",
        render_invocation=invocation,
        container_removed=removed,
    )


# ── CLI handler (registered by cli.py) ──────────────────────────────────────


def _refusal_receipt(code: str) -> str:
    return json.dumps(
        {"schema": contract.RECEIPT_SCHEMA, "outcome": "refused", "reason": code},
        sort_keys=True,
        separators=(",", ":"),
    )


def cmd_run_job(args: Any) -> int:
    """``dotmac-deploy run-job``: preflight list, or --execute through bindings."""
    from .cli import (
        EXIT_OK,
        _load,
        _load_bindings,
        _require_execution_arguments,
    )

    spec = _load(args.descriptor)
    if not args.execute:
        sys.stdout.write("run-job preflight (nothing executed):\n")
        for step in _preflight_steps():
            sys.stdout.write(f"  {step.name}: {step.description}\n")
        sys.stdout.write("DRY RUN. Re-run with --execute to run a dispatched job.\n")
        return EXIT_OK
    try:
        target, path = _require_execution_arguments(args, contract.OPERATION_RUN_JOB)
        bindings = _load_bindings(args)
        provider = _authority(bindings)
        from pathlib import Path

        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) != {
            "authorization_material",
            "dispatch_material",
        }:
            raise OperatorJobRefusal("job_authorization_refused", "pair shape")
        resolved = getattr(provider, contract.PROVIDER_RESOLVE_METHOD)(
            authorization_material=document["authorization_material"],
            dispatch_material=document["dispatch_material"],
            target=target,
        )
        from .providers.compose_operator_job import ComposeOperatorJobEffects
        from .toolchain import DEFAULT_TOOLS, resolve_tool

        effects = ComposeOperatorJobEffects(
            docker_bin=resolve_tool(
                getattr(args, "docker_bin", "") or DEFAULT_TOOLS["docker"],
                what="docker_bin",
            ),
            systemctl_bin=getattr(args, "systemctl_bin", "") or "/usr/bin/systemctl",
        )
        plan = plan_operator_job(spec, resolved, effects.read_job_identity())
        pair = OperatorJobPair(
            authorization_material_json=canonical_json(
                document["authorization_material"]
            ).encode(),
            dispatch_material_json=canonical_json(
                document["dispatch_material"]
            ).encode(),
        )
    except (OperatorJobRefusal, DeploymentFoundationError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OperatorJobRefusal) else "job_refused"
        sys.stdout.write(_refusal_receipt(code) + "\n")
        return EXIT_REFUSED
    receipt = run_operator_job(plan, effects, bindings, pair=pair, target=target)
    sys.stdout.write(receipt.to_json() + "\n")
    return receipt.exit_status


def cmd_remove_job(args: Any) -> int:
    """``dotmac-deploy remove-job --dispatch-id ID [--execute ...]``.

    Label-scoped: only containers carrying BOTH ``dotmac.dispatch_id=<id>`` and
    the job marker. It never creates or starts a container. Without
    ``--execute`` it lists the matches and removes nothing. With it, the signed
    pair passed in ``--authorization`` must resolve to THIS dispatch id.
    """
    from .cli import _load_bindings, _require_execution_arguments
    from .providers.compose_operator_job import ComposeOperatorJobEffects
    from .toolchain import DEFAULT_TOOLS, resolve_tool

    try:
        effects = ComposeOperatorJobEffects(
            docker_bin=resolve_tool(
                getattr(args, "docker_bin", "") or DEFAULT_TOOLS["docker"],
                what="docker_bin",
            )
        )
        if not _DISPATCH_ID.match(args.dispatch_id):
            raise OperatorJobRefusal("job_context_invalid", "dispatch_id shape")
        if not args.execute:
            document: dict[str, Any] = {
                "dispatch_id": args.dispatch_id,
                "matches": sorted(effects.list_dispatch_containers(args.dispatch_id)),
                "removed": [],
            }
        else:
            target, path = _require_execution_arguments(
                args, contract.OPERATION_RUN_JOB
            )
            provider = _authority(_load_bindings(args))
            from pathlib import Path

            pair = json.loads(Path(path).read_text(encoding="utf-8"))
            resolved = getattr(provider, contract.PROVIDER_RESOLVE_METHOD)(
                authorization_material=pair["authorization_material"],
                dispatch_material=pair["dispatch_material"],
                target=target,
            )
            if resolved.get(contract.RESOLVED_DISPATCH_ID) != args.dispatch_id:
                raise OperatorJobRefusal(
                    "job_context_invalid", "the signed pair is for another dispatch"
                )
            document = {
                "dispatch_id": args.dispatch_id,
                "matches": [],
                "removed": sorted(remove_dispatch(effects, args.dispatch_id)),
            }
    except (DeploymentFoundationError, OSError, ValueError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, OperatorJobRefusal) else "job_refused"
        sys.stdout.write(_refusal_receipt(code) + "\n")
        return EXIT_REFUSED
    sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    return EXIT_SUCCEEDED


def _preflight_steps() -> tuple[OperatorJobStep, ...]:
    return tuple(
        OperatorJobStep(StepKind.OPERATOR_JOB, name, text) for name, text in _STEP_TEXT
    )
