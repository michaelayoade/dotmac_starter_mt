"""Lane 3 must refuse an absent runner, not queue against it.

`exposure-rehearsal.yml` targets `runs-on: [self-hosted, dotmac-control-runner]`
and that label is registered nowhere. GitHub does not fail a job whose
self-hosted label matches nothing — it QUEUES it for up to 24 hours and then
cancels it. So the symptom of "the runner does not exist" is byte-identical to
the symptom of "a rehearsal is already running", and the operator's natural
response (wait) is correct for one and wrong for the other.

Michael's requirement: *before dispatch, independently refuse if the expected
runner is absent or offline; do not rely on a queued job to diagnose runner
availability.*

## The structural property, and why it is not merely "a check exists"

A pre-flight that runs ON `dotmac-control-runner` is the same indefinite queue
with an extra step, so the load-bearing assertion here is not "something checks
the runner" but **where** it checks from: the refusing job must run on a
GitHub-hosted runner, and the rehearsal job must depend on it. Both halves are
asserted; either one alone is satisfiable by a broken arrangement.

The refusal logic itself is exercised against planted states below — absent,
offline, busy, and no-token — because a guard never observed failing is
indistinguishable from one that cannot fail (ADR-0018, `AGENTS.md` rule 25).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"
SCRIPT = PROJECT_ROOT / "scripts" / "require_runner.py"

CONTROL_LABEL = "dotmac-control-runner"
EXIT_REFUSED = 3


def _document() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _hosted(runs_on: object) -> bool:
    """True when `runs_on` is a GitHub-hosted label rather than self-hosted."""
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    return "self-hosted" not in {str(label) for label in labels}


def test_the_script_exists() -> None:
    assert SCRIPT.is_file(), "the pre-flight refusal script is missing"


def test_the_rehearsal_job_still_targets_the_control_runner() -> None:
    """The premise. If this stops holding, the rest of the file is about nothing."""
    runs_on = _document()["jobs"]["rehearse"]["runs-on"]
    assert CONTROL_LABEL in runs_on
    assert not _hosted(runs_on)


def test_the_rehearsal_depends_on_a_preflight() -> None:
    rehearse = _document()["jobs"]["rehearse"]
    needs = rehearse.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or ())
    assert needs, (
        "`rehearse` declares no `needs`, so it is dispatched straight at an "
        "unregistered self-hosted label and queues instead of failing"
    )
    assert "preflight" in needs


def test_the_preflight_runs_on_a_hosted_runner() -> None:
    """The whole point: it must not need the runner it is looking for."""
    preflight = _document()["jobs"]["preflight"]
    assert _hosted(preflight["runs-on"]), (
        "the pre-flight runs on a self-hosted runner, so if that runner is "
        "absent the CHECK queues too — an indefinite hang with an extra step"
    )


def test_the_preflight_actually_invokes_the_refusal() -> None:
    steps = _document()["jobs"]["preflight"]["steps"]
    body = "\n".join(str(step.get("run", "")) for step in steps)
    assert "require_runner.py" in body
    assert (
        CONTROL_LABEL in body
    ), "the pre-flight does not name the label it is meant to be checking"


def test_the_preflight_is_time_bounded() -> None:
    """A pre-flight without a timeout can itself become the hang."""
    assert _document()["jobs"]["preflight"]["timeout-minutes"] <= 15


# ── planted states: each must REFUSE ────────────────────────────────────────


def _run(
    payload: str | None,
    *,
    token: str | None = "stub-token",  # noqa: S107
) -> subprocess.CompletedProcess[str]:
    """Drive the real script with a stubbed `gh` on PATH."""
    directory = Path(tempfile.mkdtemp())
    stub = directory / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\ncat <<'JSON'\n" + (payload or "{}") + "\nJSON\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{directory}{os.pathsep}{environment['PATH']}"
    if token is None:
        environment.pop("RUNNER_QUERY_TOKEN", None)
    else:
        environment["RUNNER_QUERY_TOKEN"] = token
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-local stub
        [
            sys.executable,
            str(SCRIPT),
            "--repository",
            "o/r",
            "--label",
            "self-hosted",
            "--label",
            CONTROL_LABEL,
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _runner(name: str, *, status: str, busy: bool, labels: list[str]) -> dict:
    return {
        "name": name,
        "status": status,
        "busy": busy,
        "labels": [{"name": label} for label in labels],
    }


def _page(*runners: dict) -> str:
    return json.dumps({"runners": list(runners)})


def _control(*, status: str = "online", busy: bool = False) -> dict:
    """A runner carrying exactly the labels Lane 3 asks for."""
    return _runner(
        "c1", status=status, busy=busy, labels=["self-hosted", CONTROL_LABEL]
    )


def test_no_runners_at_all_refuses() -> None:
    result = _run(_page())
    assert result.returncode == EXIT_REFUSED
    assert "REFUSED" in result.stderr


def test_a_runner_without_the_control_label_refuses() -> None:
    """The registered-but-wrong case — a hosted-ish runner that cannot serve Lane 3."""
    result = _run(
        _page(
            _runner(
                "other", status="online", busy=False, labels=["self-hosted", "build"]
            )
        )
    )
    assert result.returncode == EXIT_REFUSED


def test_an_offline_runner_refuses() -> None:
    result = _run(_page(_control(status="offline")))
    assert result.returncode == EXIT_REFUSED
    assert "online" in result.stderr


def test_a_busy_runner_refuses() -> None:
    result = _run(_page(_control(busy=True)))
    assert result.returncode == EXIT_REFUSED
    assert "BUSY" in result.stderr


def test_a_missing_token_refuses_rather_than_skipping() -> None:
    """`could not determine` must never be reported as `available`."""
    result = _run(_page(), token=None)
    assert result.returncode == EXIT_REFUSED
    assert "RUNNER_QUERY_TOKEN" in result.stderr


def test_an_available_runner_passes() -> None:
    """Sensitivity's other half: the guard must not refuse everything."""
    result = _run(_page(_control()))
    assert result.returncode == 0, result.stderr
    assert "runner available" in result.stdout
