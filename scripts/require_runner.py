"""Refuse a Lane 3 dispatch when its self-hosted runner is not there to take it.

## The failure this exists to prevent

`exposure-rehearsal.yml` targets `runs-on: [self-hosted, dotmac-control-runner]`.
That label is registered nowhere yet, and GitHub's behaviour for an unmatched
self-hosted label is not to fail — it is to QUEUE, for up to 24 hours, and only
then quietly cancel. So the observable symptom of "the runner does not exist" is
identical to the symptom of "the runner is busy with a long rehearsal": a job
that sits there. An operator watching a queued Lane 3 learns nothing about which
of those two it is, and the natural reaction — wait longer — is wrong in one
case and right in the other.

Michael's requirement is therefore explicit: *before dispatch, independently
refuse if the expected runner is absent or offline; do not rely on a queued job
to diagnose runner availability.*

## Why this cannot run on the runner it is checking

A pre-flight that runs on `dotmac-control-runner` to find out whether
`dotmac-control-runner` exists is the same indefinite queue with an extra step.
The `preflight` job in `exposure-rehearsal.yml` therefore runs on
`ubuntu-latest`, and `rehearse` declares `needs: preflight` — so the refusal is
reached on a hosted runner, before anything targets the self-hosted label.

## Why it fails closed on a missing token

Listing repository runners is `GET /repos/{owner}/{repo}/actions/runners`, which
needs the `administration: read` scope. That scope is NOT expressible in a
workflow `permissions:` block — the job's `GITHUB_TOKEN` cannot be granted it at
all — so this reads `RUNNER_QUERY_TOKEN`, a PAT with `administration: read`, and
**refuses when it is absent**.

Refusing rather than skipping is the whole point. "I could not determine whether
the runner exists" and "the runner exists" are different facts, and a pre-flight
that treats the first as the second re-creates the indefinite queue it was added
to remove, while also reporting that it checked.

## What counts as available

`online` and not `busy`. A busy runner is excluded deliberately: Lane 3 rewrites
shared firewall chains and holds a host lease under
`concurrency: cancel-in-progress: false`, so a second rehearsal landing behind a
first would queue *after* passing a check that said it was ready — which is
exactly the misleading state this script exists to avoid reporting.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 -- argv list, shell=False; gh CLI only
import sys

#: Exit code for "the runner is not available". Distinct from 1 so a caller can
#: tell a refusal from this script crashing.
EXIT_REFUSED = 3


def _query_runners(repository: str, token: str) -> list[dict[str, object]]:
    """Every registered self-hosted runner for `repository`.

    Through `gh api` for the same reason `foundation_candidate.py` does: the
    repository already depends on the CLI in CI and this needs no HTTP library.
    """
    environment = dict(os.environ)
    environment["GH_TOKEN"] = token
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["gh", "api", "--paginate", f"repos/{repository}/actions/runners"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"could not list runners for {repository} "
            f"({result.returncode}): {result.stderr.strip()}. This needs a token "
            "with `administration: read`; the job GITHUB_TOKEN cannot hold that "
            "scope. Refusing rather than assuming the runner is present"
        )
    # `--paginate` concatenates page objects; each carries its own `runners`.
    runners: list[dict[str, object]] = []
    decoder = json.JSONDecoder()
    text = result.stdout.strip()
    index = 0
    while index < len(text):
        page, offset = decoder.raw_decode(text, index)
        if isinstance(page, dict):
            found = page.get("runners")
            if isinstance(found, list):
                runners.extend(entry for entry in found if isinstance(entry, dict))
        index = offset
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
    return runners


def _labels(runner: dict[str, object]) -> set[str]:
    raw = runner.get("labels")
    if not isinstance(raw, list):
        return set()
    return {
        str(entry.get("name"))
        for entry in raw
        if isinstance(entry, dict) and entry.get("name")
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", required=True, help="owner/repo to query runners for"
    )
    parser.add_argument(
        "--label",
        required=True,
        action="append",
        dest="labels",
        help="a label the runner must carry; repeat for each (all must match)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("RUNNER_QUERY_TOKEN", "").strip()
    if not token:
        print(
            "REFUSED: RUNNER_QUERY_TOKEN is not set, so whether "
            f"{args.labels} is registered could not be determined. An "
            "undetermined runner is not an available one — dispatching now "
            "would queue indefinitely against a label that may not exist. "
            "Provide a token with `administration: read`.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    wanted = set(args.labels)
    runners = _query_runners(args.repository, token)
    matching = [runner for runner in runners if wanted <= _labels(runner)]

    if not matching:
        registered = sorted({label for r in runners for label in _labels(r)})
        print(
            f"REFUSED: no runner carries every label in {sorted(wanted)}. "
            f"Registered labels: {registered or 'none — no runners at all'}. "
            "The job would QUEUE against this label rather than fail, for up "
            "to 24 hours, so it is refused here instead.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    online = [r for r in matching if r.get("status") == "online"]
    if not online:
        states = sorted(f"{r.get('name')}={r.get('status')}" for r in matching)
        print(
            f"REFUSED: {len(matching)} runner(s) carry {sorted(wanted)} but none "
            f"is online ({states}). An offline runner queues exactly like an "
            "absent one.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    idle = [r for r in online if not r.get("busy")]
    if not idle:
        names = sorted(str(r.get("name")) for r in online)
        print(
            f"REFUSED: every online runner carrying {sorted(wanted)} is BUSY "
            f"({names}). Lane 3 holds a host lease and rewrites shared firewall "
            "chains under cancel-in-progress: false, so this dispatch would "
            "queue behind the running one — which is the indefinite wait this "
            "check exists to report rather than cause.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    names = sorted(str(r.get("name")) for r in idle)
    print(f"runner available for {sorted(wanted)}: {names}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
