"""Job roles (P-SEC rev4 3.14; REVIEW-import-jobs A3-A9, A13): policy only.

A job role's `secret_id` is minted ONLY for a dispatched job: never on a serving
deploy, at boot or from a timer. It is single-use, tracked from the moment it is
unwrapped, purged (file plus accessor) on every exit path and at agent start, never
sealed or restored, and its scoped params reach the job's env only.

What is NOT here, waiting on Control's job-plan keys and the architect's ruling:
which job a dispatch runs (A1), the import window (A2), starting the job
container (A6), the closed argv (A11) and the lease fit (A12).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from .canon import canonical_json, digest
from .params import Param, job_env_render
from .refusal import Refusal
from .wire import parse_stamp

#: Credentials that belong to a dispatched job and to nothing else (Control
#: `JOB_ONLY_MATERIALS`). Never a serving role's, never sealed.
JOB_ONLY_NAMES: Final = frozenset({"MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"})
_SEGMENT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_JOB_KEYS: Final = frozenset(
    {"name", "role", "single_use", "materials", "params", "params_env_digest"}
)


@dataclass(frozen=True)
class JobSpec:
    name: str
    role: str | None
    single_use: bool | None
    materials: tuple[str, ...]
    params: tuple[str, ...]
    params_env_digest: str


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(n, str) and _NAME.fullmatch(n) for n in value
    ):
        raise Refusal("materials_invalid")
    return tuple(value)


def plan_jobs(snapshot: Mapping[str, object]) -> tuple[JobSpec, ...]:
    """`deploy_binding.jobs` from the SIGNED plan (absent: no jobs)."""
    binding = snapshot.get("deploy_binding")
    rows = binding.get("jobs", []) if isinstance(binding, dict) else None
    if not isinstance(rows, list):
        raise Refusal("materials_invalid")
    jobs: list[JobSpec] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _JOB_KEYS:
            raise Refusal("materials_invalid")
        role, single_use = row["role"], row["single_use"]
        if (
            not isinstance(row["name"], str)
            or not _NAME.fullmatch(row["name"])
            or not (
                role is None or (isinstance(role, str) and _SEGMENT.fullmatch(role))
            )
            or not (single_use is None or type(single_use) is bool)
            or (role is None) != (single_use is None)
            or not isinstance(row["params_env_digest"], str)
        ):
            raise Refusal("materials_invalid")
        jobs.append(
            JobSpec(
                row["name"],
                role,
                single_use,
                _names(row["materials"]),
                _names(row["params"]),
                row["params_env_digest"],
            )
        )
    if len({j.name for j in jobs}) != len(jobs):
        raise Refusal("materials_invalid")
    return tuple(jobs)


RUN_JOB: Final = "run_job"
MAX_JOB_TIMEOUT: Final = 780  # 900 s lease cap minus the 120 s margin
LEASE_MARGIN: Final = 120
MAX_JOB_LEASE: Final = 900
_DISPATCHED_KEYS: Final = _JOB_KEYS | {"argv", "timeout_seconds", "window"}


@dataclass(frozen=True)
class DispatchedJob:
    """The one job a `run_job` plan runs (`deploy_binding.dispatched_job`)."""

    spec: JobSpec
    argv: tuple[str, ...]
    timeout_seconds: int
    window_id: str | None
    closes_at: datetime | None

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
    if not isinstance(raw, dict) or set(raw) != _DISPATCHED_KEYS:
        raise Refusal("materials_invalid", "dispatched_job_missing")
    (spec,) = plan_jobs({"deploy_binding": {"jobs": [{k: raw[k] for k in _JOB_KEYS}]}})
    argv, timeout, window = raw["argv"], raw["timeout_seconds"], raw["window"]
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or not all(
            isinstance(a, str) and a and len(a) <= 1024 and "\x00" not in a
            for a in argv
        )
        or type(timeout) is not int
        or not 1 <= timeout <= MAX_JOB_TIMEOUT
        or (spec.role is None) != (window is None)
    ):
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
    return DispatchedJob(spec, tuple(argv), timeout, window_id, closes_at)


def check_job_admissible(
    job: DispatchedJob,
    *,
    enrolled_roles: Collection[str],
    store_now: datetime,
    lease_ttl_seconds: int,
) -> None:
    """Before anything: the host fence, the signed window and the lease fit."""
    if job.spec.role is not None:
        if job.spec.role not in enrolled_roles:
            raise Refusal("job_role_refused")
        if job.closes_at is None or store_now >= job.closes_at:
            raise Refusal("job_window_closed")
    if not job.timeout_seconds + LEASE_MARGIN <= lease_ttl_seconds <= MAX_JOB_LEASE:
        raise Refusal("lease_ttl_invalid")  # never extended: the plan must fit


def job_roles(jobs: Sequence[JobSpec]) -> frozenset[str]:
    return frozenset(j.role for j in jobs if j.role)


def refuse_job_role_serving(
    serving_roles: Collection[str], jobs: Sequence[JobSpec]
) -> None:
    """A role a job logs in as is never also minted for the serving deploy."""
    if set(serving_roles) & job_roles(jobs):
        raise Refusal("materials_invalid", "job_role_in_serving_set")


def refuse_sealing(
    role: str,
    names: Collection[str],
    jobs: Sequence[JobSpec],
) -> None:
    """A8: the sealed last-known-good and its restore refuse a job role, and any
    role whose names include a job-only credential. No boot fallback exists."""
    if role in job_roles(jobs) or set(names) & JOB_ONLY_NAMES:
        raise Refusal("job_role_refused")


def split_params(
    params: Sequence[Param], jobs: Sequence[JobSpec]
) -> tuple[tuple[Param, ...], dict[str, tuple[Param, ...]]]:
    """A9: `(serving params, scoped params by job name)`, split by the SIGNED plan.

    A param a job scopes is never rendered into `params.env`. A scoped name CP did
    not send is left out here and refused at dispatch (`job_env`), so a serving
    deploy is not blocked by a job it does not run.
    """
    by_name = {p.name: p for p in params}
    scoped_names = {n for j in jobs for n in j.params}
    serving = tuple(p for p in params if p.name not in scoped_names)
    scoped = {j.name: tuple(by_name[n] for n in j.params if n in by_name) for j in jobs}
    return serving, scoped


def job_env(job: JobSpec, scoped: Sequence[Param]) -> bytes:
    """The job's env lines (scoped params only), refused unless they match the
    per-job digest the plan bound. The role's materials come from its rendered
    files, never from here."""
    if {p.name for p in scoped} != set(job.params):
        raise Refusal("params_digest_mismatch")
    render = job_env_render(scoped)
    if digest(render) != job.params_env_digest:
        raise Refusal("params_digest_mismatch")
    if {
        line.split(b"=", 1)[0].decode() for line in render.splitlines()
    } & JOB_ONLY_NAMES:
        raise Refusal("job_role_refused")  # a credential is never a plain param
    return render


def require_naming(roles: Collection[object], jobs: Sequence[JobSpec]) -> bool:
    """A13: the pinned naming applies when there are serving roles OR job roles."""
    return bool(roles) or bool(job_roles(jobs))


__all__ = [
    "JOB_ONLY_NAMES", "JobSpec", "job_env", "job_roles", "plan_jobs",
    "refuse_job_role_serving", "refuse_sealing", "require_naming", "split_params",
]  # fmt: skip
