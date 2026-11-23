"""dotmac-deploy-agent: job roles and `run_job` (REVIEW-import-jobs A3-A13; r3 S1-S7).

Shapes follow Control 170afa1e: `deploy_binding.jobs` are DEFINITIONS (nothing runs
from them) and `dispatched_job` carries the signed `{MATERIAL: value}` params."""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.canon import digest
from dotmac_deploy_agent.commands import CommandResult
from dotmac_deploy_agent.jobs import (
    JobDefinition,
    plan_dispatched_job,
    refuse_sealing,
)
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.transport import Response

from tests.unit.deploy_agent_support import (
    APP_IMAGE,
    Rig,
    base_images,
    make_materials,
    make_rig,
    prepare_role,
)

BASE_ROWS = [
    {"name": "app_port", "material": "APP_PORT", "type": "int", "value": "8080"}
]
GATE_ROW = {
    "name": "import_apply_ok",
    "material": "ERP_IMPORT_APPROVED",
    "type": "bool",
    "value": "true",
}
ENV_DIGEST = digest(b"ERP_IMPORT_APPROVED=true\n")
CLOSES = "2027-01-06T13:00:00Z"  # the rig's store_now is 12:00:00Z
FILES = ("legacy_database_url", "migration_database_url")


def definition(**kw: object) -> dict[str, object]:
    """A descriptor job as Control lists it in every plan (extra keys included)."""
    row: dict[str, object] = {
        "name": "import",
        "role": "import",
        "command": ["python", "-m", "erp_v2.importer"],
        "materials": ["LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"],
        "params": ["import_apply_ok"],
        "args": [{"flag": "--x", "type": "string"}],
        "timeout_seconds": 600,
    }
    row.update(kw)
    return row


def dispatched(**kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "name": "import",
        "role": "import",
        "single_use": True,
        "materials": ["LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"],
        "params": {"ERP_IMPORT_APPROVED": "true"},
        "params_env_digest": ENV_DIGEST,
        "argv": ["python", "-m", "erp_v2.importer", "execute"],
        "timeout_seconds": 600,
        "window": {"window_id": "w1", "closes_at": CLOSES},
    }
    row.update(kw)
    return row


def present() -> list[dict[str, object]]:
    return [{**i, "source": "present"} for i in base_images()]


def run_job_materials(
    job: dict[str, object] | None = None, **kw: object
) -> dict[str, object]:
    kw.setdefault("statement", {"operation": "run_job"})
    kw.setdefault("images", present())
    # v2 job plan: lease ttl = max = timeout + 120 (the default job runs 600 s)
    kw.setdefault("lease_ttl_seconds", 720)
    kw.setdefault("lease_max_seconds", kw["lease_ttl_seconds"])
    kw.setdefault("jobs", [definition()])
    kw.setdefault("params", BASE_ROWS)
    return make_materials(dispatched_job=job or dispatched(), **kw)


def authored(*names: str, extra: str = "") -> str:
    """An authored static config: the exit settings plus one template per file."""
    from tests.unit.deploy_agent_support import AUTHORED

    body = "".join(
        f'template {{\n  destination = "/run/dotmac/erp/import/secrets/{n}"\n}}\n'
        for n in names
    )
    return AUTHORED + body + extra


def job_rig(tmp_path: Path, *, render: bool = True, **config: object) -> Rig:
    config.setdefault("job_roles", ("import",))
    rig = make_rig(tmp_path, **config)
    prepare_role(rig, "import")
    for name, files in (
        ("erp-import.hcl", FILES),  # owner + legacy
        ("erp-import-owner.hcl", ("migration_database_url",)),  # owner only
    ):
        path = rig.config.openbao_config_dir / name
        path.write_text(authored(*files))
        path.chmod(0o644)
    secrets = rig.config.role_dir("import") / "secrets"
    secrets.mkdir(mode=0o710)
    secrets.chmod(0o710)
    if render:  # what the static one-shot openbao-agent unit renders
        for name in FILES:
            (secrets / name).write_text("rendered")
            (secrets / name).chmod(0o400)
    rig.systemctl.script = {
        ("--property=Result,ExecMainStatus",): CommandResult(
            0, b"Result=success\nExecMainStatus=0\n"
        ),
        **rig.systemctl.script,
    }
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, json.dumps([APP_IMAGE]).encode()
    )
    rig.cp.materials = run_job_materials()
    return rig


def secrets_dir(rig: Rig) -> Path:
    return rig.config.role_dir("import") / "secrets"


def mint_bodies(rig: Rig) -> list[dict[str, object]]:
    return [r[3] for r in rig.bao.requests if r[1].endswith("/secret-id")]


