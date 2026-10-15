"""Actual canonical operator contracts; executable evidence belongs to hosted CI."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))


def operator():
    return importlib.import_module("lane3_campaign_operator")


def test_operator_requires_explicit_protected_configuration(monkeypatch):
    module = operator()
    monkeypatch.setattr(module, "DEPLOYMENT", None)
    with pytest.raises(RuntimeError, match="protected-deployment-required"):
        module.deployment_inputs()
    for invalid in ({}, {"unrecognized": "SECRET-CANARY"}, [], None):
        with pytest.raises((ValueError, module.CampaignRefused)) as error:
            module.configure(invalid)
        assert "SECRET-CANARY" not in str(error.value)
    assert module.DEPLOYMENT is None


def test_protected_configuration_is_one_shot_and_defensively_copied(monkeypatch):
    module = operator()
    monkeypatch.setattr(module, "DEPLOYMENT", None)
    deployment = {
        **{
            key: "/synthetic/" + key
            for key in (
                "runtime",
                "coordinator_config",
                "state_path",
                "key_receipt",
                "issuer_deployment",
            )
        },
        **dict.fromkeys(
            (
                "coordinator_config_sha256",
                "bundle_digest",
                "issuer_sha256",
                "issuer_deployment_sha256",
            ),
            "a" * 64,
        ),
        "runtime_pins": dict.fromkeys(module.RUNTIME_ROLES, "b" * 64),
        "github_run_id": 12,
        "qualification": {
            "authority": {
                "schema": "dotmac.lane3.qualification-purpose.v2",
                "purpose": "qualification",
                "config_digest": "a" * 64,
                "capability_digest": "b" * 64,
                "evidence_digest": None,
            },
            "repository_id": 1,
            "run_id": 12,
            "run_attempt": 1,
            "environment_id": 2,
            **dict.fromkeys(
                ("workflow_sha", "workflow_blob", "starter_commit"), "c" * 40
            ),
            **dict.fromkeys(
                ("approval_digest", "admission_digest", "supplier_digest"), "d" * 64
            ),
        },
        "ssh_destination": "synthetic-host",
        "runner_org": "synthetic-org",
        "runner_group_id": 4,
        "issuer_module": "/synthetic/lane3_campaign_issuer.py",
    }
    module.configure(deployment)
    deployment["runtime_pins"]["lane3_handoff_protocol.py"] = "e" * 64
    view = module.deployment_inputs()
    view["qualification"]["run_id"] = 99
    assert (
        module.deployment_inputs()["runtime_pins"]["lane3_handoff_protocol.py"]
        == "b" * 64
    )
    assert module.deployment_inputs()["qualification"]["run_id"] == 12
    with pytest.raises(RuntimeError, match="deployment-already-configured"):
        module.configure(deployment)


@pytest.fixture
def pipe_child():
    processes = []

    def launch(code):
        process = subprocess.Popen(  # noqa: S603 - synthetic owned interpreter
            [sys.executable, "-I", "-B", "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
        processes.append(process)
        return process

    yield launch
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_json_pipe_preserves_fragmented_lines_and_partial_writes(
    pipe_child, monkeypatch
):
    module = operator()
    process = pipe_child(
        "import os,sys\n"
        "line=sys.stdin.buffer.readline()\n"
        "for b in line: os.write(1,bytes([b]))\n"
        "os.write(1,b'{\"second\":true}\\n')\n"
    )
    pipe = module.JsonPipe(process)
    write = module.os.write
    counts = []

    def fragmented(fd, data):
        count = write(fd, data[:3])
        counts.append(count)
        return count

    monkeypatch.setattr(module.os, "write", fragmented)
    value = {"op": "synthetic", "nested": [1, {"ok": True}]}
    deadline = time.monotonic() + 5
    pipe.send(value, deadline)
    assert pipe.receive(deadline) == value
    assert pipe.receive(deadline) == {"second": True}
    assert len(counts) > 1 and max(counts) <= 3


@pytest.mark.parametrize(
    "payload",
    [b"", b'{"incomplete":true}', b'{"op":1,"op":2}\n', b"[]\n", b"x" * 65],
)
def test_json_pipe_refuses_eof_duplicates_nonobjects_and_oversized_frames(
    pipe_child, payload
):
    module = operator()
    process = pipe_child("import os\nos.write(1," + repr(payload) + ")\n")
    with pytest.raises(module.CampaignRefused):
        module.JsonPipe(process).receive(time.monotonic() + 5, limit=64)


def test_json_pipe_never_resets_an_inherited_deadline(pipe_child):
    module = operator()
    process = pipe_child("import time\ntime.sleep(10)\n")
    deadline = time.monotonic() - 1
    pipe = module.JsonPipe(process)
    for operation in (
        lambda: pipe.send({"op": "synthetic"}, deadline),
        lambda: pipe.receive(deadline),
    ):
        with pytest.raises((module.CampaignRefused, TimeoutError)):
            operation()


def test_owned_subprocess_timeout_kills_its_actual_process_group(monkeypatch):
    module = operator()
    launched = []
    killed = []
    popen = module.subprocess.Popen
    killpg = module.os.killpg

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process)
        return process

    def kill(group, sig):
        killed.append((group, sig))
        return killpg(group, sig)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module.os, "killpg", kill)
    with pytest.raises(module.CampaignRefused):
        module._run_owned(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(10)"],
            time.monotonic() + 0.2,
        )
    assert len(launched) == 1
    assert killed == [(launched[0].pid, signal.SIGKILL)]
    assert launched[0].returncode == -signal.SIGKILL


def management_fixture(monkeypatch, tmp_path, defect=None):
    module = operator()
    protocol = importlib.import_module("lane3_handoff_protocol")
    events = []
    clock = [100.0]
    lease = "a" * 32
    evidence = {
        "schema": "dotmac.lane3.handoff-evidence.v1",
        "protocol": protocol.PROTOCOL,
        "lease_id": lease,
        "outcome": "REFUSED",
        "category": None,
        **dict.fromkeys(
            (
                "origin",
                "flags",
                "binding",
                "policy_digest",
                "controller_digest",
                "bootstrap_manifest_digest",
                "manifest_digest",
                "snapshot_digest",
                "report_digest",
                "grant_digest",
            ),
            None,
        ),
        "transitions": [{"state": "CLOSED", "offset_ms": 1}],
        "cleanup": {
            **dict.fromkeys(module.CLEANUP, True),
            "guard_installed": True,
            "global_conntrack_flushed": False,
        },
        "cleanup_blocked": None,
        "gate0_accepted": False,
        "token_request_started": False,
        "issuance": "NOT_STARTED",
        "proof_outcome": "REFUSED",
    }
    monkeypatch.setattr(
        module,
        "deployment_inputs",
        lambda: {
            "state_path": tmp_path / "absent",
            "github_run_id": 12,
        },
    )
    monkeypatch.setattr(module, "load_module", lambda role: protocol)
    monkeypatch.setattr(module, "coordinator_binding", lambda: {})
    monkeypatch.setattr(
        module,
        "durable_owned_state",
        lambda: {"lease_id": lease},
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module, "key_age", lambda maximum: events.append(("key", maximum))
    )

    class Pipe:
        def send(self, value, deadline):
            self.op = value["op"]
            events.append((self.op, deadline))
            clock[0] += 3

        def receive(self, deadline, limit=262144):
            if defect == self.op:
                raise module.CampaignRefused("synthetic-gate")
            if self.op == "finish":
                return {
                    "status": "CLOSED",
                    "op": "finish",
                    "local_journal_absent": True,
                }
            return {"status": "PASS", "op": self.op}

    def coordinator(op, deadline, run_id=None):
        events.append((op, deadline, run_id))
        if defect == op:
            raise module.CampaignRefused("synthetic-lost-response")
        if op == "cleanup":
            return {"state": "CLOSED", "evidence": evidence}
        return {
            "state": "PREPARED" if op == "prepare" else "BOOTSTRAP",
            "lease_id": lease,
        }

    def closure(role, deadline, request=None):
        events.append((role, deadline))
        if role == "group":
            return {"total_count": 1 if defect == "pagination" else 0, "runners": []}
        assert json.loads(request) == {
            "protocol": protocol.PROTOCOL,
            "op": "cleanup",
            "lease_id": lease,
        }
        archive_evidence = json.loads(json.dumps(evidence))
        if defect == "evidence-mismatch":
            archive_evidence["outcome"] = "EXPIRED"
        return {
            "protocol": protocol.PROTOCOL,
            "op": "cleanup",
            "lease_id": "b" * 32 if defect == "archive" else lease,
            "state": "CLOSED",
            "evidence": archive_evidence,
        }

    monkeypatch.setattr(module, "coordinator_cli", coordinator)
    monkeypatch.setattr(module, "closure_read", closure)
    management = module.ManagementStdio(None)
    management.pipe = Pipe()
    return module, management, events, evidence


def test_one_parent_keeps_original_deadline_through_both_gates_and_actual_cli(
    monkeypatch, tmp_path
):
    _, management, events, _ = management_fixture(monkeypatch, tmp_path)
    assert management.retained_launch()["state"] == "BOOTSTRAP"
    assert [event[0] for event in events] == [
        "key",
        "retain",
        "key",
        "gate_prepare",
        "prepare",
        "key",
        "gate_bootstrap",
        "launch",
    ]
    assert [event[1] for event in events if event[0] == "key"] == [30, 30, 60]
    assert [event[1:] for event in events if event[0] in ("prepare", "launch")] == [
        (160, 12),
        (160, 12),
    ]
    management.finish()
    assert [event[0] for event in events][-4:] == [
        "cleanup",
        "archive",
        "group",
        "finish",
    ]
    assert management.phase == "finished"


@pytest.mark.parametrize(
    "defect",
    [
        "cleanup",
        "archive",
        "evidence-mismatch",
        "pagination",
        "cleanup-bit",
        "blocked",
        "state-drift",
    ],
)
def test_closure_requires_cleanup_archive_original_bits_and_complete_zero_group(
    monkeypatch, tmp_path, defect
):
    module, management, events, evidence = management_fixture(
        monkeypatch, tmp_path, defect
    )
    management.retained_launch()
    if defect == "state-drift":
        monkeypatch.setattr(
            module, "durable_owned_state", lambda: {"lease_id": "b" * 32}
        )
    elif defect == "cleanup-bit":
        evidence["cleanup"]["processes_absent"] = False
    elif defect == "blocked":
        evidence["cleanup_blocked"] = "cleanup.drift"
    with pytest.raises(module.CampaignRefused):
        management.finish()
    assert "finish" not in [event[0] for event in events]
    assert management.finish_attempted is True and management.phase == "bootstrapped"
    with pytest.raises(module.CampaignRefused):
        management.finish()
    assert sum(event[0] == "cleanup" for event in events) == (defect != "state-drift")


@pytest.mark.parametrize("defect", ["gate_bootstrap", "launch"])
def test_prepare_success_then_failure_finalizes_once_without_reregistering(
    monkeypatch, tmp_path, defect
):
    module, management, events, _ = management_fixture(monkeypatch, tmp_path, defect)
    with pytest.raises(module.CampaignRefused):
        management.retained_launch()
    assert management.phase == "finished"
    names = [event[0] for event in events]
    assert (
        names.count("retain") == names.count("prepare") == names.count("cleanup") == 1
    )
    assert names.count("launch") == (defect == "launch")
    assert names[-4:] == ["cleanup", "archive", "group", "finish"]
    with pytest.raises(module.CampaignRefused):
        management.retained_launch()


def test_lost_prepare_with_unknown_durable_ownership_stays_unproven(
    monkeypatch, tmp_path
):
    module, management, events, _ = management_fixture(monkeypatch, tmp_path, "prepare")

    def unknown():
        raise module.CampaignRefused("state-ownership-unproved")

    monkeypatch.setattr(module, "durable_owned_state", unknown)
    with pytest.raises(module.CampaignRefused):
        management.retained_launch()
    assert management.failure == "UNPROVEN" and management.lease_id is None
    assert not {"launch", "cleanup", "finish"} & {event[0] for event in events}


def test_stale_prepare_gate_preserves_original_bridge_without_remote_prepare(
    monkeypatch, tmp_path
):
    module, management, events, _ = management_fixture(
        monkeypatch, tmp_path, "gate_prepare"
    )
    with pytest.raises(module.CampaignRefused):
        management.retained_launch()
    assert management.failure == "UNPROVEN" and management.phase == "retained"
    assert not {"prepare", "launch", "cleanup", "finish"} & {
        event[0] for event in events
    }


def test_management_close_sends_eof_without_claiming_attached_process_authority():
    module = operator()
    events = []
    process = SimpleNamespace(
        stdin=SimpleNamespace(closed=False, close=lambda: events.append("eof"))
    )
    module.ManagementStdio(process).close()
    assert events == ["eof"]


@pytest.mark.parametrize(
    "maximum,age,accepted",
    [
        (30, 30, True),
        (30, 30.1, False),
        (60, 60, True),
        (60, 60.1, False),
        (30, -1, False),
    ],
)
def test_key_age_uses_exact_30_and_60_second_bounds(
    monkeypatch, maximum, age, accepted
):
    module = operator()
    monkeypatch.setattr(
        module, "deployment_inputs", lambda: {"key_receipt": pathlib.Path("/synthetic")}
    )
    monkeypatch.setattr(
        module,
        "owned",
        lambda *args: json.dumps(
            {
                "schema": "dotmac.l3mgmt.key-load.v1",
                "loaded_at": 100.0 - age,
                "lifetime_seconds": 480,
            }
        ).encode(),
    )
    monkeypatch.setattr(module.time, "time", lambda: 100.0)
    if accepted:
        assert module.key_age(maximum) == age
    else:
        with pytest.raises(module.CampaignRefused, match="key-age"):
            module.key_age(maximum)


@pytest.mark.parametrize("drift", [False, True])
def test_actual_coordinator_cli_measures_source_before_fixed_prepare_command(
    monkeypatch, drift
):
    module = operator()
    calls = []
    monkeypatch.setattr(
        module,
        "deployment_inputs",
        lambda: {
            "runtime": pathlib.Path("/synthetic"),
            "runtime_pins": {"lane3_handoff_coordinator.py": "a" * 64},
            "coordinator_config": pathlib.Path("/synthetic/config.json"),
            "github_run_id": 12,
        },
    )
    monkeypatch.setattr(module, "coordinator_binding", lambda: {})

    def measure(path, pin):
        calls.append((path, pin))
        if drift:
            raise module.CampaignRefused("code-pin")

    monkeypatch.setattr(module, "measured", measure)
    monkeypatch.setattr(
        module,
        "_run_owned",
        lambda args, deadline, **kwargs: calls.append((args, deadline)),
    )
    if drift:
        with pytest.raises(module.CampaignRefused):
            module.coordinator_cli("prepare", 160, 12)
        assert len(calls) == 1
    else:
        module.coordinator_cli("prepare", 160, 12)
        assert calls[-1] == (
            [
                sys.executable,
                "-I",
                "-B",
                "/synthetic/lane3_handoff_coordinator.py",
                "prepare",
                "--config",
                "/synthetic/config.json",
                "--run-id",
                "12",
            ],
            160,
        )
        with pytest.raises(module.CampaignRefused):
            module.coordinator_cli("prepare", 160, 13)


def test_actual_launch_cli_inherits_the_original_parent_deadline(monkeypatch):
    module = operator()
    calls = []
    monkeypatch.setattr(
        module,
        "deployment_inputs",
        lambda: {
            "runtime": pathlib.Path("/synthetic"),
            "runtime_pins": {"lane3_campaign_bootstrap.py": "a" * 64},
            "coordinator_config": pathlib.Path("/synthetic/config.json"),
            "github_run_id": 12,
        },
    )
    monkeypatch.setattr(module, "measured", lambda *args: b"synthetic")
    monkeypatch.setattr(module, "coordinator_binding", lambda: {})
    monkeypatch.setattr(
        module,
        "_run_owned",
        lambda args, deadline, **kwargs: calls.append((args, deadline)),
    )
    module.coordinator_cli("launch", 160.125, 12)
    assert calls[0][0][-4:] == [
        "--run-id",
        "12",
        "--deadline-monotonic-ns",
        "160125000000",
    ]
    assert calls[0][1] == 160.125


@pytest.fixture
def real_owned_journal(monkeypatch):
    """Exercise actual protected reads under hosted runner-owned safe parents."""
    module = operator()
    protocol = importlib.import_module("lane3_handoff_protocol")
    with tempfile.TemporaryDirectory(
        prefix=".lane3-state-", dir=pathlib.Path.home()
    ) as rawdir:
        directory = pathlib.Path(rawdir)
        runtime = directory / "runtime"
        runtime.mkdir(mode=0o700)
        raw = pathlib.Path(protocol.__file__).read_bytes()
        staged = runtime / "lane3_handoff_protocol.py"
        staged.write_bytes(raw)
        staged.chmod(0o600)
        qualification = {
            "repository_id": 1,
            "run_id": 12,
            "run_attempt": 1,
            "environment_id": 2,
            "workflow_sha": "a" * 40,
            "workflow_blob": "b" * 40,
            "starter_commit": "c" * 40,
            "authority": {
                "schema": "dotmac.lane3.qualification-purpose.v2",
                "purpose": "qualification",
                "config_digest": "a" * 64,
                "capability_digest": "b" * 64,
                "evidence_digest": None,
            },
            "approval_digest": "d" * 64,
            "admission_digest": "e" * 64,
            "supplier_digest": "f" * 64,
        }
        state_path = directory / "state.json"
        config_path = directory / "coordinator.json"
        config = {
            "schema": "dotmac.lane3.handoff-coordinator.v2",
            "purpose": "qualification",
            "state_path": str(state_path),
            "runner_org": "synthetic-org",
            "runner_group_id": 4,
            "management": {
                "ssh_alias": "synthetic-host",
                "controller_path": "/opt/dotmac-lane3-handoff/"
                + "a" * 64
                + "/lane3_handoff_install.py",
            },
            **{
                key: qualification[key]
                for key in (
                    "repository_id",
                    "workflow_sha",
                    "workflow_blob",
                    "starter_commit",
                    "environment_id",
                )
            },
            "bootstrap_manifest_sha256": "b" * 64,
            "bootstrap_proposal_sha256": "c" * 64,
        }
        qualification["authority"]["config_digest"] = protocol.digest(config)
        config_raw = protocol.canonical_json(config)
        config_path.write_bytes(config_raw)
        config_path.chmod(0o600)
        binding = {
            "runtime": runtime,
            "runtime_pins": {staged.name: hashlib.sha256(raw).hexdigest()},
            "state_path": state_path,
            "qualification": qualification,
            "coordinator_config": config_path,
            "coordinator_config_sha256": hashlib.sha256(config_raw).hexdigest(),
            "bundle_digest": "a" * 64,
            "runner_org": "synthetic-org",
            "runner_group_id": 4,
            "ssh_destination": "synthetic-host",
            "github_run_id": 12,
            "issuer_module": directory / "lane3_campaign_issuer.py",
            "issuer_sha256": "0" * 64,
        }
        monkeypatch.setattr(module, "deployment_inputs", lambda: binding)
        prepared = {
            "schema": "dotmac.lane3.handoff-coordinator-state.v2",
            "lease_id": "d" * 32,
            "qualification": qualification,
            "bootstrap_manifest_digest": "b" * 64,
            "bootstrap_proposal_sha256": "c" * 64,
        }

        def persist(value):
            state_path.write_bytes(protocol.canonical_json(value))
            state_path.chmod(0o600)

        yield module, protocol, state_path, prepared, persist


@pytest.mark.parametrize("post_jit", [False, True])
def test_actual_owned_journal_validates_prepared_and_post_jit_without_provenance_file(
    real_owned_journal, post_jit
):
    module, protocol, path, state, persist = real_owned_journal
    if post_jit:
        state.update(
            run_id=12,
            runner_id=77,
            bootstrap_provenance_digest="e" * 64,
            launch_intent={
                "name": "lane3-handoff-" + state["lease_id"],
                "runner_group_id": 4,
                "run_id": 12,
                "qualification_digest": protocol.digest(state["qualification"]),
            },
        )
    persist(state)
    assert not path.with_name(
        path.name + "." + state["lease_id"] + ".bootstrap-provenance.json"
    ).exists()
    assert module.durable_owned_state() == state


@pytest.mark.parametrize("defect", ["manifest", "proposal", "missing", "unknown"])
def test_actual_owned_journal_refuses_hash_drift_missing_binding_and_unknown_keys(
    real_owned_journal, defect
):
    module, _, _, state, persist = real_owned_journal
    if defect == "manifest":
        state["bootstrap_manifest_digest"] = "f" * 64
    elif defect == "proposal":
        state["bootstrap_proposal_sha256"] = "f" * 64
    elif defect == "missing":
        state.pop("bootstrap_manifest_digest")
    else:
        state["unreviewed"] = "fixed synthetic value"
    persist(state)
    with pytest.raises(module.CampaignRefused, match="state-ownership-unproved"):
        module.durable_owned_state()


@pytest.mark.parametrize(
    "defect",
    [None, "foreign-target", "foreign-group", "boolean-group", "foreign-state"],
)
def test_closure_routes_must_match_pinned_coordinator_configuration(
    monkeypatch, defect
):
    module = operator()
    binding = {
        "coordinator_config": pathlib.Path("/synthetic/config"),
        "coordinator_config_sha256": "a" * 64,
        "state_path": pathlib.Path("/synthetic/state"),
        "bundle_digest": "b" * 64,
        "runner_org": "synthetic-org",
        "runner_group_id": 4,
        "ssh_destination": "synthetic-host",
        "qualification": {
            "authority": {
                "schema": "dotmac.lane3.qualification-purpose.v2",
                "purpose": "qualification",
                "config_digest": "a" * 64,
                "capability_digest": "b" * 64,
                "evidence_digest": None,
            },
        },
    }
    config = {
        "schema": "dotmac.lane3.handoff-coordinator.v2",
        "purpose": "qualification",
        "state_path": str(binding["state_path"]),
        "runner_org": "synthetic-org",
        "runner_group_id": 4,
        "management": {
            "ssh_alias": "synthetic-host",
            "controller_path": "/opt/dotmac-lane3-handoff/"
            + "b" * 64
            + "/lane3_handoff_install.py",
        },
    }
    binding["qualification"]["authority"]["config_digest"] = hashlib.sha256(
        json.dumps(
            config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    if defect == "foreign-target":
        config["management"]["ssh_alias"] = "unapproved-host"
    elif defect == "foreign-group":
        config["runner_group_id"] = 5
    elif defect == "boolean-group":
        config["runner_group_id"] = True
    elif defect == "foreign-state":
        config["state_path"] = "/other/state"
    monkeypatch.setattr(module, "deployment_inputs", lambda: binding)
    monkeypatch.setattr(module, "measured", lambda *args: json.dumps(config).encode())
    if defect:
        with pytest.raises(module.CampaignRefused, match="coordinator-binding"):
            module.coordinator_binding()
    else:
        assert module.coordinator_binding() == config


@pytest.mark.parametrize("failure", [None, "issuer", "late", "remote-cleanup"])
def test_b8_prepared_custody_precedes_public_prepare_and_is_consumed_once(
    monkeypatch, failure
):
    module = operator()
    events = []
    clock = [100.0]
    prepared = SimpleNamespace(
        secret="SECRET-CANARY", close=lambda: events.append("custody-close")
    )
    public_issued = {
        "certificates": {"observer": "synthetic-public-cert"},
        "public_record": {},
    }

    def issue(requests, *, remaining_seconds, prepared_custody):
        assert requests == {"public_keys": {"observer": "ssh-ed25519 synthetic"}}
        assert remaining_seconds == 40 and prepared_custody is prepared
        events.append("issue")
        if failure == "issuer":
            raise module.CampaignRefused("issuer-failed")
        clock[0] += 41 if failure == "late" else 7
        return public_issued

    monkeypatch.setattr(
        module,
        "load_issuer",
        lambda: SimpleNamespace(
            prepare_custody=lambda: (events.append("custody-ready") or prepared),
            issue=issue,
        ),
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    result = {
        "matrix_result": {"classification": "synthetic"},
        "full_b8_complete": False,
        "admission_changed": False,
    }

    class Pipe:
        def __init__(self, process):
            self.reads = 0

        def send(self, value, deadline):
            events.append(value["op"])
            if value["op"] == "issued":
                assert deadline == 140
                assert value["issued"] == public_issued
                assert "SECRET-CANARY" not in json.dumps(value)

        def receive(self, deadline, limit=262144):
            self.reads += 1
            if self.reads == 1:
                return {
                    "op": "requests",
                    "requests": {"public_keys": {"observer": "ssh-ed25519 synthetic"}},
                    "remaining_seconds": 40,
                }
            assert deadline == 200
            if failure == "remote-cleanup":
                raise module.CampaignRefused("cleanup-failed")
            return {"op": "closed", "result": result}

    monkeypatch.setattr(module, "JsonPipe", Pipe)
    process = SimpleNamespace(
        stdin=SimpleNamespace(closed=False, close=lambda: events.append("stdin-close")),
        wait=lambda timeout: None,
        returncode=0,
    )
    exchange = module.B8Stdio()
    with pytest.raises(module.CampaignRefused):
        exchange.exchange(process)
    assert events == []
    exchange.prepare_custody()
    if failure:
        with pytest.raises(module.CampaignRefused):
            exchange.exchange(process)
    else:
        assert exchange.exchange(process) == result
    assert events[:3] == ["custody-ready", "prepare", "issue"]
    assert events[-2:] == ["stdin-close", "custody-close"]
    assert exchange.used is True and exchange.prepared is None
    if failure in ("issuer", "late"):
        assert "issued" not in events
    with pytest.raises(module.CampaignRefused):
        exchange.exchange(process)
    assert events.count("prepare") == 1


@pytest.mark.parametrize(
    "phase", ["retain", "gate_prepare", "prepare", "gate_bootstrap", "launch"]
)
def test_failure_phase_is_preserved_and_cleanup_does_not_replace_it(
    monkeypatch, tmp_path, phase
):
    module, management, _, _ = management_fixture(monkeypatch, tmp_path)
    original_call, original_coordinator = management._call, module.coordinator_cli

    def call(op, age, deadline):
        if op == phase:
            raise module.CampaignRefused("management-unproved")
        return original_call(op, age, deadline)

    def coordinator(op, deadline, run_id=None):
        if op == phase:
            raise module.CampaignRefused("code-pin")
        return original_coordinator(op, deadline, run_id)

    def cleanup():
        raise RuntimeError("PRIVATE-CANARY")

    monkeypatch.setattr(management, "_call", call)
    monkeypatch.setattr(module, "coordinator_cli", coordinator)
    monkeypatch.setattr(management, "failure_finalize", cleanup)
    with pytest.raises(module.CampaignRefused) as error:
        management.retained_launch()
    value = module.failure_diagnostic(error.value)
    assert value["phase"] == phase
    assert value["cleanup"] == "unproven"
    assert value["category"] == (
        "code-pin" if phase in ("prepare", "launch") else "management-unproved"
    )
    assert "PRIVATE" not in repr(error.value) + json.dumps(value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"status":"REFUSED","category":"binding.mismatch"}',
        b'{"status":"REFUSED","category":"PRIVATE-CANARY"}',
        b'{"status":"REFUSED","category":"internal","secret":"PRIVATE-CANARY"}',
        b'{"status":"REFUSED","category":"internal","category":"binding.mismatch"}',
        b'{"status":"REFUSED","category":NaN}',
        b"PRIVATE-CANARY",
        b'{"status":"REFUSED"',
    ],
)
def test_only_measured_coordinator_child_can_preserve_exact_safe_nonzero_refusal(raw):
    module = operator()
    code = "import sys;sys.stdout.buffer.write(" + repr(raw) + ");sys.exit(1)"
    args = [sys.executable, "-I", "-B", "-c", code]
    categories = importlib.import_module("lane3_handoff_protocol").CATEGORIES
    for allow in (None, categories):
        with pytest.raises(module.CampaignRefused) as error:
            module._run_owned(args, time.monotonic() + 10, refusal_categories=allow)
        diagnostic = module.failure_diagnostic(error.value)
        expected = (
            "binding.mismatch"
            if allow is not None
            and raw == b'{"status":"REFUSED","category":"binding.mismatch"}'
            else "subprocess-unproved"
        )
        assert diagnostic["category"] == expected
        assert "PRIVATE" not in repr(error.value) + json.dumps(diagnostic)


@pytest.mark.parametrize(
    "change",
    [
        {"category": "PRIVATE-CANARY"},
        {"phase": "PRIVATE-CANARY"},
        {"stage": "PRIVATE-CANARY"},
        {"extra": "PRIVATE-CANARY"},
        {"cleanup": float("nan")},
    ],
)
def test_bridge_diagnostic_is_exact_bounded_and_never_advances_phase(
    monkeypatch, change
):
    module = operator()
    management = module.ManagementStdio(None)
    diagnostic = {
        "phase": "retain",
        "stage": "old-stop-readback",
        "category": "systemd-unproven",
        "cleanup": "completed",
    }
    diagnostic.update(change)
    management.pipe = SimpleNamespace(
        send=lambda *args: None,
        receive=lambda *args: {"status": "REFUSED", "diagnostic": diagnostic},
    )
    monkeypatch.setattr(module, "key_age", lambda age: None)
    with pytest.raises(module.CampaignRefused) as error:
        management._call("retain", 30, time.monotonic() + 10)
    assert management.phase == "initial"
    assert "PRIVATE" not in repr(error.value) + json.dumps(
        module.failure_diagnostic(error.value)
    )
    assert module.failure_diagnostic(error.value)["category"] == "management-unproved"


def test_bridge_valid_refusal_survives_without_accepting_a_pass(monkeypatch):
    module = operator()
    management = module.ManagementStdio(None)
    diagnostic = {
        "phase": "retain",
        "stage": "old-stop-readback",
        "category": "systemd-unproven",
        "cleanup": "completed",
    }
    management.pipe = SimpleNamespace(
        send=lambda *args: None,
        receive=lambda *args: {"status": "REFUSED", "diagnostic": diagnostic},
    )
    monkeypatch.setattr(module, "key_age", lambda age: None)
    with pytest.raises(module.CampaignRefused) as error:
        management._call("retain", 30, time.monotonic() + 10)
    assert module.failure_diagnostic(error.value) == diagnostic
    assert management.phase == "initial"


def test_coordinator_diagnostic_wire_vocabulary_matches_protocol_owner():
    assert (
        operator().COORDINATOR_FAILURE_CATEGORIES
        == importlib.import_module("lane3_handoff_protocol").CATEGORIES
    )


def test_owner_adapter_revalidates_mutated_metadata_and_ignores_duck_types():
    module = operator()
    error = module.CampaignRefused("code-pin")
    error.diagnostic["category"] = "PRIVATE-CANARY"
    for candidate in (
        error,
        SimpleNamespace(diagnostic={"category": "PRIVATE-CANARY"}),
    ):
        assert module.failure_diagnostic(candidate)["category"] == "unproven"
        assert "PRIVATE" not in json.dumps(module.failure_diagnostic(candidate))


def test_successful_finalization_cannot_erase_unproven_transition_cleanup(
    monkeypatch, tmp_path
):
    module, management, _, _ = management_fixture(monkeypatch, tmp_path)
    original = {
        "phase": "retain",
        "stage": "arm",
        "category": "systemd-unproven",
        "cleanup": "unproven",
    }

    def refuse(*args):
        raise module.CampaignRefused("management-unproved", diagnostic=original)

    monkeypatch.setattr(management, "_call", refuse)
    monkeypatch.setattr(management, "failure_finalize", lambda: None)
    with pytest.raises(module.CampaignRefused) as error:
        management.retained_launch()
    assert module.failure_diagnostic(error.value) == original
