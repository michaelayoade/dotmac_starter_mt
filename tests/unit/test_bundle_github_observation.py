"""Hosted-CI contract tests for GitHub Actions run observation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "michaelayoade/dotmac_starter_mt"
WORKFLOW_PATH = ".github/workflows/dependency-bundle.yml"
ARTIFACT_NAME = "dependency-bundle"
TRANSPORT = importlib.util.spec_from_file_location(
    "github_actions_transport", ROOT / "scripts" / "github_actions_transport.py"
)
assert TRANSPORT and TRANSPORT.loader
transport = importlib.util.module_from_spec(TRANSPORT)
sys.modules["github_actions_transport"] = transport
TRANSPORT.loader.exec_module(transport)
SPEC = importlib.util.spec_from_file_location(
    "bundle_github_observation", ROOT / "scripts" / "bundle_github_observation.py"
)
assert SPEC and SPEC.loader
observation = importlib.util.module_from_spec(SPEC)
sys.modules["bundle_github_observation"] = observation
SPEC.loader.exec_module(observation)


def payloads() -> dict[str, dict]:
    run = {
        "id": 11,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "event": "workflow_dispatch",
        "workflow_id": 22,
        "path": WORKFLOW_PATH,
        "repository": {"id": 7, "full_name": REPOSITORY},
        "head_repository": {"id": 7, "full_name": REPOSITORY},
    }
    return {
        "run": run,
        "artifact": {
            "id": 33,
            "name": ARTIFACT_NAME,
            "digest": "sha256:" + "b" * 64,
            "size_in_bytes": 123,
            "expired": False,
            "workflow_run": {
                "id": 11,
                "repository_id": 7,
                "head_repository_id": 7,
                "head_sha": "a" * 40,
                "head_branch": "main",
            },
        },
        "workflow": {"id": 22, "path": WORKFLOW_PATH},
    }


POLICY = observation.TrustedProducerPolicy(
    repository_id=7,
    repository="michaelayoade/dotmac_starter_mt",
    workflow_path=WORKFLOW_PATH,
    artifact_name=ARTIFACT_NAME,
)


def manifest_run() -> dict[str, object]:
    return {
        "repository_id": 7,
        "repository_full_name": REPOSITORY,
        "run_id": 11,
        "run_attempt": 1,
        "artifact_id": 33,
        "artifact_run_id": 11,
        "workflow_path": WORKFLOW_PATH,
        "artifact_name": ARTIFACT_NAME,
        "environment_name": "protected-producer",
        "trusted_workflow_sha": "a" * 40,
    }


def fake_fetch(data: dict[str, dict], calls: list[str] | None = None):
    def fetch(path: str, _token: str) -> dict:
        if calls is not None:
            calls.append(path)
        if "/artifacts/" in path:
            return data["artifact"]
        if "/workflows/" in path:
            return data["workflow"]
        return data["run"]

    return fetch


@pytest.mark.parametrize("run_path", [WORKFLOW_PATH, WORKFLOW_PATH + "@main"])
def test_observe_accepts_matching_api_evidence(monkeypatch, run_path: str) -> None:
    data = payloads()
    data["run"]["path"] = run_path
    monkeypatch.setattr(observation, "get_json", fake_fetch(data))
    result = observation.observe(
        11, 33, "placeholder", POLICY, manifest_run=manifest_run()
    )
    assert result["head_sha"] == "a" * 40
    assert result["artifact_run_id"] == 11
    assert result["artifact_digest"] == "sha256:" + "b" * 64
    assert result["artifact_size_in_bytes"] == 123
    assert result["environment_verified"] is False


@pytest.mark.parametrize(
    "field", ["id", "run_attempt", "head_sha", "head_branch", "event", "workflow_id"]
)
def test_observe_rejects_forged_run_fields(monkeypatch, field: str) -> None:
    data = payloads()
    data["run"][field] = (
        99 if field in {"id", "run_attempt", "workflow_id"} else "forged"
    )
    monkeypatch.setattr(observation, "get_json", fake_fetch(data))
    with pytest.raises(observation.GitHubObservationError):
        observation.observe(11, 33, "placeholder", POLICY)


@pytest.mark.parametrize(
    ("target", "field", "bad", "message"),
    [
        ("repository", "id", 8, "repository ID is not trusted"),
        ("repository", "full_name", "other/repo", "trusted repository"),
        ("head_repository", "id", 8, "head repository ID"),
        ("head_repository", "full_name", "other/repo", "trusted repository"),
        ("run", "path", WORKFLOW_PATH + "@topic", "workflow path"),
        ("artifact", "id", 34, "locator"),
        ("artifact", "name", "other", "artifact identity"),
        ("artifact", "digest", "sha256:wrong", "artifact digest"),
        ("artifact", "size_in_bytes", 0, "artifact.size_in_bytes"),
        ("artifact", "expired", True, "artifact identity"),
        ("artifact_workflow_run", "id", 12, "artifact workflow run"),
        ("artifact_workflow_run", "repository_id", 8, "artifact workflow run"),
        ("artifact_workflow_run", "head_repository_id", 8, "artifact workflow run"),
        ("artifact_workflow_run", "head_sha", "b" * 40, "artifact workflow identity"),
        ("artifact_workflow_run", "head_branch", "topic", "artifact workflow identity"),
        ("workflow", "id", 23, "workflow ID"),
        ("workflow", "path", "other.yml", "workflow path"),
    ],
)
def test_observe_rejects_api_identity_drift(
    monkeypatch, target: str, field: str, bad: object, message: str
) -> None:
    data = payloads()
    node = (
        data["artifact"]["workflow_run"]
        if target == "artifact_workflow_run"
        else data["run"][target]
        if target in {"repository", "head_repository"}
        else data[target]
    )
    node[field] = bad
    monkeypatch.setattr(observation, "get_json", fake_fetch(data))
    with pytest.raises(observation.GitHubObservationError, match=message):
        observation.observe(11, 33, "placeholder", POLICY)


@pytest.mark.parametrize(
    ("field", "bad", "message"),
    [
        ("repository_id", 8, "repository_id"),
        ("repository_full_name", "other/repo", "repository_full_name"),
        ("run_id", 12, "run_id"),
        ("run_attempt", 2, "run_attempt"),
        ("artifact_id", 34, "artifact_id"),
        ("artifact_run_id", 12, "artifact_run_id"),
        ("workflow_path", "other.yml", "workflow path"),
        ("artifact_name", "other", "artifact_name"),
        ("trusted_workflow_sha", "b" * 40, "trusted workflow SHA"),
    ],
)
def test_observe_rejects_self_consistent_but_wrong_manifest(
    monkeypatch, field: str, bad: object, message: str
) -> None:
    data = payloads()
    manifest = manifest_run()
    manifest[field] = bad
    monkeypatch.setattr(observation, "get_json", fake_fetch(data))
    with pytest.raises(observation.GitHubObservationError, match=message):
        observation.observe(11, 33, "placeholder", POLICY, manifest_run=manifest)


@pytest.mark.parametrize("bad", [False, "11", 11.5])
def test_observe_refuses_id_coercion_before_fetch(monkeypatch, bad: object) -> None:
    calls: list[str] = []
    monkeypatch.setattr(observation, "get_json", fake_fetch(payloads(), calls))
    with pytest.raises(observation.GitHubObservationError, match="run_id"):
        observation.observe(bad, 33, "placeholder", POLICY)
    assert calls == []


def test_manifest_is_compared_only_after_live_fetch(monkeypatch) -> None:
    data = payloads()
    calls: list[str] = []

    monkeypatch.setattr(observation, "get_json", fake_fetch(data, calls))
    observation.observe(11, 33, "placeholder", POLICY, manifest_run=manifest_run())
    assert len(calls) == 3


def test_call_reachability_uses_transport_get_json(monkeypatch) -> None:
    data = payloads()
    seen: list[str] = []

    monkeypatch.setattr(observation, "get_json", fake_fetch(data, seen))
    observation.observe(11, 33, "placeholder", POLICY)
    assert seen == [
        "/repos/michaelayoade/dotmac_starter_mt/actions/runs/11",
        "/repos/michaelayoade/dotmac_starter_mt/actions/artifacts/33",
        "/repos/michaelayoade/dotmac_starter_mt/actions/workflows/22",
    ]
