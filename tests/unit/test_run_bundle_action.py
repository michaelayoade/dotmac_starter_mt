"""Focused contract plants for the pinned dependency-bundle action runner."""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import run_bundle_action as runner


def _plan(path: Path, filename: str, content: bytes) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_digest": "a" * 64,
                "artifacts": [
                    {
                        "package_normalised_name": "example",
                        "filename": filename,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_load_expected_accepts_exact_product_policy_document(tmp_path: Path) -> None:
    plan = tmp_path / "expected.json"
    _plan(plan, "example-1.whl", b"wheel")
    expected = runner.load_expected(plan)
    assert expected.plan_digest == "a" * 64
    assert expected.artifacts[0].filename == "example-1.whl"


def test_load_expected_refuses_extra_and_duplicate_contract_keys(
    tmp_path: Path,
) -> None:
    plan = tmp_path / "expected.json"
    plan.write_text(
        '{"schema_version":1,"schema_version":1,"plan_digest":"a",'
        '"artifacts":[],"extra":true}',
        encoding="utf-8",
    )
    with pytest.raises(runner.BundleActionError, match="duplicate"):
        runner.load_expected(plan)


@pytest.mark.parametrize("schema, digest", [(True, "a" * 64), (1, 3)])
def test_load_expected_refuses_wrongly_typed_scalars(
    tmp_path: Path, schema: object, digest: object
) -> None:
    plan = tmp_path / "expected.json"
    plan.write_text(
        json.dumps({"schema_version": schema, "plan_digest": digest, "artifacts": []}),
        encoding="utf-8",
    )
    with pytest.raises(runner.BundleActionError):
        runner.load_expected(plan)


def test_load_expected_refuses_oversized_document(tmp_path: Path) -> None:
    plan = tmp_path / "expected.json"
    plan.write_bytes(b" " * (runner._MAX_EXPECTED_FILE_BYTES + 1))
    with pytest.raises(runner.BundleActionError, match="size cap"):
        runner.load_expected(plan)


def test_action_identity_refuses_a_caller_checkout_decoy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    action = tmp_path / "action"
    action.mkdir()
    decoy = tmp_path / "caller" / "scripts" / "run_bundle_action.py"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("# decoy\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTION_REPOSITORY", "ignored")
    monkeypatch.setenv("BUNDLE_ACTION_REPOSITORY", "michaelayoade/dotmac_starter_mt")
    monkeypatch.setenv("BUNDLE_ACTION_REF", "a" * 40)
    monkeypatch.setenv("GITHUB_ACTION_PATH", str(action))
    with pytest.raises(runner.BundleActionError, match="not this action"):
        runner._require_action_identity(decoy)


def test_construct_emits_an_exclusive_canonical_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"wheel"
    acquired = tmp_path / "acquired"
    acquired.mkdir()
    (acquired / "example-1.whl").write_bytes(content)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("example-1.whl", content)
    plan = tmp_path / "expected.json"
    _plan(plan, "example-1.whl", content)
    action_path = (
        Path(__file__).resolve().parents[2]
        / ".github/actions/verified-dependency-bundle"
    )
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.touch()
    monkeypatch.setenv("BUNDLE_ACTION_REPOSITORY", "michaelayoade/dotmac_starter_mt")
    monkeypatch.setenv("BUNDLE_ACTION_REF", "a" * 40)
    monkeypatch.setenv("GITHUB_ACTION_PATH", str(action_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/bundle")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "7")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "11")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "acme/bundle/.github/workflows/bundle.yml@refs/heads/main",
    )
    assert (
        runner.main(
            [
                "--operation",
                "construct",
                "--expected-file",
                str(plan),
                "--producer-repository",
                "acme/bundle",
                "--producer-repository-id",
                "7",
                "--producer-workflow-path",
                ".github/workflows/bundle.yml",
                "--archive-artifact-name",
                "bundle",
                "--sidecar-artifact-name",
                "sidecar",
                "--archive-artifact-id",
                "12",
                "--acquired-dir",
                str(acquired),
                "--archive-path",
                str(archive),
                "--environment-name",
                "production",
            ]
        )
        == 0
    )
    manifest_path = Path(output.read_text(encoding="utf-8").split("=", 1)[1].strip())
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert (
        json.loads(manifest_path.read_text(encoding="utf-8"))["run"]["artifact_id"]
        == 12
    )


def _observation(artifact_id: int, artifact_name: str) -> dict[str, object]:
    return {
        "repository_id": 7,
        "repository_full_name": "acme/bundle",
        "head_repository_id": 7,
        "head_repository_full_name": "acme/bundle",
        "run_id": 11,
        "run_attempt": 1,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "workflow_id": 9,
        "workflow_observed_id": 9,
        "workflow_observed_path": ".github/workflows/bundle.yml",
        "workflow_path": ".github/workflows/bundle.yml",
        "status": "completed",
        "conclusion": "success",
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": "sha256:" + "d" * 64,
        "artifact_size_in_bytes": 123,
        "artifact_run_id": 11,
        "artifact_repository_id": 7,
        "artifact_head_repository_id": 7,
        "artifact_head_sha": "b" * 40,
        "artifact_head_branch": "main",
    }


def test_verify_and_index_uses_real_bundle_and_sidecar_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"wheel"
    source = tmp_path / "source"
    source.mkdir()
    wheel = source / "example-1.whl"
    wheel.write_bytes(content)
    plan = tmp_path / "expected.json"
    _plan(plan, wheel.name, content)
    expected = runner.load_expected(plan)
    archive_observation = _observation(2, "bundle")
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as bundle:
        bundle.writestr(wheel.name, content)
    manifest = runner.create_bundle_manifest(
        expected=expected,
        acquired_files={wheel.name: wheel},
        archive_path=inner,
        run={
            "repository_full_name": "acme/bundle",
            "repository_id": 7,
            "workflow_path": ".github/workflows/bundle.yml",
            "run_id": 11,
            "run_attempt": 1,
            "trusted_workflow_sha": "b" * 40,
            "artifact_id": 2,
            "artifact_name": "bundle",
            "artifact_run_id": 11,
            "environment_name": "production",
        },
    )
    outer = tmp_path / "archive.zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("bundle.zip", inner.read_bytes())
    sidecar = tmp_path / "sidecar.zip"
    with zipfile.ZipFile(sidecar, "w") as archive:
        archive.writestr("bundle-manifest.json", json.dumps(manifest))
    sidecar_observation = _observation(3, "sidecar")

    def acquire(_policy, _run_id, artifact_id, _token, _destination):
        if artifact_id == 2:
            return archive_observation, outer
        return sidecar_observation, sidecar

    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.touch()
    action_path = (
        Path(__file__).resolve().parents[2]
        / ".github/actions/verified-dependency-bundle"
    )
    monkeypatch.setattr(runner, "acquire_observed_artifact", acquire)
    monkeypatch.setenv("BUNDLE_ACTION_REPOSITORY", "michaelayoade/dotmac_starter_mt")
    monkeypatch.setenv("BUNDLE_ACTION_REF", "a" * 40)
    monkeypatch.setenv("GITHUB_ACTION_PATH", str(action_path))
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("BUNDLE_GITHUB_TOKEN", "placeholder")
    assert (
        runner.main(
            [
                "--operation",
                "verify-and-index",
                "--expected-file",
                str(plan),
                "--producer-repository",
                "acme/bundle",
                "--producer-repository-id",
                "7",
                "--producer-workflow-path",
                ".github/workflows/bundle.yml",
                "--archive-artifact-name",
                "bundle",
                "--sidecar-artifact-name",
                "sidecar",
                "--archive-artifact-id",
                "2",
                "--run-id",
                "11",
                "--sidecar-artifact-id",
                "3",
            ]
        )
        == 0
    )
    index = Path(output.read_text(encoding="utf-8").split("=", 1)[1].strip())
    assert (index / "simple" / "example" / "index.html").is_file()


def test_verify_refuses_a_sidecar_observed_from_a_different_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = tmp_path / "expected.json"
    _plan(plan, "example-1.whl", b"wheel")
    archive_observation = _observation(2, "bundle")
    sidecar_observation = _observation(3, "sidecar")
    sidecar_observation["run_id"] = 12
    sidecar_observation["artifact_run_id"] = 12
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(mode=0o700)
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("BUNDLE_GITHUB_TOKEN", "placeholder")

    def acquire(_policy, _run_id, artifact_id, _token, _destination):
        return (
            (archive_observation, tmp_path / "archive")
            if artifact_id == 2
            else (
                sidecar_observation,
                tmp_path / "sidecar",
            )
        )

    monkeypatch.setattr(runner, "acquire_observed_artifact", acquire)
    args = Namespace(
        producer_repository="acme/bundle",
        producer_repository_id="7",
        producer_workflow_path=".github/workflows/bundle.yml",
        archive_artifact_name="bundle",
        sidecar_artifact_name="sidecar",
        archive_artifact_id="2",
        sidecar_artifact_id="3",
        run_id="11",
    )
    with pytest.raises(ValueError, match="observations disagree"):
        runner._verify(args, runner.load_expected(plan))


def test_outer_bundle_refuses_a_compression_ratio_bomb_before_inner_zip_parse(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "archive.zip"
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bundle.zip", b"\0" * (1024 * 1024))
    destination = tmp_path / "bundle.zip"
    with pytest.raises(runner.BundleActionError, match="compression ratio"):
        runner._extract_outer_bundle(outer, destination)
    assert not destination.exists()


def test_outer_bundle_accepts_an_ordinary_single_member_zip(tmp_path: Path) -> None:
    outer = tmp_path / "archive.zip"
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("bundle.zip", b"ordinary inner bundle")
    destination = tmp_path / "bundle.zip"
    assert runner._extract_outer_bundle(outer, destination) == destination
    assert destination.read_bytes() == b"ordinary inner bundle"


def test_action_yaml_uses_own_script_without_expression_in_run_body() -> None:
    action = (
        Path(__file__).resolve().parents[2]
        / ".github/actions/verified-dependency-bundle/action.yml"
    ).read_text(encoding="utf-8")
    run_body = action.split("      run: |\n", 1)[1]
    assert "$GITHUB_ACTION_PATH/../../../scripts/run_bundle_action.py" in run_body
    assert "${{" not in run_body
    assert "--github-token" not in action
