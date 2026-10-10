"""Coordinator binding/decision canaries; synthetic GitHub API and channel.

Every binding row independently prevents a grant, and the positive exact
binding succeeds. Nothing here contacts GitHub, a host or OpenBao.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_coordinator as co
import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr

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


def config() -> co.Config:
    return co.parse_config(
        {
            "schema": co.CONFIG_SCHEMA,
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
            "management": {"ssh_alias": "synthetic-host", "controller_path": "/x/c.py"},
            "resolver": "127.0.0.53",
            "policy_path": "/x/policy.json",
            "bootstrap_manifest_path": "/x/bootstrap.json",
            "state_path": "/x/state.json",
        }
    )


def world() -> dict[str, Any]:
    base = f"repos/{REPO}/actions/runs/{RUN}"
    return {
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
        f"repos/{REPO}/actions/workflows/synthetic.yml/runs?per_page=100": {
            "total_count": 2,
            "workflow_runs": [
                {"id": RUN, "status": "in_progress"},
                {"id": RUN - 1, "status": "completed"},
            ],
        },
        f"repos/{STARTER}/contents/scripts/lane3_github_oidc.py?ref={STARTER_SHA}": {
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
RUNS = f"repos/{REPO}/actions/workflows/synthetic.yml/runs?per_page=100"
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
            lambda d: d[JOBS]["jobs"].append({**d[JOBS]["jobs"][0], "id": JOB + 1})
            or d[JOBS].update(total_count=2),
            "assignment.ambiguous",
        ),
        (
            lambda d: d[JOBS]["jobs"].append(
                {"id": JOB + 1, "status": "queued", "runner_id": None}
            )
            or d[JOBS].update(total_count=2),
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
        (_set(f"{BASE}/pending_deployments", value=None), "approval.missing"),
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
    assert channel.ops() == ["status", "grant"]
    grant_request = channel.calls[1][1]
    assert grant_request["binding"]["job_id"] == JOB
    assert grant_request["snapshot"]["addresses"] == rows
    assert grant_request["snapshot"]["ttl_remaining_ms"] == 60_000


def test_unlisted_origin_refuses_without_resolution_or_grant() -> None:
    channel = Channel(report())
    policy = copy.deepcopy(POLICY)
    policy["origins"]["origins"] = ["https://other-broker.example"]
    decision = decide(channel, policy=policy)
    assert (decision.status, decision.category) == ("REFUSED", "origin.not_admitted")
    assert "grant" not in channel.ops() and channel.resolved == []  # type: ignore[attr-defined]
    assert channel.calls[-1] == (
        "refuse",
        co.request("refuse", lease_id=LEASE, category="origin.not_admitted"),
    )


@pytest.mark.parametrize(
    ("kw", "label"),
    [
        ({"data": {}}, "binding.mismatch"),
        ({"resolve_error": "dns.cname_loop"}, "snapshot.refused"),
        ({"snap": snapshot(ttl_s=46)}, "deadline.insufficient"),
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
    call = co.ssh_channel("synthetic-host", "/x/c.py")
    with pytest.raises(co.CoordinatorRefused):
        call(
            "status",
            {"protocol": hp.PROTOCOL, "op": "status", "lease_id": LEASE, "cmd": "id"},
        )
    with pytest.raises(co.CoordinatorRefused):
        call("sh", {})
    with pytest.raises(co.CoordinatorRefused):
        co.ssh_channel("synthetic-host; id", "/x/c.py")
    with pytest.raises(co.CoordinatorRefused):
        co.ssh_channel("synthetic-host", "/x/c.py; id")
