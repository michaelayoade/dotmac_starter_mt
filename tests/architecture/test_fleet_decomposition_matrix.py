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

# Column order in the doc's family table. The vendor control plane is measured
# for a different reason than the three it follows — it is a consumer assembly,
# not a monolith being decomposed — so the two sets are named separately and
# nothing here may quietly mean "all four" when it means "the monoliths".
REPOS = ("dotmac_erp", "dotmac_crm", "dotmac_sub", "dotmac_vendor_control_plane")
SOURCE_MONOLITHS = ("dotmac_erp", "dotmac_crm", "dotmac_sub")

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
    # ADR-0024 § 6: a separately deployed Dotmac application, not a module a
    # product installs. Listed so the matrix cannot use the disposition in a row
    # without defining it.
    "independent <component>",
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
    """`| family | erp | crm | sub | vendor | exact | alias | disposition |` rows."""
    columns = len(REPOS) + 2
    pattern = re.compile(
        r"^\|\s*([a-z][a-z-]+)\s*\|" + r"\s*(\d+)\s*\|" * columns + r"[^|]*\|\s*$",
        re.MULTILINE,
    )
    return {
        m.group(1): tuple(int(m.group(i)) for i in range(2, columns + 2))
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
        r"^\|\s*[a-z][a-z-]+\s*\|(?:\s*\d+\s*\|){%d}([^|]*)\|\s*$" % (len(REPOS) + 2),
        source,
        re.MULTILINE,
    )


# The CLOSED set of places a capability may end up. Each entry is matched in a
# declared form rather than by keyword, because the defect this guard exists to
# catch was literally the cell `contract, not a module` — which contains the
# word "module" and means the opposite.
#
# `independent integrator` is NAMED rather than matched as `independent \w+` on
# purpose. An open "independent <anything>" category would let any domain be
# parked outside the fleet's layering by inventing a name for it, which is the
# monolith-parking this test exists to prevent, one indirection later. Adding a
# second independent component is therefore a reviewed diff here and in the
# matrix's Dispositions table — the same treatment a namespace allocation gets.
_DESTINATIONS = (
    r"\bkernel\b",
    r"module ←",
    r"module source",
    r"\bdotmac-ui\b",
    # The sole independently deployed Dotmac component (ADR-0024 § 6).
    r"\bindependent (dotmac )?integrator\b",
)


def _has_destination(cell: str) -> bool:
    """Does this disposition name a place the code ends up?"""
    return any(re.search(pattern, cell.lower()) for pattern in _DESTINATIONS)


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


def test_an_independent_component_is_a_destination_only_when_it_is_NAMED() -> None:
    """The escape hatch this closed set exists to keep shut.

    "Independent <something>" is a real destination — ADR-0024 § 6 makes the
    Integrator a separately deployed application rather than a module a product
    installs. But accepting the CATEGORY would let any domain be parked outside
    the fleet's layering by inventing a name for it, which is monolith-parking
    with an extra step. Only the adjudicated component matches.
    """
    assert _has_destination("independent Integrator + connector plugins ← Sub")
    assert _has_destination("independent Dotmac Integrator core")

    assert not _has_destination("independent service")
    assert not _has_destination("independent product")
    assert not _has_destination("independent ERP subsystem")
    assert not _has_destination("stays independent in Sub")


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


def test_baseline_covers_every_measured_repository() -> None:
    """A baseline measured with a repo missing would freeze its duplication at 0."""
    baseline = _baseline()
    assert sorted(baseline["repos_measured"]) == sorted(REPOS)
    assert all(total > 0 for total in baseline["totals"].values())
    assert set(SOURCE_MONOLITHS) <= set(baseline["repos_measured"])


def test_the_fourth_repository_is_not_presented_as_a_fourth_monolith() -> None:
    """The vendor CP is a consumer assembly; the matrix must say so.

    Adding a column is the easy half. The failure mode is a reader taking the
    fourth column to mean "a fourth monolith to de-duplicate", which would make
    its 22 tables look like debt to retire rather than a build-once source and a
    set of capabilities nothing in the fleet implements.
    """
    normalized = " ".join(MATRIX.read_text().lower().split())
    assert "not a fourth monolith" in normalized
    assert "consumer" in normalized


def test_capability_gaps_are_recorded_separately_from_measured_families() -> None:
    """A family table cannot say "nobody built this".

    A capability implemented in no repository produces no `__tablename__`, so it
    can never appear as a measured row — it would silently read as out of scope.
    The gaps therefore get their own section, and it must name every one of them.
    """
    normalized = " ".join(MATRIX.read_text().lower().split())
    assert "capability gaps" in normalized
    for capability in (
        "release catalogue",
        "fleet desired state",
        "resumable run engine",
        "support access",
        "observed health",
        "update authority",
    ):
        assert capability in normalized, capability


def test_family_row_detector_is_sensitive() -> None:
    """A wrong number in the doc must fail, not be skipped as an unparsed row."""
    parsed = _family_rows(
        "| ticketing-sla | 6 | 17 | 22 | 0 | 10 | 6 | module |\n"
        "| projects-tasks | 10 | 11 | 11 | 0 | 10 | 0 | module candidate |\n"
        "| not-a-row | many | 17 | 22 | 0 | 10 | 6 | module |\n"
        # A row that still carries the pre-vendor column count is stale, not a
        # row with a missing number, and must not parse as if it were current.
        "| stale-shape | 6 | 17 | 22 | 10 | 6 | module |\n"
    )

    assert parsed == {
        "ticketing-sla": (6, 17, 22, 0, 10, 6),
        "projects-tasks": (10, 11, 11, 0, 10, 0),
    }
