"""dotmac-deploy-agent: D1 delivery, OpenBao Agent config, runner environment."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from dotmac_deploy_agent.materials import BindingName, RoleBindings
from dotmac_deploy_agent.openbao_agent import render_agent_config
from dotmac_deploy_agent.runner import RunSpec, runner_environment

from tests.unit.deploy_agent_support import (
    FakeBaoTransport,
    Keys,
    make_config,
    make_materials,
    make_rig,
)

BINDINGS = [
    {
        "role": "app",
        "container_uid": None,
        "single_use": False,
        "names": [
            {"name": "DB_URL", "secret_ref": "bao://staging/erp/kv/app/DB_URL"},
            {"name": "API_KEY", "secret_ref": "bao://staging/erp/kv/app/API_KEY"},
        ],
    },
    {
        "role": "migrate",
        "container_uid": None,
        "single_use": True,
        "names": [
            {
                "name": "OWNER_URL",
                "secret_ref": "bao://staging/erp/kv/migrate/OWNER_URL",
            }
        ],
    },
]


def test_secret_ids_delivered_0400_and_never_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(bindings=BINDINGS)
    result = rig.launcher.run_once()
    assert result.outcome == "completed", result
    for role in ("app", "migrate"):
        secret_id = rig.config.run_dir / role / "secret_id"
        assert secret_id.read_text() == FakeBaoTransport.SECRET
        assert secret_id.stat().st_mode & 0o777 == 0o400
        assert (rig.config.run_dir / role / "role_id").stat().st_mode & 0o777 == 0o400
    assert FakeBaoTransport.SECRET not in repr(rig.cp.observations)
    assert FakeBaoTransport.SECRET not in caplog.text
    assert rig.cp.observations[0]["details"]["delivered_roles"] == [  # type: ignore[index]
        {"role": "app", "single_use": False},
        {"role": "migrate", "single_use": True},
    ]
    assert rig.bao.paths.count("/v1/sys/wrapping/unwrap") == 2


def test_chown_not_permitted_fails_closed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    foreign = os.geteuid() + 1 if os.geteuid() else 12345
    bindings = [dict(BINDINGS[0], container_uid=foreign)]
    rig.cp.materials = make_materials(bindings=bindings)
    result = rig.launcher.run_once()
    if os.geteuid() == 0:  # pragma: no cover - the agent is never root
        pytest.skip("chown succeeds as root")
    assert (result.outcome, result.reason) == ("refused", "secret_chown_failed")
    assert rig.runner.specs == []
    assert not (rig.config.run_dir / "app" / "secret_id").exists()


def test_bindings_without_a_delivery_client_refuse(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.launcher.openbao = None
    rig.cp.materials = make_materials(bindings=BINDINGS)
    assert rig.launcher.run_once().reason == "secret_delivery_failed"
    assert rig.runner.specs == []


def test_openbao_agent_config_is_golden_and_holds_no_value(tmp_path: Path) -> None:
    config = make_config(tmp_path, Keys())
    role = RoleBindings(
        "app",
        None,
        False,
        (
            BindingName("DB_URL", "staging", "erp", "app"),
            BindingName("API_KEY", "staging", "erp", "app"),
        ),
    )
    text = render_agent_config(config, role)
    base = str(config.run_dir / "app")
    assert (
        text
        == (
            "# Managed by dotmac-deploy-agent. Holds no secret value.\n"
            "vault {\n"
            '  address = "http://192.0.2.10:8200"\n'
            "}\n"
            "auto_auth {\n"
            '  method "approle" {\n'
            '    namespace = "staging/erp"\n'
            '    mount_path = "auth/approle"\n'
            "    config = {\n"
            f'      role_id_file_path = "{base}/role_id"\n'
            f'      secret_id_file_path = "{base}/secret_id"\n'
            "      remove_secret_id_file_after_reading = false\n"
            "    }\n"
            "  }\n"
            "}\n"
            "template_config {\n"
            "  exit_on_retry_failure = true\n"
            "}\n"
            "template {\n"
            '  contents = "{{ with secret \\"kv/data/erp/app\\" }}{{ .Data.data.API_KEY }}{{ end }}"\n'  # noqa: E501
            f'  destination = "{base}/secrets/API_KEY"\n'
            '  perms = "0400"\n'
            "  error_on_missing_key = true\n"
            "}\n"
            "template {\n"
            '  contents = "{{ with secret \\"kv/data/erp/app\\" }}{{ .Data.data.DB_URL }}{{ end }}"\n'  # noqa: E501
            f'  destination = "{base}/secrets/DB_URL"\n'
            '  perms = "0400"\n'
            "  error_on_missing_key = true\n"
            "}\n"
        )
    )
    single = RoleBindings("migrate", None, True, ())
    assert "remove_secret_id_file_after_reading = true" in render_agent_config(
        config, single
    )


def test_launcher_installs_agent_config_0600_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(bindings=BINDINGS)
    rig.launcher.run_once()
    path = rig.config.openbao_agent_config_dir / "erp-app.hcl"
    assert path.stat().st_mode & 0o777 == 0o600
    first = path.stat().st_mtime_ns
    rig.launcher.run_once()
    assert path.stat().st_mtime_ns == first  # unchanged bytes are not rewritten


def test_runner_environment_is_an_allow_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("OPENBAO_TOKEN", "x")
    spec = RunSpec(
        "d1",
        "erp/staging/host-a",
        "erp",
        tmp_path / "params.env",
        tmp_path / "legacy.env",
    )
    env = runner_environment(spec)
    assert set(env) == {
        "PATH", "LANG", "DOTMAC_DEPLOY_DISPATCH_ID", "DOTMAC_DEPLOY_TARGET_REF",
        "DOTMAC_DEPLOY_PRODUCT", "DOTMAC_DEPLOY_PARAMS_ENV", "DOTMAC_DEPLOY_LEGACY_ENV",
    }  # fmt: skip
