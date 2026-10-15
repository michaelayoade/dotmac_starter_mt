"""Offline validator for the Lane 3 B7 credential-negative claim matrix.

Source-only. It reads files in this repository and nothing else: no OpenBao,
GitHub, network or host contact, and no credential of any kind. It refuses a
matrix that

* misses a claim the B7 role binds (or the audience / expiry rows required by
  docs/LANE3_EXECUTION_TOPOLOGY.md section 7),
* credits a coupled case to a single claim, or marks a structurally coupled
  claim isolable,
* lacks a denial-reason class on an isolable row, or names a claim outside the
  credited set,
* lists vacuous evidence, or defines ``no_token_issued`` by token accessor,
* leaves an UNKNOWN path row, an unpinned cross-repository source or an empty
  operation, or drops a required path,
* disagrees with the checked-in value-safe fixture of the applied role, or
* drops the pending Gate-0 runtime decision section.

The role definition is derived from the "Workflow identity" paragraph of the
topology document, ``.github/lane3-execution.json`` and the constants of
``scripts/lane3_openbao_topology.py``, and diffed against
``docs/inventories/lane3-b7-applied-role-fixture.json``. The fixture records
the 2026-10-10 read-back; it is not a live check.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/LANE3_EXECUTION_TOPOLOGY.md"
CONFIG = ".github/lane3-execution.json"
READER = "scripts/lane3_openbao_topology.py"
MATRIX = "docs/inventories/lane3-b7-credential-negative-matrix.md"
FIXTURE = "docs/inventories/lane3-b7-applied-role-fixture.json"

BEGIN = "<!-- matrix:begin -->"
END = "<!-- matrix:end -->"

LOGIN_EVIDENCE = frozenset(
    {
        "openbao_audit_login_failure_for_role",
        "no_token_issued",
        "denial_reason_class",
    }
)
REQUIRED_LOGIN_EVIDENCE = frozenset(
    {"openbao_audit_login_failure_for_role", "no_token_issued"}
)
REQUIRED_PATH_EVIDENCE = frozenset(
    {
        "openbao_audit_permission_denied_for_token",
        "http_403",
        "positive_control_same_token",
    }
)
# Evidence that cannot show the live role denied anything.
VACUOUS_EVIDENCE = frozenset(
    {
        "github_scheduling_refusal",
        "environment_protection_block",
        "fixture",
        "unit_test",
        "local_jwt_decode",
        "log_line",
        "workflow_log",
        "policy_text_review",
        "positive_login_only",
    }
)

EXPECTED_LOGIN_OUTCOME = "login_denied"
EXPECTED_PATH_OUTCOME = "http_403"
PATH_OPERATIONS = frozenset({"read", "list", "update"})
EXTRA_ROWS = ("audience", "expiry")

# Pinned cross-repository revisions. A source for a path row that is not a
# fixture key must carry one of these commit SHAs.
PINNED_SHAS = {
    "dotmac_platform_control_plane": "9e06e129d415d940387fc2a509c54e26f06c0466",
    "gate0-issuer-execution": "c460414707e8d20f7dcf0703b65a5f533fb010f1",
}
FIXTURE_SOURCES = frozenset(
    {"fixture:policy_denies", "fixture:implicit_deny_no_default_policy"}
)

# Gate-0 and CP paths a Lane 3 token must not reach: (path, operation).
REQUIRED_DENIED_PATHS = frozenset(
    {
        ("secret/data/dotmac/platform-cp/rehearsal-issuer/signing-key", "read"),
        ("secret/data/dotmac/platform-cp/rehearsal-issuer/trust-state", "read"),
        ("secret/data/dotmac/platform-cp/rehearsal-attester/signing-key", "read"),
        ("secret/data/dotmac/platform-cp/rehearsal-attester/trust-state", "read"),
        ("secret/data/dotmac/platform-cp/nonexistent-sentinel", "read"),
        ("secret/metadata/dotmac/platform-cp/", "list"),
        ("secret/metadata/", "list"),
        ("gate0-ssh/sign/rehearsal-controller", "update"),
        ("gate0-ssh/config/ca", "read"),
        ("gate0-ssh/roles/rehearsal-controller", "read"),
        ("auth/approle/role/platform-cp-rehearsal-issuer/secret-id", "update"),
        ("auth/approle/role/platform-cp-rehearsal-issuer/role-id", "read"),
        ("auth/approle/role/platform-cp-rehearsal-attester/secret-id", "update"),
        ("auth/approle/role/platform-cp-rehearsal-attester/role-id", "read"),
        ("sys/policies/acl/lane3-exposure-rehearsal", "read"),
        ("sys/audit", "read"),
        ("sys/mounts", "read"),
        ("auth/token/lookup-self", "read"),
        ("auth/token/renew-self", "update"),
    }
)
# Unauthenticated and public: returns 200 whatever the policy says.
EXCLUDED_PATHS = frozenset({"gate0-ssh/public_key"})
REQUIRED_READBACKS = frozenset({"identity_policies_empty", "mounts_exist"})
REQUIRED_CROSS_ROLE = frozenset({"rehearsal-issuer-protected"})

REQUIRED_GATE0_DEPENDENCY_ROWS = frozenset(
    {
        "W1_gate0_exchange_data_deny",
        "W1_gate0_exchange_metadata_deny",
        "W1_explicit_lane3_deny_stanza",
        "W3_cross_role_rerun_c1",
        "W3_alias_no_run_id_metadata",
        "W4_W5_approle_rows",
        "V2_key_paths",
    }
)

# Structural coupling (see the matrix document): claim -> claims that must
# appear in its coupled_with. workflow_ref embeds repository and ref; the
# immutable IDs only change with a different repository or owner; a wrong ref
# cannot come from the main-only Environment; event_name needed a second
# workflow file in A8; environment cannot be varied alone without widening the
# production workflow (topology doc line 83), so under the current topology it
# is coupled with workflow_ref.
MUST_COUPLE = {
    "repository": {"workflow_ref"},
    "repository_id": {"repository", "workflow_ref"},
    "repository_owner_id": {"repository", "repository_id", "workflow_ref"},
    "ref": {"workflow_ref", "environment"},
    "event_name": {"workflow_ref"},
    "environment": {"workflow_ref"},
}
# Rows produced by a witness job: every one depends on the consumer step in the
# protected job, because OpenBao is reachable only over WireGuard.
WITNESS_ROWS = frozenset(
    {
        "repository",
        "repository_id",
        "repository_owner_id",
        "environment",
        "ref",
        "workflow_ref",
        "event_name",
    }
)
DECISION_PREFIX = "DECISION FOR MICHAEL"


class MatrixError(Exception):
    """Raised when an input file cannot be parsed at all."""


@dataclass(frozen=True)
class RoleDefinition:
    role: str
    audience: str
    bound_claims: dict[str, str]  # claim name -> expected value
    audience_in_doc: str


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise MatrixError(f"cannot read {rel}") from exc


def _string_constants(source: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def load_role_definition(root: Path = ROOT) -> RoleDefinition:
    """Derive the B7 role's bound claims from source; refuse on any gap."""
    doc = _read(root, DOC)
    config = json.loads(_read(root, CONFIG))
    constants = _string_constants(_read(root, READER))

    match = re.search(r"\*\*Workflow identity\.\*\*(.*?)\n\n", doc, re.DOTALL)
    if not match:
        raise MatrixError("topology doc has no 'Workflow identity' paragraph")
    paragraph = " ".join(match.group(1).split())
    span = re.search(r"`bound_claims` on (.*?) plus an exact", paragraph)
    if not span:
        raise MatrixError("Workflow identity paragraph does not list bound_claims")
    names: dict[str, str | None] = {}
    for token in re.findall(r"`([a-z_]+(?:=[^`]+)?)`", span.group(1)):
        name, _, value = token.partition("=")
        names[name] = value or None
    if not names:
        raise MatrixError("no bound claim names parsed")

    audience_match = re.search(
        r"`bound_audiences` value \(proposed `([^`]+)`\)", paragraph
    )
    if not audience_match:
        raise MatrixError("Workflow identity paragraph has no bound_audiences value")

    workflow_ref = (
        f"{config['execution_repository']}/{config['workflow_path']}@refs/heads/main"
    )
    if f"`{workflow_ref}`" not in doc:
        raise MatrixError(
            "workflow_ref in config does not match topology doc section 2"
        )
    known = {
        "repository": config["execution_repository"],
        "repository_id": str(config["execution_repository_id"]),
        "repository_owner_id": str(config["execution_owner_id"]),
        "environment": config["environment"]["name"],
        "ref": "refs/heads/main",
        "workflow_ref": workflow_ref,
        "event_name": "workflow_dispatch",
    }
    bound: dict[str, str] = {}
    for name, doc_value in names.items():
        if name not in known:
            raise MatrixError(f"bound claim {name!r} has no value in source")
        if doc_value is not None and doc_value != known[name]:
            raise MatrixError(f"bound claim {name!r}: doc and config disagree")
        bound[name] = known[name]

    if "ROLE" not in constants or "AUDIENCE" not in constants:
        raise MatrixError("reader constants ROLE/AUDIENCE missing")
    if constants["AUDIENCE"] != audience_match.group(1):
        raise MatrixError("reader AUDIENCE differs from the topology doc")
    return RoleDefinition(
        role=constants["ROLE"],
        audience=constants["AUDIENCE"],
        bound_claims=bound,
        audience_in_doc=audience_match.group(1),
    )


