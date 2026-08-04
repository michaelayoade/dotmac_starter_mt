"""The release workflow re-checks its source AFTER the approval wait.

`release-kernel.yml` builds on the exact current tip of protected main, then
parks at the `registry-release` environment for a human approval that can sit
pending for hours. An approval is a decision about a SHA, not about a run id —
so if main advances during the wait, publishing the already-built artifact ships
a release that silently omits those commits and tags a SHA that is no longer the
tip.

Two things are pinned here, because the guard is worthless if either drifts:

1. `scripts/assert_current_main.sh` actually blocks a moved ref (a sensitivity
   proof, not just "the script exists").
2. BOTH the `build` and `publish` jobs call it. The build-time check alone is
   the exact hole this exists to close, and deleting the second call would be
   invisible in a green CI run — no test executes the release workflow.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
GUARD = REPO / "scripts" / "assert_current_main.sh"
WORKFLOW = REPO / ".github" / "workflows" / "release-kernel.yml"

SHA = "a854d2f26d3f73ec46e33c036138169ae356d7ab"
MOVED = "6f2785d0000000000000000000000000000000ff"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # fixed argv, no shell, no untrusted input — the guard is an in-repo script
    argv = ["bash", str(GUARD), *args]
    return subprocess.run(  # noqa: S603 # nosec B603
        argv, capture_output=True, text=True, cwd=REPO
    )


def test_the_guard_passes_when_the_run_is_the_current_tip() -> None:
    result = _run(SHA, SHA)
    assert result.returncode == 0, result.stderr
    assert SHA in result.stdout


def test_the_guard_blocks_a_moved_main_ref() -> None:
    """The sensitivity proof: simulate main advancing during the approval wait
    and require publication to fail closed."""
    result = _run(SHA, MOVED)
    assert result.returncode != 0, "a moved main ref must block the release"
    assert "::error::" in result.stdout + result.stderr
    # The operator has to be told to re-dispatch, not left guessing.
    assert "Re-dispatch" in result.stdout + result.stderr


def test_the_guard_refuses_to_run_without_a_run_sha() -> None:
    """No argument must not silently degrade into 'nothing to compare, pass'."""
    assert _run().returncode != 0


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_both_build_and_publish_assert_the_current_main_sha() -> None:
    """`publish` is the load-bearing one — it runs after the human gate, at the
    irreversible boundary. `build` alone is the gap this closes."""
    jobs = _jobs()
    for name in ("build", "publish"):
        steps = jobs[name]["steps"]
        assert any(
            "assert_current_main.sh" in str(step.get("run", "")) for step in steps
        ), (
            f"the {name!r} job no longer asserts it is on the current protected "
            "main SHA — see scripts/assert_current_main.sh for why publish needs "
            "its own check after the approval wait"
        )


def test_publish_asserts_freshness_before_it_touches_the_artifact_or_the_token() -> (
    None
):
    """Ordering is the point: fail before the artifact is downloaded and before
    the publish credential is used, not after."""
    steps = _jobs()["publish"]["steps"]
    guard_at = next(
        i
        for i, step in enumerate(steps)
        if "assert_current_main.sh" in str(step.get("run", ""))
    )
    for i, step in enumerate(steps):
        blob = (
            str(step.get("uses", ""))
            + str(step.get("run", ""))
            + str(step.get("env", ""))
        )
        if "download-artifact" in blob or "FORGEJO_PUBLISH_TOKEN" in blob:
            assert guard_at < i, (
                "the freshness check must run BEFORE the artifact download and "
                "before the publish token is referenced"
            )
