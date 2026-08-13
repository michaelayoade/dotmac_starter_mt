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

Run it from a checkout that has the fleet beside it::

    python scripts/external_connector_sweep.py --check
    python scripts/external_connector_sweep.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
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
_CHECKPOINT_CLASS_HINTS = ("checkpoint", "cursor", "syncstate", "synccursor")
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


def _runtime_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not _EXCLUDED_PARTS & set(path.parts) and not path.name.startswith("test_")
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


def _holds_a_checkpoint(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if any(hint in node.name.lower() for hint in _CHECKPOINT_CLASS_HINTS):
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


def _classify(path: pathlib.Path) -> set[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return set()

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


def measure(fleet_root: pathlib.Path) -> tuple[dict, list[str]]:
    measured: dict[str, dict[str, int]] = {}
    absent: list[str] = []

    for repo, parts in RUNTIME_ROOTS:
        root = fleet_root / repo / pathlib.Path(*parts)
        if not root.is_dir():
            absent.append(repo)
            continue
        counts = dict.fromkeys(CATEGORIES, 0)
        for path in _runtime_files(root):
            for category in _classify(path):
                counts[category] += 1
        measured[repo] = counts

    return (
        {
            "repos_measured": sorted(measured),
            "categories": list(CATEGORIES),
            "counts": measured,
            "totals": {
                category: sum(counts[category] for counts in measured.values())
                for category in CATEGORIES
            },
        },
        absent,
    )


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
        BASELINE.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
        print(f"wrote {BASELINE.relative_to(PROJECT_ROOT)}")
        return 0

    print(json.dumps(measured, indent=2, sort_keys=True))
    if not args.check:
        return 0
    if absent:
        print(
            "\nRatchet abstains: the baseline covers every repository in RUNTIME_ROOTS."
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
