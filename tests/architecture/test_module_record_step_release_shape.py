"""Module release record steps pick lineage flags from the resolver's shape.

A stateless module (allowlist db_schema null) carries no migration, so passing
--package-dir makes the recorder refuse it as UNREADABLE lineage (observed for
dotmac-config-layers 0.1.0a1).  Both the release and the recovery workflow must
branch on ``release_shape`` and fail closed on anything else.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
RECORD = "scripts/open_release_record_pr.sh"


def _record_step(name: str) -> dict:
    wf = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    found = [
        step
        for job in wf["jobs"].values()
        for step in job.get("steps", [])
        if RECORD in str(step.get("run", ""))
    ]
    assert len(found) == 1, f"{name}: expected exactly one record step"
    return found[0]


@pytest.mark.parametrize("name", ["release-module.yml", "recover-module-release.yml"])
def test_record_step_branches_on_release_shape(name: str) -> None:
    run = str(_record_step(name)["run"])
    assert 'case "$RELEASE_SHAPE" in' in run
    assert "stateless) LINEAGE=(--no-lineage)" in run
    assert 'stateful)  LINEAGE=(--package-dir "$PACKAGE_DIR")' in run
    # fail closed on anything else
    assert "*)" in run and "exit 1" in run
    # the recorder call uses only the branch result, never a bare --package-dir
    call = run[run.index(RECORD) :]
    assert '"${LINEAGE[@]}"' in call
    assert "--package-dir" not in call
    assert "--no-lineage" not in call


def test_release_module_carries_shape_from_resolver() -> None:
    wf = yaml.safe_load((WORKFLOWS / "release-module.yml").read_text("utf-8"))
    assert (
        wf["jobs"]["build"]["outputs"]["release_shape"]
        == "${{ steps.resolve.outputs.release_shape }}"
    )
    verify = [
        j
        for j in wf["jobs"].values()
        if any(RECORD in str(s.get("run", "")) for s in j["steps"])
    ]
    assert len(verify) == 1
    assert (
        verify[0]["env"]["RELEASE_SHAPE"] == "${{ needs.build.outputs.release_shape }}"
    )


def test_recover_carries_shape_from_target_step() -> None:
    step = _record_step("recover-module-release.yml")
    assert step["env"]["RELEASE_SHAPE"] == "${{ steps.target.outputs.release_shape }}"


def test_record_script_uses_open_pr_lookup_and_merges_by_number() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "open_release_record_pr.sh"
    ).read_text(encoding="utf-8")
    # a closed PR with the same branch name must never be adopted
    assert "gh pr view" not in script
    assert "gh pr list --head" in script and "--state open" in script
    assert 'gh pr merge "${PR_NUMBER}" --auto --squash' in script
    assert 'gh pr merge "${BRANCH}"' not in script
    # the number is re-read after create and the script fails closed without it
    assert "could not determine the number of the pull request" in script


def test_record_script_excludes_fork_prs_in_every_lookup() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "open_release_record_pr.sh"
    ).read_text(encoding="utf-8")
    lookups = script.count("gh pr list --head")
    assert lookups == 2  # initial lookup and the re-read after create
    assert script.count("--json number,isCrossRepository") == lookups
    assert script.count("map(select(.isCrossRepository|not))") == lookups
    assert "--json number --jq" not in script
