#!/usr/bin/env python3
"""Prove WHO created a governed module release tag, not just what it says.

`ModuleReleaseTagEvidence.v1` (rendered and strictly parsed in
`write_release_record.py`) proves a tag's WHEEL and its VERIFICATION run —
but nothing before this script proved that the run named in that evidence was
itself a legitimate, approved, successful release/recovery run on `main`, at
the tagged commit, whose own "Tag the ..." step actually succeeded. A forged
or hand-edited ledger row could otherwise name any run id at all.

This is the single owner of that provenance check. It is read-only against
GitHub's Actions API (no dependency beyond the standard library) and is run
by CI over every row a PR appends to
`docs/inventories/module-release-verifications.json` — never over the whole
ledger, since older rows were already checked when they were appended and
their runs may since have expired or been deleted from GitHub's retention
window.

Two run identities matter, and they may be the same run or different runs:

- ``verification_run_id`` — the run whose "Tag the verified/recovered
  release" step actually created and pushed the tag. It must be a completed,
  successful, `workflow_dispatch`-triggered run of an approved workflow, on
  `main`, in this repository, and its own tag step must have succeeded.
- ``source_run_id`` — the run that BUILT and PUBLISHED the wheel. For a
  normal release this is the SAME run as ``verification_run_id`` (checked by
  identity, plus the run's `head_sha` binding to the tagged commit). For a
  recovery it is the ORIGINAL failed release run: still an approved
  `workflow_dispatch` run on `main` in this repository, built at the tagged
  commit, but one that did NOT succeed — recovery only ever applies to a
  release that failed after publishing but before tagging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from write_release_record import ReleaseRecordError, parse_module_release_verifications

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = "docs/inventories/module-release-verifications.json"
_BASE_SHA = re.compile(r"[0-9a-f]{40}\Z")

#: Both workflows the "Tag the ..." step may run under — a release's own run,
#: or a recovery run acting on an ORIGINAL run's already-verified artifact.
APPROVED_VERIFICATION_WORKFLOWS = {
    ".github/workflows/release-module.yml",
    ".github/workflows/recover-module-release.yml",
}

#: The only workflow a SOURCE run (the one that built and published the
#: wheel) is ever allowed to be, whether it is also the verification run
#: (release) or a separate, failed, earlier run (recovery).
SOURCE_WORKFLOW = ".github/workflows/release-module.yml"


class ProvenanceError(RuntimeError):
    """The tag's named run(s) do not prove what the ledger row claims."""


class GitHubRuns(Protocol):
    """The two read-only GitHub Actions calls provenance checking needs."""

    def get_run(self, run_id: str) -> dict:
        """The workflow run object for ``run_id``."""

    def get_jobs(self, run_id: str) -> list[dict]:
        """Every job (with its steps) belonging to the run ``run_id``."""


class RestGitHubRuns:
    """The real GitHub REST API, read-only, stdlib-only.

    ``token`` defaults to the ``GITHUB_TOKEN`` environment variable so a
    caller need not thread it through by hand in the common case.
    """

    def __init__(
        self,
        *,
        repository: str,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        self._repository = repository
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def _get(self, path: str) -> dict:
        url = f"{self._api_url}/repos/{self._repository}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=self._timeout
            ) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ProvenanceError(
                f"GitHub API {url} returned {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProvenanceError(
                f"GitHub API {url} unreachable: {exc.reason}"
            ) from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProvenanceError(f"GitHub API {url} returned invalid JSON") from exc

    def get_run(self, run_id: str) -> dict:
        return self._get(f"/actions/runs/{run_id}")

    def get_jobs(self, run_id: str) -> list[dict]:
        payload = self._get(f"/actions/runs/{run_id}/jobs?per_page=100")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ProvenanceError(f"GitHub API returned no jobs list for run {run_id}")
        return jobs


