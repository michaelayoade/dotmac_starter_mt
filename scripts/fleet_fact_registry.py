"""Measure fact-level ownership across ERP, CRM and Sub.

The decomposition matrix answers "which capability family is duplicated". This
answers the finer question the programme frame actually gates module assignment
on: **is there a named owner for each fact, and does a second product write it
too?**

Sub already did this work for itself — `app/services/sot_registry/` declares
services, the facts each `owns`, and the module that implements them, validated
by Sub's own registry tests. ERP has a smaller `sot_relationships.py` of the
same shape. CRM has neither.

So this script EXTRACTS declarations; it does not author them.

**It is a direct-import reachability HEURISTIC, not an ownership measure.** The
distinction is the whole reason this docstring is long. The detector counts
`owns` strings without retaining their identities, separately scans
`from app.models... import Model` statements, and marks every imported model as
linked. It never associates a particular fact with a particular table.

So a direct import may be an input or a projection rather than a write, and a
real writer may reach its table indirectly through a repository or helper and
leave no edge at all. **Neither the linked nor the unlinked direction is a
reliable ownership bound**, and an earlier revision of the accompanying document
wrongly claimed the unlinked direction was.

What it therefore proves, and what it does not, is recorded in the artifact
itself (`provenance.proves` / `provenance.does_not_prove`) so a reader of the
JSON cannot pick up the numbers without the caveat.

The tables with no detected edge are a **triage queue** — a high-priority manual
review list — not a proven set of unowned facts.

The correct long-term fix is explicit product-side linkage: each declared fact
carrying `fact_id`, `owner_service_id`, the state/table/external reference it
governs, a `role` of authoritative | observation | projection | input, its owned
transitions, and its evidence/tests. Until that exists, these counts sequence
work and flag candidates; they settle nothing.

Deliberately NOT copied here: the 1,392 fact strings Sub declares. Sub's
registry is their authority and validates them; duplicating the text into this
repository would replicate a build while documenting why not to. Starter
collects stable IDs, counts and references; governance adjudicates collisions
using those IDs.

    python scripts/fleet_fact_registry.py --check
    python scripts/fleet_fact_registry.py --write
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import pathlib
import subprocess
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "docs" / "inventories" / "fleet-fact-registry.json"

# Bump when the detector's semantics change, so a stored artifact cannot be
# compared against numbers that were produced a different way.
SCHEMA_VERSION = 1

NORMALIZATION_RULES = (
    "tables: `__tablename__` string literals assigned in a class body (AST, not grep)",
    "declared services: `SOTService(...)` calls under a product's declaration source",
    "domain attribution: the `DomainSOT` in the same file, else the same directory",
    "edges: `from app.models... import Model` in a declared service module/package",
    "a model class maps to a table only via its own `__tablename__`",
)

PROVES = (
    "the count of declared facts and services per product",
    "which tables have at least one direct import edge from a declared service module",
    "which duplicated tables have no such edge from any product",
)

DOES_NOT_PROVE = (
    "that a table without an edge has no owner",
    "that a table with an edge is owned, rather than read, by that service",
    "that any particular declared fact corresponds to any particular table",
    "that a duplicated table's underlying fact is or is not declared",
)

# Where each product declares fact ownership, if it does at all.
DECLARATION_SOURCES: dict[str, tuple[str, ...]] = {
    "dotmac_sub": ("app/services/sot_registry/domains",),
    "dotmac_erp": ("app/services/sot_relationships.py",),
    "dotmac_crm": (),
}


def model_tables(repo: pathlib.Path) -> dict[str, str]:
    """Map every model class to its table name."""
    found: dict[str, str] = {}
    models = repo / "app" / "models"
    if not models.is_dir():
        return found
    for path in sorted(models.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(
                        getattr(t, "id", None) == "__tablename__" for t in stmt.targets
                    )
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    found[node.name] = stmt.value.value
    return found


def _literal(node: ast.expr | None) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def declarations(repo: pathlib.Path, sources: tuple[str, ...]) -> list[dict]:
    """Every declared service: its domain, module, and the facts it owns.

    Domain attribution cannot walk the `DomainSOT(services=...)` expression:
    Sub shards a large domain across a package and composes it as
    `services=(*FOUNDATION_SERVICES, *FIBER_PLANT_SERVICES, ...)`, so the
    `SOTService` calls live in sibling modules and a nested walk finds none of
    them. Attributing that way silently reported 230 of Sub's 426 services.

    The rule instead: a file declaring `DomainSOT` names its directory's domain;
    every `SOTService` in that file or its sibling shards belongs to it.
    """
    files: list[pathlib.Path] = []
    for source in sources:
        target = repo / source
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.is_file():
            files.append(target)

    trees: dict[pathlib.Path, ast.Module] = {}
    for path in files:
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

    # A file may declare several domains (ERP keeps all nine in one module); a
    # directory maps to the domain(s) its DomainSOT-declaring file names.
    per_file: dict[pathlib.Path, list[str]] = {}
    for path, tree in trees.items():
        names = [
            str(_literal({k.arg: k.value for k in node.keywords}.get("domain")))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "DomainSOT"
            and _literal({k.arg: k.value for k in node.keywords}.get("domain"))
        ]
        if names:
            per_file[path] = names

    services: list[dict] = []
    for path, tree in trees.items():
        owner = per_file.get(path) or next(
            (names for other, names in per_file.items() if other.parent == path.parent),
            ["<unattributed>"],
        )
        domain = owner[0] if len(owner) == 1 else "<multiple>"
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "SOTService"
            ):
                continue
            svc = {k.arg: k.value for k in node.keywords}
            name = _literal(svc.get("name"))
            if not name:
                continue
            owns = _literal(svc.get("owns")) or ()
            services.append(
                {
                    "domain": domain
                    if path not in per_file
                    else _domain_of(node, tree, owner),
                    "service": str(name),
                    "module": _literal(svc.get("module")),
                    "facts": len(tuple(owns)),
                }
            )
    return services


def _domain_of(service: ast.Call, tree: ast.Module, fallback: list[str]) -> str:
    """Which `DomainSOT` in this file encloses `service`, when several do."""
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DomainSOT"
        ):
            continue
        if any(inner is service for inner in ast.walk(node)):
            return str(_literal({k.arg: k.value for k in node.keywords}.get("domain")))
    return fallback[0]


def owned_tables(
    repo: pathlib.Path, services: list[dict], classes: dict[str, str]
) -> set[str]:
    """Tables reachable from a declared owner module's model imports.

    Import-reachability, not a write analysis: it answers "does a declared owner
    touch this table", which is the weaker claim, and the one that cannot
    over-report an owner that does not exist.
    """
    linked: set[str] = set()
    for service in services:
        module = service.get("module")
        if not module:
            continue
        base = repo / str(module).replace(".", "/")
        candidates = (
            [base.with_suffix(".py")]
            if base.with_suffix(".py").is_file()
            else (sorted(base.rglob("*.py")) if base.is_dir() else [])
        )
        for path in candidates:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "app.models"
                ):
                    for alias in node.names:
                        if alias.name in classes:
                            linked.add(classes[alias.name])
    return linked


def _revision(repo: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()[:12] or None


def measure(fleet_root: pathlib.Path, measured_at: str) -> tuple[dict, list[str]]:
    absent: list[str] = []
    products: dict[str, dict] = {}
    tables: dict[str, set[str]] = {}
    reachable: dict[str, set[str]] = {}
    revisions: dict[str, str | None] = {}

    for repo_name, sources in DECLARATION_SOURCES.items():
        repo = fleet_root / repo_name
        if not (repo / "app" / "models").is_dir():
            absent.append(repo_name)
            continue
        classes = model_tables(repo)
        services = declarations(repo, sources)
        table_names = set(classes.values())
        edged = owned_tables(repo, services, classes) & table_names

        tables[repo_name] = table_names
        reachable[repo_name] = edged
        revisions[repo_name] = _revision(repo)
        products[repo_name] = {
            "tables": len(table_names),
            "declares_ownership": bool(sources),
            "domains": len({s["domain"] for s in services}),
            "services": len(services),
            "declared_facts": sum(s["facts"] for s in services),
            # Deliberately NOT "tables_with_a_named_owner". The detector proves a
            # direct `from app.models... import Model` edge from a declared
            # service module — nothing about which fact, nor whether the service
            # writes rather than reads.
            "tables_with_a_direct_import_edge": len(edged),
            "tables_without_a_direct_import_edge": len(table_names - edged),
            "tables_without_an_edge": sorted(table_names - edged),
        }

    every_table = set().union(*tables.values()) if tables else set()
    duplicated = {
        table for table in every_table if sum(table in n for n in tables.values()) >= 2
    }
    for repo_name in products:
        exclusive = tables[repo_name] - duplicated
        products[repo_name]["tables_duplicated_elsewhere"] = len(
            tables[repo_name] & duplicated
        )
        products[repo_name]["tables_exclusive_to_this_product"] = len(exclusive)

    any_edge = set().union(*reachable.values()) if reachable else set()
    no_edge = sorted(duplicated - any_edge)

    report = {
        "provenance": {
            "measured_at": measured_at,
            "revisions": revisions,
            "detector": "scripts/fleet_fact_registry.py",
            "detector_schema_version": SCHEMA_VERSION,
            "method": "direct-import reachability heuristic",
            "normalization": NORMALIZATION_RULES,
            "proves": PROVES,
            "does_not_prove": DOES_NOT_PROVE,
        },
        "products_measured": sorted(products),
        "products": products,
        "duplicate_tables": {
            "duplicated_tables": len(duplicated),
            "duplicates_with_a_direct_import_edge": len(duplicated & any_edge),
            "duplicates_without_any_direct_import_edge": len(no_edge),
            # A triage queue, not a proven set of unowned facts.
            "triage_queue": no_edge,
        },
        "fact_text_authority": {
            repo: sources[0] if sources else None
            for repo, sources in DECLARATION_SOURCES.items()
        },
    }
    return report, absent


def _ratchet(measured: dict, stored: dict) -> list[str]:
    """Detected edges may only improve; the triage queue may only shrink.

    Both directions fail, for the ADR-0018 reason: a frozen figure that never
    follows reality down stops being evidence of anything.

    Provenance is deliberately excluded — the measurement date and the fleet
    revisions change on every run, and a ratchet that failed on them would fail
    always and be switched off.
    """
    failures: list[str] = []
    if stored["provenance"]["detector_schema_version"] != SCHEMA_VERSION:
        return [
            "detector schema changed "
            f"({stored['provenance']['detector_schema_version']} -> {SCHEMA_VERSION}); "
            "re-run --write, the stored numbers were produced a different way"
        ]
    for repo, want in stored["products"].items():
        got = measured["products"].get(repo)
        if got is None:
            failures.append(f"{repo}: recorded but no longer measured")
            continue
        for key, direction in (
            ("tables_with_a_direct_import_edge", "up"),
            ("tables_without_a_direct_import_edge", "down"),
        ):
            g, w = got[key], want[key]
            if g == w:
                continue
            improved = (g > w) if direction == "up" else (g < w)
            verb = "improved" if improved else "regressed"
            failures.append(
                f"{repo}.{key}: {verb} {w} -> {g}; "
                + (
                    "record it in the registry"
                    if improved
                    else "detected coverage may not regress"
                )
            )
    got = measured["duplicate_tables"]["duplicates_without_any_direct_import_edge"]
    want = stored["duplicate_tables"]["duplicates_without_any_direct_import_edge"]
    if got > want:
        failures.append(f"triage queue rose {want} -> {got}; it may only shrink")
    elif got < want:
        failures.append(
            f"triage queue fell {want} -> {got}; lower the registry to record it"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-root", type=pathlib.Path, default=PROJECT_ROOT.parent)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--measured-at",
        default=datetime.date.today().isoformat(),
        help="ISO date recorded in the artifact's provenance (default: today).",
    )
    args = parser.parse_args()

    measured, absent = measure(args.fleet_root, args.measured_at)
    if absent:
        print(
            f"UNMEASURED (repo not found under {args.fleet_root}): {', '.join(absent)}"
        )
    if not measured["products_measured"]:
        print("No fleet repository found; nothing measured. Not a pass.")
        return 2 if args.check else 0

    if args.write:
        REGISTRY.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
        print(f"wrote {REGISTRY.relative_to(PROJECT_ROOT)}")
        return 0

    summary = {
        repo: {k: v for k, v in data.items() if k != "tables_without_an_edge"}
        for repo, data in measured["products"].items()
    }
    print(
        json.dumps(
            {
                **measured,
                "products": summary,
                "duplicate_tables": {
                    k: v
                    for k, v in measured["duplicate_tables"].items()
                    if k != "triage_queue"
                },
            },
            indent=2,
            sort_keys=True,
        )
    )

    if not args.check:
        return 0
    if absent:
        print("\nRatchet abstains: the registry covers all three source monoliths.")
        return 2
    failures = _ratchet(measured, json.loads(REGISTRY.read_text()))
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
