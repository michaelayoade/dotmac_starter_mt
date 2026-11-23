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
BOTH = ("LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL")
OWNER = ("MIGRATION_DATABASE_URL",)
INVOCATION = "a1" * 16
RENDER_OK = f"Result=success\nExecMainStatus=0\nInvocationID={INVOCATION}\n".encode()


def loaded(materials: tuple[str, ...]) -> bytes:
    names = ",".join(sorted(m.lower() for m in materials))
    return (
        f"LoadState=loaded\nDescription=Render job secrets materials={names}\n".encode()
    )


def stem_of(rig: Rig, materials: tuple[str, ...]) -> str:
    from dotmac_deploy_agent.openbao_config import job_unit_stem

    return job_unit_stem(rig.config, "import", materials)


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
        "inputs": [],
        "output_dir": "/job/out",
        "apply_class": False,
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
    for materials in (BOTH, OWNER):  # one authored config per material set
        path = rig.config.openbao_config_dir / f"{stem_of(rig, materials)}.hcl"
        path.write_text(authored(*(m.lower() for m in materials)))
        path.chmod(0o644)
    secrets = rig.config.role_dir("import") / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    if render:  # what the static one-shot openbao-agent unit renders
        for name in FILES:
            (secrets / name).write_text("rendered")
            (secrets / name).chmod(0o400)
    rig.systemctl.script = {
        ("--property=Result,ExecMainStatus,InvocationID",): CommandResult(0, RENDER_OK),
        **{
            (
                "--property=LoadState,Description",
                f"openbao-agent@{stem_of(rig, m)}.service",
            ): CommandResult(0, loaded(m))
            for m in (BOTH, OWNER)
        },
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
    assert started(rig, "dotmac-secrets-purge@erp-import.service")
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
        ("dotmac-secrets-purge@erp-import.service",): CommandResult(1, b""),
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
    assert started(rig, "dotmac-secrets-purge@erp-import.service")


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
    assert "lstat" not in src and ".stat(" not in src and "S_ISREG" not in src  # N2


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


def test_the_job_kit_keeps_secrets_0700_and_verifies_inside_the_render_unit() -> None:
    tmpfiles = (KIT / "enrolment/tmpfiles.d/dotmac-job-role.conf.template").read_text()
    assert "/@ROLE@/secrets 0700 @CONTAINER_USER@ @CONTAINER_USER@" in tmpfiles
    assert "0710" not in tmpfiles and "W pending" not in tmpfiles  # no widening (N2)
    purge = (KIT / "enrolment/systemd/dotmac-secrets-purge@.service").read_text()
    assert (
        "[Install]" not in purge and "WantedBy" not in purge and "Type=oneshot" in purge
    )
    assert "ExecStart=/usr/bin/false" in purge  # an unenrolled role purges nothing
    dropin = (KIT / "enrolment/systemd/secrets-purge.conf.template").read_text()
    assert "-maxdepth 1 ! -type d -delete" in dropin
    assert "User=@OWNER_USER@" in dropin  # the owning uid (F26)
    assert not (KIT / "enrolment/systemd/dotmac-job-purge@.service").exists()
    render = (KIT / "enrolment/systemd/job-render.conf.template").read_text()
    assert "ExecStartPost=/usr/local/libexec/dotmac/verify-job-render " in render
    assert "Type=oneshot" in render
    polkit = (
        KIT / "enrolment/polkit/50-dotmac-deploy-agent.rules.template"
    ).read_text()
    code = "\n".join(
        line for line in polkit.splitlines() if not line.lstrip().startswith("//")
    )
    assert "@ENROLLED_JOB_UNITS@" in code and 'action.lookup("verb") == "start"' in code


def run_verify(directory: Path, *names: str) -> int:
    import subprocess

    script = KIT / "enrolment/libexec/verify-job-render.sh"
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(script), str(directory), *names],
        check=False,
    ).returncode


def test_the_in_unit_verifier_accepts_exactly_the_declared_set(tmp_path: Path) -> None:
    for name in ("a_url", "b_url"):
        (tmp_path / name).write_text("x")
        (tmp_path / name).chmod(0o400)
    assert run_verify(tmp_path, "a_url", "b_url") == 0
    assert (
        run_verify(tmp_path, "a_url") != 0
    )  # an undeclared file exists (owner-only job)
    assert (
        run_verify(tmp_path, "a_url", "b_url", "c_url") != 0
    )  # a declared file is missing


def test_the_in_unit_verifier_refuses_wrong_mode_empty_and_symlink(
    tmp_path: Path,
) -> None:
    (tmp_path / "a_url").write_text("x")
    (tmp_path / "a_url").chmod(0o440)
    assert run_verify(tmp_path, "a_url") != 0
    (tmp_path / "a_url").chmod(0o400)
    assert run_verify(tmp_path, "a_url") == 0
    (tmp_path / "a_url").chmod(0o600)
    (tmp_path / "a_url").write_text("")
    (tmp_path / "a_url").chmod(0o400)
    assert run_verify(tmp_path, "a_url") != 0  # empty
    (tmp_path / "a_url").unlink()
    (tmp_path / "real").write_text("x")
    (tmp_path / "real").chmod(0o400)
    (tmp_path / "a_url").symlink_to(tmp_path / "real")
    assert run_verify(tmp_path, "a_url") != 0


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
        started(rig, "dotmac-secrets-purge@erp-import.service")
        and receipt(rig)["exit_code"] == 3
    )
    assert any(r[1].endswith("/secret-id-accessor/destroy") for r in rig.bao.requests)


def test_a_timed_out_job_is_purged_too(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.runner.timed_out = True
    assert refused(rig) == ("failed", "runner_timeout")
    assert tracked(rig) == [] and started(
        rig, "dotmac-secrets-purge@erp-import.service"
    )


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
        b"Result=exit-code\nExecMainStatus=0\nInvocationID=" + b"a1" * 16 + b"\n",
        b"Result=success\nExecMainStatus=1\nInvocationID=" + b"a1" * 16 + b"\n",
        b"Result=success\nExecMainStatus=0\n",  # no InvocationID
        b"Result=success\nExecMainStatus=0\nInvocationID=short\n",
        b"",
    ],
)
def test_the_render_unit_must_report_success_and_status_zero(
    tmp_path: Path, show: bytes
) -> None:
    rig = job_rig(tmp_path)
    # assignment, not `{new, **old}`: the rig already has this key and the old one
    # would win the merge
    rig.systemctl.script[("--property=Result,ExecMainStatus,InvocationID",)] = (
        CommandResult(0, show)
    )
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.runner.specs == [] and tracked(rig) == []