def _require_run_identity(
    run: dict, *, run_id: str, expected_paths: set[str], repository: str, label: str
) -> None:
    if run.get("path") not in expected_paths:
        raise ProvenanceError(
            f"{label} run {run_id} is not an approved workflow: {run.get('path')!r}"
        )
    if run.get("event") != "workflow_dispatch":
        raise ProvenanceError(
            f"{label} run {run_id} was not triggered by workflow_dispatch "
            f"(event={run.get('event')!r})"
        )
    if run.get("head_branch") != "main":
        raise ProvenanceError(
            f"{label} run {run_id} did not run on main "
            f"(head_branch={run.get('head_branch')!r})"
        )
    if run.get("repository", {}).get("full_name") != repository:
        raise ProvenanceError(
            f"{label} run {run_id} is not in {repository!r}: "
            f"{run.get('repository', {}).get('full_name')!r}"
        )
    if str(run.get("id")) != str(run_id):
        raise ProvenanceError(
            f"{label} run id mismatch: requested {run_id}, API returned "
            f"{run.get('id')!r}"
        )


def _await_completed(
    runs: GitHubRuns,
    run: dict,
    *,
    run_id: str,
    wait_seconds: int,
    poll_seconds: int,
    sleep,
    label: str,
) -> dict:
    elapsed = 0
    while run.get("status") != "completed":
        if elapsed >= wait_seconds:
            raise ProvenanceError(
                f"{label} run {run_id} is still running after waiting "
                f"{wait_seconds}s"
            )
        sleep(poll_seconds)
        elapsed += poll_seconds
        run = runs.get_run(run_id)
    return run


def _require_step_succeeded(
    runs: GitHubRuns, run_id: str, step_name: str, *, label: str
) -> None:
    for job in runs.get_jobs(run_id):
        for step in job.get("steps", []):
            if step.get("name") == step_name:
                if step.get("conclusion") != "success":
                    raise ProvenanceError(
                        f"{label} run {run_id} step {step_name!r} did not "
                        f"succeed (conclusion={step.get('conclusion')!r})"
                    )
                return
    raise ProvenanceError(f"{label} run {run_id} has no step named {step_name!r}")


def verify_row_provenance(
    row: dict,
    *,
    peeled_commit: str,
    runs: GitHubRuns,
    repository: str,
    wait_seconds: int,
    poll_seconds: int,
    sleep=time.sleep,
) -> None:
    """Prove one ledger row's tag was created by an approved, successful run.

    Raises `ProvenanceError` naming the exact violated rule; never silently
    accepts an unproven row.
    """
    tag = row.get("tag")
    verification_run_id = row["verification_run_id"]

    run = runs.get_run(verification_run_id)
    _require_run_identity(
        run,
        run_id=verification_run_id,
        expected_paths=APPROVED_VERIFICATION_WORKFLOWS,
        repository=repository,
        label="verification",
    )
    run = _await_completed(
        runs,
        run,
        run_id=verification_run_id,
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        label="verification",
    )
    if run.get("conclusion") != "success":
        raise ProvenanceError(
            f"verification run {verification_run_id} did not succeed "
            f"(conclusion={run.get('conclusion')!r})"
        )

    is_release = run.get("path") == SOURCE_WORKFLOW
    tag_step_name = (
        "Tag the verified release" if is_release else "Tag the recovered release"
    )
    _require_step_succeeded(
        runs, verification_run_id, tag_step_name, label="verification"
    )

    if is_release:
        if row["source_run_id"] != row["verification_run_id"]:
            raise ProvenanceError(
                f"release tag {tag} names source_run_id "
                f"{row['source_run_id']!r} but verification_run_id is "
                f"{row['verification_run_id']!r} — a release's source and "
                "verification run must be the same run"
            )
        if run.get("head_sha") != peeled_commit:
            raise ProvenanceError(
                f"verification run {verification_run_id} built commit "
                f"{run.get('head_sha')!r}, not the tagged commit "
                f"{peeled_commit!r}"
            )
        return

    source_run_id = row["source_run_id"]
    if source_run_id == verification_run_id:
        raise ProvenanceError(
            f"recovery tag {tag} names the same run {verification_run_id!r} "
            "as both source and verification"
        )
    source_run = runs.get_run(source_run_id)
    if source_run.get("path") != SOURCE_WORKFLOW:
        raise ProvenanceError(
            f"recovery source run {source_run_id} is not {SOURCE_WORKFLOW}: "
            f"{source_run.get('path')!r}"
        )
    if source_run.get("event") != "workflow_dispatch":
        raise ProvenanceError(
            f"recovery source run {source_run_id} was not triggered by "
            f"workflow_dispatch (event={source_run.get('event')!r})"
        )
    if source_run.get("head_branch") != "main":
        raise ProvenanceError(
            f"recovery source run {source_run_id} did not run on main "
            f"(head_branch={source_run.get('head_branch')!r})"
        )
    if source_run.get("repository", {}).get("full_name") != repository:
        raise ProvenanceError(
            f"recovery source run {source_run_id} is not in {repository!r}: "
            f"{source_run.get('repository', {}).get('full_name')!r}"
        )
    if source_run.get("head_sha") != peeled_commit:
        raise ProvenanceError(
            f"recovery source run {source_run_id} built commit "
            f"{source_run.get('head_sha')!r}, not the tagged commit "
            f"{peeled_commit!r}"
        )
    if source_run.get("status") != "completed":
        raise ProvenanceError(
            f"recovery source run {source_run_id} has not completed "
            f"(status={source_run.get('status')!r})"
        )
    if source_run.get("conclusion") == "success":
        raise ProvenanceError(
            f"recovery source run {source_run_id} succeeded — recovery only "
            "applies to a failed release"
        )


