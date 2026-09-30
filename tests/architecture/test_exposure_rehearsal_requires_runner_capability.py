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


#: PINNED literals, independent of either workflow file. A THIRD independent
#: review round found that comparing exposure-rehearsal.yml's step only
#: against foundation-candidate.yml's (never against a fixed known-good
#: value) proves nothing if BOTH files are weakened the SAME way — appending
#: `|| true` to both keeps them equal to each other, and the three substring
#: checks that were the only "positive control" on the candidate side stay
#: satisfied too, since substring containment does not care what comes after.
#: Whole-dict equality against these literals closes that gap and, as a
#: side effect, also rejects any extra step key (`shell`, `env`,
#: `working-directory`) the earlier `run`-only / key-presence-only checks
#: never inspected.
CANONICAL_CAPABILITY_STEP: dict[str, Any] = {
    "name": "Refuse a source whose runner cannot produce a rehearsal receipt",
    "run": (
        "python scripts/lane3_runner_capability.py \\\n"
        "  --root . \\\n"
        "  --out lane3-runner-capability.json \\\n"
        '  --summary "${GITHUB_STEP_SUMMARY}"\n'
    ),
}

CANONICAL_UPLOAD_STEP: dict[str, Any] = {
    "name": "Upload the capability record",
    "if": "always()",
    "uses": "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "with": {
        "name": "lane3-runner-capability",
        "path": "lane3-runner-capability.json",
        "retention-days": 90,
        "if-no-files-found": "error",
    },
}


def _assert_step_matches_canonical(
    step: dict[str, Any], canonical: dict[str, Any], *, source: str
) -> None:
    """The step must be EXACTLY the canonical mapping — no extra key (a
    `shell:`, `env:` or `working-directory:` override that would let the
    script run under a no-op shell or a poisoned `PATH`/`BASH_ENV`; a
    `continue-on-error`/`if` gate), no missing key, no different value.

    Deliberately whole-dict equality against a PINNED literal, not a
    comparison between the two workflow files: two files weakened THE SAME
    WAY still equal each other, so equality-between-files alone proves the
    two files agree, never that either one is actually correct.
    """
    assert step == canonical, (
        f"{source}'s step does not exactly match the pinned canonical "
        f"mapping:\n{source}: {step!r}\ncanonical: {canonical!r}"
    )


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


def test_both_workflows_invoke_the_capability_script_with_root_dot() -> None:
    """Exact canonical-command equality between the two files, PLUS each
    file independently checked against a pinned literal.

    An independent review found the ORIGINAL version of this test — three
    `substring in run` assertions — passes on a commented-out invocation.
    `# python scripts/lane3_runner_capability.py --root . --out ... --summary
    ...` contains every one of those substrings while executing nothing: a
    `#`-prefixed line survives YAML PARSING (it is inside the `run:` block's
    own string value, not YAML syntax the parser discards — unlike a `#`
    comment in the YAML document ITSELF, which the module docstring above
    correctly says the parser is blind to).

    A THIRD independent review round then found that exact equality BETWEEN
    THE TWO FILES, on its own, does not prove either file is correct — only
    that they agree. Appending the identical `|| true` to both files keeps
    them equal to each other, and the three substring checks that used to be
    the only "positive control" here stay satisfied too, since substring
    containment does not care what comes after. `_assert_step_matches_
    canonical` below closes this: each file's step is checked against
    `CANONICAL_CAPABILITY_STEP`, a literal independent of either file.
    """
    exposure = _load_yaml(EXPOSURE_REHEARSAL_WORKFLOW)
    candidate = _load_yaml(FOUNDATION_CANDIDATE_WORKFLOW)

    exposure_step = _step_invoking(_steps(exposure, "preflight"), CAPABILITY_SCRIPT)
    candidate_step = _step_invoking(_steps(candidate, "candidate"), CAPABILITY_SCRIPT)

    assert exposure_step["run"] == candidate_step["run"], (
        "exposure-rehearsal.yml's capability-check command does not exactly "
        "match foundation-candidate.yml's canonical invocation:\n"
        f"exposure-rehearsal.yml: {exposure_step['run']!r}\n"
        f"foundation-candidate.yml: {candidate_step['run']!r}"
    )
    _assert_step_matches_canonical(
        exposure_step, CANONICAL_CAPABILITY_STEP, source="exposure-rehearsal.yml"
    )
    _assert_step_matches_canonical(
        candidate_step, CANONICAL_CAPABILITY_STEP, source="foundation-candidate.yml"
    )


def test_canonical_step_check_catches_a_planted_commented_out_invocation() -> None:
    """Sensitivity: the exact bypass the FIRST independent review round
    named, proven by calling the REAL checking function on a realistic
    mutated step — not by comparing two hand-written strings to each other,
    which a prior version of this test did and which never actually
    exercised `_assert_step_matches_canonical` or the loaded-YAML path."""
    mutated = dict(CANONICAL_CAPABILITY_STEP)
    mutated["run"] = "# " + mutated["run"]
    assert (
        CAPABILITY_SCRIPT in mutated["run"]
    ), "the plant must still contain the needle"
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            mutated, CANONICAL_CAPABILITY_STEP, source="planted"
        )


@pytest.mark.parametrize(
    "masking_suffix",
    (" || true", " || :", " || exit 0", "; exit 0", "\nset +e"),
)
def test_canonical_step_check_catches_each_planted_masking_pattern(
    masking_suffix: str,
) -> None:
    """Sensitivity: every masking pattern named across the first two review
    rounds — including `|| :` and `set +e`, which a hand-maintained denylist
    missed — is caught by the real checker without needing its own list
    entry. Runs the actual function, not a bare string comparison."""
    mutated = dict(CANONICAL_CAPABILITY_STEP)
    mutated["run"] = mutated["run"].rstrip("\n") + masking_suffix + "\n"
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            mutated, CANONICAL_CAPABILITY_STEP, source="planted"
        )


