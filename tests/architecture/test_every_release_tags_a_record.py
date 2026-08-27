"""Every workflow that writes a release tag also opens its durable record.

The tag is the irreversible boundary: it makes a publication-baseline row
false immediately, and may turn migration bytes into an immutable public
contract.  Selecting workflows by filename would repeat the guard-scope defect
ADR-0018 forbids, so this test discovers the entry-point family from the actual
``git tag`` command.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
RECORD_SCRIPT = "scripts/open_release_record_pr.sh"
RECORDER_TOKEN_ACTION = "./.github/actions/release-recorder-token"
PINNED_TOKEN_ACTION = (
    "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
)
PINNED_CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def _tagging_jobs(root: Path = WORKFLOWS) -> list[tuple[Path, str, dict]]:
    found: list[tuple[Path, str, dict]] = []
    paths = sorted((*root.glob("*.yml"), *root.glob("*.yaml")))
    for path in paths:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            if any("git tag" in str(step.get("run", "")) for step in steps):
                found.append((path, job_name, job))
    return found


def _tagging_job_permission_problems(permissions: object) -> list[str]:
    """The publisher may write the tag, never the record pull request."""
    if permissions == {"contents": "write"}:
        return []
    return [
        "workflow-token permissions must be exactly contents:write; "
        f"the recorder App owns pull-request writes, found {permissions!r}"
    ]


def _coverage_problems(
    tagging_jobs: list[tuple[Path, str, dict]], *, relative_to: Path
) -> list[str]:
    problems: list[str] = []
    for path, job_name, job in tagging_jobs:
        steps = job.get("steps", [])
        tag_at = max(
            i for i, step in enumerate(steps) if "git tag" in str(step.get("run", ""))
        )
        record_steps = [
            (i, step)
            for i, step in enumerate(steps)
            if RECORD_SCRIPT in str(step.get("run", ""))
        ]
        subject = f"{path.relative_to(relative_to)}:{job_name}"
        problems.extend(
            f"{subject} {problem}"
            for problem in _tagging_job_permission_problems(job.get("permissions"))
        )
        if job.get("environment") != "registry-release":
            problems.append(
                f"{subject} must name the main-only registry-release credential "
                "environment"
            )
        if len(record_steps) != 1:
            problems.append(
                f"{subject} writes a tag but has {len(record_steps)} record steps"
            )
            continue

        record_at, record = record_steps[0]
        if record_at <= tag_at:
            problems.append(f"{subject} opens its record before the tag exists")
        if record.get("if") != "always()":
            problems.append(
                f"{subject} record step must use if: always() so a pushed tag "
                "cannot be stranded by a later step failure"
            )

        token_steps = [
            (i, step)
            for i, step in enumerate(steps)
            if step.get("uses") == RECORDER_TOKEN_ACTION
        ]
        if len(token_steps) != 1:
            problems.append(
                f"{subject} has {len(token_steps)} recorder-token steps; expected 1"
            )
            continue
        token_at, token_step = token_steps[0]
        if token_at >= record_at:
            problems.append(f"{subject} mints its recorder token after it is needed")
        if token_step.get("id") != "recorder-token":
            problems.append(
                f"{subject} recorder-token step must have id: recorder-token"
            )
        if token_step.get("if") != "always()":
            problems.append(f"{subject} recorder-token step must use if: always()")
        if token_step.get("with") != {
            "client-id": "${{ vars.RELEASE_RECORDER_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_RECORDER_PRIVATE_KEY }}",
        }:
            problems.append(f"{subject} does not use the declared recorder App inputs")

        gh_token = str(record.get("env", {}).get("GH_TOKEN", ""))
        if "steps.recorder-token.outputs.token" not in gh_token:
            problems.append(f"{subject} record step does not prefer the recorder App")
        if "secrets.GITHUB_TOKEN" not in gh_token:
            problems.append(f"{subject} record step has no loud/manual bridge token")

    return problems


def test_every_tagging_job_opens_one_record_after_the_tag_even_on_failure() -> None:
    """A new release lane must inherit the record obligation automatically."""
    tagging_jobs = _tagging_jobs()
    assert tagging_jobs, "the detector found no tag-writing release jobs"

    problems = _coverage_problems(tagging_jobs, relative_to=REPO)
    assert not problems, "release-record coverage:\n" + "\n".join(problems)


def test_a_new_tag_writer_is_discovered_without_editing_a_fixed_list(
    tmp_path: Path,
) -> None:
    """Sensitivity: detection follows behavior, not today's workflow names."""
    # GitHub accepts both extensions. Plant the extension not used by today's
    # release files so a `*.yml`-only sweep fails this sensitivity proof.
    planted = tmp_path / "release-new-family.yaml"
    planted.write_text(
        """jobs:
  verify:
    permissions:
      contents: write
    environment: registry-release
    steps:
      - name: Tag the release
        run: git tag -a example-v1 -m example HEAD
""",
        encoding="utf-8",
    )

    found = _tagging_jobs(tmp_path)
    assert [(path.name, job) for path, job, _definition in found] == [
        ("release-new-family.yaml", "verify")
    ]
    assert _coverage_problems(found, relative_to=tmp_path) == [
        "release-new-family.yaml:verify writes a tag but has 0 record steps"
    ]