def load_fixture(root: Path = ROOT, rel: str = FIXTURE) -> dict:
    try:
        data = json.loads(_read(root, rel))
    except ValueError as exc:
        raise MatrixError("applied-role fixture json invalid") from exc
    if not isinstance(data, dict):
        raise MatrixError("applied-role fixture must be an object")
    return data


def load_matrix(root: Path = ROOT, rel: str = MATRIX) -> dict:
    text = _read(root, rel)
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        raise MatrixError("matrix markers missing")
    block = text[start + len(BEGIN) : end]
    found = re.search(r"```json\n(.*?)\n```", block, re.DOTALL)
    if not found:
        raise MatrixError("matrix json block missing")
    try:
        data = json.loads(found.group(1))
    except ValueError as exc:
        raise MatrixError("matrix json invalid") from exc
    if not isinstance(data, dict):
        raise MatrixError("matrix json must be an object")
    data["__document_text"] = text
    return data


def _str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _evidence_problems(
    row_id: str,
    evidence: object,
    required: frozenset[str],
    allowed: frozenset[str],
) -> list[str]:
    if not _str_list(evidence):
        return [f"{row_id}: evidence must be a list of strings"]
    assert isinstance(evidence, list)
    problems = [
        f"{row_id}: vacuous evidence type {e!r}"
        for e in evidence
        if e in VACUOUS_EVIDENCE
    ]
    problems += [
        f"{row_id}: unknown evidence type {e!r}"
        for e in evidence
        if e not in VACUOUS_EVIDENCE and e not in allowed
    ]
    problems += [
        f"{row_id}: missing required evidence {e!r}"
        for e in sorted(required - set(evidence))
    ]
    return problems


