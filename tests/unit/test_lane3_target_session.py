"""E7 / T1: the target half's closed inputs and its structural guarantees.

The Executor path needs a host, the CP bindings and a live Control pair, so it
is not exercised here. What is checked is everything decidable without them: the
closed `start` and `external_results` parsers, the item partition, and static
guards on the properties the design rests on (no stdout, no release writer, no
relaxed `require_build_effects`, no SSH).
"""

from __future__ import annotations

import ast
import copy
import pathlib
import sys
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_target_session as session  # noqa: E402
from dotmac_deployment_foundation.rehearsal import (  # noqa: E402
    REQUIRED_ITEMS,
    RequirementStatus,
)

SOURCE = SCRIPTS / "lane3_target_session.py"

START: dict[str, Any] = {
    "foundation_revision": "a" * 40,
    "candidate_source_revision": "b" * 40,
    "foundation_artifact": "sha256:" + "c" * 64,
    "authorization_run": "run-1",
    "controller_identity": "SHA256:" + "A" * 43,
    "host_id": "host-a",
    "candidate_version": "0.4.0a2",
    "descriptor": "/run/lane3/product.toml",
    "authorization_document": "/run/lane3/authorization.json",
    "deploy_dir": "/srv/lane3",
    "lock_dir": "/var/lock/dotmac",
    "lease_dir": "/run/lane3/lease",
    "timeout": 120,
    "probe_vantage_ref": "vantage@1",
    "execution_run": {"repository_id": 1, "run_id": 2, "run_attempt": 1},
    "expected_machine_id": "0123456789abcdef0123456789abcdef",
    "expected_host_incarnation": "incarnation-1",
    "window_seconds": 300,
    "evidence_seconds": 60,
}


def _row(code: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": code,
        "status": "executed_passed",
        "detail": "measured by the launcher",
        "evidence": ["probe:x"],
    }
    row.update(overrides)
    return row


def _rows() -> list[dict[str, Any]]:
    return [_row(code) for code in sorted(session.EXTERNAL_CODES)]


# ── the start frame ─────────────────────────────────────────────────────────


def test_a_complete_start_parses() -> None:
    start = session.parse_start(copy.deepcopy(START))
    assert start.host_id == "host-a"
    assert start.lock_dir == pathlib.Path("/var/lock/dotmac")
    assert start.execution_run.run_id == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("descriptor", "relative/product.toml"),
        ("descriptor", "/run/../etc/product.toml"),
        ("lease_dir", "lease"),
        ("lock_dir", "/tmp/lock"),  # noqa: S108 - the refusal under test
        ("lock_dir", "/var/lock"),
        ("lock_dir", "/var/lockx/dotmac"),
        ("host_id", ""),
        ("host_id", "has space"),
        ("host_id", "x" * 300),
        ("timeout", 0),
        ("timeout", True),
        ("timeout", "120"),
        ("window_seconds", 100000),
        ("evidence_seconds", 0),
        ("expected_machine_id", ""),
        ("expected_host_incarnation", ""),
    ],
)
def test_an_unusable_start_value_refuses(key: str, value: Any) -> None:
    data = copy.deepcopy(START)
    data[key] = value
    with pytest.raises(session.controller_half.PreconditionUnfit):
        session.parse_start(data)


@pytest.mark.parametrize(
    "run",
    [
        {"repository_id": 1, "run_id": 2},
        {"repository_id": 1, "run_id": 2, "run_attempt": 1, "extra": 1},
        {"repository_id": 0, "run_id": 2, "run_attempt": 1},
        {"repository_id": True, "run_id": 2, "run_attempt": 1},
        "not-a-mapping",
    ],
)
def test_the_run_binding_is_closed(run: Any) -> None:
    data = copy.deepcopy(START)
    data["execution_run"] = run
    with pytest.raises(session.controller_half.PreconditionUnfit):
        session.parse_start(data)


# ── the six launcher rows ───────────────────────────────────────────────────


