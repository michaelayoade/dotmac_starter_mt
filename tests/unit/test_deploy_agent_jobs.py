"""dotmac-deploy-agent: job roles (REVIEW-import-jobs A3-A9, A13). Names for the
job-plan keys that Control has not published yet (dispatched_job, argv) are NOT
assumed here; only `deploy_binding.jobs` (Control #76 at e9c0b60) is."""

# ruff: noqa: E501
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from dotmac_deploy_agent.canon import digest
from dotmac_deploy_agent.jobs import (
    JobSpec,
    job_env,
    refuse_sealing,
    split_params,
)
from dotmac_deploy_agent.params import Param, job_env_render
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.transport import Response

from tests.unit.deploy_agent_support import Rig, make_materials, make_rig, prepare_role

GATE = Param("import_apply_ok", "ERP_IMPORT_APPROVED", "bool", "true")
GATE_ROW = {
    "name": "import_apply_ok",
    "material": "ERP_IMPORT_APPROVED",
    "type": "bool",
    "value": "true",
}
BASE_ROWS = [
    {"name": "app_port", "material": "APP_PORT", "type": "int", "value": "8080"},
]


def job_row(**kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "name": "import",
        "role": "import",
        "single_use": True,
        "materials": ["LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"],
        "params": ["import_apply_ok"],
        "params_env_digest": digest(job_env_render([GATE])),
    }
    row.update(kw)
    return row


