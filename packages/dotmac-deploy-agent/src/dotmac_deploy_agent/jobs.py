"""Job roles (P-SEC rev4 3.14; REVIEW-import-jobs; REVIEW-agent-PR1143 r3): policy.

`deploy_binding.jobs` is a list of DEFINITIONS carried by every plan: nothing runs
from it. A job runs only when the signed statement's operation is `run_job` and
the plan's `dispatched_job` names it, and everything executed (role, env, argv,
timeout, window) is read from `dispatched_job` alone.

A job role's `secret_id` is minted ONLY for that dispatched job: never on a serving
deploy, at boot or from a timer. It is tracked from unwrap, purged on every exit
path and at agent start, and never sealed or restored. The job env is built only
from the signed `dispatched_job.params` (`{MATERIAL: value}`), never from CP's
unsigned params, and never enters `params.env`.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from .canon import canonical_json, digest
from .params import Param, check_value
from .refusal import Refusal
from .wire import parse_stamp

#: Credentials that belong to a dispatched job and to nothing else (Control
#: `JOB_ONLY_MATERIALS`). Never a serving role's, never sealed, never a plain param.
JOB_ONLY_NAMES: Final = frozenset({"MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"})
_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_MATERIAL: Final = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
RUN_JOB: Final = "run_job"
LEASE_MARGIN: Final = 120
#: R6-1: after Foundation's own deadline it needs time to stop and remove the job
#: container and write its receipt; only then does the agent send SIGTERM, and it
#: waits `JOB_TERM_GRACE` more before any SIGKILL. Both fit inside the lease margin.
JOB_STOP_MARGIN: Final = 45
JOB_TERM_GRACE: Final = 15
#: Seconds kept at the end of the lease for the purge, the accessor destroy and the
#: receipt. The deadline always binds: Foundation is stopped no later than
#: `lease end - (stop margin + grace + reserve)`, whatever `timeout_seconds` says.
JOB_TEARDOWN_RESERVE: Final = 40
#: The labels Foundation sets on a job container. The marker is `dotmac.role_job=1`
#: (proposed to Foundation; relay any change): one constant, never inlined.
JOB_DISPATCH_LABEL: Final = "dotmac.dispatch_id"
JOB_MARKER_LABEL: Final = "dotmac.role_job=1"
#: Outer bound only; the real bound is the signed lease fit (timeout + margin).
MAX_JOB_TIMEOUT: Final = 3600 - LEASE_MARGIN
_DISPATCHED_KEYS: Final = frozenset(
    {
        "name", "role", "single_use", "materials", "params", "params_env_digest",
        "argv", "timeout_seconds", "window", "inputs", "output_dir", "apply_class",
    }
)  # fmt: skip
#: Every `run_job` plan carries these (Control v2 at 2370860); none is optional.
OUTPUT_DIR: Final = "/job/out"
_JOB_IN: Final = re.compile(r"/job/in/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_JOB_OUT: Final = re.compile(r"/job/out/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FLAG: Final = re.compile(r"--[a-z][a-z0-9-]*")
_SHA256: Final = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class JobDefinition:
    """A descriptor job as every plan lists it: names only, nothing runs from it."""

    name: str
    role: str | None
    materials: tuple[str, ...]
    params: tuple[str, ...]
    #: RQ-3: what makes a dispatch apply-class, from the plan's closed arg list:
    #: the command itself applies, or an `apply_mode` bool flag is present, or an
    #: enum flag takes one of its `apply_choices`.
    apply_command: bool = False
    apply_args: tuple[tuple[str, bool, tuple[str, ...]], ...] = ()


def _apply_args(
    row: Mapping[str, object],
) -> tuple[tuple[str, bool, tuple[str, ...]], ...]:
    args = row.get("args", [])
    if not isinstance(args, list):
        raise Refusal("materials_invalid")
    found: list[tuple[str, bool, tuple[str, ...]]] = []
    for a in args:
        if not isinstance(a, dict) or not isinstance(a.get("flag"), str):
            continue  # other keys are Control's; only these three matter here
        choices = a.get("apply_choices", [])
        if not _FLAG.fullmatch(a["flag"]) or not (
            isinstance(choices, list) and all(isinstance(c, str) for c in choices)
        ):
            raise Refusal("materials_invalid")
        found.append((a["flag"], a.get("apply_mode") is True, tuple(choices)))
    return tuple(sorted(found))


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(n, str) and _NAME.fullmatch(n) for n in value
    ):
        raise Refusal("materials_invalid")
    return tuple(value)


def plan_jobs(snapshot: Mapping[str, object]) -> tuple[JobDefinition, ...]:
    """`deploy_binding.jobs` as definitions. Only the fields the agent uses are
    read (name, role, materials, params); other keys Control adds are ignored, so
    a new definition field cannot refuse every plan."""
    binding = snapshot.get("deploy_binding")
    rows = binding.get("jobs", []) if isinstance(binding, dict) else None
    if not isinstance(rows, list):
        raise Refusal("materials_invalid")
    jobs: list[JobDefinition] = []
    for row in rows:
        if not isinstance(row, dict):
            raise Refusal("materials_invalid")
        role = row.get("role")
        if (
            not isinstance(row.get("name"), str)
            or not _NAME.fullmatch(row["name"])
            or not (
                role is None or (isinstance(role, str) and _SEGMENT.fullmatch(role))
            )
        ):
            raise Refusal("materials_invalid")
        jobs.append(
            JobDefinition(
                row["name"],
                role,
                _names(row.get("materials", [])),
                _names(row.get("params", [])),
                row.get("apply_command") is True,
                _apply_args(row),
            )
        )
    if len({j.name for j in jobs}) != len(jobs):
        raise Refusal("materials_invalid")
    return tuple(jobs)


@dataclass(frozen=True)
class DispatchedJob:
    """The one job a `run_job` plan runs (`deploy_binding.dispatched_job`)."""

    name: str
    role: str | None
    single_use: bool | None
    materials: tuple[str, ...]
    #: Signed `{MATERIAL: value}`, sorted by material.
    env: tuple[tuple[str, str], ...]
    params_env_digest: str
    argv: tuple[str, ...]
    timeout_seconds: int
    window_id: str | None
    closes_at: datetime | None
    #: `(flag, in-container path, sha256:<hex>)` of every input, sorted by flag, from
    #: the signed plan (`dispatched_job.inputs`). Outputs carry no digest.
    inputs: tuple[tuple[str, str, str], ...] = ()
    #: Control's apply-class for this dispatch (None until the contract carries it).
    apply_class: bool | None = None

    @property
    def argv_digest(self) -> str:
        return digest(canonical_json(list(self.argv)))


def plan_dispatched_job(
    binding: Mapping[str, object], operation: str
) -> DispatchedJob | None:
    """Operation and `dispatched_job` must agree: `run_job` needs an object, any
    other operation needs null (or no key). Everything is read from the signed plan."""
    raw = binding.get("dispatched_job")
    if operation != RUN_JOB:
        if raw is not None:
            raise Refusal("materials_invalid", "dispatched_job_unexpected")
        return None
    if (
        not isinstance(raw, dict)
        or set(raw) != _DISPATCHED_KEYS  # exactly: a stray or missing key is refused
    ):
        raise Refusal("materials_invalid", "dispatched_job_missing")
    role, single_use = raw["role"], raw["single_use"]
    argv, timeout, window = raw["argv"], raw["timeout_seconds"], raw["window"]
    params = raw["params"]
    if (
        not isinstance(raw["name"], str)
        or not _NAME.fullmatch(raw["name"])
        or not (role is None or (isinstance(role, str) and _SEGMENT.fullmatch(role)))
        or not (single_use is None or type(single_use) is bool)
        or (role is None) != (single_use is None)
        or (role is None) != (window is None)
        or not isinstance(raw["params_env_digest"], str)
        or not isinstance(params, dict)
        or not all(
            isinstance(k, str) and _MATERIAL.fullmatch(k) and isinstance(v, str)
            for k, v in params.items()
        )
        or not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or not all(
            isinstance(a, str) and a and len(a) <= 1024 and "\x00" not in a
            for a in argv
        )
        or type(timeout) is not int
        or not 1 <= timeout <= MAX_JOB_TIMEOUT
    ):
        raise Refusal("materials_invalid", "dispatched_job_invalid")
    rows = raw["inputs"]
    apply_class = raw["apply_class"]
    if (
        not isinstance(rows, list)
        or type(apply_class) is not bool
        or raw["output_dir"] != OUTPUT_DIR
    ):
        raise Refusal("materials_invalid", "dispatched_job_invalid")
    inputs: list[tuple[str, str, str]] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"flag", "path", "sha256"}
            or not isinstance(row["flag"], str)
            or not _FLAG.fullmatch(row["flag"])
            or not isinstance(row["path"], str)
            or not _JOB_IN.fullmatch(row["path"])  # inputs only under /job/in, no ..
            or not isinstance(row["sha256"], str)
            or not _SHA256.fullmatch(
                row["sha256"]
            )  # an input WITHOUT a digest: refused
        ):
            raise Refusal("materials_invalid", "dispatched_job_invalid")
        inputs.append((row["flag"], row["path"], row["sha256"]))
    flags = [f for f, _, _ in inputs]
    if flags != sorted(set(flags)):
        raise Refusal("materials_invalid", "dispatched_job_invalid")  # sorted, unique
    in_paths = {p for _, p, _ in inputs}
    for flag, path, _ in inputs:  # an input flag is followed by its path in argv
        if not any(
            argv[i] == flag and argv[i + 1] == path for i in range(len(argv) - 1)
        ):
            raise Refusal("materials_invalid", "dispatched_job_invalid")
    for token in argv:
        if token.startswith("/job/"):
            ok_in = token in in_paths
            ok_out = _JOB_OUT.fullmatch(token) is not None
            # an input path must be declared WITH a digest; an output carries none
            if not (ok_in or ok_out) or (ok_out and token in in_paths):
                raise Refusal("materials_invalid", "dispatched_job_invalid")
    window_id, closes_at = None, None
    if window is not None:
        if (
            not isinstance(window, dict)
            or set(window) != {"window_id", "closes_at"}
            or not isinstance(window["window_id"], str)
            or not _SEGMENT.fullmatch(window["window_id"])
            or not isinstance(window["closes_at"], str)
        ):
            raise Refusal("materials_invalid", "dispatched_job_invalid")
        window_id = window["window_id"]
        closes_at = parse_stamp(window["closes_at"])
    return DispatchedJob(
        raw["name"],
        role,
        single_use,
        _names(raw["materials"]),
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
        raw["params_env_digest"],
        tuple(argv),
        timeout,
        window_id,
        closes_at,
        tuple(inputs),
        apply_class,
    )


SERVING_TTL_RANGE: Final = (120, 900)
MAX_LEASE_MAX: Final = 10800


def check_plan_binding(
    binding: Mapping[str, object], operation: str
) -> DispatchedJob | None:
    """Everything target_api_v2 pins about `deploy_binding`, as the agent enforces
    it (the shape rules, the split lease, the naming and image-source rules). Returns
    the dispatched job (None unless `run_job`)."""
    job = plan_dispatched_job(binding, operation)
    roles = binding.get("roles", [])
    if job is not None and roles:
        raise Refusal("materials_invalid", "run_job_mints_no_serving_role")
    if job is not None and list(job.materials) != sorted(job.materials):
        raise Refusal("materials_invalid", "dispatched_job_invalid")
    images = binding.get("images", [])
    if isinstance(images, list):
        present = [isinstance(i, dict) and i.get("source") == "present" for i in images]
        if any(present) != (job is not None) or (job is not None and not all(present)):
            raise Refusal("materials_invalid", "image_source_for_operation")
    ttl, lease_max = binding.get("lease_ttl_seconds"), binding.get("lease_max_seconds")
    if lease_max is not None:  # the split lease (v2); v1 plans carry only the ttl
        if type(ttl) is not int or type(lease_max) is not int:
            raise Refusal("lease_ttl_invalid")
        if job is not None:
            if not ttl == lease_max == job.timeout_seconds + LEASE_MARGIN:
                raise Refusal("lease_ttl_invalid")
        elif (
            not SERVING_TTL_RANGE[0] <= ttl <= SERVING_TTL_RANGE[1]
            or not ttl <= lease_max <= MAX_LEASE_MAX
        ):
            raise Refusal("lease_ttl_invalid")
    needs_naming = bool(roles) or (job is not None and job.role is not None)
    if needs_naming and not (
        binding.get("secrets_namespace") and binding.get("secrets_environment")
    ):
        raise Refusal("materials_invalid", "bad_secrets_name")
    return job


def check_job_admissible(
    job: DispatchedJob,
    *,
    enrolled_roles: Collection[str],
    store_now: datetime,
    lease_ttl_seconds: int,
    foundation_timeout_seconds: int | None = None,
) -> None:
    """Before anything: the host fence, the signed window and the lease fit. The
    lease is never renewed for a job (RQ-1), and the agent's Foundation bound must
    cover the job's own `timeout_seconds`."""
    if job.role is not None:
        if job.role not in enrolled_roles:
            raise Refusal("job_role_refused")
        if job.closes_at is None or store_now >= job.closes_at:
            raise Refusal("job_window_closed")
    if job.timeout_seconds + LEASE_MARGIN > lease_ttl_seconds:
        raise Refusal("lease_ttl_invalid")  # never extended: the plan must fit
    if (
        foundation_timeout_seconds is not None
        and foundation_timeout_seconds < job.timeout_seconds
    ):
        raise Refusal("lease_ttl_invalid")  # Foundation would be cut off early


