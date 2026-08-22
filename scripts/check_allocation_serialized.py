"""Merge-base gate: a module's allocation must be merged BEFORE its source.

## Why a merge-base gate, and not a test

Namespace uniqueness is a property of the CANONICAL ledger, not of any one
working tree. No check a branch runs on itself can see a sibling branch, which
is exactly how three unmerged trains — `sales`, `support_access` and
`service_access_policy` — each allocated `prefix="sa"` and each stayed green.
`tests/unit/test_namespaces.py` proves the tree it runs in is self-consistent;
two parallel branches satisfy it simultaneously and still collide at merge.

The only thing that serializes allocation is comparing a branch against what is
already merged:

    for every module whose SOURCE this branch changes,
    that module's ledger row must ALREADY EXIST at the merge base.

A branch that adds a ledger row and no module source passes vacuously — that is
the allocation-only change, and it is what makes the prefix real for everybody.
A branch that adds an allocation together with its migrations fails: at its own
merge base the row does not exist yet. A later branch editing an
already-allocated module passes. So allocation lands first, on its own, where a
duplicate is a visible conflict in one file rather than a surprise at merge with
a reviewed lineage to rename.

## Classification decides what is gated

Read from the package's `EXTRACTION.toml` AT THE BRANCH HEAD, never from the
working tree and never inferred from the layout:

- `optional-module` — gated. It must resolve a ledger row, unless its manifest
  is genuinely stateless (declares no `short_code`/`migration_prefix`), which
  is a module that owns no namespace and therefore needs no allocation.
- `stateless-protocol-adapter` (connectors, OIDC) — allowed with no row.
- `presentation-foundation` (`dotmac-ui`) — allowed with no row.
- `universal-facility` (`dotmac-kernel`) — allowed with no row; it owns the
  grandfathered host lineage in `public` and declares no installable manifest.

A package whose classification is missing or unreadable FAILS. Classifying by
directory name or by "does it have a manifest" is what made the first version
fail open, and an unclassifiable package is precisely the case where guessing
is least safe.

## Deletion

Deleting a module package passes: its ledger row is a PERMANENT reservation,
deliberately never reclaimed, so a retired prefix can never be handed to a
different owner and collide with rows still live in a deployed database. A
rename is a deletion plus an addition, and the added side is checked normally.

Exit status is 0 when serialized, 1 on a violation, 2 when the gate could not
establish an answer — an indeterminate gate must never read as a pass.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib

LEDGER_PATH = "packages/dotmac-kernel/src/dotmac_kernel/namespaces.py"

# Classifications that own a database namespace and must be allocated first.
GATED_CLASSIFICATIONS = frozenset({"optional-module"})
# Classifications that legitimately own no lineage. Enumerated, so a NEW
# classification is an error rather than something that silently skips.
EXEMPT_CLASSIFICATIONS = frozenset(
    {"stateless-protocol-adapter", "presentation-foundation", "universal-facility"}
)


class GateError(Exception):
    """The gate could not establish an answer; exit 2, never a silent pass."""


def git(*args: str, repo: str | None = None) -> str:
    command = ["git", *(["-C", repo] if repo else []), *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GateError(f"{' '.join(command)} failed: {result.stderr.strip()}")
    return result.stdout


def read_blob(revision: str, path: str, *, repo: str | None = None) -> str | None:
    """File contents at a revision, or None when the path does not exist there."""
    command = ["git", *(["-C", repo] if repo else []), "show", f"{revision}:{path}"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def allocated_owners(source: str) -> dict[str, str]:
    """`{owner: branch_label}` for a ledger revision, parsed from the AST.

    AST rather than a regex for the same reason the in-tree scan uses it: a
    pattern recognises one formatting style and reports nothing for anything
    else, which in a gate means failing open.
    """
    owners: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != (
            "MigrationOwner"
        ):
            continue
        fields: dict[str, str] = {}
        for keyword in node.keywords:
            if keyword.arg in {"owner", "branch_label"} and isinstance(
                keyword.value, ast.Constant
            ):
                fields[str(keyword.arg)] = str(keyword.value.value)
        positional = [
            argument.value
            for argument in node.args
            if isinstance(argument, ast.Constant)
        ]
        owner = fields.get("owner") or (str(positional[0]) if positional else None)
        if owner is None:
            continue
        label = fields.get("branch_label") or (
            str(positional[2]) if len(positional) > 2 else owner
        )
        owners[owner] = label
    if not owners:
        raise GateError(
            "parsed no MigrationOwner rows from the base ledger — refusing to "
            "report every module as unallocated"
        )
    return owners


def declared_allocation(source: str, label: str) -> dict[str, str | None]:
    """What a manifest DECLARES, parsed fail-closed. Raises on anything unclear."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GateError(f"cannot parse {label}: {exc}") from exc
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ModuleManifest"
    ]
    if len(calls) != 1:
        raise GateError(
            f"{label} declares {len(calls)} ModuleManifest(...) calls; one "
            "package declares exactly one module"
        )
    declared: dict[str, str | None] = {}
    for field in ("code", "short_code", "migration_prefix", "migration_branch"):
        values = [kw.value for kw in calls[0].keywords if kw.arg == field]
        if not values:
            declared[field] = None
            continue
        if not isinstance(values[0], ast.Constant) or not isinstance(
            values[0].value, str
        ):
            raise GateError(
                f"{label}: {field} must be a string literal so the ledger can "
                "be checked statically"
            )
        declared[field] = values[0].value
    return declared


