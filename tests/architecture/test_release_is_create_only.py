"""Publishing a version is create-only, and two runs of it cannot interleave.

Publishing is the moment a name stops being ours to redefine. Every pin,
lockfile, receipt and `accepts_manifest_digest` check that later cites a
version assumes the bytes behind it never changed. So the two ways a version
can quietly acquire a second meaning both have to be closed:

- **concurrently**, by two dispatches of the same facility+version racing to
  `twine upload`, where which bytes win is decided by scheduling and the tag
  `verify` writes can name the loser's SHA;
- **silently**, by a re-run that finds the version already present and reports
  success anyway.

The second is the one worth staring at. `twine --skip-existing` exists exactly
to turn a duplicate into a green no-op, which is helpful in a mirror-sync job
and catastrophic here: a re-publish that should have screamed *"this version
already exists and the bytes you just built are not the ones behind it"*
instead reports a successful publish that published nothing, and everyone
downstream keeps pinning bytes nobody re-verified.

## Sensitivity

The absence assertions are shown biting against planted mutations, because a
check over a file that happens not to contain a string passes for the wrong
reason and would keep passing if the file were renamed out from under it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-facility.yml"

#: Flags that convert "this version already exists" into a successful exit.
SILENCING_FLAGS = ("--skip-existing", "--skip_existing")


def _document() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_bodies(document: dict) -> str:
    return "\n".join(
        str(step.get("run", ""))
        for job in document.get("jobs", {}).values()
        for step in job.get("steps", [])
    )


def test_the_workflow_exists() -> None:
    """The premise. Without it every absence assertion below is vacuous."""
    assert WORKFLOW.is_file()


def test_the_release_lane_is_serialised_per_version() -> None:
    concurrency = _document().get("concurrency")
    assert concurrency, (
        "the release lane declares no `concurrency`, so two dispatches of one "
        "version can both reach twine and the winner is decided by scheduling"
    )
    group = str(concurrency.get("group", ""))
    assert "inputs.facility" in group and "inputs.version" in group, (
        f"the concurrency group {group!r} does not key on facility AND "
        "version, so it either serialises unrelated releases or fails to "
        "serialise two runs of the same one"
    )


def test_the_release_lane_is_never_cancelled_in_flight() -> None:
    """A cancelled publish can leave a version published with no tag."""
    assert _document()["concurrency"].get("cancel-in-progress") is False


@pytest.mark.parametrize("flag", SILENCING_FLAGS)
def test_no_step_silences_a_duplicate_publish(flag: str) -> None:
    assert flag not in _run_bodies(_document()), (
        f"{flag} turns 'this version already exists' into a green no-op, so a "
        "re-publish reports success while the bytes behind the version stay "
        "whatever they already were"
    )


def test_the_publish_step_actually_uploads() -> None:
    """Positive control.

    Without it, deleting the publish step entirely would make every absence
    assertion above pass — the strongest possible score for the worst possible
    lane.
    """
    assert "twine upload" in _run_bodies(_document())


# ── planted mutations: the guard must be observed failing ───────────────────


def test_a_planted_skip_existing_is_caught() -> None:
    document = _document()
    document["jobs"]["publish"]["steps"].append(
        {"name": "planted", "run": "python -m twine upload --skip-existing dist/*"}
    )
    assert "--skip-existing" in _run_bodies(document)


def test_a_removed_concurrency_block_is_caught() -> None:
    document = _document()
    del document["concurrency"]
    assert not document.get("concurrency")


def test_a_planted_cancel_in_progress_is_caught() -> None:
    document = _document()
    document["concurrency"]["cancel-in-progress"] = True
    assert document["concurrency"]["cancel-in-progress"] is not False


def test_a_version_agnostic_concurrency_group_is_caught() -> None:
    """The subtle mutation: a group that serialises the wrong thing.

    `release-facility` alone would make two DIFFERENT versions queue behind
    each other while still letting nothing about the same-version race change,
    and it reads in a diff like a working guard.
    """
    group = "release-facility"
    assert not ("inputs.version" in group and "inputs.facility" in group)
