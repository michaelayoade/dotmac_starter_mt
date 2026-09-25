"""A ledger row's named run must itself be a real, approved, successful tag.

`scripts/module_release_provenance.py` proves the two run identities a
`ModuleReleaseTagEvidence.v1`-backed ledger row makes claims about:
`verification_run_id` (the run whose "Tag the ..." step actually created the
tag) and `source_run_id` (the run that built and published the wheel — the
same run for a normal release, or the original failed run for a recovery).

Every test here drives `verify_row_provenance` against a FAKE `GitHubRuns` —
no network call is made — and a fake `sleep` so the in-progress-polling tests
run instantly.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "module_release_provenance.py"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-module.yml"
RECOVER_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "recover-module-release.yml"


def _load_module():
    # `module_release_provenance` does `from write_release_record import ...`
    # as a top-level import, so `scripts/` must already be importable.
    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("module_release_provenance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def provenance():
    return _load_module()


REPOSITORY = "dotmac/dotmac_starter_mt"
PEELED_COMMIT = "c" * 40
DISTRIBUTION = "dotmac-approvals"

# The verified live `workflow_id` for `release-module.yml`. `recover-module
# -release.yml`'s is not independently verified here — it only needs to be
# internally consistent within this fake, since nothing asserts the real
# GitHub-assigned value.
RELEASE_WORKFLOW_ID = 332879371
RECOVER_WORKFLOW_ID = 445566778

SOURCE_WORKFLOW_PATH = ".github/workflows/release-module.yml"

DEFAULT_WORKFLOWS: dict[str, dict] = {
    str(RELEASE_WORKFLOW_ID): {"path": SOURCE_WORKFLOW_PATH},
    str(RECOVER_WORKFLOW_ID): {"path": ".github/workflows/recover-module-release.yml"},
}


def _on_main_always(_commit: str) -> bool:
    """A fake `is_on_main` that treats every commit as on `origin/main`.

    Used by every test that is not specifically exercising the ancestry
    rule — that rule gets its own dedicated fakes below.
    """
    return True


def _on_main_only(*commits: str) -> Callable[[str], bool]:
    allowed = set(commits)
    return lambda commit: commit in allowed


def _row(
    *,
    tag: str = "dotmac-approvals-v0.1.0a1",
    verification_run_id: str = "1001",
    source_run_id: str = "1001",
    peeled_commit: str = PEELED_COMMIT,
) -> dict:
    # The wheel filename must bind THIS row's own distribution + version —
    # `parse_module_release_verifications` refuses a row whose `sha256` key
    # names a wheel for a different version (`_wheel_filename` in
    # `write_release_record.py`), so a fixed constant here would only work
    # for the default tag and silently break every other version a caller
    # passes.
    version = tag.removeprefix(f"{DISTRIBUTION}-v")
    wheel_name = f"{DISTRIBUTION.replace('-', '_')}-{version}-py3-none-any.whl"
    return {
        "distribution": DISTRIBUTION,
        "version": version,
        "tag": tag,
        "tag_object": "a" * 40,
        "peeled_commit": peeled_commit,
        "status": "released",
        "pinnable": True,
        "sha256": {wheel_name: "b" * 64},
        "verification_run_id": verification_run_id,
        "source_run_id": source_run_id,
        # A fixed, syntactically valid release-authority digest. This module's
        # tests are about run provenance, never about the release-authority
        # ledger's history — that is `test_write_release_record.py`'s
        # `validate_module_release_inventory` coverage.
        "release_authority_digest": "sha256:" + "9" * 64,
        "adopting_run_id": None,
    }


RECOVERY_WORKFLOW = ".github/workflows/recover-module-release.yml"
DEFAULT_VERSION = "0.1.0a1"


def _run_object(
    *,
    run_id: str,
    path: str,
    event: str = "workflow_dispatch",
    head_branch: str = "main",
    full_name: str = REPOSITORY,
    status: str = "completed",
    conclusion: str | None = "success",
    head_sha: str = PEELED_COMMIT,
    display_title: str | None = None,
    workflow_id: int | None = None,
) -> dict:
    if display_title is None:
        verb = "Recover" if path == RECOVERY_WORKFLOW else "Release"
        display_title = f"{verb} module {DISTRIBUTION} {DEFAULT_VERSION}"
    if workflow_id is None:
        workflow_id = (
            RECOVER_WORKFLOW_ID if path == RECOVERY_WORKFLOW else RELEASE_WORKFLOW_ID
        )
    return {
        "id": int(run_id),
        "path": path,
        "event": event,
        "head_branch": head_branch,
        "repository": {"full_name": full_name},
        "head_repository": {"full_name": full_name},
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "display_title": display_title,
        "workflow_id": workflow_id,
    }


class FakeGitHubRuns:
    """A dict-backed fake — no network. Values may be a list for polling."""

    def __init__(
        self,
        runs: dict[str, object],
        jobs: dict[str, list[dict]],
        workflows: dict[str, dict] | None = None,
    ) -> None:
        self._runs = runs
        self._jobs = jobs
        self._workflows = workflows if workflows is not None else DEFAULT_WORKFLOWS
        self.get_run_calls: list[str] = []

    def get_run(self, run_id: str) -> dict:
        self.get_run_calls.append(str(run_id))
        entry = self._runs[str(run_id)]
        if isinstance(entry, list):
            if len(entry) > 1:
                return entry.pop(0)
            return entry[0]
        return entry

    def get_jobs(self, run_id: str) -> list[dict]:
        return self._jobs.get(str(run_id), [])

    def get_workflow(self, workflow_id: object) -> dict:
        return self._workflows[str(workflow_id)]


def _fake_sleep():
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    return sleep, calls


def _jobs_with_step(
    name: str, conclusion: str = "success", *, job_name: str | None = None
) -> list[dict]:
    # GitHub reports a job without `name:` by its key: `verify` owns the
    # release tag step, `recover` the recovery one.
    if job_name is None:
        job_name = "recover" if name == "Tag the recovered release" else "verify"
    return [{"name": job_name, "steps": [{"name": name, "conclusion": conclusion}]}]


# ── Accepted paths ───────────────────────────────────────────────────────────


def test_accepted_normal_release(provenance) -> None:
    row = _row(verification_run_id="1001", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                head_sha=PEELED_COMMIT,
            )
        },
        jobs={"1001": _jobs_with_step("Tag the verified release")},
    )
    sleep, calls = _fake_sleep()
    provenance.verify_row_provenance(
        row,
        peeled_commit=PEELED_COMMIT,
        runs=runs,
        repository=REPOSITORY,
        wait_seconds=100,
        poll_seconds=10,
        is_on_main=_on_main_always,
        sleep=sleep,
    )
    assert calls == []


def test_accepted_recovery(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    # The recovery/verification run's own head_sha need not equal the tagged
    # commit — only the SOURCE run's does. It must still be a real commit on
    # main (proven here via `_on_main_only`, which also proves the verification
    # run's ancestry IS checked, unlike its equality to the tagged commit).
    recovery_head_sha = "f" * 40
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002",
                path=".github/workflows/recover-module-release.yml",
                head_sha=recovery_head_sha,
            ),
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                head_sha=PEELED_COMMIT,
                conclusion="failure",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    sleep, calls = _fake_sleep()
    provenance.verify_row_provenance(
        row,
        peeled_commit=PEELED_COMMIT,
        runs=runs,
        repository=REPOSITORY,
        wait_seconds=100,
        poll_seconds=10,
        is_on_main=_on_main_only(PEELED_COMMIT, recovery_head_sha),
        sleep=sleep,
    )
    assert calls == []


# ── Rule (a): verification run identity ─────────────────────────────────────


def test_refuses_a_verification_run_on_the_wrong_workflow(provenance) -> None:
    row = _row()
    unrelated_path = ".github/workflows/unrelated.yml"
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=unrelated_path)},
        jobs={},
        # The fetched workflow agrees with the run's own path — this test is
        # about the path not being APPROVED, not about the two disagreeing
        # (that is a separate test in Rule (e)).
        workflows={str(RELEASE_WORKFLOW_ID): {"path": unrelated_path}},
    )
    with pytest.raises(provenance.ProvenanceError, match="not an approved workflow"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_not_triggered_by_workflow_dispatch(
    provenance,
) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, event="push"
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="workflow_dispatch"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_off_main(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, head_branch="develop"
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="did not run on main"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_in_another_repository(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                full_name="someone-else/fork",
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="repository is not"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_that_did_not_succeed(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, conclusion="failure"
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="did not succeed"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Rule (a2): display_title binds module + version ─────────────────────────


def test_refuses_a_release_verification_run_titled_for_a_different_module(
    provenance,
) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                display_title=f"Release module dotmac-other {DEFAULT_VERSION}",
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_release_verification_run_titled_for_a_different_version(
    provenance,
) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                display_title=f"Release module {DISTRIBUTION} 9.9.9",
            )
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_release_verification_run_missing_a_title(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={},
    )
    # Force no title (as an un-migrated pre-change run would have) — the
    # helper's default fills one in, so override the dict directly.
    runs._runs["1001"]["display_title"] = None
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_verification_run_titled_for_a_different_module(
    provenance,
) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002",
                path=RECOVERY_WORKFLOW,
                display_title=f"Recover module dotmac-other {DEFAULT_VERSION}",
            ),
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, conclusion="failure"
            ),
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_verification_run_missing_a_title(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(run_id="2002", path=RECOVERY_WORKFLOW),
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, conclusion="failure"
            ),
        },
        jobs={},
    )
    runs._runs["2002"]["display_title"] = None
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_titled_for_a_different_module(
    provenance,
) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(run_id="2002", path=RECOVERY_WORKFLOW),
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                conclusion="failure",
                display_title=f"Release module dotmac-other {DEFAULT_VERSION}",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_titled_for_a_different_version(
    provenance,
) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(run_id="2002", path=RECOVERY_WORKFLOW),
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                conclusion="failure",
                display_title=f"Release module {DISTRIBUTION} 9.9.9",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_missing_a_title(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(run_id="2002", path=RECOVERY_WORKFLOW),
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, conclusion="failure"
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    runs._runs["1001"]["display_title"] = None
    with pytest.raises(provenance.ProvenanceError, match="does not bind module"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Rule (b): the tag step itself ───────────────────────────────────────────


def test_refuses_a_run_missing_the_tag_step(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={"1001": _jobs_with_step("Some other step")},
    )
    with pytest.raises(provenance.ProvenanceError, match="has no step named"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_run_whose_tag_step_failed(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={
            "1001": _jobs_with_step("Tag the verified release", conclusion="failure")
        },
    )
    with pytest.raises(provenance.ProvenanceError, match="did not succeed"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Rule (c): release source/verification binding ───────────────────────────


def test_refuses_a_release_whose_source_run_id_disagrees_with_verification(
    provenance,
) -> None:
    row = _row(verification_run_id="1001", source_run_id="9999")
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={"1001": _jobs_with_step("Tag the verified release")},
    )
    with pytest.raises(provenance.ProvenanceError, match="must be the same run"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_release_run_built_at_the_wrong_commit(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, head_sha="d" * 40
            )
        },
        jobs={"1001": _jobs_with_step("Tag the verified release")},
    )
    with pytest.raises(provenance.ProvenanceError, match="not the tagged commit"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Rule (d): recovery source run identity ──────────────────────────────────


def test_refuses_a_recovery_source_run_on_the_wrong_workflow(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    unrelated_path = ".github/workflows/unrelated.yml"
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
            "1001": _run_object(
                run_id="1001",
                path=unrelated_path,
                conclusion="failure",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
        # The fetched workflow agrees with the source run's own path — this
        # test is about the path not being APPROVED for a source run
        # (release-module.yml only), not about the two disagreeing.
        workflows={
            str(RECOVER_WORKFLOW_ID): {
                "path": ".github/workflows/recover-module-release.yml"
            },
            str(RELEASE_WORKFLOW_ID): {"path": unrelated_path},
        },
    )
    with pytest.raises(
        provenance.ProvenanceError,
        match="is not an approved workflow",
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_that_succeeded(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, conclusion="success"
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(
        provenance.ProvenanceError, match="recovery only applies to a failed release"
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_built_at_the_wrong_commit(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                conclusion="failure",
                head_sha="d" * 40,
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(provenance.ProvenanceError, match="not the tagged commit"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_whose_source_equals_the_verification_run(
    provenance,
) -> None:
    row = _row(verification_run_id="2002", source_run_id="2002")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(
        provenance.ProvenanceError, match="both source and verification"
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Rule (e): ancestry and workflow-identity binding ────────────────────────
#
# `head_branch == "main"` alone is spoofable: a `workflow_dispatch` against a
# REF (e.g. a tag) literally named `main` makes GitHub report
# `head_branch: "main"` for a commit that is not actually on the protected
# branch. These tests prove the companion `is_on_main` ancestry check and the
# independent `get_workflow` identity binding actually bite.


def test_refuses_a_verification_run_whose_head_commit_is_not_on_main(
    provenance,
) -> None:
    row = _row()
    off_main_sha = "e" * 40
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=provenance.SOURCE_WORKFLOW, head_sha=off_main_sha
            )
        },
        jobs={},
    )
    with pytest.raises(
        provenance.ProvenanceError,
        match="first-parent line of refs/remotes/origin/main",
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            # PEELED_COMMIT is on main; the run's OWN head_sha (off_main_sha)
            # deliberately is not — proving head_branch=="main" is not enough.
            is_on_main=_on_main_only(PEELED_COMMIT),
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_recovery_source_run_whose_head_commit_is_not_on_main(
    provenance,
) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
            "1001": _run_object(
                run_id="1001",
                path=provenance.SOURCE_WORKFLOW,
                conclusion="failure",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(
        provenance.ProvenanceError,
        match="first-parent line of refs/remotes/origin/main",
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            # The verification run's head_sha (PEELED_COMMIT) is allowed on
            # main, but the SOURCE run's head_sha is refused — the source
            # run's own ancestry must independently be proven.
            is_on_main=_on_main_only(PEELED_COMMIT),
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_tag_whose_peeled_commit_is_not_on_main(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={"1001": _jobs_with_step("Tag the verified release")},
    )
    with pytest.raises(
        provenance.ProvenanceError,
        match="first-parent line of refs/remotes/origin/main",
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            # Nothing is on main — the peeled-commit check fires first,
            # before either run is even fetched.
            is_on_main=_on_main_only(),
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_whose_bound_workflow_path_disagrees(
    provenance,
) -> None:
    # Near-miss: the run object's own `path` field claims the approved
    # release workflow, but the workflow independently fetched by
    # `workflow_id` names a different file — exactly the gap `get_workflow`
    # closes, since a run's self-reported `path` is not trusted alone.
    row = _row()
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={},
        workflows={
            str(RELEASE_WORKFLOW_ID): {"path": ".github/workflows/unrelated.yml"}
        },
    )
    with pytest.raises(provenance.ProvenanceError, match="disagrees with the run"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


def test_refuses_a_verification_run_whose_bound_workflow_is_not_approved(
    provenance,
) -> None:
    # Near-miss: `run.path` and the fetched workflow's path AGREE, but that
    # agreed path is not one of the approved workflows — proves the
    # `expected_paths` membership check inside `_require_workflow_binding`
    # actually bites, not just the disagreement check above.
    row = _row()
    unapproved_path = ".github/workflows/unrelated.yml"
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(
                run_id="1001", path=unapproved_path, workflow_id=RELEASE_WORKFLOW_ID
            )
        },
        jobs={},
        workflows={str(RELEASE_WORKFLOW_ID): {"path": unapproved_path}},
    )
    # Approval is decided only by `_require_workflow_binding`: the path is
    # re-derived from `workflow_id` and must be both equal to the run's own
    # path and one of the approved files.
    with pytest.raises(provenance.ProvenanceError, match="not an approved workflow"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=_fake_sleep()[0],
        )


# ── Polling: in-progress runs ────────────────────────────────────────────────


def test_an_in_progress_run_that_completes_within_the_wait_is_accepted(
    provenance,
) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": [
                _run_object(
                    run_id="1001", path=provenance.SOURCE_WORKFLOW, status="in_progress"
                ),
                _run_object(
                    run_id="1001", path=provenance.SOURCE_WORKFLOW, status="in_progress"
                ),
                _run_object(
                    run_id="1001", path=provenance.SOURCE_WORKFLOW, status="completed"
                ),
            ]
        },
        jobs={"1001": _jobs_with_step("Tag the verified release")},
    )
    sleep, calls = _fake_sleep()
    provenance.verify_row_provenance(
        row,
        peeled_commit=PEELED_COMMIT,
        runs=runs,
        repository=REPOSITORY,
        wait_seconds=100,
        poll_seconds=10,
        is_on_main=_on_main_always,
        sleep=sleep,
    )
    assert calls == [10, 10]


def test_an_in_progress_run_beyond_the_wait_is_refused_as_still_running(
    provenance,
) -> None:
    row = _row()
    always_running = _run_object(
        run_id="1001", path=provenance.SOURCE_WORKFLOW, status="in_progress"
    )
    runs = FakeGitHubRuns(runs={"1001": always_running}, jobs={})
    sleep, calls = _fake_sleep()
    with pytest.raises(provenance.ProvenanceError, match="still running"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=25,
            poll_seconds=10,
            is_on_main=_on_main_always,
            sleep=sleep,
        )
    assert calls == [10, 10, 10]


# ── new_rows: only what a PR appended ────────────────────────────────────────


def _wrap(rows: list[dict]) -> str:
    return json.dumps(
        {
            "$comment": "fixture",
            "schema": "ModuleReleaseVerifications.v1",
            "releases": rows,
        }
    )


def test_new_rows_returns_only_the_appended_rows(provenance) -> None:
    base_row = _row(tag="dotmac-approvals-v0.1.0a1")
    added_row_1 = _row(tag="dotmac-approvals-v0.1.0a2")
    added_row_2 = _row(tag="dotmac-approvals-v0.1.0a3")

    base_text = _wrap([base_row])
    head_text = _wrap([base_row, added_row_1, added_row_2])

    result = provenance.new_rows(base_text, head_text)
    assert result == [added_row_1, added_row_2]


def test_new_rows_returns_nothing_when_head_equals_base(provenance) -> None:
    base_row = _row(tag="dotmac-approvals-v0.1.0a1")
    text = _wrap([base_row])
    assert provenance.new_rows(text, text) == []


# ── ci.yml: the module-release-provenance job ───────────────────────────────


def _ci_job() -> dict:
    data = yaml.safe_load(CI_WORKFLOW.read_text())
    return data["jobs"]["module-release-provenance"]


def _job_permissions_exactly(job: dict, expected: dict[str, str]) -> bool:
    return job.get("permissions") == expected


def _run_bodies(job: dict) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def test_ci_has_a_module_release_provenance_job() -> None:
    data = yaml.safe_load(CI_WORKFLOW.read_text())
    assert "module-release-provenance" in data["jobs"]


def test_ci_job_permissions_are_exactly_contents_and_actions_read() -> None:
    job = _ci_job()
    assert _job_permissions_exactly(job, {"contents": "read", "actions": "read"})


def test_ci_job_permissions_sensitivity_plant_catches_an_added_write_scope() -> None:
    # Near-miss: broaden `contents` to `write` and the same check must reject
    # it — a check that only ever sees the real, correct file proves nothing.
    job = _ci_job()
    polluted = dict(job)
    polluted["permissions"] = {**job["permissions"], "contents": "write"}
    assert not _job_permissions_exactly(
        polluted, {"contents": "read", "actions": "read"}
    )


def test_ci_job_has_a_thirty_minute_timeout() -> None:
    assert _ci_job()["timeout-minutes"] == 30


def test_ci_job_checkout_uses_full_history() -> None:
    job = _ci_job()
    checkout_step = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout")
    )
    assert checkout_step["with"]["fetch-depth"] == 0


def _interpolating_run_bodies(ci_text: str) -> list[str]:
    job = yaml.safe_load(ci_text)["jobs"]["module-release-provenance"]
    return [body for body in _run_bodies(job) if "${{" in body]


def test_ci_job_run_bodies_never_interpolate_an_expression() -> None:
    assert _interpolating_run_bodies(CI_WORKFLOW.read_text()) == []


def test_ci_job_run_body_sensitivity_plant_catches_an_interpolated_expression() -> None:
    # The SAME detector, fed the real ci.yml with the env bridge bypassed:
    # the base SHA inlined straight into the run body (shell-injectable).
    real = CI_WORKFLOW.read_text()
    bridged = 'run: python scripts/module_release_provenance.py --base "$BASE_SHA"'
    assert bridged in real
    planted = real.replace(
        bridged,
        "run: python scripts/module_release_provenance.py "
        '--base "${{ github.event.before }}"',
        1,
    )
    assert _interpolating_run_bodies(planted) != []


def test_ci_job_invokes_the_provenance_script_with_the_base_env_var() -> None:
    bodies = _run_bodies(_ci_job())
    assert any(
        'python scripts/module_release_provenance.py --base "$BASE_SHA"' in body
        for body in bodies
    )


def test_ci_job_invocation_sensitivity_plant_catches_a_missing_base_flag() -> None:
    plant_bodies = ["python scripts/module_release_provenance.py"]
    assert not any(
        'python scripts/module_release_provenance.py --base "$BASE_SHA"' in body
        for body in plant_bodies
    )


# ── run-name binds the dispatched module + version ──────────────────────────


_RELEASE_RUN_NAME = "Release module ${{ inputs.module }} ${{ inputs.version }}"
_RECOVER_RUN_NAME = "Recover module ${{ inputs.module }} ${{ inputs.version }}"


def _run_name_binds(workflow_text: str, expected: str) -> bool:
    return yaml.safe_load(workflow_text).get("run-name") == expected


def test_release_workflow_run_name_binds_module_and_version() -> None:
    assert _run_name_binds(RELEASE_WORKFLOW.read_text(), _RELEASE_RUN_NAME)


def test_recover_workflow_run_name_binds_module_and_version() -> None:
    assert _run_name_binds(RECOVER_WORKFLOW.read_text(), _RECOVER_RUN_NAME)


def test_run_name_sensitivity_plant_catches_a_dropped_version() -> None:
    # The SAME detector, fed the real workflows with the version dropped from
    # the title — exactly the forgeable gap this rule closes.
    for path, expected in (
        (RELEASE_WORKFLOW, _RELEASE_RUN_NAME),
        (RECOVER_WORKFLOW, _RECOVER_RUN_NAME),
    ):
        real = path.read_text()
        assert f"run-name: {expected}" in real
        planted = real.replace(
            f"run-name: {expected}",
            f"run-name: {expected.removesuffix(' ${{ inputs.version }}')}",
            1,
        )
        assert not _run_name_binds(planted, expected)


# ── Repository, fork and tag-job binding ────────────────────────────────────


def _release_verify_kwargs() -> dict:
    return {
        "peeled_commit": PEELED_COMMIT,
        "repository": REPOSITORY,
        "wait_seconds": 0,
        "poll_seconds": 1,
        "is_on_main": lambda sha: True,
    }


def test_refuses_a_run_whose_head_repository_is_a_fork(provenance) -> None:
    run = _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)
    run["head_repository"] = {"full_name": "someone-else/fork"}
    runs = FakeGitHubRuns(
        runs={"1001": run}, jobs={"1001": _jobs_with_step("Tag the verified release")}
    )
    with pytest.raises(provenance.ProvenanceError, match="head_repository is not"):
        provenance.verify_row_provenance(_row(), runs=runs, **_release_verify_kwargs())


def test_refuses_a_run_with_a_null_repository_without_crashing(provenance) -> None:
    run = _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)
    run["repository"] = None
    runs = FakeGitHubRuns(
        runs={"1001": run}, jobs={"1001": _jobs_with_step("Tag the verified release")}
    )
    with pytest.raises(provenance.ProvenanceError, match="repository is not"):
        provenance.verify_row_provenance(_row(), runs=runs, **_release_verify_kwargs())


def test_refuses_a_tag_step_that_sits_in_a_different_job(provenance) -> None:
    runs = FakeGitHubRuns(
        runs={"1001": _run_object(run_id="1001", path=provenance.SOURCE_WORKFLOW)},
        jobs={"1001": _jobs_with_step("Tag the verified release", job_name="build")},
    )
    with pytest.raises(provenance.ProvenanceError, match="in job 'verify'"):
        provenance.verify_row_provenance(_row(), runs=runs, **_release_verify_kwargs())


# ── The real first-parent check against a real git graph ────────────────────


def _git_in(path: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name, encoding="utf-8")
    _git_in(path, "add", name)
    _git_in(path, "commit", "-q", "-m", name)
    return _git_in(path, "rev-parse", "HEAD")


def test_is_on_main_uses_first_parent_of_the_real_remote_branch(
    provenance, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tag named `origin/main`, a side-branch commit merged into main, and a
    commit never merged are all refused; main's first-parent commits pass."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git_in(origin, "init", "-q", "--initial-branch=main")
    _git_in(origin, "config", "user.name", "Test")
    _git_in(origin, "config", "user.email", "test@example.invalid")
    first = _commit(origin, "a")
    _git_in(origin, "checkout", "-q", "-b", "side")
    side = _commit(origin, "side")
    _git_in(origin, "checkout", "-q", "main")
    _commit(origin, "b")
    _git_in(origin, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    tip = _git_in(origin, "rev-parse", "HEAD")

    work = tmp_path / "work"
    _git_in(tmp_path, "clone", "-q", str(origin), str(work))
    _git_in(work, "config", "user.name", "Test")
    _git_in(work, "config", "user.email", "test@example.invalid")
    _git_in(work, "checkout", "-q", "-b", "forged")
    forged = _commit(work, "forged")
    # The shadowing plant: a TAG named `origin/main` pointing at the forged
    # commit. A short `origin/main` would resolve to it.
    _git_in(work, "tag", "origin/main", forged)
    assert _git_in(work, "rev-parse", "origin/main") == forged

    monkeypatch.setattr(provenance, "REPO_ROOT", work)
    assert provenance._is_on_main(tip) is True
    assert provenance._is_on_main(first) is True
    assert provenance._is_on_main(forged) is False
    assert provenance._is_on_main(side) is False  # ancestor, not first-parent
    assert provenance._is_on_main("not-a-sha") is False
    assert provenance._is_on_main(None) is False


def test_the_tag_owning_jobs_keep_github_reporting_their_key_as_name() -> None:
    """GitHub reports a job without `name:` by its key; the provenance check
    binds the tag step to jobs named `verify` and `recover`. Adding a `name:`
    to either job would silently refuse every record, so the premise is pinned."""
    for path, key in ((RELEASE_WORKFLOW, "verify"), (RECOVER_WORKFLOW, "recover")):
        job = yaml.safe_load(path.read_text())["jobs"][key]
        assert "name" not in job, f"{path.name} job {key!r} gained a name:"