def check_matches_definition(
    job: DispatchedJob, definitions: Sequence[JobDefinition]
) -> None:
    """The dispatched job is one of the plan's own definitions, same role."""
    for definition in definitions:
        if definition.name == job.name:
            if definition.role != job.role:
                raise Refusal("materials_invalid", "dispatched_job_role_differs")
            return
    raise Refusal("materials_invalid", "dispatched_job_undefined")


def derive_apply_class(
    job: DispatchedJob, definition: JobDefinition | None = None
) -> bool:
    """RQ-3: apply-class from the SIGNED plan alone, the way Control derives it: any
    scoped gate `"1"`, or the job's command applies, or an `apply_mode` flag is in
    argv, or an `apply_choices` value follows its flag. Never from a name or label."""
    if any(value == "1" for _, value in job.env):
        return True
    if definition is None:
        return False
    if definition.apply_command:
        return True
    for flag, apply_mode, choices in definition.apply_args:
        for index, token in enumerate(job.argv):
            if token != flag:
                continue
            if apply_mode:
                return True
            if choices and index + 1 < len(job.argv) and job.argv[index + 1] in choices:
                return True
    return False


def check_apply_class(
    job: DispatchedJob, definition: JobDefinition | None = None
) -> None:
    """Refuse a plan whose stated class (required) disagrees with the derivation."""
    if job.apply_class != derive_apply_class(job, definition):
        raise Refusal("job_apply_class_mismatch")


