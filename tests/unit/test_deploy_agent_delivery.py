"""dotmac-deploy-agent: D1 delivery (ruling R2/R4), accessor lifecycle, naming, run env."""

# ruff: noqa: E501
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult
from dotmac_deploy_agent.openbao import mint_path
from dotmac_deploy_agent.runner import RunSpec, runner_environment

from tests.unit.deploy_agent_support import (
    AUTHORED,
    FakeBaoTransport,
    Rig,
    make_materials,
    make_rig,
    prepare_role,
)

WEB = [{"role": "web", "single_use": False}]
MIGRATE = [{"role": "migrate", "single_use": True}]
ROLES = MIGRATE + WEB  # a signed plan lists roles sorted
SETTLE = 600  # AgentConfig.settle_seconds default


def deliver_rig(
    tmp_path: Path, roles: list[dict[str, object]] = ROLES, **kw: object
) -> Rig:
    rig = make_rig(tmp_path, **kw)  # type: ignore[arg-type]
    for role in roles:
        prepare_role(rig, str(role["role"]))
    rig.cp.materials = make_materials(roles=roles)
    return rig


def secret_id_file(rig: Rig, role: str) -> Path:
    return rig.config.role_dir(role) / "secret_id"


def state(rig: Rig, role: str) -> dict[str, object]:
    return json.loads((rig.config.state_dir / "accessors" / f"{role}.json").read_text())


def deliver_again(rig: Rig, dispatch: str) -> None:
    """Another dispatch (the journal refuses a repeat of the same id)."""
    rig.cp.materials = make_materials(roles=WEB, dispatch_id=dispatch)
    rig.launcher.run_once()


def idle_cycle(rig: Rig, after_seconds: float) -> None:
    rig.cp.materials = None
    rig.epoch[0] += after_seconds
    rig.launcher.run_once()


def test_secret_ids_are_written_0400_as_the_agent_uid_and_never_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = deliver_rig(tmp_path)
    result = rig.launcher.run_once()
    assert result.outcome == "completed", result
    path = secret_id_file(rig, "web")
    assert path.read_text() == FakeBaoTransport.SECRET
    assert path.stat().st_mode & 0o777 == 0o400
    assert path.stat().st_uid == os.geteuid()  # no chown, ever
    assert FakeBaoTransport.SECRET not in repr(rig.cp.observations)
    assert FakeBaoTransport.SECRET not in caplog.text
    assert FakeBaoTransport.TOKEN not in repr(rig.cp.observations)
    assert rig.cp.observations[0]["details"]["delivered_roles"] == [  # type: ignore[index]
        {"role": "migrate", "single_use": True, "restarted": True},
        {"role": "web", "single_use": False, "restarted": True},
    ]


def test_a_spent_single_use_secret_id_is_not_left_behind(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path)
    rig.launcher.run_once()
    assert not secret_id_file(rig, "migrate").exists()  # L8: removed after the run
    assert secret_id_file(rig, "web").exists()  # a runtime id must outlive the run


