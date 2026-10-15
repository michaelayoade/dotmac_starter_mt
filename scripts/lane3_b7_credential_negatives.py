"""Offline validator for the Lane 3 B7 credential-negative claim matrix.

Source-only. It reads files in this repository and nothing else: no OpenBao,
GitHub, network or host contact, and no credential of any kind. It refuses a
matrix that

* misses a claim the B7 role binds (or the audience / expiry rows required by
  docs/LANE3_EXECUTION_TOPOLOGY.md section 7),
* credits a COUPLED denial to a single claim,
* claims a claim is isolable although the role definition makes it coupled, or
* lists vacuous evidence (scheduling refusal, fixture, log line ...).

The role definition is the source-proposed one: the "Workflow identity"
paragraph of the topology document, ``.github/lane3-execution.json`` and the
constants of ``scripts/lane3_openbao_topology.py``. It does not prove what is
provisioned; the live role read-back is a separate, unauthorized step.
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

BEGIN = "<!-- matrix:begin -->"
END = "<!-- matrix:end -->"

# Evidence that proves the role denied the login.
REQUIRED_LOGIN_EVIDENCE = frozenset(
    {"openbao_audit_login_failure_for_role", "no_token_issued"}
)
OPTIONAL_LOGIN_EVIDENCE = frozenset({"denial_reason_class"})
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

# Rows the role definition requires beyond the bound claims (topology doc
# section 7: "name, immutable IDs, environment, ref, workflow, event,
# audience, expiry").
EXTRA_ROWS = ("audience", "expiry")

# Gate-0 and CP paths a Lane 3 token must not read. Source: Control Plane
# docs/design/gate0-d-implementation-spec.md lines 262, 343-344, 949-952 and
# the Gate-0 issuer execution repo scripts/a8_oidc_proof.py. They are not
# defined in this repository, so the matrix cites the other repository.
REQUIRED_DENIED_PATHS = frozenset(
    {
        "secret/data/dotmac/platform-cp/rehearsal-issuer/signing-key",
        "secret/data/dotmac/platform-cp/rehearsal-issuer/trust-state",
        "secret/data/dotmac/platform-cp/rehearsal-attester/signing-key",
        "secret/data/dotmac/platform-cp/rehearsal-attester/trust-state",
        "gate0-ssh/sign/rehearsal-controller",
    }
)
# A Lane 3 JWT presented to the Gate-0 role must also be denied.
REQUIRED_CROSS_ROLE = frozenset({"rehearsal-issuer-protected"})

# Structural coupling that follows from the A8 refusal evidence for the
# Gate-0 issuer role (a different role: it does not transfer as evidence, but
# the GitHub claim mechanics are the same).
# - A repository name or ID change changes workflow_ref, which embeds the
#   repository path.
# - A ref change changes workflow_ref, which embeds the ref.
# - An event_name change was produced in A8 only through a second workflow
#   file, so workflow_ref changed with it
#   (gate0-issuer-execution scripts/a8_oidc_proof.py, workflow_witness:
#   differences = ['event_name', 'workflow_ref']).
ALWAYS_COUPLED_WITH_WORKFLOW_REF = ("repository", "repository_id", "ref", "event_name")
# repository_id and repository_owner_id are immutable; the only non-destructive
# GitHub variation that changes them is a different repository, which also
# changes the repository name.
ALWAYS_COUPLED_WITH_REPOSITORY = ("repository_id", "repository_owner_id")
# The protected Environment is main-only. A token with a wrong ref cannot come
# from that Environment, so a witness changes or drops the environment claim.
REF_ALSO_COUPLED_WITH = "environment"


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
    return data


def _evidence_problems(
    row_id: str, evidence: object, required: frozenset[str], optional: frozenset[str]
) -> list[str]:
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        return [f"{row_id}: evidence must be a list of strings"]
    problems = [
        f"{row_id}: vacuous evidence type {e!r}"
        for e in evidence
        if e in VACUOUS_EVIDENCE
    ]
    problems += [
        f"{row_id}: unknown evidence type {e!r}"
        for e in evidence
        if e not in VACUOUS_EVIDENCE and e not in required | optional
    ]
    problems += [
        f"{row_id}: missing required evidence {e!r}"
        for e in sorted(required - set(evidence))
    ]
    return problems


def validate(role: RoleDefinition, matrix: dict) -> list[str]:
    problems: list[str] = []
    if matrix.get("schema") != "Lane3B7CredentialNegativeMatrix.v1":
        problems.append("schema must be Lane3B7CredentialNegativeMatrix.v1")
    if matrix.get("role") != role.role:
        problems.append("matrix role differs from the reader ROLE")
    if matrix.get("live_execution") != "NOT_AUTHORIZED":
        problems.append("live_execution must be NOT_AUTHORIZED")

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

    must_couple: dict[str, set[str]] = {}
    if "workflow_ref" in role.bound_claims:
        for name in ALWAYS_COUPLED_WITH_WORKFLOW_REF:
            must_couple.setdefault(name, set()).add("workflow_ref")
    for name in ALWAYS_COUPLED_WITH_REPOSITORY:
        must_couple.setdefault(name, set()).add("repository")
    must_couple.setdefault("ref", set()).add(REF_ALSO_COUPLED_WITH)

    for name, row in by_claim.items():
        if name not in required:
            continue
        coupled = row.get("coupled_with")
        credited = row.get("credited_to")
        if not isinstance(coupled, list) or not all(
            isinstance(c, str) for c in coupled
        ):
            problems.append(f"{name}: coupled_with must be a list of claim names")
            continue
        if not isinstance(credited, list) or not all(
            isinstance(c, str) for c in credited
        ):
            problems.append(f"{name}: credited_to must be a list of claim names")
            continue
        isolable = row.get("isolable")
        if not isinstance(isolable, bool):
            problems.append(f"{name}: isolable must be true or false")
            continue
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
        needed = must_couple.get(name, set())
        if needed and (isolable or not needed <= set(coupled)):
            problems.append(
                f"{name}: must be COUPLED with {sorted(needed)} per the role definition"
            )
        if row.get("expected_outcome") != EXPECTED_LOGIN_OUTCOME:
            problems.append(
                f"{name}: expected_outcome must be {EXPECTED_LOGIN_OUTCOME}"
            )
        for field in ("production", "source"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                problems.append(f"{name}: {field} must be a non-empty string")
        problems += _evidence_problems(
            name, row.get("evidence"), REQUIRED_LOGIN_EVIDENCE, OPTIONAL_LOGIN_EVIDENCE
        )

    problems += _validate_paths(matrix)
    plan = matrix.get("live_probe_plan")
    if not isinstance(plan, dict) or plan.get("status") != "NOT_AUTHORIZED":
        problems.append("live_probe_plan.status must be NOT_AUTHORIZED")
    return problems


def _validate_paths(matrix: dict) -> list[str]:
    problems: list[str] = []
    rows = matrix.get("path_isolation")
    if not isinstance(rows, list):
        return ["path_isolation must be a list"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            problems.append("path_isolation row without a path")
            continue
        path = row["path"]
        seen.add(path)
        if path == "UNKNOWN":
            if row.get("status") != "UNKNOWN":
                problems.append("UNKNOWN path rows must carry status UNKNOWN")
            continue
        if row.get("expected_outcome") != EXPECTED_PATH_OUTCOME:
            problems.append(f"{path}: expected_outcome must be {EXPECTED_PATH_OUTCOME}")
        problems += _evidence_problems(
            path, row.get("evidence"), REQUIRED_PATH_EVIDENCE, frozenset()
        )
    problems += [
        f"{p}: no path-isolation row" for p in sorted(REQUIRED_DENIED_PATHS - seen)
    ]
    cross = matrix.get("cross_role_login")
    seen_roles = set()
    if not isinstance(cross, list):
        problems.append("cross_role_login must be a list")
        cross = []
    for row in cross:
        if not isinstance(row, dict) or not isinstance(row.get("role"), str):
            problems.append("cross_role_login row without a role")
            continue
        seen_roles.add(row["role"])
        if row.get("expected_outcome") != EXPECTED_LOGIN_OUTCOME:
            problems.append(
                f"{row['role']}: expected_outcome must be {EXPECTED_LOGIN_OUTCOME}"
            )
        problems += _evidence_problems(
            f"cross_role:{row['role']}",
            row.get("evidence"),
            REQUIRED_LOGIN_EVIDENCE,
            OPTIONAL_LOGIN_EVIDENCE,
        )
    problems += [
        f"cross_role:{r}: no cross-role login row"
        for r in sorted(REQUIRED_CROSS_ROLE - seen_roles)
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else ROOT
    try:
        problems = validate(load_role_definition(root), load_matrix(root))
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
