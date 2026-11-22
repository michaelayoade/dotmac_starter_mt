"""dotmac-deploy-agent: D1 delivery (ruling R2/R4), authored configs, runner environment."""
# ruff: noqa: E501

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult
from dotmac_deploy_agent.openbao import mint_path
from dotmac_deploy_agent.runner import RunSpec, runner_environment

from tests.unit.deploy_agent_support import (
    FakeBaoTransport,
    Rig,
    make_materials,
    make_rig,
    prepare_role,
)

WEB = [{"role": "web", "single_use": False}]
MIGRATE = [{"role": "migrate", "single_use": True}]
ROLES = MIGRATE + WEB  # a signed plan lists roles sorted


def deliver_rig(tmp_path: Path, roles: list[dict[str, object]] = ROLES) -> Rig:
    rig = make_rig(tmp_path)
    for role in roles:
        prepare_role(rig, str(role["role"]))
    rig.cp.materials = make_materials(roles=roles)
    return rig


def test_secret_ids_are_written_0400_as_the_agent_uid_and_never_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = deliver_rig(tmp_path)
    result = rig.launcher.run_once()
    assert result.outcome == "completed", result
    for role in ("web", "migrate"):
        path = rig.config.run_dir / role / "secret_id"
        assert path.read_text() == FakeBaoTransport.SECRET
        assert path.stat().st_mode & 0o777 == 0o400
        assert path.stat().st_uid == os.geteuid()  # no chown, ever
    assert FakeBaoTransport.SECRET not in repr(rig.cp.observations)
    assert FakeBaoTransport.SECRET not in caplog.text
    assert FakeBaoTransport.TOKEN not in repr(rig.cp.observations)
    assert rig.cp.observations[0]["details"]["delivered_roles"] == [  # type: ignore[index]
        {
            "role": "migrate",
            "single_use": True,
            "restarted": True,
            "old_accessors_destroyed": 0,
        },
        {
            "role": "web",
            "single_use": False,
            "restarted": True,
            "old_accessors_destroyed": 0,
        },
    ]


def test_no_role_id_is_fetched_and_no_role_file_is_written(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path)
    rig.launcher.run_once()
    assert not any(p.endswith("/role-id") for p in rig.bao.paths)
    assert not (rig.config.run_dir / "web" / "role_id").exists()
    assert not (rig.config.run_dir / "web" / "secrets").exists()  # tmpfiles.d, not us


