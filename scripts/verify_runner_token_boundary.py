"""Prove that a runner-query identity reaches one repository and no other.

The token is read only from the process environment.  It is never accepted as
an argument, embedded in a URL, or included in an exception.  The command
prints HTTP status codes and the matched runner's public identity only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence

API_ROOT = "https://api.github.com"


def _request(repository: str, token: str) -> tuple[int, bytes]:
    request = urllib.request.Request(  # noqa: S310 -- fixed HTTPS API root
        f"{API_ROOT}/repos/{repository}/actions/runners",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        # The body can contain server diagnostics and is irrelevant to the
        # boundary.  Do not turn it into log output.
        error.close()
        return error.code, b""


def _labels(runner: dict[str, object]) -> set[str]:
    raw = runner.get("labels")
    if not isinstance(raw, list):
        return set()
    return {
        str(entry.get("name"))
        for entry in raw
        if isinstance(entry, dict) and entry.get("name")
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--own-repository", required=True)
    parser.add_argument("--foreign-repository", required=True)
    parser.add_argument("--runner-name", required=True)
    parser.add_argument("--label", action="append", default=[], dest="labels")
    args = parser.parse_args(argv)

    token = os.environ.get("RUNNER_QUERY_TOKEN", "").strip()
    if not token:
        print("REFUSED: RUNNER_QUERY_TOKEN is absent", file=sys.stderr)
        return 3
    if args.own_repository == args.foreign_repository:
        print("REFUSED: own and foreign repositories are identical", file=sys.stderr)
        return 3

    own_status, own_body = _request(args.own_repository, token)
    foreign_status, _ = _request(args.foreign_repository, token)
    print(f"own runners endpoint: {own_status}")
    print(f"foreign runners endpoint: {foreign_status}")
    if own_status != 200 or foreign_status != 403:
        print(
            "REFUSED: expected an exact 200/403 repository boundary",
            file=sys.stderr,
        )
        return 3

    try:
        document = json.loads(own_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("REFUSED: own runner response is not JSON", file=sys.stderr)
        return 3
    runners = document.get("runners") if isinstance(document, dict) else None
    if not isinstance(runners, list):
        print("REFUSED: own runner response has no runner list", file=sys.stderr)
        return 3

    wanted = set(args.labels)
    matches = [
        runner
        for runner in runners
        if isinstance(runner, dict)
        and runner.get("name") == args.runner_name
        and wanted <= _labels(runner)
        and runner.get("status") == "online"
    ]
    if len(matches) != 1:
        print(
            "REFUSED: expected exactly one online runner with the declared "
            "name and labels",
            file=sys.stderr,
        )
        return 3

    print(f"runner identity: {args.runner_name}")
    print(f"required labels: {sorted(wanted)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