def job_rig(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    prepare_role(rig, "import")
    return rig


def mint_bodies(rig: Rig) -> list[dict[str, object]]:
    return [r[3] for r in rig.bao.requests if r[1].endswith("/secret-id")]


def secret_file(rig: Rig) -> Path:
    return rig.config.role_dir("import") / "secret_id"


def tracked(rig: Rig) -> list[str]:
    path = rig.config.state_dir / "job-accessors" / "import.json"
    return json.loads(path.read_text())["retire"] if path.exists() else []


def deliver(rig: Rig) -> str:
    from dotmac_deploy_agent.job_delivery import deliver_job_secret_id

    session = rig.launcher._bao_session()
    try:
        return deliver_job_secret_id(
            session, rig.config, "import", rig.launcher.job_accessors, dispatch_id="d-1"
        )
    finally:
        session.revoke()


# -- A5 / A7: the job mint -------------------------------------------------
def test_a_job_mint_body_is_metadata_only(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    assert [set(b) for b in mint_bodies(rig)] == [{"metadata"}]  # no ttl, no num_uses


def test_a_serving_mint_still_sends_ttl(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    prepare_role(rig, "web")
    rig.cp.materials = make_materials(roles=[{"role": "web", "single_use": False}])
    rig.launcher.run_once()
    assert set(mint_bodies(rig)[0]) == {"metadata", "ttl"}


def test_the_job_accessor_is_tracked_before_the_file_exists_and_the_receipt_is_a_prefix(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    prefix = deliver(rig)
    assert tracked(rig) == ["accessor-wrap-1"] and prefix == "accessor"[:8]
    assert stat.S_IMODE(secret_file(rig).stat().st_mode) == 0o400


def test_a_second_job_mint_is_refused_while_an_accessor_is_still_tracked(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    with pytest.raises(Refusal) as refused:
        deliver(rig)
    assert refused.value.code == "secret_delivery_failed"
    assert len(mint_bodies(rig)) == 1


def test_a_job_accessor_that_cannot_be_tracked_is_never_written(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    original = rig.bao.request

    def no_accessor(method, path, headers, body):  # type: ignore[no-untyped-def]
        response = original(method, path, headers, body)
        if path == "/v1/sys/wrapping/unwrap":
            document = json.loads(response.body)
            del document["data"]["secret_id_accessor"]
            return Response(200, {}, json.dumps(document).encode())
        return response

    rig.launcher.bao_transport = type("T", (), {"request": staticmethod(no_accessor)})()
    with pytest.raises(Refusal):
        deliver(rig)
    assert not secret_file(rig).exists() and tracked(rig) == []


# -- A7: purge on exit, die and agent start --------------------------------
def test_purge_removes_the_file_and_destroys_the_accessor(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    (rig.config.role_dir("import") / "legacy_url").write_text("x")
    assert rig.launcher.purge_job_role("import", ("legacy_url",)) == 0
    assert not secret_file(rig).exists()
    assert not (rig.config.role_dir("import") / "legacy_url").exists()
    assert tracked(rig) == []
    assert any(r[1].endswith("/secret-id-accessor/destroy") for r in rig.bao.requests)


def test_purge_with_openbao_down_still_removes_files_and_keeps_the_accessor(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    rig.bao.fail_destroy = True
    assert rig.launcher.purge_job_role("import") == 1
    assert not secret_file(rig).exists() and tracked(rig) == ["accessor-wrap-1"]


def test_purge_refuses_a_rendered_name_that_is_a_path(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    with pytest.raises(Refusal):
        rig.launcher.purge_job_role("import", ("../x",))


def test_agent_start_purges_a_stale_job_secret_and_accessor(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)  # then the agent "crashes": file and tracking survive
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"
    assert not secret_file(rig).exists() and tracked(rig) == []


def test_agent_start_with_openbao_unreachable_keeps_tracking_and_does_not_crash(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    rig.bao.fail_destroy = True
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"
    assert not secret_file(rig).exists() and tracked(rig) == ["accessor-wrap-1"]


# -- A4: mint only at job dispatch -----------------------------------------
def test_a_serving_deploy_and_idle_cycles_never_mint_a_job_role(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    prepare_role(rig, "web")
    rig.cp.materials = make_materials(
        roles=[{"role": "web", "single_use": False}],
        jobs=[job_row()],
        params=[*BASE_ROWS, GATE_ROW],
    )
    assert rig.launcher.run_once().outcome == "completed"
    rig.cp.materials = None
    for _ in range(3):
        rig.epoch[0] += 900
        rig.launcher.run_once()
    assert not any("-import/" in p for p in rig.bao.paths)
    assert list((rig.config.state_dir / "job-accessors").glob("*.json")) == []


def test_a_role_that_is_both_serving_and_a_job_role_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    prepare_role(rig, "import")
    rig.cp.materials = make_materials(
        roles=[{"role": "import", "single_use": True}],
        jobs=[job_row()],
        params=[*BASE_ROWS, GATE_ROW],
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


# -- A8: never sealed ------------------------------------------------------
def test_sealing_refuses_a_job_role_and_the_job_only_names() -> None:
    jobs = (JobSpec("import", "import", True, (), (), "sha256:" + "0" * 64),)
    for role, names in (
        ("import", ()),
        ("app", ("LEGACY_DATABASE_URL",)),
        ("app", ("MIGRATION_DATABASE_URL", "X")),
    ):
        with pytest.raises(Refusal) as refused:
            refuse_sealing(role, names, jobs)
        assert refused.value.code == "job_role_refused"
    refuse_sealing("app", ("DATABASE_URL",), jobs)  # an ordinary serving role


def test_the_kit_installs_no_sealed_copy_instance_for_a_job_role() -> None:
    kit = Path(__file__).resolve().parents[2] / "packages/dotmac-deploy-agent/deploy"
    names = [p.name.lower() for p in kit.rglob("*")]
    assert not any("lkg" in n or "seal" in n or "restore" in n for n in names)


# -- A9: scoped params reach the job env only ------------------------------
def test_scoped_params_never_enter_params_env(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(jobs=[job_row()], params=[*BASE_ROWS, GATE_ROW])
    assert rig.launcher.run_once().outcome == "completed"
    seen = rig.runner.seen_params or b""
    assert b"APP_PORT=8080" in seen and b"ERP_IMPORT_APPROVED" not in seen


def test_a_serving_digest_that_counts_a_scoped_param_is_refused(tmp_path: Path) -> None:
    from dotmac_deploy_agent.params import canonical_render

    rig = make_rig(tmp_path)
    wrong = digest(
        canonical_render([Param("app_port", "APP_PORT", "int", "8080"), GATE], "x")
    )
    rig.cp.materials = make_materials(
        jobs=[job_row()],
        params=[*BASE_ROWS, GATE_ROW],
        params_env_digest=wrong,
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "params_digest_mismatch")


def test_the_job_env_is_only_its_scoped_params_and_matches_the_per_job_digest() -> None:
    job = JobSpec(
        "import",
        "import",
        True,
        (),
        ("import_apply_ok",),
        digest(job_env_render([GATE])),
    )
    serving, scoped = split_params(
        [Param("app_port", "APP_PORT", "int", "8080"), GATE], (job,)
    )
    assert [p.name for p in serving] == ["app_port"]
    assert job_env(job, scoped["import"]) == b"ERP_IMPORT_APPROVED=true\n"


def test_a_job_env_with_a_changed_or_missing_scoped_value_is_refused() -> None:
    job = JobSpec(
        "import",
        "import",
        True,
        (),
        ("import_apply_ok",),
        digest(job_env_render([GATE])),
    )
    changed = Param("import_apply_ok", "ERP_IMPORT_APPROVED", "bool", "false")
    for scoped in ([changed], []):
        with pytest.raises(Refusal) as refused:
            job_env(job, scoped)
        assert refused.value.code == "params_digest_mismatch"


def test_a_job_credential_is_never_accepted_as_a_plain_param() -> None:
    dsn = Param("dsn", "MIGRATION_DATABASE_URL", "string", "postgres-host")
    job = JobSpec("import", "import", True, (), ("dsn",), digest(job_env_render([dsn])))
    with pytest.raises(Refusal) as refused:
        job_env(job, [dsn])
    assert refused.value.code == "job_role_refused"


# -- A13: the pinned naming also covers job-only plans ---------------------
@pytest.mark.parametrize(
    "override", [{"secrets_namespace": "other"}, {"secrets_environment": "prod"}]
)
def test_a_job_only_plan_with_a_different_naming_is_refused(
    tmp_path: Path, override: dict[str, str]
) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(
        jobs=[job_row()], params=[*BASE_ROWS, GATE_ROW], **override
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "secrets_naming_mismatch")


def test_a_job_only_plan_without_naming_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(
        jobs=[job_row()], params=[*BASE_ROWS, GATE_ROW], secrets_environment=None
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_invalid")


def test_a_plan_with_no_role_job_needs_no_naming(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(
        jobs=[job_row(role=None, single_use=None, params=[])],
        secrets_environment=None,
    )
    assert rig.launcher.run_once().outcome == "completed"


# -- run_job: the signed one-shot (REVIEW-import-jobs, Foundation runs it) --
CLOSES = "2027-01-06T13:00:00Z"  # the rig's store_now is 12:00:00Z


def dispatched(**kw: object) -> dict[str, object]:
    row = job_row(
        argv=["python", "-m", "erp_v2.importer", "execute"],
        timeout_seconds=600,
        window={"window_id": "w1", "closes_at": CLOSES},
    )
    row.update(kw)
    return row


def run_job_materials(
    job: dict[str, object] | None = None, **kw: object
) -> dict[str, object]:
    kw.setdefault("statement", {"operation": "run_job"})
    return make_materials(
        dispatched_job=job or dispatched(),
        params=[*BASE_ROWS, GATE_ROW],
        lease_ttl_seconds=kw.pop("lease_ttl_seconds", 900),
        **kw,
    )


def run_job_rig(tmp_path: Path, *, render: bool = True, **config: object) -> Rig:
    config.setdefault("job_roles", ("import",))
    rig = make_rig(tmp_path, **config)
    prepare_role(rig, "import")
    if render:  # what the static openbao-agent unit renders, once
        for name in ("legacy_database_url", "migration_database_url"):
            path = rig.config.role_dir("import") / name
            path.write_text("rendered")
            path.chmod(0o400)
    rig.cp.materials = run_job_materials()
    return rig


def refused(rig: Rig) -> tuple[str, str | None]:
    result = rig.launcher.run_once()
    return result.outcome, result.reason


def receipt(rig: Rig) -> dict[str, object]:
    return rig.cp.observations[-1]["details"]["job"]  # type: ignore[no-any-return, index]


def test_a_run_job_runs_with_a_metadata_only_mint_and_a_complete_receipt(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert [set(b) for b in mint_bodies(rig)] == [{"metadata"}]
    spec = rig.runner.specs[0]
    assert spec.job_name == "import" and spec.job_secrets_dir == rig.config.role_dir(
        "import"
    )
    assert spec.job_argv_digest == digest(
        b'["python","-m","erp_v2.importer","execute"]'
    )
    done = receipt(rig)
    assert done["name"] == "import" and done["exit_code"] == 0
    assert (
        done["accessor_prefix"] == "accessor" and done["accessors_still_tracked"] == 0
    )
    assert done["files"] == ["legacy_database_url", "migration_database_url"]
    assert {"started_at", "finished_at", "window_id"} <= set(done)


def test_job_params_go_to_their_own_file_never_into_params_env(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    seen: dict[str, bytes] = {}

    def effect(spec) -> None:  # type: ignore[no-untyped-def]
        seen["job"] = spec.job_params_env.read_bytes()
        seen["mode"] = oct(spec.job_params_env.stat().st_mode & 0o777).encode()

    rig.runner.effect = effect
    assert refused(rig) == ("completed", None)
    assert seen == {"job": b"ERP_IMPORT_APPROVED=true\n", "mode": b"0o400"}
    assert b"ERP_IMPORT_APPROVED" not in (rig.runner.seen_params or b"")
    assert list(rig.config.deploy_run_dir.iterdir()) == []  # both files removed


def test_files_and_accessor_are_purged_after_a_failing_run_and_before_the_receipt(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.runner import RunResult  # noqa: F401

    rig = run_job_rig(tmp_path)
    rig.runner.exit_code = 3
    assert refused(rig) == ("failed", None)
    assert list(rig.config.role_dir("import").iterdir()) == []
    assert tracked(rig) == [] and receipt(rig)["exit_code"] == 3
    assert any(r[1].endswith("/secret-id-accessor/destroy") for r in rig.bao.requests)


def test_a_timed_out_job_is_purged_too(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.runner.timed_out = True
    assert refused(rig) == ("failed", "runner_timeout")
    assert list(rig.config.role_dir("import").iterdir()) == [] and tracked(rig) == []


def test_a_crash_after_the_mint_is_purged_at_the_next_agent_start(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path)

    def crash(spec) -> None:  # type: ignore[no-untyped-def]
        raise Refusal("runner_start_failed")

    rig.runner.effect = crash
    assert refused(rig) == ("failed", "runner_start_failed")
    assert list(rig.config.role_dir("import").iterdir()) == []


def test_with_openbao_down_the_job_fails_closed_before_it_runs(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.launcher.bao_transport = None
    assert refused(rig) == ("refused", "secret_delivery_failed")
    assert rig.runner.specs == []
    assert list(rig.config.role_dir("import").iterdir()) == []


def test_a_job_run_refuses_a_second_dispatch_of_the_same_id(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    assert refused(rig)[0] == "completed"
    for name in ("legacy_database_url", "migration_database_url"):
        (rig.config.role_dir("import") / name).write_text("x")
        (rig.config.role_dir("import") / name).chmod(0o400)
    rig.cp.materials = run_job_materials()
    assert refused(rig) == ("refused", "dispatch_replayed")
    assert len(mint_bodies(rig)) == 1


def test_a_second_role_job_on_the_host_is_refused_while_the_lock_is_held(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path)
    held = rig.launcher._lock_role_job()
    try:
        assert refused(rig) == ("refused", "lease_refused")
    finally:
        import os

        os.close(held)
    assert rig.runner.specs == [] and mint_bodies(rig) == []


# parse-time refusals: before any lease, mint or file
@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {
                "job": dispatched(
                    window={"window_id": "w1", "closes_at": "2027-01-06T12:00:00Z"}
                )
            },
            "job_window_closed",
        ),
        (
            {
                "job": dispatched(
                    window={"window_id": "w1", "closes_at": "2027-01-06T11:00:00Z"}
                )
            },
            "job_window_closed",
        ),
        ({"job": dispatched(timeout_seconds=3481)}, "materials_invalid"),
        (
            {"job": dispatched(timeout_seconds=600), "lease_ttl_seconds": 719},
            "lease_ttl_invalid",
        ),
        (
            {"job": dispatched(timeout_seconds=1800), "lease_ttl_seconds": 1919},
            "lease_ttl_invalid",
        ),
        ({"job": dispatched(role=None, single_use=None)}, "materials_invalid"),
        ({"job": dispatched(role="other")}, "job_role_refused"),
    ],
)
def test_run_job_is_refused_before_anything_when_the_plan_does_not_fit(
    tmp_path: Path, override: dict[str, object], expected: str
) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        override.pop("job"),
        **override,  # type: ignore[arg-type]
    )
    assert refused(rig) == ("refused", expected)
    assert rig.bao.paths == [] and rig.runner.specs == [] and rig.cp.lease_log == []


def test_the_lease_fit_boundary_is_accepted(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=600), lease_ttl_seconds=720
    )
    assert refused(rig) == ("completed", None)


def test_a_job_role_the_host_has_not_enrolled_is_refused_by_default(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path, job_roles=())
    assert refused(rig) == ("refused", "job_role_refused")
    assert rig.bao.paths == [] and rig.runner.specs == []


def test_operation_and_dispatched_job_must_agree(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(statement={"operation": "deploy"})
    assert refused(rig) == ("refused", "materials_invalid")
    rig.cp.materials = make_materials(
        statement={"operation": "run_job"}, dispatch_id="dispatch-2"
    )
    assert refused(rig) == ("refused", "materials_invalid")


def test_a_run_job_plan_that_also_mints_serving_roles_is_refused(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(roles=[{"role": "web", "single_use": False}])
    assert refused(rig) == ("refused", "materials_invalid")


def test_a_run_job_plan_with_a_different_naming_is_refused(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(secrets_namespace="other")
    assert refused(rig) == ("refused", "secrets_naming_mismatch")


def test_a_per_job_digest_mismatch_is_refused_before_any_mint(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(params_env_digest="sha256:" + "9" * 64)
    )
    assert refused(rig) == ("refused", "params_digest_mismatch")
    assert rig.bao.paths == []


@pytest.mark.parametrize("mode", [0o440, 0o644, 0o600])
def test_rendered_files_with_a_wrong_mode_stop_the_job(
    tmp_path: Path, mode: int
) -> None:
    rig = run_job_rig(tmp_path)
    (rig.config.role_dir("import") / "legacy_database_url").chmod(mode)
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.runner.specs == [] and tracked(rig) == []


def test_a_missing_rendered_file_stops_the_job(tmp_path: Path) -> None:
    rig = run_job_rig(tmp_path, render=False)
    assert refused(rig) == ("refused", "job_files_invalid")
    assert (
        rig.runner.specs == [] and list(rig.config.role_dir("import").iterdir()) == []
    )


def test_rendered_files_owned_by_another_uid_stop_the_job(tmp_path: Path) -> None:
    import os

    rig = run_job_rig(tmp_path, job_secrets_uid=os.geteuid() + 1)
    assert refused(rig) == ("refused", "job_files_invalid")


# -- Foundation's operator-job entry does not exist yet ---------------------
def test_the_real_runner_refuses_a_job_with_no_foundation_entry(tmp_path: Path) -> None:
    from dotmac_deploy_agent.runner import SubprocessRunner

    rig = run_job_rig(tmp_path)
    rig.launcher.runner = SubprocessRunner(("/usr/bin/true",), 60)
    assert refused(rig) == ("refused", "foundation_job_entry_unavailable")
    # nothing was spent: no lease, no mint, no file, not journalled
    assert rig.bao.paths == [] and rig.cp.lease_log == []
    assert not rig.launcher.executed.seen("dispatch-1")


def test_the_job_environment_names_for_foundation_are_exactly_the_four() -> None:
    from dotmac_deploy_agent.runner import RunSpec, runner_environment

    env = runner_environment(
        RunSpec(
            "d", "erp/staging/h", "erp", "sha256:" + "a" * 64, Path("/p"), None,
            job_name="import", job_params_env=Path("/j"), job_secrets_dir=Path("/s"),
            job_argv_digest="sha256:" + "b" * 64,
        )
    )  # fmt: skip
    assert {k for k in env if k.startswith("DOTMAC_DEPLOY_JOB")} == {
        "DOTMAC_DEPLOY_JOB",
        "DOTMAC_DEPLOY_JOB_PARAMS_ENV",
        "DOTMAC_DEPLOY_JOB_SECRETS_DIR",
        "DOTMAC_DEPLOY_JOB_ARGV_DIGEST",
    }


def test_job_roles_config_is_validated(tmp_path: Path) -> None:
    from tests.unit.deploy_agent_support import Keys, make_config

    for bad in (("Import",), ("a", "a"), ("../x",)):
        with pytest.raises(Refusal):
            make_config(tmp_path, Keys(), job_roles=bad)
    assert make_config(tmp_path, Keys()).job_roles == ()  # empty by default


def test_a_lease_longer_than_900_is_accepted_when_the_job_fits_inside_it(
    tmp_path: Path,
) -> None:
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=1800), lease_ttl_seconds=3000
    )
    assert refused(rig) == ("completed", None)
    assert rig.cp.lease_log[0][1]["ttl_seconds"] == 3000  # type: ignore[index]


def test_only_the_declared_materials_are_left_for_the_job_to_mount(
    tmp_path: Path,
) -> None:
    """An owner-only job declares MIGRATION only: the LEGACY file the role's
    authored config also rendered is removed before the job starts."""
    rig = run_job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(materials=["MIGRATION_DATABASE_URL"])
    )
    seen: dict[str, list[str]] = {}

    def effect(spec) -> None:  # type: ignore[no-untyped-def]
        seen["files"] = sorted(p.name for p in spec.job_secrets_dir.iterdir())

    rig.runner.effect = effect
    assert refused(rig) == ("completed", None)
    assert seen["files"] == ["migration_database_url"]
    assert receipt(rig)["files"] == ["migration_database_url"]


def test_a_refused_job_mint_fails_that_job_only_and_touches_no_serving_role(
    tmp_path: Path,
) -> None:
    from tests.unit.test_deploy_agent_delivery import WEB, secret_id_file, state

    rig = make_rig(tmp_path, job_roles=("import",))
    prepare_role(rig, "web")
    prepare_role(rig, "import")
    rig.cp.materials = make_materials(roles=WEB)
    assert rig.launcher.run_once().outcome == "completed"  # a delivered serving role
    served_file = secret_id_file(rig, "web").read_bytes()
    served_state = state(rig, "web")
    rig.bao.requests.clear()
    rig.bao.deny_mint = "erp-staging-import"  # OpenBao 403s the JOB mint
    rig.cp.materials = make_materials(
        dispatched_job=dispatched(),
        params=[*BASE_ROWS, GATE_ROW],
        statement={"operation": "run_job"},
        dispatch_id="dispatch-2",
    )
    for name in ("legacy_database_url", "migration_database_url"):
        (rig.config.role_dir("import") / name).write_text("x")
        (rig.config.role_dir("import") / name).chmod(0o400)
    assert refused(rig) == ("refused", "job_mint_refused")  # not a delivery fault
    assert len(rig.runner.specs) == 1  # only the serving run ever started
    assert secret_id_file(rig, "web").read_bytes() == served_file
    assert state(rig, "web") == served_state
    assert not any("-web" in r[1] for r in rig.bao.requests)  # nothing of web touched
    assert list(rig.config.role_dir("import").iterdir()) == []  # job files purged
