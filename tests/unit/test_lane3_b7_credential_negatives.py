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
def fixture() -> dict[str, Any]:
    return copy.deepcopy(mod.load_fixture(ROOT))


@pytest.fixture
def matrix() -> dict[str, Any]:
    return copy.deepcopy(mod.load_matrix(ROOT))


def row(matrix: dict[str, Any], claim: str) -> dict[str, Any]:
    return next(r for r in matrix["claims"] if r["claim"] == claim)


def test_checked_in_matrix_is_valid(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    assert mod.validate(role, matrix, fixture) == []
    assert mod.main([str(ROOT)]) == 0
    assert not any(r["path"] == "UNKNOWN" for r in matrix["path_isolation"])


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
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any], claim: str
) -> None:
    matrix["claims"] = [r for r in matrix["claims"] if r["claim"] != claim]
    assert any(
        p.startswith(f"{claim}: no matrix row")
        for p in mod.validate(role, matrix, fixture)
    )


def test_extra_row_for_unbound_claim_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    extra = copy.deepcopy(row(matrix, "environment"))
    extra["claim"] = "sub"
    matrix["claims"].append(extra)
    assert any("does not bind" in p for p in mod.validate(role, matrix, fixture))


def test_coupled_case_credited_to_single_claim_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "event_name")["credited_to"] = ["event_name"]
    problems = mod.validate(role, matrix, fixture)
    assert any(
        "event_name: coupled denial credited to a single claim" in p for p in problems
    )


def test_event_name_declared_isolable_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    r = row(matrix, "event_name")
    r.update(isolable=True, coupled_with=[], credited_to=["event_name"])
    assert any(
        "event_name: must be COUPLED" in p for p in mod.validate(role, matrix, fixture)
    )


def test_ref_must_be_coupled_with_workflow_ref_and_environment(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    r = row(matrix, "ref")
    r.update(coupled_with=["workflow_ref"], credited_to=["ref", "workflow_ref"])
    assert any("ref: must be COUPLED" in p for p in mod.validate(role, matrix, fixture))


def test_repository_id_declared_isolable_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    r = row(matrix, "repository_id")
    r.update(isolable=True, coupled_with=[], credited_to=["repository_id"])
    assert any(
        "repository_id: must be COUPLED" in p
        for p in mod.validate(role, matrix, fixture)
    )


def test_isolable_row_listing_coupling_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "audience")["coupled_with"] = ["expiry"]
    assert any(
        "audience: marked isolable but lists coupled" in p
        for p in mod.validate(role, matrix, fixture)
    )


def test_coupled_with_unknown_claim_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "workflow_ref")["coupled_with"] = ["sub"]
    assert any("unknown claim" in p for p in mod.validate(role, matrix, fixture))


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
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any], vacuous: str
) -> None:
    row(matrix, "environment")["evidence"].append(vacuous)
    assert any(
        "vacuous evidence type" in p for p in mod.validate(role, matrix, fixture)
    )


def test_missing_required_evidence_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "expiry")["evidence"] = ["openbao_audit_login_failure_for_role"]
    assert any(
        "missing required evidence 'no_token_issued'" in p
        for p in mod.validate(role, matrix, fixture)
    )


def test_unknown_evidence_type_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "expiry")["evidence"].append("trust_me")
    assert any(
        "unknown evidence type" in p for p in mod.validate(role, matrix, fixture)
    )


def test_wrong_expected_outcome_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    row(matrix, "audience")["expected_outcome"] = "login_allowed"
    assert any("expected_outcome" in p for p in mod.validate(role, matrix, fixture))


def test_missing_gate0_path_row_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    matrix["path_isolation"] = [
        p
        for p in matrix["path_isolation"]
        if p["path"] != "gate0-ssh/sign/rehearsal-controller"
    ]
    assert any(
        "gate0-ssh/sign/rehearsal-controller: no path-isolation row" in p
        for p in mod.validate(role, matrix, fixture)
    )


def test_path_row_without_positive_control_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    matrix["path_isolation"][0]["evidence"] = [
        "openbao_audit_permission_denied_for_token",
        "http_403",
    ]
    assert any(
        "positive_control_same_token" in p for p in mod.validate(role, matrix, fixture)
    )


def test_path_row_with_fixture_evidence_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    matrix["path_isolation"][0]["evidence"].append("fixture")
    assert any(
        "vacuous evidence type" in p for p in mod.validate(role, matrix, fixture)
    )


