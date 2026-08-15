"""Measure every direct external-connector surface still in a product runtime.

Step 1 of the Integrator sequence (ADR-0024 § 6): before anything is extracted,
make the thing countable and stop it growing. `docs/inventories/
external-connector-sources.md` is the prose; this is the measurement, and
`external-connector-baseline.json` is what the ratchet freezes.

## What is being counted, and why these six

ADR-0024 § 6 names what a product must retire from its application runtime:
provider clients, provider credentials, provider webhook verification, connector
scheduling, checkpoints and delivery retries. Those become the six categories
below. A capability is Integrator-owned when it is generic control-plane
machinery; it stays product-owned when it is a business decision about a
domain payload. The detectors therefore look for the MACHINERY.

## Precision over recall, deliberately

A ratchet that fires on hundreds of lines gets switched off, and ADR-0018
requires the detector to carry a sensitivity proof rather than an assertion. So
every rule here is narrow and AST-grounded, and the inventory states what each
rule does NOT see. An undercount that shrinks honestly beats an overcount
nobody trusts.

Two choices inherited from `fleet_decomposition_sweep.py`, both load-bearing:

* **AST, not grep.** `import httpx` inside a docstring, a comment, or a vendored
  fixture is not a connector. Imports and calls are read from the parse tree.
* **A missing repo is UNMEASURED, never zero.** A sweep that scores 0 for a repo
  it could not find would report the duplication as solved. Absent repos are
  named and make the ratchet abstain.

Tests are excluded everywhere: a test that fakes a provider is how a connector
is verified, not a connector.

## No silent caps

Every run prints a COVERAGE block, because a bounded measurement that does not
state its bounds reads as "covered everything". Four bounds exist and all four
are now said out loud rather than inferred:

1. **The measured subtree.** Only `app/` (or the repo's equivalent) is read, so
   every other Python file in that repository is outside the measurement. The
   count of those files is reported per repo — a bound with a number is a bound
   a reviewer can weigh; a bound in a docstring is one they cannot.
2. **Unreadable and unparseable files.** These used to return an empty
   classification, which is indistinguishable from "this file has no connector"
   and silently LOWERS a count. They are now named, and they FAIL `--check`:
   inside an enumerated repo a dropped file corrupts the number the ratchet is
   defending.
3. **Absent enumerated repos.** Already handled — named, and the ratchet
   abstains rather than scoring them zero.
4. **Fleet repositories nobody classified.** `RUNTIME_ROOTS` measures five
   repositories; the fleet has more. A repository that is neither measured nor
   listed in `OUT_OF_SCOPE` with a premise is reported as UNCLASSIFIED. It does
   not fail by default — a developer's second clone of an already-measured repo
   is not a governance defect — but `--strict-coverage` turns it into one, which
   is how CI runs it.

ADR-0018 governs (4): an exemption states an ENFORCEABLE premise, or the region
is unmonitored rather than exempt. Each `OUT_OF_SCOPE` entry therefore says
something a reader can check against the repository, and "grandfathered" is not
one of them.

Run it from a checkout that has the fleet beside it::

    python scripts/external_connector_sweep.py --check
    python scripts/external_connector_sweep.py --check --strict-coverage
    python scripts/external_connector_sweep.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "external-connector-baseline.json"

# Repo -> the subtree its application runtime lives under. Only runtime code is
# measured: a connector in a migration is history, and a connector in a script
# is not in the request path this ADR governs.
RUNTIME_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dotmac_erp", ("app",)),
    ("dotmac_crm", ("app",)),
    ("dotmac_sub", ("app",)),
    ("dotmac_academy_app", ("app",)),
    ("dotmac_vendor_control_plane", ("src", "vendor_cp")),
)

REPOS = tuple(repo for repo, _ in RUNTIME_ROOTS)

#: Fleet directories deliberately NOT measured, each with the premise that keeps
#: it out of scope. ADR-0018: an exemption states an ENFORCEABLE premise — one a
#: reader can check against the repository — or the region is unmonitored rather
#: than exempt. "Grandfathered" is not a premise and none of these say it.
#:
#: The distinction that matters here is DESTINATION versus SOURCE. ADR-0024 § 6
#: retires connector surface out of product runtimes and INTO the Integrator, so
#: counting the Integrator would ratchet the place the surface is supposed to
#: arrive, and every successful migration would read as a regression.
OUT_OF_SCOPE: tuple[tuple[str, str], ...] = (
    (
        "dotmac_starter_mt",
        "the Starter is the module workspace this sweep is RUN from, not a "
        "product runtime; its packages are the destination of the retirement",
    ),
    (
        "dotmac_integrator",
        "the Integrator assembly — the DESTINATION of every retirement this "
        "ratchet drives. Counting it would make each successful migration read "
        "as a new connector surface (ADR-0024 § 6)",
    ),
    (
        "dotmac-integration-client",
        "the client library products CALL to reach the Integrator; a connector "
        "it holds is the point, not the debt",
    ),
    (
        "dotmac_workspace",
        "an assembly that composes modules and owns no provider integration; if "
        "it grows one it must be MEASURED, not re-exempted",
    ),
    (
        "dotmac_governance",
        "engineering-standards tooling with no application runtime and no "
        "`app/` subtree — nothing this sweep's categories can describe",
    ),
    (
        "dotmac-academy",
        "course and manual content plus build scripts; its Python is authoring "
        "tooling, not a request path",
    ),
    (
        "claude_knowledge",
        "the knowledge MCP server — operator tooling outside the product fleet "
        "ADR-0024 governs",
    ),
)

OUT_OF_SCOPE_REASONS: dict[str, str] = dict(OUT_OF_SCOPE)

#: Directory names that are never application runtime.
_EXCLUDED_PARTS = frozenset(
    {"tests", "test", "__pycache__", "migrations", "alembic", "node_modules", ".venv"}
)

#: HTTP client libraries. A module importing one of these is making outbound
#: calls itself rather than asking a control plane to make them.
_HTTP_CLIENTS = frozenset({"httpx", "requests", "aiohttp", "urllib3", "http.client"})

#: Method names that actually issue a request. Importing `httpx` for a type
#: annotation is not a connector, and this is what separates the two.
_REQUEST_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "request", "stream", "send"}
)

#: Substrings that make a route or handler a provider callback surface rather
#: than a product API. Matched against the decorator's path literal.
_WEBHOOK_PATH_HINTS = ("webhook", "callback", "/hooks", "notify-url", "ipn")

#: Signature/authenticity verification of an inbound provider payload — the
#: single most security-relevant thing ADR-0024 § 6 moves to the Integrator.
_WEBHOOK_VERIFY_HINTS = ("verify_signature", "verify_webhook", "check_signature")

#: Provider credential material held in a product's own configuration. Narrow on
#: purpose: a generic `api_key` is a product's own key more often than a
#: provider's, so a provider token must be NAMED with its provider.
_PROVIDER_TOKENS = (
    "stripe",
    "twilio",
    "paystack",
    "flutterwave",
    "meta_",
    "facebook",
    "instagram",
    "whatsapp",
    "erpnext",
    "sendgrid",
    "mailgun",
    "smtp_",
    "s3_",
    "minio",
)
_CREDENTIAL_SUFFIXES = (
    "_api_key",
    "_secret",
    "_secret_key",
    "_token",
    "_access_token",
    "_webhook_secret",
    "_client_secret",
    "_password",
)

#: Scheduling a connector run from inside the product runtime.
_TASK_HINTS = ("celery", "shared_task", "scheduled_task", "periodic_task", "cron")
_TASK_SUBJECT_HINTS = ("sync", "connector", "integration", "webhook", "poll", "fetch")

#: Durable cursor/watermark state for an external feed.
#:
#: Split in two because the words are not equally specific. `checkpoint` /
#: `syncstate` / `synccursor` already NAME durable progress over a stream, so
#: they stand alone. Bare `cursor` does not: it is the ordinary word for a
#: pagination cursor, a DBAPI cursor, and — the miscount that motivated this
#: split — a round-robin ROTATION pointer over an internal roster
#: (`InboxTeamRoundRobinCursor`, dotmac_sub `app/models/team_inbox.py`), which
#: has no feed, no watermark and no external system anywhere near it. A bare
#: `*Cursor` therefore has to also name the feed it tracks.
_FEED_CHECKPOINT_CLASS_HINTS = ("checkpoint", "syncstate", "synccursor")
_AMBIGUOUS_CHECKPOINT_CLASS_HINTS = ("cursor",)
_CHECKPOINT_CLASS_HINTS = (
    _FEED_CHECKPOINT_CLASS_HINTS + _AMBIGUOUS_CHECKPOINT_CLASS_HINTS
)

#: `meta_` is a provider prefix in a SETTINGS name but an ordinary programming
#: prefix in a CLASS name (`MetadataCursor`), so it does not qualify a cursor.
_NOT_A_CLASS_NAME_PROVIDER = frozenset({"meta_"})

#: What turns an ambiguous `*Cursor` into a connector checkpoint: the name says
#: which external feed it is a position in. Provider names count too, so a
#: `StripeCursor` needs no generic word to qualify.
_EXTERNAL_FEED_HINTS = (
    "sync",
    "external",
    "integration",
    "connector",
    "feed",
    "ingest",
    "import",
    "poll",
    "replicat",
    "upstream",
    "remote",
    "mirror",
    "provider",
    "webhook",
    "erp",
    *(
        provider.rstrip("_")
        for provider in _PROVIDER_TOKENS
        if provider not in _NOT_A_CLASS_NAME_PROVIDER
    ),
)

_CHECKPOINT_COLUMN_HINTS = ("last_synced_at", "sync_cursor", "last_cursor", "watermark")

CATEGORIES = (
    "http_client",
    "webhook_surface",
    "provider_credential",
    "connector_task",
    "sync_checkpoint",
    "delivery_retry",
)

_RETRY_HINTS = ("dead_letter", "deadletter", "retry_backoff", "max_retries", "requeue")


def _walk_python(
    root: pathlib.Path, *, nested_checkouts: list[pathlib.Path] | None = None
) -> list[pathlib.Path]:
    """Every `.py` under `root`, pruning excluded directories as it descends.

    Pruning rather than filtering `rglob` output: a repository with a `.venv`
    holds tens of thousands of files this sweep will discard, and descending
    into them to throw them away makes the coverage report expensive enough that
    someone would be tempted to drop it.

    Passing `nested_checkouts` also prunes any subdirectory that is itself a git
    checkout, appending it to that list. `dotmac_erp/worktrees/` holds 5,000+
    `.py` files that are COPIES of code measured elsewhere; counting them would
    have made the coverage disclosure read as though two-thirds of ERP were
    unmeasured, and a disclosure that overstates is discarded as fast as one
    that understates. The premise is checkable rather than a name guess: the
    directory contains a `.git` entry.
    """
    found: list[pathlib.Path] = []
    for directory, subdirectories, filenames in os.walk(root):
        keep: list[str] = []
        for name in sorted(subdirectories):
            if name in _EXCLUDED_PARTS or name.startswith("."):
                continue
            candidate = pathlib.Path(directory) / name
            if nested_checkouts is not None and (candidate / ".git").exists():
                nested_checkouts.append(candidate)
                continue
            keep.append(name)
        subdirectories[:] = keep
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                found.append(pathlib.Path(directory) / filename)
    return found


def _runtime_files(
    root: pathlib.Path, *, nested_checkouts: list[pathlib.Path] | None = None
) -> list[pathlib.Path]:
    return [
        path
        for path in _walk_python(root, nested_checkouts=nested_checkouts)
        if not path.name.startswith("test_")
    ]


def _imports_http_client(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in _HTTP_CLIENTS for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _HTTP_CLIENTS:
                return True
    return False


def _issues_a_request(tree: ast.AST) -> bool:
    """An `httpx.post(...)`/`client.get(...)`-shaped call.

    Attribute-name matching, not receiver tracking: a receiver could be any
    local name, and resolving it would need type inference this sweep has no
    business doing. Paired with `_imports_http_client`, the false-positive
    surface is a module that both imports a client AND calls something named
    `get` — rare enough to be worth the recall.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _REQUEST_METHODS:
                return True
    return False


