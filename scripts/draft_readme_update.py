"""Trusted GitHub automation: draft only README, never execute PR code."""

from __future__ import annotations

import base64
import html
import json
import os
import re
from http.client import HTTPSConnection
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from scripts.check_readme_update import DRAFT_MARKER, requires_readme


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected an API object")
    return cast(dict[str, object], value)


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected API text")
    return value


def sha(value: object) -> str:
    result = text(value)
    if re.fullmatch(r"[0-9a-f]{40}", result) is None:
        raise ValueError("Expected a full commit SHA")
    return result


@dataclass(frozen=True)
class DraftInput:
    title: str
    description: str
    number: int
    repository: str
    source_sha: str
    paths: tuple[str, ...]


def render_draft(*, readme: str, change: DraftInput) -> str:
    """A factual first draft from author prose and paths, not AI inference."""
    if DRAFT_MARKER in readme:
        raise ValueError("A draft already needs review")
    title = html.escape(" ".join(change.title.split())[:180])
    # Quote the author's first paragraph, so PR text cannot introduce markup
    # outside the draft section or masquerade as verified deployment evidence.
    paragraph = change.description.strip().split("\n\n")[0][:1200]
    summary = " ".join(paragraph.split()) or change.title
    modules = sorted({"/".join(p.split("/")[:2]) for p in change.paths})[:12]
    paths = ", ".join(html.escape(module) for module in modules)
    url = f"https://github.com/{change.repository}/pull/{change.number}"
    return (
        readme.rstrip() + "\n\n" + DRAFT_MARKER + "\n"
        "## Proposed change for review\n\n"
        f"**{title}** â€” [PR #{change.number}]({url}).\n\n"
        f"Author's description:\n\n<blockquote>{html.escape(summary)}</blockquote>\n\n"
        f"Implementation areas: <code>{paths}</code>.\n\n"
        "Review this draft, place the explanation in the appropriate README "
        "section, and add any required usage or setup instructions. "
        "This draft does not establish release, adoption or deployment.\n\n"
        f"Draft based on source commit {change.source_sha}. "
        "Remove the pending-review marker only after human review.\n"
    )


@dataclass(frozen=True, repr=False)
class GitHub:
    repository: str
    token: str

    def call(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> object:
        connection = HTTPSConnection("api.github.com", timeout=30)
        try:
            connection.request(
                method,
                f"/repos/{self.repository}/{path}",
                body=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "dotmac-starter-readme-draft",
                },
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise RuntimeError(f"GitHub request refused: HTTP {response.status}")
            result: object = json.loads(response.read())
            return result
        finally:
            connection.close()


def publish(*, github: GitHub, number: int, expected_sha: str) -> bool:
    pr = record(github.call("GET", f"pulls/{number}"))
    head = record(pr["head"])
    if (
        pr["state"] != "open"
        or record(head["repo"])["full_name"] != github.repository
        or sha(head["sha"]) != expected_sha
    ):
        return False
    # GitHub's changed_files is checked against the enumerated population.
    count = pr["changed_files"]
    if not isinstance(count, int) or count > 1000:
        raise ValueError("PR file extent cannot be established within draft limits")
    paths: list[str] = []
    previous: list[str] = []
    for page in range(1, (count + 99) // 100 + 1):
        values = github.call("GET", f"pulls/{number}/files?per_page=100&page={page}")
        if not isinstance(values, list):
            raise ValueError("Expected file list")
        for item in values:
            file = record(item)
            paths.append(text(file["filename"]))
            if "previous_filename" in file:
                previous.append(text(file["previous_filename"]))
    if len(paths) != count:
        raise ValueError("Incomplete PR file inventory")
    paths.extend(previous)
    # Never overwrite an author-written README, including a reviewed draft.
    if "README.md" in paths or not requires_readme(tuple(paths)):
        return False
    existing = record(github.call("GET", f"contents/README.md?ref={expected_sha}"))
    readme = base64.b64decode(text(existing["content"])).decode("utf-8")
    body = pr.get("body")
    change = DraftInput(
        title=text(pr["title"]),
        description=text(body) if body is not None else "",
        number=number,
        repository=github.repository,
        source_sha=expected_sha,
        paths=tuple(paths),
    )
    draft = render_draft(readme=readme, change=change)
    commit = record(github.call("GET", f"git/commits/{expected_sha}"))
    tree = record(
        github.call(
            "POST",
            "git/trees",
            {
                "base_tree": sha(record(commit["tree"])["sha"]),
                "tree": [
                    {
                        "path": "README.md",
                        "mode": "100644",
                        "type": "blob",
                        "content": draft,
                    }
                ],
            },
        )
    )
    created = record(
        github.call(
            "POST",
            "git/commits",
            {
                "message": "docs: draft README update for human review",
                "tree": sha(tree["sha"]),
                "parents": [expected_sha],
            },
        )
    )
    # A sibling update is not a fast-forward. Never force; concurrent changes
    # refuse publication rather than overwriting the contributor's branch.
    branch = quote(text(head["ref"]), safe="")
    github.call(
        "PATCH",
        f"git/refs/heads/{branch}",
        {
            "sha": sha(created["sha"]),
            "force": False,
        },
    )
    return True


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "michaelayoade/dotmac_starter_mt":
        raise ValueError("This automation targets Dotmac Starter only")
    event = record(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()))
    run = record(event["workflow_run"])
    if (
        run["event"] != "pull_request"
        or record(run["repository"])["full_name"] != repository
    ):
        return
    pulls = run["pull_requests"]
    if not isinstance(pulls, list):
        raise ValueError("Expected workflow PR associations")
    github = GitHub(repository, os.environ["GH_TOKEN"])
    for item in pulls:
        number = record(item)["number"]
        if not isinstance(number, int) or number <= 0:
            raise ValueError("Expected a PR number")
        changed = publish(
            github=github, number=number, expected_sha=sha(run["head_sha"])
        )
        print(
            f"PR #{number}: "
            + ("README draft published for review" if changed else "no draft needed")
        )


if __name__ == "__main__":
    main()
