"""dotmac-deploy-agent: params.env render, refusals, post-run classification."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotmac_deploy_agent.canon import digest
from dotmac_deploy_agent.params import (
    Param,
    PostRun,
    canonical_render,
    classify_post_run,
    refuse_preexisting_overrides,
    render_digest,
    with_app_image,
)
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_support import (
    APP_IMAGE,
    PREVIOUS_IMAGE,
    base_params,
    make_materials,
    make_rig,
    to_params,
)


def test_canonical_render_golden_vector() -> None:
    """Pinned bytes: Control (WP2) must render exactly this; APP_IMAGE sorts in."""
    render = canonical_render(
        [
            Param("b", "ZED", "string", "z"),
            Param("a", "APP_PORT", "int", "8080"),
            Param("n", None, "string", "ignored"),
        ],
        "img@sha256:" + "0" * 64,
    )
    assert render == (
        b"APP_IMAGE=img@sha256:" + b"0" * 64 + b"\nAPP_PORT=8080\nZED=z\n"
    )
    assert render_digest(render) == digest(render)


@pytest.mark.parametrize(
    "value",
    [
        "ghp_" + "A" * 36,
        "github_pat_abcdefghijklmnopqrstuv",
        "-----BEGIN PRIVATE KEY-----",
        "hvs.CAESIabcdefghijkl",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
        "postgres://user:pw@db.example.invalid/x",
        "Qm9yZWRPZlNlY3JldHNBbmRUb2tlbnMxMjM0NTY3ODkwQUJDREVG",
    ],
)
def test_render_refuses_secret_shaped(value: str) -> None:
    with pytest.raises(Refusal) as caught:
        canonical_render([Param("p", "SOME_VALUE", "string", value)], APP_IMAGE)
    assert caught.value.code == "params_secret_shaped"


def test_render_refuses_secret_typed_and_secret_named_material() -> None:
    with pytest.raises(Refusal) as typed:
        canonical_render([Param("p", "X", "secret", "v")], APP_IMAGE)
    assert typed.value.code == "params_secret_shaped"
    with pytest.raises(Refusal) as named:
        canonical_render([Param("p", "DB_PASSWORD", "string", "v")], APP_IMAGE)
    assert named.value.code == "params_secret_shaped"
    # A secret-NAMED material that points at a file is the ruled shape.
    canonical_render([Param("p", "SECRETS_DIR_APP", "path", "/run/x")], APP_IMAGE)


@pytest.mark.parametrize(
    "value",
    ["a\nb", "a\rb", "a\x00b", " lead", "trail ", '"quoted"', "'quoted'", ""],
)
def test_render_refuses_values_foundation_would_alter(value: str) -> None:
    with pytest.raises(Refusal) as caught:
        canonical_render([Param("p", "SOME_VALUE", "string", value)], APP_IMAGE)
    assert caught.value.code == "params_value_invalid"


def test_render_refuses_app_image_collision_and_duplicates() -> None:
    for params in (
        [Param("p", "APP_IMAGE", "string", "x")],
        [Param("p", "A", "string", "1"), Param("q", "A", "string", "2")],
        [Param("p", "lower", "string", "1")],
    ):
        with pytest.raises(Refusal):
            canonical_render(params, APP_IMAGE)


def test_post_run_classifies_two_renderings(tmp_path: Path) -> None:
    planned = canonical_render(to_params(base_params()), APP_IMAGE)
    previous = with_app_image(planned, PREVIOUS_IMAGE)
    path = tmp_path / "params.env"

    def put(content: bytes, mode: int) -> None:
        if path.exists() or path.is_symlink():
            path.unlink()
        path.write_bytes(content)
        path.chmod(mode)

    put(planned, 0o400)
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.PLANNED
    )
    put(planned, 0o600)  # Foundation's pin rewrite changes only the mode
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.PLANNED
    )
    put(previous, 0o600)  # Foundation's own restore after a failed switch
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.PLANNED_PREVIOUS_PIN
    )
    # With no previous pin recorded the second rendering is not acceptable.
    assert (
        classify_post_run(path, planned_render=planned, previous_app_image=None)
        is PostRun.INTEGRITY_VIOLATION
    )
    put(planned + b"EXTRA=1\n", 0o600)
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.INTEGRITY_VIOLATION
    )
    put(planned, 0o644)  # wider than Foundation ever leaves it
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.INTEGRITY_VIOLATION
    )
    path.unlink()
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.INTEGRITY_VIOLATION
    )
    path.symlink_to(tmp_path / "elsewhere")
    assert (
        classify_post_run(
            path, planned_render=planned, previous_app_image=PREVIOUS_IMAGE
        )
        is PostRun.INTEGRITY_VIOLATION
    )


def test_refuse_dotenv_present(tmp_path: Path) -> None:
    refuse_preexisting_overrides(tmp_path)
    (tmp_path / ".env").write_text("A=1\n")
    with pytest.raises(Refusal) as caught:
        refuse_preexisting_overrides(tmp_path)
    assert caught.value.code == "dotenv_present"


def test_refuse_compose_override_present(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
    with pytest.raises(Refusal) as caught:
        refuse_preexisting_overrides(tmp_path)
    assert caught.value.code == "compose_override_present"


def test_launcher_writes_0400_runs_then_deletes_and_reports(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert result.outcome == "completed"
    assert rig.runner.seen_mode == 0o400
    assert rig.runner.seen_params == canonical_render(
        to_params(base_params()), APP_IMAGE
    )
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    [observation] = rig.cp.observations
    assert observation["outcome"] == "completed"
    assert observation["details"]["params_env_post_run"] == "planned"  # type: ignore[index]
    assert rig.launcher.outbox.pending() == []


def test_launcher_refuses_params_digest_mismatch_and_never_runs(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(params_env_digest="sha256:" + "0" * 64)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "params_digest_mismatch")
    assert rig.runner.specs == []
    assert rig.cp.observations[0]["outcome"] == "refused"
    assert not (rig.config.deploy_run_dir / "params.env").exists()


def test_launcher_refuses_dotenv_present_and_never_runs(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    (rig.config.deploy_dir / ".env").write_text("A=1\n")
    rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert result.reason == "dotenv_present"
    assert rig.runner.specs == []


def test_launcher_refuses_secret_shaped_param(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    params = base_params()
    params[0]["value"] = "ghp_" + "A" * 36
    rig.cp.materials = make_materials(params=params)
    assert rig.launcher.run_once().reason == "params_secret_shaped"
    assert rig.runner.specs == []


def test_tampered_params_env_is_an_integrity_violation(tmp_path: Path) -> None:
    def tamper(spec) -> None:  # type: ignore[no-untyped-def]
        spec.params_env.chmod(0o600)
        spec.params_env.write_bytes(b"APP_IMAGE=evil\n")

    rig = make_rig(tmp_path, runner=None)
    rig.runner.effect = tamper
    rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == (
        "integrity_violation",
        "integrity_violation",
    )
    assert not (rig.config.deploy_run_dir / "params.env").exists()


def test_stale_files_are_removed_at_start(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.config.run_dir.mkdir(mode=0o700)
    rig.config.deploy_run_dir.mkdir(mode=0o700)
    stale = rig.config.deploy_run_dir / "params.env"
    stale.write_text("OLD=1\n")
    legacy = rig.config.deploy_run_dir / "legacy.env"
    legacy.write_text("OLD=1\n")
    rig.launcher.run_once()  # nothing pending
    assert not stale.exists() and not legacy.exists()


def test_failed_run_is_reported_and_files_removed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.runner.exit_code = 3
    rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert result.outcome == "failed"
    assert rig.cp.observations[0]["details"]["runner"]["exit_code"] == 3  # type: ignore[index]
    assert not (rig.config.deploy_run_dir / "params.env").exists()


def test_interrupted_run_is_reported_on_next_start(tmp_path: Path) -> None:
    from dotmac_deploy_agent.observation import mark_inflight

    rig = make_rig(tmp_path)
    mark_inflight(rig.config.state_dir, "dispatch-9")
    rig.launcher.run_once()
    [observation] = rig.cp.observations
    assert observation["dispatch_id"] == "dispatch-9"
    assert observation["details"] == {"interrupted": True}
    assert not (rig.config.state_dir / "inflight.json").exists()


def test_observation_is_kept_in_outbox_until_cp_acknowledges(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    rig.cp.overrides["observations"] = (409, {"error": "busy"})
    rig.launcher.run_once()
    assert len(rig.launcher.outbox.pending()) == 1
    del rig.cp.overrides["observations"]
    rig.cp.materials = None
    rig.launcher.run_once()
    assert rig.launcher.outbox.pending() == []
    assert os.listdir(rig.config.state_dir / "outbox") == []