def test_the_publisher_workflow_token_cannot_write_pull_requests() -> None:
    """Sensitivity: App fallback must not silently regain recorder authority."""
    permissions = {"contents": "write", "pull-requests": "write"}
    assert _tagging_job_permission_problems(permissions) == [
        "workflow-token permissions must be exactly contents:write; the recorder "
        "App owns pull-request writes, found {'contents': 'write', "
        "'pull-requests': 'write'}"
    ]


def test_a_tag_writer_without_the_release_environment_is_refused(
    tmp_path: Path,
) -> None:
    """Sensitivity: tag creation remains inside the credential boundary."""
    planted = tmp_path / "release-ungated.yml"
    planted.write_text(
        """jobs:
  verify:
    permissions:
      contents: write
    steps:
      - run: git tag -a example-v1 -m example HEAD
      - id: recorder-token
        if: always()
        uses: ./.github/actions/release-recorder-token
        with:
          client-id: ${{ vars.RELEASE_RECORDER_CLIENT_ID }}
          private-key: ${{ secrets.RELEASE_RECORDER_PRIVATE_KEY }}
      - if: always()
        env:
          GH_TOKEN: ${{ steps.recorder-token.outputs.token || secrets.GITHUB_TOKEN }}
        run: bash scripts/open_release_record_pr.sh
""",
        encoding="utf-8",
    )
    problems = _coverage_problems(_tagging_jobs(tmp_path), relative_to=tmp_path)
    assert problems == [
        "release-ungated.yml:verify must name the main-only registry-release "
        "credential environment"
    ]


def _token_permission_problems(inputs: dict[str, object]) -> list[str]:
    allowed = {
        "client-id",
        "private-key",
        "permission-contents",
        "permission-pull-requests",
    }
    problems = [
        f"unexpected recorder permission/input: {key}"
        for key in sorted(set(inputs) - allowed)
    ]
    for permission in ("permission-contents", "permission-pull-requests"):
        if inputs.get(permission) != "write":
            problems.append(f"{permission} must be write")
    return problems


def test_the_recorder_app_token_has_only_branch_and_pull_request_authority() -> None:
    """It cannot dispatch, approve a deployment or change repository settings."""
    action_path = REPO / ".github" / "actions" / "release-recorder-token" / "action.yml"
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    steps = action["runs"]["steps"]
    assert len(steps) == 2
    token_step, checkout_step = steps
    assert token_step["uses"] == PINNED_TOKEN_ACTION
    assert token_step["with"] == {
        "client-id": "${{ inputs.client-id }}",
        "private-key": "${{ inputs.private-key }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }
    assert _token_permission_problems(token_step["with"]) == []
    assert checkout_step["uses"] == PINNED_CHECKOUT_ACTION
    assert checkout_step["if"] == "always()"
    assert checkout_step["with"] == {
        "token": "${{ steps.token.outputs.token || github.token }}",
        "fetch-depth": 0,
        "persist-credentials": True,
    }


def test_recorder_checkout_guard_detects_the_publisher_credential() -> None:
    """Sensitivity: PR creation alone is not identity separation for git push."""
    planted = {
        "token": "${{ github.token }}",
        "fetch-depth": 0,
        "persist-credentials": True,
    }
    expected = {
        "token": "${{ steps.token.outputs.token || github.token }}",
        "fetch-depth": 0,
        "persist-credentials": True,
    }
    assert planted != expected


def test_recorder_permission_guard_detects_deployment_authority() -> None:
    """Sensitivity: a plausible privilege expansion must turn the guard RED."""
    inputs = {
        "client-id": "example",
        "private-key": "example",
        "permission-contents": "write",
        "permission-pull-requests": "write",
        "permission-deployments": "write",
    }
    assert _token_permission_problems(inputs) == [
        "unexpected recorder permission/input: permission-deployments"
    ]
