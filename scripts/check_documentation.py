"""Check changed Markdown links and opted-in feature guides without network access."""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

FEATURE_MARKER = "<!-- starter-feature-doc:v1 -->"
FEATURE_SECTIONS = (
    "Purpose and audience",
    "Ownership and availability",
    "Setup",
    "Usage",
    "Permissions and data boundaries",
    "Failure and recovery",
    "Validation",
    "Limits and compatibility",
)


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def prose(source: str) -> str:
    result: list[str] = []
    fence = ""
    for line in source.splitlines():
        match = re.match(r"\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if not fence:
            result.append(line)
    return "\n".join(result)


def destinations(source: str) -> tuple[str, ...]:
    """Inline/image links and reference definitions, excluding code samples.

    Deliberately not a complete CommonMark/HTML parser. Unsupported constructs
    and external URLs are outside this gate's claim.
    """
    body = re.sub(r"`[^`\n]*`", "", prose(source))
    inline = re.findall(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s()]+))", body)
    references = re.findall(
        r"^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))", body, re.M
    )
    return tuple(a or b for a, b in (*inline, *references))


def anchors(source: str) -> frozenset[str]:
    body = prose(source)
    result = set(re.findall(r'\b(?:id|name)=["\x27]([^"\x27]+)', body))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", body, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        index = counts.get(slug, 0)
        counts[slug] = index + 1
        result.add(f"{slug}-{index}" if index else slug)
    return frozenset(result)


def target(path: str, destination: str) -> tuple[str, str] | None:
    url = urlsplit(destination)
    if url.scheme or url.netloc:
        return None
    local = unquote(url.path)
    resolved = posixpath.normpath(
        local.lstrip("/")
        if local.startswith("/")
        else posixpath.join(posixpath.dirname(path), local)
        if local
        else path
    )
    if resolved == ".." or resolved.startswith("../"):
        raise ValueError("local link escapes the repository")
    return resolved, unquote(url.fragment)


def check_page(
    *,
    path: str,
    source: str,
    tracked: frozenset[str],
    read: Callable[[str], str],
    deleted_only: frozenset[str] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for destination in destinations(source):
        try:
            resolved = target(path, destination)
        except ValueError:
            if deleted_only is None:
                findings.append(Finding(path, "invalid local link"))
            continue
        if resolved is None:
            continue
        name, fragment = resolved
        if deleted_only is not None and name not in deleted_only:
            continue
        exists = name in tracked or any(
            p.startswith(name.rstrip("/") + "/") for p in tracked
        )
        if not exists:
            findings.append(Finding(path, f"missing local target: {name}"))
        elif fragment and name.endswith(".md") and fragment not in anchors(read(name)):
            findings.append(
                Finding(path, f"missing Markdown heading: {name}#{fragment}")
            )
    if (
        deleted_only is None
        and FEATURE_MARKER in source
        and path != "docs/templates/feature.md"
    ):
        sections = dict(
            re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", source, re.M | re.S)
        )
        for heading in FEATURE_SECTIONS:
            value = sections.get(heading, "").strip()
            if not value or "TODO:" in value:
                findings.append(Finding(path, f"complete feature section: {heading}"))
    return tuple(findings)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])  # nosec B603,B607


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    for ref in (args.base, args.head):
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            parser.error("base/head must be full lowercase commit SHAs")
    ancestor = git("merge-base", args.base, args.head).decode().strip()
    tracked = frozenset(
        p.decode()
        for p in git("ls-tree", "-r", "--name-only", "-z", args.head).split(b"\0")
        if p
    )
    changed = frozenset(
        p.decode()
        for p in git(
            "diff", "--name-only", "--no-renames", "-z", ancestor, args.head
        ).split(b"\0")
        if p
    )
    removed = changed - tracked

    def read(path: str) -> str:
        return git("show", f"{args.head}:{path}").decode("utf-8")

    findings: list[Finding] = []
    inspected = 0
    for path in sorted(tracked):
        if not path.endswith(".md") or (path not in changed and not removed):
            continue
        inspected += 1
        findings.extend(
            check_page(
                path=path,
                source=read(path),
                tracked=tracked,
                read=read,
                deleted_only=None if path in changed else removed,
            )
        )
    for finding in findings:
        print(f"{finding.path}: {finding.reason}")
    print(
        f"Documentation check: {inspected} pages inspected, {len(findings)} findings."
    )
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
