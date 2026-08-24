"""Paystack is a stateless provider edge in both directions, never a money owner.

The outbound slice makes these checks matter more, not less. An ingress
connector that oversteps makes a fact arrive wrong; an outbound one that
oversteps moves somebody's money — so the same boundary is asserted against
the whole package, commands included.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_paystack
from dotmac_connector_paystack import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-paystack"
SOURCE = PACKAGE / "src" / "dotmac_connector_paystack"


def _imports() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_connector_version_is_one_fact_on_every_public_surface() -> None:
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    version = declared["tool"]["poetry"]["version"]
    assert version == dotmac_connector_paystack.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert "dotmac_connector_paystack" in internal
    assert internal - {"dotmac_connector_paystack"} == {"dotmac_integration"}


def test_network_is_provider_io_and_no_persistence_or_private_retry_exists() -> None:
    forbidden = {
        "alembic",
        "apscheduler",
        "asyncpg",
        "backoff",
        "celery",
        "psycopg",
        "requests",
        "sqlalchemy",
        "tenacity",
        "urllib3",
    }
    assert "httpx" in _imports()
    assert not (_imports() & forbidden)


def test_connector_has_no_sibling_provider_or_product_decision_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "flutterwave",
        "invoice_id",
        "billing_account_id",
        "subscription_id",
        "tenant_id",
        "net_amount",
        "paymentstatus",
        "balance_due",
    ):
        assert forbidden not in source
    assert "float(" not in source


def test_connector_package_is_not_composed_into_the_starter_runtime() -> None:
    assembly = (PROJECT_ROOT / "app" / "assembly.py").read_text(encoding="utf-8")
    root = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = root["tool"]["poetry"]["dependencies"]
    dev_dependencies = root["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "dotmac_connector_paystack" not in assembly
    assert "dotmac-connector-paystack" not in runtime_dependencies
    assert "dotmac-connector-paystack" in dev_dependencies


def test_the_command_side_owns_no_retry_no_sleep_and_no_clock() -> None:
    """A connector attempts once. Whether there is a second attempt is the
    engine's decision, and an outbound money command is exactly the place a
    private retry loop would turn one charge into several."""
    forbidden_modules = {"asyncio", "sched", "signal", "threading", "time"}
    assert not (_imports() & forbidden_modules)
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        loops = [n for n in ast.walk(tree) if isinstance(n, ast.While)]
        assert loops == [], f"{path.name} contains a loop that could re-send"


def test_no_provider_text_is_ever_written_to_the_persisted_error_detail() -> None:
    """`error_detail` is stored by the engine, and the only text a connector
    has at that point came back from a call made with materialized
    credentials. The machine `error_code` is what may cross."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    )
    assert "error_detail=" not in source


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Identity of every docstring constant, so prose can be excluded exactly.

    By identity rather than by value: `ast.get_docstring` cleans indentation,
    so comparing text would silently stop excluding anything and turn the
    guard below into a check that nothing is ever said about the boundary.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            continue
        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            ids.add(id(first))
    return ids


def test_the_outbound_slice_names_no_product_money_decision() -> None:
    """The forbidden vocabulary, checked over CODE rather than prose.

    A docstring may — and does — say which decisions stay with the product.
    An identifier or a live literal that named one would mean the connector
    had TAKEN it. Docstrings are excluded by node identity so the guard cannot
    be satisfied by saying nothing about the boundary.
    """
    forbidden = (
        "invoice",
        "receivable",
        "dunning",
        "creditnote",
        "writeoff",
        "settlement_policy",
    )
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose = _docstring_node_ids(tree)
        spoken: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                spoken.append(node.id)
            elif isinstance(node, ast.Attribute):
                spoken.append(node.attr)
            elif isinstance(node, ast.arg):
                spoken.append(node.arg)
            elif isinstance(node, ast.FunctionDef | ast.ClassDef):
                spoken.append(node.name)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in prose
            ):
                spoken.append(node.value)
        for word in spoken:
            assert not any(
                term in word.casefold() for term in forbidden
            ), f"{path.name} names a product money decision: {word!r}"


def test_the_product_money_vocabulary_guard_still_bites() -> None:
    """SENSITIVITY PROOF. The guard above passes over a clean package, which is
    exactly what it would do if the exclusion had swallowed everything."""
    tree = ast.parse('"""A docstring may mention an invoice."""\nx = "invoice_id"\n')
    prose = _docstring_node_ids(tree)
    live = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]
    assert live == ["invoice_id"]


def test_product_first_evidence_is_immutable() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
