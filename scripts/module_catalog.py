#!/usr/bin/env python3
"""Generate the human module catalogue from the facts that already own it.

This script deliberately owns no module metadata.  It joins:

* ``packages/*/EXTRACTION.toml`` for contract, evidence and adoption state;
* package ``pyproject.toml`` for declared version and kernel requirement;
* ``ModuleManifest`` source for persistence-plane and schema declarations; and
* ``.github/release-modules.json`` for the closed module publish allowlist.

Stdlib only so the catalogue can be checked before repository dependencies are
installed, just like the module release resolver.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final = REPO_ROOT / "docs" / "MODULE_CATALOG.md"
ALLOWLIST_PATH: Final = REPO_ROOT / ".github" / "release-modules.json"

CLASSIFICATION_LABELS: Final = {
    "universal-facility": "universal facility",
    "presentation-foundation": "presentation foundation",
    "optional-module": "optional module",
    # A distribution a product CALLS rather than installs: an external protocol
    # and no rows. It has no manifest, so the "optional module has no
    # manifest.py" rule below must not reach it — the catalogue renders `n/a`
    # for its persistence plane, which is the truth rather than a gap
    # (ADR-0006, 2026-08-14 amendment).
    "stateless-protocol-adapter": "stateless protocol adapter",
}
DEDICATED_RELEASE_WORKFLOWS: Final = {
    "dotmac-kernel": ".github/workflows/release-kernel.yml",
    "dotmac-ui": ".github/workflows/release-ui.yml",
}


class CatalogError(RuntimeError):
    """The source metadata cannot produce one unambiguous catalogue row."""


@dataclass(frozen=True)
class ModuleRecord:
    distribution: str
    version: str
    classification: str
    evidence_status: str
    source_mode: str
    owner: str
    contract: str
    contract_consumers: tuple[str, ...]
    candidate_consumers: tuple[str, ...]
    package_dir: Path
    dossier_path: Path
    manifest_path: Path | None
    persistence_plane: str
    installation_sets: tuple[str, ...]
    db_schema: str | None
    kernel_requirement: str | None
    release_policy: str
    release_path: Path | None


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_allowlist(repo_root: Path) -> dict[str, dict]:
    path = repo_root / ".github" / "release-modules.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    modules = data.get("modules")
    if not isinstance(modules, dict):
        raise CatalogError(f"{path}: modules must be an object")
    return modules


def _shared_package_dirs(repo_root: Path) -> list[Path]:
    packages = repo_root / "packages"
    return sorted(
        path
        for path in packages.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )


def _module_manifest_path(package_dir: Path) -> Path | None:
    manifests = sorted((package_dir / "src").glob("*/manifest.py"))
    if len(manifests) > 1:
        listed = ", ".join(str(path) for path in manifests)
        raise CatalogError(f"{package_dir.name}: multiple module manifests: {listed}")
    return manifests[0] if manifests else None


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _module_manifest_call(path: Path) -> ast.Call:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        if not isinstance(value, ast.Call):
            continue
        is_module_assignment = any(
            isinstance(target, ast.Name) and target.id == "module" for target in targets
        )
        if not is_module_assignment:
            continue
        if _call_name(value.func) != "ModuleManifest":
            raise CatalogError(f"{path}: module is not constructed with ModuleManifest")
        return value
    raise CatalogError(f"{path}: no module = ModuleManifest(...) declaration")


def _keyword_map(call: ast.Call) -> dict[str, ast.expr]:
    return {
        keyword.arg: keyword.value
        for keyword in call.keywords
        if keyword.arg is not None
    }


def _literal_string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Constant) and node.value is None:
        return None
    return None


def _declares_values(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return node.value is not None and bool(node.value)
    if isinstance(node, ast.List | ast.Set | ast.Tuple):
        return bool(node.elts)
    # A named tuple such as TENANT_TABLES is a real declaration. The live
    # manifest and namespace gates validate its members; this catalogue only
    # classifies which plane the manifest declares.
    return True


def _plane_name(node: ast.expr) -> str | None:
    """`ModulePlane.TENANT` -> "tenant". Only the declared enum form counts."""
    if isinstance(node, ast.Attribute) and _call_name(node.value) == "ModulePlane":
        return node.attr.lower()
    return None


def _installation_sets(
    keywords: dict[str, ast.expr], capability: str
) -> tuple[str, ...]:
    """ADR-0028 § 1: what this ONE lineage can be asked to build.

    An empty or absent declaration is not "nothing" — it is the historical
    ATOMIC contract, where every declared plane installs together. Rendering
    that as a blank cell would invite exactly the omission-reads-as-intent
    confusion ADR-0028 § 2 exists to remove.
    """
    node = keywords.get("supported_plane_sets")
    if node is None or not isinstance(node, ast.Tuple | ast.List) or not node.elts:
        return ("atomic",) if capability != "stateless" else ()
    sets: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Tuple | ast.List):
            raise CatalogError("supported_plane_sets entries must be literal tuples")
        planes = [_plane_name(item) for item in element.elts]
        if any(plane is None for plane in planes):
            raise CatalogError("supported_plane_sets must use ModulePlane members")
        sets.append("+".join(sorted(plane for plane in planes if plane)))
    return tuple(dict.fromkeys(sets))


def _persistence_from_manifest(
    path: Path,
) -> tuple[str, tuple[str, ...], str | None]:
    """Capability, buildable installation sets, schema — three separate facts.

    ADR-0028 § 1 keeps these apart deliberately. Capability is what the module
    OWNS tables for; the installation sets are what its one lineage can be asked
    to BUILD. Collapsing them is the mistake ADR-0028 supersedes ADR-0027 to fix.
    """
    keywords = _keyword_map(_module_manifest_call(path))
    short_code = _literal_string(keywords.get("short_code"))
    tenant = _declares_values(keywords.get("tables"))
    platform = _declares_values(keywords.get("platform_tables"))

    if (tenant or platform) and short_code is None:
        raise CatalogError(f"{path}: declares tables without a literal short_code")
    if short_code is not None and not (tenant or platform):
        raise CatalogError(f"{path}: stateful manifest declares no persistence plane")
    if tenant and platform:
        capability = "tenant+platform"
    elif tenant:
        capability = "tenant"
    elif platform:
        capability = "platform"
    else:
        capability = "stateless"

    sets = _installation_sets(keywords, capability)
    for entry in sets:
        if entry == "atomic":
            continue
        for plane in entry.split("+"):
            if plane not in capability.split("+"):
                raise CatalogError(
                    f"{path}: supported_plane_sets names {plane!r}, which the "
                    f"manifest owns no tables for (capability {capability!r})"
                )

    schema = f"mod_{short_code}" if capability != "stateless" else None
    return capability, sets, schema


def assembly_installed(repo_root: Path = REPO_ROOT) -> frozenset[str]:
    """Which modules this assembly composes at all.

    Needed to keep the selection column honest: a module the assembly does not
    install owes it no plane selection, and flagging one would make the
    catalogue cry wolf on every distribution the product simply does not use.
    """
    source = repo_root / "app" / "assembly.py"
    if not source.exists():
        return frozenset()
    tree = ast.parse(source.read_text(encoding="utf-8"))
    installed: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "module"
            and isinstance(node.value, ast.Name)
            and node.value.id.startswith("dotmac_")
        ):
            installed.add(node.value.id.removeprefix("dotmac_"))
    return frozenset(installed)


def assembly_selections(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """What THIS assembly installs, per ADR-0028 § 2.

    Read from `app/assembly.py`'s `module_planes`, because the assembly — not
    the manifest — is where installation intent lives. A selectable module
    absent from this mapping is an invalid assembly, so a blank cell here is a
    finding rather than a default.
    """
    source = repo_root / "app" / "assembly.py"
    if not source.exists():
        return {}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selections: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "ModulePlaneSelection":
            continue
        keywords = _keyword_map(node)
        module = _literal_string(keywords.get("module"))
        planes_node = keywords.get("planes")
        if module is None or not isinstance(planes_node, ast.Tuple | ast.List):
            raise CatalogError("assembly.py: ModulePlaneSelection must use literals")
        planes = [_plane_name(item) for item in planes_node.elts]
        if not planes or any(plane is None for plane in planes):
            raise CatalogError(
                f"assembly.py: selection for {module!r} must name ModulePlane members"
            )
        selections[module] = "+".join(sorted(plane for plane in planes if plane))
    return selections


def _kernel_requirement(dependencies: dict) -> str | None:
    dependency = dependencies.get("dotmac-kernel")
    if isinstance(dependency, str):
        return dependency
    if isinstance(dependency, dict):
        version = dependency.get("version")
        if isinstance(version, str):
            return version
        path = dependency.get("path")
        if isinstance(path, str):
            return f"path:{path}"
    return None


def _string_tuple(value: object, *, field: str, package: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CatalogError(f"{package}: {field} must be a string list")
    return tuple(sorted(set(value)))


def discover_modules(repo_root: Path = REPO_ROOT) -> tuple[ModuleRecord, ...]:
    allowlist = _load_allowlist(repo_root)
    records: list[ModuleRecord] = []

    for package_dir in _shared_package_dirs(repo_root):
        pyproject_path = package_dir / "pyproject.toml"
        dossier_path = package_dir / "EXTRACTION.toml"
        if not dossier_path.is_file():
            raise CatalogError(f"{package_dir.name}: missing EXTRACTION.toml")

        poetry = _load_toml(pyproject_path)["tool"]["poetry"]
        dossier = _load_toml(dossier_path)
        distribution = poetry["name"]
        if distribution != package_dir.name or dossier.get("package") != distribution:
            raise CatalogError(
                f"{package_dir.name}: directory, pyproject name and dossier "
                "package disagree"
            )

        classification = dossier.get("classification")
        if classification not in CLASSIFICATION_LABELS:
            raise CatalogError(
                f"{distribution}: unknown classification {classification!r}"
            )

        manifest_path = _module_manifest_path(package_dir)
        if classification == "optional-module" and manifest_path is None:
            raise CatalogError(f"{distribution}: optional module has no manifest.py")
        if manifest_path is None:
            persistence_plane, installation_sets, db_schema = "n/a", (), None
        else:
            persistence_plane, installation_sets, db_schema = (
                _persistence_from_manifest(manifest_path)
            )

        release_entry = allowlist.get(distribution)
        if release_entry is not None:
            expected_dir = package_dir.relative_to(repo_root).as_posix()
            if release_entry.get("package_dir") != expected_dir:
                raise CatalogError(
                    f"{distribution}: release package_dir disagrees with {expected_dir}"
                )
            allowlisted_schema = release_entry.get("db_schema")
            if allowlisted_schema != db_schema:
                raise CatalogError(
                    f"{distribution}: manifest schema {db_schema!r} disagrees with "
                    f"release allowlist {allowlisted_schema!r}"
                )
            release_policy = "module allowlist"
            release_path = repo_root / ".github" / "release-modules.json"
        elif distribution in DEDICATED_RELEASE_WORKFLOWS:
            release_policy = "dedicated workflow"
            release_path = repo_root / DEDICATED_RELEASE_WORKFLOWS[distribution]
        else:
            release_policy = "not allowlisted"
            release_path = None

        records.append(
            ModuleRecord(
                distribution=distribution,
                version=str(poetry["version"]),
                classification=classification,
                evidence_status=str(dossier["status"]),
                source_mode=str(dossier["source_mode"]),
                owner=str(dossier["owner"]),
                contract=str(dossier["contract"]),
                contract_consumers=_string_tuple(
                    dossier.get("contract_consumers"),
                    field="contract_consumers",
                    package=distribution,
                ),
                candidate_consumers=_string_tuple(
                    dossier.get("candidate_consumers"),
                    field="candidate_consumers",
                    package=distribution,
                ),
                package_dir=package_dir,
                dossier_path=dossier_path,
                manifest_path=manifest_path,
                persistence_plane=persistence_plane,
                installation_sets=installation_sets,
                db_schema=db_schema,
                kernel_requirement=_kernel_requirement(poetry.get("dependencies", {})),
                release_policy=release_policy,
                release_path=release_path,
            )
        )

    discovered = {record.distribution for record in records}
    orphaned = sorted(set(allowlist) - discovered)
    if orphaned:
        raise CatalogError(
            "release allowlist names packages absent from the catalogue inputs: "
            + ", ".join(orphaned)
        )
    return tuple(sorted(records, key=lambda record: record.distribution))


def _relative_link(repo_root: Path, path: Path) -> str:
    return "../" + path.relative_to(repo_root).as_posix()


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", r"\|")


def _codes(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "—"


def _persistence_cell(record: ModuleRecord, repo_root: Path) -> str:
    label = record.persistence_plane
    if record.db_schema:
        label += f" · `{record.db_schema}`"
    if record.manifest_path is None:
        return label
    return f"[{label}]({_relative_link(repo_root, record.manifest_path)})"


def _installation_cell(record: ModuleRecord) -> str:
    """What the lineage can BUILD — never what this assembly installs."""
    if not record.installation_sets:
        return "—"
    if record.installation_sets == ("atomic",):
        return "atomic (all declared planes)"
    return ", ".join(f"`{entry}`" for entry in record.installation_sets)


def _selection_cell(
    record: ModuleRecord, selections: dict[str, str], installed: frozenset[str]
) -> str:
    """What THIS assembly installs — distinct from what the module can build.

    Three outcomes, and keeping them apart is the point. A module the assembly
    does not compose is simply not installed here. A composed selectable module
    with no selection is an INVALID assembly under ADR-0028 § 2, which is a
    finding. A composed atomic module has nothing to choose.
    """
    if record.persistence_plane in {"stateless", "n/a"}:
        return "—"
    module_key = record.distribution.removeprefix("dotmac-").replace("-", "_")
    if module_key not in installed:
        return "not installed here"
    chosen = selections.get(module_key)
    if chosen is not None:
        return f"`{chosen}`"
    if record.installation_sets == ("atomic",):
        return "atomic — no selection required"
    return "**not selected — invalid assembly**"


def _release_cell(record: ModuleRecord, repo_root: Path) -> str:
    if record.release_path is None:
        return record.release_policy
    target = _relative_link(repo_root, record.release_path)
    return f"[{record.release_policy}]({target})"


def render_catalog(repo_root: Path = REPO_ROOT) -> str:
    records = discover_modules(repo_root)
    selections = assembly_selections(repo_root)
    installed = assembly_installed(repo_root)
    lines = [
        "# Composable module catalogue",
        "",
        "<!-- Generated by scripts/module_catalog.py. Do not edit by hand. -->",
        "",
        "This is the discovery view for reusable Starter distributions. It joins",
        "facts from the files that already own them; it is not another module",
        "registry. Regenerate it with `make module-catalog` and verify it with",
        "`make module-catalog-check`.",
        "",
        "A **module** is a reusable distribution. An **assembly** selects and pins",
        "modules for one application. A **profile/stack** is a named assembly",
        "selection. Each application remains authoritative for its installed pins;",
        "`ModuleRegistry.inventory_payload()` reports that deployment-local state.",
        "",
        "## Reading the evidence",
        "",
        "- `audit-complete` means the boundary and source inventory are complete; it",
        "  does **not** mean a product has adopted the contract.",
        "- `adopted` means exactly one real contract consumer has cut over.",
        "- `reuse-proven` means at least two independent consumers exercise the same",
        "  contract.",
        "- `historical-pre-rule` and `audit-required` are exact grandfathered debt,",
        "  not entry states for a new package.",
        "- A release allowlist row means the workflow may publish the package. It is",
        "  neither proof that a version was published nor proof of adoption.",
        "",
        "### Planes: three different questions (ADR-0028)",
        "",
        "- **Module capability** — which planes the module owns tables for.",
        "- **Supported installation sets** — what its one lineage can be asked to",
        "  build. `atomic` is the historical contract: every declared plane installs",
        "  together, and there is nothing for an assembly to choose.",
        "- **This assembly installs** — what `app/assembly.py` actually selects.",
        "  `not installed here` means this assembly does not compose the module at",
        "  all. A composed selectable module showing **not selected** is an invalid",
        "  assembly, not a default; ADR-0028 § 2 makes omission fail rather than",
        "  infer.",
        "",
        "Capability is not intent. A module that *can* build a platform plane has",
        "not thereby installed one, and reading the first as the second is the",
        "confusion ADR-0028 supersedes ADR-0027 to remove.",
        "",
        "## Catalogue",
        "",
        "| Distribution | Classification | Evidence | Module capability "
        "| Supported installation sets | This assembly installs | Release policy "
        "| Declared version | Kernel requirement | Proven consumers | "
        "Candidate consumers |",
        "|---|---|---|---|---|---|---|---:|---|---|---|",
    ]

    for record in records:
        readme = record.package_dir / "README.md"
        package_target = readme if readme.is_file() else record.dossier_path
        distribution = (
            f"[`{record.distribution}`]({_relative_link(repo_root, package_target)})"
        )
        evidence = (
            f"[`{record.evidence_status}`]"
            f"({_relative_link(repo_root, record.dossier_path)})"
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    distribution,
                    CLASSIFICATION_LABELS[record.classification],
                    evidence,
                    _persistence_cell(record, repo_root),
                    _installation_cell(record),
                    _selection_cell(record, selections, installed),
                    _release_cell(record, repo_root),
                    f"`{record.version}`",
                    f"`{record.kernel_requirement}`"
                    if record.kernel_requirement
                    else "—",
                    _codes(record.contract_consumers),
                    _codes(record.candidate_consumers),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Contracts and ownership",
            "",
            "The dossier linked from each entry remains authoritative for source",
            "paths, parity tests, first cutover, drift proof, local-copy retirement",
            "and the next gate.",
            "",
        ]
    )

    for record in records:
        readme = record.package_dir / "README.md"
        package_target = readme if readme.is_file() else record.dossier_path
        package_link = _relative_link(repo_root, package_target)
        dossier_link = _relative_link(repo_root, record.dossier_path)
        lines.extend(
            [
                f"### [`{record.distribution}`]({package_link})",
                "",
                f"- **Owner:** {_cell(record.owner)}",
                f"- **Contract:** {_cell(record.contract)}",
                f"- **Evidence:** `{record.evidence_status}` from "
                f"[`EXTRACTION.toml`]({dossier_link}); "
                f"source mode `{record.source_mode}`.",
                f"- **Proven consumers:** {_codes(record.contract_consumers)}.",
                f"- **Candidate consumers:** {_codes(record.candidate_consumers)}.",
                "",
            ]
        )

    return "\n".join(lines)


def _check(output: Path, expected: str) -> int:
    if not output.is_file():
        print(f"{output} is missing; run make module-catalog", file=sys.stderr)
        return 1
    actual = output.read_text(encoding="utf-8")
    if actual == expected:
        return 0
    print(f"{output} is stale; run make module-catalog", file=sys.stderr)
    diff = difflib.unified_diff(
        actual.splitlines(),
        expected.splitlines(),
        fromfile=str(output),
        tofile="generated module catalogue",
        lineterm="",
    )
    for line in list(diff)[:80]:
        print(line, file=sys.stderr)
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed catalogue differs from its machine sources",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="catalogue path (defaults to docs/MODULE_CATALOG.md)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rendered = render_catalog()
    except (CatalogError, KeyError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"module catalogue refused: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return _check(args.output, rendered)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
