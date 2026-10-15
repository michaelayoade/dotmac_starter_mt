"""Coordinator binding/decision canaries; synthetic GitHub API and channel.

Every binding row independently prevents a grant, and the positive exact
binding succeeds. Nothing here contacts GitHub, a host or OpenBao.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_coordinator as co  # noqa: E402
import lane3_handoff_protocol as hp  # noqa: E402
import lane3_handoff_resolver as hr  # noqa: E402

REPO = "synthetic-org/synthetic-launcher"
STARTER = "synthetic-org/synthetic-starter"
WF = ".github/workflows/synthetic.yml"
SHA = "a" * 40
BLOB = "b" * 40
STARTER_SHA = "c" * 40
MODULE = b"synthetic supplier bytes\n"
RUN, RUNNER, JOB, GROUP, ENV_ID = 101, 202, 303, 4, 505
LEASE = "d" * 32
ORIGIN = "https://broker.example"

ADMISSION = hp.canonical_json(
    {
        "execution_repository": REPO,
        "execution_repository_id": 9,
        "workflow_path": WF,
        "admitted_launcher_revisions": [SHA],
        "admission_evidence": None,
        "runner_group": {"id": GROUP, "name": "synthetic-protected"},
    }
)
ADMISSION_DIGEST = hashlib.sha256(ADMISSION).hexdigest()
SUPPLIER_DIGEST = hp.digest(
    {"lane3_github_oidc.py": hashlib.sha256(MODULE).hexdigest()}
)
ADMISSION_PATH = (
    f"repos/{STARTER}/contents/.github/lane3-execution.json?ref={STARTER_SHA}"
)
DISPATCHER = "/opt/dotmac-lane3-handoff/" + "a" * 64 + "/lane3_handoff_install.py"


def config_doc(controller_path: str = DISPATCHER) -> dict[str, Any]:
    return {
        "schema": co.CONFIG_SCHEMA,
        "purpose": "qualification",
        "evidence_sha256": None,
        "source_capability": {
            "schema": "dotmac.lane3.source-capability.v1",
            "purpose": "qualification",
            "repository": REPO,
            "workflow_path": WF,
            "workflow_sha": SHA,
            "workflow_blob": BLOB,
            "starter_repository": STARTER,
            "starter_commit": STARTER_SHA,
            "supplier_modules": {
                "scripts/lane3_github_oidc.py": hashlib.sha256(MODULE).hexdigest()
            },
            "operations": [
                "github-oidc",
                "bounded-openbao-kv-read",
                "proof-record",
                "cleanup",
            ],
            "terminal": "execution-refused",
        },
        "repository": REPO,
        "repository_id": 9,
        "workflow_path": WF,
        "workflow_sha": SHA,
        "workflow_blob": BLOB,
        "environment": "synthetic-env",
        "environment_id": ENV_ID,
        "approvers": ["synthetic-human"],
        "runner_org": "synthetic-org",
        "runner_group_id": GROUP,
        "runner_labels": ["self-hosted", "synthetic"],
        "starter_repository": STARTER,
        "starter_commit": STARTER_SHA,
        "supplier_modules": {
            "scripts/lane3_github_oidc.py": hashlib.sha256(MODULE).hexdigest()
        },
        "admission": {
            "repository": STARTER,
            "path": ".github/lane3-execution.json",
            "commit": STARTER_SHA,
            "sha256": ADMISSION_DIGEST,
        },
        "management": {
            "ssh_alias": "synthetic-host",
            "controller_path": controller_path,
        },
        "resolver": "127.0.0.53",
        "policy_path": "/x/policy.json",
        "bootstrap_manifest_path": "/x/bootstrap.json",
        "bootstrap_manifest_sha256": "a" * 64,
        "bootstrap_proposal_path": "/x/proposal.json",
        "bootstrap_proposal_sha256": "b" * 64,
        "state_path": "/x/state.json",
    }


def config() -> co.Config:
    return co.parse_config(config_doc())


@pytest.mark.parametrize(
    "path",
    [
        "/opt/dotmac-lane3-handoff/" + "a" * 64 + "/lane3_handoff_controller.py",
        "/opt/dotmac-lane3-handoff/" + "A" * 64 + "/lane3_handoff_install.py",
        "/opt/dotmac-lane3-handoff/" + "a" * 63 + "/lane3_handoff_install.py",
        "/opt/dotmac-lane3-handoff/../" + "a" * 64 + "/lane3_handoff_install.py",
        DISPATCHER + "\n",
        "/x/c.py",
    ],
)
def test_management_path_is_only_the_installed_dispatcher(path: str) -> None:
    # Sensitivity: the same document is accepted with the installed dispatcher.
    assert co.parse_config(config_doc()).controller_path == DISPATCHER
    with pytest.raises(co.CoordinatorRefused):
        co.parse_config(config_doc(path))
    with pytest.raises(co.CoordinatorRefused):
        co.ssh_channel("synthetic-host", path)


def world() -> dict[str, Any]:
    base = f"repos/{REPO}/actions/runs/{RUN}"
    return {
        **{
            (
                f"repos/{REPO}/actions/workflows/synthetic.yml/runs"
                f"?status={status}&per_page=100"
            ): {
                "total_count": 0,
                "workflow_runs": [],
            }
            for status in co.ACTIVE_RUN
            if status != "in_progress"
        },
        ADMISSION_PATH: {
            "encoding": "base64",
            "content": base64.b64encode(ADMISSION).decode(),
        },
        base: {
            "id": RUN,
            "run_attempt": 1,
            "head_sha": SHA,
            "path": WF,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "status": "in_progress",
            "repository": {"id": 9, "full_name": REPO},
        },
        f"{base}/attempts/1": {"id": RUN, "run_attempt": 1, "head_sha": SHA},
        f"repos/{REPO}/contents/{WF}?ref={SHA}": {
            "type": "file",
            "path": WF,
            "sha": BLOB,
        },
        f"{base}/attempts/1/jobs?per_page=100": {
            "total_count": 1,
            "jobs": [
                {
                    "id": JOB,
                    "run_id": RUN,
                    "run_attempt": 1,
                    "head_sha": SHA,
                    "status": "in_progress",
                    "runner_id": RUNNER,
                    "runner_group_id": GROUP,
                }
            ],
        },
        f"orgs/synthetic-org/actions/runners/{RUNNER}": {
            "id": RUNNER,
            "busy": True,
            "labels": [{"name": "self-hosted"}, {"name": "synthetic"}],
        },
        f"orgs/synthetic-org/actions/runner-groups/{GROUP}": {
            "id": GROUP,
            "name": "synthetic-protected",
            "visibility": "selected",
            "restricted_to_workflows": True,
            "selected_workflows": [f"{REPO}/{WF}@refs/heads/main"],
        },
        f"orgs/synthetic-org/actions/runner-groups/{GROUP}/repositories?per_page=100": {
            "total_count": 1,
            "repositories": [{"id": 9}],
        },
        f"orgs/synthetic-org/actions/runner-groups/{GROUP}/runners": {
            "total_count": 1,
            "runners": [{"id": RUNNER}],
        },
        f"{base}/pending_deployments": [],
        f"{base}/approvals": [
            {
                "state": "approved",
                "environments": [{"id": ENV_ID, "name": "synthetic-env"}],
                "user": {"login": "synthetic-human"},
            }
        ],
        (
            f"repos/{REPO}/actions/workflows/synthetic.yml/runs"
            "?status=in_progress&per_page=100"
        ): {
            "total_count": 1,
            "workflow_runs": [
                {"id": RUN, "status": "in_progress"},
            ],
        },
        f"repos/{STARTER}/contents/scripts/lane3_github_oidc.py?ref={STARTER_SHA}": {
            "type": "file",
            "path": "scripts/lane3_github_oidc.py",
            "encoding": "base64",
            "content": base64.b64encode(MODULE).decode(),
        },
    }


def api_of(data: dict[str, Any]) -> Any:
    def api(path: str) -> Any:
        if path not in data:
            raise co.CoordinatorRefused("api.unavailable")
        return copy.deepcopy(data[path])

    return api


def report(**expected: Any) -> dict[str, Any]:
    values = {
        "run_id": RUN,
        "run_attempt": 1,
        "workflow_sha": SHA,
        "starter_commit": STARTER_SHA,
    }
    values.update(expected)
    return {
        "protocol": hp.PROTOCOL,
        "op": "report",
        "lease_id": LEASE,
        "nonce": "e" * 64,
        "origin": ORIGIN,
        "flags": {"explicit_port_present": False, "userinfo_present": False},
        "expected": values,
    }


def binding(data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return co.readback_binding(
        api_of(data if data is not None else world()),
        config(),
        run_id=kwargs.pop("run_id", RUN),
        runner_id=kwargs.pop("runner_id", RUNNER),
        report=kwargs.pop("report", report()),
    )


def test_positive_exact_binding() -> None:
    assert binding() == {
        "repository_id": 9,
        "run_id": RUN,
        "run_attempt": 1,
        "job_id": JOB,
        "runner_id": RUNNER,
        "workflow_sha": SHA,
        "workflow_blob": BLOB,
        "starter_commit": STARTER_SHA,
        "admission_digest": ADMISSION_DIGEST,
        "supplier_digest": SUPPLIER_DIGEST,
    }


def _set(path: str, *keys: Any, value: Any) -> Any:
    def mutate(data: dict[str, Any]) -> None:
        target = data[path]
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return mutate


BASE = f"repos/{REPO}/actions/runs/{RUN}"
JOBS = f"{BASE}/attempts/1/jobs?per_page=100"
RUNS = (
    f"repos/{REPO}/actions/workflows/synthetic.yml/runs?status=in_progress&per_page=100"
)
MODPATH = f"repos/{STARTER}/contents/scripts/lane3_github_oidc.py?ref={STARTER_SHA}"


@pytest.mark.parametrize(
    ("mutate", "label"),
    [
        (_set(BASE, "run_attempt", value=2), "binding.mismatch"),
        (_set(BASE, "head_sha", value="f" * 40), "binding.mismatch"),
        (_set(BASE, "path", value=".github/workflows/other.yml"), "binding.mismatch"),
        (_set(BASE, "event", value="push"), "binding.mismatch"),
        (_set(BASE, "head_branch", value="feature"), "binding.mismatch"),
        (_set(BASE, "status", value="completed"), "binding.mismatch"),
        (_set(BASE, "repository", "id", value=10), "binding.mismatch"),
        (_set(BASE, "id", value=True), "binding.mismatch"),
        (_set(f"{BASE}/attempts/1", "head_sha", value="f" * 40), "binding.mismatch"),
        (
            _set(f"repos/{REPO}/contents/{WF}?ref={SHA}", "sha", value="f" * 40),
            "binding.mismatch",
        ),
        (_set(JOBS, "jobs", 0, "runner_id", value=RUNNER + 1), "assignment.ambiguous"),
        (
            _set(JOBS, "jobs", 0, "runner_group_id", value=GROUP + 1),
            "assignment.ambiguous",
        ),
        (_set(JOBS, "total_count", value=2), "assignment.ambiguous"),
        (
            lambda d: (
                d[JOBS]["jobs"].append({**d[JOBS]["jobs"][0], "id": JOB + 1})
                or d[JOBS].update(total_count=2)
            ),
            "assignment.ambiguous",
        ),
        (
            lambda d: (
                d[JOBS]["jobs"].append(
                    {"id": JOB + 1, "status": "queued", "runner_id": None}
                )
                or d[JOBS].update(total_count=2)
            ),
            "assignment.ambiguous",
        ),
        (
            _set(
                f"orgs/synthetic-org/actions/runners/{RUNNER}",
                "labels",
                value=[{"name": "self-hosted"}],
            ),
            "assignment.ambiguous",
        ),
        (
            _set(
                f"orgs/synthetic-org/actions/runner-groups/{GROUP}/runners",
                "total_count",
                value=2,
            ),
            "assignment.ambiguous",
        ),
        (
            lambda d: d.update({f"{BASE}/pending_deployments": None}),
            "approval.missing",
        ),
        (
            lambda d: d.update({f"{BASE}/pending_deployments": [{"x": 1}]}),
            "approval.missing",
        ),
        (lambda d: d.update({f"{BASE}/approvals": []}), "approval.missing"),
        (_set(f"{BASE}/approvals", 0, "state", value="rejected"), "approval.missing"),
        (
            _set(f"{BASE}/approvals", 0, "user", "login", value="someone-else"),
            "approval.missing",
        ),
        (
            _set(
                f"{BASE}/approvals",
                0,
                "environments",
                value=[{"id": ENV_ID + 1, "name": "x"}],
            ),
            "approval.missing",
        ),
        (
            lambda d: d[RUNS]["workflow_runs"].append(
                {"id": RUN + 1, "status": "waiting"}
            ),
            "assignment.ambiguous",
        ),
        (_set(RUNS, "total_count", value=500), "assignment.ambiguous"),
        (
            _set(MODPATH, "content", value=base64.b64encode(b"tampered").decode()),
            "binding.mismatch",
        ),
        (lambda d: d.pop(f"{BASE}/approvals"), "approval.missing"),
    ],
)
def test_each_readback_row_independently_prevents_a_grant(
    mutate: Any, label: str
) -> None:
    data = world()
    mutate(data)
    with pytest.raises(co.CoordinatorRefused) as caught:
        binding(data)
    assert caught.value.label == label
    assert binding()  # sensitivity: the unmutated world binds


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({"run_id": RUN + 1}, "binding.mismatch"),
        ({"runner_id": RUNNER + 1}, "assignment.ambiguous"),
        ({"report": report(run_id=RUN + 1)}, "binding.mismatch"),
        ({"report": report(workflow_sha="f" * 40)}, "binding.mismatch"),
        ({"report": report(starter_commit="f" * 40)}, "binding.mismatch"),
        ({"report": report(run_attempt=2)}, "approval.missing"),
    ],
)
def test_report_claims_never_override_readback(
    kwargs: dict[str, Any], label: str
) -> None:
    with pytest.raises(co.CoordinatorRefused) as caught:
        binding(**kwargs)
    assert caught.value.label == label


# ── decision ───────────────────────────────────────────────────────────────


POLICY = {
    "schema": "dotmac.lane3.handoff-policy.v1",
    "version": 1,
    "origins": {"schema": "dotmac.lane3.broker-origin-policy.v1", "origins": [ORIGIN]},
    "aliases": {"exact": [], "namespaces": []},
}


class Channel:
    def __init__(self, report_value: dict[str, Any] | None, state: str = "REPORTED"):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.report = report_value
        self.state = state
        self.manifest_digest: str | None = None

    def __call__(self, op: str, payload: Any) -> dict[str, Any]:
        self.calls.append((op, dict(payload)))
        if op == "status":
            return {
                "protocol": hp.PROTOCOL,
                "op": "status",
                "lease_id": LEASE,
                "state": self.state,
                "category": None,
                "report": self.report,
                "report_digest": hp.digest(self.report) if self.report else None,
                "remaining_ms": 200_000,
            }
        if op == "timing":
            return {
                "protocol": hp.PROTOCOL,
                "op": op,
                "lease_id": LEASE,
                "challenge": "a" * 64,
            }
        if op == "grant":
            return {
                "protocol": hp.PROTOCOL,
                "op": "grant",
                "lease_id": LEASE,
                "state": "GRANTED",
                "grant_digest": "0" * 64,
                "manifest_digest": self.manifest_digest or "1" * 64,
            }
        return {
            "protocol": hp.PROTOCOL,
            "op": "cleanup",
            "lease_id": LEASE,
            "state": "CLOSED",
            "evidence": {},
        }

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]


BOOTSTRAP = {
    "schema": "dotmac.lane3.handoff-bootstrap.v1",
    "destinations": [
        {
            "purpose": "runner-https",
            "origin": "https://runner.example",
            "family": 4,
            "address": "8.8.4.4",
            "port": 443,
        }
    ],
}


def snapshot(ttl_s: int = 60) -> hr.Snapshot:
    return hr.Snapshot(
        origin=ORIGIN,
        resolver="127.0.0.53",
        chain=("broker.example",),
        addresses=((4, "8.8.8.8"),),
        ttl_s=ttl_s,
        captured_at_monotonic_ns=0,
        expires_at_monotonic_ns=ttl_s * 10**9,
        digest="2" * 64,
    )


def decide(
    channel: Channel, *, data: dict[str, Any] | None = None, **kw: Any
) -> co.Decision:
    resolved: list[str] = []

    def resolve(origin: str, **_: Any) -> hr.Snapshot:
        resolved.append(origin)
        if "resolve_error" in kw:
            raise hr.SnapshotRefused(kw["resolve_error"])
        return kw.get("snap", snapshot())

    decision = co.decide(
        cfg=config(),
        api=api_of(data if data is not None else world()),
        channel=channel,
        lease=LEASE,
        run_id=RUN,
        runner_id=RUNNER,
        qualification=co.prelaunch_binding(api_of(prelaunch_world()), config(), RUN),
        policy_doc=kw.get("policy", POLICY),
        bootstrap_manifest=BOOTSTRAP,
        resolve=resolve,
        clock_ns=lambda: 0,
        sleep=lambda _: None,
    )
    decision_resolved = list(resolved)
    channel.resolved = decision_resolved  # type: ignore[attr-defined]
    return decision


def test_positive_decision_grants_once_with_the_exact_manifest() -> None:
    import lane3_handoff_controller as hc

    channel = Channel(report())
    rows = snapshot().rows()
    channel.manifest_digest = hp.digest(hc.effective_manifest(BOOTSTRAP, ORIGIN, rows))
    decision = decide(channel)
    assert decision.status == "GRANTED"
    assert channel.ops() == ["status", "timing", "grant"]
    grant_request = channel.calls[2][1]
    assert grant_request["binding"]["job_id"] == JOB
    assert grant_request["snapshot"]["addresses"] == rows
    assert grant_request["snapshot"]["ttl_remaining_ms"] == 60_000


def test_unlisted_origin_refuses_without_resolution_or_grant() -> None:
    channel = Channel(report())
    policy = copy.deepcopy(POLICY)
    policy["origins"]["origins"] = ["https://other-broker.example"]
    decision = decide(channel, policy=policy)
    assert (decision.status, decision.category) == ("REFUSED", "origin.not_admitted")
    assert "grant" not in channel.ops()
    assert channel.resolved == []  # type: ignore[attr-defined]
    assert channel.calls[-1] == (
        "refuse",
        co.request("refuse", lease_id=LEASE, category="origin.not_admitted"),
    )


@pytest.mark.parametrize(
    ("kw", "label"),
    [
        ({"data": {}}, "binding.mismatch"),
        ({"resolve_error": "dns.cname_loop"}, "snapshot.refused"),
        ({"snap": snapshot(ttl_s=44)}, "deadline.insufficient"),
    ],
)
def test_decision_refusals_never_reach_grant(kw: dict[str, Any], label: str) -> None:
    channel = Channel(report())
    decision = decide(channel, **kw)
    assert (decision.status, decision.category) == ("REFUSED", label)
    assert "grant" not in channel.ops()


def test_flags_and_bad_reports_refuse() -> None:
    flagged = report()
    flagged["flags"]["userinfo_present"] = True
    channel = Channel(flagged)
    assert decide(channel).category == "origin.flags"
    bad = report()
    bad["origin"] = ORIGIN + ":443"
    channel = Channel(bad)
    assert decide(channel).category == "origin.invalid"
    assert "grant" not in channel.ops()


def test_manifest_mismatch_after_grant_refuses() -> None:
    channel = Channel(report())
    channel.manifest_digest = "9" * 64
    decision = decide(channel)
    assert decision.category == "grant.mismatch"
    assert channel.ops()[-1] == "refuse"


def test_a_lease_that_never_reports_is_refused() -> None:
    channel = Channel(None, state="EXPIRED")
    decision = decide(channel)
    assert decision.status == "REFUSED"
    assert "grant" not in channel.ops()


def test_management_responses_are_schema_checked() -> None:
    with pytest.raises(co.CoordinatorRefused) as caught:
        co.validate_mgmt_response(
            "status",
            {"protocol": hp.PROTOCOL, "status": "REFUSED", "category": "lease.unknown"},
        )
    assert caught.value.label == "lease.unknown"
    with pytest.raises(co.CoordinatorRefused):
        co.validate_mgmt_response(
            "grant", {"protocol": hp.PROTOCOL, "op": "grant", "extra": 1}
        )


def test_channel_refuses_unvalidated_payloads_before_any_process() -> None:
    call = co.ssh_channel("synthetic-host", DISPATCHER)
    with pytest.raises(co.CoordinatorRefused):
        call(
            "status",
            {"protocol": hp.PROTOCOL, "op": "status", "lease_id": LEASE, "cmd": "id"},
        )
    with pytest.raises(co.CoordinatorRefused):
        call("sh", {})
    with pytest.raises(co.CoordinatorRefused):
        co.ssh_channel("synthetic-host; id", DISPATCHER)
    with pytest.raises(co.CoordinatorRefused):
        co.ssh_channel("synthetic-host", DISPATCHER + "; id")


def test_remote_command_runs_the_controller_without_bytecode(
    monkeypatch: Any,
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> Any:
        seen.append(argv)
        raise OSError

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    call = co.ssh_channel("synthetic-host", DISPATCHER)
    with pytest.raises(co.CoordinatorRefused):
        call(
            "status",
            {"protocol": hp.PROTOCOL, "op": "status", "lease_id": LEASE},
        )
    assert seen and seen[0][-1] == f"sudo -n /usr/bin/python3 -I -B {DISPATCHER} status"


def test_prelaunch_missing_approval_has_no_jit_or_bootstrap(
    tmp_path: Any, monkeypatch: Any
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    qualification = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": qualification},
    )
    data = prelaunch_world()
    data[f"{BASE}/approvals"] = []
    effects: list[Any] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append(path)
        return api_of(data)(path)

    with pytest.raises(co.CoordinatorRefused):
        co.op_launch(
            cfg,
            lambda *args: effects.append(args),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    assert effects == []


def prelaunch_world() -> dict[str, Any]:
    data = world()
    # Documented queued/unassigned JSON null shape; no live snapshot is claimed.
    data[JOBS]["jobs"][0].update(
        status="queued",
        runner_id=None,
        runner_name=None,
        runner_group_id=None,
        runner_group_name=None,
        labels=[],
        steps=[],
    )
    data[f"orgs/synthetic-org/actions/runner-groups/{GROUP}/runners"] = {
        "total_count": 0,
        "runners": [],
    }
    return data


def test_prelaunch_canary_is_sensitive_to_approval_predicate(
    tmp_path: Any, monkeypatch: Any
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    data = prelaunch_world()
    good = co.prelaunch_binding(api_of(data), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    data[f"{BASE}/approvals"] = []
    monkeypatch.setattr(co, "prelaunch_binding", lambda *args: good)
    effects: list[str] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append("jit")
            return {"runner": {"id": RUNNER}, "encoded_jit_config": "c3ludGhldGlj"}
        return api_of(data)(path)

    co.op_launch(
        cfg,
        lambda *args: {"state": "BOOTSTRAP"},
        api,
        RUN,
        deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
    )
    assert effects == ["jit"]  # the original no-effects canary would go red


@pytest.mark.parametrize(
    "mutate",
    [
        _set(BASE, "head_sha", value="f" * 40),
        _set(BASE, "run_attempt", value=2),
        _set(f"repos/{REPO}/contents/{WF}?ref={SHA}", "sha", value="f" * 40),
        lambda data: data.pop(ADMISSION_PATH),
        _set(ADMISSION_PATH, "content", value=base64.b64encode(b"{}").decode()),
        lambda data: data[RUNS]["workflow_runs"].append(
            {"id": RUN + 1, "status": "waiting"}
        ),
        _set(JOBS, "jobs", 0, "runner_id", value=RUNNER),
    ],
)
def test_every_failed_prelaunch_predicate_prevents_effects(
    tmp_path: Any, mutate: Any
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    data = prelaunch_world()
    mutate(data)
    effects: list[Any] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append(path)
        return api_of(data)(path)

    with pytest.raises(co.CoordinatorRefused):
        co.op_launch(
            cfg,
            lambda *args: effects.append(args),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    assert effects == []


def closed_reply() -> dict[str, Any]:
    return {
        "protocol": hp.PROTOCOL,
        "op": "cleanup",
        "lease_id": LEASE,
        "state": "CLOSED",
        "evidence": co.hc.evidence(
            {"lease_id": LEASE, "outcome": "EXPIRED", "category": None}
        ),
    }


@pytest.mark.parametrize("removed", [True, False])
def test_cleanup_removes_exact_jit_and_keeps_state_until_absence_readback(
    tmp_path: Any, removed: bool
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "runner_id": RUNNER},
    )
    row = {"id": RUNNER, "name": f"lane3-handoff-{LEASE}"}
    deleted: list[int] = []

    def api(path: str, method: str = "GET") -> Any:
        if method == "DELETE":
            assert path.endswith(f"/runners/{RUNNER}")
            deleted.append(RUNNER)
            return None
        rows = [] if removed and deleted else [row]
        return {"total_count": len(rows), "runners": rows}

    if removed:
        co.op_cleanup(cfg, lambda *args: closed_reply(), api)
        assert not cfg.state_path.exists()
    else:
        with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
            co.op_cleanup(cfg, lambda *args: closed_reply(), api)
        assert cfg.state_path.exists()
    assert deleted == [RUNNER]


def test_operation_lock_blocks_second_jit_for_same_state(
    tmp_path: Any, monkeypatch: Any
) -> None:
    import threading

    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    monkeypatch.setattr(co.hc, "LOCK_WAIT_S", 0)
    first_jit = threading.Event()
    release = threading.Event()
    effects: list[str] = []
    errors: list[BaseException] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append("jit")
            first_jit.set()
            assert release.wait(2)
            return {"runner": {"id": RUNNER}, "encoded_jit_config": "c3ludGhldGlj"}
        return api_of(prelaunch_world())(path)

    def launch() -> None:
        try:
            co.op_launch(
                cfg,
                lambda *args: {"state": "BOOTSTRAP"},
                api,
                RUN,
                deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=launch)
    worker.start()
    try:
        assert first_jit.wait(2)
        with pytest.raises(co.CoordinatorRefused, match="lock.busy"):
            co.op_launch(
                cfg,
                lambda *args: {"state": "BOOTSTRAP"},
                api,
                RUN,
                deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
            )
        assert effects == ["jit"]
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and errors == []
    with pytest.raises(co.CoordinatorRefused, match="state.invalid"):
        co.op_launch(
            cfg,
            lambda *args: {"state": "BOOTSTRAP"},
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    assert effects == ["jit"]


def test_second_jit_canary_detects_removed_operation_lock(
    tmp_path: Any, monkeypatch: Any
) -> None:
    import contextlib

    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    monkeypatch.setattr(co.hc, "LOCK_WAIT_S", 0)
    effects: list[str] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append("jit")
            return {"runner": {"id": RUNNER}, "encoded_jit_config": "c3ludGhldGlj"}
        return api_of(prelaunch_world())(path)

    def canary() -> bool:
        try:
            co.op_launch(
                cfg,
                lambda *args: {"state": "BOOTSTRAP"},
                api,
                RUN,
                deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
            )
        except co.CoordinatorRefused as exc:
            return exc.label == "lock.busy" and effects == []
        return False

    with co.operation_lock(cfg.state_path):
        assert canary()
        monkeypatch.setattr(co, "operation_lock", lambda path: contextlib.nullcontext())
        assert not canary()  # named no-second-JIT canary goes red
    assert effects == ["jit"]


@pytest.mark.parametrize(
    "failure", ["response-lost", "runner-save-failed", "delete-response-lost"]
)
def test_uncertain_registration_is_recovered_from_durable_exact_intent(
    tmp_path: Any, monkeypatch: Any, failure: str
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    row = {"id": RUNNER, "name": f"lane3-handoff-{LEASE}"}
    registrations: list[str] = []
    deleted: list[int] = []
    bootstrap: list[Any] = []
    original_save = co.save_state
    runner_save_interrupted = False

    def save(path: Any, state: Any) -> None:
        nonlocal runner_save_interrupted
        if (
            failure == "runner-save-failed"
            and "runner_id" in state
            and not runner_save_interrupted
        ):
            runner_save_interrupted = True
            raise OSError("synthetic save interruption")
        original_save(path, state)

    monkeypatch.setattr(co, "save_state", save)

    def api(path: str, method: str = "GET", payload: Any = None) -> Any:
        if method == "POST":
            durable = co.load_state(cfg.state_path)
            assert durable["launch_intent"]["name"] == row["name"]
            assert "runner_id" not in durable
            assert payload["name"] == row["name"]
            registrations.append(row["name"])
            if failure in {"response-lost", "delete-response-lost"}:
                raise co.CoordinatorRefused("api.unavailable")
            return {"runner": row, "encoded_jit_config": "c3ludGhldGlj"}
        if method == "DELETE":
            assert path.endswith(f"/runners/{RUNNER}")
            assert co.load_state(cfg.state_path)["runner_id"] == RUNNER
            deleted.append(RUNNER)
            if failure == "delete-response-lost":
                raise co.CoordinatorRefused("api.unavailable")
            return None
        if path.endswith("runners?per_page=100"):
            rows = [] if deleted else [row]
            return {"total_count": len(rows), "runners": rows}
        return api_of(prelaunch_world())(path)

    with pytest.raises((co.CoordinatorRefused, OSError)):
        co.op_launch(
            cfg,
            lambda *args: bootstrap.append(args),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    durable = co.load_state(cfg.state_path)
    assert (
        durable["launch_intent"]["name"] == row["name"] and "runner_id" not in durable
    )
    with pytest.raises(co.CoordinatorRefused, match="state.invalid"):
        co.op_launch(
            cfg,
            lambda *args: bootstrap.append(args),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    assert registrations == [row["name"]] and bootstrap == []
    monkeypatch.setattr(co, "save_state", original_save)
    if failure == "delete-response-lost":
        with pytest.raises(co.CoordinatorRefused, match="api.unavailable"):
            co.op_cleanup(cfg, lambda *args: closed_reply(), api)
        assert co.load_state(cfg.state_path)["runner_id"] == RUNNER
    co.op_cleanup(cfg, lambda *args: closed_reply(), api)
    assert deleted == [RUNNER] and not cfg.state_path.exists()


@pytest.mark.parametrize("case", ["unknown-absent", "duplicate-name", "foreign-group"])
def test_uncertain_registration_cannot_delete_unowned_or_ambiguous_runners(
    tmp_path: Any, case: str
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    name = f"lane3-handoff-{LEASE}"
    co.save_state(
        cfg.state_path,
        {
            "schema": co.STATE_SCHEMA,
            "lease_id": LEASE,
            "qualification": good,
            "run_id": RUN,
            "launch_intent": {
                "name": name,
                "runner_group_id": GROUP,
                "run_id": RUN,
                "qualification_digest": hp.digest(good),
            },
        },
    )
    row = {"id": RUNNER, "name": name}
    deleted: list[Any] = []
    unrelated = {"id": RUNNER + 9, "name": "lane3-handoff-foreign"}

    def api(path: str, method: str = "GET") -> Any:
        if method == "DELETE":
            deleted.append(path)
            return None
        rows = [unrelated]
        if case == "duplicate-name":
            rows += [row, {**row, "id": RUNNER + 1}]
        elif case == "foreign-group" and "runner-groups/" not in path:
            rows += [row]
        return {"total_count": len(rows), "runners": rows}

    with pytest.raises(co.CoordinatorRefused):
        co.op_cleanup(cfg, lambda *args: closed_reply(), api)
    assert deleted == [] and cfg.state_path.exists()


def test_registration_canary_detects_removed_durable_intent(
    tmp_path: Any, monkeypatch: Any
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    co.save_state(
        cfg.state_path,
        {"schema": co.STATE_SCHEMA, "lease_id": LEASE, "qualification": good},
    )
    original_save = co.save_state

    def weakened_save(path: Any, state: Any) -> None:
        original_save(
            path, {key: value for key, value in state.items() if key != "launch_intent"}
        )

    monkeypatch.setattr(co, "save_state", weakened_save)

    def api(path: str, *args: Any) -> Any:
        if args:
            assert "launch_intent" in co.load_state(cfg.state_path)
            pytest.fail("weakened guard unexpectedly passed the registration canary")
        return api_of(prelaunch_world())(path)

    with pytest.raises(AssertionError):
        co.op_launch(
            cfg,
            lambda *args: pytest.fail("bootstrap forbidden"),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )


def test_historical_runs_do_not_block_complete_active_exclusivity() -> None:
    data = world()
    # An old unfiltered-history endpoint may contain arbitrary terminal volume;
    # it is not queried when deciding current active exclusivity.
    data[f"repos/{REPO}/actions/workflows/synthetic.yml/runs?per_page=100"] = {
        "total_count": 10_000,
        "workflow_runs": [{"id": RUN - 1, "status": "completed"}],
    }
    assert binding(data)["run_id"] == RUN
    pending = (
        f"repos/{REPO}/actions/workflows/synthetic.yml/runs?status=pending&per_page=100"
    )
    data[pending] = {"total_count": 101, "workflow_runs": []}
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        binding(data)


GROUP_PATH = f"orgs/synthetic-org/actions/runner-groups/{GROUP}"
REPOSITORIES = f"{GROUP_PATH}/repositories?per_page=100"
GROUP_DRIFT = [
    *[_set(GROUP_PATH, "id", value=v) for v in (GROUP + 1, True, 4.0, None)],
    _set(GROUP_PATH, "name", value="renamed"),
    _set(GROUP_PATH, "visibility", value="all"),
    *[_set(GROUP_PATH, "restricted_to_workflows", value=v) for v in (False, 1, None)],
    *[
        _set(GROUP_PATH, "selected_workflows", value=v)
        for v in (
            [],
            [f"{REPO}/{WF}@main"],
            [f"{REPO}/{WF}@refs/heads/other"],
            [f"{REPO}/{WF}@{SHA}"],
            [f"{REPO}/{WF}@refs/tags/main"],
            [f"other/repository/{WF}@refs/heads/main"],
            [f"{REPO}/{WF}@refs/heads/main", "extra"],
        )
    ],
    *[
        _set(REPOSITORIES, "repositories", value=v)
        for v in (
            [],
            [{"id": 10}],
            [{"id": True}],
            [{"id": 9.0}],
            [{}],
            [{"id": 9}, {"id": 10}],
        )
    ],
    *[_set(REPOSITORIES, "total_count", value=v) for v in (2, True, 1.0, None)],
    lambda d: d.pop(GROUP_PATH),
    lambda d: d.pop(REPOSITORIES),
    *[
        lambda d, key=key: d[GROUP_PATH].pop(key)
        for key in (
            "id",
            "name",
            "visibility",
            "restricted_to_workflows",
            "selected_workflows",
        )
    ],
    lambda d: d[REPOSITORIES].pop("total_count"),
    lambda d: d[REPOSITORIES].pop("repositories"),
]


@pytest.mark.parametrize("mutate", GROUP_DRIFT)
def test_group_drift_refuses_prepare_launch_and_decide_without_effects(
    tmp_path: Any, mutate: Any
) -> None:
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    data = prelaunch_world()
    mutate(data)
    effects: list[Any] = []

    def api(path: str, *args: Any) -> Any:
        if args:
            effects.append(path)
        return api_of(data)(path)

    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.op_prepare(cfg, lambda *args: effects.append(args), api, RUN)
    assert not cfg.state_path.exists()
    co.save_state(
        cfg.state_path,
        {
            "schema": co.STATE_SCHEMA,
            "lease_id": LEASE,
            "qualification": good,
        },
    )
    before = cfg.state_path.read_bytes()
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.op_launch(
            cfg,
            lambda *args: effects.append(args),
            api,
            RUN,
            deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
        )
    assert effects == [] and cfg.state_path.read_bytes() == before
    assigned = world()
    mutate(assigned)
    channel = Channel(report())
    decision = decide(channel, data=assigned)
    assert decision.category == "assignment.ambiguous"
    assert "grant" not in channel.ops() and channel.resolved == []
    assert binding()  # the same fully bound world remains reachable


QUEUED_DRIFT = [
    *[
        _set(JOBS, "jobs", 0, key, value=value)
        for key, values in {
            "runner_group_id": [0, False, 0.0, 4.0, True, GROUP + 1],
            "runner_id": [0, False, 0.0, RUNNER],
            "runner_name": ["", "hosted-runner"],
            "runner_group_name": ["", "other-group"],
            "labels": [["ubuntu-latest"], None],
            "steps": [[{"name": "started"}], None],
            "run_id": [True, float(RUN), RUN + 1],
            "run_attempt": [True, 1.0, 2],
            "head_sha": [None, "f" * 40],
            "status": ["waiting", "in_progress"],
        }.items()
        for value in values
    ],
    *[
        lambda d, key=key: d[JOBS]["jobs"][0].pop(key)
        for key in (
            "runner_id",
            "runner_name",
            "runner_group_id",
            "runner_group_name",
            "labels",
            "steps",
            "run_id",
            "run_attempt",
            "head_sha",
            "status",
        )
    ],
]


@pytest.mark.parametrize("mutate", QUEUED_DRIFT)
def test_null_group_requires_explicit_typed_unassigned_shape(
    tmp_path: Any, mutate: Any
) -> None:
    data = prelaunch_world()
    mutate(data)
    cfg = dataclasses.replace(config(), state_path=tmp_path / "state.json")
    effects: list[Any] = []
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.op_prepare(cfg, lambda *args: effects.append(args), api_of(data), RUN)
    assert effects == [] and not cfg.state_path.exists()
    test_every_failed_prelaunch_predicate_prevents_effects(tmp_path, mutate)
    data = prelaunch_world()
    mutate(data)
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.prelaunch_binding(api_of(data), config(), RUN)
    assert co.prelaunch_binding(api_of(prelaunch_world()), config(), RUN)


@pytest.mark.parametrize("runner_id", [0, None])
@pytest.mark.parametrize("runner_name", [None, ""])
def test_explicit_positive_group_prelaunch_remains_supported(
    runner_id: Any, runner_name: Any
) -> None:
    data = prelaunch_world()
    data[JOBS]["jobs"][0].update(
        runner_group_id=GROUP, runner_id=runner_id, runner_name=runner_name
    )
    assert co.prelaunch_binding(api_of(data), config(), RUN)


def zero_shape_world() -> dict[str, Any]:
    # Live GitHub shape for a queued, never-assigned job (run 38526558257,
    # job 115728197720): explicit zeros and empty strings, not nulls.
    data = prelaunch_world()
    data[JOBS]["jobs"][0].update(
        runner_group_id=0,
        runner_id=0,
        runner_name="",
        runner_group_name="",
        labels=[],
        steps=[],
    )
    return data


def test_explicit_zero_group_prelaunch_accepts_fully_unassigned_shape() -> None:
    zero = co.prelaunch_binding(api_of(zero_shape_world()), config(), RUN)
    null = co.prelaunch_binding(api_of(prelaunch_world()), config(), RUN)
    assert zero
    # The unassigned representation carries no runner/group data into the
    # qualification, so both shapes bind identically downstream.
    assert zero == null


@pytest.mark.parametrize(
    "key,value",
    [
        ("runner_group_id", False),
        ("runner_group_id", 0.0),
        ("runner_group_id", GROUP + 1),
        ("runner_id", None),
        ("runner_id", False),
        ("runner_id", 0.0),
        ("runner_id", RUNNER),
        ("runner_name", None),
        ("runner_name", "assigned"),
        ("runner_group_name", None),
        ("runner_group_name", "other-group"),
        ("labels", ["ubuntu-latest"]),
        ("labels", None),
        ("steps", [{"name": "started"}]),
        ("steps", None),
    ],
)
def test_zero_group_refuses_any_mixed_or_started_shape(key: str, value: Any) -> None:
    data = zero_shape_world()
    data[JOBS]["jobs"][0][key] = value
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.prelaunch_binding(api_of(data), config(), RUN)


@pytest.mark.parametrize(
    "key", ["runner_id", "runner_name", "runner_group_name", "labels", "steps"]
)
def test_zero_group_requires_present_unassigned_fields(key: str) -> None:
    data = zero_shape_world()
    data[JOBS]["jobs"][0].pop(key)
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.prelaunch_binding(api_of(data), config(), RUN)


@pytest.mark.parametrize(
    "group",
    [
        None,
        {},
        {"id": GROUP},
        {"id": GROUP + 1, "name": "synthetic-protected"},
        {"id": True, "name": "synthetic-protected"},
        {"id": 4.0, "name": "synthetic-protected"},
        {"id": GROUP, "name": None},
        {"id": GROUP, "name": ""},
    ],
)
def test_pinned_admission_must_bind_the_configured_group(group: Any) -> None:
    import json

    data = prelaunch_world()
    admitted = json.loads(ADMISSION)
    admitted["runner_group"] = group
    raw = hp.canonical_json(admitted)
    data[ADMISSION_PATH]["content"] = base64.b64encode(raw).decode()
    cfg = dataclasses.replace(
        config(),
        admission={
            **config().admission,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    with pytest.raises(co.CoordinatorRefused, match="binding.mismatch"):
        co.prelaunch_binding(api_of(data), cfg, RUN)


@pytest.mark.parametrize(
    "key,value",
    [
        ("runner_id", False),
        ("runner_id", 0.0),
        ("runner_name", "assigned"),
        ("runner_name", False),
        ("steps", [{"name": "started"}]),
        ("steps", None),
    ],
)
def test_positive_group_still_requires_a_never_started_job(
    key: str, value: Any
) -> None:
    data = prelaunch_world()
    data[JOBS]["jobs"][0].update(runner_group_id=GROUP, runner_id=0)
    data[JOBS]["jobs"][0][key] = value
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.prelaunch_binding(api_of(data), config(), RUN)


@pytest.mark.parametrize(
    "key,value",
    [
        ("runner_id", float(RUNNER)),
        ("runner_id", True),
        ("runner_group_id", 4.0),
        ("runner_group_id", True),
        ("run_id", float(RUN)),
        ("run_attempt", True),
    ],
)
def test_decide_rejects_coercible_assigned_job_fields(key: str, value: Any) -> None:
    data = world()
    data[JOBS]["jobs"][0][key] = value
    channel = Channel(report())
    decision = decide(channel, data=data)
    assert decision.category == "assignment.ambiguous"
    assert "grant" not in channel.ops() and channel.resolved == []


@pytest.mark.parametrize("key", ["runner_id", "runner_name", "steps"])
def test_positive_group_requires_present_unassigned_fields(key: str) -> None:
    data = prelaunch_world()
    data[JOBS]["jobs"][0].update(runner_group_id=GROUP, runner_id=0)
    data[JOBS]["jobs"][0].pop(key)
    with pytest.raises(co.CoordinatorRefused, match="assignment.ambiguous"):
        co.prelaunch_binding(api_of(data), config(), RUN)


@pytest.mark.parametrize(
    "mutate",
    [
        _set(JOBS, "total_count", value=2),
        lambda d: d[JOBS]["jobs"].append({**d[JOBS]["jobs"][0], "id": JOB + 1})
        or d[JOBS].update(total_count=2),
        _set(f"{GROUP_PATH}/runners", "total_count", value=1),
        _set(f"{GROUP_PATH}/runners", "runners", value=[{"id": RUNNER}]),
    ],
)
def test_null_group_does_not_relax_exclusivity(tmp_path: Any, mutate: Any) -> None:
    test_every_failed_prelaunch_predicate_prevents_effects(tmp_path, mutate)


@pytest.fixture(autouse=True)
def legacy_binding_tests_use_a_separate_admitted_dns_capture(monkeypatch):
    """Existing GitHub/cleanup canaries isolate their pre-existing predicates.

    The real new capture path is exercised in test_lane3_campaign_freshness.py
    and the canonical TCP wire suite; this substitute never weakens production.
    """

    def fresh(*args):
        return (
            SimpleNamespace(
                digest="a" * 64,
                provenance_digest="b" * 64,
                remaining_ns=lambda now: 60_000_000_000,
            ),
            {"challenge": "c" * 64},
        )

    monkeypatch.setattr(co, "fresh_bootstrap", fresh)
    monkeypatch.setattr(co, "retain_bootstrap_provenance", lambda *args: None)


@pytest.mark.parametrize("purpose", [None, "", "skip_admission", "Qualification"])
def test_unknown_purpose_never_creates_authority(purpose: Any) -> None:
    doc = config_doc()
    doc["purpose"] = purpose
    with pytest.raises(co.CoordinatorRefused):
        co.parse_config(doc)


def test_legacy_config_is_recovery_only() -> None:
    doc = config_doc()
    doc["schema"] = co.LEGACY_CONFIG_SCHEMA
    for key in ("purpose", "source_capability", "evidence_sha256"):
        doc.pop(key)
    with pytest.raises(co.CoordinatorRefused):
        co.parse_config(doc)
    cfg = co.parse_config(doc, recovery=True)
    with pytest.raises(co.CoordinatorRefused):
        co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)


def test_capability_manifest_binds_sources_and_finite_operations() -> None:
    assert co.parse_config(config_doc()).purpose == "qualification"
    for key, value in (
        ("workflow_blob", "f" * 40),
        ("operations", ["candidate-install"]),
        ("terminal", "success"),
    ):
        doc = config_doc()
        doc["source_capability"][key] = value
        with pytest.raises(co.CoordinatorRefused):
            co.parse_config(doc)


def test_phase_recheck_rejects_changed_capability() -> None:
    cfg = config()
    good = co.prelaunch_binding(api_of(prelaunch_world()), cfg, RUN)
    changed = dataclasses.replace(cfg, config_digest="f" * 64)
    with pytest.raises(co.CoordinatorRefused, match="binding.mismatch"):
        co.readback_binding(
            api_of(world()),
            changed,
            run_id=RUN,
            runner_id=RUNNER,
            report=report(),
            expected_qualification=good,
        )


def execution_world() -> tuple[co.Config, dict[str, Any], str]:
    from tests.unit.lane3_evidence_support import REFERENCE, rows, table

    raw_evidence = table(rows()).encode()
    doc = config_doc()
    doc.update(
        purpose="admitted_execution",
        source_capability=None,
        evidence_sha256=hashlib.sha256(raw_evidence).hexdigest(),
    )
    admitted = json.loads(ADMISSION)
    admitted["admission_evidence"] = REFERENCE
    raw_topology = hp.canonical_json(admitted)
    doc["admission"]["sha256"] = hashlib.sha256(raw_topology).hexdigest()
    data = prelaunch_world()
    data[ADMISSION_PATH] = {
        "encoding": "base64",
        "content": base64.b64encode(raw_topology).decode(),
    }
    path = f"repos/{STARTER}/contents/{co.le.EVIDENCE_FILE}?ref={STARTER_SHA}"
    data[path] = {
        "type": "file",
        "path": co.le.EVIDENCE_FILE,
        "encoding": "base64",
        "content": base64.b64encode(raw_evidence).decode(),
    }
    tree_sha = "e" * 40
    data[f"repos/{STARTER}/git/commits/{STARTER_SHA}"] = {
        "sha": STARTER_SHA,
        "tree": {"sha": tree_sha},
    }
    entries = []
    for file_path, raw in (
        (".github/lane3-execution.json", raw_topology),
        (co.le.EVIDENCE_FILE, raw_evidence),
    ):
        git_blob = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        entries.append(
            {
                "path": file_path,
                "mode": "100644",
                "type": "blob",
                "sha": git_blob,
                "size": len(raw),
            }
        )
    data[f"repos/{STARTER}/git/trees/{tree_sha}?recursive=1"] = {
        "sha": tree_sha,
        "truncated": False,
        "tree": entries,
    }
    return co.parse_config(doc), data, path


def test_complete_real_table_is_checked_but_execution_has_no_owner(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    cfg, data, path = execution_world()
    cfg = dataclasses.replace(cfg, state_path=tmp_path / "state")
    checked: list[pathlib.Path] = []
    validator = co.le.require_admission_evidence

    def guard(reference: Any, *, repo_root: pathlib.Path) -> str:
        assert (
            repo_root / ".github/lane3-execution.json"
        ).read_bytes() == base64.b64decode(data[ADMISSION_PATH]["content"])
        assert not (repo_root / co.le.EVIDENCE_FILE).is_symlink()
        checked.append(repo_root)
        return validator(reference, repo_root=repo_root)

    monkeypatch.setattr(co.le, "require_admission_evidence", guard)
    effects: list[Any] = []
    with pytest.raises(co.CoordinatorRefused, match="execution.unsupported"):
        co.op_prepare(cfg, lambda *args: effects.append(args), api_of(data), RUN)
    assert checked and not checked[0].exists()
    assert effects == []


@pytest.mark.parametrize(
    "defect",
    ["tampered", "symlink", "other-path", "cross-commit", "missing"],
)
def test_same_commit_evidence_data_refuses_planted_changes(defect: str) -> None:
    cfg, data, path = execution_world()
    if defect == "tampered":
        data[path]["content"] = base64.b64encode(b"tampered").decode()
    elif defect == "symlink":
        data[path]["type"] = "symlink"
    elif defect == "other-path":
        data[path]["path"] = "elsewhere.md"
    elif defect == "cross-commit":
        cfg = dataclasses.replace(cfg, admission={**cfg.admission, "commit": "f" * 40})
    elif defect == "missing":
        data.pop(path)
    with pytest.raises(co.CoordinatorRefused):
        co.prelaunch_binding(api_of(data), cfg, RUN)


@pytest.mark.parametrize("schema", [co.STATE_SCHEMA, co.LEGACY_STATE_SCHEMA])
def test_owned_cleanup_survives_invalid_purpose_and_evidence(
    tmp_path: pathlib.Path, schema: str
) -> None:
    doc = config_doc()
    doc.update(purpose="unclassified", source_capability=None, evidence_sha256="broken")
    cfg = dataclasses.replace(
        co.parse_config(doc, recovery=True), state_path=tmp_path / "state"
    )
    original = {
        "schema": schema,
        "lease_id": LEASE,
        "qualification": {"original": "retained"},
    }
    co.save_state(cfg.state_path, original)
    calls: list[str] = []

    def channel(op: str, req: Any) -> Any:
        calls.append(op)
        assert req["lease_id"] == LEASE
        return closed_reply()

    def no_api(*args: Any) -> Any:
        pytest.fail("cleanup consulted admission or GitHub")

    co.op_cleanup(cfg, channel, no_api)
    assert calls == ["cleanup"]
    assert not cfg.state_path.exists()


def test_factory_requires_explicit_reviewed_v2_inputs_and_preserves_input() -> None:
    doc = config_doc()
    legacy = copy.deepcopy(doc)
    legacy["schema"] = co.LEGACY_CONFIG_SCHEMA
    for key in ("purpose", "source_capability", "evidence_sha256"):
        legacy.pop(key)
    before = copy.deepcopy(legacy)
    rendered = co.assemble_v2_config(
        legacy,
        purpose="qualification",
        source_capability=doc["source_capability"],
        evidence_sha256=None,
    )
    assert rendered == doc
    assert legacy == before
    with pytest.raises(co.CoordinatorRefused):
        co.assemble_v2_config(
            legacy,
            purpose="qualification",
            source_capability=None,
            evidence_sha256=None,
        )
    assert co.authority_binding(co.parse_config(rendered))[
        "config_digest"
    ] == hp.digest(rendered)


@pytest.mark.parametrize(
    "defect", ["git-symlink", "truncated", "wrong-blob", "missing-mode"]
)
def test_contents_api_cannot_hide_git_tree_symlink_or_changed_blob(defect: str) -> None:
    cfg, data, _ = execution_world()
    tree = data[f"repos/{STARTER}/git/trees/{'e' * 40}?recursive=1"]
    row = tree["tree"][1]
    if defect == "git-symlink":
        row["mode"] = "120000"
    elif defect == "truncated":
        tree["truncated"] = True
    elif defect == "wrong-blob":
        row["sha"] = "f" * 40
    else:
        row.pop("mode")
    with pytest.raises(co.CoordinatorRefused, match="admission.invalid"):
        co.prelaunch_binding(api_of(data), cfg, RUN)


def replace_execution_data(
    cfg: co.Config,
    data: dict[str, Any],
    path: str,
    *,
    reference: Any,
    evidence: bytes | None = None,
) -> co.Config:
    topology = json.loads(base64.b64decode(data[ADMISSION_PATH]["content"]))
    topology["admission_evidence"] = reference
    raw = hp.canonical_json(topology)
    data[ADMISSION_PATH]["content"] = base64.b64encode(raw).decode()
    if evidence is not None:
        data[path]["content"] = base64.b64encode(evidence).decode()
    evidence = base64.b64decode(data[path]["content"])
    tree = data[f"repos/{STARTER}/git/trees/{'e' * 40}?recursive=1"]["tree"]
    for row, contents in zip(tree, (raw, evidence), strict=True):
        row["sha"] = hashlib.sha1(
            b"blob " + str(len(contents)).encode() + b"\0" + contents,
            usedforsecurity=False,
        ).hexdigest()
        row["size"] = len(contents)
    return dataclasses.replace(
        cfg,
        admission={**cfg.admission, "sha256": hashlib.sha256(raw).hexdigest()},
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
    )


@pytest.mark.parametrize(
    "defect", ["null", "bare", "absent-anchor", "incomplete-table"]
)
def test_canonical_admission_guard_is_reached_for_reference_and_table_failures(
    tmp_path: pathlib.Path, monkeypatch: Any, defect: str
) -> None:
    from tests.unit.lane3_evidence_support import REFERENCE, rows, table

    cfg, data, path = execution_world()
    reference = {
        "null": None,
        "bare": "invented",
        "absent-anchor": co.le.EVIDENCE_FILE + "#absent",
        "incomplete-table": REFERENCE,
    }[defect]
    evidence = (
        table(
            {key: value for key, value in rows().items() if key != "No-grant refusal"}
        ).encode()
        if defect == "incomplete-table"
        else None
    )
    cfg = replace_execution_data(
        cfg, data, path, reference=reference, evidence=evidence
    )
    cfg = dataclasses.replace(cfg, state_path=tmp_path / "state")
    called = []
    validator = co.le.require_admission_evidence

    def guard(value: Any, *, repo_root: pathlib.Path) -> str:
        called.append(value)
        return validator(value, repo_root=repo_root)

    monkeypatch.setattr(co.le, "require_admission_evidence", guard)
    effects = []
    with pytest.raises(co.CoordinatorRefused, match="admission.invalid"):
        co.op_prepare(cfg, lambda *args: effects.append(args), api_of(data), RUN)
    assert called == [reference]
    assert effects == [] and not cfg.state_path.exists()


@pytest.mark.parametrize("phase", ["prepare", "launch", "decide"])
def test_complete_execution_evidence_is_unsupported_before_each_effect_boundary(
    tmp_path: pathlib.Path, monkeypatch: Any, phase: str
) -> None:
    cfg, data, _ = execution_world()
    cfg = dataclasses.replace(cfg, state_path=tmp_path / "state")
    validator = co.le.require_admission_evidence
    checked = []

    def guard(reference: Any, *, repo_root: pathlib.Path) -> str:
        checked.append(reference)
        return validator(reference, repo_root=repo_root)

    monkeypatch.setattr(co.le, "require_admission_evidence", guard)
    effects = []
    if phase == "prepare":
        with pytest.raises(co.CoordinatorRefused, match="execution.unsupported"):
            co.op_prepare(cfg, lambda *args: effects.append(args), api_of(data), RUN)
    elif phase == "launch":
        co.save_state(
            cfg.state_path,
            {
                "schema": co.STATE_SCHEMA,
                "lease_id": LEASE,
                "qualification": {"authority": co.authority_binding(cfg)},
            },
        )
        with pytest.raises(co.CoordinatorRefused, match="execution.unsupported"):
            co.op_launch(
                cfg,
                lambda *args: effects.append(args),
                api_of(data),
                RUN,
                deadline_ns=co.time.monotonic_ns() + 60_000_000_000,
            )
    else:
        assigned = world()
        assigned.update(
            {
                key: value
                for key, value in data.items()
                if key == ADMISSION_PATH or "/git/" in key or co.le.EVIDENCE_FILE in key
            }
        )
        channel = Channel(report())
        result = co.decide(
            cfg=cfg,
            api=api_of(assigned),
            channel=channel,
            lease=LEASE,
            run_id=RUN,
            runner_id=RUNNER,
            qualification={"authority": co.authority_binding(cfg)},
            policy_doc=POLICY,
            bootstrap_manifest=BOOTSTRAP,
        )
        assert result.status == "REFUSED" and result.category == "execution.unsupported"
        assert channel.ops() == ["status", "refuse"]
    assert checked and effects == []


def test_true_v1_config_status_and_launch_intent_cleanup_preserve_original_digest(
    tmp_path: pathlib.Path,
) -> None:
    legacy = config_doc()
    legacy["schema"] = co.LEGACY_CONFIG_SCHEMA
    for key in ("purpose", "source_capability", "evidence_sha256"):
        legacy.pop(key)
    legacy["state_path"] = str(tmp_path / "state")
    with pytest.raises(co.CoordinatorRefused, match="config.invalid"):
        co.parse_config(legacy)
    cfg = co.parse_config(legacy, recovery=True)
    original = co.prelaunch_binding(api_of(prelaunch_world()), config(), RUN)
    original.pop("authority")
    digest = hp.digest(original)
    name = f"lane3-handoff-{LEASE}"
    state = {
        "schema": co.LEGACY_STATE_SCHEMA,
        "lease_id": LEASE,
        "qualification": original,
        "run_id": RUN,
        "launch_intent": {
            "name": name,
            "runner_group_id": GROUP,
            "run_id": RUN,
            "qualification_digest": digest,
        },
    }
    co.save_state(cfg.state_path, state)
    calls = []
    deleted = []

    def channel(op: str, req: Any) -> Any:
        calls.append(op)
        assert req["lease_id"] == LEASE
        assert co.read_json(cfg.state_path)["qualification"] == original
        assert (
            co.read_json(cfg.state_path)["launch_intent"]["qualification_digest"]
            == digest
        )
        return (
            {"lease_id": LEASE, "state": "BOOTSTRAP"}
            if op == "status"
            else closed_reply()
        )

    def api(path: str, method: str = "GET") -> Any:
        assert "/actions/runners" in path or "/actions/runner-groups/" in path
        if method == "DELETE":
            assert path.endswith(f"/runners/{RUNNER}")
            retained = co.read_json(cfg.state_path)
            assert retained["qualification"] == original
            assert retained["launch_intent"]["qualification_digest"] == digest
            deleted.append(RUNNER)
            return None
        rows = [] if deleted else [{"id": RUNNER, "name": name}]
        return {"total_count": len(rows), "runners": rows}

    assert co.op_status(cfg, channel, api)["state"] == "BOOTSTRAP"
    assert co.read_json(cfg.state_path) == state
    with pytest.raises(co.CoordinatorRefused, match="config.invalid"):
        co.prelaunch_binding(api, cfg, RUN)
    co.op_cleanup(cfg, channel, api)
    assert calls == ["status", "cleanup"] and deleted == [RUNNER]
    assert not cfg.state_path.exists()
