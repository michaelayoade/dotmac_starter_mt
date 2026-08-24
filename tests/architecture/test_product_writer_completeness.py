"""Every inventoried product has one typed, exact-pinned writer claim.

The transitional prose-only baseline reached zero on 2026-08-23 and was
deleted. These tests are absolute enforcement: a dossier cannot reintroduce an
UNKNOWN writer disposition, and a row cannot drift from the source revision the
dossier says it audited.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "product_writer_check.py"


def _check():
    spec = importlib.util.spec_from_file_location("product_writer_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _complete_dossier() -> dict:
    return {
        "source_repositories": ["dotmac_sub", "dotmac_starter_mt"],
        "source_revisions": ["dotmac_sub:883a0ff1aff89e3ea5e241897a4b965527e9bce1"],
        "product_writers": [
            {
                "product": "dotmac_sub",
                "writer_state": "inventory_only",
                "retirement_required": False,
                "revision": "883a0ff1aff89e3ea5e241897a4b965527e9bce1",
                "evidence_paths": ["docs/SOT_RELATIONSHIP_MAP.md"],
            }
        ],
    }


def test_every_checked_in_dossier_is_complete_and_exact_pinned() -> None:
    check = _check()
    assert check.repository_problems() == []


def test_missing_product_writer_row_is_refused() -> None:
    """SENSITIVITY: deleting the typed row must make the guard red."""
    check = _check()
    dossier = _complete_dossier()
    dossier["product_writers"] = []
    problems = check.dossier_problems("dotmac-example", dossier)
    assert any("no typed [[product_writers]] row" in item for item in problems)


def test_newly_declared_product_without_a_row_is_refused() -> None:
    """SENSITIVITY: new dossiers cannot reopen the prose channel."""
    check = _check()
    dossier = _complete_dossier()
    dossier["source_repositories"].append("dotmac_new_product")
    problems = check.dossier_problems("dotmac-example", dossier)
    assert any("dotmac_new_product" in item for item in problems)


def test_writer_revision_must_equal_the_source_revision_pin() -> None:
    """SENSITIVITY: a true claim at one tree is not a claim about another."""
    check = _check()
    dossier = _complete_dossier()
    dossier["source_revisions"][0] = (
        "dotmac_sub:1111111111111111111111111111111111111111"
    )
    problems = check.dossier_problems("dotmac-example", dossier)
    assert any("must equal" in item for item in problems)


def test_revalidated_writer_must_equal_the_revalidation_pin() -> None:
    """A later exact audit supersedes the original pin without erasing it."""
    check = _check()
    dossier = _complete_dossier()
    dossier["revalidation_revisions"] = [
        "dotmac_sub:3333333333333333333333333333333333333333"
    ]
    problems = check.dossier_problems("dotmac-example", dossier)
    assert any("effective audit pin" in item for item in problems)

    dossier["product_writers"][0]["revision"] = (
        "3333333333333333333333333333333333333333"
    )
    assert check.dossier_problems("dotmac-example", dossier) == []


def test_an_extra_typed_product_is_refused() -> None:
    """SENSITIVITY: completeness means exact set equality, not only coverage."""
    check = _check()
    dossier = _complete_dossier()
    dossier["source_revisions"].append(
        "dotmac_crm:2222222222222222222222222222222222222222"
    )
    dossier["product_writers"].append(
        {
            "product": "dotmac_crm",
            "writer_state": "inventory_only",
            "retirement_required": False,
            "revision": "2222222222222222222222222222222222222222",
            "evidence_paths": ["README.md"],
        }
    )
    problems = check.dossier_problems("dotmac-example", dossier)
    assert any("not a declared external source product" in item for item in problems)


def test_a_complete_matching_dossier_has_no_findings() -> None:
    """SPECIFICITY: the guard does not object to every dossier."""
    check = _check()
    assert check.dossier_problems("dotmac-example", _complete_dossier()) == []
