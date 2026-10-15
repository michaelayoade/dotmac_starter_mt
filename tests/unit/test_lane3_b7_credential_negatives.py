"""Planted-violation tests for the Lane 3 B7 credential-negative validator.

Offline and synthetic: no OpenBao, GitHub or host contact.
"""

from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_b7_credential_negatives", ROOT / "scripts/lane3_b7_credential_negatives.py"
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


@pytest.fixture
def role() -> Any:
    return mod.load_role_definition(ROOT)


@pytest.fixture
def matrix() -> dict[str, Any]:
    return copy.deepcopy(mod.load_matrix(ROOT))


def row(matrix: dict[str, Any], claim: str) -> dict[str, Any]:
    return next(r for r in matrix["claims"] if r["claim"] == claim)


def test_checked_in_matrix_is_valid(role: Any, matrix: dict[str, Any]) -> None:
    assert mod.validate(role, matrix) == []
    assert mod.main([str(ROOT)]) == 0


def test_role_definition_is_derived_from_source(role: Any) -> None:
    assert role.role == "lane3-exposure-rehearsal"
    assert set(role.bound_claims) == {
        "repository",
        "repository_id",
        "repository_owner_id",
        "environment",
        "ref",
        "workflow_ref",
        "event_name",
    }
    assert role.bound_claims["event_name"] == "workflow_dispatch"
    assert role.bound_claims["workflow_ref"].endswith("@refs/heads/main")
    assert role.audience == "urn:dotmac:lane3:exposure-rehearsal"


@pytest.mark.parametrize(
    "claim",
    [
        "repository",
        "repository_id",
        "repository_owner_id",
        "environment",
        "ref",
        "workflow_ref",
        "event_name",
        "audience",
        "expiry",
    ],
)
def test_missing_claim_row_is_refused(
    role: Any, matrix: dict[str, Any], claim: str
) -> None:
    matrix["claims"] = [r for r in matrix["claims"] if r["claim"] != claim]
    assert any(
        p.startswith(f"{claim}: no matrix row") for p in mod.validate(role, matrix)
    )


def test_extra_row_for_unbound_claim_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    extra = copy.deepcopy(row(matrix, "environment"))
    extra["claim"] = "sub"
    matrix["claims"].append(extra)
    assert any("does not bind" in p for p in mod.validate(role, matrix))


def test_coupled_case_credited_to_single_claim_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    row(matrix, "event_name")["credited_to"] = ["event_name"]
    problems = mod.validate(role, matrix)
    assert any(
        "event_name: coupled denial credited to a single claim" in p for p in problems
    )


def test_event_name_declared_isolable_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    r = row(matrix, "event_name")
    r.update(isolable=True, coupled_with=[], credited_to=["event_name"])
    assert any("event_name: must be COUPLED" in p for p in mod.validate(role, matrix))


def test_ref_must_be_coupled_with_workflow_ref_and_environment(
    role: Any, matrix: dict[str, Any]
) -> None:
    r = row(matrix, "ref")
    r.update(coupled_with=["workflow_ref"], credited_to=["ref", "workflow_ref"])
    assert any("ref: must be COUPLED" in p for p in mod.validate(role, matrix))


def test_repository_id_declared_isolable_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    r = row(matrix, "repository_id")
    r.update(isolable=True, coupled_with=[], credited_to=["repository_id"])
    assert any(
        "repository_id: must be COUPLED" in p for p in mod.validate(role, matrix)
    )


def test_isolable_row_listing_coupling_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    row(matrix, "audience")["coupled_with"] = ["expiry"]
    assert any(
        "audience: marked isolable but lists coupled" in p
        for p in mod.validate(role, matrix)
    )


def test_coupled_with_unknown_claim_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    row(matrix, "workflow_ref")["coupled_with"] = ["sub"]
    assert any("unknown claim" in p for p in mod.validate(role, matrix))


@pytest.mark.parametrize(
    "vacuous",
    [
        "github_scheduling_refusal",
        "environment_protection_block",
        "fixture",
        "unit_test",
        "local_jwt_decode",
        "log_line",
        "workflow_log",
        "policy_text_review",
        "positive_login_only",
    ],
)
def test_vacuous_evidence_is_refused(
    role: Any, matrix: dict[str, Any], vacuous: str
) -> None:
    row(matrix, "environment")["evidence"].append(vacuous)
    assert any("vacuous evidence type" in p for p in mod.validate(role, matrix))


def test_missing_required_evidence_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    row(matrix, "expiry")["evidence"] = ["openbao_audit_login_failure_for_role"]
    assert any(
        "missing required evidence 'no_token_issued'" in p
        for p in mod.validate(role, matrix)
    )


