#!/usr/bin/env python3
"""``CandidateArtifact.v1`` — the six facts that make candidate bytes re-fetchable.

A bootstrap candidate is built once and then depended on by a restore proof, an
issuer stand-up and a Lane 3 rehearsal before anything publishes it. Every one
of those produces a receipt naming the candidate, so the candidate's own record
has to be enough to *find the exact bytes again* — not merely to recognise them
if someone hands them to you.

## Why six and not one

- ``source_sha`` — which tree it was built from.
- ``run_id`` — which execution produced it.
- ``artifact_id`` — the addressable handle. A run can hold several artifacts,
  so the run ID alone does not say which bytes came out.
- ``filename`` — which file inside the artifact is the wheel.
- ``size_bytes`` — a cheap independent check that a re-fetch got the whole thing.
- ``sha256`` — identity. But a digest alone lets you *verify* bytes somebody
  gives you; it does not let you *obtain* them, which is the job here.

Dropping any one of them turns "re-fetch the candidate" into a search.

## expires_at is READ, never assumed

`retention-days: 90` is what the workflow REQUESTS. What matters is what the
artifact actually carries, because a repository- or org-level retention cap
silently lowers it and the difference is invisible until the bytes are gone. So
this reads `expires_at` back from the artifacts API and records that.

## The rule this file exists to make mechanical

**If the artifact expires or becomes unavailable, every dependent receipt is
invalidated and bootstrap restarts with a new candidate digest. Rebuilding and
claiming continuity is forbidden.**

At the moment it bites, a rebuild will look identical, cost minutes and be
wrong: the downstream receipts name a digest, and re-deriving bytes that happen
to match is a claim rather than a proof. `check` exists so that "is this
candidate still usable?" is a command rather than a recollection, and it refuses
on a margin — see :data:`MINIMUM_REMAINING_DAYS`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 -- argv list, shell=False; gh CLI only
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "CandidateArtifact.v1"

#: Bootstrap spans a restore proof, an issuer stand-up and a full Lane 3
#: rehearsal. Starting one with less than this left is how a candidate
#: evaporates mid-sequence, and a candidate that evaporates mid-sequence is
#: worse than one that was never built: it invalidates work already done.
MINIMUM_REMAINING_DAYS = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gh_api(path: str) -> object:
    """Read-only GitHub API call through the `gh` CLI.

    `gh` rather than a raw request so the job token is used the same way every
    other workflow in this repository uses it, and so this needs no HTTP
    dependency.
    """
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"gh api {path} failed ({result.returncode}): {result.stderr.strip()}. "
            "Recording a candidate needs `actions: read`; without it the "
            "artifacts endpoint returns 403 and the receipt would have to guess "
            "the artifact id and the real expiry"
        )
    return json.loads(result.stdout)


def _find_wheel(dist: Path) -> Path:
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one wheel in {dist}, found "
            f"{[w.name for w in wheels]}. A candidate names ONE artifact; two "
            "wheels means the downstream receipts cannot say which bytes they "
            "depended on"
        )
    return wheels[0]


def cmd_record(args: argparse.Namespace) -> int:
    dist = Path(args.dist)
    wheel = _find_wheel(dist)
    payload = _gh_api(f"repos/{args.repository}/actions/runs/{args.run_id}/artifacts")
    if not isinstance(payload, dict):
        raise SystemExit("unexpected artifacts payload")
    matching = [
        item
        for item in payload.get("artifacts", [])
        if isinstance(item, dict) and item.get("name") == args.artifact_name
    ]
    if len(matching) != 1:
        raise SystemExit(
            f"expected exactly one artifact named {args.artifact_name!r} in run "
            f"{args.run_id}, found {len(matching)}"
        )
    artifact = matching[0]

    receipt = {
        "schema": SCHEMA,
        "facility": args.facility,
        "version": args.version,
        "repository": args.repository,
        # ── the six ──
        "source_sha": args.source_sha,
        "run_id": str(args.run_id),
        "artifact_id": str(artifact["id"]),
        "filename": wheel.name,
        "size_bytes": wheel.stat().st_size,
        "sha256": _sha256(wheel),
        # ── the expiry, as READ ──
        "expires_at": artifact.get("expires_at"),
        "retention_requested_days": 90,
        "artifact_size_bytes": artifact.get("size_in_bytes"),
        "published": False,
        "tagged": False,
    }
    Path(args.out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    lines = [
        "## Foundation candidate — NOT published, NOT tagged",
        "",
        "These exact bytes are the only admissible input to the restore proof,",
        "the issuer bootstrap and Lane 3. Address them by run and artifact id;",
        "never by 'latest'. **If this artifact expires or becomes unavailable,**",
        "**invalidate every dependent receipt and restart with a new candidate**",
        "**digest — rebuilding and claiming continuity is forbidden.**",
        "",
        "| fact | value |",
        "|---|---|",
    ]
    for key in (
        "facility",
        "version",
        "source_sha",
        "run_id",
        "artifact_id",
        "filename",
        "size_bytes",
        "sha256",
        "expires_at",
    ):
        lines.append(f"| `{key}` | `{receipt[key]}` |")
    summary = "\n".join(lines) + "\n"
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Refuse to START bootstrap on a candidate that may not outlive it.

    A precondition, not a fact to assume. The margin is checked against the
    RECORDED `expires_at`, which is what the API returned, not the retention the
    workflow asked for.
    """
    receipt = json.loads(Path(args.receipt).read_text())
    if receipt.get("schema") != SCHEMA:
        raise SystemExit(f"not a {SCHEMA} receipt: {receipt.get('schema')!r}")
    expires_raw = receipt.get("expires_at")
    if not expires_raw:
        raise SystemExit(
            "the receipt carries no expires_at. An unknown expiry is not a long "
            "one — re-record the candidate rather than proceeding"
        )
    expires = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
    remaining = (expires - datetime.now(UTC)).days
    print(f"candidate {receipt['sha256'][:16]}… expires {expires_raw} ({remaining}d)")
    if remaining < MINIMUM_REMAINING_DAYS:
        raise SystemExit(
            f"REFUSED: {remaining} day(s) remain and bootstrap needs at least "
            f"{MINIMUM_REMAINING_DAYS}. Bootstrap spans a restore proof, an "
            "issuer stand-up and a full Lane 3 rehearsal; starting now risks the "
            "candidate expiring mid-sequence, which invalidates the work already "
            "done rather than merely delaying it. Build a new candidate"
        )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Prove a local file IS the recorded candidate, before depending on it."""
    receipt = json.loads(Path(args.receipt).read_text())
    wheel = Path(args.wheel)
    actual = _sha256(wheel)
    size = wheel.stat().st_size
    problems = []
    if wheel.name != receipt["filename"]:
        problems.append(f"filename {wheel.name!r} != {receipt['filename']!r}")
    if size != receipt["size_bytes"]:
        problems.append(f"size {size} != {receipt['size_bytes']}")
    if actual != receipt["sha256"]:
        problems.append(f"sha256 {actual} != {receipt['sha256']}")
    if problems:
        raise SystemExit(
            "these are NOT the recorded candidate bytes: "
            + "; ".join(problems)
            + ". Do not proceed by rebuilding — a rebuild that happens to match "
            "is a claim, not a proof. Restart bootstrap with a new candidate"
        )
    print(f"verified {wheel.name} sha256={actual}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="write the CandidateArtifact.v1 receipt")
    record.set_defaults(handler=cmd_record)
    for flag in (
        "--facility",
        "--version",
        "--dist",
        "--run-id",
        "--source-sha",
        "--repository",
        "--artifact-name",
        "--out",
    ):
        record.add_argument(flag, required=True)
    record.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))

    check = sub.add_parser("check", help="refuse a candidate too close to expiry")
    check.set_defaults(handler=cmd_check)
    check.add_argument("--receipt", required=True)

    verify = sub.add_parser("verify", help="prove a file is the recorded candidate")
    verify.set_defaults(handler=cmd_verify)
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--wheel", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