def test_no_role_id_is_fetched_and_no_role_file_is_written(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    assert not any(p.endswith("/role-id") for p in rig.bao.paths)
    assert not (rig.config.role_dir("web") / "role_id").exists()
    assert not (rig.config.role_dir("web") / "secrets").exists()  # tmpfiles.d, not us


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
    assert rig.bao.revoked
    for _, _, headers, _ in rig.bao.requests:
        assert headers["X-Vault-Namespace"] == "staging"


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
    assert not secret_id_file(rig, "web").exists()
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


# -- accessor lifecycle (M1, M2, L9) ----------------------------------------


def test_the_new_accessor_is_tracked_before_the_unit_is_restarted_and_confirmed_after(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    seen: list[dict[str, object]] = []
    original = rig.systemctl.run

    def spy(argv, *, timeout):  # type: ignore[no-untyped-def]
        if "restart" in argv:
            seen.append(state(rig, "web"))  # what is durable at restart time
        return original(argv, timeout=timeout)

    rig.systemctl.run = spy  # type: ignore[method-assign]
    rig.launcher.run_once()
    assert seen[0]["candidate"]["stage"] == "issued"  # type: ignore[index]
    final = state(rig, "web")
    assert final["candidate"]["stage"] == "restarted"  # type: ignore[index]
    assert final["candidate"]["accessor"] == "accessor-wrap-1"  # type: ignore[index]
    assert final["candidate"]["nrestarts"] == "0"  # type: ignore[index]


def test_a_failed_restart_destroys_the_new_accessor_and_removes_the_file(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.systemctl.script[("restart",)] = CommandResult(1, b"")
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "secret_delivery_failed")
    destroys = [r for r in rig.bao.requests if r[1].endswith("/destroy")]
    assert [d[3] for d in destroys] == [{"secret_id_accessor": "accessor-wrap-1"}]
    assert not secret_id_file(rig, "web").exists()
    assert state(rig, "web")["candidate"] is None


def test_a_failed_restart_whose_destroy_also_fails_keeps_the_accessor_for_retirement(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.systemctl.script[("restart",)] = CommandResult(1, b"")
    rig.bao.fail_destroy = True
    rig.launcher.run_once()
    assert state(rig, "web")["retire"] == ["accessor-wrap-1"]  # never forgotten


def test_older_accessors_are_not_destroyed_in_the_run_that_replaces_them(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    deliver_again(rig, "dispatch-2")
    assert not any(r[1].endswith("/destroy") for r in rig.bao.requests)
    now = state(rig, "web")
    assert now["retire"] == [
        "accessor-wrap-1"
    ]  # waiting for the new one to prove itself
    assert now["candidate"]["accessor"] == "accessor-wrap-2"  # type: ignore[index]


def test_old_accessors_are_destroyed_on_a_later_cycle_once_the_unit_stayed_up(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    deliver_again(rig, "dispatch-2")
    idle_cycle(rig, SETTLE - 1)  # not settled yet
    assert not any(r[1].endswith("/destroy") for r in rig.bao.requests)
    idle_cycle(rig, 2)  # now old enough, and unchanged
    destroys = [r for r in rig.bao.requests if r[1].endswith("/destroy")]
    assert [d[1] for d in destroys] == [
        "/v1/erp/auth/approle-apps/role/erp-staging-web/secret-id-accessor/destroy"
    ]
    assert destroys[0][3] == {"secret_id_accessor": "accessor-wrap-1"}
    settled = state(rig, "web")
    assert (settled["current"], settled["candidate"], settled["retire"]) == (
        "accessor-wrap-2",
        None,
        [],
    )
    assert rig.bao.revoked  # the settlement session is revoked too


@pytest.mark.parametrize(
    "unit",
    [
        b"ActiveState=failed\nNRestarts=0\nActiveEnterTimestampMonotonic=1000\n",
        b"ActiveState=active\nNRestarts=1\nActiveEnterTimestampMonotonic=1000\n",
        b"ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=2000\n",
        b"",
    ],
)
def test_an_unstable_unit_keeps_its_predecessors_valid(
    tmp_path: Path, unit: bytes
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    deliver_again(rig, "dispatch-2")
    rig.systemctl.script[("show",)] = CommandResult(0, unit)
    idle_cycle(rig, SETTLE + 5)
    assert not any(r[1].endswith("/destroy") for r in rig.bao.requests)
    assert state(rig, "web")["retire"] == ["accessor-wrap-1"]


def test_an_accessor_that_cannot_be_destroyed_is_kept_not_forgotten(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.launcher.run_once()
    deliver_again(rig, "dispatch-2")
    rig.bao.fail_destroy = True
    idle_cycle(rig, SETTLE + 5)
    assert state(rig, "web")["retire"] == ["accessor-wrap-1"]


def test_a_candidate_left_at_issued_by_a_crash_is_dropped_before_minting_again(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    store = rig.config.state_dir / "accessors"
    store.mkdir(mode=0o700, exist_ok=True)
    (store / "web.json").write_text(
        json.dumps(
            {
                "version": 1,
                "current": "accessor-old",
                "candidate": {
                    "accessor": "accessor-lost",
                    "stage": "issued",
                    "at": 1_900_000_000,  # minted AFTER the unit last entered active
                    "nrestarts": None,
                    "active_enter": None,
                },
                "retire": [],
            }
        )
    )
    secret_id_file(rig, "web").write_text("maybe-the-lost-one")
    secret_id_file(rig, "web").chmod(0o400)
    rig.launcher.run_once()
    destroys = [r[3] for r in rig.bao.requests if r[1].endswith("/destroy")]
    assert {"secret_id_accessor": "accessor-lost"} in destroys
    assert (
        "accessor-old" in state(rig, "web")["retire"]
    )  # superseded, not yet destroyed


@pytest.mark.parametrize(
    "content", ["not json", '{"version": 2}', '{"version": 1}', "[]"]
)
def test_corrupt_accessor_state_is_a_refusal_never_an_empty_list(
    tmp_path: Path, content: str
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    store = rig.config.state_dir / "accessors"
    store.mkdir(mode=0o700, exist_ok=True)
    (store / "web.json").write_text(content)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "accessor_state_invalid")
    assert "/v1/sys/wrapping/unwrap" not in rig.bao.paths


def test_single_use_roles_have_no_accessor_bookkeeping(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, MIGRATE)
    rig.launcher.run_once()
    assert not (rig.config.state_dir / "accessors" / "migrate.json").exists()


# -- one naming key (H1), pinned naming (M5), optional naming (L1) ---------

REAL_PAIRS = [
    ("dotmac-erp-v2/staging/host-a", "erp"),
    ("dotmac-integrator-companion/staging/host-a", "erp-integrator"),
]


@pytest.mark.parametrize(("target", "namespace"), REAL_PAIRS)
def test_every_per_role_artefact_is_named_by_the_secrets_namespace_not_the_product(
    tmp_path: Path, target: str, namespace: str
) -> None:
    """product_code and secrets_namespace differ for the real products; role dir,
    unit, authored HCL and OpenBao names must all follow secrets_namespace."""
    rig = make_rig(tmp_path, target_ref=target, secrets_namespace=namespace)
    assert rig.config.product != namespace
    prepare_role(rig, "web")
    rig.cp.materials = make_materials(
        target_ref=target, roles=WEB, secrets_namespace=namespace
    )
    result = rig.launcher.run_once()
    assert result.outcome == "completed", result
    # role dir
    assert (
        secret_id_file(rig, "web")
        == rig.config.secrets_root / namespace / "web" / "secret_id"
    )
    assert secret_id_file(rig, "web").read_text() == FakeBaoTransport.SECRET
    assert not (rig.config.secrets_root / rig.config.product).exists()
    # unit (and so the polkit entry and the drop-in)
    restart = next(c for c in rig.systemctl.calls if "restart" in c)
    assert restart[-1] == f"openbao-agent@{namespace}-web.service"
    # authored HCL, reported by digest
    assert rig.cp.observations[0]["details"]["openbao_config"]["web"].startswith(
        "sha256:"
    )  # type: ignore[index]
    # OpenBao
    assert rig.bao.paths[1] == (
        f"/v1/{namespace}/auth/approle-apps/role/{namespace}-staging-web/secret-id"
    )


def test_a_plan_whose_naming_differs_from_the_pinned_config_is_refused(
    tmp_path: Path,
) -> None:
    for change in (
        {"secrets_namespace": "erp-integrator"},
        {"secrets_environment": "prod"},
    ):
        rig = deliver_rig(
            tmp_path / str(len(change)) / next(iter(change.values())), WEB
        )
        rig.cp.materials = make_materials(roles=WEB, **change)
        result = rig.launcher.run_once()
        assert (result.outcome, result.reason) == ("refused", "secrets_naming_mismatch")
        assert rig.bao.paths == [] and rig.runner.specs == []


@pytest.mark.parametrize(
    "change",
    [
        {"secrets_namespace": None},
        {"secrets_environment": None},
        {"secrets_namespace": "Erp"},
        {"secrets_environment": "Prod"},
        {"secrets_namespace": "erp/x"},
    ],
)
def test_a_plan_with_roles_must_name_a_valid_secrets_namespace_and_env(
    tmp_path: Path, change: dict[str, object]
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.cp.materials = make_materials(roles=WEB, **change)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_invalid")
    assert rig.bao.paths == []


def test_a_plan_without_roles_may_carry_a_null_namespace_env(tmp_path: Path) -> None:
    """Control emits a namespace with a NULL namespace-env when no role is bound."""
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(secrets_environment=None)
    assert rig.launcher.run_once().outcome == "completed"
    rig.cp.materials = make_materials(
        dispatch_id="dispatch-2", secrets_namespace=None, secrets_environment=None
    )
    assert rig.launcher.run_once().outcome == "completed"
    assert rig.bao.paths == []


def test_a_role_directory_that_enrolment_did_not_create_is_refused(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path)
    (rig.config.openbao_config_dir / "erp-web.hcl").write_text(AUTHORED)
    (rig.config.openbao_config_dir / "erp-web.hcl").chmod(0o644)
    rig.cp.materials = make_materials(roles=WEB)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "runtime_dir_unsafe")
    assert not rig.config.role_dir("web").exists()  # the agent never creates it


def test_a_role_directory_with_group_or_world_read_is_refused(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    rig.config.role_dir("web").chmod(0o755)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "runtime_dir_unsafe")


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


def test_authored_configs_are_checked_and_their_digest_reported(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    assert rig.launcher.run_once().outcome == "completed"
    digest = rig.cp.observations[0]["details"]["openbao_config"]["web"]  # type: ignore[index]
    assert digest.startswith("sha256:") and len(digest) == 71
    authored = rig.config.openbao_config_dir / "erp-web.hcl"
    assert authored.read_text() == AUTHORED


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
    assert rig.bao.paths == []


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


def test_foundation_gets_a_per_run_credentials_directory_holding_only_pop_key(
    tmp_path: Path,
) -> None:
    """L6: Foundation reads `pop-key` to sign consume, not secret zero."""
    rig = make_rig(tmp_path)
    seen: dict[str, object] = {}

    def effect(spec) -> None:  # type: ignore[no-untyped-def]
        directory = Path(spec.credentials_directory)
        seen["names"] = sorted(p.name for p in directory.iterdir())
        seen["mode"] = (directory / "pop-key").stat().st_mode & 0o777
        seen["same_as_agent"] = (
            spec.credentials_directory == rig.environ["CREDENTIALS_DIRECTORY"]
        )

    rig.runner.effect = effect
    rig.cp.materials = make_materials()
    assert rig.launcher.run_once().outcome == "completed"
    assert seen == {"names": ["pop-key"], "mode": 0o400, "same_as_agent": False}
    assert not (rig.config.run_dir / "credentials").exists()  # removed after the run


def test_roles_come_only_from_the_signed_plan(tmp_path: Path) -> None:
    rig = deliver_rig(tmp_path, WEB)
    document = make_materials(roles=WEB)
    document["roles"] = [{"role": "evil", "single_use": False}]  # CP-supplied, unsigned
    rig.cp.materials = document
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_invalid")
    assert rig.bao.paths == [] and rig.runner.specs == []
    rig.cp.materials = make_materials(dispatch_id="dispatch-2")
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


def test_the_agents_run_directory_is_private_and_role_directories_are_traverse_only(
    tmp_path: Path,
) -> None:
    """The agent's own state is 0700 throughout; the tmpfiles.d role directories
    are traverse-only and every file is 0400-class (no group or other bit)."""
    rig = deliver_rig(tmp_path)
    seen: list[tuple[Path, int]] = []

    def snapshot(_spec) -> None:  # type: ignore[no-untyped-def]
        for root in (rig.config.run_dir, rig.config.secrets_root):
            seen.extend((p, p.lstat().st_mode) for p in sorted(root.rglob("*")))

    rig.runner.effect = snapshot
    rig.launcher.run_once()
    seen.extend(
        (p, p.lstat().st_mode) for p in sorted(rig.config.secrets_root.rglob("*"))
    )
    seen.append((rig.config.run_dir, rig.config.run_dir.lstat().st_mode))
    traverse_only = {
        rig.config.secrets_root / rig.config.secrets_namespace,
        *(rig.config.role_dir(str(r["role"])) for r in ROLES),
    }
    assert seen
    for path, mode in seen:
        if stat.S_ISDIR(mode):
            allowed = 0o066 if path in traverse_only else 0o077
            assert mode & allowed == 0, path
        else:
            assert mode & 0o077 == 0, path


def test_the_launcher_hands_the_runner_the_signed_plan_digest(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    rig.launcher.run_once()
    [spec] = rig.runner.specs
    assert spec.plan_digest.startswith("sha256:")
    assert spec.agent_config == rig.config.config_path
    assert dict(spec.bindings_env) == dict(rig.config.bindings_env)


# -- R2-L4, N1 ---------------------------------------------------------------


def _seed_issued(rig: Rig, at: int) -> None:
    store = rig.config.state_dir / "accessors"
    store.mkdir(mode=0o700, exist_ok=True)
    (store / "web.json").write_text(
        json.dumps(
            {
                "version": 1,
                "current": "accessor-old",
                "candidate": {
                    "accessor": "accessor-running",
                    "stage": "issued",
                    "at": at,
                    "nrestarts": None,
                    "active_enter": None,
                },
                "retire": [],
            }
        )
    )


def test_an_issued_candidate_the_unit_is_running_is_promoted_not_destroyed(
    tmp_path: Path,
) -> None:
    """A crash after a SUCCESSFUL restart leaves `issued`; the unit entered active
    after the mint, so it runs that id. Destroying it first would leave the unit
    on a dead id if the next mint failed. Minted 100 s ago: promoted and tracked,
    but not yet old enough for settlement to destroy anything."""
    rig = deliver_rig(tmp_path, WEB)
    _seed_issued(rig, at=1_799_999_900)
    rig.systemctl.script[("show",)] = CommandResult(
        0,
        b"ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=1000\n"
        b"ActiveEnterTimestamp=@1799999950\n",
    )
    rig.launcher.run_once()
    destroys = [r[3] for r in rig.bao.requests if r[1].endswith("/destroy")]
    assert destroys == []  # nothing destroyed: the running id is superseded, not killed
    now = state(rig, "web")
    # Both older ids wait on the list until the NEW candidate proves itself.
    assert sorted(now["retire"]) == ["accessor-old", "accessor-running"]  # type: ignore[arg-type]
    assert now["candidate"]["accessor"] == "accessor-wrap-1"  # type: ignore[index]


def test_an_issued_candidate_is_promoted_when_the_unit_start_time_is_unknown(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    _seed_issued(rig, at=1_700_000_000)
    rig.systemctl.script[("show",)] = CommandResult(0, b"ActiveState=active\n")
    rig.launcher.run_once()
    destroys = [r[3] for r in rig.bao.requests if r[1].endswith("/destroy")]
    assert {"secret_id_accessor": "accessor-running"} not in destroys


def test_settlement_tracks_an_issued_candidate_the_unit_is_running(
    tmp_path: Path,
) -> None:
    rig = deliver_rig(tmp_path, WEB)
    _seed_issued(rig, at=1_799_999_900)  # 105 s before the cycle: not yet settled
    rig.systemctl.script[("show",)] = CommandResult(
        0,
        b"ActiveState=active\nNRestarts=2\nActiveEnterTimestampMonotonic=77\n"
        b"ActiveEnterTimestamp=@1799999950\n",
    )
    idle_cycle(rig, 5)
    candidate = state(rig, "web")["candidate"]
    assert candidate["stage"] == "restarted" and candidate["nrestarts"] == "2"  # type: ignore[index]
    assert not any(r[1].endswith("/destroy") for r in rig.bao.requests)


def test_promoting_a_settled_candidate_retires_the_id_it_replaces(
    tmp_path: Path,
) -> None:
    """A stable candidate replaces `current`; the old `current` is destroyed too,
    never dropped from tracking while still valid."""
    rig = deliver_rig(tmp_path, WEB)
    store = rig.config.state_dir / "accessors"
    store.mkdir(mode=0o700, exist_ok=True)
    (store / "web.json").write_text(
        json.dumps(
            {
                "version": 1,
                "current": "accessor-old",
                "candidate": {
                    "accessor": "accessor-new",
                    "stage": "restarted",
                    "at": 1_700_000_000,
                    "nrestarts": "0",
                    "active_enter": "1000",
                },
                "retire": ["accessor-older"],
            }
        )
    )
    idle_cycle(rig, 5)  # the default unit matches the recorded NRestarts/enter
    destroyed = [
        r[3]["secret_id_accessor"]
        for r in rig.bao.requests
        if r[1].endswith("/destroy")
    ]
    assert sorted(destroyed) == ["accessor-old", "accessor-older"]
    settled = state(rig, "web")
    assert (settled["current"], settled["candidate"], settled["retire"]) == (
        "accessor-new",
        None,
        [],
    )


@pytest.mark.parametrize(
    "hcl",
    [
        "template_config {\n  exit_on_retry_failure = true\n}\n",  # no exit_on_err
        'auto_auth {\n  method "approle" {\n    exit_on_err = true\n  }\n}\n',  # no retry
        AUTHORED.replace("exit_on_err = true", "exit_on_err = false"),
        AUTHORED.replace(
            "exit_on_retry_failure = true", "# exit_on_retry_failure = true"
        ),
        AUTHORED.replace("exit_on_err = true", "// exit_on_err = true"),
        "# authored config\n",
    ],
)
def test_an_authored_config_that_does_not_exit_on_auth_failure_is_refused(
    tmp_path: Path, hcl: str
) -> None:
    """N1: without both settings a bad secret_id leaves the Agent retrying while
    the unit stays active, so the settle check would destroy live predecessors."""
    rig = deliver_rig(tmp_path, WEB)
    (rig.config.openbao_config_dir / "erp-web.hcl").write_text(hcl)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "openbao_config_invalid")
    assert rig.bao.paths == []  # no login, no mint for an unsafe config


def test_both_exit_settings_are_accepted_in_any_spacing(tmp_path: Path) -> None:
    from dotmac_deploy_agent.openbao_config import missing_exit_settings

    assert missing_exit_settings(AUTHORED) == []
    assert (
        missing_exit_settings(
            "exit_on_err=true\nexit_on_retry_failure   =   true # ok\n"
        )
        == []
    )
    assert missing_exit_settings("") == ["exit_on_err", "exit_on_retry_failure"]