def tracked(rig: Rig) -> list[str]:
    path = rig.config.state_dir / "job-accessors" / "import.json"
    return json.loads(path.read_text())["retire"] if path.exists() else []


def started(rig: Rig, unit: str) -> bool:
    return any(
        c[-2:] == ("start", unit) or (c[-1] == unit and "start" in c)
        for c in rig.systemctl.calls
    )


def refused(rig: Rig) -> tuple[str, str | None]:
    result = rig.launcher.run_once()
    return result.outcome, result.reason


def last(rig: Rig) -> dict[str, object]:
    return rig.cp.observations[-1]


def receipt(rig: Rig) -> dict[str, object]:
    return last(rig)["details"]["job"]  # type: ignore[no-any-return, index]


def deliver(rig: Rig) -> str:
    from dotmac_deploy_agent.job_delivery import deliver_job_secret_id

    session = rig.launcher._bao_session()
    try:
        return deliver_job_secret_id(
            session, rig.config, "import", rig.launcher.job_accessors, dispatch_id="d-1"
        )
    finally:
        session.revoke()


# -- S1: definitions in every plan never refuse a serving deploy -------------
def test_a_serving_plan_listing_job_definitions_runs_and_mints_no_job_role(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path)
    prepare_role(rig, "web")
    rig.cp.materials = make_materials(
        roles=[{"role": "web", "single_use": False}],
        jobs=[
            definition(),
            definition(name="holdings", role=None, params=[], materials=[]),
        ],
        params=[*BASE_ROWS, GATE_ROW],
    )
    assert rig.launcher.run_once().outcome == "completed"
    rig.cp.materials = None
    for _ in range(3):
        rig.epoch[0] += 900
        rig.launcher.run_once()
    assert not any("-import/" in p for p in rig.bao.paths)
    assert list((rig.config.state_dir / "job-accessors").glob("*.json")) == []
    assert b"ERP_IMPORT_APPROVED" not in (
        rig.runner.seen_params or b""
    )  # scoped: never params.env


def test_a_role_that_is_both_serving_and_a_job_role_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    prepare_role(rig, "import")
    rig.cp.materials = make_materials(
        roles=[{"role": "import", "single_use": True}], jobs=[definition()]
    )
    assert refused(rig) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


