"""The marketing suite must preserve its product-first source rulings.

The source audit deliberately decomposes the suite before any package exists.
This guard prevents a future implementation from quietly turning the seven
owners back into a marketing monolith, laundering provider transport into a
domain module, or claiming a product-first source for the greenfield Sites or
first-party Web Analytics owners.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = PROJECT_ROOT / "docs" / "inventories" / "marketing-suite-sources.md"
CONTENT_DOSSIER = PROJECT_ROOT / "packages" / "dotmac-content" / "EXTRACTION.toml"
CONTENT_RETIREMENT = (
    PROJECT_ROOT / "docs" / "inventories" / "content-writer-retirement.toml"
)
PUBLISHING_DOSSIER = PROJECT_ROOT / "packages" / "dotmac-publishing" / "EXTRACTION.toml"
PUBLISHING_RETIREMENT = (
    PROJECT_ROOT / "docs" / "inventories" / "publishing-writer-retirement.toml"
)

EXPECTED_SOURCES = {
    "dotmac-content": ("product-first", "dotmac_mkt"),
    "dotmac-sites": ("greenfield-after-inventory", "none"),
    "dotmac-publishing": ("product-first", "dotmac_mkt"),
    "dotmac-media-observations": ("product-first", "dotmac_mkt"),
    "dotmac-web-analytics": ("greenfield-after-inventory", "none"),
    "dotmac-forms": ("product-first", "dotmac_erp"),
    "dotmac-campaigns": ("product-first", "dotmac_sub"),
}

SOURCE_REVISIONS = {
    "dotmac_starter_mt": "c6ef6cd7b13105bd95c3faf354ffee9032077625",
    "dotmac_mkt": "7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d",
    "dotmac_sub": "510b80ca7fab4f54a57f261872f94b5e972c8eb6",
    "dotmac_erp": "dd6416cd981ffdf48564e2770b87d3cd7201186c",
    "dotmac_crm": "60daaa2dd305696636632f48505ab784110a55d2",
    "dotmac_backoffice": "fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d",
}

_SOURCE_ROW = re.compile(
    r"^\| `(?P<module>dotmac-[a-z-]+)` "
    r"\| `(?P<mode>product-first|greenfield-after-inventory)` "
    r"\| `(?P<source>dotmac_[a-z_]+|none)` \|",
    re.MULTILINE,
)


def _source_rows(source: str) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for match in _SOURCE_ROW.finditer(source):
        module = match.group("module")
        if module in rows:
            raise ValueError(f"duplicate marketing source row: {module}")
        rows[module] = (match.group("mode"), match.group("source"))
    return rows


def _normalized() -> str:
    return " ".join(INVENTORY.read_text().lower().split())


def test_source_audit_exists_and_is_indexed() -> None:
    assert INVENTORY.is_file()
    assert (
        "marketing-suite-sources.md"
        in (PROJECT_ROOT / "docs" / "inventories" / "README.md").read_text()
    )
    assert (
        "marketing-suite-sources.md"
        in (
            PROJECT_ROOT / "docs" / "inventories" / "module-extraction-sources.md"
        ).read_text()
    )


def test_every_marketing_module_has_one_exact_source_ruling() -> None:
    assert _source_rows(INVENTORY.read_text()) == EXPECTED_SOURCES


def test_source_row_detector_rejects_duplicate_authority() -> None:
    """Sensitivity proof: two source rows cannot masquerade as one ruling."""
    row = "| `dotmac-content` | `product-first` | `dotmac_mkt` | evidence |\n"
    with pytest.raises(ValueError, match="duplicate marketing source row"):
        _source_rows(row + row)


def test_source_revisions_are_full_and_pinned() -> None:
    source = INVENTORY.read_text()
    for repository, revision in SOURCE_REVISIONS.items():
        assert re.search(
            rf"^\| `{repository}` \| `{revision}` \|",
            source,
            re.MULTILINE,
        ), f"missing pinned source row for {repository}"


def test_transport_and_application_boundaries_are_explicit() -> None:
    normalized = _normalized()
    required = (
        "integrator owns provider transport",
        "credentials, oauth, provider sdks",
        "no shared database",
        "backoffice is the first adopter",
        "sub is a later independent adopter",
        "local immutable website snapshot",
        "siterelease",
        "typed outbox",
        "modules never import sibling modules",
    )
    assert [phrase for phrase in required if phrase not in normalized] == []


def test_campaign_site_and_web_evidence_is_not_overstated() -> None:
    normalized = _normalized()
    assert "sub is the mandatory campaign source" in normalized
    assert "consent and suppression are inputs owned outside campaigns" in normalized
    assert "no qualifying site-builder implementation was found" in normalized
    assert "greenfield-after-inventory proofs" in normalized
    assert "no qualifying first-party observation owner" in normalized
    assert "mkt's ga4 aggregate reader remains external/provider evidence" in normalized


def test_merged_campaigns_is_not_misreported_as_released_or_adopted() -> None:
    normalized = _normalized()
    required = (
        "300ebd7523e85dff7e94efcdf81d8c1f34b80de5",
        "68939275fdb302b1f50ed92a8920ccea745e5d37",
        "campaigns `0.1.0a1` remains unallowlisted, unpublished",
        "dossier status `audit-complete` with no contract consumer",
        "sub cutover 1 for campaigns",
        "backoffice is cutover 2",
        "kernel a72 and durable timers `0.1.0a1` are tagged and registry-verified",
        "kernel a73 is also published and registry-verified",
        "campaigns package and lock now correctly use a73 as the effective floor",
        "retaining a72 as allocation evidence",
        "campaigns release remains gated on sub's kernel s7",
        "the timer and kernel publication gates are closed",
    )
    assert [phrase for phrase in required if phrase not in normalized] == []


def test_released_media_observations_is_not_misreported_as_adopted() -> None:
    normalized = _normalized()
    required = (
        "c548ef02aca10b421d1ebf4158b9c4fdf72e6025",
        "abf1b9ad4c3889aa6c40ed2e01419e440452f565",
        "56517abc7f05cb6e20f9b0e5fdb6a492dbf0fdd2",
        "2ade09d16c3e2d246ad361129c4700de6eff819b",
        "that commit is not validation or release evidence",
        "candidate package is `0.1.0a1`",
        "published kernel a77 now owns the vendor cohort",
        "media a78, content a79, publishing a80 and sites a81",
        "all four packages floor at the first installable cohort kernel, a81",
        "pr #284 passed all sixteen required checks",
        "8f99413826e5adf3d35379ebc6deb79bcb5c8242",
        "protected release train published and registry-verified kernel a81 plus "
        "all four a1 modules",
        "release did not resume adoption or move authority",
        "all five tags point and peel to the exact main revision",
        "artifact and manifest compatibility evidence; it is not product "
        "composition, writer cutover or adoption",
        "backoffice and sub remain candidate consumers",
        "all writer-retirement rows remain `not-started`",
        "adoption pause remains active",
        "release and adoption remain separate gates",
    )
    assert [phrase for phrase in required if phrase not in normalized] == []


def test_inherited_mkt_platform_code_is_explicitly_rejected() -> None:
    normalized = _normalized()
    rejected = (
        "people and identity",
        "billing",
        "rbac and sessions",
        "settings",
        "generic tasks",
        "provider clients",
    )
    assert [item for item in rejected if item not in normalized] == []


def test_first_slice_is_pre_registered_against_the_selected_source() -> None:
    dossier = tomllib.loads(CONTENT_DOSSIER.read_text(encoding="utf-8"))
    assert dossier["package"] == "dotmac-content"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac-erp"
    assert any(
        source.startswith("dotmac_mkt:app/models/campaign.py")
        for source in dossier["source_paths"]
    )


def test_content_retirement_ledger_freezes_nine_distinct_unstarted_writers() -> None:
    ledger = tomllib.loads(CONTENT_RETIREMENT.read_text(encoding="utf-8"))
    writers = ledger["writer"]
    assert [row["id"] for row in writers] == [
        "CNT-R1",
        "CNT-R2",
        "CNT-R3",
        "CNT-R4",
        "CNT-R5",
        "CNT-R6",
        "CNT-R7",
        "CNT-R8",
        "CNT-R9",
    ]
    assert {row["status"] for row in writers} == {"not-started"}
    assert len({row["source_behavior"] for row in writers}) == len(writers)


def test_publishing_slice_is_pre_registered_against_mkt_delivery_behavior() -> None:
    dossier = tomllib.loads(PUBLISHING_DOSSIER.read_text(encoding="utf-8"))
    assert dossier["package"] == "dotmac-publishing"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac-erp"
    assert dossier["source_revisions"] == [
        "dotmac_mkt:7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d"
    ]
    assert {
        "dotmac_mkt:app/models/post_delivery.py",
        "dotmac_mkt:app/services/publishing_service.py",
        "dotmac_mkt:app/tasks/publish_scheduled.py",
    } <= set(dossier["source_paths"])


def test_publishing_retirement_ledger_freezes_ten_distinct_unstarted_writers() -> None:
    ledger = tomllib.loads(PUBLISHING_RETIREMENT.read_text(encoding="utf-8"))
    writers = ledger["writer"]
    assert [row["id"] for row in writers] == [
        "PUB-R1",
        "PUB-R2",
        "PUB-R3",
        "PUB-R4",
        "PUB-R5",
        "PUB-R6",
        "PUB-R7",
        "PUB-R8",
        "PUB-R9",
        "PUB-R10",
    ]
    assert {row["status"] for row in writers} == {"not-started"}
    assert len({row["source_behavior"] for row in writers}) == len(writers)