def _source_pinned(source: object) -> bool:
    """A cross-repository source needs a pinned commit SHA; fixture keys do not."""
    if not isinstance(source, str) or not source.strip():
        return False
    if source in FIXTURE_SOURCES:
        return True
    found = re.findall(r"@([0-9a-f]{40})\b", source)
    return bool(found) and all(sha in PINNED_SHAS.values() for sha in found)


def validate(role: RoleDefinition, matrix: dict, fixture: dict) -> list[str]:
    problems: list[str] = []
    if matrix.get("schema") != "Lane3B7CredentialNegativeMatrix.v2":
        problems.append("schema must be Lane3B7CredentialNegativeMatrix.v2")
    if matrix.get("role") != role.role:
        problems.append("matrix role differs from the reader ROLE")
    if matrix.get("live_execution") != "NOT_AUTHORIZED":
        problems.append("live_execution must be NOT_AUTHORIZED")
    if "raw audit logging" in str(matrix.get("__document_text", "")).lower():
        problems.append("document proposes raw audit logging")

    problems += _validate_fixture(role, matrix, fixture)
    problems += _validate_definitions(matrix)

    rows = matrix.get("claims")
    if not isinstance(rows, list):
        return [*problems, "claims must be a list"]
    by_claim: dict[str, dict] = {}
    for row in rows:
        name = row.get("claim") if isinstance(row, dict) else None
        if not isinstance(name, str):
            problems.append("claim row without a claim name")
        elif name in by_claim:
            problems.append(f"{name}: duplicate row")
        else:
            by_claim[name] = row

    required = [*role.bound_claims, *EXTRA_ROWS]
    problems += [
        f"{name}: no matrix row for this bound claim"
        for name in required
        if name not in by_claim
    ]
    problems += [
        f"{name}: row for a claim the role does not bind"
        for name in by_claim
        if name not in required
    ]
    for name, row in by_claim.items():
        if name in required:
            problems += _validate_claim_row(name, row, set(required))

    problems += _validate_paths(matrix, fixture)
    problems += _validate_gate0_dependency(matrix)
    plan = matrix.get("live_probe_plan")
    if not isinstance(plan, dict) or plan.get("status") != "NOT_AUTHORIZED":
        problems.append("live_probe_plan.status must be NOT_AUTHORIZED")
    return problems