def test_unknown_evidence_type_is_refused(role: Any, matrix: dict[str, Any]) -> None:
    row(matrix, "expiry")["evidence"].append("trust_me")
    assert any("unknown evidence type" in p for p in mod.validate(role, matrix))


def test_wrong_expected_outcome_is_refused(role: Any, matrix: dict[str, Any]) -> None:
    row(matrix, "audience")["expected_outcome"] = "login_allowed"
    assert any("expected_outcome" in p for p in mod.validate(role, matrix))


def test_missing_gate0_path_row_is_refused(role: Any, matrix: dict[str, Any]) -> None:
    matrix["path_isolation"] = [
        p
        for p in matrix["path_isolation"]
        if p["path"] != "gate0-ssh/sign/rehearsal-controller"
    ]
    assert any(
        "gate0-ssh/sign/rehearsal-controller: no path-isolation row" in p
        for p in mod.validate(role, matrix)
    )


def test_path_row_without_positive_control_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    matrix["path_isolation"][0]["evidence"] = [
        "openbao_audit_permission_denied_for_token",
        "http_403",
    ]
    assert any("positive_control_same_token" in p for p in mod.validate(role, matrix))


def test_path_row_with_fixture_evidence_is_refused(
    role: Any, matrix: dict[str, Any]
) -> None:
    matrix["path_isolation"][0]["evidence"].append("fixture")
    assert any("vacuous evidence type" in p for p in mod.validate(role, matrix))


def test_unknown_path_row_must_carry_unknown_status(
    role: Any, matrix: dict[str, Any]
) -> None:
    unknown = next(p for p in matrix["path_isolation"] if p["path"] == "UNKNOWN")
    del unknown["status"]
    assert any("UNKNOWN path rows" in p for p in mod.validate(role, matrix))


def test_missing_cross_role_row_is_refused(role: Any, matrix: dict[str, Any]) -> None:
    matrix["cross_role_login"] = []
    assert any("no cross-role login row" in p for p in mod.validate(role, matrix))


def test_live_probe_plan_must_stay_unauthorized(
    role: Any, matrix: dict[str, Any]
) -> None:
    matrix["live_probe_plan"]["status"] = "AUTHORIZED"
    matrix["live_execution"] = "AUTHORIZED"
    problems = mod.validate(role, matrix)
    assert any("live_probe_plan.status" in p for p in problems)
    assert any("live_execution" in p for p in problems)


def test_matrix_markers_missing_is_an_error(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "m.md"
    target.write_text("no markers", encoding="utf-8")
    with pytest.raises(mod.MatrixError):
        mod.load_matrix(tmp_path, "m.md")


def _copy_sources(tmp_path: pathlib.Path) -> None:
    for rel in (mod.DOC, mod.CONFIG, mod.READER, mod.MATRIX):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")


def test_new_bound_claim_in_the_doc_requires_a_matrix_row(
    tmp_path: pathlib.Path,
) -> None:
    _copy_sources(tmp_path)
    doc = tmp_path / mod.DOC
    text = doc.read_text(encoding="utf-8")
    assert "`event_name=workflow_dispatch`" in text
    doc.write_text(
        text.replace(
            "`event_name=workflow_dispatch`", "`event_name=workflow_dispatch`, `sub`", 1
        ),
        encoding="utf-8",
    )
    # `sub` has no value in source: the loader refuses rather than guessing.
    with pytest.raises(mod.MatrixError):
        mod.load_role_definition(tmp_path)
    assert mod.main([str(tmp_path)]) == 2


def test_doc_config_disagreement_is_refused(tmp_path: pathlib.Path) -> None:
    _copy_sources(tmp_path)
    cfg = tmp_path / mod.CONFIG
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "lane3-exposure-rehearsal.yml", "other.yml"
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.MatrixError):
        mod.load_role_definition(tmp_path)


def test_audience_disagreement_is_refused(tmp_path: pathlib.Path) -> None:
    _copy_sources(tmp_path)
    reader = tmp_path / mod.READER
    reader.write_text(
        reader.read_text(encoding="utf-8").replace(
            'AUDIENCE = "urn:dotmac:lane3:exposure-rehearsal"',
            'AUDIENCE = "urn:example:other"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.MatrixError):
        mod.load_role_definition(tmp_path)


def test_validator_makes_no_network_import() -> None:
    source = (ROOT / "scripts/lane3_b7_credential_negatives.py").read_text(
        encoding="utf-8"
    )
    for banned in ("socket", "urllib", "http.client", "requests", "subprocess"):
        assert f"import {banned}" not in source
