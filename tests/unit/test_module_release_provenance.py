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
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "module_release_provenance.py"


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
WHEEL_NAME = "dotmac_approvals-0.1.0a1-py3-none-any.whl"


def _row(
    *,
    tag: str = "dotmac-approvals-v0.1.0a1",
    verification_run_id: str = "1001",
    source_run_id: str = "1001",
    peeled_commit: str = PEELED_COMMIT,
) -> dict:
    return {
        "distribution": DISTRIBUTION,
        "version": tag.removeprefix(f"{DISTRIBUTION}-v"),
        "tag": tag,
        "tag_object": "a" * 40,
        "peeled_commit": peeled_commit,
        "status": "released",
        "pinnable": True,
        "sha256": {WHEEL_NAME: "b" * 64},
        "verification_run_id": verification_run_id,
        "source_run_id": source_run_id,
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
) -> dict:
    if display_title is None:
        verb = "Recover" if path == RECOVERY_WORKFLOW else "Release"
        display_title = f"{verb} module {DISTRIBUTION} {DEFAULT_VERSION}"
    return {
        "id": int(run_id),
        "path": path,
        "event": event,
        "head_branch": head_branch,
        "repository": {"full_name": full_name},
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "display_title": display_title,
    }


class FakeGitHubRuns:
    """A dict-backed fake — no network. Values may be a list for polling."""

    def __init__(self, runs: dict[str, object], jobs: dict[str, list[dict]]) -> None:
        self._runs = runs
        self._jobs = jobs
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


def _fake_sleep():
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    return sleep, calls


def _jobs_with_step(name: str, conclusion: str = "success") -> list[dict]:
    return [{"steps": [{"name": name, "conclusion": conclusion}]}]


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
        sleep=sleep,
    )
    assert calls == []


def test_accepted_recovery(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            # The recovery/verification run's own head_sha is never checked —
            # only the SOURCE run's is. Set it to something else to prove that.
            "2002": _run_object(
                run_id="2002",
                path=".github/workflows/recover-module-release.yml",
                head_sha="f" * 40,
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
        sleep=sleep,
    )
    assert calls == []


# ── Rule (a): verification run identity ─────────────────────────────────────


def test_refuses_a_verification_run_on_the_wrong_workflow(provenance) -> None:
    row = _row()
    runs = FakeGitHubRuns(
        runs={
            "1001": _run_object(run_id="1001", path=".github/workflows/unrelated.yml")
        },
        jobs={},
    )
    with pytest.raises(provenance.ProvenanceError, match="not an approved workflow"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
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
    with pytest.raises(provenance.ProvenanceError, match="is not in"):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
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
            sleep=_fake_sleep()[0],
        )


# ── Rule (d): recovery source run identity ──────────────────────────────────


def test_refuses_a_recovery_source_run_on_the_wrong_workflow(provenance) -> None:
    row = _row(verification_run_id="2002", source_run_id="1001")
    runs = FakeGitHubRuns(
        runs={
            "2002": _run_object(
                run_id="2002", path=".github/workflows/recover-module-release.yml"
            ),
            "1001": _run_object(
                run_id="1001",
                path=".github/workflows/unrelated.yml",
                conclusion="failure",
            ),
        },
        jobs={"2002": _jobs_with_step("Tag the recovered release")},
    )
    with pytest.raises(
        provenance.ProvenanceError,
        match=r"is not \.github/workflows/release-module\.yml",
    ):
        provenance.verify_row_provenance(
            row,
            peeled_commit=PEELED_COMMIT,
            runs=runs,
            repository=REPOSITORY,
            wait_seconds=100,
            poll_seconds=10,
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
