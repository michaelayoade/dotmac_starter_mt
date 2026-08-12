"""The decomposition matrix must stay countable, and its numbers must be real.

PRODUCT_VISION § "Make the monoliths countable" makes the matrix a standing
artifact, not a one-off memo. Two things rot first: the frozen duplication
baseline drifts away from the prose that quotes it, and the required columns
quietly disappear until the table is a list of opinions.

This test deliberately does NOT re-measure the fleet. ERP, CRM and Sub are not
present in this repository's CI, and a re-measuring test would either fail
everywhere or — far worse — score three absent repositories as zero duplication
and report the programme complete. `scripts/fleet_decomposition_sweep.py` owns
measurement and refuses that reading explicitly; this file owns doc↔baseline
agreement.
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORIES = PROJECT_ROOT / "docs" / "inventories"
MATRIX = INVENTORIES / "fleet-decomposition-matrix.md"
BASELINE = INVENTORIES / "fleet-decomposition-baseline.json"
VISION = PROJECT_ROOT / "docs" / "PRODUCT_VISION.md"

REPOS = ("dotmac_erp", "dotmac_crm", "dotmac_sub")

# The columns PRODUCT_VISION § 2 requires the matrix to record.
REQUIRED_COLUMNS = (
    "owner today",
    "competing implementations",
    "consumers",
    "db / migration owner",
    "authority overlap",
    "target layer",
    "retirement condition",
)

# Every family must resolve to kernel, a Starter module, or a contract. The
# vocabulary loses its meaning in two specific ways, so both are asserted:
# dropping `consolidate` collapses "settle who decides" into "delete the copy",
# and dropping `module ←` lets a single-owner domain read as though staying in
# its monolith were a terminal disposition.
REQUIRED_DISPOSITIONS = (
    "kernel",
    "module ←",
    "consolidate →",
    "contract",
    "unassigned",
)

# The rule the matrix exists to state, and the one earlier revisions got wrong.
SCOPE_RULE = (
    "duplication determines sequencing",
    "does not determine final scope",
    "second consumer proves reuse",
    "not a prerequisite",
    "intermediate",
)

# Progress is adoption, not duplicate counts.
PROGRESS_MEASURES = (
    "capabilities implemented in starter",
    "source apps consuming those packages",
    "old writers retired",
)


def _baseline() -> dict:
    return json.loads(BASELINE.read_text())


def _family_rows(source: str) -> dict[str, tuple[int, ...]]:
    """Every `| family | erp | crm | sub | exact | alias | disposition |` row."""
    pattern = re.compile(
        r"^\|\s*([a-z][a-z-]+)\s*\|" + r"\s*(\d+)\s*\|" * 5 + r"[^|]*\|\s*$",
        re.MULTILINE,
    )
    return {
        m.group(1): tuple(int(m.group(i)) for i in range(2, 7))
        for m in pattern.finditer(source)
    }


def test_matrix_and_baseline_exist_and_are_linked() -> None:
    assert MATRIX.is_file()
    assert BASELINE.is_file()
    assert "fleet-decomposition-matrix.md" in (INVENTORIES / "README.md").read_text()
    assert "fleet-decomposition-matrix.md" in VISION.read_text()


def test_matrix_records_every_required_column() -> None:
    normalized = MATRIX.read_text().lower()
    assert [column for column in REQUIRED_COLUMNS if column not in normalized] == []


def test_matrix_defines_the_full_disposition_vocabulary() -> None:
    normalized = MATRIX.read_text().lower()
    assert [word for word in REQUIRED_DISPOSITIONS if word not in normalized] == []


def test_matrix_states_that_duplication_sequences_rather_than_scopes() -> None:
    """The correction of 2026-08-12: measured duplication chooses the order only.

    An earlier revision used "zero duplication" to mean "not an extraction
    candidate", which would leave ERP's payroll, ledger, procurement, inventory
    and asset domains in a monolith permanently.
    """
    normalized = " ".join(MATRIX.read_text().lower().split())
    assert [phrase for phrase in SCOPE_RULE if phrase not in normalized] == []


def test_matrix_measures_progress_by_adoption_not_duplicate_counts() -> None:
    normalized = " ".join(MATRIX.read_text().lower().split())
    assert [phrase for phrase in PROGRESS_MEASURES if phrase not in normalized] == []


def _disposition_cells(source: str) -> list[str]:
    return re.findall(
        r"^\|\s*[a-z][a-z-]+\s*\|(?:\s*\d+\s*\|){5}([^|]*)\|\s*$",
        source,
        re.MULTILINE,
    )


def _has_destination(cell: str) -> bool:
    """Does this disposition name a place the code ends up?

    Substring-matching "module" is not enough: the defect this guard exists to
    catch was literally the cell `contract, not a module`, which contains the
    word. The destination vocabulary is matched in its declared arrow form so a
    negation cannot satisfy it.
    """
    return any(
        re.search(pattern, cell.lower())
        for pattern in (r"\bkernel\b", r"module ←", r"module source", r"\bdotmac-ui\b")
    )


def test_every_family_resolves_to_kernel_ui_or_a_starter_module() -> None:
    """`contract` and `consolidate →` are NOT destinations.

    A contract is relationship metadata between two destinations, and a
    consolidation is a transition on the way to one. Accepting either as terminal
    lets "contract, not a module" — and with it a domain parked in a monolith —
    come back while CI stays green.
    """
    cells = _disposition_cells(MATRIX.read_text())
    assert cells, "no family rows parsed"
    assert [cell.strip() for cell in cells if not _has_destination(cell)] == []


def test_destination_detector_rejects_transitions_and_metadata() -> None:
    """Sensitivity proof, using the exact strings earlier revisions shipped."""
    assert not _has_destination("product (ERP)")
    assert not _has_destination("contract, not a module")
    assert not _has_destination("retire-to-Sub")
    assert not _has_destination("consolidate → Sub")
    assert not _has_destination("contract with ERP")

    assert _has_destination("module ← ERP")
    assert _has_destination("consolidate → Sub, then module ← Sub")
    assert _has_destination("module ← Sub + contract with ERP")
    assert _has_destination("kernel (cutover in flight)")
    assert _has_destination("dotmac-ui + template studio")


def test_contract_and_consolidation_are_documented_as_non_destinations() -> None:
    normalized = " ".join(MATRIX.read_text().lower().split())
    assert "an annotation, not a location" in normalized
    assert "intermediate" in normalized


def test_every_measured_family_has_a_matrix_row() -> None:
    documented = _family_rows(MATRIX.read_text())
    assert set(_baseline()["families"]) == set(documented)


def test_family_counts_match_the_frozen_baseline() -> None:
    documented = _family_rows(MATRIX.read_text())
    mismatched = {
        family: {"doc": documented[family], "baseline": counts}
        for family, counts in _baseline()["families"].items()
        if documented[family]
        != (*(counts[repo] for repo in REPOS), counts["collisions"], counts["aliased"])
    }
    assert mismatched == {}


def test_headline_totals_match_the_frozen_baseline() -> None:
    source = MATRIX.read_text()
    baseline = _baseline()
    duplicated = baseline["duplicated_table_names"]

    assert f"{sum(baseline['totals'].values()):,} tables" in source
    assert (
        f"**{duplicated['exact'] + duplicated['aliased']} duplicated table names**"
        in source
    )
    assert f"({duplicated['exact']} exact + {duplicated['aliased']} aliased)" in source
    for repo, total in baseline["totals"].items():
        assert re.search(
            rf"\|\s*{total}\s*\|", source
        ), f"{repo} total {total} not in the table"


def test_baseline_covers_all_three_source_monoliths() -> None:
    """A baseline measured with a repo missing would freeze its duplication at 0."""
    baseline = _baseline()
    assert sorted(baseline["repos_measured"]) == sorted(REPOS)
    assert all(total > 0 for total in baseline["totals"].values())


def test_family_row_detector_is_sensitive() -> None:
    """A wrong number in the doc must fail, not be skipped as an unparsed row."""
    parsed = _family_rows(
        "| ticketing-sla | 6 | 17 | 22 | 10 | 6 | module |\n"
        "| projects-tasks | 10 | 11 | 11 | 10 | 0 | module candidate |\n"
        "| not-a-row | many | 17 | 22 | 10 | 6 | module |\n"
    )

    assert parsed == {
        "ticketing-sla": (6, 17, 22, 10, 6),
        "projects-tasks": (10, 11, 11, 10, 0),
    }
