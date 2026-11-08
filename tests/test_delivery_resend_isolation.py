"""Isolation proofs for ``dotmac-delivery-resend``.

The behavior suites live with the first adopter (Academy) because they
need its genesis database and product models; see EXTRACTION.toml
`preserved_tests`. These checks prove the package boundary only:
stateless manifest, exact pins, and no product imports in the fence.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "dotmac-delivery-resend"


def test_manifest_is_stateless() -> None:
    from dotmac_delivery_resend import module

    assert module.code == "delivery_resend"
    assert module.version == "0.1.0a1"
    assert module.core is False
    assert not getattr(module, "short_code", None)
    assert not getattr(module, "tables", None)


def test_fence_imports_no_product_modules() -> None:
    tree = ast.parse((PACKAGE / "src/dotmac_delivery_resend/fence.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
    assert "app" not in imported
    assert imported <= {
        "__future__",
        "collections",
        "copy",
        "dataclasses",
        "dotmac_kernel",
        "re",
        "sqlalchemy",
        "sys",
        "threading",
        "typing",
        "uuid",
        "weakref",
    }


def test_exact_sqlalchemy_pin_is_the_contract() -> None:
    pyproject = (PACKAGE / "pyproject.toml").read_text()
    assert 'sqlalchemy = "==2.0.51"' in pyproject
    assert 'dotmac-kernel = ">=0.1.0a107"' in pyproject
    fence = (PACKAGE / "src/dotmac_delivery_resend/fence.py").read_text()
    assert 'sqlalchemy_version != "2.0.51"' in fence