def test_missing_cross_role_row_is_refused(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    matrix["cross_role_login"] = []
    assert any(
        "no cross-role login row" in p for p in mod.validate(role, matrix, fixture)
    )


def test_live_probe_plan_must_stay_unauthorized(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
) -> None:
    matrix["live_probe_plan"]["status"] = "AUTHORIZED"
    matrix["live_execution"] = "AUTHORIZED"
    problems = mod.validate(role, matrix, fixture)
    assert any("live_probe_plan.status" in p for p in problems)
    assert any("live_execution" in p for p in problems)


def test_matrix_markers_missing_is_an_error(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "m.md"
    target.write_text("no markers", encoding="utf-8")
    with pytest.raises(mod.MatrixError):
        mod.load_matrix(tmp_path, "m.md")


def _copy_sources(tmp_path: pathlib.Path) -> None:
    for rel in (mod.DOC, mod.CONFIG, mod.READER, mod.MATRIX, mod.FIXTURE):
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


# --- Review round: violations the first validator accepted ------------------


def path_row(matrix: dict[str, Any], path: str) -> dict[str, Any]:
    return next(r for r in matrix["path_isolation"] if r["path"] == path)


def test_isolable_row_without_denial_reason_class_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    row(matrix, "workflow_ref")["evidence"] = [
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
    ]
    problems = mod.validate(role, matrix, fixture)
    assert "workflow_ref: missing required evidence 'denial_reason_class'" in problems


def test_denial_reason_naming_a_claim_outside_the_credited_set_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    row(matrix, "event_name")["denial_reason_named_claims"] = ["ref"]
    problems = mod.validate(role, matrix, fixture)
    assert any("outside the credited set" in p for p in problems)


def test_owner_coupled_only_with_repository_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    r = row(matrix, "repository_owner_id")
    r.update(
        coupled_with=["repository"], credited_to=["repository", "repository_owner_id"]
    )
    problems = mod.validate(role, matrix, fixture)
    assert any(
        p.startswith("repository_owner_id: must be COUPLED with") for p in problems
    )


def test_environment_declared_isolable_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    r = row(matrix, "environment")
    r.update(isolable=True, coupled_with=[], credited_to=["environment"])
    problems = mod.validate(role, matrix, fixture)
    assert any(p.startswith("environment: must be COUPLED") for p in problems)


@pytest.mark.parametrize("claim", ["environment", "expiry"])
def test_decision_for_michael_note_is_required(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any], claim: str
) -> None:
    del row(matrix, claim)["decision"]
    assert f"{claim}: needs a 'DECISION FOR MICHAEL' note" in mod.validate(
        role, matrix, fixture
    )


def test_expiry_marked_producible_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    row(matrix, "expiry")["status"] = "PRODUCIBLE"
    problems = mod.validate(role, matrix, fixture)
    assert "expiry: must be UNPRODUCIBLE_UNDER_CURRENT_TIMEOUT" in problems


def test_audience_probe_with_gate0_audience_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    row(matrix, "audience")["production"] += " urn:dotmac:gate0:rehearsal-issuer"
    problems = mod.validate(role, matrix, fixture)
    assert "audience: must never use the Gate-0 audience" in problems


def test_witness_row_without_consumer_dependency_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    row(matrix, "workflow_ref")["dependencies"] = []
    problems = mod.validate(role, matrix, fixture)
    assert any(p.startswith("workflow_ref: witness row must depend") for p in problems)


def test_path_operation_changed_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    path_row(matrix, "secret/metadata/")["operation"] = "read"
    problems = mod.validate(role, matrix, fixture)
    assert "secret/metadata/: no path-isolation row for operation list" in problems


def test_empty_path_operation_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    path_row(matrix, "sys/mounts")["operation"] = ""
    problems = mod.validate(role, matrix, fixture)
    assert any(p.startswith("sys/mounts: operation must be one of") for p in problems)


def test_empty_path_source_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    path_row(matrix, "sys/mounts")["source"] = ""
    problems = mod.validate(role, matrix, fixture)
    assert "sys/mounts: source missing or not pinned to a commit SHA" in problems


def test_unpinned_cross_repo_source_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    path_row(matrix, "gate0-ssh/config/ca")["source"] = (
        "dotmac_platform_control_plane spec:459"
    )
    problems = mod.validate(role, matrix, fixture)
    assert (
        "gate0-ssh/config/ca: source missing or not pinned to a commit SHA" in problems
    )


def test_unknown_sha_in_source_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    path_row(matrix, "gate0-ssh/sign/rehearsal-controller")["source"] = "x@" + "a" * 40
    problems = mod.validate(role, matrix, fixture)
    assert any("not pinned to a commit SHA" in p for p in problems)


def test_extra_unknown_path_rows_cannot_bypass_checks(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["path_isolation"].append({"path": "UNKNOWN", "status": "UNKNOWN"})
    assert "UNKNOWN: UNKNOWN path rows are not allowed" in mod.validate(
        role, matrix, fixture
    )


def test_public_key_path_must_stay_excluded(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    extra = copy.deepcopy(path_row(matrix, "sys/mounts"))
    extra["path"] = "gate0-ssh/public_key"
    matrix["path_isolation"].append(extra)
    problems = mod.validate(role, matrix, fixture)
    assert "gate0-ssh/public_key: public path must be excluded" in problems


def test_cross_role_row_without_operation_or_source_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["cross_role_login"][0]["operation"] = ""
    matrix["cross_role_login"][0]["source"] = "unpinned"
    problems = mod.validate(role, matrix, fixture)
    assert (
        "cross_role:rehearsal-issuer-protected: operation must be a non-empty string"
        in problems
    )
    assert any("cross_role" in p and "not pinned" in p for p in problems)


def test_gate0_audience_cross_role_variant_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["cross_role_login"][0]["jwt_audience"] = "gate0"
    problems = mod.validate(role, matrix, fixture)
    assert any("Gate-0 audience needs separate approval" in p for p in problems)


@pytest.mark.parametrize("key", ["http_status", "accessor_based"])
def test_accessor_based_no_token_definition_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any], key: str
) -> None:
    defs = matrix["evidence_definitions"]["no_token_issued"]
    defs[key] = True if key == "accessor_based" else 200
    problems = mod.validate(role, matrix, fixture)
    assert any(p.startswith(f"no_token_issued: {key} must be") for p in problems)


def test_raw_audit_logging_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["audit_correlation"]["log_raw_permitted"] = True
    assert "audit_correlation.log_raw_permitted must be false" in mod.validate(
        role, matrix, fixture
    )
    matrix["audit_correlation"]["log_raw_permitted"] = False
    matrix["__document_text"] += " use Raw Audit Logging "
    assert "document proposes raw audit logging" in mod.validate(role, matrix, fixture)


@pytest.mark.parametrize(
    "key,value",
    [
        ("bound_subject", "something"),
        ("bound_claims_type", "glob"),
        ("user_claim", "run_id"),
        ("token_no_default_policy", False),
        ("token_bound_cidrs", ["192.0.2.0/24"]),
    ],
)
def test_fixture_diff_catches_applied_role_drift(
    role: Any,
    matrix: dict[str, Any],
    fixture: dict[str, Any],
    key: str,
    value: Any,
) -> None:
    fixture[key] = value
    assert f"applied_role.{key} differs from the fixture" in mod.validate(
        role, matrix, fixture
    )


def test_fixture_dropping_a_claim_the_source_binds_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    fixture["bound_claims_names"].remove("ref")
    problems = mod.validate(role, matrix, fixture)
    assert "fixture bound claim names differ from the source-derived set" in problems


def test_unread_leeways_require_a_worst_case(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    del matrix["applied_role"]["worst_case_expiration_leeway_seconds"]
    problems = mod.validate(role, matrix, fixture)
    assert any("worst-case leeway" in p for p in problems)


def test_applied_deny_without_a_row_is_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    fixture["policy_denies"].append("lane3-ssh/*")
    problems = mod.validate(role, matrix, fixture)
    assert "applied deny 'lane3-ssh/*': no path row exercises it" in problems


def test_positive_control_must_follow_the_last_denial(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["path_probe_order"]["positive_control_after_last_denial"] = False
    problems = mod.validate(role, matrix, fixture)
    assert any("positive_control_after_last_denial" in p for p in problems)


def test_missing_readbacks_are_refused(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    matrix["path_readbacks"] = ["identity_policies_empty"]
    assert any(
        "path_readbacks must include" in p for p in mod.validate(role, matrix, fixture)
    )


def test_gate0_dependency_section_cannot_be_dropped(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    del matrix["gate0_decision_dependency"]
    assert any(
        p.startswith("gate0_decision_dependency must exist")
        for p in mod.validate(role, matrix, fixture)
    )


def test_gate0_dependency_row_cannot_be_dropped_or_approved(
    role: Any, matrix: dict[str, Any], fixture: dict[str, Any]
) -> None:
    rows = matrix["gate0_decision_dependency"]["rows"]
    matrix["gate0_decision_dependency"]["rows"] = [
        r for r in rows if r["id"] != "V2_key_paths"
    ]
    assert "gate0_decision_dependency: missing row V2_key_paths" in mod.validate(
        role, matrix, fixture
    )
    matrix["gate0_decision_dependency"]["rows"] = rows
    rows[0]["status"] = "APPROVED"
    assert any(
        "must be PENDING_NOT_APPROVED" in p for p in mod.validate(role, matrix, fixture)
    )
