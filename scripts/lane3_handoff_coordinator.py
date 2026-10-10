"""Coordinator for the Lane 3 same-job broker handoff: sole admission owner.

The coordinator decides; the pinned root controller enforces. This module:

* reads back, independently through the authenticated GitHub API, the exact
  run, attempt, head SHA, workflow path and blob, the single job assigned to
  this lease's JIT runner, that runner, the runner group, the protected
  Environment's specific positive human approval and the absence of any other
  active protected run, plus the actual supplier module bytes at the approved
  Starter commit. Any ambiguity, absence or unexpected shape refuses;
* treats the job's report as a SELECTION REQUEST from the finite approved
  origin set (exact membership; ``origin.not_admitted`` otherwise). Runner
  text is never authority: the job ID is coordinator-assigned after readback;
* captures one DNS snapshot (``lane3_handoff_resolver``) and computes the
  effective manifest the controller must report back;
* talks to the controller only through the fixed management operation
  protocol: a fixed op name and a schema-validated JSON document on stdin. No
  shell fragment, executable name or path is ever taken from a caller.

Approval for one run/attempt cannot authorize another. GitHub's review history
is not attributable to an attempt, so any ``run_attempt`` other than 1 refuses
as ``approval.missing`` (fail-closed).

Refusals are fixed labels. Tokens, JIT configuration, bodies and exception
text are never printed. Python standard library only.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import fcntl
import functools
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr

CONFIG_SCHEMA: Final = "dotmac.lane3.handoff-coordinator.v1"
STATE_SCHEMA: Final = "dotmac.lane3.handoff-coordinator-state.v1"
ACTIVE_RUN: Final = frozenset(
    {"queued", "in_progress", "waiting", "requested", "pending"}
)
POLL_INTERVAL_S: Final = 1.0
_REPO: Final = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_ORG: Final = re.compile(r"[A-Za-z0-9-]{1,39}")
_WORKFLOW: Final = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]{1,100}\.ya?ml")
_MODULE: Final = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*\.py")
_ALIAS: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_LOGIN: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_LABEL: Final = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ENV_NAME: Final = re.compile(r"[A-Za-z0-9_.-]{1,255}")
_ABS: Final = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")


class CoordinatorRefused(RuntimeError):
    """A fixed-label refusal; ``label`` is never input or exception text."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label

    def __str__(self) -> str:
        return self.label


def refused(label: str) -> CoordinatorRefused:
    return CoordinatorRefused(label)


# ── configuration ──────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Config:
    repository: str
    repository_id: int
    workflow_path: str
    workflow_sha: str
    workflow_blob: str
    environment: str
    environment_id: int
    approvers: frozenset[str]
    runner_org: str
    runner_group_id: int
    runner_labels: frozenset[str]
    starter_repository: str
    starter_commit: str
    supplier_modules: Mapping[str, str]
    admission: Mapping[str, str]
    ssh_alias: str
    controller_path: str
    resolver: str
    policy_path: Path
    bootstrap_manifest_path: Path
    state_path: Path


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise refused(label)
    return value


