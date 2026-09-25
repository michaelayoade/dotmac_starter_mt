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

The Actions runs API exposes no dispatch inputs, so a real, successful run for
module A at commit X is otherwise indistinguishable from a forged ledger row
naming that same run for module B at the same commit. Both release workflows
therefore declare a top-level ``run-name`` binding the dispatched module and
version into the run's own ``display_title`` (``Release module <dist>
<version>`` / ``Recover module <dist> <version>``), and this script checks it
exactly against every row it verifies. A run dispatched BEFORE this change
carries no such title and cannot be proven this way — acceptable, since no
governed module has a verified row yet.
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
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import release_authority
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
RECOVERY_WORKFLOW = ".github/workflows/recover-module-release.yml"


class ProvenanceError(RuntimeError):
    """The tag's named run(s) do not prove what the ledger row claims."""


class GitHubRuns(Protocol):
    """The three read-only GitHub Actions calls provenance checking needs."""

    def get_run(self, run_id: str) -> dict:
        """The workflow run object for ``run_id``."""

    def get_jobs(self, run_id: str) -> list[dict]:
        """Every job (with its steps) belonging to the run ``run_id``."""

    def get_workflow(self, workflow_id: object) -> dict:
        """The workflow definition object owning ``workflow_id``.

        Binds a run to its workflow BY ID rather than trusting the run
        object's own ``path`` field in isolation — the run's `path` and the
        workflow fetched independently by `workflow_id` must name the same
        file, and that file must be one of the approved ones.
        """


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
        # One page is the whole run: both approved workflows have at most
        # three jobs. A count beyond one page means this is not one of them.
        total = payload.get("total_count")
        if not isinstance(total, int) or total > len(jobs):
            raise ProvenanceError(
                f"GitHub API returned {len(jobs)} of {total!r} jobs for run "
                f"{run_id}; refusing a partial job list"
            )
        return jobs

    def get_workflow(self, workflow_id: object) -> dict:
        return self._get(f"/actions/workflows/{workflow_id}")


def _require_workflow_binding(
    runs: GitHubRuns,
    run: dict,
    *,
    run_id: str,
    expected_paths: set[str],
    label: str,
) -> None:
    """The run's own ``path`` field is not trusted alone.

    A tag-ref `workflow_dispatch` can make GitHub report the dispatched
    ref's NAME as `head_branch`; this binding does NOT close that (a forged
    run at the approved path has the same `workflow_id`) — the first-parent
    check on main does. It only ensures the approved path is re-derived from
    `workflow_id` by a second API call rather than trusted from the run
    object alone.
    """
    workflow_id = run.get("workflow_id")
    if workflow_id is None:
        raise ProvenanceError(f"{label} run {run_id} has no workflow_id")
    workflow = runs.get_workflow(workflow_id)
    workflow_path = workflow.get("path")
    if workflow_path != run.get("path"):
        raise ProvenanceError(
            f"{label} run {run_id} workflow {workflow_id} path "
            f"{workflow_path!r} disagrees with the run's own path "
            f"{run.get('path')!r}"
        )
    if workflow_path not in expected_paths:
        raise ProvenanceError(
            f"{label} run {run_id} workflow {workflow_id} is not an "
            f"approved workflow: {workflow_path!r}"
        )


