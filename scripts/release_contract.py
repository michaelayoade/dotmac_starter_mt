#!/usr/bin/env python3
"""Resolve, inspect and verify a product-owned CONTRACT CATALOGUE release.

This is a release profile, not a new extraction classification. A catalogue is
still a ``stateless-protocol-adapter``: called by control planes, no persistence
or module manifest. The stricter profile proves something an ordinary adapter
does not promise—the installed artifact's Product Manifest, capability
contracts and canonical schema bytes exactly cover one another.

The allowlist is intentionally closed. Every subcommand refuses rather than
warns; publishing a malformed ownership contract would let all later plans be
cryptographically exact about semantics no product actually declared.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib
import zipfile
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from declared_publication_sweep import git_tags
from release_module import ReleaseRefused, secret_shaped

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
ALLOWLIST: Final = REPO_ROOT / ".github" / "release-contracts.json"
KERNEL_TAG_PREFIX: Final = "dotmac-kernel-v"

REQUIRED_FIELDS: Final = (
    "package_dir",
    "import_name",
    "owner_code",
    "kernel_floor",
    "composition_dependencies",
    "tag_prefix",
    "wheel_contents",
)
WRONG_PROFILE_FIELDS: Final = (
    "db_schema",
    "manifest_attr",
    "integration_floor",
    "connector_key",
    "plugin_attr",
)
FORBIDDEN_IMPORT_ROOTS: Final = frozenset(
    {
        "alembic",
        "ansible",
        "asyncpg",
        "httpx",
        "paramiko",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
)
_STABLE_CODE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")


def load_policy() -> dict:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    conformance = data.get("conformance")
    contracts = data.get("contracts")
    if not isinstance(conformance, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'conformance' must be an object")
    if not isinstance(contracts, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'contracts' must be an object")
    return data


def _dependency_constraint(pyproject: dict, distribution: str) -> str | None:
    dependency = (
        pyproject.get("tool", {})
        .get("poetry", {})
        .get("dependencies", {})
        .get(distribution)
    )
    if isinstance(dependency, str):
        return dependency
    if isinstance(dependency, dict) and isinstance(dependency.get("version"), str):
        return dependency["version"]
    return None


def _has_connector_entry_point(pyproject: dict) -> bool:
    poetry = pyproject.get("tool", {}).get("poetry", {})
    plugins = poetry.get("plugins", {})
    if isinstance(plugins, dict) and "dotmac_integration.connectors" in plugins:
        return True
    project = pyproject.get("project", {})
    entry_points = project.get("entry-points", {})
    return isinstance(entry_points, dict) and (
        "dotmac_integration.connectors" in entry_points
    )


def resolve(distribution: str, *, tags: set[str] | None = None) -> dict:
    """Resolve one reviewed catalogue entry and prove its published floor."""

    policy = load_policy()
    contracts = policy["contracts"]
    entry = contracts.get(distribution)
    if entry is None:
        listed = ", ".join(sorted(contracts)) or "(none — the lane is shut)"
        raise ReleaseRefused(
            f"{distribution!r} is not an allowlisted contract catalogue. "
            f"Publishable catalogues are: {listed}. Absence is the publication lock."
        )
    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        raise ReleaseRefused(
            f"{distribution}: contract entry is missing {', '.join(missing)}"
        )
    misplaced = [field for field in WRONG_PROFILE_FIELDS if field in entry]
    if misplaced:
        raise ReleaseRefused(
            f"{distribution}: wrong release profile; entry declares "
            f"{', '.join(misplaced)}"
        )

    conformance = policy["conformance"]
    package_dir_value = entry["package_dir"]
    if not isinstance(package_dir_value, str) or not (
        package_dir_value.startswith(conformance["package_dir_prefix"])
        and package_dir_value.endswith(conformance["package_dir_suffix"])
        and distribution.endswith("-contracts")
    ):
        raise ReleaseRefused(
            f"{distribution}: contract catalogues live under packages/ and both "
            "distribution and package_dir must end in '-contracts'"
        )
    if (
        not isinstance(entry["owner_code"], str)
        or _STABLE_CODE.fullmatch(entry["owner_code"]) is None
    ):
        raise ReleaseRefused(f"{distribution}: owner_code is not a stable code")

    package_path = REPO_ROOT / package_dir_value
    pyproject_path = package_path / "pyproject.toml"
    dossier_path = package_path / "EXTRACTION.toml"
    if not pyproject_path.is_file():
        raise ReleaseRefused(f"{distribution}: package has no pyproject.toml")
    if not dossier_path.is_file():
        raise ReleaseRefused(f"{distribution}: package has no EXTRACTION.toml")
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    if dossier.get("classification") != conformance["classification"]:
        raise ReleaseRefused(
            f"{distribution}: EXTRACTION.toml classification must be "
            f"{conformance['classification']!r}"
        )

    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    if _has_connector_entry_point(pyproject):
        raise ReleaseRefused(
            f"{distribution}: a contract catalogue cannot declare a connector "
            "entry point"
        )
    floor = entry["kernel_floor"]
    constraint = _dependency_constraint(pyproject, "dotmac-kernel")
    if constraint is None or f">={floor}" not in constraint.replace(" ", ""):
        raise ReleaseRefused(
            f"{distribution}: kernel_floor {floor!r} is not the declared "
            f"dotmac-kernel dependency floor {constraint!r}"
        )
    composition_dependencies = entry["composition_dependencies"]
    if not isinstance(composition_dependencies, dict) or not all(
        isinstance(name, str)
        and name.endswith("-contracts")
        and isinstance(version, str)
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9A-Za-z.-]+", version)
        for name, version in composition_dependencies.items()
    ):
        raise ReleaseRefused(
            f"{distribution}: composition_dependencies must map contract "
            "distribution names to exact versions"
        )
    for name, version in composition_dependencies.items():
        declared_constraint = _dependency_constraint(pyproject, name)
        if declared_constraint != version:
            raise ReleaseRefused(
                f"{distribution}: composition dependency {name!r} must be "
                f"exactly {version!r}, not {declared_constraint!r}"
            )
    known_tags = set(git_tags(REPO_ROOT) if tags is None else tags)
    if f"{KERNEL_TAG_PREFIX}{floor}" not in known_tags:
        raise ReleaseRefused(
            f"{distribution}: kernel_floor {floor!r} has no release tag; an "
            "installer cannot resolve a declared-only contract grammar"
        )

    return {
        **entry,
        "distribution": distribution,
        "package_path": package_path,
        "pyproject": pyproject,
    }


def _python_source_violations(package_path: pathlib.Path) -> list[str]:
    violations: list[str] = []
    for source in sorted((package_path / "src").rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        relative = source.relative_to(package_path)
        for node in ast.walk(tree):
            root: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{relative}: imports {root}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{relative}: imports {root}")
            elif isinstance(node, ast.Name) and node.id == "ModuleManifest":
                violations.append(f"{relative}: declares ModuleManifest")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    violations.append(f"{relative}: uses dynamic import")
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"
                ):
                    violations.append(f"{relative}: uses dynamic import")
    return sorted(set(violations))


def conformance(distribution: str) -> None:
    entry = resolve(distribution)
    violations = _python_source_violations(entry["package_path"])
    if _has_connector_entry_point(entry["pyproject"]):
        violations.append("pyproject: declares connector entry point")
    for path in entry["package_path"].rglob("*"):
        if path.is_file() and "migrations" in path.parts:
            violations.append(f"{path.relative_to(entry['package_path'])}: migration")
    if violations:
        raise ReleaseRefused(
            f"{distribution}: contract catalogue must be data-only:\n  - "
            + "\n  - ".join(sorted(set(violations)))
        )


def _declared(entry: dict) -> dict:
    return entry["pyproject"]["tool"]["poetry"]


def cmd_resolve(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    declared = _declared(entry)
    if declared["name"] != args.distribution:
        raise ReleaseRefused(
            f"pyproject declares {declared['name']!r}, dispatched {args.distribution!r}"
        )
    if args.version and declared["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: dispatched version {args.version!r} != package "
            f"version {declared['version']!r}"
        )
    for key in ("package_dir", "import_name", "owner_code", "tag_prefix"):
        print(f"{key}={entry[key]}")
    dependency_dirs = [
        load_policy()["contracts"][name]["package_dir"]
        for name in sorted(entry["composition_dependencies"])
    ]
    print(f"composition_dependency_dirs={' '.join(dependency_dirs)}")
    print(f"version={declared['version']}")
    print(f"tag={entry['tag_prefix']}{declared['version']}")


def cmd_conformance(args: argparse.Namespace) -> None:
    conformance(args.distribution)
    print(f"{args.distribution}: source conformance OK")


def cmd_inspect(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    policy = entry["wheel_contents"]
    wheels = sorted(pathlib.Path(args.dist).glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseRefused(
            f"expected exactly one wheel in {args.dist}, found {len(wheels)}"
        )
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata is None:
            raise ReleaseRefused(f"{wheel.name}: no METADATA")
        metadata_text = archive.read(metadata).decode("utf-8")
        entry_points = "\n".join(
            archive.read(name).decode("utf-8")
            for name in names
            if name.endswith(".dist-info/entry_points.txt")
        )

    problems: list[str] = []
    for required in policy["required"]:
        if required not in names:
            problems.append(f"missing from wheel: {required}")
    for name in names:
        if any(name.startswith(prefix) for prefix in policy["forbidden_prefixes"]):
            problems.append(f"forbidden content: {name}")
        if "/migrations/" in name:
            problems.append(f"migration lineage in contract catalogue: {name}")
    if "dotmac_integration.connectors" in entry_points:
        problems.append("connector entry point in contract catalogue")
    allowed = {name.lower() for name in policy["allowed_requires"]}
    for line in metadata_text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = line.split(":", 1)[1].strip()
        name = re.split(r"[ (<>=!\[]", requirement, maxsplit=1)[0].lower()
        if name not in allowed:
            problems.append(f"dependency outside allowed closure: {requirement!r}")
    problems.extend(secret_shaped(names))
    if problems:
        raise ReleaseRefused(
            f"{wheel.name} fails contract wheel policy:\n  - " + "\n  - ".join(problems)
        )
    print(f"{wheel.name}: content policy OK ({len(names)} entries)")


_INSTALLED_CONFORMANCE = r"""
import pathlib
from importlib.metadata import version as installed_version
import {import_name} as package
from dotmac_kernel import (
    CapabilityCompositionSnapshot,
    CapabilityContractSnapshot,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)