def classify(revision: str, package: str, *, repo: str | None = None) -> str | None:
    """The package's declared classification at `revision`; None if deleted."""
    dossier = read_blob(revision, f"packages/{package}/EXTRACTION.toml", repo=repo)
    if dossier is None:
        return None  # package does not exist at head: a deletion
    try:
        parsed = tomllib.loads(dossier)
    except tomllib.TOMLDecodeError as exc:
        raise GateError(
            f"packages/{package}/EXTRACTION.toml is unreadable: {exc}"
        ) from exc
    classification = parsed.get("classification")
    if not isinstance(classification, str) or not classification:
        raise GateError(
            f"packages/{package}/EXTRACTION.toml declares no `classification`; "
            "the gate will not guess whether a package owns a namespace"
        )
    return classification


def run_gate(base: str, head: str = "HEAD", *, repo: str | None = None) -> list[str]:
    """Violations for `base..head`. Empty means serialized. Raises on GateError."""
    merge_base = git("merge-base", base, head, repo=repo).strip()
    if not merge_base:
        raise GateError(f"no merge base between {base} and {head}")

    touched: dict[str, str] = {}
    for path in git("diff", "--name-only", f"{merge_base}..{head}", repo=repo).split(
        "\n"
    ):
        parts = path.split("/")
        # packages/<pkg>/src/... — module SOURCE only. A dossier, README or
        # changelog describes a module without being it.
        if len(parts) >= 4 and parts[0] == "packages" and parts[2] == "src":
            touched.setdefault(parts[1], path)
    if not touched:
        return []

    ledger_source = read_blob(merge_base, LEDGER_PATH, repo=repo)
    if ledger_source is None:
        raise GateError(f"no {LEDGER_PATH} at merge base {merge_base}")
    owners = allocated_owners(ledger_source)
    labels = set(owners.values())

    violations: list[str] = []
    for package, example in sorted(touched.items()):
        classification = classify(head, package, repo=repo)
        if classification is None:
            # Pure deletion. The ledger reservation is permanent and stays.
            continue
        if classification in EXEMPT_CLASSIFICATIONS:
            continue
        if classification not in GATED_CLASSIFICATIONS:
            violations.append(
                f"{package}: unknown classification {classification!r}; teach "
                "the gate whether it owns a namespace"
            )
            continue

        listing = git(
            "ls-tree", "-r", "--name-only", head, f"packages/{package}/", repo=repo
        )
        manifests = [
            line
            for line in listing.split("\n")
            if line.endswith("/manifest.py") and line.count("/") == 4
        ]
        if len(manifests) != 1:
            violations.append(
                f"{package}: optional-module changing source ({example}) with "
                f"{len(manifests)} manifest.py; its allocation cannot be checked"
            )
            continue
        source = read_blob(head, manifests[0], repo=repo)
        if source is None:
            violations.append(f"{package}: cannot read {manifests[0]} at head")
            continue
        declared = declared_allocation(source, manifests[0])

        if declared["short_code"] is None and declared["migration_prefix"] is None:
            continue  # genuinely stateless module: owns no namespace

        # Resolve by module `code`, then by `migration_branch` against the
        # ledger's `branch_label`. The branch label is the immutable lineage
        # identity, so a module whose code was renamed after allocation still
        # resolves to the row it already owns instead of looking unallocated.
        code = declared["code"]
        branch = declared["migration_branch"]
        if (code is not None and code in owners) or (
            branch is not None and branch in labels
        ):
            continue
        violations.append(
            f"{package}: changes module source ({example}) but neither code "
            f"{code!r} nor migration_branch {branch!r} has a ledger row at the "
            f"merge base {merge_base[:12]}"
        )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Serialized allocation gate.")
    parser.add_argument(
        "--base",
        required=True,
        help="immutable base commit SHA (never a moving branch ref)",
    )
    parser.add_argument("--head", default="HEAD")
    arguments = parser.parse_args()

    violations = run_gate(arguments.base, arguments.head)
    if violations:
        print("allocation is not serialized:\n", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "\nMerge an allocation-only change to the ledger first, then the "
            "module source. Uniqueness is decided in the canonical ledger; a "
            "branch cannot see a sibling branch's claim on the same prefix.",
            file=sys.stderr,
        )
        return 1
    print("allocation gate: every changed module was allocated at the merge base")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GateError as error:
        print(f"allocation gate could not run: {error}", file=sys.stderr)
        sys.exit(2)
