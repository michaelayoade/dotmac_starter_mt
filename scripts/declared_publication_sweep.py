#!/usr/bin/env python3
"""Find every distribution declaring a version nobody can install.

`tests/architecture/test_module_version_sync.py` proves a module's three version
surfaces AGREE. It cannot prove the agreed version EXISTS: three surfaces reading
`0.1.0a2` in unison say nothing about whether `0.1.0a2` was ever built, uploaded
and verified. Internal consistency and publication are different questions, and
only the first one had a guard.

The live example is `dotmac-integration`. It declares `0.1.0a2` on all three
surfaces — the version-identity guard passes — while the newest tag is
`dotmac-integration-v0.1.0a1`. A consumer that reads the repository, the
changelog or the module catalogue and pins `==0.1.0a2` gets a resolver error,
and the repository as checked in offers no way to notice. `dotmac-imports` is
the sharper case: it declares `0.1.0a2` and has NO tag at all, so a distribution
that has never been published reads exactly like one that has.

This is a DETECTOR, not a fixer. The repair for a declared-but-unpublished
version is a release run or a deliberate decision to leave it unreleased — never
a quiet edit of the declared number, which would make the repository agree with
the index by discarding the work the number describes.

## The oracle: git tags, and why not the index

A tag is written by the release workflows' `verify` job, and only AFTER
`release_module.py verify-registry` has installed the exact published version
from the private index and registered its manifest. So a tag is this
repository's own assertion that a version was published AND proved installable —
strictly stronger than "an upload succeeded".

Querying the index directly was rejected. It needs an authenticated URL, which
makes the check un-runnable at PR time on a fork and impossible offline, and a
gate that cannot run is not a gate. The cost is stated rather than hidden: a
version published by a run whose tag step failed reads here as unpublished. That
is the failure `recover-module-release.yml` exists for, and reporting it is
correct — an untagged publication is exactly the state that workflow repairs.

## Two-directional, per ADR-0018

`docs/inventories/declared-publication-baseline.json` records every distribution
currently in an unpublished state, with the reason it is acceptable.

* a distribution that ENTERS the state without a ledger entry fails — that is a
  version silently promised to consumers;
* a ledger entry whose distribution has since been published, or which names a
  package that no longer exists, ALSO fails, and must be removed in the same
  change — otherwise the ledger accumulates stale absolutions and the count
  drifts downward unnoticed, which is the one-directional failure ADR-0018 names.

Stdlib only: this runs before dependencies are installed, like every other gate
in `scripts/`.

    python scripts/declared_publication_sweep.py --check
    python scripts/declared_publication_sweep.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import Final

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
PACKAGES: Final = REPO_ROOT / "packages"
LEDGER: Final = (
    REPO_ROOT / "docs" / "inventories" / "declared-publication-baseline.json"
)

#: Release lane allowlists, in the order a package is looked up. A package
#: listed in one of these carries its own `tag_prefix`; anything else falls back
#: to the repository-wide `<distribution>-v` convention, which is what the
#: kernel and UI workflows already write.
LANES: Final = (
    ("module", ".github/release-modules.json", "modules"),
    ("stateless-protocol-adapter", ".github/release-adapters.json", "adapters"),
    ("connector", ".github/release-connectors.json", "connectors"),
)

#: Packages released by a DEDICATED workflow rather than a lane allowlist.
#: Deliberately enumerated rather than inferred from "absent from every lane":
#: absence is also what an unreleasable package looks like, and the two states
#: must not collapse into one.
DEDICATED_WORKFLOWS: Final = {
    "dotmac-kernel": ".github/workflows/release-kernel.yml",
    "dotmac-ui": ".github/workflows/release-ui.yml",
}

PUBLISHED: Final = "published"
DECLARED_UNPUBLISHED: Final = "declared-unpublished"
NEVER_PUBLISHED: Final = "never-published"


class SweepRefused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"declared-publication sweep: {message}")


def git_tags(repo_root: pathlib.Path = REPO_ROOT) -> list[str]:
    """Every tag in the checkout.

    A SHALLOW or tagless clone would make every distribution look unpublished,
    which is a false alarm on a scale that would get this switched off — so the
    caller refuses on an empty tag set rather than reporting nine defects.
    """
    try:
        result = subprocess.run(  # nosec B603 — fixed argv, no shell
            ["git", "tag", "--list"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # A missing working directory or a missing `git` must refuse, not raise
        # something the caller has to guess at. Both mean the oracle is
        # unavailable, and an unavailable oracle is never a pass.
        raise SweepRefused(f"cannot run `git tag` in {repo_root}: {exc}") from exc
    if result.returncode != 0:
        raise SweepRefused(f"`git tag` failed: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_shallow(repo_root: pathlib.Path = REPO_ROOT) -> bool:
    """Is this checkout shallow?

    A shallow clone is not merely smaller — by default it carries NO tags, and
    a partial tag set is worse than none, because the sweep cannot tell an
    unreleased distribution from one whose tag simply was not fetched. It would
    then report a real-looking defect, or (having been made lenient to avoid
    that) report nothing at all.

    Treated as an incomplete oracle rather than as "probably fine": a checkout
    that fetched tags explicitly is not shallow in CI's default configuration,
    and the cost of refusing is one workflow line.
    """
    try:
        result = subprocess.run(  # nosec B603 — fixed argv, no shell
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # Names the probe, like `git_tags` does. A refusal a reader cannot
        # attribute to a specific check is one they cannot act on.
        raise SweepRefused(
            f"cannot run `git rev-parse` in {repo_root}: {exc}"
        ) from None
    if result.returncode != 0:
        raise SweepRefused(f"`git rev-parse` failed: {result.stderr.strip()}")
    return result.stdout.strip() == "true"


def version_key(version: str) -> tuple:
    """Natural-order sort key, so `0.1.0a9` does not outrank `0.1.0a64`.

    Lexical ordering is the wrong default for these versions and wrong in the
    direction that misleads: it makes the newest kernel look older than one
    published fifty-five releases earlier. Stdlib only, so no `packaging`.
    """
    parts: list[object] = []
    digits = ""
    letters = ""
    for character in version:
        if character.isdigit():
            if letters:
                parts.append((0, letters))
                letters = ""
            digits += character
        elif character.isalpha():
            if digits:
                parts.append((1, int(digits)))
                digits = ""
            letters += character
        else:
            if digits:
                parts.append((1, int(digits)))
                digits = ""
            if letters:
                parts.append((0, letters))
                letters = ""
    if digits:
        parts.append((1, int(digits)))
    if letters:
        parts.append((0, letters))
    return tuple(parts)


def _lane_entries(repo_root: pathlib.Path) -> dict[str, tuple[str, dict]]:
    found: dict[str, tuple[str, dict]] = {}
    for lane, relative, key in LANES:
        path = repo_root / relative
        if not path.is_file():
            continue
        for distribution, entry in json.loads(path.read_text(encoding="utf-8"))[
            key
        ].items():
            found[distribution] = (lane, entry)
    return found


def _declared_versions(repo_root: pathlib.Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for pyproject in sorted((repo_root / "packages").glob("*/pyproject.toml")):
        poetry = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]
        versions[poetry["name"]] = poetry["version"]
    return versions


def survey(repo_root: pathlib.Path = REPO_ROOT) -> dict:
    """Every package's declared version against the tags that prove publication."""
    if is_shallow(repo_root):
        raise SweepRefused(
            "the checkout is SHALLOW, so its tag set cannot be trusted to be "
            "complete. A missing tag is indistinguishable from an unreleased "
            "version here, and guessing in either direction is wrong: guessing "
            "'published' hides a version promised to nobody, guessing "
            "'unpublished' reports defects that are artefacts of the clone. "
            "Check out with full history and tags "
            "(`actions/checkout` with `fetch-depth: 0`) and re-run."
        )
    tags = set(git_tags(repo_root))
    if not tags:
        raise SweepRefused(
            "the checkout has no tags at all. Every distribution would read as "
            "unpublished, which is a property of this clone rather than of the "
            "releases. Fetch tags (`git fetch --tags`) and re-run."
        )

    lanes = _lane_entries(repo_root)
    findings: dict[str, dict] = {}
    for distribution, declared in sorted(_declared_versions(repo_root).items()):
        lane, entry = lanes.get(distribution, ("", {}))
        prefix = entry.get("tag_prefix") or f"{distribution}-v"
        published = sorted(
            (tag.removeprefix(prefix) for tag in tags if tag.startswith(prefix)),
            key=version_key,
        )
        if f"{prefix}{declared}" in tags:
            state = PUBLISHED
        elif published:
            state = DECLARED_UNPUBLISHED
        else:
            state = NEVER_PUBLISHED
        findings[distribution] = {
            "declared": declared,
            "state": state,
            "tag_prefix": prefix,
            "published_versions": published,
            "release_lane": lane
            or ("dedicated-workflow" if distribution in DEDICATED_WORKFLOWS else ""),
        }
    return {"distributions": findings}