def test_a_render_unit_that_fails_to_start_stops_the_job(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.systemctl.script = {
        ("start", f"openbao-agent@{stem_of(rig, BOTH)}.service"): CommandResult(1, b""),
        **rig.systemctl.script,
    }
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
            job_deadline=1_800_000_000, job_render_invocation="a1" * 16,
        )
    )  # fmt: skip
    assert {k for k in env if k.startswith("DOTMAC_DEPLOY_JOB")} == {
        "DOTMAC_DEPLOY_JOB_DEADLINE",
        "DOTMAC_DEPLOY_JOB_RENDER_INVOCATION",
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
        wire.decode_b64url(envelope["signature"], length=64) or b"",
        o2.signed_bytes(statement),
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


def work_rig(tmp_path: Path, content: bytes = b"manifest") -> tuple[Rig, str]:
    """A rig whose `job_work_root` holds the job's input at `<root>/import/in/`."""
    import hashlib

    root = tmp_path / "work"
    inputs = root / "import" / "in"
    inputs.mkdir(parents=True)
    (inputs / "manifest.json").write_bytes(content)
    rig = job_rig(tmp_path, job_work_root=root)
    return rig, "sha256:" + hashlib.sha256(content).hexdigest()


BASE_ARGV = ["python", "-m", "erp_v2.importer", "execute"]


def with_input(digest_: str, **kw: object) -> dict[str, object]:
    """`dispatched()` overrides: one input, its flag and path present in argv."""
    entry = one_input(digest_, **kw)
    return {
        "inputs": [entry],
        "argv": [*BASE_ARGV, str(entry["flag"]), str(entry["path"])],
    }


def one_input(digest_: str, **kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "flag": "--manifest",
        "path": "/job/in/manifest.json",
        "sha256": digest_,
    }
    row.update(kw)
    return row


def test_rq2_an_input_whose_sha256_matches_the_plan_is_accepted(tmp_path: Path) -> None:
    rig, digest_ = work_rig(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(**with_input(digest_)))
    assert refused(rig) == ("completed", None)


def test_rq2_an_input_digest_mismatch_is_refused_before_any_lease_or_mint(
    tmp_path: Path,
) -> None:
    rig, _ = work_rig(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(**with_input("sha256:" + "0" * 64)))
    assert refused(rig) == ("refused", "job_input_mismatch")
    assert rig.bao.paths == [] and rig.cp.lease_log == [] and rig.runner.specs == []


def test_rq2_a_missing_or_symlinked_input_is_a_mismatch(tmp_path: Path) -> None:
    rig, digest_ = work_rig(tmp_path)
    inputs = tmp_path / "work" / "import" / "in"
    (inputs / "manifest.json").unlink()
    rig.cp.materials = run_job_materials(dispatched(**with_input(digest_)))
    assert refused(rig) == ("refused", "job_input_mismatch")  # missing
    real = tmp_path / "real.json"
    real.write_bytes(b"manifest")
    (inputs / "manifest.json").symlink_to(real)
    rig.cp.materials = run_job_materials(
        dispatched(**with_input(digest_)), dispatch_id="dispatch-2"
    )
    assert refused(rig) == ("refused", "job_input_mismatch")  # a symlink


def test_rq2_inputs_without_a_work_root_are_refused(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)  # no job_work_root configured
    rig.cp.materials = run_job_materials(dispatched(**with_input("sha256:" + "0" * 64)))
    assert refused(rig) == ("refused", "job_input_mismatch")


@pytest.mark.parametrize(
    "inputs",
    [
        [{"flag": "--manifest", "path": "/job/in/manifest.json"}],  # no sha256
        [one_input("sha256:" + "0" * 64, path="/job/out/manifest.json")],  # not /job/in
        [one_input("sha256:" + "0" * 64, path="/job/in/../etc/passwd")],  # ..
        [one_input("sha256:" + "0" * 64, path="/var/lib/x/manifest.json")],
        [one_input("sha256:" + "0" * 64, flag="manifest")],  # not a flag
        [one_input("sha256:ABC")],  # not canonical
        [
            one_input("sha256:" + "0" * 64),
            one_input("sha256:" + "1" * 64),
        ],  # duplicate flag
        [
            one_input("sha256:" + "0" * 64, flag="--z", path="/job/in/z"),
            one_input("sha256:" + "0" * 64, flag="--a", path="/job/in/a"),
        ],  # not sorted by flag
        [one_input("sha256:" + "0" * 64, extra="x")],  # a stray key
    ],
)
def test_rq2_a_malformed_inputs_list_is_refused_at_parse(
    tmp_path: Path, inputs: list[dict[str, object]]
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(inputs=inputs))
    assert refused(rig) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


def test_rq2_an_input_path_in_argv_must_be_declared_with_a_digest_and_an_output_has_none(
    tmp_path: Path,
) -> None:
    rig, digest_ = work_rig(tmp_path)
    base = ["python", "-m", "erp_v2.importer", "execute"]
    undeclared = [*base, "--manifest", "/job/in/manifest.json"]  # an input with no sha
    rig.cp.materials = run_job_materials(dispatched(argv=undeclared))
    assert refused(rig) == ("refused", "materials_invalid")
    declared_output = [*base, "--receipt", "/job/out/receipt.json"]  # an output: no sha
    rig.cp.materials = run_job_materials(
        dispatched(argv=declared_output), dispatch_id="dispatch-2"
    )
    assert refused(rig) == ("completed", None)
    both = [
        *base,
        "--manifest",
        "/job/in/manifest.json",
        "--receipt",
        "/job/out/r.json",
    ]
    rig.cp.materials = run_job_materials(
        dispatched(argv=both, inputs=[one_input(digest_)]), dispatch_id="dispatch-3"
    )
    assert refused(rig) == ("completed", None)
    sha_on_output = one_input(digest_, flag="--receipt", path="/job/out/r.json")
    rig.cp.materials = run_job_materials(
        dispatched(argv=both, inputs=[sha_on_output]), dispatch_id="dispatch-4"
    )
    assert refused(rig) == ("refused", "materials_invalid")  # an output with a sha


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
    flagged = definition(args=[{"flag": "--apply", "type": "bool", "apply_mode": True}])
    rig.cp.materials = run_job_materials(
        dispatched(apply_class=declared, **extra), jobs=[flagged]
    )
    result = refused(rig)
    if expected is None:
        assert result == ("completed", None)
    else:
        assert result == ("refused", expected)
        assert rig.bao.paths == []


@pytest.mark.parametrize("missing", ["apply_class", "inputs", "output_dir"])
def test_the_v2_dispatched_job_keys_are_required(tmp_path: Path, missing: str) -> None:
    rig = job_rig(tmp_path)
    job = dispatched()
    del job[missing]
    rig.cp.materials = run_job_materials(job)
    assert refused(rig) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


@pytest.mark.parametrize(
    "bad",
    [
        {"apply_class": "yes"},
        {"apply_class": 1},
        {"output_dir": "/var/out"},
        {"output_dir": "/job/in"},
    ],
)
def test_a_non_bool_apply_class_or_a_foreign_output_dir_is_refused(
    tmp_path: Path, bad: dict[str, object]
) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(dispatched(**bad))
    assert refused(rig) == ("refused", "materials_invalid")


def test_rq3_apply_choices_and_an_applying_command_make_a_plan_apply_class(
    tmp_path: Path,
) -> None:
    choice = definition(
        args=[
            {
                "flag": "--mode",
                "type": "enum",
                "choices": ["plan", "apply"],
                "apply_choices": ["apply"],
            }
        ]
    )
    plan = ["python", "-m", "erp_v2.importer", "execute", "--mode", "plan"]
    apply = [*plan[:-1], "apply"]
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(argv=plan, apply_class=False), jobs=[choice]
    )
    assert refused(rig) == ("completed", None)  # the plan choice is not apply
    rig.cp.materials = run_job_materials(
        dispatched(argv=apply, apply_class=False),
        jobs=[choice],
        dispatch_id="dispatch-2",
    )
    assert refused(rig) == (
        "refused",
        "job_apply_class_mismatch",
    )  # the apply choice is
    rig.cp.materials = run_job_materials(
        dispatched(argv=apply, apply_class=True),
        jobs=[choice],
        dispatch_id="dispatch-3",
    )
    assert refused(rig) == ("completed", None)
    command = definition(apply_command=True)
    rig.cp.materials = run_job_materials(
        dispatched(apply_class=False), jobs=[command], dispatch_id="dispatch-4"
    )
    assert refused(rig) == (
        "refused",
        "job_apply_class_mismatch",
    )  # the command applies


def test_rq4_an_owner_only_job_renders_exactly_the_owner_file(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(materials=["MIGRATION_DATABASE_URL"]),
        jobs=[definition(materials=["MIGRATION_DATABASE_URL"])],
    )
    assert refused(rig) == ("completed", None)
    assert started(rig, f"openbao-agent@{stem_of(rig, OWNER)}.service")
    assert not started(rig, f"openbao-agent@{stem_of(rig, BOTH)}.service")
    assert receipt(rig)["files"] == ["migration_database_url"]