def test_canonical_step_check_catches_a_mirrored_weakening_of_both_files() -> None:
    """The exact gap the THIRD independent review round named: appending the
    SAME masking suffix to both files keeps them equal to EACH OTHER, so
    the cross-file equality check above would not catch it. The pinned
    canonical is what actually closes this."""
    weakened = dict(CANONICAL_CAPABILITY_STEP)
    weakened["run"] = weakened["run"].rstrip("\n") + " || true\n"
    weakened_other_file = dict(weakened)  # identically mirrored, byte-for-byte
    assert weakened == weakened_other_file, (
        "the plant itself must reproduce the property under test: two "
        "identically-weakened steps that are still equal to each other"
    )
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            weakened, CANONICAL_CAPABILITY_STEP, source="planted-exposure"
        )
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            weakened_other_file, CANONICAL_CAPABILITY_STEP, source="planted-candidate"
        )


def test_canonical_step_check_catches_an_added_shell_override() -> None:
    """`shell: "true {0}"` (or any other custom-shell template) would let
    GitHub Actions accept the step, log the unchanged `run:` text, and
    execute nothing — the run-text-only check the first two review rounds
    left in place never inspected this key at all."""
    mutated = dict(CANONICAL_CAPABILITY_STEP)
    mutated["shell"] = "true {0}"
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            mutated, CANONICAL_CAPABILITY_STEP, source="planted"
        )


def test_canonical_step_check_catches_an_added_env_override() -> None:
    """An `env:` override (e.g. a `BASH_ENV` pointing at a file that shadows
    `python`) can silence the script without touching `run:`, `if`, or
    `continue-on-error` — none of which the earlier checks would catch."""
    mutated = dict(CANONICAL_CAPABILITY_STEP)
    mutated["env"] = {"BASH_ENV": "shadow-python.sh"}
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(
            mutated, CANONICAL_CAPABILITY_STEP, source="planted"
        )


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
    exposure_step = _step_invoking(_steps(exposure, "preflight"), CAPABILITY_SCRIPT)
    candidate_step = _step_invoking(_steps(candidate, "candidate"), CAPABILITY_SCRIPT)

    assert not _declares_continue_on_error(exposure_step), (
        "the capability-check step declares continue-on-error at all "
        f"(value: {exposure_step.get('continue-on-error')!r}) — any value, "
        "literal or a `${{ }}` expression, can tolerate a NOT_CAPABLE "
        "verdict; the key must be absent entirely"
    )
    assert "if" not in exposure_step, (
        "the capability-check step is gated by an `if:` condition, which "
        "could skip it entirely and let a NOT_CAPABLE verdict pass silently — "
        "mirror foundation-candidate.yml's own unconditional step"
    )
    # Whole-dict equality against a PINNED literal, not just against each
    # other — see `_assert_step_matches_canonical`'s own docstring and
    # `test_canonical_step_check_catches_a_mirrored_weakening_of_both_files`
    # for why cross-file equality alone is not enough.
    _assert_step_matches_canonical(
        exposure_step, CANONICAL_CAPABILITY_STEP, source="exposure-rehearsal.yml"
    )
    _assert_step_matches_canonical(
        candidate_step, CANONICAL_CAPABILITY_STEP, source="foundation-candidate.yml"
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

    capability_run = str(_step_invoking(exposure_steps, CAPABILITY_SCRIPT)["run"])
    assert "--out lane3-runner-capability.json" in capability_run

    exposure_upload = _upload_step(exposure_steps)
    candidate_upload = _upload_step(candidate_steps)

    # Whole-dict equality against a PINNED literal for BOTH files, not
    # `exposure_upload["uses"] == candidate_upload["uses"]` alone -- the same
    # "equal to each other proves nothing about either being correct" gap
    # named for the capability-check step above applies here identically,
    # and also rejects an extra key (e.g. a silently-added
    # `continue-on-error` that would let a failed upload pass quietly).
    _assert_step_matches_canonical(
        exposure_upload, CANONICAL_UPLOAD_STEP, source="exposure-rehearsal.yml upload"
    )
    _assert_step_matches_canonical(
        candidate_upload,
        CANONICAL_UPLOAD_STEP,
        source="foundation-candidate.yml upload",
    )
    # The artifact's declared `path` must be the exact file the capability
    # check step wrote via `--out`, not merely a similarly-named string.
    assert exposure_upload["with"]["path"] in capability_run


def test_canonical_upload_step_check_catches_a_dropped_always_condition() -> None:
    """Sensitivity: dropping `if: always()` would mean a failed capability
    check's own artifact never uploads, hiding the evidence of why CI
    failed."""
    mutated = dict(CANONICAL_UPLOAD_STEP)
    del mutated["if"]
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(mutated, CANONICAL_UPLOAD_STEP, source="planted")


def test_canonical_upload_step_check_catches_an_added_continue_on_error() -> None:
    """A `continue-on-error` on the upload step would let a missing/failed
    record pass quietly instead of failing the job."""
    mutated = dict(CANONICAL_UPLOAD_STEP)
    mutated["continue-on-error"] = True
    with pytest.raises(AssertionError):
        _assert_step_matches_canonical(mutated, CANONICAL_UPLOAD_STEP, source="planted")