def unpublished(survey_result: dict) -> dict[str, dict]:
    return {
        distribution: finding
        for distribution, finding in survey_result["distributions"].items()
        if finding["state"] != PUBLISHED
    }


def _ledger(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, dict]:
    path = repo_root / LEDGER.relative_to(REPO_ROOT)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["unpublished"]


def reconcile(survey_result: dict, ledger: dict[str, dict]) -> list[str]:
    """Two-directional, per ADR-0018.

    Both directions are failures with different meanings, so both name the
    repair rather than only the symptom.
    """
    problems: list[str] = []
    live = unpublished(survey_result)

    for distribution, finding in sorted(live.items()):
        recorded = ledger.get(distribution)
        if recorded is None:
            problems.append(
                f"{distribution}: declares {finding['declared']} and no "
                f"{finding['tag_prefix']}{finding['declared']} tag exists "
                f"({finding['state']}). A version consumers cannot install is "
                "either released or recorded in "
                "docs/inventories/declared-publication-baseline.json with the "
                "reason — NEVER repaired by editing the declared version down."
            )
            continue
        if recorded.get("declared") != finding["declared"]:
            problems.append(
                f"{distribution}: the ledger records {recorded.get('declared')!r} "
                f"but the package now declares {finding['declared']!r}. Update "
                "the entry in the same change as the bump, so the reason is "
                "reviewed against the version it now excuses."
            )
        if not str(recorded.get("reason", "")).strip():
            problems.append(f"{distribution}: the ledger entry states no reason")

    for distribution in sorted(set(ledger) - set(live)):
        if distribution not in survey_result["distributions"]:
            problems.append(
                f"{distribution}: recorded as unpublished but no such package "
                "exists. Remove the stale entry."
            )
        else:
            problems.append(
                f"{distribution}: recorded as unpublished but "
                f"{survey_result['distributions'][distribution]['declared']} is "
                "now tagged. Remove the entry in the SAME change as the release "
                "— a ledger that only ever grows stops describing anything."
            )
    return problems


def render(survey_result: dict) -> str:
    lines = ["Declared version vs. published tag:"]
    for distribution, finding in sorted(survey_result["distributions"].items()):
        published = finding["published_versions"]
        latest = published[-1] if published else "—"
        lines.append(
            f"  {distribution:<32} declares {finding['declared']:<10} "
            f"latest tag {latest:<10} {finding['state']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    survey_result = survey()
    print(render(survey_result))

    if args.write_baseline:
        existing = _ledger()
        # Everything but `unpublished` is carried through, so regenerating the
        # ledger never silently deletes the prose explaining what it is.
        document = (
            json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else {}
        )
        document["unpublished"] = {
            distribution: {
                "declared": finding["declared"],
                "state": finding["state"],
                "reason": existing.get(distribution, {}).get(
                    "reason", "TODO: state why this version is not installable"
                ),
            }
            for distribution, finding in sorted(unpublished(survey_result).items())
        }
        LEDGER.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {LEDGER.relative_to(REPO_ROOT)}")
        return 0

    if not args.check:
        return 0

    problems = reconcile(survey_result, _ledger())
    if problems:
        print("\n" + "\n".join(problems))
        return 1
    print("\ndeclared-publication sweep PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