def _pos_int(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise refused("config.invalid")
    return value


def _str(value: Any, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise refused("config.invalid")
    return value


def parse_config(value: Any) -> Config:
    keys = {
        "schema",
        "repository",
        "repository_id",
        "workflow_path",
        "workflow_sha",
        "workflow_blob",
        "environment",
        "environment_id",
        "approvers",
        "runner_org",
        "runner_group_id",
        "runner_labels",
        "starter_repository",
        "starter_commit",
        "supplier_modules",
        "admission",
        "management",
        "resolver",
        "policy_path",
        "bootstrap_manifest_path",
        "state_path",
    }
    doc = _exact(value, keys, "config.invalid")
    if doc["schema"] != CONFIG_SCHEMA:
        raise refused("config.invalid")
    try:
        for key in ("workflow_sha", "workflow_blob", "starter_commit"):
            hp.hex40(doc[key])
        modules = doc["supplier_modules"]
        if not isinstance(modules, dict) or not 1 <= len(modules) <= 32:
            raise refused("config.invalid")
        for path, digest in modules.items():
            _str(path, _MODULE)
            hp.hex64(digest)
    except hp.ProtocolRefused:
        raise refused("config.invalid") from None
    admission = _exact(
        doc["admission"], {"repository", "path", "commit", "sha256"}, "config.invalid"
    )
    _str(admission["repository"], _REPO)
    _str(admission["path"], re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.json"))
    if ".." in admission["path"].split("/"):
        raise refused("config.invalid")
    try:
        hp.hex40(admission["commit"])
        hp.hex64(admission["sha256"])
    except hp.ProtocolRefused:
        raise refused("config.invalid") from None
    approvers, labels = doc["approvers"], doc["runner_labels"]
    if type(approvers) is not list or not 1 <= len(approvers) <= 16:
        raise refused("config.invalid")
    if type(labels) is not list or not 1 <= len(labels) <= 16:
        raise refused("config.invalid")
    management = _exact(
        doc["management"], {"ssh_alias", "controller_path"}, "config.invalid"
    )
    try:
        resolver = str(ipaddress.ip_address(doc["resolver"]))
    except (TypeError, ValueError):
        raise refused("config.invalid") from None
    if resolver != doc["resolver"]:
        raise refused("config.invalid")
    return Config(
        repository=_str(doc["repository"], _REPO),
        repository_id=_pos_int(doc["repository_id"]),
        workflow_path=_str(doc["workflow_path"], _WORKFLOW),
        workflow_sha=doc["workflow_sha"],
        workflow_blob=doc["workflow_blob"],
        environment=_str(doc["environment"], _ENV_NAME),
        environment_id=_pos_int(doc["environment_id"]),
        approvers=frozenset(_str(a, _LOGIN) for a in approvers),
        runner_org=_str(doc["runner_org"], _ORG),
        runner_group_id=_pos_int(doc["runner_group_id"]),
        runner_labels=frozenset(_str(label, _LABEL) for label in labels),
        starter_repository=_str(doc["starter_repository"], _REPO),
        starter_commit=doc["starter_commit"],
        supplier_modules=dict(modules),
        admission=dict(admission),
        ssh_alias=_str(management["ssh_alias"], _ALIAS),
        controller_path=_str(management["controller_path"], _ABS),
        resolver=resolver,
        policy_path=Path(_str(doc["policy_path"], _ABS)),
        bootstrap_manifest_path=Path(_str(doc["bootstrap_manifest_path"], _ABS)),
        state_path=Path(_str(doc["state_path"], _ABS)),
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise refused("config.invalid") from None
    if len(raw) > hc.MAX_CONFIG:
        raise refused("config.invalid")
    try:
        return hc.strict_json(raw)
    except hc.ControllerRefused:
        raise refused("config.invalid") from None


# ── GitHub API readback ────────────────────────────────────────────────────

Api = Callable[..., Any]


def gh_api(path: str, method: str = "GET", payload: Any = None) -> Any:
    """``gh api`` with a fixed argv; diagnostics are suppressed."""
    if re.fullmatch(r"[A-Za-z0-9_./?=&-]+", path) is None:
        raise refused("api.path")
    if method not in ("GET", "POST", "DELETE"):
        raise refused("api.path")
    argv = ["gh", "api", path, "--method", method]
    data = None
    if payload is not None:
        argv += ["--input", "-"]
        data = json.dumps(payload).encode("utf-8")
    try:
        result = subprocess.run(
            argv, input=data, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        raise refused("api.unavailable") from None
    if result.returncode:
        raise refused("api.unavailable")
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise refused("api.unavailable") from None


def _field(obj: Any, key: str, kind: type, label: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise refused(label)
    value = obj[key]
    if kind is int:
        if type(value) is not int:
            raise refused(label)
    elif not isinstance(value, kind):
        raise refused(label)
    return value


def _api(api: Api, path: str, label: str) -> Any:
    try:
        return api(path)
    except CoordinatorRefused:
        raise refused(label) from None
    except Exception:
        raise refused(label) from None


def readback_binding(
    api: Api,
    cfg: Config,
    *,
    run_id: int,
    runner_id: int,
    report: Mapping[str, Any],
    prelaunch: bool = False,
    expected_qualification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The independent binding, or a fixed refusal. Report text is checked
    against the readback; it never supplies a value of its own."""
    expected = report["expected"]
    mismatch = "binding.mismatch"
    if (
        expected["run_id"] != run_id
        or expected["workflow_sha"] != cfg.workflow_sha
        or expected["starter_commit"] != cfg.starter_commit
    ):
        raise refused(mismatch)
    attempt = expected["run_attempt"]
    if attempt != 1:
        raise refused("approval.missing")
    repo = cfg.repository
    base = f"repos/{repo}/actions/runs/{run_id}"

    run = _api(api, base, mismatch)
    repository = _field(run, "repository", dict, mismatch)
    if (
        _field(run, "id", int, mismatch) != run_id
        or _field(run, "run_attempt", int, mismatch) != attempt
        or _field(run, "head_sha", str, mismatch) != cfg.workflow_sha
        or _field(run, "path", str, mismatch) != cfg.workflow_path
        or _field(run, "event", str, mismatch) != "workflow_dispatch"
        or _field(run, "head_branch", str, mismatch) != "main"
        or _field(run, "status", str, mismatch)
        not in ({"queued", "waiting", "in_progress"} if prelaunch else {"in_progress"})
        or _field(repository, "id", int, mismatch) != cfg.repository_id
        or _field(repository, "full_name", str, mismatch) != repo
    ):
        raise refused(mismatch)

    detail = _api(api, f"{base}/attempts/{attempt}", mismatch)
    if (
        _field(detail, "id", int, mismatch) != run_id
        or _field(detail, "run_attempt", int, mismatch) != attempt
        or _field(detail, "head_sha", str, mismatch) != cfg.workflow_sha
    ):
        raise refused(mismatch)

    blob = _api(
        api,
        f"repos/{repo}/contents/{cfg.workflow_path}?ref={cfg.workflow_sha}",
        mismatch,
    )
    if (
        _field(blob, "type", str, mismatch) != "file"
        or _field(blob, "path", str, mismatch) != cfg.workflow_path
        or _field(blob, "sha", str, mismatch) != cfg.workflow_blob
    ):
        raise refused(mismatch)

    ambiguous = "assignment.ambiguous"
    listing = _api(api, f"{base}/attempts/{attempt}/jobs?per_page=100", ambiguous)
    jobs = _field(listing, "jobs", list, ambiguous)
    if _field(listing, "total_count", int, ambiguous) != len(jobs):
        raise refused(ambiguous)
    job_id = 0
    if prelaunch:
        pending_jobs = [
            j for j in jobs if isinstance(j, dict) and j.get("status") != "completed"
        ]
        if len(pending_jobs) != 1:
            raise refused(ambiguous)
        job = pending_jobs[0]
        if (
            job.get("runner_id") not in (0, None)
            or job.get("status") != "queued"
            or job.get("run_id") != run_id
            or job.get("run_attempt") != attempt
            or job.get("head_sha") != cfg.workflow_sha
            or job.get("runner_group_id") != cfg.runner_group_id
        ):
            raise refused(ambiguous)
        group = _api(
            api,
            f"orgs/{cfg.runner_org}/actions/runner-groups/{cfg.runner_group_id}/runners",
            ambiguous,
        )
        if (
            _field(group, "total_count", int, ambiguous) != 0
            or _field(group, "runners", list, ambiguous) != []
        ):
            raise refused(ambiguous)
    else:
        mine = [
            j for j in jobs if isinstance(j, dict) and j.get("runner_id") == runner_id
        ]
        if len(mine) != 1:
            raise refused(ambiguous)
        job = mine[0]
        if (
            _field(job, "status", str, ambiguous) != "in_progress"
            or _field(job, "run_id", int, ambiguous) != run_id
            or _field(job, "run_attempt", int, ambiguous) != attempt
            or _field(job, "head_sha", str, ambiguous) != cfg.workflow_sha
            or _field(job, "runner_group_id", int, ambiguous) != cfg.runner_group_id
        ):
            raise refused(ambiguous)
        job_id = _field(job, "id", int, ambiguous)
        for other in jobs:
            if other is job or not isinstance(other, dict):
                continue
            if other.get("status") != "completed" and (
                other.get("runner_group_id") == cfg.runner_group_id
                or other.get("runner_id") in (None, 0)
            ):
                # A second protected job pending or running in this attempt.
                raise refused(ambiguous)

        org = cfg.runner_org
        runner = _api(api, f"orgs/{org}/actions/runners/{runner_id}", ambiguous)
        labels = _field(runner, "labels", list, ambiguous)
        names = {_field(x, "name", str, ambiguous) for x in labels}
        if (
            _field(runner, "id", int, ambiguous) != runner_id
            or _field(runner, "busy", bool, ambiguous) is not True
            or names != cfg.runner_labels
        ):
            raise refused(ambiguous)
        group = _api(
            api,
            f"orgs/{org}/actions/runner-groups/{cfg.runner_group_id}/runners",
            ambiguous,
        )
        members = _field(group, "runners", list, ambiguous)
        if (
            _field(group, "total_count", int, ambiguous) != 1
            or len(members) != 1
            or _field(members[0], "id", int, ambiguous) != runner_id
        ):
            raise refused(ambiguous)

    missing = "approval.missing"
    pending = _api(api, f"{base}/pending_deployments", missing)
    if pending != []:
        raise refused(missing)
    reviews = _api(api, f"{base}/approvals", missing)
    if type(reviews) is not list or not reviews:
        # Absence of pending approvals alone is insufficient.
        raise refused(missing)
    positive = 0
    for review in reviews:
        state = _field(review, "state", str, missing)
        environments = _field(review, "environments", list, missing)
        ids = {_field(e, "id", int, missing) for e in environments}
        if state != "approved":
            raise refused(missing)
        user = _field(review, "user", dict, missing)
        if cfg.environment_id in ids:
            if _field(user, "login", str, missing) not in cfg.approvers:
                raise refused(missing)
            names = {_field(e, "name", str, missing) for e in environments}
            if cfg.environment not in names:
                raise refused(missing)
            positive += 1
    if positive != 1:
        raise refused(missing)

    workflow = cfg.workflow_path.rsplit("/", 1)[1]
    runs = _api(
        api, f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100", ambiguous
    )
    listed = _field(runs, "workflow_runs", list, ambiguous)
    if _field(runs, "total_count", int, ambiguous) > len(listed):
        raise refused(ambiguous)
    others = [
        r
        for r in listed
        if isinstance(r, dict)
        and r.get("id") != run_id
        and r.get("status") in ACTIVE_RUN
    ]
    if others or not any(isinstance(r, dict) and r.get("id") == run_id for r in listed):
        raise refused(ambiguous)

    for path, digest in sorted(cfg.supplier_modules.items()):
        doc = _api(
            api,
            f"repos/{cfg.starter_repository}/contents/{path}?ref={cfg.starter_commit}",
            mismatch,
        )
        if _field(doc, "encoding", str, mismatch) != "base64":
            raise refused(mismatch)
        try:
            content = base64.b64decode(
                _field(doc, "content", str, mismatch), validate=False
            )
        except ValueError:
            raise refused(mismatch) from None
        if hashlib.sha256(content).hexdigest() != digest:
            raise refused(mismatch)

    admission = cfg.admission
    record = _api(
        api,
        f"repos/{admission['repository']}/contents/{admission['path']}?ref={admission['commit']}",
        mismatch,
    )
    try:
        if _field(record, "encoding", str, mismatch) != "base64":
            raise refused(mismatch)
        raw = base64.b64decode(_field(record, "content", str, mismatch), validate=False)
        if (
            len(raw) > hc.MAX_CONFIG
            or hashlib.sha256(raw).hexdigest() != admission["sha256"]
        ):
            raise refused(mismatch)
        admitted = hc.strict_json(raw)
    except (ValueError, hc.ControllerRefused):
        raise refused(mismatch) from None
    revisions = admitted.get("admitted_launcher_revisions")
    if (
        admitted.get("execution_repository") != cfg.repository
        or admitted.get("execution_repository_id") != cfg.repository_id
        or admitted.get("workflow_path") != cfg.workflow_path
        or type(revisions) is not list
        or cfg.workflow_sha not in revisions
    ):
        raise refused(mismatch)
    supplier = {
        path.rsplit("/", 1)[-1]: digest for path, digest in cfg.supplier_modules.items()
    }
    if len(supplier) != len(cfg.supplier_modules):
        raise refused(mismatch)
    qualification = {
        "repository_id": cfg.repository_id,
        "run_id": run_id,
        "run_attempt": attempt,
        "workflow_sha": cfg.workflow_sha,
        "workflow_blob": cfg.workflow_blob,
        "starter_commit": cfg.starter_commit,
        "environment_id": cfg.environment_id,
        "approval_digest": hp.digest(reviews),
        "admission_digest": admission["sha256"],
        "supplier_digest": hp.digest(supplier),
    }
    if expected_qualification is not None and qualification != expected_qualification:
        raise refused("binding.mismatch")
    if prelaunch:
        return qualification
    return {
        "repository_id": cfg.repository_id,
        "run_id": run_id,
        "run_attempt": attempt,
        "job_id": job_id,
        "runner_id": runner_id,
        "workflow_sha": cfg.workflow_sha,
        "workflow_blob": cfg.workflow_blob,
        "starter_commit": cfg.starter_commit,
        "admission_digest": admission["sha256"],
        "supplier_digest": hp.digest(supplier),
    }


def prelaunch_binding(api: Api, cfg: Config, run_id: int) -> dict[str, Any]:
    return readback_binding(
        api,
        cfg,
        run_id=run_id,
        runner_id=0,
        prelaunch=True,
        report={
            "expected": {
                "run_id": run_id,
                "run_attempt": 1,
                "workflow_sha": cfg.workflow_sha,
                "starter_commit": cfg.starter_commit,
            }
        },
    )


# ── fixed management-channel protocol ──────────────────────────────────────

MGMT_RESPONSES: Final[dict[str, set[str]]] = {
    "prepare": {
        "protocol",
        "op",
        "lease_id",
        "boot_id",
        "state",
        "manifest_digest",
        "controller_digest",
    },
    "bootstrap": {"protocol", "op", "lease_id", "state", "remaining_ms"},
    "timing": {"protocol", "op", "lease_id", "challenge"},
    "status": {
        "protocol",
        "op",
        "lease_id",
        "state",
        "category",
        "report",
        "report_digest",
        "remaining_ms",
    },
    "grant": {
        "protocol",
        "op",
        "lease_id",
        "state",
        "grant_digest",
        "manifest_digest",
    },
    "refuse": {"protocol", "op", "lease_id", "state", "evidence"},
    "cleanup": {"protocol", "op", "lease_id", "state", "evidence"},
}
_REFUSAL_KEYS: Final = {"protocol", "status", "category"}

Channel = Callable[[str, Mapping[str, Any]], dict[str, Any]]


def validate_mgmt_response(op: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and set(value) == _REFUSAL_KEYS:
        if value["protocol"] == hp.PROTOCOL and value["status"] == "REFUSED":
            label = value["category"]
            raise refused(label if type(label) is str else "mgmt.invalid")
    if not isinstance(value, dict) or set(value) != MGMT_RESPONSES[op]:
        raise refused("mgmt.invalid")
    expected_op = "cleanup" if op == "refuse" else op
    if value["protocol"] != hp.PROTOCOL or value["op"] != expected_op:
        raise refused("mgmt.invalid")
    try:
        hp.lease_id(value["lease_id"])
    except hp.ProtocolRefused:
        raise refused("mgmt.invalid") from None
    return value


def ssh_channel(alias: str, controller_path: str) -> Channel:
    """The existing authenticated management path, with a fixed command."""
    _str(alias, _ALIAS)
    _str(controller_path, _ABS)

    def call(op: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if op not in hc.MGMT_STDIN_OPS:
            raise refused("mgmt.invalid")
        try:
            request = hc.validate_mgmt(dict(payload))
        except hc.ControllerRefused:
            raise refused("mgmt.invalid") from None
        if request["op"] != op:
            raise refused("mgmt.invalid")
        remote = f"sudo -n /usr/bin/python3 -I {controller_path} {op}"
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        argv += [alias, remote]
        try:
            result = subprocess.run(
                argv,
                input=json.dumps(request).encode("utf-8"),
                capture_output=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise refused("mgmt.unavailable") from None
        try:
            value = hc.strict_json(result.stdout)
        except hc.ControllerRefused:
            raise refused("mgmt.unavailable") from None
        return validate_mgmt_response(op, value)

    return call


def request(op: str, **fields: Any) -> dict[str, Any]:
    return {"protocol": hp.PROTOCOL, "op": op, **fields}


# ── decision ───────────────────────────────────────────────────────────────


@dataclasses.dataclass
class Decision:
    status: str
    category: str | None
    grant_digest: str | None = None
    manifest_digest: str | None = None


def wait_for_report(
    channel: Channel,
    lease: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll the controller until REPORTED, a terminal state or the deadline."""
    started = clock()
    while True:
        status = channel("status", request("status", lease_id=lease))
        state = status["state"]
        if state != "BOOTSTRAP" and state != "PREPARED":
            return status
        remaining = status["remaining_ms"]
        if type(remaining) is not int or remaining <= 0:
            return status
        if clock() - started > hp.WINDOW_NS / 1e9:
            raise refused("lease.expired")
        sleep(POLL_INTERVAL_S)


def decide(
    *,
    cfg: Config,
    api: Api,
    channel: Channel,
    lease: str,
    run_id: int,
    runner_id: int,
    qualification: Mapping[str, Any],
    policy_doc: Mapping[str, Any],
    bootstrap_manifest: Mapping[str, Any],
    resolve: Callable[..., hr.Snapshot] = hr.resolve,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> Decision:
    """Validate the reported selection and grant, or refuse, exactly once."""
    status = wait_for_report(channel, lease, sleep=sleep)
    if status["state"] != "REPORTED":
        if status["state"] not in hc.TERMINAL:
            channel(
                "refuse", request("refuse", lease_id=lease, category="state.invalid")
            )
        return Decision("REFUSED", status["category"] or "state.invalid")

    def refuse(label: str) -> Decision:
        category = label if label in hp.CATEGORIES else "internal"
        channel("refuse", request("refuse", lease_id=lease, category=category))
        return Decision("REFUSED", category)

    try:
        report = hp.validate_report(status["report"])
    except hp.ProtocolRefused as exc:
        return refuse(exc.label)
    report_digest = hp.digest(report)
    if status["report_digest"] != report_digest or report["lease_id"] != lease:
        return refuse("binding.mismatch")
    if any(report["flags"].values()):
        return refuse("origin.flags")
    try:
        policy = hc.parse_policy(dict(policy_doc))
        manifest = hc.validate_bootstrap(dict(bootstrap_manifest), hr.globally_routable)
    except hc.ControllerRefused:
        return refuse("internal")
    origin = report["origin"]
    if not policy.origins.admits(origin):
        # No token request and no automatic addition to the inventory.
        return refuse("origin.not_admitted")
    try:
        binding = readback_binding(
            api,
            cfg,
            run_id=run_id,
            runner_id=runner_id,
            report=report,
            expected_qualification=qualification,
        )
    except CoordinatorRefused as exc:
        return refuse(exc.label)
    try:
        timing = channel("timing", request("timing", lease_id=lease))
        hp.hex64(timing["challenge"])
        snapshot = resolve(
            origin,
            resolver=cfg.resolver,
            aliases=policy.aliases,
            max_addresses=hp.MAX_ADDRESSES,
        )
    except (hr.SnapshotRefused, hp.ProtocolRefused, CoordinatorRefused, KeyError):
        return refuse("snapshot.refused")
    rows = snapshot.rows()
    union = {r["address"] for r in manifest["destinations"]} | {
        r["address"] for r in rows
    }
    if len(union) > hp.MAX_ADDRESSES:
        return refuse("snapshot.refused")
    ttl_ms = min(
        snapshot.remaining_ns(clock_ns()) // 1_000_000,
        hp.SNAPSHOT_MAX_AGE_NS // 1_000_000,
    )
    margin_ms = hp.GRANT_MIN_REMAINING_NS // 1_000_000
    if ttl_ms < margin_ms:
        return refuse("deadline.insufficient")
    expected_manifest = hp.digest(hc.effective_manifest(manifest, origin, rows))
    try:
        reply = channel(
            "grant",
            request(
                "grant",
                lease_id=lease,
                report_digest=report_digest,
                binding=binding,
                qualification=dict(qualification),
                origin=origin,
                snapshot={
                    "digest": snapshot.digest,
                    "challenge": timing["challenge"],
                    "ttl_remaining_ms": int(ttl_ms),
                    "addresses": rows,
                },
                policy_digest=policy.digest,
            ),
        )
    except CoordinatorRefused as exc:
        # The controller has already ended the lease through cleanup.
        label = exc.label if exc.label in hp.CATEGORIES else "install.failed"
        return Decision("REFUSED", label)
    if reply["state"] != "GRANTED" or reply["manifest_digest"] != expected_manifest:
        return refuse("grant.mismatch")
    return Decision("GRANTED", None, reply["grant_digest"], reply["manifest_digest"])


# ── coordinator state and CLI ──────────────────────────────────────────────


@contextlib.contextmanager
def operation_lock(path: Path) -> Any:
    """One persistent owner-protected inode serializes the complete operation.

    The lock is never unlinked with state: replacing/deleting that inode would
    let concurrent invocations hold different locks for the same lease.
    """
    parent = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        info = os.fstat(parent)
        if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
            raise refused("state.invalid")
        fd = os.open(
            path.name + ".lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise refused("state.invalid")
        until = time.monotonic() + hc.LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise refused("lock.busy") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def serialized(
    function: Callable[..., dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    @functools.wraps(function)
    def wrapped(cfg: Config, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with operation_lock(cfg.state_path):
            return function(cfg, *args, **kwargs)

    return wrapped


def load_state(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("schema") != STATE_SCHEMA:
        raise refused("state.invalid")
    return value


def save_state(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(dict(value), stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    parent = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


@serialized
def op_prepare(cfg: Config, channel: Channel, api: Api, run_id: int) -> dict[str, Any]:
    if cfg.state_path.exists():
        raise refused("lease.active")
    qualification = prelaunch_binding(api, cfg, run_id)
    policy = hc.parse_policy(read_json(cfg.policy_path))
    manifest = read_json(cfg.bootstrap_manifest_path)
    reply = channel(
        "prepare",
        request(
            "prepare",
            bootstrap_manifest=manifest,
            policy_digest=policy.digest,
            qualification=qualification,
        ),
    )
    state = {
        "schema": STATE_SCHEMA,
        "lease_id": reply["lease_id"],
        "qualification": qualification,
    }
    save_state(cfg.state_path, state)
    return {"lease_id": reply["lease_id"], "state": reply["state"]}


@serialized
def op_launch(cfg: Config, channel: Channel, api: Api, run_id: int) -> dict[str, Any]:
    state = load_state(cfg.state_path)
    if "runner_id" in state or "launch_intent" in state:
        # A POST with an uncertain response must never be repeated. Cleanup
        # reconciles the durable full-lease identity independently.
        raise refused("state.invalid")
    qualification = prelaunch_binding(api, cfg, run_id)
    if qualification != state.get("qualification"):
        raise refused("binding.mismatch")
    name = f"lane3-handoff-{state['lease_id']}"
    state["run_id"] = run_id
    state["launch_intent"] = {
        "name": name,
        "runner_group_id": cfg.runner_group_id,
        "run_id": run_id,
        "qualification_digest": hp.digest(qualification),
    }
    save_state(cfg.state_path, state)  # durable BEFORE registration effect
    jit = api(
        f"orgs/{cfg.runner_org}/actions/runners/generate-jitconfig",
        "POST",
        {
            "name": name,
            "runner_group_id": cfg.runner_group_id,
            "labels": sorted(cfg.runner_labels),
            "work_folder": "_work",
        },
    )
    runner_id = _field(
        _field(jit, "runner", dict, "api.unavailable"), "id", int, "api.unavailable"
    )
    state.update(run_id=run_id, runner_id=runner_id)
    save_state(cfg.state_path, state)
    encoded = _field(jit, "encoded_jit_config", str, "api.unavailable")
    jit = None
    try:
        reply = channel(
            "bootstrap",
            request("bootstrap", lease_id=state["lease_id"], jit_config=encoded),
        )
    finally:
        encoded = ""
    return {"lease_id": state["lease_id"], "state": reply["state"]}


@serialized
def op_decide(cfg: Config, channel: Channel, api: Api) -> dict[str, Any]:
    state = load_state(cfg.state_path)
    decision = decide(
        cfg=cfg,
        api=api,
        channel=channel,
        lease=state["lease_id"],
        run_id=state["run_id"],
        runner_id=state["runner_id"],
        qualification=state["qualification"],
        policy_doc=read_json(cfg.policy_path),
        bootstrap_manifest=read_json(cfg.bootstrap_manifest_path),
    )
    return {
        "status": decision.status,
        "category": decision.category,
        "manifest_digest": decision.manifest_digest,
    }


@serialized
def op_cleanup(cfg: Config, channel: Channel, api: Api) -> dict[str, Any]:
    state = load_state(cfg.state_path)
    reply = channel("cleanup", request("cleanup", lease_id=state["lease_id"]))
    if reply.get("state") != "CLOSED" or reply.get("lease_id") != state["lease_id"]:
        raise refused("state.invalid")
    try:
        public = hp.validate_evidence(reply["evidence"])
    except (hp.ProtocolRefused, KeyError):
        raise refused("state.invalid") from None
    if public["lease_id"] != state["lease_id"]:
        raise refused("state.invalid")
    if "runner_id" in state or "launch_intent" in state:
        runner_id = state.get("runner_id")
        if runner_id is not None and (type(runner_id) is not int or runner_id <= 0):
            raise refused("state.invalid")
        name = f"lane3-handoff-{state['lease_id']}"
        intent = state.get("launch_intent")
        if intent is not None:
            _exact(
                intent,
                {"name", "runner_group_id", "run_id", "qualification_digest"},
                "state.invalid",
            )
            if (
                intent["name"] != name
                or intent["runner_group_id"] != cfg.runner_group_id
                or intent["run_id"] != state.get("run_id")
                or intent["qualification_digest"]
                != hp.digest(state.get("qualification"))
            ):
                raise refused("state.invalid")
        org_path = f"orgs/{cfg.runner_org}/actions/runners?per_page=100"
        group_path = (
            f"orgs/{cfg.runner_org}/actions/runner-groups/"
            f"{cfg.runner_group_id}/runners?per_page=100"
        )

        def members(path: str) -> list[Any]:
            listing = _api(api, path, "api.unavailable")
            rows = _field(listing, "runners", list, "api.unavailable")
            if _field(listing, "total_count", int, "api.unavailable") != len(rows):
                raise refused("api.unavailable")
            ids = [row.get("id") if type(row) is dict else None for row in rows]
            if any(type(value) is not int or value <= 0 for value in ids) or len(
                set(ids)
            ) != len(ids):
                raise refused("api.unavailable")
            return rows

        rows = members(org_path)
        matching = [row for row in rows if row.get("name") == name]
        if len(matching) > 1:
            raise refused("assignment.ambiguous")
        group = members(group_path)
        if matching:
            found_id = matching[0]["id"]
            if runner_id is not None and found_id != runner_id:
                raise refused("binding.mismatch")
            if not any(
                row["id"] == found_id and row.get("name") == name for row in group
            ):
                # The name alone cannot authorize deleting a foreign-group runner.
                raise refused("binding.mismatch")
            runner_id = found_id
            api(f"orgs/{cfg.runner_org}/actions/runners/{runner_id}", "DELETE")
        elif runner_id is None:
            # Response UNKNOWN: present absence does not settle an in-flight POST.
            raise refused("assignment.ambiguous")
        elif any(row["id"] == runner_id for row in rows):
            raise refused("binding.mismatch")
        remaining = [*members(org_path), *members(group_path)]
        if any(row["id"] == runner_id or row.get("name") == name for row in remaining):
            raise refused("assignment.ambiguous")
    cfg.state_path.unlink()
    return {"state": reply["state"], "evidence": reply["evidence"]}


def main(argv: list[str]) -> int:
    ops = {"prepare", "launch", "decide", "cleanup"}
    if len(argv) < 4 or argv[1] not in ops or argv[2] != "--config":
        print(json.dumps({"status": "REFUSED", "category": "operation.invalid"}))
        return 2
    try:
        cfg = parse_config(read_json(Path(argv[3])))
        channel = ssh_channel(cfg.ssh_alias, cfg.controller_path)
        op = argv[1]
        if op in {"prepare", "launch"}:
            if len(argv) != 6 or argv[4] != "--run-id" or not argv[5].isdigit():
                raise refused("operation.invalid")
            result = (op_launch if op == "launch" else op_prepare)(
                cfg, channel, gh_api, int(argv[5])
            )
        elif len(argv) != 4:
            raise refused("operation.invalid")
        else:
            result = {
                "prepare": op_prepare,
                "decide": op_decide,
                "cleanup": op_cleanup,
            }[op](cfg, channel, gh_api)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        label = (
            exc.label
            if isinstance(exc, CoordinatorRefused | hc.ControllerRefused)
            else "internal"
        )
        print(json.dumps({"status": "REFUSED", "category": label}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
