"""Guard the product-first sales evidence and accepted P11 transition."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORIES = PROJECT_ROOT / "docs" / "inventories"
ADR = (
    PROJECT_ROOT / "docs" / "adr" / "0033-sales-authority-stops-at-an-accepted-quote.md"
)
PINS = {
    "starter": "7828697ef11fb1ae765a5397dfa7dc221ae6207a",
    "sub": "f64946fc451ba94a1d4c8f0a61b7831367d5b598",
    "crm": "57e112f0757edcee6b9ad625ee3e13ebff5c7d71",
    "erp": "2749ec5396cbbd7a1132b394e85855a1d133a7cd",
}

EVIDENCE_FILES = (
    "sales-sources.md",
    "sales-caller-inventory.md",
    "sales-parity-and-canaries.md",
    "sales-extraction-dossier.md",
    "sales-retirement-ledger.md",
)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _boundary_errors(text: str) -> tuple[str, ...]:
    normalized = _normalized(text)
    required = {
        "accepted boundary": "Sales authority stops at an accepted quote",
        "orders excluded": (
            "`dotmac-sales` does not own `SalesOrder` or `SalesOrderLine` rows"
        ),
        "orders import excluded": "does not import `dotmac-orders`",
        "versioned handoff": "`AcceptedQuoteHandoffV1`",
        "sub current authority": "The approved Sub SOT is the current authority",
        "crm retirement only": "CRM is a retirement source",
        "campaign exclusion": "Campaign ownership remains unverified",
        "retention exclusion": (
            "retention guidance conflicts and requires a separate explicit "
            "owner decision"
        ),
        "p11 not cleared": "This ADR does not clear, reinterpret or modify that gate",
    }
    return tuple(name for name, phrase in required.items() if phrase not in normalized)


def test_sales_extraction_evidence_is_complete_and_pinned() -> None:
    assert ADR.is_file()
    documents = {
        name: (INVENTORIES / name).read_text(encoding="utf-8")
        for name in EVIDENCE_FILES
    }

    source_evidence = documents["sales-sources.md"]
    dossier = documents["sales-extraction-dossier.md"]
    for revision in PINS.values():
        assert revision in source_evidence
        assert revision in dossier

    assert "product-first from Sub with mandatory port deltas" in source_evidence
    assert "CRM supplies parity and retirement evidence only" in source_evidence
    assert "P11 is now met" in dossier


def test_sales_boundary_is_explicit_and_product_neutral() -> None:
    text = ADR.read_text(encoding="utf-8")

    assert _boundary_errors(text) == ()


def test_sales_boundary_guard_is_sensitive_to_each_required_claim() -> None:
    text = ADR.read_text(encoding="utf-8")
    normalized = _normalized(text)
    required_phrases = (
        "Sales authority stops at an accepted quote",
        "`dotmac-sales` does not own `SalesOrder` or `SalesOrderLine` rows",
        "does not import `dotmac-orders`",
        "`AcceptedQuoteHandoffV1`",
        "The approved Sub SOT is the current authority",
        "CRM is a retirement source",
        "Campaign ownership remains unverified",
        "retention guidance conflicts and requires a separate explicit owner decision",
        "This ADR does not clear, reinterpret or modify that gate",
    )

    for phrase in required_phrases:
        violated = normalized.replace(phrase, "removed by sensitivity canary", 1)
        assert _boundary_errors(violated), phrase


def test_all_sales_canaries_are_declared_before_implementation() -> None:
    text = (INVENTORIES / "sales-parity-and-canaries.md").read_text(encoding="utf-8")

    for index in range(1, 12):
        assert f"C-SALES-{index:02d}" in text

    assert "raw SQL `UPDATE`/`DELETE`" in text
    assert "two-directional baseline" in text
    assert "sensitivity test" in text


def test_canonical_p11_authorizes_sales_implementation_only() -> None:
    p11 = (INVENTORIES / "p11-adoption-status.md").read_text(encoding="utf-8")
    dossier = (INVENTORIES / "sales-extraction-dossier.md").read_text(encoding="utf-8")

    assert "**Status:** **P11 is MET.**" in p11
    assert "package and lineage implementation may now begin" in _normalized(p11)
    assert "P11 product production lineage | **MET**" in dossier
    assert (
        "does not advance any module-specific release, adoption, cutover or "
        "retirement gate" in _normalized(dossier)
    )
