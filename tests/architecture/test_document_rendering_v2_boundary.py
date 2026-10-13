"""Sensitivity proof that V2 retains the existing stateless owner boundary."""

import ast
import tomllib

import pytest

from tests.architecture.test_document_rendering_module import (
    PACKAGE_ROOT,
    SOURCE_ROOT,
    _forbidden_import_findings,
    _money_findings,
    _purity_findings,
)


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy\n",
        "from dotmac_kernel.db import get_db\n",
        "from dotmac_billing import get_invoice_document_fact\n",
        "from dotmac_numbering import allocate_number\n",
        "from dotmac_brand_profiles import lookup_brand\n",
        "from dotmac_customers import get_customer\n",
        "from dotmac_tax import calculate_tax\n",
        "from dotmac_payments import get_payment\n",
    ],
)
def test_v2_persistence_and_sibling_import_detector_sensitivity(source):
    assert _forbidden_import_findings(source, label="planted_v2.py")


@pytest.mark.parametrize(
    "source",
    [
        "import requests\n",
        "from urllib.request import urlopen\n",
        "import subprocess\n",
        "from datetime import datetime\nx = datetime.now()\n",
        "x = open('invoice.json').read()\n",
        "x = urlopen(payment_link)\n",
    ],
)
def test_v2_clock_network_and_external_io_detector_sensitivity(source):
    assert _purity_findings(source, label="planted_v2.py")


@pytest.mark.parametrize(
    "source",
    [
        "grand_total = fact.subtotal + fact.tax_total\n",
        "new_amount = line.quantity * line.unit_amount\n",
        "remaining = line.total_amount - line.discount_total\n",
        "x = float(line.unit_amount)\n",
        "x = sum(line.total_amount for line in lines)\n",
    ],
)
def test_v2_financial_arithmetic_detector_sensitivity(source):
    assert _money_findings(source, label="planted_v2.py")


def test_v2_sources_use_shared_kernel_domains_and_keep_v1_separate():
    projection = ast.parse((SOURCE_ROOT / "projection_v2.py").read_text())
    imports = {
        node.module for node in ast.walk(projection) if isinstance(node, ast.ImportFrom)
    }
    assert "dotmac_kernel.semantic_encoding" in imports
    assert "dotmac_kernel.idempotency" not in imports
    source = (SOURCE_ROOT / "projection_v2.py").read_text()
    assert '"dotmac-document-rendering.fact.v2"' in source
    assert '"dotmac-document-rendering.projection.v2"' in source
    assert "hashlib" not in source
    # The original golden remains the full V1 semantic contract proof.
    assert (
        PACKAGE_ROOT.parents[1]
        / "tests/fixtures/document_rendering/invoice_projection_v1.json"
    ).is_file()


def test_v2_declares_the_published_shared_encoding_kernel_floor():
    project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    assert project["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a103"
    from dataclasses import fields

    from dotmac_document_rendering import (
        DocumentProjectionV2,
        RenderedDocumentV2,
        module,
    )

    assert module.db_schema is None
    assert module.tables == ()
    assert module.platform_tables == ()
    forbidden = {
        "subtotal",
        "tax_total",
        "grand_total",
        "amount_paid",
        "balance_due",
        "payment_status",
    }
    assert forbidden.isdisjoint(
        item.name
        for cls in (DocumentProjectionV2, RenderedDocumentV2)
        for item in fields(cls)
    )
