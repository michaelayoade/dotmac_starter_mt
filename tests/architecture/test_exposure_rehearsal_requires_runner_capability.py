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

import pytest
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


# ── 1. both workflows invoke the EXACT SAME canonical command ───────────────


def _capability_run_text(workflow: dict[str, Any], job_name: str) -> str:
    step = _step_invoking(_steps(workflow, job_name), CAPABILITY_SCRIPT)
    return str(step.get("run", ""))


def test_both_workflows_invoke_the_capability_script_with_root_dot() -> None:
    """Exact canonical-command equality against `foundation-candidate.yml`,
    not substring/pattern matching.

    An independent review found the ORIGINAL version of this test — three
    `substring in run` assertions — passes on a commented-out invocation.
    `# python scripts/lane3_runner_capability.py --root . --out ... --summary
    ...` contains every one of those substrings while executing nothing: a
    `#`-prefixed line survives YAML PARSING (it is inside the `run:` block's
    own string value, not YAML syntax the parser discards — unlike a `#`
    comment in the YAML document ITSELF, which the module docstring above
    correctly says the parser is blind to). Exact string equality against
    `foundation-candidate.yml`'s own already-reviewed invocation closes this
    and every masking bypass in one property: ANY difference — a comment
    prefix, an appended `|| true`, an inserted `set +e`, a conditional
    wrapper — makes the two strings unequal, so no denylist of bypasses needs
    to be maintained or kept complete.
    """
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)

    exposure_run = _capability_run_text(exposure, "preflight")
    candidate_run = _capability_run_text(candidate, "candidate")

    assert exposure_run == candidate_run, (
        "exposure-rehearsal.yml's capability-check command does not exactly "
        "match foundation-candidate.yml's canonical invocation:\n"
        f"exposure-rehearsal.yml: {exposure_run!r}\n"
        f"foundation-candidate.yml: {candidate_run!r}"
    )
    # A positive control on the canonical text itself, so a change to BOTH
    # files in the same way (which exact equality alone cannot see) does not
    # silently drift the actual command shape this whole file assumes.
    assert "--root ." in candidate_run
    assert "--out lane3-runner-capability.json" in candidate_run
    assert '--summary "${GITHUB_STEP_SUMMARY}"' in candidate_run


def test_exact_equality_catches_a_planted_commented_out_invocation() -> None:
    """Sensitivity: the exact bypass the independent review named, proven
    directly rather than trusted from the reasoning alone."""
    canonical = (
        "python scripts/lane3_runner_capability.py \\\n"
        "  --root . \\\n"
        "  --out lane3-runner-capability.json \\\n"
        '  --summary "${GITHUB_STEP_SUMMARY}"\n'
    )
    commented = "# " + canonical
    assert CAPABILITY_SCRIPT in commented, "the plant must still contain the needle"
    assert commented != canonical


@pytest.mark.parametrize(
    "masking_suffix",
    (" || true", " || :", " || exit 0", "; exit 0", "\nset +e"),
)
def test_exact_equality_catches_each_planted_masking_pattern(
    masking_suffix: str,
) -> None:
    """Sensitivity: every masking pattern named across both review rounds —
    including `|| :` and `set +e`, which a hand-maintained denylist missed —
    is caught by exact equality without needing its own list entry."""
    canonical = f"python {CAPABILITY_SCRIPT} --root ."
    masked = canonical + masking_suffix
    assert masked != canonical


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


#: A hand-maintained denylist of shell-level exit-masking patterns was tried
#: here and rejected after TWO independent review rounds. Round 1 found
#: `step.get("continue-on-error") is not True` passes, wrongly, on
#: `continue-on-error: ${{ true }}` (a GitHub Actions expression STRING,
#: never the Python `True` singleton the check compared against) — fixed by
#: checking the key's PRESENCE, not its value, below. Round 2 found the
#: three-pattern denylist (`|| true`, `|| exit 0`, `; exit 0`) that replaced
#: it missed real, equally valid bypasses (`|| :`, `set +e`, a conditional
#: wrapper) — the exact "denylist wearing a structural claim" shape this
#: codebase's own `test_deployment_foundation_host_source_constructor_seam
#: .py` names and rejects elsewhere for an unrelated guard. A denylist can
#: only ever catch a bypass someone thought of. The `run:` text check below
#: is exact equality against `foundation-candidate.yml`'s own canonical
#: invocation (see `test_both_workflows_invoke_the_capability_script_with_
#: root_dot` above) instead — ANY difference, whatever shape it takes, fails
#: it, so no enumeration of bypass shapes is needed or kept.


def _declares_continue_on_error(step: dict[str, Any]) -> bool:
    """The KEY's mere presence is unsafe, regardless of its value. A literal
    YAML boolean `true` is unsafe, and so is any other value, because
    `continue-on-error: ${{ <any expression> }}` is parsed as a STRING (a
    GitHub Actions expression, evaluated at run time) — checking `value is
    not True` would pass on that string, since a string is never the `True`
    singleton. Presence, not value, is what must be refused."""
    return "continue-on-error" in step


def test_capability_check_step_is_unconditional_and_can_fail_the_job() -> None:
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)
    step = _step_invoking(_steps(exposure, "preflight"), CAPABILITY_SCRIPT)

    assert not _declares_continue_on_error(step), (
        "the capability-check step declares continue-on-error at all "
        f"(value: {step.get('continue-on-error')!r}) — any value, literal or "
        "a `${{ }}` expression, can tolerate a NOT_CAPABLE verdict; the key "
        "must be absent entirely"
    )
    assert "if" not in step, (
        "the capability-check step is gated by an `if:` condition, which "
        "could skip it entirely and let a NOT_CAPABLE verdict pass silently — "
        "mirror foundation-candidate.yml's own unconditional step"
    )
    # Exact equality, not a masking denylist — see the module-level comment
    # above for why. Any appended `|| true`, `|| :`, `set +e`, a conditional
    # wrapper, or a commented-out line all fail this the same way: they are
    # not byte-identical to the canonical, already-reviewed command.
    exposure_run = _capability_run_text(exposure, "preflight")
    candidate_run = _capability_run_text(candidate, "candidate")
    assert exposure_run == candidate_run, (
        "the capability-check step's command is not byte-identical to "
        f"foundation-candidate.yml's canonical invocation: {exposure_run!r} "
        f"!= {candidate_run!r}"
    )


def test_continue_on_error_detector_catches_a_planted_expression_value() -> None:
    """Sensitivity: proves `_declares_continue_on_error` catches the string-
    vs-`True` bypass directly, without needing a real workflow file."""
    assert _declares_continue_on_error({"continue-on-error": True})
    assert _declares_continue_on_error({"continue-on-error": "${{ true }}"})
    assert _declares_continue_on_error({"continue-on-error": False})
    assert not _declares_continue_on_error({"run": "python x.py"})


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