# -- A5 / A7: the job mint and its tracking ----------------------------------
def test_a_job_mint_body_is_metadata_only_and_a_serving_mint_still_sends_ttl(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    assert [set(b) for b in mint_bodies(rig)] == [{"metadata"}]
    (tmp_path / "s").mkdir()
    other = make_rig(tmp_path / "s")
    prepare_role(other, "web")
    other.cp.materials = make_materials(roles=[{"role": "web", "single_use": False}])
    other.launcher.run_once()
    assert set(mint_bodies(other)[0]) == {"metadata", "ttl"}


def test_the_job_accessor_is_tracked_and_the_file_is_private(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert deliver(rig) == "accessor"
    assert tracked(rig) == ["accessor-wrap-1"]
    assert (
        stat.S_IMODE((rig.config.role_dir("import") / "secret_id").stat().st_mode)
        == 0o400
    )


def test_a_second_job_mint_is_refused_while_an_accessor_is_still_tracked(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    with pytest.raises(Refusal) as refused_mint:
        deliver(rig)
    assert refused_mint.value.code == "secret_delivery_failed"
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
    assert (
        not (rig.config.role_dir("import") / "secret_id").exists()
        and tracked(rig) == []
    )


# -- S3: purge through the purge unit; the agent never touches secrets/ ------
def test_purge_removes_the_agents_secret_id_starts_the_purge_unit_and_destroys_the_accessor(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    assert rig.launcher.purge_job_role("import") == 0
    assert not (rig.config.role_dir("import") / "secret_id").exists()
    assert started(rig, "dotmac-job-purge@erp-import.service")
    assert tracked(rig) == []
    assert any(r[1].endswith("/secret-id-accessor/destroy") for r in rig.bao.requests)
    # the renders are the purge unit's to remove (as the container uid), never the agent's
    assert sorted(p.name for p in secrets_dir(rig).iterdir()) == sorted(FILES)


def test_purge_with_openbao_down_keeps_the_accessor_tracked(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    rig.bao.fail_destroy = True
    assert rig.launcher.purge_job_role("import") == 1
    assert tracked(rig) == ["accessor-wrap-1"]


def test_a_failed_purge_unit_still_destroys_the_accessor_then_reports(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    rig.systemctl.script = {
        ("dotmac-job-purge@erp-import.service",): CommandResult(1, b""),
        **rig.systemctl.script,
    }
    with pytest.raises(Refusal) as failed:
        rig.launcher.purge_job_role("import")
    assert failed.value.code == "job_files_invalid"
    assert tracked(rig) == []  # the accessor was destroyed regardless


def test_agent_start_purges_a_stale_job_secret_and_accessor(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)  # then the agent "crashes": the file and tracking survive
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"
    assert (
        not (rig.config.role_dir("import") / "secret_id").exists()
        and tracked(rig) == []
    )
    assert started(rig, "dotmac-job-purge@erp-import.service")


def test_agent_start_with_openbao_unreachable_keeps_tracking_and_does_not_crash(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    deliver(rig)
    rig.bao.fail_destroy = True
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"
    assert tracked(rig) == ["accessor-wrap-1"]


def test_the_agent_never_lists_or_deletes_inside_secrets(tmp_path: Path) -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent/job_delivery.py"
    ).read_text()
    assert (
        "scandir" not in src
        and "iterdir" not in src
        and "listdir" not in src
        and "glob(" not in src
    )


# -- A8: never sealed -------------------------------------------------------
def test_sealing_refuses_a_job_role_and_the_job_only_names() -> None:
    jobs = (JobDefinition("import", "import", (), ()),)
    for role, names in (
        ("import", ()),
        ("app", ("LEGACY_DATABASE_URL",)),
        ("app", ("MIGRATION_DATABASE_URL", "X")),
    ):
        with pytest.raises(Refusal) as refused_seal:
            refuse_sealing(role, names, jobs)
        assert refused_seal.value.code == "job_role_refused"
    refuse_sealing("app", ("DATABASE_URL",), jobs)


KIT = Path(__file__).resolve().parents[2] / "packages/dotmac-deploy-agent/deploy"


def test_the_kit_installs_no_sealed_copy_instance_for_a_job_role() -> None:
    names = [p.name.lower() for p in KIT.rglob("*")]
    assert not any("lkg" in n or "seal" in n or "restore" in n for n in names)


def test_the_job_kit_is_the_narrow_widening_and_the_purge_unit_is_static() -> None:
    tmpfiles = (KIT / "enrolment/tmpfiles.d/dotmac-job-role.conf.template").read_text()
    assert "/@ROLE@/secrets 0710 @CONTAINER_USER@ dotmac-deploy" in tmpfiles
    assert "W pending Michael" in tmpfiles
    serving = (KIT / "enrolment/tmpfiles.d/dotmac-role.conf.template").read_text()
    assert (
        "/secrets 0700 @CONTAINER_USER@ @CONTAINER_USER@" in serving
    )  # serving roles unchanged
    purge = (KIT / "enrolment/systemd/dotmac-job-purge@.service").read_text()
    assert (
        "[Install]" not in purge and "WantedBy" not in purge and "Type=oneshot" in purge
    )
    assert "-mindepth 1 -maxdepth 1 -type f -delete" in purge
    polkit = (
        KIT / "enrolment/polkit/50-dotmac-deploy-agent.rules.template"
    ).read_text()
    code = "\n".join(
        line for line in polkit.splitlines() if not line.lstrip().startswith("//")
    )
    assert "@ENROLLED_JOB_UNITS@" in code and 'action.lookup("verb") == "start"' in code


# -- S2 / A9: the job env comes only from the signed params -----------------
def test_the_job_params_file_holds_the_signed_values_and_never_params_env(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    seen: dict[str, bytes] = {}

    def effect(spec) -> None:  # type: ignore[no-untyped-def]
        seen["job"] = spec.job_params_env.read_bytes()
        seen["mode"] = oct(spec.job_params_env.stat().st_mode & 0o777).encode()

    rig.runner.effect = effect
    assert refused(rig) == ("completed", None)
    assert seen == {"job": b"ERP_IMPORT_APPROVED=true\n", "mode": b"0o400"}
    assert b"ERP_IMPORT_APPROVED" not in (rig.runner.seen_params or b"")
    assert list(rig.config.deploy_run_dir.iterdir()) == []


def test_a_signed_value_that_does_not_match_the_signed_digest_is_refused_before_any_mint(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(params={"ERP_IMPORT_APPROVED": "false"})
    )
    assert refused(rig) == ("refused", "params_digest_mismatch")
    assert rig.bao.paths == []


def test_a_param_the_job_scopes_sent_by_cp_is_refused(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(params=[*BASE_ROWS, GATE_ROW])
    assert refused(rig) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


@pytest.mark.parametrize("name", ["MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"])
def test_a_job_credential_is_never_accepted_as_a_plain_param(
    tmp_path: Path, name: str
) -> None:
    rig = job_rig(tmp_path)
    line = f"{name}=host\n".encode()
    rig.cp.materials = run_job_materials(
        dispatched(params={name: "host"}, params_env_digest=digest(line))
    )
    assert refused(rig) == ("refused", "job_role_refused")
    assert rig.bao.paths == []


# -- A13: the pinned naming also covers job-only plans ----------------------
@pytest.mark.parametrize(
    "override", [{"secrets_namespace": "other"}, {"secrets_environment": "prod"}]
)
def test_a_job_only_plan_with_a_different_naming_is_refused(
    tmp_path: Path, override: dict[str, str]
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(**override)
    assert refused(rig) == ("refused", "secrets_naming_mismatch")


def test_a_job_only_plan_without_naming_is_refused(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(secrets_environment=None)
    assert refused(rig) == ("refused", "materials_invalid")


# -- run_job -----------------------------------------------------------------
def test_a_run_job_runs_with_a_metadata_only_mint_and_a_job_receipt(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert [set(b) for b in mint_bodies(rig)] == [{"metadata"}]
    spec = rig.runner.specs[0]
    assert spec.job_name == "import" and spec.job_secrets_dir == secrets_dir(rig)
    assert spec.job_secret_files == FILES
    assert spec.job_argv_digest == digest(
        b'["python","-m","erp_v2.importer","execute"]'
    )
    assert last(rig)["kind"] == "job"  # never a deploy observation (S7)
    done = receipt(rig)
    assert done["name"] == "import" and done["exit_code"] == 0
    assert (
        done["accessor_prefix"] == "accessor" and done["accessors_still_tracked"] == 0
    )
    assert done["files"] == list(FILES)
    assert {"started_at", "finished_at", "window_id"} <= set(done)


def test_a_run_job_transfers_nothing_and_checks_the_image_by_digest(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert rig.transfer.calls == []
    assert any(
        c[1:3] == ("image", "inspect") and c[-1] == APP_IMAGE
        for c in rig.commands.calls
    )


def test_a_missing_image_refuses_before_any_lease_mint_or_file(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[("image", "inspect")] = CommandResult(1, b"")
    assert refused(rig) == ("refused", "job_image_absent")
    assert rig.bao.paths == [] and rig.cp.lease_log == [] and rig.transfer.calls == []


def test_an_image_present_under_another_digest_is_absent(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["127.0.0.1:5000/org/app@sha256:' + b"9" * 64 + b'"]'
    )
    assert refused(rig) == ("refused", "job_image_absent")


def test_image_source_must_match_the_operation(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        images=base_images()
    )  # `transfer` on a run_job
    assert refused(rig) == ("refused", "materials_invalid")
    rig.cp.materials = make_materials(
        images=present(), dispatch_id="dispatch-2"
    )  # `present` on a deploy
    assert refused(rig) == ("refused", "materials_invalid")


def test_files_and_accessor_are_purged_after_a_failing_run_and_before_the_receipt(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.runner.exit_code = 3
    assert refused(rig) == ("failed", None)
    assert (
        not (rig.config.role_dir("import") / "secret_id").exists()
        and tracked(rig) == []
    )
    assert (
        started(rig, "dotmac-job-purge@erp-import.service")
        and receipt(rig)["exit_code"] == 3
    )
    assert any(r[1].endswith("/secret-id-accessor/destroy") for r in rig.bao.requests)


def test_a_timed_out_job_is_purged_too(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.runner.timed_out = True
    assert refused(rig) == ("failed", "runner_timeout")
    assert tracked(rig) == [] and started(rig, "dotmac-job-purge@erp-import.service")


def test_a_crash_inside_the_run_is_purged(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)

    def crash(spec) -> None:  # type: ignore[no-untyped-def]
        raise Refusal("runner_start_failed")

    rig.runner.effect = crash
    assert refused(rig) == ("failed", "runner_start_failed")
    assert tracked(rig) == []


def test_with_openbao_down_the_job_fails_closed_before_it_runs(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.launcher.bao_transport = None
    assert refused(rig) == ("refused", "secret_delivery_failed")
    assert rig.runner.specs == []


def test_a_job_run_refuses_a_second_dispatch_of_the_same_id(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig)[0] == "completed"
    rig.cp.materials = run_job_materials()
    assert refused(rig) == ("refused", "dispatch_replayed")
    assert len(mint_bodies(rig)) == 1


def test_a_second_role_job_on_the_host_is_refused_while_the_lock_is_held(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    held = rig.launcher._lock_role_job()
    try:
        assert refused(rig) == ("refused", "lease_refused")
    finally:
        os.close(held)
    assert rig.runner.specs == [] and mint_bodies(rig) == []


@pytest.mark.parametrize(
    ("job", "extra", "expected"),
    [
        (
            dispatched(window={"window_id": "w1", "closes_at": "2027-01-06T12:00:00Z"}),
            {},
            "job_window_closed",
        ),
        (
            dispatched(window={"window_id": "w1", "closes_at": "2027-01-06T11:00:00Z"}),
            {},
            "job_window_closed",
        ),
        (dispatched(timeout_seconds=3481), {}, "materials_invalid"),
        (
            dispatched(timeout_seconds=600),
            {"lease_ttl_seconds": 719},
            "lease_ttl_invalid",
        ),
        (
            dispatched(timeout_seconds=1800),
            {"lease_ttl_seconds": 1919},
            "lease_ttl_invalid",
        ),
        (dispatched(role=None, single_use=None), {}, "materials_invalid"),
        (
            dispatched(role="other"),
            {},
            "materials_invalid",
        ),  # not the definition's role
        (dispatched(name="unknown"), {}, "materials_invalid"),  # not a plan definition
    ],
)
def test_run_job_is_refused_before_anything_when_the_plan_does_not_fit(
    tmp_path: Path, job: dict[str, object], extra: dict[str, object], expected: str
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(job, **extra)
    assert refused(rig) == ("refused", expected)
    assert rig.bao.paths == [] and rig.runner.specs == [] and rig.cp.lease_log == []


def test_a_lease_longer_than_900_is_accepted_when_the_job_fits_inside_it(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=1800), lease_ttl_seconds=1920
    )
    assert refused(rig) == ("completed", None)
    assert rig.cp.lease_log[0][1]["ttl_seconds"] == 1920  # type: ignore[index]


def test_the_lease_fit_boundary_is_accepted(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=600), lease_ttl_seconds=720
    )
    assert refused(rig) == ("completed", None)


def test_a_job_role_the_host_has_not_enrolled_is_refused_by_default(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path, job_roles=())
    assert refused(rig) == ("refused", "job_role_refused")
    assert rig.bao.paths == [] and rig.runner.specs == []


def test_operation_and_dispatched_job_must_agree(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(statement={"operation": "deploy"})
    assert refused(rig) == ("refused", "materials_invalid")
    rig.cp.materials = make_materials(
        statement={"operation": "run_job"}, dispatch_id="dispatch-2"
    )
    assert refused(rig) == ("refused", "materials_invalid")


def test_a_run_job_plan_that_also_mints_serving_roles_is_refused(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(roles=[{"role": "web", "single_use": False}])
    assert refused(rig) == ("refused", "materials_invalid")


def test_a_refused_job_mint_fails_that_job_only_and_touches_no_serving_role(
    tmp_path: Path,
) -> None:
    from tests.unit.test_deploy_agent_delivery import WEB, secret_id_file, state

    rig = job_rig(tmp_path)
    prepare_role(rig, "web")
    rig.cp.materials = make_materials(roles=WEB)
    assert rig.launcher.run_once().outcome == "completed"
    served_file = secret_id_file(rig, "web").read_bytes()
    served_state = state(rig, "web")
    rig.bao.requests.clear()
    rig.bao.deny_mint = "erp-staging-import"  # OpenBao 403s the JOB mint
    rig.cp.materials = run_job_materials(dispatch_id="dispatch-2")
    assert refused(rig) == ("refused", "job_mint_refused")  # not a delivery fault
    assert len(rig.runner.specs) == 1  # only the serving run ever started
    assert (
        secret_id_file(rig, "web").read_bytes() == served_file
        and state(rig, "web") == served_state
    )
    assert not any("-web" in r[1] for r in rig.bao.requests)


# -- S3: render verification without listing secrets/ -----------------------
@pytest.mark.parametrize(
    "show",
    [
        b"Result=exit-code\nExecMainStatus=0\n",
        b"Result=success\nExecMainStatus=1\n",
        b"",
    ],
)
def test_the_render_unit_must_report_success_and_status_zero(
    tmp_path: Path, show: bytes
) -> None:
    rig = job_rig(tmp_path)
    rig.systemctl.script = {
        ("--property=Result,ExecMainStatus",): CommandResult(0, show),
        **rig.systemctl.script,
    }
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.runner.specs == [] and tracked(rig) == []


def test_a_render_unit_that_fails_to_start_stops_the_job(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.systemctl.script = {
        ("openbao-agent@erp-import.service",): CommandResult(1, b""),
        **rig.systemctl.script,
    }
    assert refused(rig) == ("refused", "job_files_invalid")


@pytest.mark.parametrize("mode", [0o440, 0o644, 0o600])
def test_a_declared_file_with_a_wrong_mode_stops_the_job(
    tmp_path: Path, mode: int
) -> None:
    rig = job_rig(tmp_path)
    (secrets_dir(rig) / "legacy_database_url").chmod(mode)
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.runner.specs == []


def test_a_missing_or_empty_declared_file_stops_the_job(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    empty = secrets_dir(rig) / "legacy_database_url"
    empty.chmod(0o600)
    empty.write_text("")
    empty.chmod(0o400)
    assert refused(rig) == ("refused", "job_files_invalid")
    (tmp_path / "b").mkdir()
    rig = job_rig(tmp_path / "b", render=False)
    assert refused(rig) == ("refused", "job_files_invalid")


def test_a_declared_file_owned_by_another_uid_stops_the_job(tmp_path: Path) -> None:
    rig = job_rig(tmp_path, job_secrets_uid=os.geteuid() + 1)
    assert refused(rig) == ("refused", "job_files_invalid")


def test_an_undeclared_rendered_file_is_left_alone_and_never_reported(
    tmp_path: Path,
) -> None:
    """Narrowing is Foundation's per-file mounts: the agent neither lists nor deletes."""
    rig = job_rig(tmp_path)
    (secrets_dir(rig) / "other_secret").write_text("x")
    rig.cp.materials = run_job_materials(
        dispatched(materials=["MIGRATION_DATABASE_URL"]),
        jobs=[definition(materials=["MIGRATION_DATABASE_URL"])],
    )
    assert refused(rig) == ("completed", None)
    assert rig.runner.specs[0].job_secret_files == ("migration_database_url",)
    assert (
        secrets_dir(rig) / "other_secret"
    ).exists() and "other_secret" not in json.dumps(receipt(rig))


# -- Foundation's operator-job entry does not exist yet ---------------------
def test_the_real_runner_refuses_a_job_with_no_foundation_entry(tmp_path: Path) -> None:
    from dotmac_deploy_agent.runner import SubprocessRunner

    rig = job_rig(tmp_path)
    rig.launcher.runner = SubprocessRunner(("/usr/bin/true",), 60)
    assert refused(rig) == ("refused", "foundation_job_entry_unavailable")
    assert (
        rig.bao.paths == []
        and rig.cp.lease_log == []
        and not rig.launcher.executed.seen("dispatch-1")
    )


def test_the_job_environment_names_for_foundation() -> None:
    from dotmac_deploy_agent.runner import RunSpec, runner_environment

    env = runner_environment(
        RunSpec(
            "d", "erp/staging/h", "erp", "sha256:" + "a" * 64, Path("/p"), None,
            job_name="import", job_params_env=Path("/j"), job_secrets_dir=Path("/s"),
            job_argv_digest="sha256:" + "b" * 64, job_secret_files=("a", "b"),
        )
    )  # fmt: skip
    assert {k for k in env if k.startswith("DOTMAC_DEPLOY_JOB")} == {
        "DOTMAC_DEPLOY_JOB",
        "DOTMAC_DEPLOY_JOB_PARAMS_ENV",
        "DOTMAC_DEPLOY_JOB_SECRETS_DIR",
        "DOTMAC_DEPLOY_JOB_ARGV_DIGEST",
        "DOTMAC_DEPLOY_JOB_SECRET_FILES",
    }
    assert env["DOTMAC_DEPLOY_JOB_SECRET_FILES"] == "a,b"


def test_job_roles_config_is_validated(tmp_path: Path) -> None:
    from tests.unit.deploy_agent_support import Keys, make_config

    for bad in (("Import",), ("a", "a"), ("../x",)):
        with pytest.raises(Refusal):
            make_config(tmp_path, Keys(), job_roles=bad)
    assert make_config(tmp_path, Keys()).job_roles == ()


def test_a_dispatched_job_is_parsed_only_from_a_run_job_statement() -> None:
    binding = {"dispatched_job": dispatched()}
    assert plan_dispatched_job(binding, "run_job") is not None
    with pytest.raises(Refusal):
        plan_dispatched_job(binding, "deploy")
    assert plan_dispatched_job({"dispatched_job": None}, "deploy") is None


# -- N-1: observation envelope switch (v2 format not published yet) ---------
def test_target_api_v1_posts_the_observation_as_before(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert last(rig)["schema"] == "dotmac.deploy_observation/1"
    assert rig.config.target_api_version == 1


def test_target_api_v2_posts_a_pop_signed_envelope_for_a_job_refusal(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent import observation_v2 as o2

    rig = job_rig(tmp_path, target_api_version=2)
    rig.cp.materials = make_materials(
        statement={"operation": "run_job"}
    )  # no dispatched job: refused
    assert rig.launcher.run_once().outcome == "refused"
    envelope = rig.cp.observations[-1]
    assert set(envelope) == {"signature", "statement"}
    statement = envelope["statement"]
    assert (
        statement["kind"] == "job"
        and statement["outcome"] == "refused"
        and statement["key_id"] == rig.config.credential_id
    )
    assert (
        statement["reason"] == "materials_invalid" and statement["schema"] == o2.SCHEMA
    )
    key = rig.keys.agent.public_key()
    key.verify(
        wire.decode_b64url(envelope["signature"]) or b"", o2.signed_bytes(statement)
    )
    assert rig.launcher.outbox.pending() == []  # acknowledged


def test_target_api_v2_posts_a_completed_job_as_kind_job_succeeded(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path, target_api_version=2)
    assert refused(rig) == ("completed", None)
    statement = rig.cp.observations[-1]["statement"]
    assert (statement["kind"], statement["outcome"], statement["reason"]) == (
        "job",
        "succeeded",
        "",
    )
    assert statement["details"]["job"]["name"] == "import"


def test_a_record_that_cannot_become_a_statement_is_dead_lettered_not_blocking(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path, target_api_version=2)
    rig.launcher.outbox.seal(
        {
            "schema": "x",
            "kind": "other",
            "dispatch_id": "d9",
            "target_ref": rig.config.target_ref,
            "outcome": "refused",
            "reason": None,
            "details": {},
            "agent_version": "0",
            "observed_at": "2027-01-06T12:00:00Z",
        }
    )
    rig.launcher.flush_outbox()
    assert rig.launcher.outbox.pending() == [] and rig.cp.observations == []


def test_target_api_version_is_validated(tmp_path: Path) -> None:
    from tests.unit.deploy_agent_support import Keys, make_config

    with pytest.raises(Refusal):
        make_config(tmp_path, Keys(), target_api_version=3)


def test_a_job_plan_whose_lease_max_differs_from_its_ttl_is_refused(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(lease_ttl_seconds=720, lease_max_seconds=900)
    assert refused(rig) == ("refused", "lease_ttl_invalid")
    assert rig.bao.paths == [] and rig.cp.lease_log == []


def test_a_serving_plan_with_a_split_lease_is_accepted_and_one_over_900_is_not(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(lease_ttl_seconds=600, lease_max_seconds=3120)
    assert rig.launcher.run_once().outcome == "completed"
    rig.cp.materials = make_materials(
        lease_ttl_seconds=901, lease_max_seconds=3120, dispatch_id="dispatch-2"
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_ttl_invalid")


# -- P-SEC rev5 RQ-1 .. RQ-5 ---------------------------------------------------
def test_rq1_a_job_lease_is_never_renewed_and_is_released(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert ("POST", "lease/renew") not in rig.cp.calls
    assert [e for e, _ in rig.cp.lease_log][
        -1
    ] == "lease/release"  # never EXPIRED_UNRELEASED


def test_rq1_the_import_default_fits_under_the_2700_foundation_bound(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)  # foundation_timeout_seconds default 2700
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=2400), lease_ttl_seconds=2520
    )
    assert refused(rig) == ("completed", None)


def test_rq1_a_job_the_foundation_bound_cannot_cover_is_refused_before_anything(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(timeout_seconds=2800), lease_ttl_seconds=2920
    )
    assert refused(rig) == ("refused", "lease_ttl_invalid")
    assert rig.bao.paths == [] and rig.cp.lease_log == []


def input_file(tmp_path: Path, content: bytes = b"manifest") -> tuple[Path, str]:
    import hashlib

    path = tmp_path / "work-manifest.json"
    path.write_bytes(content)
    return path, "sha256:" + hashlib.sha256(content).hexdigest()


def test_rq2_an_input_whose_sha256_matches_the_plan_is_accepted(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    path, digest_ = input_file(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(input_digests={str(path): digest_}))
    assert refused(rig) == ("completed", None)


def test_rq2_an_input_digest_mismatch_is_refused_before_any_lease_or_mint(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    path, _ = input_file(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(input_digests={str(path): "sha256:" + "0" * 64})
    )
    assert refused(rig) == ("refused", "job_input_mismatch")
    assert rig.bao.paths == [] and rig.cp.lease_log == [] and rig.runner.specs == []


def test_rq2_a_missing_or_symlinked_input_is_a_mismatch(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    path, digest_ = input_file(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    for target in (str(tmp_path / "absent.json"), str(link)):
        rig.cp.materials = run_job_materials(
            dispatched(input_digests={target: digest_}),
            dispatch_id=f"dispatch-{len(target)}",
        )
        assert refused(rig) == ("refused", "job_input_mismatch")


def test_rq2_a_path_under_the_secrets_root_is_refused(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    out = f"{rig.config.secrets_root}/erp/import/out.json"
    rig.cp.materials = run_job_materials(
        dispatched(
            argv=["python", "-m", "erp_v2.importer", "execute", "--receipt", out]
        )
    )
    assert refused(rig) == ("refused", "job_input_mismatch")
    assert rig.bao.paths == []


def gate(value: str) -> dict[str, object]:
    return {
        "params": {"ERP_IMPORT_APPROVED": value},
        "params_env_digest": digest(f"ERP_IMPORT_APPROVED={value}\n".encode()),
    }


@pytest.mark.parametrize(
    ("extra", "declared", "expected"),
    [
        (gate("1"), True, None),  # a gate "1" is apply-class, labelled so
        (
            gate("1"),
            False,
            "job_apply_class_mismatch",
        ),  # a "verify" label with a gate "1"
        (gate("0"), False, None),  # defaults "0": not apply
        (
            gate("0"),
            True,
            "job_apply_class_mismatch",
        ),  # labelled apply, nothing applies
        (
            {
                **gate("0"),
                "argv": ["python", "-m", "erp_v2.importer", "execute", "--apply"],
            },
            True,
            None,
        ),  # an apply-mode argv token with gates "0" is still apply
        (
            {
                **gate("0"),
                "argv": ["python", "-m", "erp_v2.importer", "execute", "--apply"],
            },
            False,
            "job_apply_class_mismatch",
        ),
    ],
)
def test_rq3_apply_class_is_rederived_from_the_signed_plan(
    tmp_path: Path, extra: dict[str, object], declared: bool, expected: str | None
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(apply_class=declared, **extra))
    result = refused(rig)
    if expected is None:
        assert result == ("completed", None)
    else:
        assert result == ("refused", expected)
        assert rig.bao.paths == []


def test_rq3_a_plan_without_a_stated_class_is_not_refused_until_the_contract_carries_it(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)


def test_rq4_an_owner_only_job_renders_exactly_the_owner_file(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(materials=["MIGRATION_DATABASE_URL"]),
        jobs=[definition(materials=["MIGRATION_DATABASE_URL"])],
    )
    assert refused(rig) == ("completed", None)
    assert started(rig, "openbao-agent@erp-import-owner.service")
    assert not started(rig, "openbao-agent@erp-import.service")
    assert receipt(rig)["files"] == ["migration_database_url"]


def test_rq4_an_owner_plus_legacy_job_uses_the_two_file_config(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert started(rig, "openbao-agent@erp-import.service")
    assert receipt(rig)["files"] == list(FILES)


def test_rq4_a_materials_set_that_matches_no_config_is_refused_with_nothing_minted(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(materials=["LEGACY_DATABASE_URL"]),
        jobs=[definition(materials=["LEGACY_DATABASE_URL"])],
    )
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.bao.paths == [] and rig.cp.lease_log == [] and rig.runner.specs == []


def test_rq4_a_config_that_renders_a_meta_file_is_refused(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    path = rig.config.openbao_config_dir / "erp-import.hcl"
    path.write_text(authored(*FILES, "meta/render"))
    path.chmod(0o644)
    assert refused(rig) == ("refused", "openbao_config_invalid")
    assert rig.bao.paths == []


def test_rq5_a_delivery_login_403_is_still_a_delivery_scope_refusal(
    tmp_path: Path,
) -> None:
    """Control case: the identity itself being refused is NOT the job-mint class."""
    rig = job_rig(tmp_path)
    rig.bao.deny_login = True
    assert refused(rig) == ("refused", "secret_delivery_failed")
    assert rig.runner.specs == [] and mint_bodies(rig) == []


def test_rq5_a_serving_delivery_login_403_is_the_same_delivery_scope_refusal(
    tmp_path: Path,
) -> None:
    from tests.unit.test_deploy_agent_delivery import WEB

    rig = make_rig(tmp_path)
    prepare_role(rig, "web")
    rig.bao.deny_login = True
    rig.cp.materials = make_materials(roles=WEB)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "secret_delivery_failed")