def test_approle_login_mint_in_parent_namespace_lookup_before_unwrap_then_revoke(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    assert rig.bao.paths == [
        "/v1/auth/approle-delivery/login",
        "/v1/erp/auth/approle-apps/role/erp-staging-web/secret-id",
        "/v1/sys/wrapping/lookup",
        "/v1/sys/wrapping/unwrap",
        "/v1/auth/token/revoke-self",
    ]
    assert mint_path("erp", "staging", "web") == rig.bao.paths[1].removeprefix("/v1/")
    assert rig.bao.revoked  # the 5 minute token does not outlive the run
    for _, _, headers, _ in rig.bao.requests:
        assert headers["X-Vault-Namespace"] == "staging"  # parent namespace


def test_mint_body_is_limited_to_metadata_ttl_and_num_uses(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path)
    rig.launcher.run_once()
    mints = [r for r in rig.bao.requests if r[1].endswith("/secret-id")]
    single, runtime = mints[0][3], mints[1][3]  # roles are sorted: migrate, web
    assert set(runtime) == {"metadata", "ttl"} and runtime["ttl"] == f"{35 * 86400}s"
    assert set(single) == {"metadata", "ttl", "num_uses"}
    assert single["num_uses"] == 1 and single["ttl"] == "900s"
    assert json.loads(str(single["metadata"])) == {"dispatch_id": "dispatch-1"}


def test_wrapping_token_from_an_unexpected_creation_path_is_refused_before_unwrap(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.bao.lookup_path = "erp/auth/approle-apps/role/erp-staging-other/secret-id"
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "secret_wrap_unexpected_path")
    assert "/v1/sys/wrapping/unwrap" not in rig.bao.paths
    assert not (rig.config.run_dir / "web" / "secret_id").exists()
    assert rig.bao.revoked and rig.runner.specs == []


def test_an_already_unwrapped_token_is_a_distinct_alarm(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.bao.fail_lookup = True
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == (
        "refused",
        "secret_wrap_already_unwrapped",
    )
    assert "/v1/sys/wrapping/unwrap" not in rig.bao.paths
    assert rig.runner.specs == []


def test_unit_is_restarted_and_a_failed_restart_removes_the_unconsumed_id(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    restart = [c for c in rig.systemctl.calls if "restart" in c]
    assert restart == [
        (
            str(rig.config.systemctl_path),
            "--no-ask-password",
            "restart",
            "openbao-agent@erp-web.service",
        )
    ]
    rig2 = deliver_rig(tmp_path / "second", WEB)
    rig2.systemctl.script[("restart",)] = CommandResult(1, b"")
    result = rig2.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "secret_delivery_failed")
    assert not (rig2.config.run_dir / "web" / "secret_id").exists()


def test_previous_accessor_is_destroyed_after_the_new_login_is_confirmed(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()  # first delivery: nothing to destroy
    assert not any(p.endswith("/destroy") for p in rig.bao.paths)
    rig.launcher.run_once()  # second: the first accessor goes
    destroys = [
        r for r in rig.bao.requests if r[1].endswith("/secret-id-accessor/destroy")
    ]
    assert [d[1] for d in destroys] == [
        "/v1/erp/auth/approle-apps/role/erp-staging-web/secret-id-accessor/destroy"
    ]
    assert destroys[0][3] == {"secret_id_accessor": "accessor-wrap-1"}
    kept = json.loads((rig.config.state_dir / "accessors" / "web.json").read_text())
    assert kept == ["accessor-wrap-2"]


def test_an_accessor_that_cannot_be_destroyed_is_kept_not_forgotten(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    rig.bao.fail_destroy = True
    rig.launcher.run_once()
    kept = json.loads((rig.config.state_dir / "accessors" / "web.json").read_text())
    assert kept == ["accessor-wrap-1", "accessor-wrap-2"]


def test_single_use_roles_have_no_accessor_bookkeeping(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, MIGRATE)
    rig.launcher.run_once()
    assert not (rig.config.state_dir / "accessors" / "migrate.json").exists()


def test_a_role_directory_that_enrolment_did_not_create_is_refused(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path)
    (rig.config.openbao_config_dir / "erp-web.hcl").write_text("# authored\n")
    (rig.config.openbao_config_dir / "erp-web.hcl").chmod(0o644)
    rig.cp.materials = make_materials(roles=WEB)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "runtime_dir_unsafe")
    assert not (rig.config.run_dir / "web").exists()  # the agent never creates it


def test_authored_configs_are_checked_and_their_digest_reported(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    result = rig.launcher.run_once()
    assert result.outcome == "completed"
    digest = rig.cp.observations[0]["details"]["openbao_config"]["web"]  # type: ignore[index]
    assert digest.startswith("sha256:") and len(digest) == 71
    # Never written: the file is still exactly what the kit installed.
    authored = rig.config.openbao_config_dir / "erp-web.hcl"
    assert authored.read_text() == "# authored config for web\n"


@pytest.mark.parametrize("case", ["missing", "writable", "symlink"])
def test_unsafe_authored_config_is_refused(tmp_path: Path, case: str) -> None:
    rig = deliver_rig(tmp_path, WEB)
    authored = rig.config.openbao_config_dir / "erp-web.hcl"
    if case == "missing":
        authored.unlink()
    elif case == "writable":
        authored.chmod(0o664)
    else:
        authored.unlink()
        authored.symlink_to(tmp_path / "elsewhere")
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "openbao_config_invalid")
    assert rig.bao.paths == []  # no login before the authored bytes check out


def test_the_agent_has_no_renderer_for_openbao_config() -> None:
    import importlib.util

    assert importlib.util.find_spec("dotmac_deploy_agent.openbao_agent") is None


def test_bindings_without_a_delivery_transport_refuse(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.bao_transport = None
    assert rig.launcher.run_once().reason == "secret_delivery_failed"
    assert rig.runner.specs == []


def test_runner_environment_is_the_bindings_names_plus_the_deploy_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("OPENBAO_TOKEN", "x")
    rig = make_rig(tmp_path)
    spec = RunSpec(
        "d1",
        "erp/staging/host-a",
        "erp",
        "sha256:" + "9" * 64,
        tmp_path / "params.env",
        rig.config.config_path,
        rig.config.bindings_env,
        "/run/credentials/unit",
        tmp_path / "legacy.env",
        "lease-1",
    )
    env = runner_environment(spec)
    assert set(env) == {
        "PATH", "LANG",
        "AUTHORIZATION_VERIFICATION_IDENTITY", "DISPATCH_VERIFICATION_IDENTITY",
        "EXECUTION_CONTEXT_MATERIALS", "HOST_ADMISSION_MATERIALS",
        "RELEASE_EVIDENCE_REPOSITORY", "RELEASE_EVIDENCE_VERIFICATION_IDENTITY",
        "DOTMAC_DEPLOY_DISPATCH_ID", "DOTMAC_DEPLOY_TARGET_REF", "DOTMAC_DEPLOY_PRODUCT",
        "DOTMAC_DEPLOY_PARAMS_ENV", "DOTMAC_DEPLOY_LEGACY_ENV", "DOTMAC_DEPLOY_LEASE_ID",
        "DOTMAC_DEPLOY_PLAN_DIGEST", "DOTMAC_DEPLOY_AGENT_CONFIG", "CREDENTIALS_DIRECTORY",
    }  # fmt: skip
    assert env["DOTMAC_DEPLOY_PLAN_DIGEST"] == "sha256:" + "9" * 64
    assert not any(
        name.startswith("PG") or name.endswith("DATABASE_URL") for name in env
    )


def test_the_launcher_hands_the_runner_the_signed_plan_digest(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    rig.launcher.run_once()
    [spec] = rig.runner.specs
    assert spec.plan_digest.startswith("sha256:")
    assert spec.agent_config == rig.config.config_path
    assert dict(spec.bindings_env) == dict(rig.config.bindings_env)
    assert spec.credentials_directory == rig.environ["CREDENTIALS_DIRECTORY"]


def test_roles_come_only_from_the_signed_plan(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    document = make_materials(roles=WEB)
    document["roles"] = [{"role": "evil", "single_use": False}]  # CP-supplied, unsigned
    rig.cp.materials = document
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_invalid")
    assert rig.bao.paths == [] and rig.runner.specs == []
    # And a plan that binds no roles mints nothing.
    rig.cp.materials = make_materials()
    assert rig.launcher.run_once().outcome == "completed"
    assert rig.bao.paths == []


def test_the_creation_path_form_seen_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    assert "creation_path form=bare" in caplog.text
    caplog.clear()
    rig2 = deliver_rig(tmp_path / "ns", WEB)
    rig2.bao.lookup_path = (
        "staging/erp/auth/approle-apps/role/erp-staging-web/secret-id"
    )
    rig2.launcher.run_once()
    assert "creation_path form=namespaced" in caplog.text


def test_no_file_in_the_run_directory_is_group_or_world_accessible(
    tmp_path: Path,
) -> None:
    """RuntimeDirectoryMode 0711 is traverse-only: the product directory and the
    per-role directories may carry `x` for others and nothing else; every other
    directory is 0700 and every file has no group or other bit at all."""
    import stat

    rig = deliver_rig(tmp_path)
    rig.runner.effect = lambda spec: _snapshot(rig, seen)
    seen: list[tuple[Path, int]] = []
    rig.launcher.run_once()
    seen += _snapshot(rig, [])
    assert seen
    traverse_only = {
        rig.config.run_dir,
        *(rig.config.run_dir / r["role"] for r in ROLES),
    }
    for path, mode in seen:
        if stat.S_ISDIR(mode):
            if path in traverse_only:
                assert mode & 0o066 == 0, path  # no group/other read or write
            else:
                assert mode & 0o077 == 0, path
        else:
            assert mode & 0o077 == 0, path


def _snapshot(rig: Rig, into: list[tuple[Path, int]]) -> list[tuple[Path, int]]:
    for path in sorted(rig.config.run_dir.rglob("*")):
        into.append((path, path.lstat().st_mode))
    into.append((rig.config.run_dir, rig.config.run_dir.lstat().st_mode))
    return into
