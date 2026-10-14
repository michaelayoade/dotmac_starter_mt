"""Architecture boundary for the stateless dotmac-config-layers engine.

The engine is pure composition. These guards pin that: it imports only the
standard library, pydantic and itself (the manifest alone touches the kernel),
reaches no network, clock, environment, filesystem, process or persistence, and
names no provider. ADR-0074.
"""

from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-config-layers"
SOURCE_ROOT = PACKAGE_ROOT / "src" / "dotmac_config_layers"
PORT_MAP = PACKAGE_ROOT / "PORT_MAP.json"

# Standard-library modules the engine is allowed to use. A closed list: a new
# import root is a reviewed change to the engine's purity claim.
ALLOWED_STDLIB = frozenset(
    {
        "__future__",
        "collections",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "ipaddress",
        "json",
        "re",
        "types",
        "typing",
        "urllib",
    }
)
ALLOWED_THIRD_PARTY = frozenset({"pydantic"})
# `urllib.parse` is string quoting only; nothing in urllib may open a connection.
FORBIDDEN_URLLIB = frozenset({"urllib.request", "urllib.error", "urllib.robotparser"})
FORBIDDEN_CALLS = frozenset(
    {"now", "today", "utcnow", "getenv", "environ", "open", "urlopen", "uuid4"}
)
PROVIDER_WORDS = ("routeros", "mikrotik", "snmp", "ntp ")


def _modules() -> list[Path]:
    return sorted(SOURCE_ROOT.glob("*.py"))


def _import_findings(source: str, *, label: str, kernel_allowed: bool) -> list[str]:
    findings: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import of this package
                continue
            if node.module:
                names = [node.module]
        for name in names:
            root = name.split(".", 1)[0]
            if name in FORBIDDEN_URLLIB:
                findings.append(f"{label}:{node.lineno}: {name}")
            elif root == "dotmac_config_layers" or root in ALLOWED_THIRD_PARTY:
                continue
            elif root == "dotmac_kernel":
                if not kernel_allowed:
                    findings.append(f"{label}:{node.lineno}: {name}")
            elif root not in ALLOWED_STDLIB:
                findings.append(f"{label}:{node.lineno}: {name}")
    return findings


def _call_findings(source: str, *, label: str) -> list[str]:
    findings: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else "")
            )
            if name in FORBIDDEN_CALLS:
                findings.append(f"{label}:{node.lineno}: {name}()")
    return findings


def test_distribution_is_a_stateless_optional_module_with_no_namespace() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    from dotmac_config_layers import module

    assert dossier["package"] == "dotmac-config-layers"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert dossier["contract_consumers"] == []
    assert pyproject["tool"]["poetry"]["name"] == dossier["package"]
    assert module.code == "config_layers"
    assert module.version == pyproject["tool"]["poetry"]["version"]
    assert module.short_code is None
    assert module.migration_prefix is None
    assert module.db_schema is None
    assert module.tables == ()
    assert module.platform_tables == ()
    assert not (SOURCE_ROOT / "models.py").exists()
    assert not (SOURCE_ROOT / "migrations").exists()
    assert (SOURCE_ROOT / "py.typed").is_file()


def test_engine_imports_only_the_standard_library_pydantic_and_itself() -> None:
    findings: list[str] = []
    for path in _modules():
        findings.extend(
            _import_findings(
                path.read_text(encoding="utf-8"),
                label=path.name,
                kernel_allowed=path.name == "manifest.py",
            )
        )
    assert not findings, "engine import boundary violated:\n" + "\n".join(findings)
    # Sensitivity: each detector fires on a planted violation.
    assert _import_findings("import sqlalchemy\n", label="p.py", kernel_allowed=False)
    assert _import_findings("import httpx\n", label="p.py", kernel_allowed=False)
    assert _import_findings(
        "from urllib.request import urlopen\n", label="p.py", kernel_allowed=False
    )
    assert _import_findings(
        "from dotmac_kernel.modules import ModuleManifest\n",
        label="p.py",
        kernel_allowed=False,
    )
    assert not _import_findings(
        "from dotmac_kernel.modules import ModuleManifest\n",
        label="manifest.py",
        kernel_allowed=True,
    )