here = pathlib.Path(package.__file__).resolve()
assert "site-packages" in str(here), here
assert package.__version__ == {version!r}
for name in package.__all__:
    assert hasattr(package, name), name
for required in {required_exports!r}:
    assert required in package.__all__, required

manifest = package.PRODUCT_MANIFEST
contracts = package.CAPABILITY_CONTRACTS
schemas = package.CAPABILITY_SCHEMAS
compositions = package.CAPABILITY_COMPOSITIONS
dependency_contracts = package.COMPOSITION_DEPENDENCY_CONTRACTS
dependency_schemas = package.COMPOSITION_DEPENDENCY_SCHEMAS
assert isinstance(manifest, ProductManifestSnapshot)
assert manifest.product_code == {owner_code!r}
assert manifest.product_version == {version!r}
for distribution, expected_version in {composition_dependencies!r}.items():
    assert installed_version(distribution) == expected_version
assert isinstance(contracts, tuple)
assert all(isinstance(item, CapabilityContractSnapshot) for item in contracts)
contract_order = tuple(
    (item.capability_code, item.schema_version) for item in contracts
)
assert contract_order == tuple(sorted(contract_order))
assert len({{item.capability_id for item in contracts}}) == len(contracts)
assert {{item.capability_id for item in contracts}} == set(manifest.capability_codes)

