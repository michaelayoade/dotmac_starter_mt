"""The candidate lane must be INCAPABLE of publishing, not merely not asked to.

`foundation-candidate.yml` builds the one wheel the bootstrap depends on and
must never publish or tag it. "Must never" is worth nothing on its own: a lane
that *could* publish but currently does not is one edit away from being a second
publisher, which is exactly the shape this programme spent a day removing from
Control.

So the property is checked structurally, and the checker is shown failing
against each planted mutation. A guard that has never been observed failing is
indistinguishable from one that cannot fail — the same discipline as the
role-DDL guard in `test_deployment_foundation_recovery_bundle.py`.

## What makes this checkable rather than aspirational

Publish authority in this repository rests on exactly two declarations, both
visible in `release-facility.yml`:

- ``environment:`` — the credential boundary. `publish` and `verify` declare
  `registry-release`; without it the registry token is unreachable.
- ``permissions: contents: write`` — declared only by `verify`, and it is what
  lets a job create a tag.

Asserting their ABSENCE is therefore not a proxy for "cannot publish"; it is the
thing itself. The verb scan below is defence in depth for the case where a
future credential arrives by some other route.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "foundation-candidate.yml"

#: Any write-shaped permission. `contents: write` is the tag capability;
#: `packages: write` and `id-token: write` are publish capabilities (the latter
#: because OIDC trades a token for registry credentials without a secret ever
#: appearing in the workflow).
FORBIDDEN_PERMISSION_VALUES = {"write"}

#: Verbs that publish or tag. Matched against the RUN SCRIPTS only, so prose in
#: the header explaining why the lane does not publish cannot trip the guard —
#: the mistake that once got a guard disabled for reading its own documentation.
PUBLISH_VERBS = (
    "twine",
    "poetry publish",
    "gh release",
    "git tag",
    "git push",
    "pypi",
    "upload-to",
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_scripts(document: dict) -> list[str]:
    scripts: list[str] = []
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                scripts.append(step["run"])
    return scripts


def candidate_lane_violations(document: dict) -> list[str]:
    """Every way ``document`` fails to be incapable of publishing.

    A pure function over a parsed workflow so the planted mutations below can
    drive it directly, rather than requiring a mutated file on disk.
    """
    problems: list[str] = []

    jobs = document.get("jobs") or {}
    if not jobs:
        problems.append("the workflow declares no jobs")

    for name, job in jobs.items():
        if "environment" in job:
            problems.append(
                f"job {name!r} declares environment {job['environment']!r} — that "
                "IS the credential boundary; a candidate lane that can reach the "
                "registry environment can publish"
            )
        permissions = job.get("permissions") or {}
        if isinstance(permissions, str):
            problems.append(
                f"job {name!r} sets blanket permissions {permissions!r}; the "
                "candidate lane enumerates exactly contents: read and actions: read"
            )
            continue
        for scope, value in permissions.items():
            if value in FORBIDDEN_PERMISSION_VALUES:
                problems.append(
                    f"job {name!r} declares {scope}: {value} — a write permission "
                    "in the candidate lane. `contents: write` creates tags and "
                    "`packages: write`/`id-token: write` reach a registry"
                )
        if set(permissions) - {"contents", "actions"}:
            problems.append(
                f"job {name!r} declares permission scopes "
                f"{sorted(set(permissions) - {'contents', 'actions'})}; only "
                "contents and actions are admissible here"
            )

    top = document.get("permissions") or {}
    if isinstance(top, dict):
        for scope, value in top.items():
            if value in FORBIDDEN_PERMISSION_VALUES:
                problems.append(f"workflow-level {scope}: {value} is a write grant")

    for script in _run_scripts(document):
        lowered = script.lower()
        for verb in PUBLISH_VERBS:
            if verb in lowered:
                problems.append(
                    f"a run step invokes {verb!r} — the candidate lane preserves "
                    "bytes and publishes nothing"
                )

    triggers = document.get("on") or document.get(True) or {}
    if isinstance(triggers, dict) and "pull_request" in triggers:
        problems.append(
            "the candidate lane declares a pull_request trigger; a fork could "
            "then reach it"
        )

    return problems


# ── the real workflow ────────────────────────────────────────────────────────


def test_the_candidate_workflow_exists() -> None:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"


def test_the_candidate_lane_cannot_publish_or_tag() -> None:
    assert candidate_lane_violations(_workflow()) == []


def test_the_candidate_lane_declares_exactly_the_two_read_scopes() -> None:
    """`actions: read` is REQUIRED, not tidiness: recording the artifact id and
    the real expiry calls the artifacts API, which 403s on `contents: read`."""
    job = _workflow()["jobs"]["candidate"]
    assert job["permissions"] == {"actions": "read", "contents": "read"}


def test_the_candidate_lane_has_no_lane_three_precondition() -> None:
    """The whole reason this lane exists. If it grew the release lane's rehearsal
    gate it would deadlock exactly as that lane does."""
    combined = " ".join(_run_scripts(_workflow())).lower()
    assert "require_rehearsal" not in combined
    assert "exposure-rehearsal" not in combined


def test_the_candidate_lane_builds_from_current_protected_main() -> None:
    combined = " ".join(_run_scripts(_workflow()))
    assert "assert_current_main.sh" in combined


def test_the_candidate_lane_requests_the_maximum_retention() -> None:
    """90 is the cap for a public repository (`maximum_allowed_days: 90`), not a
    preference. The RECORDED expiry is what the API returns, which is why the
    receipt reads it back rather than echoing this number."""
    uploads = [
        step
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if isinstance(step, dict) and "upload-artifact" in str(step.get("uses", ""))
    ]
    assert uploads, "the candidate lane uploads nothing"
    for step in uploads:
        assert step["with"]["retention-days"] == 90
        assert step["with"]["if-no-files-found"] == "error"


# ── planted mutations: each must turn the guard RED ──────────────────────────


def _mutate(**changes: object) -> dict:
    document = _workflow()
    job = document["jobs"]["candidate"]
    for key, value in changes.items():
        job[key] = value
    return document


def test_a_planted_registry_environment_is_caught() -> None:
    problems = candidate_lane_violations(_mutate(environment="registry-release"))
    assert any("credential boundary" in problem for problem in problems), problems


def test_a_planted_contents_write_is_caught() -> None:
    problems = candidate_lane_violations(
        _mutate(permissions={"actions": "read", "contents": "write"})
    )
    assert any("write permission" in problem for problem in problems), problems


def test_a_planted_packages_write_is_caught() -> None:
    problems = candidate_lane_violations(
        _mutate(
            permissions={"actions": "read", "contents": "read", "packages": "write"}
        )
    )
    assert any("write permission" in problem for problem in problems), problems


def test_a_planted_id_token_write_is_caught() -> None:
    """OIDC trades a token for registry credentials with no secret in the file,
    so a `contents`-only check would miss it."""
    problems = candidate_lane_violations(
        _mutate(
            permissions={"actions": "read", "contents": "read", "id-token": "write"}
        )
    )
    assert any("write permission" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "command",
    [
        "twine upload dist/*",
        "poetry publish --no-interaction",
        "git tag -a v0.3.0a1 -m release",
        "gh release create v0.3.0a1",
        "git push origin --tags",
    ],
)
def test_a_planted_publish_or_tag_command_is_caught(command: str) -> None:
    document = _workflow()
    document["jobs"]["candidate"]["steps"].append({"name": "planted", "run": command})
    problems = candidate_lane_violations(document)
    assert any("publishes nothing" in problem for problem in problems), problems


def test_a_planted_pull_request_trigger_is_caught() -> None:
    document = _workflow()
    key = "on" if "on" in document else True
    document[key] = {"workflow_dispatch": {}, "pull_request": {}}
    problems = candidate_lane_violations(document)
    assert any("fork" in problem for problem in problems), problems


def test_a_conforming_synthetic_lane_has_no_violations() -> None:
    """The case that must PASS. Without it, a checker that flagged everything
    would satisfy every test above."""
    clean = {
        "on": {"workflow_dispatch": {}},
        "permissions": {"contents": "read"},
        "jobs": {
            "candidate": {
                "permissions": {"contents": "read", "actions": "read"},
                "steps": [{"run": "poetry build"}],
            }
        },
    }
    assert candidate_lane_violations(clean) == []