def test_engine_has_no_clock_environment_filesystem_or_network_call() -> None:
    findings: list[str] = []
    for path in _modules():
        findings.extend(
            _call_findings(path.read_text(encoding="utf-8"), label=path.name)
        )
    assert not findings, "engine purity violations:\n" + "\n".join(findings)
    assert _call_findings(
        "from datetime import date\nvalue = date.today()\n", label="planted_clock.py"
    ), "sensitivity: the clock detector no longer bites"
    assert _call_findings("import os\nos.getenv('X')\n", label="planted_env.py")


def test_engine_source_names_no_provider() -> None:
    findings = [
        f"{path.name}: {word!r}"
        for path in _modules()
        for word in PROVIDER_WORDS
        if word in path.read_text(encoding="utf-8").lower()
    ]
    assert not findings, "a provider belongs in a plug-in package:\n" + "\n".join(
        findings
    )


def test_public_surface_is_declared_and_resolvable() -> None:
    import dotmac_config_layers as package

    declared = list(package.__all__)
    assert declared == sorted(declared)
    assert len(declared) == len(set(declared))
    missing = [name for name in declared if not hasattr(package, name)]
    assert not missing, f"__all__ names that do not resolve: {missing}"
    for required in (
        "compose",
        "evaluate_module",
        "widening",
        "DomainPlugin",
        "Effective",
        "ModuleBase",
        "ExceptionSpec",
    ):
        assert required in declared


def test_package_is_strict_typed_and_in_the_independence_contract() -> None:
    root_pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        'dotmac-config-layers = { path = "packages/dotmac-config-layers", '
        "develop = true }" in root_pyproject
    )
    parsed = tomllib.loads(root_pyproject)
    strict = {
        module
        for override in parsed["tool"]["mypy"]["overrides"]
        if override.get("disallow_untyped_defs") is True
        for module in override["module"]
    }
    assert "dotmac_config_layers.*" in strict
    contracts = parsed["tool"]["importlinter"]["contracts"]
    independent = next(
        contract
        for contract in contracts
        if contract["name"] == "Modules are independent of each other"
    )
    assert "dotmac_config_layers" in independent["modules"]
    forbidden = next(
        contract
        for contract in contracts
        if contract["name"] == "Modules must not import the assembly"
    )
    assert "dotmac_config_layers" in forbidden["source_modules"]


def test_port_map_records_every_ported_file_and_matches_the_tree() -> None:
    """The reviewed port: each destination blob is what the port map records.

    The map is the reviewer's index of what changed from the Sub source; a file
    edited without regenerating the map, or a ported file missing from it, fails.
    """
    document = json.loads(PORT_MAP.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert len(document["source_revision"]) == 40
    recorded = {entry["destination"] for entry in document["files"]}
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in [
            *(p for p in _modules() if p.name != "manifest.py"),
            *sorted((ROOT / "tests" / "unit" / "config_layers").glob("*.py")),
        ]
        if path.name != "__init__.py" or path.parent == SOURCE_ROOT
    }
    assert recorded == expected
    for entry in document["files"]:
        destination = ROOT / entry["destination"]
        data = destination.read_bytes()
        blob = hashlib.sha1(  # noqa: S324 - git object identity, not security
            b"blob %d\0" % len(data) + data
        ).hexdigest()
        assert (
            blob == entry["destination_blob"]
        ), f"{entry['destination']} changed without regenerating PORT_MAP.json"
        assert entry["allowed_deltas"], f"{entry['destination']} records no delta"
        assert entry["source"].startswith("dotmac_sub:")
