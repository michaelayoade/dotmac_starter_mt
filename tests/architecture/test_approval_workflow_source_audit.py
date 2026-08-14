"""A1's measured `governance-workflow` bucket is not a package boundary.

The fleet sweep originally grouped every table beginning with ``approval_``,
``workflow``, ``form`` or ``case_`` together.  The product-first source audit
found eight different owners behind those names.  This guard binds the measured
rows to the checked-in dispositions and includes a sensitivity proof showing
that restoring the old catch-all makes the classification fail.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from scripts import fleet_decomposition_sweep as sweep

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISPOSITIONS = (
    PROJECT_ROOT / "docs" / "inventories" / "approval-workflow-dispositions.toml"
)

EXPECTED_TABLES = {
    "dotmac_erp": {
        "approval_decision",
        "approval_request",
        "approval_workflow",
        "case_action",
        "case_document",
        "case_response",
        "case_witness",
        "control_evidence",
        "disclosure_checklist",
        "form",
        "form_answer",
        "form_field",
        "form_field_option",
        "form_section",
        "form_submission",
        "form_version",
        "workflow_execution",
        "workflow_rule",
        "workflow_rule_version",
        "workflow_task",
    },
    "dotmac_sub": {"admin_alerts", "admin_whats_new_items"},
    "dotmac_vendor_control_plane": {"approval_policies", "approval_records"},
}

VALID_DISPOSITIONS = {
    "domain-owned",
    "extract-source",
    "reclassify-existing-family",
    "required-port-delta",
    "retire-orphan",
    "separate-audit-required",
}
VALID_EVIDENCE_STATES = {"behavior-tested", "incidental-only", "untested"}

# One representative per classification decision.  The complete row set above
# prevents a table from disappearing; this smaller map makes the sensitivity
# proof readable.
CLASSIFICATION_CANARIES = {
    "approval_request": "approvals",
    "workflow_rule": "workflow-automation",
    "workflow_task": "work-items",
    "form_answer": "forms-data-capture",
    "case_action": "people-payroll",
    "control_evidence": "finance-ledger",
    "admin_alerts": "notifications-comms",
    "admin_whats_new_items": "content-help",
}


def _rows() -> list[dict[str, object]]:
    return tomllib.loads(DISPOSITIONS.read_text(encoding="utf-8"))["tables"]


def _classification_mismatches() -> dict[str, str]:
    return {
        table: sweep.classify(table)
        for table, expected in CLASSIFICATION_CANARIES.items()
        if sweep.classify(table) != expected
    }


def test_a1_dispositions_cover_every_measured_row_exactly_once() -> None:
    rows = _rows()
    identities = [(row["repository"], row["table"]) for row in rows]
    expected = {
        (repository, table)
        for repository, tables in EXPECTED_TABLES.items()
        for table in tables
    }
    assert len(identities) == len(set(identities))
    assert set(identities) == expected


def test_every_a1_row_names_evidence_and_a_terminal_disposition() -> None:
    for row in _rows():
        assert row["disposition"] in VALID_DISPOSITIONS
        assert row["evidence_state"] in VALID_EVIDENCE_STATES
        assert isinstance(row["target"], str) and row["target"].strip()
        for field in ("model", "writer", "reason"):
            assert isinstance(row[field], str) and row[field].strip()
        tests = row["tests"]
        assert isinstance(tests, list) and all(
            isinstance(reference, str) and reference.strip() for reference in tests
        )
        if row["evidence_state"] == "behavior-tested":
            assert tests, f"{row['repository']}:{row['table']} claims tests without one"
        if row["evidence_state"] == "untested":
            identity = f"{row['repository']}:{row['table']}"
            assert tests == [], f"{identity} overstates evidence"


def test_a1_tables_resolve_to_their_real_capability_families() -> None:
    assert _classification_mismatches() == {}
    assert "governance-workflow" not in {name for name, _ in sweep.FAMILIES}


def test_the_a1_canary_detects_the_retired_catch_all(monkeypatch) -> None:
    """Sensitivity: put the old broad rule first and prove every canary fails."""
    old = re.compile(
        r"^(approval_|workflow|form|case_|control_evidence|disclosure|"
        r"checklist|admin_alerts|admin_whats_new|bulk_actions)"
    )
    monkeypatch.setattr(
        sweep,
        "_COMPILED",
        (("governance-workflow", old), *sweep._COMPILED),
    )
    assert set(_classification_mismatches()) == set(CLASSIFICATION_CANARIES)
