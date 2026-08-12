"""The fleet recomposition thesis must remain explicit and discoverable.

This is deliberately a documentation contract, not proof of implementation.
It prevents onboarding text from drifting back to "clone a starter" while the
accepted end state is ERP/CRM/Sub decomposition into a shared platform and
domain-module ecosystem.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README = PROJECT_ROOT / "README.md"
VISION = PROJECT_ROOT / "docs" / "PRODUCT_VISION.md"

VISION_CONTRACT = {
    "source monoliths": (
        "source monoliths",
        "dotmac_erp",
        "dotmac_crm",
        "dotmac_sub",
    ),
    "target layers": (
        "dotmac-kernel",
        "dotmac-ui",
        "domain modules",
        "thin product assemblies",
    ),
    "build-once boundary": (
        "build once",
        "one named authoritative writer",
        "remote capability binding",
        "shared production database",
    ),
    "adoption exit": (
        "source monolith consume the package",
        "retired",
        "tested dependency update",
        "no bespoke port",
    ),
}


def _missing_vision_contract(source: str) -> dict[str, tuple[str, ...]]:
    normalized = " ".join(source.lower().split())
    return {
        rule: tuple(term for term in terms if term not in normalized)
        for rule, terms in VISION_CONTRACT.items()
        if any(term not in normalized for term in terms)
    }


def test_product_vision_is_linked_from_onboarding() -> None:
    assert VISION.is_file()
    assert "(docs/PRODUCT_VISION.md)" in README.read_text()


def test_product_vision_preserves_the_recomposition_contract() -> None:
    assert _missing_vision_contract(VISION.read_text()) == {}


def test_product_vision_contract_detector_is_sensitive() -> None:
    incomplete = """
    Source monoliths dotmac_erp, dotmac_crm, and dotmac_sub build once using
    dotmac-kernel, dotmac-ui, domain modules, and thin product assemblies.
    """

    missing = _missing_vision_contract(incomplete)

    assert set(missing) == {"build-once boundary", "adoption exit"}