def new_rows(base_text: str, head_text: str) -> list[dict]:
    """Rows present in HEAD but not BASE, by tag — never the whole ledger.

    Append-only already guarantees an older row was checked when it was
    appended, and its run may since have been deleted or expired from
    GitHub's retention window, so re-checking it here would only produce a
    false refusal.
    """
    base_rows = parse_module_release_verifications(base_text)
    head_rows = parse_module_release_verifications(head_text)
    added_tags = set(head_rows) - set(base_rows)
    return [row for tag, row in head_rows.items() if tag in added_tags]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="the immutable 40-hex base commit to diff against"
    )
    parser.add_argument("--wait-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=int, default=20)
    args = parser.parse_args(argv)

    if not _BASE_SHA.fullmatch(args.base):
        print(
            "module release provenance REFUSED: --base must be one immutable "
            "40-hex commit",
            file=sys.stderr,
        )
        return 2

    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        print(
            "module release provenance REFUSED: GITHUB_REPOSITORY is not set",
            file=sys.stderr,
        )
        return 2

    base_result = _git("show", f"{args.base}:{LEDGER_PATH}")
    if base_result.returncode != 0:
        print(
            f"module release provenance REFUSED: base {args.base} has no "
            f"{LEDGER_PATH}",
            file=sys.stderr,
        )
        return 2
    head_result = _git("show", f"HEAD:{LEDGER_PATH}")
    if head_result.returncode != 0:
        print(
            f"module release provenance REFUSED: HEAD has no {LEDGER_PATH}",
            file=sys.stderr,
        )
        return 2

    try:
        rows = new_rows(base_result.stdout, head_result.stdout)
    except ReleaseRecordError as failure:
        print(f"module release provenance REFUSED: {failure}", file=sys.stderr)
        return 1

    if not rows:
        print("no new verified rows")
        return 0

    runs = RestGitHubRuns(repository=repository)

    for row in rows:
        tag = row["tag"]
        commit_result = _git("rev-parse", f"{tag}^{{commit}}")
        if commit_result.returncode != 0:
            print(
                f"module release provenance REFUSED: cannot resolve {tag} to a "
                "commit",
                file=sys.stderr,
            )
            return 1
        peeled_commit = commit_result.stdout.strip()
        if peeled_commit != row["peeled_commit"]:
            print(
                f"module release provenance REFUSED: {tag} peeled commit "
                f"{peeled_commit} disagrees with ledger row "
                f"{row['peeled_commit']}",
                file=sys.stderr,
            )
            return 1
        try:
            verify_row_provenance(
                row,
                peeled_commit=peeled_commit,
                runs=runs,
                repository=repository,
                wait_seconds=args.wait_seconds,
                poll_seconds=args.poll_seconds,
            )
        except ProvenanceError as failure:
            print(
                f"module release provenance REFUSED for {tag}: {failure}",
                file=sys.stderr,
            )
            return 1
        print(
            f"verified provenance for {tag} (verification run "
            f"{row['verification_run_id']}, source run {row['source_run_id']})"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
