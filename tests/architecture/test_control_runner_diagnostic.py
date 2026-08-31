"""The query token stays hosted before a no-repository-secret runner job."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts import verify_runner_token_boundary as boundary

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/control-runner-diagnostic.yml"
OWN = "michaelayoade/dotmac_starter_mt"
FOREIGN = "michaelayoade/dotmac_observability"
RUNNER = "control-runner-starter-mt"
LABEL = "dotmac-foundation-control"
REQUIRED_LABELS = (
    "self-hosted",
    "Linux",
    "X64",
    "dotmac-control-runner",
    LABEL,
)


def _document() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_diagnostic_is_dispatch_only_and_main_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    triggers = text[: text.index("jobs:")]
    assert "workflow_dispatch:" in triggers
    for forbidden in ("pull_request", "pull_request_target", "push:", "schedule:"):
        assert forbidden not in triggers
    assert text.count("if: github.ref == 'refs/heads/main'") == 2


def test_self_hosted_job_has_exact_labels_and_no_injected_secret_or_action() -> None:
    document = _document()
    assert document["permissions"] == {}
    assert "env" not in document
    job = document["jobs"]["diagnostic"]
    assert job["runs-on"] == list(REQUIRED_LABELS)
    body = json.dumps(job)
    assert "secrets." not in body
    assert '"uses"' not in body
    assert RUNNER in body
    run = job["steps"][0]["run"]
    for assertion in (
        'test "${RUNNER_OS}" = "Linux"',
        'test "${RUNNER_ARCH}" = "X64"',
        'test "$(id -u)" = "1002"',
        'test "$(id -un)" = "ghrun-starter"',
        '"ghrun-starter,ghrunners"',
        'test -n "${RUNNER_QUERY_TOKEN+x}"',
        "test -S /var/run/docker.sock",
    ):
        assert assertion in run


def test_query_secret_is_injected_only_in_the_hosted_boundary_job() -> None:
    document = _document()
    boundary_job = document["jobs"]["token-boundary"]
    assert boundary_job["runs-on"] == "ubuntu-latest"
    assert boundary_job["permissions"] == {"contents": "read"}
    body = json.dumps(boundary_job)
    assert "${{ secrets.RUNNER_QUERY_TOKEN }}" in body
    assert OWN in body
    assert FOREIGN in body
    for label in REQUIRED_LABELS:
        assert f"--label {label}" in body
    checkout = boundary_job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False
    diagnostic = document["jobs"]["diagnostic"]
    assert "env" not in diagnostic
    assert "secrets.RUNNER_QUERY_TOKEN" not in json.dumps(diagnostic)


def _runner(
    *, status: str = "online", labels: tuple[str, ...] = REQUIRED_LABELS
) -> dict:
    return {
        "name": RUNNER,
        "status": status,
        "labels": [{"name": label} for label in labels],
    }


def _argv() -> list[str]:
    argv = [
        "--own-repository",
        OWN,
        "--foreign-repository",
        FOREIGN,
        "--runner-name",
        RUNNER,
    ]
    for label in REQUIRED_LABELS:
        argv.extend(("--label", label))
    return argv


def _install_request(
    monkeypatch,
    *,
    own_status: int = 200,
    foreign_status: int = 403,
    runner: dict | None = None,
) -> None:
    response = json.dumps({"runners": [runner or _runner()]}).encode()

    def request(repository: str, token: str) -> tuple[int, bytes]:
        assert token == "opaque-test-value"
        if repository == OWN:
            return own_status, response
        assert repository == FOREIGN
        return foreign_status, b""

    monkeypatch.setenv("RUNNER_QUERY_TOKEN", "opaque-test-value")
    monkeypatch.setattr(boundary, "_request", request)


def test_boundary_refuses_anything_but_own_200_foreign_403(monkeypatch) -> None:
    _install_request(monkeypatch)
    assert boundary.main(_argv()) == 0


@pytest.mark.parametrize(
    ("own_status", "foreign_status"),
    ((403, 403), (200, 404), (200, 401), (200, 200)),
)
def test_boundary_refuses_every_other_status_pair(
    monkeypatch, own_status: int, foreign_status: int
) -> None:
    _install_request(
        monkeypatch,
        own_status=own_status,
        foreign_status=foreign_status,
    )
    assert boundary.main(_argv()) == 3


def test_boundary_refuses_an_offline_runner(monkeypatch) -> None:
    _install_request(monkeypatch, runner=_runner(status="offline"))
    assert boundary.main(_argv()) == 3


def test_boundary_refuses_a_runner_missing_one_required_label(monkeypatch) -> None:
    _install_request(monkeypatch, runner=_runner(labels=REQUIRED_LABELS[:-1]))
    assert boundary.main(_argv()) == 3