def test_the_external_codes_are_exactly_items_eleven_to_sixteen() -> None:
    external = {item.code for item in REQUIRED_ITEMS if item.number >= 11}
    assert external == session.EXTERNAL_CODES
    assert len(session.EXTERNAL_CODES) == 6


def test_six_closed_rows_parse() -> None:
    parsed = session.parse_external_rows({"rows": _rows()})
    assert {row.code for row in parsed} == session.EXTERNAL_CODES
    assert all(row.status is RequirementStatus.EXECUTED_PASSED for row in parsed)


def test_a_failed_or_blocked_status_is_carried_not_judged() -> None:
    rows = _rows()
    rows[0] = _row(rows[0]["code"], status="executed_failed")
    rows[1] = _row(rows[1]["code"], status="blocked")
    parsed = {r.code: r.status for r in session.parse_external_rows({"rows": rows})}
    assert parsed[rows[0]["code"]] is RequirementStatus.EXECUTED_FAILED
    assert parsed[rows[1]["code"]] is RequirementStatus.BLOCKED


def test_a_missing_row_is_incomplete() -> None:
    with pytest.raises(session.controller_half.EvidenceIncomplete):
        session.parse_external_rows({"rows": _rows()[:-1]})


def test_a_seventh_row_is_incomplete() -> None:
    rows = [*_rows(), _row("digest_equality")]
    with pytest.raises(session.controller_half.EvidenceIncomplete):
        session.parse_external_rows({"rows": rows})


def test_rows_must_be_a_list() -> None:
    with pytest.raises(session.controller_half.EvidenceIncomplete):
        session.parse_external_rows({"rows": {}})


def test_a_row_for_an_item_this_half_owns_is_refused() -> None:
    rows = _rows()
    rows[0] = _row("digest_equality")
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


def test_a_duplicate_code_is_refused() -> None:
    rows = _rows()
    rows[1] = copy.deepcopy(rows[0])
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


@pytest.mark.parametrize(
    "override",
    [
        {"status": "passed"},
        {"status": 1},
        {"detail": "   "},
        {"detail": "x" * 5000},
        {"evidence": "probe:x"},
        {"evidence": [1]},
        {"evidence": ["x"] * 17},
        {"evidence": ["x" * 501]},
    ],
)
def test_a_malformed_row_is_refused(override: dict[str, Any]) -> None:
    rows = _rows()
    rows[0] = _row(rows[0]["code"], **override)
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


def test_a_row_with_an_extra_key_is_refused() -> None:
    rows = _rows()
    rows[0]["extra"] = 1
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


# ── base64 members ──────────────────────────────────────────────────────────


def test_base64_members_are_strict() -> None:
    assert session._decode_b64("YWJj", limit=10) == b"abc"
    for bad in ("", "not base64!", 12, "YWJj" * 10):
        with pytest.raises(session.controller_half.EvidenceUnreadable):
            session._decode_b64(bad, limit=10)


# ── static guards on the design ─────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_the_session_never_writes_to_stdout() -> None:
    tree = _tree()
    printed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    stdout = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "stdout"
    ]
    assert not printed
    assert not stdout


def test_the_session_never_writes_a_release_or_reaches_over_ssh() -> None:
    names = {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "write_release",
        "record_terminal",
        "build_release",
        "prove_principal",
        "_ssh_runner",
        "subprocess",
    ):
        assert forbidden not in names, forbidden


def test_require_build_effects_is_kept() -> None:
    calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "prepare_authorization"
    ]
    assert len(calls) == 1
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    value = keywords.get("require_build_effects")
    assert isinstance(value, ast.Constant) and value.value is True


def test_the_channel_leaves_stdout_before_anything_else_in_main() -> None:
    main = next(
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    sources = [ast.unparse(statement) for statement in main.body]
    taken = next(i for i, text in enumerate(sources) if "from_stdio" in text)
    received = next(i for i, text in enumerate(sources) if "Session(" in text)
    assert taken < received
    assert not any("receive" in text for text in sources[:taken])


def test_the_placement_check_precedes_the_grant() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert source.index("self._placement(") < source.index("establish_authorization(")
