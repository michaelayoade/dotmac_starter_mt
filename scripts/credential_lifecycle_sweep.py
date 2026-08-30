"""Measure every direct call to a password primitive outside the lifecycle owner.

`dotmac_kernel.credential_lifecycle` is the single owner of human credential
decisions. Everything else that reaches for `hash_password`, `verify_password`
or `password_needs_rehash` directly is a SECOND owner — it re-decides for itself
what active, locked and reset-required mean, and returns a bare boolean that
cannot carry the answer.

This is the measurement. `docs/inventories/credential-lifecycle-sources.md` is
the prose, `docs/inventories/credential-lifecycle-baseline.json` is what the
ratchet freezes, and `tests/architecture/test_credential_lifecycle_ratchet.py`
is the gate.

## Entry-point FAMILIES, not one directory

Hard rule 25 is explicit that a guard enumerates entry-point families. It is not
an abstract concern here: two of Sub's eleven `hash_password` call sites are in
`scripts/seed/`, so a sweep scoped to `app/` would have reported nine and called
the other two absent. The families below are enumerated by name, each repository
reports which of them it HAS, and a family that exists but was never scanned is
a defect the coverage block makes visible.

## Calls, not mentions

AST, not grep. An import line, a docstring, a comment and a re-export are not
call sites, and counting them makes the number un-actionable: retiring a caller
would leave its import behind and the ratchet would report no progress. This is
also why the numbers here differ from the hand census recorded in the inventory,
which counted grep lines — the inventory states both and says which is which.

## A missing repository is UNMEASURED, never zero

A sibling that is not checked out scores nothing at all and the ratchet
abstains for it. Scoring it zero would report the debt as retired.

Sibling repositories are measured from IMMUTABLE GIT OBJECTS at the exact commit
the baseline row names — never from whatever branch a colleague happens to have
checked out. Hard rule 30: a claim about another repository needs a coordinate,
and `main@sha` or a working tree is not one. When the commit is not present in
the local clone the sweep abstains for that repository rather than scoring it
(ADR-0032: unobserved is unknown, never absent).

The repository under test is different — it is measured from its WORKING TREE
and always enforced, because that tree is the thing being reviewed.

    python scripts/credential_lifecycle_sweep.py --check
    python scripts/credential_lifecycle_sweep.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Final

PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
BASELINE_PATH: Final[pathlib.Path] = (
    PROJECT_ROOT / "docs" / "inventories" / "credential-lifecycle-baseline.json"
)

#: The repository this sweep lives in, measured from its working tree because
#: that tree is the thing under review.
SELF_REPOSITORY: Final[str] = "dotmac_starter_mt"

#: The three primitives `dotmac_kernel.security` publishes. A product calling any
#: of them directly has taken a credential decision the lifecycle owner should
#: have taken.
SYMBOLS: Final[tuple[str, ...]] = (
    "hash_password",
    "password_needs_rehash",
    "verify_password",
)

#: Every Python entry-point family a Dotmac repository can have, and the
#: repository-relative roots each one lives under. Named rather than globbed so
#: a repository that grows a `workers/` tree is measured the day it appears, and
#: so the coverage block can say which families a repository does NOT have —
#: which is the difference between "zero" and "nothing was looked at".
ENTRY_POINT_FAMILIES: Final[dict[str, tuple[str, ...]]] = {
    "application": ("app",),
    "installable_packages": ("packages",),
    "library_source": ("src",),
    "scripts": ("scripts", "bin"),
    "migrations": ("alembic", "migrations"),
    "tasks": ("tasks",),
    "workers": ("worker", "workers", "jobs"),
    "cli": ("cli",),
    "cron": ("cron",),
}

#: Directory names never scanned anywhere. Each premise is checkable by looking
#: at the directory, which is what hard rule 25 requires of an exclusion.
SKIPPED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",  # build output
        ".venv",  # a vendored interpreter environment, not repository source
        "venv",
        "node_modules",  # JavaScript dependencies
        "site-packages",  # installed third-party code
        "tests",  # see TEST_PREMISE
        "test",
    }
)

#: Why test code is excluded, stated so the exclusion can be argued with: a test
#: that hashes a fixture password is how the lifecycle is VERIFIED. Counting it
#: would make the number rise every time the facility gained a test, which is
#: precisely backwards.
TEST_PREMISE: Final[str] = (
    "test modules exercise the primitives to verify them; counting a test "
    "would make the debt rise when coverage improves"
)

#: The files allowed to call the primitives, and the enforceable premise for
#: each. `test_credential_lifecycle_ratchet.py` proves both files exist AND that
#: each one really calls a primitive — an exemption for a file that no longer
#: uses the thing it is exempt from is a stale exemption, not a safe one.
OWNER_PATHS: Final[dict[str, str]] = {
    "packages/dotmac-kernel/src/dotmac_kernel/security.py": (
        "defines the primitives; the definition cannot be a call site"
    ),
    "packages/dotmac-kernel/src/dotmac_kernel/credential_lifecycle.py": (
        "IS the lifecycle owner; this is the one place a primitive is called"
    ),
}


@dataclass
class RepoMeasurement:
    """One repository's live measurement, plus what bounded it."""

    repository: str
    revision: str | None
    files: dict[str, dict[str, int]] = field(default_factory=dict)
    families_present: tuple[str, ...] = ()
    families_absent: tuple[str, ...] = ()
    scanned_files: int = 0
    unreadable: tuple[str, ...] = ()

    @property
    def totals(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for symbols in self.files.values():
            counter.update(symbols)
        return {symbol: counter.get(symbol, 0) for symbol in SYMBOLS}


def count_calls(source: str) -> dict[str, int]:
    """Direct CALLS to a primitive in one module.

    `from dotmac_kernel.security import hash_password` is not a call.
    `def hash_password(...)` is not a call. `hash_password(x)` and
    `security.hash_password(x)` both are.
    """
    counter: Counter[str] = Counter()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name: str | None = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name in SYMBOLS:
            counter[name] += 1
    return dict(sorted(counter.items()))


def _is_scannable(relative: pathlib.Path) -> bool:
    if any(part in SKIPPED_DIRECTORY_NAMES for part in relative.parts):
        return False
    return not (relative.name.startswith("test_") or relative.name == "conftest.py")


def _git(repo_root: pathlib.Path, *arguments: str) -> str | None:
    """Read-only git. Returns None on any failure rather than raising: a
    repository we cannot read is unmeasured, and unmeasured is a reported state
    rather than an exception."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - git absent on the runner
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _head_revision(repo_root: pathlib.Path) -> str | None:
    output = _git(repo_root, "rev-parse", "HEAD")
    return output.strip() if output else None


def has_revision(repo_root: pathlib.Path, revision: str) -> bool:
    return _git(repo_root, "cat-file", "-e", f"{revision}^{{commit}}") is not None


def measure_revision(
    repository: str, repo_root: pathlib.Path, revision: str
) -> RepoMeasurement | None:
    """Measure a repository at an IMMUTABLE commit, without touching its tree.

    Everything is read through `git ls-tree` and `git show`, so a colleague's
    in-progress branch, uncommitted edits and stashes are all invisible here —
    which is the point. Returns None when the commit is not present locally.
    """
    if not has_revision(repo_root, revision):
        return None
    listing = _git(repo_root, "ls-tree", "-r", "--name-only", revision)
    if listing is None:
        return None
    tracked = [pathlib.Path(line) for line in listing.splitlines() if line.strip()]

    present: list[str] = []
    absent: list[str] = []
    roots_with_files = {
        part
        for path in tracked
        for part in ([path.parts[0]] if len(path.parts) > 1 else [])
    }
    for family, candidates in ENTRY_POINT_FAMILIES.items():
        if roots_with_files & set(candidates):
            present.append(family)
        else:
            absent.append(family)
    measured_roots = {
        candidate
        for candidates in ENTRY_POINT_FAMILIES.values()
        for candidate in candidates
    }

    files: dict[str, dict[str, int]] = {}
    unreadable: list[str] = []
    scanned = 0
    for path in tracked:
        if path.suffix != ".py":
            continue
        in_family = len(path.parts) > 1 and path.parts[0] in measured_roots
        at_repository_root = len(path.parts) == 1
        if not (in_family or at_repository_root):
            continue
        if not _is_scannable(path):
            continue
        key = path.as_posix()
        if key in OWNER_PATHS:
            continue
        scanned += 1
        source = _git(repo_root, "show", f"{revision}:{key}")
        if source is None:
            unreadable.append(f"{key}: unreadable at {revision}")
            continue
        try:
            found_calls = count_calls(source)
        except (SyntaxError, ValueError) as exc:
            unreadable.append(f"{key}: {type(exc).__name__}")
            continue
        if found_calls:
            files[key] = found_calls

    return RepoMeasurement(
        repository=repository,
        revision=revision,
        files=dict(sorted(files.items())),
        families_present=tuple(sorted(present)),
        families_absent=tuple(sorted(absent)),
        scanned_files=scanned,
        unreadable=tuple(sorted(unreadable)),
    )


def measure_repository(repository: str, repo_root: pathlib.Path) -> RepoMeasurement:
    """Every non-owner call site under every entry-point family this repo has."""
    present: list[str] = []
    absent: list[str] = []
    roots: list[pathlib.Path] = []
    for family, candidates in ENTRY_POINT_FAMILIES.items():
        found = [repo_root / candidate for candidate in candidates]
        found = [path for path in found if path.is_dir()]
        if found:
            present.append(family)
            roots.extend(found)
        else:
            absent.append(family)

    files: dict[str, dict[str, int]] = {}
    unreadable: list[str] = []
    scanned = 0
    # Repository-root modules are their own family: a one-file CLI at the top of
    # a repository is an entry point, and no directory root would see it.
    candidates = sorted(repo_root.glob("*.py"))
    for root in roots:
        candidates.extend(sorted(root.rglob("*.py")))
    for path in candidates:
        relative = path.relative_to(repo_root)
        if not _is_scannable(relative):
            continue
        key = relative.as_posix()
        if key in OWNER_PATHS:
            continue
        scanned += 1
        try:
            source = path.read_text(encoding="utf-8")
            found_calls = count_calls(source)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            # Named, and fatal under --check: a dropped file inside a measured
            # repository silently LOWERS the number the ratchet defends.
            unreadable.append(f"{key}: {type(exc).__name__}")
            continue
        if found_calls:
            files[key] = found_calls

    return RepoMeasurement(
        repository=repository,
        revision=_head_revision(repo_root),
        files=dict(sorted(files.items())),
        families_present=tuple(sorted(present)),
        families_absent=tuple(sorted(absent)),
        scanned_files=scanned,
        unreadable=tuple(sorted(unreadable)),
    )


def measure(
    fleet_root: pathlib.Path, baseline: dict
) -> tuple[dict[str, RepoMeasurement], list[str], list[str]]:
    """Measure what can be measured; name what cannot, and why.

    Returns (measured, absent, unverified). The three states are kept apart on
    purpose: absent means no clone, unverified means a clone without the
    recorded commit, and neither is a number.
    """
    measured: dict[str, RepoMeasurement] = {}
    absent: list[str] = []
    unverified: list[str] = []
    rows: dict[str, dict] = baseline.get("repositories", {})

    for repository in sorted(set(rows) | {SELF_REPOSITORY}):
        if repository == SELF_REPOSITORY:
            measured[repository] = measure_repository(repository, PROJECT_ROOT)
            continue
        repo_root = fleet_root / repository
        if not repo_root.is_dir():
            absent.append(f"{repository}: no clone beside this checkout")
            continue
        revision = rows.get(repository, {}).get("revision")
        if not revision:
            unverified.append(f"{repository}: the baseline row names no revision")
            continue
        found = measure_revision(repository, repo_root, revision)
        if found is None:
            unverified.append(
                f"{repository}: commit {revision} is not present in the local "
                "clone (fetch it, or the measurement is about a different tree)"
            )
            continue
        measured[repository] = found
    return measured, absent, unverified


def load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _drift(
    live: dict[str, dict[str, int]], recorded: dict[str, dict[str, int]], where: str
) -> list[str]:
    problems: list[str] = []
    for path in sorted(set(live) | set(recorded)):
        live_symbols = live.get(path, {})
        recorded_symbols = recorded.get(path, {})
        for symbol in sorted(set(live_symbols) | set(recorded_symbols)):
            now = live_symbols.get(symbol, 0)
            was = recorded_symbols.get(symbol, 0)
            if now > was:
                problems.append(
                    f"{where}: a new direct `{symbol}` call landed in {path} "
                    f"({was} -> {now}). Call the lifecycle owner "
                    "(dotmac_kernel.credential_lifecycle) instead."
                )
            elif now < was:
                problems.append(
                    f"{where}: `{symbol}` in {path} fell {was} -> {now} without "
                    "the baseline moving. If you RETIRED it, lower the baseline "
                    "in the SAME change; if you did not, the detector stopped "
                    "seeing it."
                )
    return problems


def ratchet(
    measured: dict[str, RepoMeasurement], baseline: dict
) -> tuple[list[str], list[str]]:
    """Two-directional per repository, per file, per symbol.

    Returns (failures, abstentions). A repository that could not be measured at
    its recorded commit is an abstention — never a pass and never a failure.
    """
    failures: list[str] = []
    abstentions: list[str] = []
    recorded_repos: dict[str, dict] = baseline["repositories"]

    for repository, row in sorted(recorded_repos.items()):
        live = measured.get(repository)
        if live is None:
            abstentions.append(f"{repository}: UNMEASURED this run")
            continue
        if live.unreadable:
            failures.append(
                f"{repository}: unreadable or unparseable source inside a "
                f"measured repository: {', '.join(live.unreadable)}"
            )
            continue
        failures.extend(_drift(live.files, row["files"], repository))

    for repository in sorted(set(measured) - set(recorded_repos)):
        failures.append(
            f"{repository}: measured but absent from the baseline; a repository "
            "nobody recorded is unmonitored, not clean"
        )
    return failures, abstentions


def build_baseline(measured: dict[str, RepoMeasurement], previous: dict) -> dict:
    previous_repos: dict[str, dict] = previous.get("repositories", {})
    repositories: dict[str, dict] = {}
    for repository, live in sorted(measured.items()):
        repositories[repository] = {
            "revision": live.revision,
            "measured_from": (
                "working tree" if repository == SELF_REPOSITORY else "git objects"
            ),
            "families_present": list(live.families_present),
            "families_absent": list(live.families_absent),
            "scanned_files": live.scanned_files,
            "files": live.files,
            "totals": live.totals,
        }
    # A repository that could not be measured this run keeps its recorded row.
    # Dropping it would silently retire debt nobody retired.
    for repository, prior in sorted(previous_repos.items()):
        repositories.setdefault(repository, prior)
    return {
        "schema_version": 1,
        "symbols": list(SYMBOLS),
        "owner_paths": dict(sorted(OWNER_PATHS.items())),
        "entry_point_families": {
            family: list(roots) for family, roots in ENTRY_POINT_FAMILIES.items()
        },
        "test_exclusion_premise": TEST_PREMISE,
        "repositories": repositories,
    }


def _coverage(
    measured: dict[str, RepoMeasurement],
    absent: list[str],
    unverified: list[str],
) -> str:
    """Say the bounds out loud. A bounded measurement that does not state its
    bounds reads as "covered everything"."""
    lines = ["COVERAGE"]
    for repository, live in sorted(measured.items()):
        lines.append(
            f"  {repository} @ {live.revision}: {live.scanned_files} files across "
            f"{', '.join(live.families_present) or 'no family'}; absent families: "
            f"{', '.join(live.families_absent) or 'none'}"
        )
        if live.unreadable:
            lines.append(f"    UNREADABLE: {', '.join(live.unreadable)}")
    for line in absent:
        lines.append(f"  ABSENT {line} — unmeasured, not zero")
    for line in unverified:
        lines.append(f"  UNVERIFIED {line} — unmeasured, not zero")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-root", default=str(PROJECT_ROOT.parent))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    baseline = load_baseline() if BASELINE_PATH.is_file() else {"repositories": {}}
    measured, absent, unverified = measure(pathlib.Path(args.fleet_root), baseline)
    print(_coverage(measured, absent, unverified))

    if args.write_baseline:
        BASELINE_PATH.write_text(
            json.dumps(build_baseline(measured, baseline), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
        return 0

    failures, abstentions = ratchet(measured, baseline)
    for line in abstentions:
        print(f"ABSTAIN {line}")
    if failures and args.check:
        print("\ncredential-lifecycle ratchet:")
        for line in failures:
            print(f"  {line}")
        return 1
    for line in failures:
        print(f"  {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
