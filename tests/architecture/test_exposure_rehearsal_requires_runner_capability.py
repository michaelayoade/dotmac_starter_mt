"""`exposure-rehearsal.yml`'s `preflight` job must refuse a source whose
runner cannot produce a rehearsal receipt at all, before it ever queries the
self-hosted control runner.

`Lane3RunnerCapability.v1` (`scripts/lane3_runner_capability.py`) already
answers "could this SOURCE ever produce a rehearsal receipt?" and already
gates `foundation-candidate.yml`'s build. It gated nothing in
`exposure-rehearsal.yml` — Lane 3's own actual rehearsal-execution workflow —
so a candidate that had already cleared the build-time check could still be
dispatched into a rehearsal a control runner is queried and a host lease is
held for, on a revision the runner is structurally unable to report on. This
file proves the gap is closed: the same invocation runs in `preflight`,
strictly before `require_runner.py`, unconditionally, and its artifact upload
matches `foundation-candidate.yml`'s own shape byte for byte.

## Why this file walks PARSED YAML rather than grepping

`test_deployment_release_lane.py` establishes the reason once and it applies
here without change: a `#`-comment or an explanatory prose string can contain
the same substrings a raw-text search is looking for — this very workflow's
own header comments discuss `lane3_runner_capability.py`,
`require_runner.py` and `upload-artifact` at length to justify the steps
below, and a grep over the raw file text cannot tell an invocation from a
sentence describing one. A YAML `#` comment is discarded by the parser before
it ever reaches the data structure PyYAML hands back, so walking that parsed
tree with `yaml.safe_load` is blind to a comment by construction rather than
by a heuristic that has to remember to strip it — the exact property this
file's checks depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPOSURE_REHEARSAL_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"
)
FOUNDATION_CANDIDATE_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "foundation-candidate.yml"
)

CAPABILITY_SCRIPT = "scripts/lane3_runner_capability.py"
RUNNER_LIVENESS_SCRIPT = "scripts/require_runner.py"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    return list(workflow["jobs"][job_name]["steps"])


def _step_invoking(steps: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    matches = [step for step in steps if needle in str(step.get("run", ""))]
    assert (
        len(matches) == 1
    ), f"expected exactly one step invoking {needle!r}, found {len(matches)}"
    return matches[0]


def _index_invoking(steps: list[dict[str, Any]], needle: str) -> int:
    matches = [
        index for index, step in enumerate(steps) if needle in str(step.get("run", ""))
    ]
    assert (
        len(matches) == 1
    ), f"expected exactly one step invoking {needle!r}, found {len(matches)}"
    return matches[0]


# ── 5. sensitivity: both files actually parse to something non-trivial ──────


def test_both_workflows_parse_to_non_trivial_documents_with_a_jobs_key() -> None:
    """A YAML-parsing regression in either file must fail loudly here rather
    than silently matching nothing in every check below."""
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)
    assert isinstance(exposure, dict) and exposure.get("jobs"), (
        "exposure-rehearsal.yml did not parse to a dict with a non-empty " "'jobs' key"
    )
    assert isinstance(candidate, dict) and candidate.get("jobs"), (
        "foundation-candidate.yml did not parse to a dict with a non-empty "
        "'jobs' key"
    )
    assert "preflight" in exposure["jobs"]
    assert "candidate" in candidate["jobs"]


# ── 1. both workflows invoke the same script, with the same argument shape ──


def test_both_workflows_invoke_the_capability_script_with_root_dot() -> None:
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)

    exposure_step = _step_invoking(_steps(exposure, "preflight"), CAPABILITY_SCRIPT)
    candidate_step = _step_invoking(_steps(candidate, "candidate"), CAPABILITY_SCRIPT)

    for step, workflow_name in (
        (exposure_step, "exposure-rehearsal.yml"),
        (candidate_step, "foundation-candidate.yml"),
    ):
        run = str(step["run"])
        assert "--root ." in run, (
            f"{workflow_name}'s capability-check step does not pass --root ., "
            f"got: {run!r}"
        )
        assert "--out lane3-runner-capability.json" in run, workflow_name
        assert '--summary "${GITHUB_STEP_SUMMARY}"' in run, workflow_name


# ── 2. exposure-rehearsal.yml runs it in preflight, before require_runner.py ─


def test_capability_check_runs_in_preflight_before_the_runner_liveness_check() -> None:
    workflow = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    steps = _steps(workflow, "preflight")

    capability_index = _index_invoking(steps, CAPABILITY_SCRIPT)
    liveness_index = _index_invoking(steps, RUNNER_LIVENESS_SCRIPT)

    assert capability_index < liveness_index, (
        "the capability check must run before the control-runner liveness "
        "check — refusing after querying the runner would spend a "
        "runner-availability check (and a held host lease downstream) on a "
        "revision that was never eligible to rehearse"
    )


# ── 3. the capability-check step cannot be silenced ──────────────────────────


def test_capability_check_step_is_unconditional_and_can_fail_the_job() -> None:
    workflow = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    step = _step_invoking(_steps(workflow, "preflight"), CAPABILITY_SCRIPT)

    assert step.get("continue-on-error") is not True, (
        "the capability-check step tolerates a NOT_CAPABLE verdict via "
        "continue-on-error, which is exactly what must not happen"
    )
    assert "if" not in step, (
        "the capability-check step is gated by an `if:` condition, which "
        "could skip it entirely and let a NOT_CAPABLE verdict pass silently — "
        "mirror foundation-candidate.yml's own unconditional step"
    )


# ── 4. the artifact upload matches foundation-candidate.yml's shape ─────────


def test_capability_record_upload_matches_foundation_candidates_shape() -> None:
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)

    exposure_steps = _steps(exposure, "preflight")
    candidate_steps = _steps(candidate, "candidate")

    capability_step = _step_invoking(exposure_steps, CAPABILITY_SCRIPT)
    capability_run = str(capability_step["run"])
    assert "--out lane3-runner-capability.json" in capability_run

    def _upload_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
        matches = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact")
            and step.get("with", {}).get("name") == "lane3-runner-capability"
        ]
        assert len(matches) == 1, (
            "expected exactly one lane3-runner-capability upload-artifact step, "
            f"found {len(matches)}"
        )
        return matches[0]

    exposure_upload = _upload_step(exposure_steps)
    candidate_upload = _upload_step(candidate_steps)

    assert exposure_upload["uses"] == candidate_upload["uses"], (
        "exposure-rehearsal.yml's upload-artifact pin does not match "
        "foundation-candidate.yml's — verify the current pin in "
        "foundation-candidate.yml rather than assuming it is unchanged"
    )
    assert exposure_upload.get("if") == "always()"
    assert exposure_upload["with"]["retention-days"] == 90
    assert exposure_upload["with"]["if-no-files-found"] == "error"
    assert exposure_upload["with"]["path"] == "lane3-runner-capability.json"
    # The artifact's declared `path` must be the exact file the capability
    # check step wrote via `--out`, not merely a similarly-named string.
    assert exposure_upload["with"]["path"] in capability_run