def _validate_claim_row(name: str, row: dict, required: set[str]) -> list[str]:
    problems: list[str] = []
    coupled = row.get("coupled_with")
    credited = row.get("credited_to")
    isolable = row.get("isolable")
    if not _str_list(coupled):
        return [f"{name}: coupled_with must be a list of claim names"]
    if not _str_list(credited):
        return [f"{name}: credited_to must be a list of claim names"]
    if not isinstance(isolable, bool):
        return [f"{name}: isolable must be true or false"]
    assert isinstance(coupled, list) and isinstance(credited, list)
    for other in coupled:
        if other not in required or other == name:
            problems.append(f"{name}: coupled_with names unknown claim {other!r}")
    if isolable:
        if coupled:
            problems.append(f"{name}: marked isolable but lists coupled claims")
        if credited != [name]:
            problems.append(f"{name}: isolable row must credit exactly itself")
    else:
        if not coupled:
            problems.append(f"{name}: not isolable but no coupled_with")
        if sorted(credited) != sorted({name, *coupled}):
            problems.append(
                f"{name}: coupled denial credited to a single claim or a different "
                "set than the claims the variation changes"
            )
    needed = MUST_COUPLE.get(name, set())
    if needed and (isolable or not needed <= set(coupled)):
        problems.append(
            f"{name}: must be COUPLED with {sorted(needed)} per the role definition"
        )
    if row.get("expected_outcome") != EXPECTED_LOGIN_OUTCOME:
        problems.append(f"{name}: expected_outcome must be {EXPECTED_LOGIN_OUTCOME}")
    for field in ("production", "source"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            problems.append(f"{name}: {field} must be a non-empty string")
    evidence = row.get("evidence")
    required_evidence = set(REQUIRED_LOGIN_EVIDENCE)
    if isolable:
        required_evidence.add("denial_reason_class")
    problems += _evidence_problems(
        name, evidence, frozenset(required_evidence), LOGIN_EVIDENCE
    )
    named = row.get("denial_reason_named_claims")
    if not _str_list(named) or not named:
        problems.append(f"{name}: denial_reason_named_claims must be a non-empty list")
    else:
        assert isinstance(named, list)
        outside = [c for c in named if c not in credited]
        if outside:
            problems.append(
                f"{name}: denial reason names {outside} outside the credited set"
            )
    if name in WITNESS_ROWS:
        deps = row.get("dependencies")
        if not _str_list(deps) or "witness_consumer_step" not in deps:
            problems.append(
                f"{name}: witness row must depend on witness_consumer_step "
                "(OpenBao is reachable only over WireGuard)"
            )
    if name in ("environment", "expiry"):
        decision = row.get("decision")
        if not isinstance(decision, str) or not decision.startswith(DECISION_PREFIX):
            problems.append(f"{name}: needs a '{DECISION_PREFIX}' note")
    if name == "expiry":
        if row.get("status") != "UNPRODUCIBLE_UNDER_CURRENT_TIMEOUT":
            problems.append("expiry: must be UNPRODUCIBLE_UNDER_CURRENT_TIMEOUT")
        forbidden = row.get("forbidden")
        if not _str_list(forbidden) or not {"forging", "jwt_export"} <= set(forbidden):
            problems.append("expiry: must forbid forging and jwt_export")
    if name == "audience":
        probe = row.get("probe_audience")
        if probe != "nonce_bound_by_no_role":
            problems.append("audience: probe_audience must be nonce_bound_by_no_role")
        if "urn:dotmac:gate0" in json.dumps(row):
            problems.append("audience: must never use the Gate-0 audience")
    return problems


def _validate_definitions(matrix: dict) -> list[str]:
    problems: list[str] = []
    defs = matrix.get("evidence_definitions")
    token = defs.get("no_token_issued") if isinstance(defs, dict) else None
    if not isinstance(token, dict):
        return ["evidence_definitions.no_token_issued missing"]
    expected = {
        "audit_response_has_auth_block": False,
        "same_request_id": True,
        "http_status": 400,
        "accessor_based": False,
    }
    for key, value in expected.items():
        if token.get(key) != value:
            problems.append(
                f"no_token_issued: {key} must be {value!r} (batch tokens have no "
                "accessor, so an accessor test is vacuous)"
            )
    corr = matrix.get("audit_correlation")
    if not isinstance(corr, dict):
        return [*problems, "audit_correlation missing"]
    if corr.get("method") != "hmac_equality_with_p0_login":
        problems.append("audit_correlation.method must be hmac_equality_with_p0_login")
    if corr.get("alternative") != "sys_audit_hash_michael_root_step":
        problems.append("audit_correlation.alternative must be the root audit-hash")
    if corr.get("log_raw_permitted") is not False:
        problems.append("audit_correlation.log_raw_permitted must be false")
    return problems


def _validate_fixture(role: RoleDefinition, matrix: dict, fixture: dict) -> list[str]:
    problems: list[str] = []
    if fixture.get("role") != role.role:
        problems.append("fixture role differs from the reader ROLE")
    if sorted(fixture.get("bound_claims_names", [])) != sorted(role.bound_claims):
        problems.append("fixture bound claim names differ from the source-derived set")
    if fixture.get("bound_audiences") != [role.audience]:
        problems.append("fixture bound_audiences differ from the source audience")
    applied = matrix.get("applied_role")
    if not isinstance(applied, dict):
        return [*problems, "applied_role block missing"]
    for key in (
        "bound_subject",
        "bound_claims_type",
        "user_claim",
        "claim_mappings",
        "token_type",
        "token_no_default_policy",
        "token_bound_cidrs",
    ):
        if key not in applied or applied[key] != fixture.get(key):
            problems.append(f"applied_role.{key} differs from the fixture")
    leeways = fixture.get("leeways", {})
    if applied.get("leeways_read_back") != leeways.get("read_back"):
        problems.append("applied_role.leeways_read_back differs from the fixture")
    if not leeways.get("read_back") and not isinstance(
        applied.get("worst_case_expiration_leeway_seconds"), int
    ):
        problems.append(
            "leeways are not read back: a worst-case leeway must be planned for"
        )
    if applied.get("bound_issuer_shared_with") != (
        fixture.get("bound_issuer", {}).get("shared_with_roles")
    ):
        problems.append(
            "applied_role.bound_issuer_shared_with differs from the fixture"
        )
    return problems


def _validate_paths(matrix: dict, fixture: dict) -> list[str]:
    problems: list[str] = []
    rows = matrix.get("path_isolation")
    if not isinstance(rows, list):
        return ["path_isolation must be a list"]
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            problems.append("path_isolation row without a path")
            continue
        path = row["path"]
        operation = row.get("operation")
        label = f"{path}"
        if path == "UNKNOWN" or row.get("status") == "UNKNOWN":
            problems.append(f"{label}: UNKNOWN path rows are not allowed")
            continue
        if path in EXCLUDED_PATHS:
            problems.append(f"{label}: public path must be excluded")
        if operation not in PATH_OPERATIONS:
            problems.append(
                f"{label}: operation must be one of {sorted(PATH_OPERATIONS)}"
            )
        else:
            seen.add((path, operation))
        if not _source_pinned(row.get("source")):
            problems.append(f"{label}: source missing or not pinned to a commit SHA")
        if row.get("expected_outcome") != EXPECTED_PATH_OUTCOME:
            problems.append(
                f"{label}: expected_outcome must be {EXPECTED_PATH_OUTCOME}"
            )
        problems += _evidence_problems(
            label, row.get("evidence"), REQUIRED_PATH_EVIDENCE, REQUIRED_PATH_EVIDENCE
        )
    problems += [
        f"{p}: no path-isolation row for operation {op}"
        for p, op in sorted(REQUIRED_DENIED_PATHS - seen)
    ]
    # Every explicit deny of the applied policy must be exercised by a row.
    paths = [p for p, _ in seen]
    for deny in fixture.get("policy_denies", []):
        prefix = deny.rstrip("*")
        if not any(p.startswith(prefix) for p in paths):
            problems.append(f"applied deny {deny!r}: no path row exercises it")

    order = matrix.get("path_probe_order")
    if (
        not isinstance(order, dict)
        or order.get("positive_control_after_last_denial") is not True
    ):
        problems.append(
            "path_probe_order.positive_control_after_last_denial must be true"
        )
    readbacks = matrix.get("path_readbacks")
    if not _str_list(readbacks) or not REQUIRED_READBACKS <= set(readbacks):
        problems.append(f"path_readbacks must include {sorted(REQUIRED_READBACKS)}")

    cross = matrix.get("cross_role_login")
    if not isinstance(cross, list):
        return [*problems, "cross_role_login must be a list"]
    seen_roles: set[str] = set()
    for row in cross:
        if not isinstance(row, dict) or not isinstance(row.get("role"), str):
            problems.append("cross_role_login row without a role")
            continue
        label = f"cross_role:{row['role']}"
        seen_roles.add(row["role"])
        if row.get("jwt_audience") != "lane3":
            problems.append(
                f"{label}: jwt_audience must be lane3 "
                "(Gate-0 audience needs separate approval)"
            )
        if not isinstance(row.get("operation"), str) or not row["operation"].strip():
            problems.append(f"{label}: operation must be a non-empty string")
        if not _source_pinned(row.get("source")):
            problems.append(f"{label}: source missing or not pinned to a commit SHA")
        if row.get("expected_outcome") != EXPECTED_LOGIN_OUTCOME:
            problems.append(
                f"{label}: expected_outcome must be {EXPECTED_LOGIN_OUTCOME}"
            )
        problems += _evidence_problems(
            label, row.get("evidence"), REQUIRED_LOGIN_EVIDENCE, LOGIN_EVIDENCE
        )
    problems += [
        f"cross_role:{r}: no cross-role login row"
        for r in sorted(REQUIRED_CROSS_ROLE - seen_roles)
    ]
    return problems


def _validate_gate0_dependency(matrix: dict) -> list[str]:
    section = matrix.get("gate0_decision_dependency")
    if not isinstance(section, dict) or section.get("status") != "PENDING_NOT_APPROVED":
        return ["gate0_decision_dependency must exist with status PENDING_NOT_APPROVED"]
    rows = section.get("rows")
    if not isinstance(rows, list):
        return ["gate0_decision_dependency.rows must be a list"]
    ids = set()
    problems: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            problems.append("gate0_decision_dependency row without an id")
            continue
        ids.add(row["id"])
        if row.get("status") != "PENDING_NOT_APPROVED":
            problems.append(f"{row['id']}: status must be PENDING_NOT_APPROVED")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            problems.append(f"{row['id']}: text must be non-empty")
    problems += [
        f"gate0_decision_dependency: missing row {r}"
        for r in sorted(REQUIRED_GATE0_DEPENDENCY_ROWS - ids)
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else ROOT
    try:
        problems = validate(
            load_role_definition(root), load_matrix(root), load_fixture(root)
        )
    except MatrixError as exc:
        print(f"REFUSED: {exc}")
        return 2
    for problem in problems:
        print(f"REFUSED: {problem}")
    if problems:
        return 1
    print("lane3 b7 credential-negative matrix: OK (offline; nothing live was run)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
