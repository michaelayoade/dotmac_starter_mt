"""T1-T11 and the P-SEC rev6 import-uid gates, against fake effects (no Docker)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from typing import Any

import pytest
from dotmac_deployment_foundation import operator_job_contract as contract
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.operator_job import (
    OperatorJobRefusal,
    WaitResult,
    plan_operator_job,
    run_operator_job,
)
from dotmac_deployment_foundation.providers.compose_operator_job import (
    ComposeOperatorJobEffects,
)

from tests.unit.foundation_operator_job_support import (
    IDENTITY,
    INVOCATION,
    JOB_TOML,
    PAIR,
    FakeAuthority,
    FakeEffects,
    make_bindings,
    make_plan,
    make_resolved,
    make_spec,
)


def _run(plan, effects, authority=None):
    authority = authority or FakeAuthority()
    return run_operator_job(
        plan, effects, make_bindings(authority), pair=PAIR, target="host-a"
    )


def _job(resolved: dict[str, Any]) -> dict[str, Any]:
    return resolved[contract.RESOLVED_DISPATCHED_JOB]


def test_happy_path_runs_once_and_removes() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    receipt = _run(plan, effects)
    assert receipt.outcome == "succeeded" and receipt.exit_status == 0
    assert receipt.container_removed and effects.removed == [plan.container_name]
    assert effects.calls.count("create") == 1
    assert json.loads(receipt.to_json())["schema"] == contract.RECEIPT_SCHEMA
    # timeout is min(spec 2400, signed 600)
    assert plan.timeout_seconds == 600
    assert f"start:{plan.timeout_seconds}" in effects.calls


# T1
def test_t1_second_consume_is_refused_and_creates_nothing() -> None:
    plan = make_plan()
    authority = FakeAuthority()
    first = FakeEffects(plan)
    assert _run(plan, first, authority).outcome == "succeeded"
    second = FakeEffects(plan)
    receipt = _run(plan, second, authority)
    assert receipt.outcome == "refused" and receipt.reason == "job_already_consumed"
    assert receipt.exit_status == 2
    assert "create" not in second.calls and second.removed == []


# T2
@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update({contract.RESOLVED_OPERATION: "deploy"}),
        lambda r: r.pop(contract.RESOLVED_DISPATCHED_JOB),
    ],
)
def test_t2_wrong_operation_or_missing_job_is_refused_before_consume(mutate) -> None:
    resolved = make_resolved()
    mutate(resolved)
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved)
    assert excinfo.value.code == "job_operation_refused"


# T3
def test_t3_argv_not_prefixed_or_too_long_is_refused() -> None:
    resolved = make_resolved()
    _job(resolved)[contract.JOB_ARGV] = ["python", "-m", "other", "--x"]
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved)
    assert excinfo.value.code == "job_argv_refused"
    resolved = make_resolved()
    _job(resolved)[contract.JOB_ARGV] = [
        "python",
        "-m",
        "importer",
        "execute",
        *(["x"] * 61),
    ]
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved)
    assert excinfo.value.code == "job_argv_refused"


def test_t3_argv_digest_mismatch_creates_nothing() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.env[contract.ENV_ARGV_DIGEST] = "sha256:" + "0" * 64
    receipt = _run(plan, effects)
    assert receipt.reason == "job_env_mismatch" and "create" not in effects.calls


def test_unknown_job_name_is_refused() -> None:
    resolved = make_resolved()
    _job(resolved)[contract.JOB_NAME] = "nope"
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved)
    assert excinfo.value.code == "job_unknown"


# T4
@pytest.mark.parametrize(
    ("what", "reason"),
    [
        ("files", "job_env_mismatch"),
        ("result", "job_render_failed"),
        ("status", "job_render_failed"),
        ("stale", "job_render_stale"),
        ("params_digest", "job_env_mismatch"),
        ("params_extra", "job_env_mismatch"),
    ],
)
def test_t4_env_and_render_checks(what: str, reason: str) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    if what == "files":
        effects.env[contract.ENV_SECRET_FILES] = "migration_database_url,extra"
    elif what == "result":
        effects.show["Result"] = "exit-code"
    elif what == "status":
        effects.show["ExecMainStatus"] = "1"
    elif what == "stale":
        effects.show["InvocationID"] = "b" * 32
    elif what == "params_digest":
        effects.params = b"IMPORT_BATCH=other\n"
    else:
        effects.params = b"IMPORT_BATCH=batch-1\nMIGRATION_DATABASE_URL=x\n"
    authority = FakeAuthority()
    receipt = _run(plan, effects, authority)
    assert receipt.outcome == "refused" and receipt.reason == reason
    # F-E: the cross-checks run BEFORE consume, so a bad invocation spends nothing
    assert authority.consumed == [] and "create" not in effects.calls


def test_render_unit_is_derived_here_and_must_equal_the_agents() -> None:
    plan = make_plan()
    assert plan.render_unit == "openbao-agent@appns-import-c8cd75e18839.service"
    effects = FakeEffects(plan)
    effects.env[contract.ENV_RENDER_UNIT] = (
        "openbao-agent@appns-import-000000000000.service"
    )
    authority = FakeAuthority()
    receipt = _run(plan, effects, authority)
    assert receipt.reason == "job_material_set_unknown"
    assert authority.consumed == [] and "create" not in effects.calls


# T5, T6, T7, T8
def _break(effects: FakeEffects, how: str) -> None:
    c = effects.container
    plan = effects.plan
    if how == "extra_rw":
        c["Mounts"].append(
            {"Type": "bind", "Source": "/srv/x", "Destination": "/x", "RW": True}
        )
    elif how == "extra_secret":
        c["Mounts"].append(
            {
                "Type": "bind",
                "Source": f"{plan.secrets_dir}/legacy_database_url",
                "Destination": "/run/secrets/dotmac/LEGACY_DATABASE_URL",
                "RW": False,
            }
        )
    elif how == "rw_secret":
        c["Mounts"][0]["RW"] = True
    elif how == "socket":
        c["Mounts"].append(
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
                "RW": False,
            }
        )
    elif how == "extra_network":
        c["NetworkSettings"]["Networks"]["bridge"] = {}
    elif how == "missing_network":
        c["NetworkSettings"]["Networks"] = {}
    elif how == "restart":
        c["HostConfig"]["RestartPolicy"] = {"Name": "always", "MaximumRetryCount": 0}
    elif how == "retries":
        c["HostConfig"]["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 3}
    elif how == "autoremove":
        c["HostConfig"]["AutoRemove"] = True
    elif how == "extra_env":
        c["Config"]["Env"].append("EXTRA_NAME=1")
    elif how == "missing_env":
        c["Config"]["Env"] = ["PATH=/usr/bin"]
    elif how == "job_only_env":
        c["Config"]["Env"].append("MIGRATION_DATABASE_URL=x")
    elif how == "job_only_image_env":
        effects.image["Config"]["Env"].append("LEGACY_DATABASE_URL=x")
    elif how == "capdrop":
        c["HostConfig"]["CapDrop"] = []
    elif how == "secopt_unconfined":
        c["HostConfig"]["SecurityOpt"].append("seccomp=unconfined")
    elif how == "secopt_missing":
        c["HostConfig"]["SecurityOpt"] = []
    elif how == "privileged":
        c["HostConfig"]["Privileged"] = True
    elif how == "root_user":
        c["Config"]["User"] = "0:0"
    elif how == "cmd":
        c["Config"]["Cmd"] = [*plan.argv, "--extra"]
    elif how == "image":
        c["Config"]["Image"] = "registry.example.invalid/app@sha256:" + "0" * 64
    elif how == "image_id":
        c["Image"] = "sha256:" + "2" * 64
    else:  # pragma: no cover
        raise AssertionError(how)


@pytest.mark.parametrize(
    "how",
    [
        "extra_rw",
        "extra_secret",
        "rw_secret",
        "socket",
        "extra_network",
        "missing_network",
        "restart",
        "retries",
        "autoremove",
        "extra_env",
        "missing_env",
        "job_only_env",
        "job_only_image_env",
        "privileged",
        "capdrop",
        "secopt_unconfined",
        "secopt_missing",
        "root_user",
        "cmd",
        "image",
        "image_id",
    ],
)
def test_t5_t8_inspect_readback_refuses_and_removes(how: str) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    _break(effects, how)
    receipt = _run(plan, effects)
    assert receipt.outcome == "refused" and receipt.reason == "job_shape_refused"
    assert receipt.container_removed and effects.removed == [plan.container_name]
    assert not any(call.startswith("start:") for call in effects.calls)


def test_t5_t8_the_good_container_passes() -> None:
    plan = make_plan()
    assert _run(plan, FakeEffects(plan)).outcome == "succeeded"


# T9
def test_t9_input_digest_mismatch_is_refused() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.input_hash = hashlib.sha256(b"tampered").hexdigest()
    receipt = _run(plan, effects)
    assert receipt.reason == "job_input_digest_mismatch"
    assert "create" not in effects.calls


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "/job/in/../x", "/job/out/x", "/job/in/a/b", "job/in/x"]
)
def test_t9_input_path_outside_job_in_is_refused(path: str) -> None:
    resolved = make_resolved()
    _job(resolved)[contract.JOB_INPUTS][0][contract.INPUT_PATH] = path
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved, IDENTITY)
    assert excinfo.value.code == "job_input_refused"


def test_t9_symlinked_and_oversize_inputs_are_refused(tmp_path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(real)
    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=lambda *a, **k: CommandResult(0),
    )
    assert effects.hash_input(str(real)) == hashlib.sha256(b"x").hexdigest()
    with pytest.raises(OperatorJobRefusal):
        effects.hash_input(str(link))
    with pytest.raises(OperatorJobRefusal):
        effects.hash_input(str(tmp_path))  # a directory is not a regular file


def test_params_env_file_must_be_0400_regular_and_ours(tmp_path) -> None:
    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=lambda *a, **k: CommandResult(0),
    )
    path = tmp_path / "params.env"
    path.write_bytes(b"A=1\n")
    os.chmod(path, 0o400)
    assert effects.read_params_env(str(path)) == b"A=1\n"
    os.chmod(path, 0o440)
    with pytest.raises(OperatorJobRefusal):
        effects.read_params_env(str(path))
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(OperatorJobRefusal):
        effects.read_params_env(str(link))


# T10
def test_t10_timeout_is_runner_timeout_exit_3_and_removed() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.wait = WaitResult(None, True)
    receipt = _run(plan, effects)
    assert receipt.outcome == "runner_timeout" and receipt.exit_status == 3
    assert receipt.container_removed and effects.removed == [plan.container_name]


def test_t10_real_effects_stop_then_kill_on_overrun() -> None:
    commands: list[list[str]] = []

    def runner(argv, **_):
        commands.append(list(argv))
        return CommandResult(0)

    class Slow:
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("docker", timeout)

        def kill(self):
            commands.append(["kill-process"])

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=runner,
        popen=lambda argv: Slow(),
    )
    result = effects.start_and_wait("c1", timeout_seconds=1)
    assert result.timed_out and result.exit_code is None
    assert commands[0] == ["/usr/bin/docker", "stop", "-t", "60", "c1"]
    assert commands[1] == ["/usr/bin/docker", "kill", "c1"]


def test_nonzero_exit_is_failed_exit_1() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.wait = WaitResult(3, False)
    receipt = _run(plan, effects)
    assert receipt.outcome == "failed" and receipt.exit_status == 1
    assert receipt.exit_code == 3 and receipt.container_removed


# T11
def test_t11_existing_output_volume_is_refused() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.volume_present = True
    receipt = _run(plan, effects)
    assert receipt.reason == "job_output_exists" and "create" not in effects.calls


# P-SEC rev6 / C-29: import uid, and not in the docker group
def test_the_container_runs_as_the_spec_user_and_a_different_user_is_refused() -> None:
    plan = make_plan()
    assert plan.user == "10001:10001"
    effects = FakeEffects(plan)
    effects.container["Config"]["User"] = "10002:10001"
    assert _run(plan, effects).reason == "job_shape_refused"


@pytest.mark.parametrize(
    ("uids", "gids"),
    [(frozenset({10001}), frozenset({999})), (frozenset(), frozenset({10001}))],
)
def test_the_job_uid_must_not_reach_the_docker_group(uids, gids) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.docker_uids, effects.docker_gids = uids, gids
    receipt = _run(plan, effects)
    assert receipt.reason == "job_uid_in_docker_group"
    assert "create" not in effects.calls


def test_pinned_import_uid_must_equal_the_spec_user() -> None:
    assert make_plan().user == "10001:10001"
    with pytest.raises(OperatorJobRefusal) as excinfo:
        make_plan(identity={**IDENTITY, contract.IDENTITY_JOB_UID: 10002})
    assert excinfo.value.code == "job_uid_mismatch"


class _Sec:
    def __init__(self, user: str, exceptions: tuple = ()) -> None:
        self.user = user
        self.exceptions = exceptions


class _Placement:
    mounts = ()
    networks = ("back",)


class _Job:
    volumes = ()
    entrypoint = ()
    placement = _Placement()

    def __init__(self, security: _Sec) -> None:
        self.security = security


@pytest.mark.parametrize(
    "security", [_Sec("0:0"), _Sec("10001:0"), _Sec("10001:10001", (object(),))]
)
def test_root_users_and_security_exceptions_never_plan(security: _Sec) -> None:
    from dotmac_deployment_foundation.operator_job import _require_declared_shape

    with pytest.raises(OperatorJobRefusal):
        _require_declared_shape(_Job(security))  # type: ignore[arg-type]


# P-SEC R6-1: SIGTERM and the agent deadline
def test_sigterm_during_the_wait_removes_the_container_and_writes_a_receipt() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.wait = WaitResult(None, False, interrupted=True)
    receipt = _run(plan, effects)
    assert receipt.outcome == "interrupted" and receipt.exit_status == 3
    assert receipt.container_removed and effects.removed == [plan.container_name]
    assert json.loads(receipt.to_json())["outcome"] == "interrupted"


def test_a_signal_before_create_creates_nothing_but_still_reports() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.terminate = True
    receipt = _run(plan, effects)
    assert receipt.outcome == "interrupted"
    assert "create" not in effects.calls and effects.removed == []


def test_real_sigterm_is_trapped_then_stops_and_removes() -> None:
    import signal

    commands: list[list[str]] = []

    def runner(argv, **_):
        commands.append(list(argv))
        return CommandResult(0)

    class Waiting:
        def wait(self, timeout=None):
            os.kill(os.getpid(), signal.SIGTERM)
            raise subprocess.TimeoutExpired("docker", timeout)

        def kill(self):  # pragma: no cover
            pass

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=runner,
        popen=lambda argv: Waiting(),
    )
    with effects.sigterm_trap():
        result = effects.start_and_wait("c1", timeout_seconds=100)
        assert effects.terminate_requested()
    assert result.interrupted and not result.timed_out
    assert commands[0][:5] == ["/usr/bin/docker", "stop", "-t", "20", "c1"]
    assert commands[1] == ["/usr/bin/docker", "kill", "c1"]
    assert signal.getsignal(signal.SIGTERM) is not None


def test_the_agent_deadline_caps_the_wait_before_the_stop_grace_and_rm() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    now = int(effects.now().timestamp())
    effects.env[contract.ENV_DEADLINE] = str(now + 300)
    _run(plan, effects)
    # (p): stop is started 60 s grace + 30 s rm budget before the deadline
    assert "start:210" in effects.calls


@pytest.mark.parametrize("offset", [None, "", "soon", -5, 0, 60, 119])
def test_q_a_missing_past_or_short_deadline_is_invalid_with_zero_consumes(
    offset,
) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    now = int(effects.now().timestamp())
    if offset is None:
        del effects.env[contract.ENV_DEADLINE]
    elif isinstance(offset, str):
        effects.env[contract.ENV_DEADLINE] = offset
    else:
        effects.env[contract.ENV_DEADLINE] = str(now + offset)
    authority = FakeAuthority()
    receipt = _run(plan, effects, authority)
    assert receipt.reason == "job_deadline_invalid" and receipt.exit_status == 2
    assert authority.consumed == [] and "create" not in effects.calls


def test_s_a_deadline_later_than_the_lease_is_invalid_and_creates_nothing() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    now = int(effects.now().timestamp())
    effects.env[contract.ENV_DEADLINE] = str(now + 1000)
    authority = FakeAuthority()
    authority.lease_expires_at = now + 900
    receipt = _run(plan, effects, authority)
    assert receipt.reason == "job_deadline_invalid"
    assert "create" not in effects.calls and "create_volume" not in effects.calls
    # a deadline at or before the lease is accepted
    plan2 = make_plan(make_resolved(**{contract.RESOLVED_DISPATCH_ID: "d2"}))
    ok = FakeEffects(plan2)
    ok.env[contract.ENV_DEADLINE] = str(now + 900)
    other = FakeAuthority()
    other.lease_expires_at = now + 900
    assert _run(plan2, ok, other).outcome == "succeeded"


def test_deadline_passing_mid_run_is_a_timeout_stop_then_kill() -> None:
    commands: list[list[str]] = []
    clock = iter([0.0, 0.0, 50.0, 200.0, 200.0])

    def runner(argv, **_):
        commands.append(list(argv))
        return CommandResult(0)

    class Slow:
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("docker", timeout)

        def kill(self):  # pragma: no cover
            pass

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=runner,
        popen=lambda argv: Slow(),
        monotonic=lambda: next(clock),
    )
    result = effects.start_and_wait("c1", timeout_seconds=100)
    assert result.timed_out and not result.interrupted
    assert commands[0][1] == "stop" and commands[1][1] == "kill"


def test_every_job_container_is_labelled_with_its_dispatch_id() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    _run(plan, effects)
    argv = effects.create_argv[0]
    assert "dotmac.dispatch_id=disp-0001" in argv


def test_the_authority_must_support_run_job() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    bindings = copy.copy(make_bindings())
    bindings.authorization_v3_provider = object()
    receipt = run_operator_job(plan, effects, bindings, pair=PAIR, target="host-a")
    assert receipt.reason == "job_authority_absent"
    assert "create" not in effects.calls


def test_the_receipt_carries_names_digests_and_ids_only() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    document = _run(plan, effects).to_document()
    assert document["render_invocation"] == INVOCATION
    assert document["inputs"] == [
        {
            "flag": "--manifest",
            "sha256": make_resolved()[contract.RESOLVED_DISPATCHED_JOB][
                contract.JOB_INPUTS
            ][0][contract.INPUT_SHA256],
        }
    ]
    assert "batch-1" not in json.dumps(document)


def test_output_dir_must_be_the_fixed_job_out() -> None:
    resolved = make_resolved()
    _job(resolved)[contract.JOB_OUTPUT_DIR] = "/job/out"
    assert (
        plan_operator_job(make_spec(), resolved, IDENTITY).outputs_target == "/job/out"
    )
    _job(resolved)[contract.JOB_OUTPUT_DIR] = "/elsewhere"
    with pytest.raises(OperatorJobRefusal):
        plan_operator_job(make_spec(), resolved, IDENTITY)


def test_n_the_pinned_uid_host_account_and_out_dir_owner() -> None:
    plan = make_plan()
    assert plan.user == "10001:10001" and plan.job_uid == 10001
    good = FakeEffects(plan)
    assert _run(plan, good).outcome == "succeeded"
    assert "stat:/job/out" in good.calls
    wrong_host = FakeEffects(plan)
    wrong_host.accounts = {"importer": 10002}
    assert _run(plan, wrong_host, FakeAuthority()).reason == "job_uid_mismatch"
    for owner in [(0, 0, 0o700), (10001, 10001, 0o755), None]:
        effects = FakeEffects(plan)
        effects.image_dir = owner
        receipt = _run(plan, effects, FakeAuthority())
        assert receipt.reason == "job_shape_refused"
        # refused from the IMAGE before any volume or container exists
        assert "create" not in effects.calls and effects.removed == []


def test_n_a_pinned_uid_needs_the_exact_uid_uid_spec_user() -> None:
    with pytest.raises(OperatorJobRefusal) as excinfo:
        make_plan(identity={**IDENTITY, contract.IDENTITY_JOB_UID: 10040})
    assert excinfo.value.code == "job_uid_mismatch"


def test_o_removal_is_scoped_to_this_dispatch_and_both_labels_are_set() -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    _run(plan, effects)
    assert effects.removed_for == [plan.dispatch_id]
    argv = effects.create_argv[0]
    assert "dotmac.dispatch_id=disp-0001" in argv and "dotmac.role_job=1" in argv


def test_remove_container_refuses_another_dispatchs_container() -> None:
    commands: list[list[str]] = []

    def runner(argv, **_):
        commands.append(list(argv))
        return CommandResult(0, "someone-else\n")

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker", environ={}, runner=runner
    )
    assert effects.remove_container("c1", dispatch_id="disp-0001") is False
    assert all(c[1] != "rm" for c in commands)


def test_o_sigint_and_sighup_are_trapped_too() -> None:
    import signal

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=lambda *a, **k: CommandResult(0),
    )
    for number in (signal.SIGINT, signal.SIGHUP):
        with effects.sigterm_trap():
            os.kill(os.getpid(), number)
            assert effects.terminate_requested()


# F-A: the lease expiry from consume is MANDATORY (review, R7-2 / RQ-9 (s))
@pytest.mark.parametrize("lease", [None, "soon", True, 1, "past"])
def test_f_a_a_missing_or_unusable_lease_expiry_refuses_before_create(lease) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    authority = FakeAuthority()
    authority.lease_expires_at = (
        int(effects.now().timestamp()) - 5 if lease == "past" else lease
    )
    receipt = _run(plan, effects, authority)
    assert receipt.reason == "job_deadline_invalid"
    assert authority.consumed == [plan.dispatch_id]  # consumed, then refused
    assert "create" not in effects.calls and "create_volume" not in effects.calls


# F-B: a role job needs job_uid AND job_user; the /job/out check is unconditional
@pytest.mark.parametrize(
    "drop", [contract.IDENTITY_JOB_UID, contract.IDENTITY_JOB_USER, "*"]
)
def test_f_b_a_role_job_without_uid_or_user_never_plans(drop: str) -> None:
    identity = {} if drop == "*" else {k: v for k, v in IDENTITY.items() if k != drop}
    with pytest.raises(OperatorJobRefusal) as excinfo:
        make_plan(identity=identity)
    assert excinfo.value.code == "job_context_invalid"
    with pytest.raises(OperatorJobRefusal):
        plan_operator_job(make_spec(), make_resolved(), None)


def _roleless() -> dict:
    resolved = make_resolved()
    job = _job(resolved)
    job[contract.JOB_ROLE] = ""
    job[contract.JOB_MATERIALS] = ["IMPORT_BATCH"]
    resolved[contract.RESOLVED_JOB_BINDING_NAMES] = ["MIGRATION_DATABASE_URL"]
    resolved.pop(contract.RESOLVED_SECRETS_NAMESPACE)
    return resolved


def test_f_b_the_out_dir_check_runs_even_without_a_pinned_uid() -> None:
    plan = make_plan(
        _roleless(),
        identity={
            contract.IDENTITY_INPUTS_ROOT: IDENTITY[contract.IDENTITY_INPUTS_ROOT]
        },
    )
    assert plan.job_uid is None
    good = FakeEffects(plan)
    assert _run(plan, good).outcome == "succeeded" and "stat:/job/out" in good.calls
    bad = FakeEffects(plan)
    bad.image_dir = (0, 0, 0o700)
    assert _run(plan, bad, FakeAuthority()).reason == "job_shape_refused"


# review ruling 1: a provider missing ANY run_job method is job_authority_absent
@pytest.mark.parametrize(
    "missing", [contract.PROVIDER_RESOLVE_METHOD, contract.PROVIDER_CONSUME_METHOD]
)
def test_ruling_1_a_provider_missing_a_method_is_refused(missing) -> None:
    from types import SimpleNamespace

    from dotmac_deployment_foundation.operator_job import _authority

    class Partial:
        pass

    provider = Partial()
    for name in (contract.PROVIDER_RESOLVE_METHOD, contract.PROVIDER_CONSUME_METHOD):
        if name != missing:
            setattr(provider, name, lambda **_: None)
    bindings = SimpleNamespace(authorization_v3_provider=provider)
    with pytest.raises(OperatorJobRefusal) as excinfo:
        _authority(bindings)
    assert excinfo.value.code == "job_authority_absent"
    plan = make_plan()
    effects = FakeEffects(plan)
    receipt = run_operator_job(plan, effects, bindings, pair=PAIR, target="host-a")
    assert receipt.reason == "job_authority_absent" and "create" not in effects.calls


# review ruling 5: networks must be the compose project's own
@pytest.mark.parametrize("labels", [None, {}, {"com.docker.compose.project": "other"}])
def test_ruling_5_network_labels_must_match_the_compose_project(labels) -> None:
    plan = make_plan()
    effects = FakeEffects(plan)
    effects.net_labels[plan.networks[0]] = labels
    receipt = _run(plan, effects)
    assert receipt.reason == "job_shape_refused" and "create" not in effects.calls


def test_ruling_5_real_effects_read_labels_from_network_inspect() -> None:
    def runner(argv, **_):
        return CommandResult(0, '{"com.docker.compose.project":"p"}\n')

    effects = ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker", environ={}, runner=runner
    )
    assert effects.network_labels("p_back") == {"com.docker.compose.project": "p"}


# F-F: no `assert` statements in production code
def test_f_f_no_assert_in_the_operator_job_production_modules() -> None:
    import ast
    from pathlib import Path

    import dotmac_deployment_foundation as facility

    src = Path(facility.__file__).parent
    for relative in (
        "operator_job.py",
        "providers/compose_operator_job.py",
        "operator_job_contract.py",
    ):
        tree = ast.parse((src / relative).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)], relative
    spec_tree = ast.parse((src / "spec.py").read_text(encoding="utf-8"))
    function = next(
        n
        for n in spec_tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_parse_operator_job"
    )
    assert not [n for n in ast.walk(function) if isinstance(n, ast.Assert)]


# CP does not supply uid/user/render unit/path materials: the root-owned file does
def _identity_file(tmp_path, body: str, mode: int = 0o644):
    path = tmp_path / "job-identity.toml"
    path.write_text(body, encoding="utf-8")
    os.chmod(path, mode)
    return path


def _id_effects(path, owner=None):
    return ComposeOperatorJobEffects(
        docker_bin="/usr/bin/docker",
        environ={},
        runner=lambda *a, **k: CommandResult(0),
        identity_path=str(path),
        identity_owner_uid=os.geteuid() if owner is None else owner,
    )


def test_identity_file_is_read_when_owned_and_not_group_or_world_writable(
    tmp_path,
) -> None:
    path = _identity_file(
        tmp_path, 'job_uid = 10040\njob_user = "importer"\ninputs_root = "/srv/x"\n'
    )
    assert _id_effects(path).read_job_identity() == {
        "job_uid": 10040,
        "job_user": "importer",
        "inputs_root": "/srv/x",
    }


@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
def test_identity_file_writable_by_group_or_other_is_refused(tmp_path, mode) -> None:
    path = _identity_file(tmp_path, "job_uid = 1\n", mode)
    with pytest.raises(OperatorJobRefusal) as excinfo:
        _id_effects(path).read_job_identity()
    assert excinfo.value.code == "job_identity_invalid"


def test_identity_file_with_the_wrong_owner_symlink_or_toml_is_refused(
    tmp_path,
) -> None:
    path = _identity_file(tmp_path, "job_uid = 1\n")
    with pytest.raises(OperatorJobRefusal):
        _id_effects(path, owner=os.geteuid() + 1).read_job_identity()
    link = tmp_path / "link.toml"
    link.symlink_to(path)
    with pytest.raises(OperatorJobRefusal):
        _id_effects(link).read_job_identity()
    with pytest.raises(OperatorJobRefusal):
        _id_effects(_identity_file(tmp_path, "not = [toml")).read_job_identity()


def test_an_absent_identity_file_is_none_and_the_default_path_is_the_constant() -> None:
    assert _id_effects(tmp_path_missing()).read_job_identity() is None
    assert contract.JOB_IDENTITY_PATH == "/etc/dotmac/deploy-agent/job-identity.toml"


def tmp_path_missing() -> str:
    return "/nonexistent-dir-for-test/job-identity.toml"


def test_input_host_paths_come_from_the_identity_inputs_root_not_cp() -> None:
    plan = make_plan()
    assert plan.input_mounts[0].source == (
        "/srv/example/job-work/import_execute/in/manifest.json"
    )
    with pytest.raises(OperatorJobRefusal) as excinfo:
        make_plan(identity={k: v for k, v in IDENTITY.items() if k != "inputs_root"})
    assert excinfo.value.code == "job_input_refused"


# F2-H1 (ii): the loaded descriptor must be the one the authorization approved
def test_descriptor_digest_match_plans() -> None:
    assert make_plan().name == "import_execute"


@pytest.mark.parametrize(
    "tampered",
    [
        pytest.param(
            JOB_TOML.replace('"/run/secrets/dotmac"', '"/run/secrets/evil"'),
            id="operator_table",
        ),
        pytest.param(
            JOB_TOML + '[compose_topology.jobs.security]\nuser = "10002:10002"\n',
            id="security_user",
        ),
    ],
)
def test_descriptor_digest_mismatch_refuses_before_anything_else(tampered) -> None:
    resolved = make_resolved()  # signed over the UNTAMPERED descriptor
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(tampered), resolved, IDENTITY)
    assert excinfo.value.code == "job_descriptor_mismatch"


@pytest.mark.parametrize("value", [None, "", "not-a-digest", "sha256:" + "0" * 64])
def test_descriptor_digest_absent_malformed_or_wrong_refuses(value) -> None:
    resolved = make_resolved()
    if value is None:
        del resolved[contract.RESOLVED_DESCRIPTOR_DIGEST]
    else:
        resolved[contract.RESOLVED_DESCRIPTOR_DIGEST] = value
    with pytest.raises(OperatorJobRefusal) as excinfo:
        plan_operator_job(make_spec(), resolved, IDENTITY)
    assert excinfo.value.code == "job_descriptor_mismatch"
