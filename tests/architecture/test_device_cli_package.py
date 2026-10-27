"""Architecture boundary for the stateless dotmac-device-cli package (ADR-0077).

The package is device KNOWLEDGE and DIALOGUE LOGIC. The channel is injected by
the host, so the package imports nothing that could open a connection, hold a
credential, touch a database or read the clock/environment. These guards pin
that with the AST (a docstring explaining the absence must not trip a rule), and
each carries a sensitivity proof that it fires on the planted defect.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_device_cli

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-device-cli"
SOURCE = PACKAGE / "src" / "dotmac_device_cli"

# Closed list: a new import root is a reviewed change to the "no I/O" claim.
ALLOWED_STDLIB = frozenset(
    {
        "__future__", "collections", "contextlib", "copy", "dataclasses", "enum",
        "functools", "hashlib", "importlib", "json", "pathlib", "random", "re",
        "string", "threading", "time", "tomllib", "typing",
    }
)  # fmt: skip
FORBIDDEN_ROOTS = frozenset(
    {
        "socket", "ssl", "paramiko", "asyncssh", "subprocess", "sqlalchemy",
        "psycopg", "psycopg2", "redis", "requests", "httpx", "urllib", "os",
        "dotmac_kernel", "dotmac_ui", "app",
    }
)  # fmt: skip
FORBIDDEN_CALLS = frozenset(
    {
        "open",
        "getenv",
        "environ",
        "urlopen",
        "system",
        "popen",
        "__import__",
        "import_module",
    }
)
# `pathlib` and `importlib` are allowed only where the module needs them to read
# shipped profile data (importlib.resources) or to be a test seam (Path types).
ALLOWED_PATHLIB = frozenset({"conformance.py"})


def _findings(source: str, *, label: str, allow_pathlib: bool = False) -> list[str]:
    out: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        for name in names:
            root = name.split(".", 1)[0]
            if root == "dotmac_device_cli":
                continue
            if root in FORBIDDEN_ROOTS or root not in ALLOWED_STDLIB:
                out.append(f"{label}:{node.lineno}: imports {name}")
            elif root == "pathlib" and not allow_pathlib:
                out.append(f"{label}:{node.lineno}: imports {name}")
        if isinstance(node, ast.Call):
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if called in FORBIDDEN_CALLS:
                out.append(f"{label}:{node.lineno}: calls {called}()")
        if isinstance(node, ast.Name) and node.id == "ModuleManifest":
            out.append(f"{label}:{node.lineno}: references ModuleManifest")
    return out


def _modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def test_the_package_performs_no_io_and_imports_no_transport_or_persistence() -> None:
    findings: list[str] = []
    for path in _modules():
        findings += _findings(
            path.read_text(encoding="utf-8"),
            label=str(path.relative_to(PACKAGE)),
            allow_pathlib=path.name in ALLOWED_PATHLIB,
        )
    assert findings == []


def test_the_scan_covers_real_modules() -> None:
    names = {p.name for p in _modules()}
    assert {"session.py", "limiter.py", "huawei.py", "zte.py", "layers.py"} <= names


def test_the_guard_fires_on_planted_defects() -> None:
    for planted in (
        "import socket\n",
        "import paramiko\n",
        "from sqlalchemy import text\n",
        "import subprocess\n",
        "import os\n",
        "from dotmac_kernel.modules import ModuleManifest\n",
        "import httpx\n",
        "x = open('f')\n",
        "import importlib\nimportlib.import_module('socket')\n",
        "import pathlib\n",
    ):
        assert _findings(planted, label="planted.py"), planted


def test_the_guard_is_silent_on_prose_that_explains_the_absence() -> None:
    prose = '"""No socket, no paramiko, no ModuleManifest, no open()."""\nimport re\n'
    assert _findings(prose, label="prose.py") == []


def test_the_package_is_stateless_and_declares_no_lineage() -> None:
    assert not (SOURCE / "manifest.py").exists()
    assert not list(PACKAGE.rglob("migrations"))
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text())
    assert dossier["classification"] == "stateless-protocol-adapter"
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text())
    deps = pyproject["tool"]["poetry"]["dependencies"]
    assert set(deps) == {
        "python"
    }, "standard library only: no kernel floor, no runtime deps"


def test_the_package_carries_no_estate_literals() -> None:
    import re

    ipv4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
    doc_nets = ("192.0.2.", "198.51.100.", "203.0.113.")
    for path in [
        *PACKAGE.rglob("*.py"),
        *PACKAGE.rglob("*.toml"),
        *PACKAGE.rglob("*.md"),
    ]:
        for m in ipv4.finditer(path.read_text(encoding="utf-8")):
            assert m.group(0).startswith(doc_nets) or m.group(0) in {"0.1.0.1"}, (
                path,
                m.group(0),
            )


def test_the_version_matches_the_distribution() -> None:
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["tool"][
        "poetry"
    ]["version"]
    assert dotmac_device_cli.__version__ == declared, (
        f"__version__ is {dotmac_device_cli.__version__!r} but pyproject declares "
        f"{declared!r} — bump BOTH. (This is the drift that went unnoticed in "
        "four other packages because only the kernel had this test.)"
    )


def test_the_manifest_modules_import_and_declare_all() -> None:
    import importlib

    assert not (
        dotmac_device_cli.SUPPORTED_MODULES & dotmac_device_cli.INTERNAL_MODULES
    )
    for name in dotmac_device_cli.SUPPORTED_MODULES:
        module = importlib.import_module(name)
        assert getattr(module, "__all__", None), f"{name} declares no __all__"


def test_every_advertised_top_level_name_resolves() -> None:
    # The source-tree half of `scripts/release_adapter.py`'s installed-wheel
    # smoke, which asserts the same against the built artifact.
    missing = [
        name
        for name in dotmac_device_cli.__all__
        if not hasattr(dotmac_device_cli, name)
    ]
    assert not missing, f"__all__ names absent from the package: {missing}"
    assert "SUPPORTED_MODULES" in dotmac_device_cli.__all__
    assert "INTERNAL_MODULES" in dotmac_device_cli.__all__