expected = {{}}
for contract in contracts:
    contract.require_declared_by(manifest)
    for operation in contract.operations:
        for reference, digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        ):
            previous = expected.setdefault(reference, digest)
            assert previous == digest, (reference, previous, digest)

assert isinstance(schemas, tuple)
assert all(isinstance(item, CapabilitySchemaDocument) for item in schemas)
assert tuple(item.schema_ref for item in schemas) == tuple(
    sorted(item.schema_ref for item in schemas)
)
assert len({{item.schema_ref for item in schemas}}) == len(schemas)
assert set(expected) == {{item.schema_ref for item in schemas}}
for schema in schemas:
    CapabilitySchemaDocument.from_json_bytes(
        schema.to_json_bytes(),
        expected_ref=schema.schema_ref,
        expected_digest=expected[schema.schema_ref],
    )
assert isinstance(compositions, tuple)
assert all(isinstance(item, CapabilityCompositionSnapshot) for item in compositions)
assert contracts or compositions
assert bool(contracts) == bool(schemas)
assert tuple(item.identity for item in compositions) == tuple(
    sorted(item.identity for item in compositions)
)

assert isinstance(dependency_contracts, tuple)
assert all(
    isinstance(item, CapabilityContractSnapshot) for item in dependency_contracts
)
assert tuple(item.identity for item in dependency_contracts) == tuple(
    sorted(item.identity for item in dependency_contracts)
)
assert len({{item.identity for item in dependency_contracts}}) == len(
    dependency_contracts
)
assert not ({{item.identity for item in contracts}} & {{
    item.identity for item in dependency_contracts
}})

dependency_expected = {{}}
for contract in dependency_contracts:
    for operation in contract.operations:
        for reference, digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        ):
            previous = dependency_expected.setdefault(reference, digest)
            assert previous == digest, (reference, previous, digest)