def _require_run_identity(
    run: dict,
    *,
    run_id: str,
    expected_paths: set[str],
    repository: str,
    label: str,
    distribution: str,
    version: str,
    runs: GitHubRuns,
    is_on_main: Callable[[str], bool],
) -> None:
    # The workflow's approval is decided from the INDEPENDENTLY fetched
    # `get_workflow(workflow_id)` result, not from the run object's own
    # self-reported `path` — that field is only used to prove agreement
    # with the fetched workflow (below), never as the source of truth for
    # which workflow ran.
    _require_workflow_binding(
        runs, run, run_id=run_id, expected_paths=expected_paths, label=label
    )
    expected_title = (
        f"Release module {distribution} {version}"
        if run.get("path") == SOURCE_WORKFLOW
        else f"Recover module {distribution} {version}"
    )
    if run.get("display_title") != expected_title:
        raise ProvenanceError(
            f"{label} run {run_id} display_title {run.get('display_title')!r} "
            f"does not bind module {distribution!r} version {version!r} "
            f"(expected {expected_title!r})"
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
    # `head_branch == "main"` alone is spoofable: a `workflow_dispatch`
    # against a REF (e.g. a tag) literally named `main` makes GitHub report
    # `head_branch: "main"` even though that ref is not the protected
    # branch and can point at an arbitrary commit. The proof is the commit
    # graph: the run's head commit must be on main's first-parent line
    # (see `_is_on_main`).
    head_sha = run.get("head_sha")
    if not is_on_main(head_sha):
        raise ProvenanceError(
            f"{label} run {run_id} head commit {head_sha!r} is not on the "
            "first-parent line of refs/remotes/origin/main"
        )
    for field in ("repository", "head_repository"):
        owner = _full_name(run, field)
        if owner != repository:
            raise ProvenanceError(
                f"{label} run {run_id} {field} is not {repository!r}: {owner!r}"
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


def _full_name(run: dict, field: str) -> str | None:
    """``run[field]["full_name"]``, or None when the field is absent/null —
    a missing repository is a refusal, never an AttributeError."""
    value = run.get(field)
    if not isinstance(value, dict):
        return None
    name = value.get("full_name")
    return name if isinstance(name, str) else None


def _require_step_succeeded(
    runs: GitHubRuns, run_id: str, step_name: str, *, job_name: str, label: str
) -> None:
    # The step must sit in the ONE job that owns tag creation (`verify` in
    # release-module.yml, `recover` in recover-module-release.yml — neither
    # sets `name:`, so GitHub reports the job key). A same-named step in any
    # other job proves nothing.
    for job in runs.get_jobs(run_id):
        if job.get("name") != job_name:
            continue
        for step in job.get("steps", []):
            if step.get("name") == step_name:
                if step.get("conclusion") != "success":
                    raise ProvenanceError(
                        f"{label} run {run_id} step {step_name!r} did not "
                        f"succeed (conclusion={step.get('conclusion')!r})"
                    )
                return
    raise ProvenanceError(
        f"{label} run {run_id} has no step named {step_name!r} in job {job_name!r}"
    )


class ReleaseAuthorityView(Protocol):
    """What provenance needs from `release_authority.py`, injectable for tests.

    Authority is decided at EXECUTION time: the digest the ledger at the
    release run's exact commit marked active. A later authority change can
    never invalidate an already authorized release, and a release PR can
    never invent its own historical authority, because the digest must also
    already sit in the BASE branch's append-only history.
    """

    def base_history(self) -> set[str]:
        """The append-only authority history on the pull request's base."""

    def active_at(self, commit: str) -> str | None:
        """The ledger's active digest at ``commit``, or None if it had none."""

    def reconstruct_at(self, commit: str) -> str:
        """The digest re-derived from ``commit``'s bytes with its own ledger's
        declared surface."""


def _require_authorized_commit(
    authority: ReleaseAuthorityView,
    commit: str,
    *,
    digest: str | None,
    label: str,
) -> str:
    """``commit`` executed under an authority the base branch already holds.

    The ledger at ``commit`` must mark a digest active, that digest must be in
    the base branch's history, and ``commit``'s own bytes must reconstruct it.
    When ``digest`` is given (the one the tag and row carry) it must be
    exactly that active digest. Returns the authorized digest.
    """
    active = authority.active_at(commit)
    if active is None:
        raise ProvenanceError(
            f"{label} commit {commit} carries no active release authority"
        )
    if digest is not None and active != digest:
        raise ProvenanceError(
            f"{label} commit {commit} marked {active!r} active, but the tag "
            f"and row carry {digest!r}"
        )
    if active not in authority.base_history():
        raise ProvenanceError(
            f"{label} authority {active!r} is not in the base branch's "
            "append-only authority history"
        )
    rebuilt = authority.reconstruct_at(commit)
    if rebuilt != active:
        raise ProvenanceError(
            f"{label} commit {commit} reconstructs authority {rebuilt!r}, not "
            f"its declared active {active!r}"
        )
    return active


def verify_row_provenance(
    row: dict,
    *,
    peeled_commit: str,
    runs: GitHubRuns,
    repository: str,
    wait_seconds: int,
    poll_seconds: int,
    is_on_main: Callable[[str], bool],
    authority: ReleaseAuthorityView,
    sleep=time.sleep,
) -> None:
    """Prove one ledger row's tag was created by an approved, successful run.

    ``is_on_main`` decides whether a commit is on the first-parent line of
    the real main tip — never `head_branch == "main"` alone, which a
    `workflow_dispatch` against a ref literally named `main` can spoof.

    Raises `ProvenanceError` naming the exact violated rule; never silently
    accepts an unproven row.
    """
    tag = row.get("tag")
    verification_run_id = row["verification_run_id"]
    distribution = row["distribution"]
    version = row["version"]

    if not is_on_main(peeled_commit):
        raise ProvenanceError(
            f"tag {tag} peeled commit {peeled_commit!r} is not on the "
            "first-parent line of refs/remotes/origin/main"
        )

    row_digest = row["release_authority_digest"]
    if row.get("adopting_run_id") is not None:
        _verify_adopted_row(
            row,
            peeled_commit=peeled_commit,
            runs=runs,
            repository=repository,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            is_on_main=is_on_main,
            authority=authority,
            sleep=sleep,
        )
        return

    run = runs.get_run(verification_run_id)
    _require_run_identity(
        run,
        run_id=verification_run_id,
        expected_paths=APPROVED_VERIFICATION_WORKFLOWS,
        repository=repository,
        label="verification",
        distribution=distribution,
        version=version,
        runs=runs,
        is_on_main=is_on_main,
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
        runs,
        verification_run_id,
        tag_step_name,
        job_name="verify" if is_release else "recover",
        label="verification",
    )
    # The run that wrote the tag executed under the row's authority.
    _require_authorized_commit(
        authority, run["head_sha"], digest=row_digest, label="verification run"
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
    _require_run_identity(
        source_run,
        run_id=source_run_id,
        expected_paths={SOURCE_WORKFLOW},
        repository=repository,
        label="recovery source",
        distribution=distribution,
        version=version,
        runs=runs,
        is_on_main=is_on_main,
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


def _verify_adopted_row(
    row: dict,
    *,
    peeled_commit: str,
    runs: GitHubRuns,
    repository: str,
    wait_seconds: int,
    poll_seconds: int,
    is_on_main: Callable[[str], bool],
    authority: ReleaseAuthorityView,
    sleep,
) -> None:
    """A recovery ADOPTED a tag the original release run had written.

    The original run wrote the tag (it is both verification and source run),
    its publication and tag step succeeded, and it failed only afterwards; its
    commit authorizes the row's digest. The adopting recovery run is a
    different, successful, authorized recovery run for the same release.
    """
    tag = row.get("tag")
    distribution = row["distribution"]
    version = row["version"]
    original_id = row["verification_run_id"]
    adopting_id = row["adopting_run_id"]
    if row["source_run_id"] != original_id:
        raise ProvenanceError(
            f"adopted tag {tag} must name the original run as both source and "
            "verification run"
        )
    original = runs.get_run(original_id)
    _require_run_identity(
        original,
        run_id=original_id,
        expected_paths={SOURCE_WORKFLOW},
        repository=repository,
        label="adopted original",
        distribution=distribution,
        version=version,
        runs=runs,
        is_on_main=is_on_main,
    )
    if original.get("head_sha") != peeled_commit:
        raise ProvenanceError(
            f"adopted original run {original_id} built "
            f"{original.get('head_sha')!r}, not the tagged commit {peeled_commit!r}"
        )
    if original.get("status") != "completed" or original.get("conclusion") == (
        "success"
    ):
        raise ProvenanceError(
            f"adopted original run {original_id} must be a completed, failed "
            "run — a successful run needs no recovery"
        )
    _require_job_succeeded(runs, original_id, "publish", label="adopted original")
    _require_step_succeeded(
        runs,
        original_id,
        "Tag the verified release",
        job_name="verify",
        label="adopted original",
    )
    _require_authorized_commit(
        authority,
        original["head_sha"],
        digest=row["release_authority_digest"],
        label="adopted original run",
    )

    adopting = runs.get_run(adopting_id)
    _require_run_identity(
        adopting,
        run_id=adopting_id,
        expected_paths={RECOVERY_WORKFLOW},
        repository=repository,
        label="adopting recovery",
        distribution=distribution,
        version=version,
        runs=runs,
        is_on_main=is_on_main,
    )
    adopting = _await_completed(
        runs,
        adopting,
        run_id=adopting_id,
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        label="adopting recovery",
    )
    if adopting.get("conclusion") != "success":
        raise ProvenanceError(
            f"adopting recovery run {adopting_id} did not succeed "
            f"(conclusion={adopting.get('conclusion')!r})"
        )
    _require_step_succeeded(
        runs,
        adopting_id,
        "Tag the recovered release",
        job_name="recover",
        label="adopting recovery",
    )
    _require_authorized_commit(
        authority, adopting["head_sha"], digest=None, label="adopting recovery run"
    )


def _require_job_succeeded(
    runs: GitHubRuns, run_id: str, job_name: str, *, label: str
) -> None:
    for job in runs.get_jobs(run_id):
        if job.get("name") == job_name:
            if job.get("conclusion") != "success":
                raise ProvenanceError(
                    f"{label} run {run_id} job {job_name!r} did not succeed "
                    f"(conclusion={job.get('conclusion')!r})"
                )
            return
    raise ProvenanceError(f"{label} run {run_id} has no job named {job_name!r}")


def refuse_authority_change_with_new_rows(
    base_ledger_text: str | None, head_ledger_text: str | None, added: list[dict]
) -> None:
    """Authority changes and new release rows never share a pull request.

    Any byte change to the authority ledger is an authority change; a base
    with no ledger has no authority to grant, so it admits no new row either.
    """
    if not added:
        return
    if base_ledger_text is None:
        raise ProvenanceError(
            "the base branch has no release-authority ledger; no release row "
            "can be authorized in this pull request"
        )
    if head_ledger_text != base_ledger_text:
        raise ProvenanceError(
            "this change edits the release-authority ledger AND adds release "
            "rows; authority changes must land in a separate reviewed pull "
            "request first"
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


MAIN_REF = "refs/remotes/origin/main"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _is_on_main(commit: str | None) -> bool:
    """Is ``commit`` on the FIRST-PARENT line of the real ``main`` tip?

    `head_branch == "main"` only reports a REF NAME, which a dispatch against
    a tag literally named `main` can spoof, so the proof is the commit graph.
    Three details carry the weight:

    - The tip is resolved from the fully qualified ``refs/remotes/origin/main``
      (fetched by `actions/checkout` with `fetch-depth: 0`). A short
      ``origin/main`` would resolve a TAG named ``origin/main`` first
      (gitrevisions(7): ``refs/tags/<name>`` precedes ``refs/remotes/<name>``),
      and checkout fetches every tag.
    - Membership is on the FIRST-PARENT line, not mere ancestry: the ruleset
      allows merge and rebase merges, so a commit added and reverted inside a
      merged branch is an ancestor of main without ever having BEEN main. A
      release runs on main's tip (`assert_current_main.sh`), so every
      legitimate run commit is a first-parent commit.
    - Every commit is validated as 40 lowercase hex before it reaches git.
    """
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        return False
    return commit in _main_first_parent_line(REPO_ROOT)


_FIRST_PARENT_CACHE: dict[Path, frozenset[str]] = {}


def _main_first_parent_line(root: Path) -> frozenset[str]:
    """Main's first-parent commits, computed once per repository per process.

    ``show-ref --verify`` matches ONLY the exact ref — unlike ``rev-parse``,
    it has no fallback to ``refs/tags/<name>`` if the remote-tracking ref were
    ever missing.
    """
    cached = _FIRST_PARENT_CACHE.get(root)
    if cached is not None:
        return cached
    tip = _git("show-ref", "--verify", "--hash", MAIN_REF)
    tip_sha = tip.stdout.strip()
    if tip.returncode != 0 or not _COMMIT.fullmatch(tip_sha):
        raise ProvenanceError(f"cannot resolve {MAIN_REF} exactly")
    first_parent = _git("rev-list", "--first-parent", tip_sha)
    if first_parent.returncode != 0:
        raise ProvenanceError(f"cannot list the first-parent history of {MAIN_REF}")
    line = frozenset(first_parent.stdout.split())
    _FIRST_PARENT_CACHE[root] = line
    return line


class GitReleaseAuthority:
    """`ReleaseAuthorityView` over this checkout's git objects.

    The base history comes from the base commit's ledger; a commit's active
    digest from the ledger AT that commit; the reconstruction re-derives the
    surface from that commit's bytes, requires it to equal the ledger's
    declared surface there, and digests exactly those bytes.
    """

    def __init__(self, base_ledger_text: str) -> None:
        self._base = release_authority.parse_authority_ledger(base_ledger_text)

    def base_history(self) -> set[str]:
        return set(self._base["history"])

    def _ledger_at(self, commit: str) -> dict | None:
        if not _COMMIT.fullmatch(commit or ""):
            raise ProvenanceError(f"not a commit: {commit!r}")
        shown = _git("show", f"{commit}:{release_authority.LEDGER_PATH}")
        if shown.returncode != 0:
            return None
        try:
            return release_authority.parse_authority_ledger(shown.stdout)
        except release_authority.ReleaseAuthorityError as failure:
            raise ProvenanceError(
                f"authority ledger at {commit} is malformed: {failure}"
            ) from failure

    def active_at(self, commit: str) -> str | None:
        ledger = self._ledger_at(commit)
        return None if ledger is None else ledger["active"]["digest"]

    def reconstruct_at(self, commit: str) -> str:
        ledger = self._ledger_at(commit)
        if ledger is None:
            raise ProvenanceError(f"no authority ledger at {commit}")
        declared_files = list(ledger["active"]["files"])
        declared_external = list(ledger["active"]["external_imports"])
        try:
            read = release_authority.commit_reader(REPO_ROOT, commit)
            files, external = release_authority.derive_surface(read)
            if (sorted(files), sorted(external)) != (
                sorted(declared_files),
                sorted(declared_external),
            ):
                raise ProvenanceError(
                    f"authority surface derived at {commit} differs from the "
                    "surface its ledger declares"
                )
            return release_authority.reconstruct_at(
                REPO_ROOT, commit, declared_files, declared_external
            )
        except release_authority.ReleaseAuthorityError as failure:
            raise ProvenanceError(
                f"cannot reconstruct authority at {commit}: {failure}"
            ) from failure


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

    base_authority = _git("show", f"{args.base}:{release_authority.LEDGER_PATH}")
    head_authority = _git("show", f"HEAD:{release_authority.LEDGER_PATH}")
    base_authority_text = (
        base_authority.stdout if base_authority.returncode == 0 else None
    )
    head_authority_text = (
        head_authority.stdout if head_authority.returncode == 0 else None
    )
    try:
        refuse_authority_change_with_new_rows(
            base_authority_text, head_authority_text, rows
        )
    except ProvenanceError as failure:
        print(f"module release provenance REFUSED: {failure}", file=sys.stderr)
        return 1

    if not rows:
        print("no new verified rows")
        return 0

    runs = RestGitHubRuns(repository=repository)
    assert base_authority_text is not None  # refused above when rows exist
    authority = GitReleaseAuthority(base_authority_text)

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
                is_on_main=_is_on_main,
                authority=authority,
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