def _decorator_paths(node: ast.AST) -> list[str]:
    paths: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Call):
            for arg in decorator.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    paths.append(arg.value.lower())
    return paths


def _is_webhook_surface(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                hint in path
                for path in _decorator_paths(node)
                for hint in _WEBHOOK_PATH_HINTS
            ):
                return True
            if any(hint in node.name.lower() for hint in _WEBHOOK_VERIFY_HINTS):
                return True
    return False


def _holds_provider_credential(tree: ast.AST) -> bool:
    """A settings attribute naming BOTH a provider and secret material."""
    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
        if not name:
            continue
        lowered = name.lower()
        if any(token in lowered for token in _PROVIDER_TOKENS) and any(
            lowered.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES
        ):
            return True
    return False


def _schedules_a_connector(tree: ast.AST, source: str) -> bool:
    lowered = source.lower()
    if not any(hint in lowered for hint in _TASK_HINTS):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.decorator_list:
                continue
            if any(hint in node.name.lower() for hint in _TASK_SUBJECT_HINTS):
                return True
    return False


def _is_checkpoint_class_name(class_name: str) -> bool:
    """Does this class name denote a position in an EXTERNAL feed?

    The class-name rule is the SECONDARY net: a class that actually declares a
    watermark column is caught by `_CHECKPOINT_COLUMN_HINTS` below whatever it
    is called. This rule exists only for feed state whose column is named
    something else — which is why narrowing bare `*Cursor` costs no recall on
    anything that stores a watermark.
    """
    lowered = class_name.lower()
    if any(hint in lowered for hint in _FEED_CHECKPOINT_CLASS_HINTS):
        return True
    if any(hint in lowered for hint in _AMBIGUOUS_CHECKPOINT_CLASS_HINTS):
        return any(hint in lowered for hint in _EXTERNAL_FEED_HINTS)
    return False