assert isinstance(dependency_schemas, tuple)
assert all(isinstance(item, CapabilitySchemaDocument) for item in dependency_schemas)
assert tuple(item.schema_ref for item in dependency_schemas) == tuple(
    sorted(item.schema_ref for item in dependency_schemas)
)
assert len({{item.schema_ref for item in dependency_schemas}}) == len(
    dependency_schemas
)
assert set(dependency_expected) == {{
    item.schema_ref for item in dependency_schemas
}}
for schema in dependency_schemas:
    CapabilitySchemaDocument.from_json_bytes(
        schema.to_json_bytes(),
        expected_ref=schema.schema_ref,
        expected_digest=dependency_expected[schema.schema_ref],
    )

referenced_dependency_identities = set()
for composition in compositions:
    composition.require_owned_by(manifest)
    for binding in composition.evidence_bindings:
        for identity in (
            (
                binding.source_owner_code,
                binding.source_capability_code,
                binding.source_capability_schema_version,
            ),
            (
                binding.target_owner_code,
                binding.target_capability_code,
                binding.target_capability_schema_version,
            ),
        ):
            if identity not in {{item.identity for item in contracts}}:
                referenced_dependency_identities.add(identity)
    composition.require_compatible_with(
        contracts=contracts + dependency_contracts,
        schemas=schemas + dependency_schemas,
    )
assert referenced_dependency_identities == {{
    item.identity for item in dependency_contracts
}}
assert not hasattr(package, "ModuleManifest")
print(
    "verified", package.__name__, package.__version__,
    len(contracts), len(schemas), len(compositions)
)
"""


def _venv(path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    bin_dir = path / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / "python", bin_dir / "pip"


def _verify_installed(python: pathlib.Path, entry: dict, version: str) -> None:
    script = _INSTALLED_CONFORMANCE.format(
        import_name=entry["import_name"],
        owner_code=entry["owner_code"],
        required_exports=tuple(load_policy()["conformance"]["required_exports"]),
        composition_dependencies=entry["composition_dependencies"],
        version=version,
    )
    subprocess.run([str(python), "-c", script], check=True)


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    import tempfile

    entry = resolve(args.distribution)
    version = _declared(entry)["version"]
    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(pathlib.Path(tmp) / "venv")
        links = ["--find-links", args.dist, "--find-links", args.kernel_dist]
        if args.dependency_dist:
            links.extend(["--find-links", args.dependency_dist])
        subprocess.run(
            [str(pip), "install", "--quiet", *links, entry["distribution"]],
            check=True,
        )
        _verify_installed(python, entry, version)
    print(f"{entry['distribution']}: wheel conformance OK")


def cmd_verify_registry(args: argparse.Namespace) -> None:
    import tempfile

    distribution, separator, version = args.pin.partition("==")
    if separator != "==" or not version:
        raise ReleaseRefused(f"{args.pin!r} is not an exact pin")
    entry = resolve(distribution)
    public_index = os.environ.get("PUBLIC_INDEX_URL", "https://pypi.org/simple")
    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(pathlib.Path(tmp) / "venv")
        subprocess.run(
            [
                str(pip),
                "install",
                "--quiet",
                "--index-url",
                args.index,
                "--extra-index-url",
                public_index,
                args.pin,
            ],
            check=True,
        )
        _verify_installed(python, entry, version)
    print(f"registry verification OK for {args.pin}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    resolve_parser = sub.add_parser("resolve")
    resolve_parser.add_argument("distribution")
    resolve_parser.add_argument("--version", default="")
    resolve_parser.set_defaults(func=cmd_resolve)

    conformance_parser = sub.add_parser("conformance")
    conformance_parser.add_argument("distribution")
    conformance_parser.set_defaults(func=cmd_conformance)

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("distribution")
    inspect_parser.add_argument("--dist", required=True)
    inspect_parser.set_defaults(func=cmd_inspect)

    wheel_parser = sub.add_parser("verify-wheel")
    wheel_parser.add_argument("distribution")
    wheel_parser.add_argument("--dist", required=True)
    wheel_parser.add_argument("--kernel-dist", required=True)
    wheel_parser.add_argument("--dependency-dist", default="")
    wheel_parser.set_defaults(func=cmd_verify_wheel)

    registry_parser = sub.add_parser("verify-registry")
    registry_parser.add_argument("--index", required=True)
    registry_parser.add_argument("--pin", required=True)
    registry_parser.set_defaults(func=cmd_verify_registry)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
