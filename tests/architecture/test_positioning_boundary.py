"""Position evidence is shared mechanics; products own its consequences.

ADR-0039 deliberately cuts a narrow positioning unit out of the broader
``field-workforce`` and ``assets-fleet`` families.  These canaries keep the
measurement classifier, accepted decision and product-first inventory aligned.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR = (
    PROJECT_ROOT
    / "docs/adr/0039-positioning-owns-location-evidence-not-business-consequences.md"
)
INVENTORY = PROJECT_ROOT / "docs/inventories/positioning-sources.md"
DOSSIER = PROJECT_ROOT / "packages/dotmac-positioning/EXTRACTION.toml"
INVENTORY_INDEX = PROJECT_ROOT / "docs/inventories/README.md"
MATRIX = PROJECT_ROOT / "docs/inventories/fleet-decomposition-matrix.md"
ARCHITECTURE = PROJECT_ROOT / "docs/ARCHITECTURE.md"
SWEEP = PROJECT_ROOT / "scripts/fleet_decomposition_sweep.py"


def _sweep_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_decomposition_sweep", SWEEP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positioning_classifier_separates_evidence_from_business_domains() -> None:
    classify = _sweep_module().classify

    assert classify("field_tech_location_pings") == "positioning"
    assert classify("field_tech_presence") == "positioning"
    assert classify("crm_agent_location_pings") == "positioning"

    # Sensitivity proof: nearby map-, GIS- and fleet-named state is not
    # positioning merely because it can be rendered or carries coordinates.
    assert classify("field_map_asset_location_provenance") == "field-workforce"
    assert classify("geo_locations") == "geospatial-qualification"
    assert classify("vehicle") == "assets-fleet"


def test_positioning_decision_and_inventory_are_authoritative_and_indexed() -> None:
    assert ADR.is_file()
    assert INVENTORY.is_file()
    assert "positioning-sources.md" in INVENTORY_INDEX.read_text(encoding="utf-8")

    adr = " ".join(ADR.read_text(encoding="utf-8").lower().split())
    assert "**status:** accepted" in adr
    assert "dotmac-positioning" in adr
    assert "position observations" in adr
    assert "business consequences" in adr
    assert "product-owned" in adr

    inventory = " ".join(INVENTORY.read_text(encoding="utf-8").lower().split())
    for evidence in (
        "dotmac_sub",
        "dotmac_crm",
        "dotmac_erp",
        "qualifying source",
        "first cutover",
        "shadow",
        "retention",
        "client_observation_id",
    ):
        assert evidence in inventory


def test_positioning_is_a_named_module_destination_and_resource_owner() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    row = re.search(
        r"^\|\s*positioning\s*\|(?:\s*\d+\s*\|){7}([^|]*)\|\s*$",
        matrix,
        re.MULTILINE,
    )
    assert row is not None
    assert "`dotmac-positioning`" in row.group(1)

    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    assert (
        "| Position observations and projections | `dotmac-positioning`" in architecture
    )
    assert "products own consequences" in architecture.lower()


def test_positioning_dossier_is_product_first_and_claims_no_adoption() -> None:
    dossier = tomllib.loads(DOSSIER.read_text(encoding="utf-8"))

    assert dossier["package"] == "dotmac-positioning"
    assert dossier["classification"] == "optional-module"
    assert dossier["source_mode"] == "product-first"
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_sub", "dotmac_erp"]
    assert {"dotmac_sub", "dotmac_crm", "dotmac_erp"}.issubset(
        dossier["source_repositories"]
    )
    assert "docs/inventories/positioning-sources.md" in dossier["inventory_evidence"]

    boundary = dossier["authority_boundary"].lower()
    for excluded_owner in (
        "vehicle lifecycle",
        "work-order lifecycle",
        "attendance consequences",
        "provider transport",
        "map presentation",
    ):
        assert excluded_owner in boundary