def check_job_paths(secrets_root: Path, job: DispatchedJob) -> None:
    """RQ-2: no argv path sits under the secrets root (`/run/dotmac`): job work and
    secrets do not share a tree."""
    root = str(secrets_root).rstrip("/")
    for token in job.argv:
        if token.startswith("/") and (token == root or token.startswith(root + "/")):
            raise Refusal("job_input_mismatch", "path_under_secrets_root")


def check_job_inputs(
    secrets_root: Path, job: DispatchedJob, work_root: Path | None
) -> None:
    """RQ-2: before Foundation runs, every input's sha256 equals the signed one. An
    input `/job/in/<name>` lives on the host at `<work_root>/<job>/in/<name>` (the
    root-owned `job_work_root`). A symlink, a non-regular file or a missing file is a
    mismatch; no work root with inputs to check is a refusal."""
    check_job_paths(secrets_root, job)
    if not job.inputs:
        return
    if work_root is None:
        raise Refusal("job_input_mismatch", "no_work_root")
    for _, path, expected in job.inputs:
        host = work_root / job.name / "in" / path.rsplit("/", 1)[1]
        try:
            descriptor = os.open(host, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise Refusal("job_input_mismatch") from None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise Refusal("job_input_mismatch")
            hasher = hashlib.sha256()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while chunk := stream.read(1 << 20):
                    hasher.update(chunk)
        finally:
            os.close(descriptor)
        if "sha256:" + hasher.hexdigest() != expected:
            raise Refusal("job_input_mismatch")


def job_roles(jobs: Sequence[JobDefinition]) -> frozenset[str]:
    return frozenset(j.role for j in jobs if j.role)


def refuse_job_role_serving(
    serving_roles: Collection[str], jobs: Sequence[JobDefinition]
) -> None:
    """A role a job logs in as is never also minted for the serving deploy."""
    if set(serving_roles) & job_roles(jobs):
        raise Refusal("materials_invalid", "job_role_in_serving_set")


def refuse_sealing(
    role: str, names: Collection[str], jobs: Sequence[JobDefinition]
) -> None:
    """A8: the sealed last-known-good and its restore refuse a job role, and any
    role whose names include a job-only credential. No boot fallback exists."""
    if role in job_roles(jobs) or set(names) & JOB_ONLY_NAMES:
        raise Refusal("job_role_refused")


def split_params(
    params: Sequence[Param], jobs: Sequence[JobDefinition]
) -> tuple[Param, ...]:
    """The serving params: a param any job scopes is never rendered into
    `params.env`."""
    scoped = {n for j in jobs for n in j.params}
    return tuple(p for p in params if p.name not in scoped)


def refuse_cp_scoped_params(
    params: Sequence[Param], jobs: Sequence[JobDefinition]
) -> None:
    """S2: a `run_job` plan's job values are signed in the plan; CP's unsigned
    `params` may not carry any param a job scopes."""
    scoped = {n for j in jobs for n in j.params}
    if any(p.name in scoped for p in params):
        raise Refusal("materials_invalid", "scoped_param_from_cp")


def job_env(job: DispatchedJob) -> bytes:
    """The job's env lines (sorted `K=V\\n`), from the signed `params` only, and
    refused unless they match the digest the plan bound. The role's credentials
    come from its rendered files, never from here."""
    lines: list[str] = []
    for material, value in job.env:
        if material in JOB_ONLY_NAMES:
            raise Refusal("job_role_refused")  # a credential is never a plain param
        check_value(value)
        lines.append(f"{material}={value}\n")
    render = "".join(lines).encode("utf-8")
    if digest(render) != job.params_env_digest:
        raise Refusal("params_digest_mismatch")
    return render


def require_naming(roles: Collection[object], jobs: Sequence[JobDefinition]) -> bool:
    """A13: the pinned naming applies when there are serving roles OR job roles."""
    return bool(roles) or bool(job_roles(jobs))


__all__ = [
    "JOB_ONLY_NAMES", "DispatchedJob", "JobDefinition", "check_apply_class",
    "check_job_admissible", "check_job_inputs", "derive_apply_class",
    "check_matches_definition", "job_env", "job_roles", "plan_dispatched_job",
    "plan_jobs", "refuse_cp_scoped_params", "refuse_job_role_serving",
    "refuse_sealing", "require_naming", "split_params",
]  # fmt: skip