def test_rq4_an_owner_plus_legacy_job_uses_the_two_file_config(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert started(rig, f"openbao-agent@{stem_of(rig, BOTH)}.service")
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
    path = rig.config.openbao_config_dir / f"{stem_of(rig, BOTH)}.hcl"
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


# -- N5: setup plus teardown fits the 120 s lease margin ------------------------
def test_n5_job_setup_and_teardown_fit_the_lease_margin_with_injected_timings(
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    from dotmac_deploy_agent.jobs import LEASE_MARGIN

    from tests.unit.deploy_agent_support import NOW

    rig = job_rig(tmp_path)
    elapsed = [0.0]
    original_bao = rig.bao.request
    original_systemctl = rig.systemctl.run

    def timed_bao(method, path, headers, body):  # type: ignore[no-untyped-def]
        elapsed[0] += 4.0  # a typical OpenBao round trip
        return original_bao(method, path, headers, body)

    def timed_systemctl(argv, *, timeout):  # type: ignore[no-untyped-def]
        verb = argv[2] if len(argv) > 2 else ""
        elapsed[0] += {"start": 15.0, "show": 1.0}.get(verb, 1.0)
        assert timeout <= 30.0  # every systemd step carries a hard timeout
        return original_systemctl(argv, timeout=timeout)

    rig.bao.request = timed_bao  # type: ignore[method-assign]
    rig.systemctl.run = timed_systemctl  # type: ignore[method-assign]
    rig.launcher.bao_transport = rig.bao
    rig.launcher.commands = rig.systemctl
    rig.launcher.clock = lambda: NOW + timedelta(seconds=elapsed[0])
    assert refused(rig) == ("completed", None)  # the fake runner takes no time
    done = receipt(rig)
    from dotmac_deploy_agent.wire import parse_stamp

    spent = (
        parse_stamp(str(done["finished_at"])) - parse_stamp(str(done["started_at"]))
    ).total_seconds()
    assert 0 < spent <= LEASE_MARGIN


def test_n5_the_systemd_steps_carry_timeouts_that_leave_room_for_openbao() -> None:
    from dotmac_deploy_agent import job_delivery as jd
    from dotmac_deploy_agent.jobs import LEASE_MARGIN

    assert jd.SYSTEMD_BUDGET_SECONDS == (
        jd.LOADED_TIMEOUT
        + jd.RENDER_START_TIMEOUT
        + jd.SHOW_TIMEOUT
        + 2 * jd.PURGE_START_TIMEOUT
    )
    assert jd.SYSTEMD_BUDGET_SECONDS <= LEASE_MARGIN - 40  # about eight OpenBao calls


def test_n4_a_missing_image_is_refused_job_image_absent() -> None:
    """Pointer: `test_a_missing_image_refuses_before_any_lease_mint_or_file` (c7789470)."""
    names = {n for n in globals() if n.startswith("test_")}
    assert "test_a_missing_image_refuses_before_any_lease_mint_or_file" in names


# -- Foundation a2: render unit per material set, InvocationID ----------------
def test_the_job_unit_naming_is_one_constant_over_the_sorted_lowercase_set(
    tmp_path: Path,
) -> None:
    import hashlib

    from dotmac_deploy_agent.openbao_config import JOB_UNIT_NAMING, material_set_id

    rig = job_rig(tmp_path)
    expected = hashlib.sha256(
        b'["legacy_database_url","migration_database_url"]'
    ).hexdigest()[:12]
    assert material_set_id(BOTH) == material_set_id(tuple(reversed(BOTH))) == expected
    assert stem_of(rig, BOTH) == f"erp-import-{expected}" and JOB_UNIT_NAMING.endswith(
        "<S12>"
    )
    assert material_set_id(OWNER) != material_set_id(BOTH)


def test_the_render_invocation_id_is_recorded_and_handed_to_foundation(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.runner import runner_environment

    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    spec = rig.runner.specs[0]
    assert spec.job_render_invocation == INVOCATION
    assert runner_environment(spec)["DOTMAC_DEPLOY_JOB_RENDER_INVOCATION"] == INVOCATION
    assert receipt(rig)["render_invocation"] == INVOCATION
    assert spec.job_secret_files == tuple(sorted(spec.job_secret_files)) == FILES


def test_a_set_with_no_loaded_unit_is_refused_before_anything_is_minted(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.systemctl.script = {
        ("--property=LoadState,Description",): CommandResult(
            0, b"LoadState=not-found\n"
        ),
        **rig.systemctl.script,
    }
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.bao.paths == [] and rig.cp.lease_log == [] and rig.runner.specs == []


def test_the_agent_creates_no_host_output_directory(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    assert not any("workdir" in " ".join(c) for c in rig.systemctl.calls)
    assert sorted(p.name for p in rig.config.secrets_root.iterdir()) == ["erp"]


def test_rq3_apply_mode_flags_come_from_the_plans_closed_arg_list(
    tmp_path: Path,
) -> None:
    """A flag the definition marks `apply_mode` is apply-class even with gates 0."""
    flagged = definition(
        args=[{"flag": "--commit", "type": "bool", "apply_mode": True}]
    )
    argv = ["python", "-m", "erp_v2.importer", "execute", "--commit"]
    rig = job_rig(tmp_path)
    rig.cp.materials = run_job_materials(
        dispatched(argv=argv, apply_class=False), jobs=[flagged]
    )
    assert refused(rig) == ("refused", "job_apply_class_mismatch")
    rig.cp.materials = run_job_materials(
        dispatched(argv=argv, apply_class=True),
        jobs=[flagged],
        dispatch_id="dispatch-2",
    )
    assert refused(rig) == ("completed", None)


# -- P-SEC rev6 ----------------------------------------------------------------
def test_s12_shared_vectors() -> None:
    """The one S12 vector P-SEC, Control and Foundation also test against."""
    from dotmac_deploy_agent.openbao_config import material_set_id

    assert (
        material_set_id(("LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"))
        == "0dcc661ea25f"
    )
    assert (
        material_set_id(("MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"))
        == "0dcc661ea25f"
    )
    assert material_set_id(("MIGRATION_DATABASE_URL",)) == "c8cd75e18839"
    assert material_set_id(()) == "4f53cda18c2b"


def test_a_unit_whose_description_lists_another_set_is_a_collision_and_is_refused(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    key = (
        "--property=LoadState,Description",
        f"openbao-agent@{stem_of(rig, BOTH)}.service",
    )
    rig.systemctl.script[key] = CommandResult(0, loaded(OWNER))
    assert refused(rig) == ("refused", "job_files_invalid")
    assert rig.bao.paths == [] and rig.cp.lease_log == []


def test_a_unit_description_without_the_materials_key_is_refused(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    key = (
        "--property=LoadState,Description",
        f"openbao-agent@{stem_of(rig, BOTH)}.service",
    )
    rig.systemctl.script[key] = CommandResult(
        0, b"LoadState=loaded\nDescription=Render\n"
    )
    assert refused(rig) == ("refused", "job_files_invalid")


def test_staged_inputs_need_only_acl_read_not_world_readable(tmp_path: Path) -> None:
    """Staged inputs are 0440 with read ACLs; the agent hashes with plain read."""
    rig, digest_ = work_rig(tmp_path)
    staged = tmp_path / "work" / "import" / "in" / "manifest.json"
    staged.chmod(0o440)
    assert staged.stat().st_mode & 0o007 == 0  # not world-readable
    rig.cp.materials = run_job_materials(dispatched(**with_input(digest_)))
    assert refused(rig) == ("completed", None)


def test_the_polkit_start_list_is_exact_names_only_no_wildcard() -> None:
    polkit = (
        KIT / "enrolment/polkit/50-dotmac-deploy-agent.rules.template"
    ).read_text()
    code = "\n".join(
        ln for ln in polkit.splitlines() if not ln.lstrip().startswith("//")
    )
    assert "@ENROLLED_JOB_UNITS@" in code
    for wild in (
        "*",
        "startsWith",
        'indexOf(action.lookup("unit")) === 0',
        "match(",
        "RegExp",
    ):
        assert wild not in code, wild
    assert code.count('enrolledJobs.indexOf(action.lookup("unit")) !== -1') == 1


def test_the_agent_s12_is_compact_json_and_never_the_spaced_form() -> None:
    import hashlib

    from dotmac_deploy_agent.openbao_config import material_set_id

    names = ["legacy_database_url", "migration_database_url"]
    spaced = hashlib.sha256(json.dumps(names).encode()).hexdigest()[
        :12
    ]  # ", " separators
    assert spaced == "a50fd593917c"
    produced = material_set_id(("LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"))
    assert produced == "0dcc661ea25f" and produced != spaced


def test_a_purge_instance_without_its_dropin_fails_closed() -> None:
    base = (KIT / "enrolment/systemd/dotmac-secrets-purge@.service").read_text()
    live = [
        ln.strip() for ln in base.splitlines() if ln.strip() and not ln.startswith("#")
    ]
    assert [ln for ln in live if ln.startswith("ExecStart=")] == [
        "ExecStart=/usr/bin/false"
    ]
    assert "User=dotmac-secrets-unenrolled" in live  # a user that does not exist
    assert not any(
        "find" in ln or "-delete" in ln or "ReadWritePaths" in ln for ln in live
    )


def purge_argv(secrets: Path) -> list[str]:
    dropin = (KIT / "enrolment/systemd/secrets-purge.conf.template").read_text()
    line = next(
        ln for ln in dropin.splitlines() if ln.startswith("ExecStart=/usr/bin/find")
    )
    path = "/run/dotmac/@SECRETS_NAMESPACE@/@ROLE@/secrets"
    return [
        "find",
        *line.removeprefix("ExecStart=/usr/bin/find ")
        .replace(path, str(secrets))
        .split(),
    ]


def test_the_purge_find_does_not_follow_symlinks_or_leave_the_filesystem() -> None:
    argv = purge_argv(Path("/x"))
    assert (
        argv[1] == "-P" and "-xdev" in argv and argv[argv.index("-maxdepth") + 1] == "1"
    )
    assert (
        argv[-4:] == ["!", "-type", "d", "-delete"]
        and "-L" not in argv
        and "-H" not in argv
    )


def test_the_purge_find_removes_a_link_as_a_link_and_leaves_its_target(
    tmp_path: Path,
) -> None:
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("precious")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "a_url").write_text("x")
    (secrets / "link").symlink_to(outside)  # a link to a DIRECTORY outside
    (secrets / "flink").symlink_to(outside / "keep")  # a link to a file outside
    (secrets / "sub").mkdir()
    (secrets / "sub" / "inner").write_text("y")
    subprocess.run(purge_argv(secrets), check=True)  # noqa: S603
    assert sorted(p.name for p in secrets.iterdir()) == ["sub"]  # files and links gone
    assert (outside / "keep").read_text() == "precious"  # nothing followed
    assert (secrets / "sub" / "inner").exists()  # no recursion


# -- R6-1: no orphaned job container --------------------------------------------
def ps_filter(rig: Rig) -> list[tuple[str, ...]]:
    return [c for c in rig.commands.calls if len(c) > 1 and c[1] == "ps"]


def test_r61_foundation_gets_an_absolute_deadline_and_the_agent_bound_is_later(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.jobs import JOB_STOP_MARGIN
    from dotmac_deploy_agent.runner import runner_environment

    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    spec = rig.runner.specs[0]
    assert spec.job_deadline == int(rig.epoch[0]) + 600  # start + timeout_seconds
    # SIGTERM at the deadline itself: Foundation's own 90 s headroom is inside it
    assert JOB_STOP_MARGIN == 0 and spec.job_bound_seconds == 600
    env = runner_environment(spec)
    assert env["DOTMAC_DEPLOY_JOB_DEADLINE"] == str(spec.job_deadline)


def test_r61_the_margin_and_grace_fit_inside_the_lease_margin() -> None:
    from dotmac_deploy_agent.jobs import JOB_STOP_MARGIN, JOB_TERM_GRACE, LEASE_MARGIN

    assert JOB_STOP_MARGIN == 0 and JOB_TERM_GRACE == 60  # SIGKILL only after 60 s
    assert JOB_STOP_MARGIN + JOB_TERM_GRACE < LEASE_MARGIN


def test_r61_a_job_with_no_deadline_cannot_be_started() -> None:
    from dotmac_deploy_agent.runner import RunSpec, runner_environment

    spec = RunSpec("d", "erp/staging/h", "erp", "sha256:" + "a" * 64, Path("/p"), None,
        job_name="import", job_params_env=Path("/j"), job_secrets_dir=Path("/s"),
        job_argv_digest="sha256:" + "b" * 64)  # fmt: skip
    with pytest.raises(Refusal):
        runner_environment(spec)


POST_RUN = (
    "--filter",
    "label=dotmac.dispatch_id=dispatch-1",
)  # only the after-run proof


def test_r61_the_agent_proves_no_container_remains_before_purge_and_release(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    proof = [c for c in ps_filter(rig) if "label=dotmac.dispatch_id=dispatch-1" in c]
    assert len(proof) == 1 and "-a" in proof[0]
    purged = [c[-1] for c in rig.systemctl.calls if "secrets-purge" in " ".join(c)]
    assert len(purged) == 2  # before the mint and after the run, after a clean proof


def test_rev7_a_surviving_container_still_gets_the_purge_and_only_holds_the_lease(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[POST_RUN] = CommandResult(0, b"3f2a9c1b7d44\n")
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("failed", "job_container_orphaned")
    assert "lease/release" not in [e for e, _ in rig.cp.lease_log]  # HELD
    assert tracked(rig) == []  # the accessor was destroyed regardless
    assert not (rig.config.role_dir("import") / "secret_id").exists()
    purges = [c for c in rig.systemctl.calls if "secrets-purge" in " ".join(c)]
    assert len(purges) == 2  # the purge unit ran after the run too
    done = last(rig)
    assert done["details"]["page"] is True  # type: ignore[index]
    assert receipt(rig)["orphan_container"] is True
    assert "contained" not in json.dumps(done)  # never claims containment


def test_r61_an_unreadable_container_listing_is_treated_as_an_orphan(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[POST_RUN] = CommandResult(1, b"")
    assert refused(rig) == ("failed", "job_container_orphaned")


def test_r61_a_job_that_never_started_needs_no_container_proof(tmp_path: Path) -> None:
    rig = job_rig(tmp_path, job_roles=())
    assert refused(rig) == ("refused", "job_role_refused")
    # the agent-start preflight lists containers; no dispatch's own proof is asked for
    assert [
        c for c in ps_filter(rig) if "label=dotmac.dispatch_id=dispatch-1" in c
    ] == []


def preflight_calls(rig: Rig) -> list[tuple[str, ...]]:
    return [
        c
        for c in ps_filter(rig)
        if "label=dotmac.dispatch_id" in c and "label=dotmac.role_job=1" in c
    ]


def test_rev7_the_preflight_lists_stopped_containers_carrying_both_job_labels(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.jobs import JOB_DISPATCH_LABEL, JOB_MARKER_LABEL

    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    calls = preflight_calls(rig)
    assert len(calls) == 2  # at agent start, and again before the lease
    for call in calls:
        assert "-a" in call  # stopped ones included
        assert call.count("--filter") == 2
        assert (
            f"label={JOB_DISPATCH_LABEL}" in call
            and f"label={JOB_MARKER_LABEL}" in call
        )
    call = calls[-1]
    assert "-a" in call  # stopped ones included
    assert call.count("--filter") == 2
    assert f"label={JOB_DISPATCH_LABEL}" in call and f"label={JOB_MARKER_LABEL}" in call


@pytest.mark.parametrize("kind", ["job", "serving"])
def test_rev7_an_orphan_blocks_every_lease_job_or_serving(
    tmp_path: Path, kind: str
) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[("--filter", "label=dotmac.role_job=1")] = CommandResult(
        0, b"3f2a9c1b7d44\n"
    )
    if kind == "serving":
        rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "job_container_orphaned")
    assert rig.cp.lease_log == [] and rig.bao.paths == [] and rig.runner.specs == []
    assert rig.transfer.calls == []


def test_rev7_an_unreadable_preflight_blocks_too(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[("--filter", "label=dotmac.role_job=1")] = CommandResult(1, b"")
    assert refused(rig) == ("refused", "job_container_orphaned")


def test_rev7_agent_start_still_purges_and_the_orphan_blocks_the_next_lease(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.observation import mark_inflight

    rig = job_rig(tmp_path)
    deliver(rig)  # an accessor and a secret_id from the run that was interrupted
    mark_inflight(rig.config.state_dir, "dispatch-1")
    rig.commands.script[("ps",)] = CommandResult(0, b"3f2a9c1b7d44\n")
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"
    assert not (rig.config.role_dir("import") / "secret_id").exists()  # purged anyway
    assert tracked(rig) == []
    rig.cp.materials = run_job_materials(dispatch_id="dispatch-2")
    assert refused(rig) == ("refused", "job_container_orphaned")  # nothing new starts


def test_rev7_the_deadline_always_binds_to_what_the_lease_leaves(
    tmp_path: Path,
) -> None:
    """timeout 600 in a 720 s lease; setup eats 200 s: the budget shrinks, never grows."""
    from dotmac_deploy_agent.jobs import (
        JOB_STOP_MARGIN,
        JOB_TEARDOWN_RESERVE,
        JOB_TERM_GRACE,
    )

    rig = job_rig(tmp_path)
    acquired = int(rig.epoch[0])
    original = rig.systemctl.run

    def slow(argv, *, timeout):  # type: ignore[no-untyped-def]
        if "start" in argv and "openbao-agent@" in argv[-1]:
            rig.epoch[0] += 200  # the render took 200 s
        return original(argv, timeout=timeout)

    rig.systemctl.run = slow  # type: ignore[method-assign]
    assert refused(rig) == ("completed", None)
    spec = rig.runner.specs[0]
    reserve = JOB_STOP_MARGIN + JOB_TERM_GRACE + JOB_TEARDOWN_RESERVE
    assert spec.job_deadline == acquired + 720 - reserve  # not now + 600
    assert spec.job_deadline < acquired + 200 + 600
    assert (
        spec.job_bound_seconds == spec.job_deadline - (acquired + 200) + JOB_STOP_MARGIN
    )


def test_rev7_setup_that_uses_the_whole_lease_leaves_no_run(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    original = rig.systemctl.run

    def slower(argv, *, timeout):  # type: ignore[no-untyped-def]
        if "start" in argv and "openbao-agent@" in argv[-1]:
            rig.epoch[0] += 700
        return original(argv, timeout=timeout)

    rig.systemctl.run = slower  # type: ignore[method-assign]
    assert refused(rig) == ("failed", "lease_unavailable")
    assert rig.runner.specs == [] and tracked(rig) == []  # purged, nothing ran


def run_real(tmp_path: Path, script: str, *, bound: int = 1, grace: float = 1.0):  # type: ignore[no-untyped-def]
    import time

    from dotmac_deploy_agent.runner import RunSpec, SubprocessRunner

    runner = SubprocessRunner(
        ("/bin/sh", "-c", script),
        60,
        term_grace_seconds=grace,
        job_entry_available=True,
    )
    spec = RunSpec("d", "erp/staging/h", "erp", "sha256:" + "a" * 64, tmp_path / "p", None,
        job_name="import", job_params_env=tmp_path / "j", job_secrets_dir=tmp_path / "s",
        job_argv_digest="sha256:" + "b" * 64, job_deadline=1, job_bound_seconds=bound)  # fmt: skip
    started = time.monotonic()
    result = runner.run(spec)
    return result, time.monotonic() - started


def test_r61_the_timeout_sends_sigterm_first_and_foundation_gets_to_clean_up(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "cleaned"
    script = f"trap 'echo done > {marker}; exit 0' TERM; /bin/sleep 30 & wait"
    result, elapsed = run_real(tmp_path, script, bound=1, grace=5.0)
    assert result.timed_out and result.exit_code is None
    assert marker.read_text().strip() == "done"  # its finally ran: SIGTERM, not SIGKILL
    assert elapsed < 4.5  # it ended on SIGTERM, well before the grace elapsed


def test_r61_sigkill_comes_only_after_the_grace_for_a_process_that_ignores_sigterm(
    tmp_path: Path,
) -> None:
    script = "trap '' TERM; while :; do /bin/sleep 1; done"
    result, elapsed = run_real(tmp_path, script, bound=1, grace=2.0)
    assert result.timed_out
    assert 2.5 <= elapsed < 8  # bound + the full grace elapsed before SIGKILL


def test_r61_a_job_runner_without_the_foundation_entry_still_refuses(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.runner import RunSpec, SubprocessRunner

    runner = SubprocessRunner(("/bin/true",), 60)
    spec = RunSpec("d", "erp/staging/h", "erp", "sha256:" + "a" * 64, tmp_path / "p", None,
        job_name="import", job_params_env=tmp_path / "j", job_secrets_dir=tmp_path / "s",
        job_argv_digest="sha256:" + "b" * 64, job_deadline=1)  # fmt: skip
    with pytest.raises(Refusal) as refused_run:
        runner.run(spec)
    assert refused_run.value.code == "foundation_job_entry_unavailable"


# -- R6-2: template identity and path ---------------------------------------------
def test_r62_the_purge_base_refuses_without_its_dropin_and_refuses_root() -> None:
    base = (KIT / "enrolment/systemd/dotmac-secrets-purge@.service").read_text()
    live = [ln for ln in base.splitlines() if ln.strip() and not ln.startswith("#")]
    text = "\n".join(live)
    assert (
        "AssertPathExists=/etc/systemd/system/dotmac-secrets-purge@%i.service.d/secrets-purge.conf"
        in text
    )
    assert "ExecStartPre=/bin/sh -c 'test \"$(id -u)\" -ne 0'" in text
    assert "ExecStart=/usr/bin/false" in text
    assert "PrivateNetwork=yes" in text and "RestrictAddressFamilies=AF_UNIX" in text
    # nothing in the base derives a user, a path or a config from the instance name
    for ln in live:
        if "%i" in ln:
            assert ln.startswith(("Description=", "AssertPathExists="))


def test_r62_user_directory_and_command_come_only_from_the_dropin() -> None:
    dropin = (KIT / "enrolment/systemd/secrets-purge.conf.template").read_text()
    live = [ln for ln in dropin.splitlines() if ln.strip() and not ln.startswith("#")]
    assert "User=@OWNER_USER@" in live and "ExecStart=" in live  # the reset line
    assert not any("%i" in ln for ln in live)  # never parsed from the instance name
    find = next(ln for ln in live if ln.startswith("ExecStart=/usr/bin/find"))
    assert find.startswith(
        "ExecStart=/usr/bin/find -P /run/dotmac/@SECRETS_NAMESPACE@/@ROLE@/secrets -xdev "
    )


def test_r62_the_agent_names_purge_units_from_the_enrolled_namespace_and_role_only(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.job_delivery import purge_unit_name

    rig = job_rig(tmp_path)
    assert (
        purge_unit_name(rig.config.secrets_namespace, "import")
        == "dotmac-secrets-purge@erp-import.service"
    )
    rig.config = (
        rig.config
    )  # the dispatch path refuses a role the root config did not enrol
    other = (
        job_rig(tmp_path / "o", job_roles=("export",))
        if (tmp_path / "o").mkdir() is None
        else None
    )
    assert other is not None
    assert refused(other) == ("refused", "job_role_refused")
    assert not any("secrets-purge" in " ".join(c) for c in other.systemctl.calls)


def test_r62_the_render_dropin_lives_at_a_per_set_path_and_refuses_a_dirty_secrets_dir() -> (
    None
):
    render = (KIT / "enrolment/systemd/job-render.conf.template").read_text()
    assert (
        "openbao-agent@@SECRETS_NAMESPACE@-@ROLE@-@S12@.service.d/job-render.conf"
        in render
    )
    assert (
        "ExecStartPre=/bin/sh -c 'test -z \"$(ls -A /run/dotmac/@SECRETS_NAMESPACE@/@ROLE@/secrets)\"'"
        in render
    )
    assert "never enabled" in render


# -- shared S12 vectors, and the enrolment read-back ---------------------------
VECTORS = KIT / "enrolment/s12-vectors.json"


def test_the_shared_s12_vector_file_is_what_the_agent_computes() -> None:
    from dotmac_deploy_agent.openbao_config import material_set_id

    document = json.loads(VECTORS.read_text())
    for vector in document["vectors"]:
        assert material_set_id(tuple(vector["materials"])) == vector["s12"]
        import hashlib

        assert hashlib.sha256(vector["json"].encode()).hexdigest()[:12] == vector["s12"]
    for refused_form in document["not_produced"]:
        assert material_set_id(tuple(refused_form["materials"])) != refused_form["s12"]
    assert document["not_produced"][0]["s12"] == "a50fd593917c"


def fake_systemctl(path: Path) -> Path:
    path.write_text(
        "#!/bin/sh\ncase $1 in is-enabled) echo disabled; exit 1;; cat) exit 1;; esac\nexit 0\n"
    )
    path.chmod(0o755)
    return path


def enrol(tmp_path: Path, stem: str, *, role: str = "import") -> Path:
    unit_dir = tmp_path / "units"
    for rel, name in (
        (f"openbao-agent@{stem}.service.d", "job-render.conf"),
        (f"dotmac-secrets-purge@erp-{role}.service.d", "secrets-purge.conf"),
    ):
        (unit_dir / rel).mkdir(parents=True, exist_ok=True)
        (unit_dir / rel / name).write_text("[Service]\n")
    return unit_dir


def run_check(
    tmp_path: Path, unit_dir: Path, *names: str, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    import shutil
    import subprocess

    if shutil.which("sha256sum") is None:
        pytest.skip("GNU coreutils sha256sum is required")
    script = KIT / "enrolment/libexec/check-job-enrolment.sh"
    done = subprocess.run(  # noqa: S603
        ["/bin/sh", str(script), "erp", "import", *names],
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "SYSTEMCTL": str(fake_systemctl(tmp_path / "systemctl")), "UNIT_DIR": str(unit_dir), **(env_extra or {})},
    )  # fmt: skip
    return done.returncode, done.stdout.strip()


def test_the_enrolment_readback_accepts_a_correctly_enrolled_set(
    tmp_path: Path,
) -> None:
    unit_dir = enrol(tmp_path, "erp-import-0dcc661ea25f")
    code, out = run_check(
        tmp_path, unit_dir, "MIGRATION_DATABASE_URL", "LEGACY_DATABASE_URL"
    )
    assert (code, out) == (0, "erp-import-0dcc661ea25f")


def test_the_enrolment_readback_refuses_a_missing_dropin_and_an_enabled_instance(
    tmp_path: Path,
) -> None:
    unit_dir = enrol(tmp_path, "erp-import-c8cd75e18839")
    code, _ = run_check(
        tmp_path, unit_dir, "LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"
    )
    assert code != 0  # the per-set drop-in for THIS set is not installed
    unit_dir = (
        enrol(tmp_path / "e", "erp-import-c8cd75e18839")
        if (tmp_path / "e").mkdir() is None
        else unit_dir
    )
    enabled = tmp_path / "systemctl"
    enabled.write_text(
        "#!/bin/sh\ncase $1 in is-enabled) echo enabled; exit 0;; cat) exit 1;; esac\nexit 0\n"
    )
    enabled.chmod(0o755)
    import subprocess

    done = subprocess.run(  # noqa: S603
        ["/bin/sh", str(KIT / "enrolment/libexec/check-job-enrolment.sh"), "erp", "import", "MIGRATION_DATABASE_URL"],
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "SYSTEMCTL": str(enabled), "UNIT_DIR": str(unit_dir)},
    )  # fmt: skip
    assert done.returncode != 0  # job instances are never enabled


# -- F-H: Foundation's job receipt is the last JSON line of its stdout ------------
def real_receipt(tmp_path: Path, script: str):  # type: ignore[no-untyped-def]
    return run_real(tmp_path, script, bound=10, grace=1.0)[0]


def test_fh_the_last_json_line_of_foundation_stdout_is_the_receipt(
    tmp_path: Path,
) -> None:
    script = 'echo starting; echo \'{"step": 1}\'; echo \'{"job": "import", "exit_code": 0}\''
    result = real_receipt(tmp_path, script)
    assert result.exit_code == 0 and result.receipt_error is None
    assert result.receipt == {"job": "import", "exit_code": 0}  # the LAST line wins


def test_fh_no_output_is_a_missing_receipt(tmp_path: Path) -> None:
    result = real_receipt(tmp_path, "true")
    assert result.receipt is None and result.receipt_error == "missing"


@pytest.mark.parametrize("last", ["not json", "[1, 2]", '"text"', "42", '{"a": '])
def test_fh_a_last_line_that_is_not_a_json_object_is_malformed(
    tmp_path: Path, last: str
) -> None:
    result = real_receipt(tmp_path, f"echo '{{\"ok\": true}}'; echo '{last}'")
    assert result.receipt is None and result.receipt_error == "malformed"


def test_fh_only_the_tail_is_kept_when_stdout_is_large(tmp_path: Path) -> None:
    from dotmac_deploy_agent.runner import MAX_RECEIPT_BYTES

    script = (
        f"i=0; while [ $i -lt {MAX_RECEIPT_BYTES // 40 + 500} ]; do "
        "echo 'noise noise noise noise noise noise noise'; i=$((i+1)); done; "
        "echo '{\"final\": true}'"
    )
    result = real_receipt(tmp_path, script)
    assert result.receipt == {"final": True}


def test_fh_a_receipt_written_on_sigterm_is_captured(tmp_path: Path) -> None:
    script = 'trap \'echo "{\\"stopped\\": true}"; exit 0\' TERM; /bin/sleep 30 & wait'
    result, _ = run_real(tmp_path, script, bound=1, grace=5.0)
    assert result.timed_out and result.receipt == {"stopped": True}


def test_fh_a_deploy_run_never_captures_stdout(tmp_path: Path) -> None:
    from dotmac_deploy_agent.runner import RunSpec, SubprocessRunner

    runner = SubprocessRunner(("/bin/sh", "-c", "echo '{\"x\": 1}'"), 30)
    spec = RunSpec(
        "d", "erp/staging/h", "erp", "sha256:" + "a" * 64, tmp_path / "p", None
    )
    result = runner.run(spec)
    assert result.receipt is None and result.receipt_error is None


def test_fh_the_receipt_reaches_the_job_receipt(tmp_path: Path) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    # at details.foundation (one level up), not details.job.foundation (R5-H2)
    document = last(rig)["details"]
    assert document["foundation"]["job"] == "import"  # type: ignore[index]
    assert document["foundation"]["dispatch_id"] == "dispatch-1"  # type: ignore[index]
    assert "foundation" not in receipt(rig)


def test_fh_a_clean_exit_without_a_readable_receipt_is_not_success(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.runner.receipt = None
    rig.runner.receipt_error = "malformed"
    assert refused(rig) == ("failed", "job_receipt_invalid")
    assert receipt(rig)["foundation_receipt"] == "malformed"
    assert tracked(rig) == []  # still purged


def test_fh_a_failing_job_with_no_receipt_is_flagged_not_reclassified(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.runner.exit_code = 3
    rig.runner.receipt = None
    assert refused(rig) == ("failed", None)
    assert (
        receipt(rig)["foundation_receipt"] == "missing"
        and receipt(rig)["exit_code"] == 3
    )


def test_fh_an_oversized_capture_is_flagged_and_only_the_tail_is_read(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.runner import MAX_CAPTURE_BYTES

    script = (
        f"yes x | head -c {MAX_CAPTURE_BYTES + 4096}; " "echo; echo '{\"ok\": true}'"
    )
    result = real_receipt(tmp_path, script)
    assert result.exit_code == 0
    assert result.receipt is None and result.receipt_error == "oversized"


def test_fh_an_oversized_capture_makes_a_clean_exit_job_receipt_invalid(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.runner.receipt = None
    rig.runner.receipt_error = "oversized"
    assert refused(rig) == ("failed", "job_receipt_invalid")
    assert receipt(rig)["foundation_receipt"] == "oversized"


def test_fh_the_capture_file_lives_in_the_agents_own_run_directory(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)  # the run directory exists once it ran
    run = rig.config.deploy_run_dir
    assert str(run).startswith(str(rig.config.runtime_root))  # its RuntimeDirectory
    assert not str(run).startswith(str(rig.config.secrets_root))  # never /run/dotmac
    assert list(run.iterdir()) == []  # the anonymous file leaves no name behind


# -- the job identity file Foundation reads (kit) --------------------------------
INPUTS = "/var/lib/dotmac/erp-v2/job-work"


def identity_file(
    tmp_path: Path,
    uid: str = "10040",
    user: str = "dotmac-erp-import",
    inputs: str = INPUTS,
) -> Path:
    template = (KIT / "enrolment/job-identity.toml.template").read_text()
    path = tmp_path / "job-identity.toml"
    path.write_text(
        template.replace("@JOB_UID@", uid)
        .replace("@JOB_USER@", user)
        .replace("@INPUTS_ROOT@", inputs)
    )
    path.chmod(0o640)  # root:dotmac-deploy in production
    return path


def group_of(path: Path) -> str:
    import grp

    return grp.getgrgid(path.stat().st_gid).gr_name


def run_identity(path: Path, **extra: str) -> tuple[int, str]:
    import subprocess

    done = subprocess.run(  # noqa: S603
        ["/bin/sh", str(KIT / "enrolment/libexec/check-job-identity.sh")],
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "IDENTITY_FILE": str(path), "EXPECT_OWNER": str(os.geteuid()), "EXPECT_GROUP": group_of(path), "CHECK_USER": "0", **extra},
    )  # fmt: skip
    return done.returncode, done.stdout.strip()


def test_the_job_identity_template_is_valid_toml_with_exactly_the_two_keys(
    tmp_path: Path,
) -> None:
    import tomllib

    document = tomllib.loads(identity_file(tmp_path).read_text())
    assert document == {
        "job_uid": 10040,
        "job_user": "dotmac-erp-import",
        "inputs_root": INPUTS,
    }


def test_the_identity_readback_accepts_the_enrolled_file(tmp_path: Path) -> None:
    assert run_identity(identity_file(tmp_path)) == (
        0,
        f"dotmac-erp-import 10040 {INPUTS}",
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"uid": "0"},  # root
        {"uid": "999"},  # a system uid
        {"user": "root"},
        {"user": "Bad User"},
        {"uid": "ten"},
    ],
)
def test_the_identity_readback_refuses_root_system_and_malformed_identities(
    tmp_path: Path, bad: dict[str, str]
) -> None:
    assert run_identity(identity_file(tmp_path, **bad))[0] != 0


def test_the_identity_readback_refuses_a_wrong_mode_a_symlink_and_a_missing_file(
    tmp_path: Path,
) -> None:
    path = identity_file(tmp_path)
    path.chmod(0o666)
    assert run_identity(path)[0] != 0  # group/other-writable
    path.chmod(0o600)
    assert run_identity(path)[0] != 0  # not 0640
    path.chmod(0o644)
    assert run_identity(path)[0] != 0  # world-readable is not allowed (R5-L2)
    path.chmod(0o640)
    assert run_identity(path, EXPECT_GROUP="no-such-group")[0] != 0  # wrong group
    link = tmp_path / "link.toml"
    link.symlink_to(
        identity_file(tmp_path / "x") if (tmp_path / "x").mkdir() is None else path
    )
    assert run_identity(link)[0] != 0
    assert run_identity(tmp_path / "absent.toml")[0] != 0


def test_the_identity_readback_refuses_a_file_owned_by_another_uid(
    tmp_path: Path,
) -> None:
    assert (
        run_identity(identity_file(tmp_path), EXPECT_OWNER=str(os.geteuid() + 1))[0]
        != 0
    )


def test_the_job_identity_never_travels_in_dispatch_env_or_the_agent_code() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
    )
    text = "".join(p.read_text() for p in src.glob("*.py"))
    assert (
        "job_uid" not in text and "JOB_UID" not in text and "job-identity" not in text
    )


@pytest.mark.parametrize(
    "inputs", ["relative/path", "/run/dotmac/erp/in", "/", "/var/../etc", "/a b"]
)
def test_the_identity_readback_refuses_a_bad_inputs_root(
    tmp_path: Path, inputs: str
) -> None:
    assert run_identity(identity_file(tmp_path, inputs=inputs))[0] != 0


def test_the_identity_inputs_root_must_equal_the_agent_job_work_root(
    tmp_path: Path,
) -> None:
    config = tmp_path / "agent.toml"
    config.write_text(f'job_work_root = "{INPUTS}"\n')
    assert run_identity(identity_file(tmp_path), AGENT_CONFIG=str(config))[0] == 0
    config.write_text('job_work_root = "/var/lib/other"\n')
    assert run_identity(identity_file(tmp_path), AGENT_CONFIG=str(config))[0] != 0


def run_config_check(path: Path, **extra: str) -> int:
    import subprocess

    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(KIT / "enrolment/libexec/check-agent-config.sh"), str(path)],
        check=False, capture_output=True,
        env={"PATH": "/usr/bin:/bin", "EXPECT_OWNER": str(os.geteuid()), "EXPECT_GROUP": group_of(path), **extra},
    ).returncode  # fmt: skip


def test_the_agent_config_readback_wants_0640_root_group_not_0600_or_world_readable(
    tmp_path: Path,
) -> None:
    config = tmp_path / "erp.toml"
    config.write_text("x = 1\n")
    config.chmod(0o640)
    assert run_config_check(config) == 0
    for mode in (0o600, 0o644, 0o660, 0o666):
        config.chmod(mode)
        assert run_config_check(config) != 0, oct(
            mode
        )  # unreadable, public or writable
    config.chmod(0o640)
    assert run_config_check(config, EXPECT_OWNER=str(os.geteuid() + 1)) != 0
    assert run_config_check(config, EXPECT_GROUP="no-such-group") != 0


def test_no_kit_or_doc_asks_for_a_0600_agent_config() -> None:
    root = Path(__file__).resolve().parents[2] / "packages/dotmac-deploy-agent"
    for path in [root / "README.md", root / "deploy/config.example.toml"]:
        text = path.read_text()
        assert (
            "0600" not in text.replace("never 0600", "") and "mode 600" not in text
        ), path.name
    example = (root / "deploy/config.example.toml").read_text()
    assert "0640 root:dotmac-deploy" in example and "0644" not in example


def test_there_is_no_job_secrets_uid_key_anywhere() -> None:
    root = Path(__file__).resolve().parents[2]
    here = Path(__file__).resolve()
    hits = [
        str(p)
        for base in ("packages/dotmac-deploy-agent", "tests/unit")
        for p in (root / base).rglob("*")
        if p.is_file()
        and p != here
        and p.suffix in {".py", ".md", ".toml", ".template", ".sh", ".service", ".conf"}
        and "job_secrets_uid" in p.read_text()
    ]
    assert hits == []


def test_the_instance_name_is_the_product_segment_and_two_instances_run_side_by_side(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.refusal import Refusal

    from tests.unit.deploy_agent_support import Keys, make_config

    def instance(product: str) -> object:
        return make_config(
            tmp_path / product,
            Keys(),
            target_ref=f"{product}/staging/host-a",
            runtime_root=tmp_path / "run",
            state_dir=tmp_path / "state" / product,
        )

    (tmp_path / "erp").mkdir()
    (tmp_path / "erp-companion").mkdir()
    erp, companion = instance("erp"), instance("erp-companion")
    assert erp.product == "erp" and erp.run_dir == tmp_path / "run" / "erp"  # type: ignore[attr-defined]

    def paths(c):  # type: ignore[no-untyped-def]
        return {
            c.run_dir,
            c.deploy_run_dir,
            c.state_dir,
            c.state_dir / "agent.lock",
            c.state_dir / "role-job.lock",
        }

    assert paths(erp).isdisjoint(paths(companion))  # nothing is shared
    # what systemd sets for `dotmac-deploy-agent@erp` and `@erp-companion`
    erp.check_instance(
        {
            "RUNTIME_DIRECTORY": str(tmp_path / "run" / "erp"),
            "STATE_DIRECTORY": str(tmp_path / "state" / "erp"),
        }
    )  # type: ignore[attr-defined]
    companion.check_instance(
        {"RUNTIME_DIRECTORY": str(tmp_path / "run" / "erp-companion")}
    )  # type: ignore[attr-defined]
    erp.check_instance({})  # type: ignore[attr-defined]  # outside systemd: nothing to compare
    with pytest.raises(
        Refusal
    ) as wrong:  # the ERP config started as the companion's instance
        erp.check_instance(
            {"RUNTIME_DIRECTORY": str(tmp_path / "run" / "erp-companion")}
        )  # type: ignore[attr-defined]
    assert wrong.value.code == "config_invalid"
    with pytest.raises(Refusal):
        erp.check_instance(
            {"STATE_DIRECTORY": str(tmp_path / "state" / "erp-companion")}
        )  # type: ignore[attr-defined]
    unit = (KIT / "systemd/dotmac-deploy-agent@.service").read_text()
    assert (
        "RuntimeDirectory=dotmac-deploy-agent/%i" in unit
        and "StateDirectory=dotmac-deploy-agent/%i" in unit
    )


# -- r5 R5-H1: remove the orphan, re-prove, hold only if one REMAINS ---------------
REMOVE = ("/opt/dotmac/dotmac-deploy", "remove-job")
ORPHAN_ID = b"3f2a9c1b7d44\n"


def stateful_ps(rig: Rig, *, cleared_by: str, dispatch_only: bool) -> None:
    """`docker ps -a` lists a container until `cleared_by` has run (rm -f / remove-job)."""
    original = rig.commands.run

    def run(argv, *, timeout):  # type: ignore[no-untyped-def]
        if len(argv) > 1 and argv[1] == "ps":
            gone = any(cleared_by in " ".join(c) for c in rig.systemctl.calls)
            wanted = ("label=dotmac.dispatch_id=dispatch-1" in argv) == dispatch_only
            if wanted and not gone:
                return CommandResult(0, ORPHAN_ID)
        return original(argv, timeout=timeout)

    rig.commands.run = run  # type: ignore[method-assign]


@pytest.mark.parametrize("timed_out", [False, True], ids=["exit", "sigkilled"])
def test_r5h1_an_orphan_after_the_run_is_removed_by_foundation_then_released(
    tmp_path: Path, timed_out: bool
) -> None:
    rig = job_rig(tmp_path, foundation_remove_job_argv=REMOVE)
    rig.runner.timed_out = timed_out  # the SIGKILL case: Foundation's finally never ran
    stateful_ps(rig, cleared_by="remove-job", dispatch_only=True)
    result = rig.launcher.run_once()
    expected = ("failed", "runner_timeout") if timed_out else ("completed", None)
    assert (result.outcome, result.reason) == expected  # a routine run, not an anomaly
    [call] = [c for c in rig.systemctl.calls if "remove-job" in c]
    assert call == (
        *REMOVE,
        "--dispatch-id",
        "dispatch-1",
        "--execute",
        "--target",
        rig.config.target_ref,
    )
    assert "lease/release" in [e for e, _ in rig.cp.lease_log]  # released, not held
    assert tracked(rig) == [] and receipt(rig)["orphan_removed"] is True
    assert "page" not in last(rig)["details"] and "orphan_container" not in receipt(rig)  # type: ignore[operator]


def test_r5h1_an_orphan_that_survives_removal_holds_the_lease_and_pages(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path, foundation_remove_job_argv=REMOVE)
    rig.commands.script[POST_RUN] = CommandResult(0, ORPHAN_ID)  # never goes away
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("failed", "job_container_orphaned")
    assert any("remove-job" in c for c in rig.systemctl.calls)  # it was tried
    assert "lease/release" not in [e for e, _ in rig.cp.lease_log]
    assert (
        receipt(rig)["orphan_container"] is True
        and receipt(rig)["orphan_removed"] is False
    )
    assert last(rig)["details"]["page"] is True  # type: ignore[index]
    assert tracked(rig) == []  # purged regardless


def test_r5h1_without_a_configured_removal_the_container_remains_and_pages(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)  # no foundation_remove_job_argv
    rig.commands.script[POST_RUN] = CommandResult(0, ORPHAN_ID)
    assert refused(rig) == ("failed", "job_container_orphaned")
    assert not any("remove-job" in " ".join(c) for c in rig.systemctl.calls)


def test_r5h1_agent_restart_removes_a_leftover_container_by_label(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    stateful_ps(rig, cleared_by="rm -f", dispatch_only=False)
    rig.cp.materials = None
    assert rig.launcher.run_once().outcome == "idle"  # agent start cleans up
    removals = [c for c in rig.systemctl.calls if c[1:3] == ("rm", "-f")]
    assert [c[-1] for c in removals] == [ORPHAN_ID.decode().strip()]
    rig.cp.materials = run_job_materials()
    assert refused(rig) == ("completed", None)  # nothing blocks the next job


def appears_on_the_second_listing(rig: Rig) -> None:
    """A container shows up after agent start: the 2nd both-label listing finds it, and
    it stays listed until a `docker rm -f` has run."""
    original = rig.commands.run
    seen = {"listings": 0}

    def run(argv, *, timeout):  # type: ignore[no-untyped-def]
        if len(argv) > 1 and argv[1] == "ps" and "label=dotmac.role_job=1" in argv:
            seen["listings"] += 1
            removed = any(c[1:3] == ("rm", "-f") for c in rig.systemctl.calls)
            if seen["listings"] >= 2 and not removed:
                return CommandResult(0, ORPHAN_ID)
        return original(argv, timeout=timeout)

    rig.commands.run = run  # type: ignore[method-assign]


def test_r5h1_a_container_found_before_a_lease_is_removed_then_the_cycle_is_refused_and_paged(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    appears_on_the_second_listing(rig)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "job_container_orphaned")
    assert any(c[1:3] == ("rm", "-f") for c in rig.systemctl.calls)  # removed by label
    assert last(rig)["details"] == {"page": True, "removed": True}  # an anomaly: paged
    assert rig.cp.lease_log == [] and rig.bao.paths == []  # nothing started
    rig.cp.materials = run_job_materials(dispatch_id="dispatch-2")
    assert refused(rig) == ("completed", None)  # the next poll proceeds: none remains


def test_r5h1_a_container_found_at_agent_start_is_removed_and_the_same_cycle_proceeds(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    stateful_ps(rig, cleared_by="rm -f", dispatch_only=False)
    assert refused(rig) == ("completed", None)
    assert any(c[1:3] == ("rm", "-f") for c in rig.systemctl.calls)


def test_r5h1_the_label_scoped_removal_only_ever_names_ids_from_the_label_listing(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.commands.script[("--filter", "label=dotmac.role_job=1")] = CommandResult(
        0, b"; rm -rf /\n"
    )
    assert refused(rig) == ("refused", "job_container_orphaned")  # unreadable listing
    assert not any(c[1:3] == ("rm", "-f") for c in rig.systemctl.calls)  # nothing run


# -- R5-H2: the full receipt fits Control's depth limit --------------------------
def test_r5h2_a_full_foundation_receipt_is_not_dropped_by_the_v2_depth_rule(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent import observation_v2 as o2

    rig = job_rig(tmp_path, target_api_version=2)
    assert refused(rig) == ("completed", None)
    statement = rig.cp.observations[-1]["statement"]
    assert "details_dropped" not in statement["details"]
    foundation = statement["details"]["foundation"]
    assert foundation["inputs"] == [
        {"flag": "--manifest", "sha256": "sha256:" + "7" * 64}
    ]
    assert foundation["container_removed"] is True and foundation[
        "output_volume"
    ].startswith("dotmac-job-out-")
    for kept in ("job", "lease_released"):
        assert kept in statement["details"]
    assert (
        o2.shaped_details(statement["details"]) == statement["details"]
    )  # idempotent: nothing dropped


def test_r5h2_the_old_nesting_would_have_been_dropped() -> None:
    from dotmac_deploy_agent import observation_v2 as o2

    inputs = [{"flag": "--m", "sha256": "sha256:" + "7" * 64}]
    old = {
        "job": {"foundation": {"inputs": inputs}}
    }  # details > job > foundation > inputs > item
    assert o2.shaped_details(old) == {"details_dropped": "shape"}
    assert o2.shaped_details(
        {"foundation": {"inputs": inputs}, "job": {"files": ["a"]}}
    ) != {"details_dropped": "shape"}


# -- R5-L1: Foundation's receipt names THIS run ------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        {"dispatch_id": "dispatch-0"},
        {"job": "export"},
        {"render_invocation": "b2" * 16},
        {"schema": "dotmac.foundation.operator-job-receipt/2"},
    ],
)
def test_r5l1_a_receipt_of_another_run_is_not_this_jobs_result(
    tmp_path: Path, bad: dict[str, object]
) -> None:
    from tests.unit.deploy_agent_support import AUTO

    rig = job_rig(tmp_path)
    assert rig.runner.receipt is AUTO
    original = rig.runner.run

    def run(spec):  # type: ignore[no-untyped-def]
        from tests.unit.deploy_agent_support import foundation_receipt

        rig.runner.receipt = foundation_receipt(spec, **bad)
        return original(spec)

    rig.runner.run = run  # type: ignore[method-assign]
    assert refused(rig) == ("failed", "job_receipt_invalid")
    assert receipt(rig)["foundation_receipt"] == "mismatch"
    assert last(rig)["details"]["foundation"]["job"] in ("import", "export")  # type: ignore[index]


def test_r5l1_a_foundation_refusal_receipt_is_kept_but_is_not_a_success(
    tmp_path: Path,
) -> None:
    rig = job_rig(tmp_path)
    rig.runner.receipt = {
        "schema": "dotmac.foundation.operator-job-receipt/1",
        "outcome": "refused",
        "reason": "job_context_invalid",
    }
    rig.runner.exit_code = 2
    assert refused(rig) == ("failed", None)
    assert last(rig)["details"]["foundation"]["reason"] == "job_context_invalid"  # type: ignore[index]


# -- R5-M1 -------------------------------------------------------------------------
def test_r5m1_sigterm_comes_at_the_deadline_and_sigkill_only_after_sixty_seconds(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent.jobs import JOB_TEARDOWN_RESERVE, JOB_TERM_GRACE

    rig = job_rig(tmp_path)
    assert refused(rig) == ("completed", None)
    spec = rig.runner.specs[0]
    assert spec.job_bound_seconds == spec.job_deadline - int(
        rig.epoch[0]
    )  # the deadline itself
    assert JOB_TERM_GRACE == 60 and JOB_TEARDOWN_RESERVE == 40  # the reserve stays 100


def test_r5m1_the_real_runner_uses_the_sixty_second_job_grace(tmp_path: Path) -> None:
    from dotmac_deploy_agent.runner import SubprocessRunner

    runner = SubprocessRunner(("/bin/true",), 60)  # the default grace
    assert runner._grace == 60.0