def _holds_a_checkpoint(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if _is_checkpoint_class_name(node.name):
                return True
        name: str | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name and name.lower() in _CHECKPOINT_COLUMN_HINTS:
            return True
    return False


def _owns_delivery_retry(tree: ast.AST, source: str) -> bool:
    lowered = source.lower()
    if not any(hint in lowered for hint in _RETRY_HINTS):
        return False
    # Only counts alongside an actual outbound or inbound connector surface —
    # a retry loop around a local database write is not delivery machinery.
    return _imports_http_client(tree) or _is_webhook_surface(tree)


def _parse(path: pathlib.Path) -> tuple[ast.AST | None, str, str | None]:
    """Parse one file, returning WHY it could not be parsed rather than nothing.

    The old form swallowed `OSError`/`SyntaxError` into an empty classification,
    which is byte-for-byte indistinguishable from "this file holds no connector"
    — and silently LOWERS the number the ratchet defends. A file the sweep
    cannot read is not a clean file; it is an unmeasured one, and the difference
    has to survive as far as the report.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, "", f"unreadable ({type(exc).__name__}: {exc})"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, source, f"unparseable (SyntaxError line {exc.lineno}: {exc.msg})"
    return tree, source, None


def _classify(path: pathlib.Path) -> set[str]:
    tree, source, error = _parse(path)
    if tree is None or error is not None:
        return set()
    return _classify_source(tree, source)


def _classify_source(tree: ast.AST, source: str) -> set[str]:
    found: set[str] = set()
    if _imports_http_client(tree) and _issues_a_request(tree):
        found.add("http_client")
    if _is_webhook_surface(tree):
        found.add("webhook_surface")
    if _holds_provider_credential(tree):
        found.add("provider_credential")
    if _schedules_a_connector(tree, source):
        found.add("connector_task")
    if _holds_a_checkpoint(tree):
        found.add("sync_checkpoint")
    if _owns_delivery_retry(tree, source):
        found.add("delivery_retry")
    return found


def _unclassified_repositories(fleet_root: pathlib.Path) -> list[str]:
    """Fleet directories that are neither measured nor exempted with a premise.

    A Python distribution — something with a `pyproject.toml` or a `setup.py` —
    sitting beside the measured repositories and named in neither list is a
    coverage bound nobody declared. Reporting it is the whole point: five of N
    repositories measured, presented as a fleet sweep, is the silent truncation
    this block exists to prevent.
    """
    if not fleet_root.is_dir():
        return []
    known = set(REPOS) | set(OUT_OF_SCOPE_REASONS)
    found: list[str] = []
    for entry in sorted(fleet_root.iterdir()):
        if not entry.is_dir() or entry.name in known or entry.name.startswith("."):
            continue
        if (entry / "pyproject.toml").is_file() or (entry / "setup.py").is_file():
            found.append(entry.name)
    return found


def measure(fleet_root: pathlib.Path) -> tuple[dict, list[str]]:
    measured: dict[str, dict[str, int]] = {}
    absent: list[str] = []
    subtrees: dict[str, str] = {}
    scanned: dict[str, int] = {}
    outside: dict[str, int] = {}
    unmeasurable: dict[str, list[str]] = {}
    nested: dict[str, list[str]] = {}

    for repo, parts in RUNTIME_ROOTS:
        repo_root = fleet_root / repo
        root = repo_root / pathlib.Path(*parts)
        subtrees[repo] = "/".join(parts)
        if not root.is_dir():
            absent.append(repo)
            continue
        counts = dict.fromkeys(CATEGORIES, 0)
        files = _runtime_files(root)
        scanned[repo] = len(files)
        for path in files:
            tree, source, error = _parse(path)
            if error is not None:
                unmeasurable.setdefault(repo, []).append(
                    f"{path.relative_to(repo_root)}: {error}"
                )
                continue
            assert tree is not None
            for category in _classify_source(tree, source):
                counts[category] += 1
        measured[repo] = counts
        # Everything the repository holds that the measured subtree does not.
        # A bound with a number is one a reviewer can weigh.
        checkouts: list[pathlib.Path] = []
        outside[repo] = len(
            [
                path
                for path in _runtime_files(repo_root, nested_checkouts=checkouts)
                if not path.is_relative_to(root)
            ]
        )
        if checkouts:
            nested[repo] = sorted(
                str(path.relative_to(repo_root)) for path in checkouts
            )

    return (
        {
            "repos_measured": sorted(measured),
            "categories": list(CATEGORIES),
            "counts": measured,
            "totals": {
                category: sum(counts[category] for counts in measured.values())
                for category in CATEGORIES
            },
            "coverage": {
                "fleet_root": str(fleet_root),
                "measured_subtrees": subtrees,
                "files_scanned": scanned,
                "files_outside_measured_subtree": outside,
                "files_unmeasurable": unmeasurable,
                "nested_checkouts_pruned": nested,
                "repos_absent": sorted(absent),
                "repos_out_of_scope": dict(sorted(OUT_OF_SCOPE_REASONS.items())),
                "repos_unclassified": _unclassified_repositories(fleet_root),
            },
        },
        absent,
    )


def coverage_report(measured: dict) -> str:
    """Everything this run did NOT look at, as prose, on every run.

    Printed unconditionally rather than under a flag. A disclosure nobody turned
    on is the same as no disclosure.
    """
    coverage = measured["coverage"]
    lines = ["COVERAGE — what this measurement does and does not cover:"]
    for repo in sorted(coverage["measured_subtrees"]):
        subtree = coverage["measured_subtrees"][repo]
        if repo in coverage["repos_absent"]:
            lines.append(f"  {repo}: ABSENT under {coverage['fleet_root']} — not 0")
            continue
        lines.append(
            f"  {repo}: measured {coverage['files_scanned'][repo]} files under "
            f"{subtree}/; {coverage['files_outside_measured_subtree'][repo]} "
            "runtime .py files elsewhere in the repo are OUTSIDE the measurement"
        )
    for repo, problems in sorted(coverage["files_unmeasurable"].items()):
        for problem in problems:
            lines.append(f"  {repo}: UNMEASURABLE {problem}")
    for repo, checkouts in sorted(coverage["nested_checkouts_pruned"].items()):
        lines.append(
            f"  {repo}: pruned {len(checkouts)} nested git checkout(s) from the "
            f"outside-the-subtree count ({', '.join(checkouts)}) — copies of "
            "code measured elsewhere, not additional unmeasured runtime"
        )
    if coverage["repos_unclassified"]:
        lines.append(
            "  UNCLASSIFIED fleet distributions (neither measured nor exempted "
            "with a premise in OUT_OF_SCOPE): "
            + ", ".join(coverage["repos_unclassified"])
        )
    lines.append(
        f"  out of scope by declared premise: "
        f"{', '.join(sorted(coverage['repos_out_of_scope']))}"
    )
    return "\n".join(lines)


def frozen(measured: dict) -> dict:
    """The part of a measurement a baseline may freeze: the COUNTS.

    Coverage is a property of the run, not of the fleet — file totals move with
    every unrelated commit in a sibling repository, and a baseline that froze
    them would demand a re-baseline for changes this ratchet does not govern.
    Freezing it would also convert a genuine disclosure into diff noise, which
    is how disclosures stop being read.
    """
    return {key: value for key, value in measured.items() if key != "coverage"}


def _ratchet(measured: dict, baseline: dict) -> list[str]:
    """Two-directional, per ADR-0018.

    Rising means a new direct connector landed; FALLING without the baseline
    being lowered in the same change means the detector stopped seeing
    something, which reads as progress and is the failure mode a one-directional
    ratchet cannot tell from real retirement.
    """
    failures: list[str] = []
    for repo in sorted(set(baseline["counts"]) | set(measured["counts"])):
        want = baseline["counts"].get(repo)
        got = measured["counts"].get(repo)
        if want is None or got is None:
            failures.append(f"{repo}: present in one of baseline/measurement only")
            continue
        for category in CATEGORIES:
            if got[category] > want[category]:
                failures.append(
                    f"{repo}.{category}: {got[category]} > baseline "
                    f"{want[category]} — a new direct connector surface landed. "
                    "Route it through the Integrator (ADR-0024 § 6)."
                )
            elif got[category] < want[category]:
                failures.append(
                    f"{repo}.{category}: {got[category]} < baseline "
                    f"{want[category]} — lower the baseline in the SAME change "
                    "(`--write-baseline`) so the retirement is reviewable."
                )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-root", type=pathlib.Path, default=PROJECT_ROOT.parent)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--strict-coverage",
        action="store_true",
        help=(
            "refuse when a fleet distribution is neither measured nor exempted "
            "with a premise. CI runs it this way; a workstation with a second "
            "clone of an already-measured repo is not a governance defect"
        ),
    )
    args = parser.parse_args()

    measured, absent = measure(args.fleet_root)
    if absent:
        print(
            f"UNMEASURED (repo not found under {args.fleet_root}): {', '.join(absent)}"
        )
    if not measured["repos_measured"]:
        print("No fleet repository found; nothing measured. Not a pass.")
        return 2 if args.check else 0

    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(frozen(measured), indent=2, sort_keys=True) + "\n"
        )
        print(f"wrote {BASELINE.relative_to(PROJECT_ROOT)}")
        print(coverage_report(measured))
        return 0

    print(json.dumps(measured, indent=2, sort_keys=True))
    print("\n" + coverage_report(measured))
    if not args.check:
        return 0
    if absent:
        print(
            "\nRatchet abstains: the baseline covers every repository in RUNTIME_ROOTS."
        )
        return 2

    unmeasurable = measured["coverage"]["files_unmeasurable"]
    if unmeasurable:
        # Inside an enumerated repo, a file the sweep could not read lowers a
        # count for a reason that has nothing to do with retirement — and the
        # falling direction of the ratchet would then blame a rebaseline. Fail
        # on the cause rather than let it surface as a mislabelled symptom.
        print(
            "\nRefusing: files inside a MEASURED subtree could not be read, so "
            "the counts below are an undercount of unknown size."
        )
        return 2

    if args.strict_coverage and measured["coverage"]["repos_unclassified"]:
        print(
            "\nRefusing (--strict-coverage): classify each distribution above in "
            "RUNTIME_ROOTS or in OUT_OF_SCOPE with a premise a reader can check."
        )
        return 2

    failures = _ratchet(measured, json.loads(BASELINE.read_text()))
    if failures:
        print("\n" + "\n".join(failures))
        return 1
    print("\nexternal-connector ratchet PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
