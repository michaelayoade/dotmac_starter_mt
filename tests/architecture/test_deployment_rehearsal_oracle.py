"""The rehearsal is a MACHINE precondition of publishing, not a sentence.

`dotmac-deployment-foundation` executes migrations, backup, the warm-candidate
handoff and rollback. In-repo those paths are exercised against a fake
`Effects`, which is the right tool for asserting the plan refuses at the right
step and is incapable of proving a real Docker daemon honours
`service_completed_successfully`.

`docs/inventories/deployment-foundation-rehearsal.md` required the
disposable-host rehearsal before publication IN PROSE, and prose is bypassed by
anyone who does not read it. These tests assert the requirement is executable:
the release workflow calls the oracle, and the oracle refuses.

Every refusal case here carries its own negative control — a test that only
proves "the checker says no" would pass just as happily if the checker said no
to EVERYTHING, so `decide` is also shown accepting the one shape it should.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
RELEASE = REPO / ".github" / "workflows" / "release-facility.yml"
REHEARSAL = REPO / ".github" / "workflows" / "deployment-rehearsal.yml"
CHECKER = REPO / "scripts" / "require_rehearsal.py"

sys.path.insert(0, str(REPO / "scripts"))
from require_rehearsal import RehearsalMissing, decide  # noqa: E402

GOOD_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _run(**over: object) -> dict[str, object]:
    base = {
        "id": 1,
        "head_sha": GOOD_SHA,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://example.invalid/1",
        "run_started_at": "2026-08-27T00:00:00Z",
    }
    base.update(over)
    return base


OLDER = "2026-08-27T00:00:00Z"
NEWER = "2026-08-27T06:00:00Z"


def _at(when: str, identifier: int, **over: object) -> dict[str, object]:
    """A run pinned to an explicit ordering coordinate."""
    return _run(id=identifier, run_started_at=when, **over)


# ── the oracle exists and is reachable from the release lane ────────────────


def test_the_release_workflow_calls_the_rehearsal_oracle() -> None:
    doc = yaml.safe_load(RELEASE.read_text())
    steps = doc["jobs"]["build"]["steps"]
    calls = [
        step for step in steps if "require_rehearsal.py" in str(step.get("run", ""))
    ]
    assert calls, (
        "release-facility.yml must call scripts/require_rehearsal.py before it "
        "publishes; without it the rehearsal requirement is prose again"
    )


def test_the_oracle_runs_before_anything_is_built_or_published() -> None:
    """Order matters: a gate after the build still burns the publish token's job.

    The gate is only worth having if it runs before the artifact exists.
    """
    doc = yaml.safe_load(RELEASE.read_text())
    steps = doc["jobs"]["build"]["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    gate = next(i for i, r in enumerate(runs) if "require_rehearsal.py" in r)
    build = next((i for i, r in enumerate(runs) if "poetry build" in r), len(runs))
    assert gate < build, "the rehearsal gate must precede `poetry build`"


def test_the_rehearsal_workflow_exists_and_tears_itself_down() -> None:
    doc = yaml.safe_load(REHEARSAL.read_text())
    job = doc["jobs"]["rehearse"]
    runs = [str(step.get("run", "")) for step in job["steps"]]
    assert any("deployment_rehearsal.sh all" in r for r in runs)
    teardown = [
        step
        for step in job["steps"]
        if "deployment_rehearsal.sh down" in str(step.get("run", ""))
    ]
    assert teardown, "the rehearsal must tear its own infrastructure down"
    assert teardown[-1].get("if") == "always()", (
        "teardown must run even when the rehearsal fails — a failed rehearsal "
        "that leaks containers is worse than a failed rehearsal"
    )


def test_the_checker_offers_no_escape_hatch() -> None:
    """A flag that skips the gate is the gate, deleted, with extra steps.

    Read the CODE, not the file. A substring search over the source also reads
    the module docstring — which says, in as many words, that there is no
    `--allow-missing` — so a text search fails on the documentation promising
    the very property it is checking. This walks the AST instead and looks at
    what `argparse` actually accepts and what environment variables are
    actually consulted.
    """
    tree = ast.parse(CHECKER.read_text())
    surface: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if name not in {"add_argument", "get", "getenv"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                surface.add(arg.value)

    forbidden = {
        "--allow-missing",
        "--skip",
        "--skip-rehearsal",
        "--force",
        "--no-verify",
        "SKIP_REHEARSAL",
        "ALLOW_MISSING_REHEARSAL",
    }
    assert not (surface & forbidden), (
        f"the checker accepts {sorted(surface & forbidden)}, which would defeat "
        "the oracle it exists to be"
    )
    # Negative control: the walk must actually SEE the real surface, or the
    # assertion above passes because it found nothing at all.
    assert "--repo" in surface and "GITHUB_TOKEN" in surface, (
        "the AST walk found none of the checker's real arguments, so its "
        "verdict on escape hatches means nothing"
    )


# ── the oracle refuses, and the negative control shows it can accept ────────


def test_a_successful_rehearsal_on_the_exact_sha_is_accepted() -> None:
    """The negative control for every refusal below.

    Without this, a `decide` that raised unconditionally would pass the whole
    refusal suite.
    """
    proof = decide([_run()], GOOD_SHA)
    assert proof["run_id"] == 1
    assert proof["head_sha"] == GOOD_SHA


@pytest.mark.parametrize(
    ("runs", "because"),
    [
        ([], "no rehearsal has ever run for this commit"),
        ([_run(status="in_progress", conclusion=None)], "still running"),
        ([_run(conclusion="failure")], "it failed"),
        ([_run(conclusion="cancelled")], "it was cancelled"),
        ([_run(conclusion="skipped")], "it was skipped"),
        ([_run(head_sha=OTHER_SHA)], "it proves a DIFFERENT commit"),
    ],
)
def test_the_oracle_refuses(runs: list[dict[str, object]], because: str) -> None:
    with pytest.raises(RehearsalMissing):
        decide(runs, GOOD_SHA)


def test_a_pass_on_another_commit_is_refused_even_alongside_this_one() -> None:
    """The likeliest real defeat: a green rehearsal for some other SHA.

    The API is asked to filter by `head_sha`; if it ever returns a foreign one,
    the checker must not quietly ignore it and accept the rest.
    """
    with pytest.raises(RehearsalMissing):
        decide([_run(head_sha=OTHER_SHA), _run(id=2)], GOOD_SHA)


@pytest.mark.parametrize("sha", ["", "abc", "z" * 40, GOOD_SHA.upper()])
def test_a_malformed_sha_is_refused_rather_than_matched_loosely(sha: str) -> None:
    with pytest.raises(RehearsalMissing):
        decide([_run(head_sha=sha)], sha)


def test_the_checker_fails_closed_on_an_unreadable_oracle() -> None:
    """An oracle that cannot be read has not said yes."""
    source = CHECKER.read_text()
    body = re.search(r"def _fetch\(.*?(?=\ndef )", source, re.S)
    assert body, "_fetch must exist"
    assert "raise RehearsalMissing" in body.group(0), (
        "_fetch must convert every transport failure into a refusal, not let "
        "an exception type decide the outcome by accident"
    )


# ── latest-run semantics ────────────────────────────────────────────────────
#
# The order of operations is the whole point: pick the NEWEST run for this SHA,
# THEN require it to be completed/success. Filtering to successes first and
# taking the newest of THOSE lets an old green rehearsal mask a newer one that
# broke — which is a green publish on a commit whose rehearsal is currently
# failing.


def test_an_older_success_does_not_mask_a_newer_failure() -> None:
    runs = [
        _at(OLDER, 1, conclusion="success"),
        _at(NEWER, 2, conclusion="failure"),
    ]
    with pytest.raises(RehearsalMissing, match="most recent"):
        decide(runs, GOOD_SHA)


def test_an_older_success_does_not_mask_a_newer_run_still_in_flight() -> None:
    """A queued or running rehearsal is not yet a statement, and the previous
    statement has been superseded by somebody starting a new one."""
    for status, conclusion in (("queued", None), ("in_progress", None)):
        runs = [
            _at(OLDER, 1, conclusion="success"),
            _at(NEWER, 2, status=status, conclusion=conclusion),
        ]
        with pytest.raises(RehearsalMissing, match="most recent"):
            decide(runs, GOOD_SHA)


def test_an_older_success_does_not_mask_a_newer_cancellation() -> None:
    runs = [
        _at(OLDER, 1, conclusion="success"),
        _at(NEWER, 2, conclusion="cancelled"),
    ]
    with pytest.raises(RehearsalMissing):
        decide(runs, GOOD_SHA)


def test_an_older_failure_is_overruled_by_a_newer_success() -> None:
    """The honest repair path: it broke, it was fixed, it passed. This is the
    case that must NOT be refused, and it is what stops the three tests above
    from being satisfied by a `decide` that simply always refuses."""
    runs = [
        _at(OLDER, 1, conclusion="failure"),
        _at(NEWER, 2, conclusion="success"),
    ]
    proof = decide(runs, GOOD_SHA)
    assert proof["run_id"] == 2, "the NEWER successful run is the evidence"


def test_list_order_does_not_decide_anything() -> None:
    """The API does not promise an ordering, so neither ordering of the same
    two runs may change the verdict."""
    older_ok = _at(OLDER, 1, conclusion="success")
    newer_bad = _at(NEWER, 2, conclusion="failure")
    for runs in ([older_ok, newer_bad], [newer_bad, older_ok]):
        with pytest.raises(RehearsalMissing):
            decide(runs, GOOD_SHA)


@pytest.mark.parametrize(
    "broken",
    [
        {"run_started_at": None},
        {"run_started_at": ""},
        {"run_started_at": "not-a-timestamp"},
        {"run_started_at": "2026-13-45T99:00:00Z"},
        {"id": None},
        {"id": "2"},
    ],
)
def test_an_unusable_ordering_coordinate_is_refused(broken: dict) -> None:
    """Without a trustworthy coordinate, 'newest' is a guess — and guessing is
    exactly how an older success masks a newer failure. Refuse instead of
    falling back to list order."""
    runs = [_at(OLDER, 1, conclusion="success"), _run(**{**{"id": 2}, **broken})]
    with pytest.raises(RehearsalMissing):
        decide(runs, GOOD_SHA)


def test_a_tie_on_time_is_broken_by_id_not_by_position() -> None:
    same = "2026-08-27T03:00:00Z"
    lower_ok = _at(same, 1, conclusion="success")
    higher_bad = _at(same, 2, conclusion="failure")
    for runs in ([lower_ok, higher_bad], [higher_bad, lower_ok]):
        with pytest.raises(RehearsalMissing):
            decide(runs, GOOD_SHA)


# ── the job token can actually read the oracle ──────────────────────────────


def test_the_build_job_may_read_the_workflow_runs_api() -> None:
    """`actions: read` or the first release gets HTTP 403 and fails closed.

    The checker is right to refuse an unreadable oracle, which is exactly why
    this has to be caught here: the symptom would be a refusal that looks like
    a missing rehearsal and has nothing to do with one.
    """
    doc = yaml.safe_load(RELEASE.read_text())
    build = doc["jobs"]["build"]
    granted = build.get("permissions") or {}
    assert granted.get("actions") == "read", (
        "the build job calls the workflow-runs API; without `actions: read` "
        "the job token receives 403 and the gate refuses for the wrong reason"
    )
    assert granted.get("contents") == "read"


def test_the_release_workflow_no_longer_claims_a_reviewer_wait() -> None:
    """`registry-release` is a credential boundary now, not an approval gate.

    The freshness re-check stays — a job still queues, and main can move — but
    describing it as happening 'after the approval wait' documents a control
    that no longer exists, which is how a reader concludes a human is watching.
    """
    text = RELEASE.read_text().lower()
    for stale in (
        "approval wait",
        "approved sha",
        "await environment approval",
        "awaiting approval",
        "required review",
    ):
        assert stale not in text, f"stale approval language remains: {stale!r}"
