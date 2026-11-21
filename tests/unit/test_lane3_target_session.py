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
    "proceed_seconds": 30,
    "store_time": "2026-11-17T05:00:00+00:00",
    "role": "positive",
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
        ("store_time", "not-a-time"),
        ("store_time", "2026-11-17T05:00:00"),
        ("store_time", ""),
        ("proceed_seconds", 0),
        ("proceed_seconds", 601),
        ("expected_machine_id", ""),
        ("expected_host_incarnation", ""),
        ("role", ""),
        ("role", "both"),
        ("role", "Positive"),
        ("role", 1),
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


def test_a_seventh_row_that_is_not_row_8_is_refused() -> None:
    rows = [*_rows(), _row("digest_equality")]
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


def _row8(**overrides: Any) -> dict[str, Any]:
    import lane3_negative_proof as nproof

    values: dict[str, Any] = {"evidence": [nproof.row_evidence(b"member")]}
    values.update(overrides)
    return _row(nproof.ROW_CODE, **values)


def test_row_8_is_accepted_as_a_seventh_row_by_shape_only() -> None:
    parsed = session.parse_external_rows({"rows": [*_rows(), _row8()]})
    assert len(parsed) == 7
    assert parsed[-1].code == "provoked_rollback"
    assert parsed[-1].status is RequirementStatus.EXECUTED_PASSED


@pytest.mark.parametrize(
    "override",
    [
        {"status": "blocked"},
        {"status": "executed_failed"},
        {"evidence": []},
        {"evidence": ["bundle:provoked_rollback"]},
        {"evidence": ["lane3-negative-compensation-proof:sha256:" + "z" * 64]},
    ],
)
def test_a_row_8_of_the_wrong_shape_is_refused(override: dict[str, Any]) -> None:
    rows = [*_rows(), _row8(**override)]
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": rows})


def test_row_8_replacing_a_launcher_row_is_incomplete() -> None:
    rows = [*_rows()[:-1], _row8()]
    with pytest.raises(session.controller_half.EvidenceIncomplete):
        session.parse_external_rows({"rows": rows})


def test_the_negative_role_never_receives_external_rows() -> None:
    with pytest.raises(session.controller_half.EvidenceUnreadable):
        session.parse_external_rows({"rows": _rows()}, role="negative")


def test_row_8_is_blocked_unless_the_launcher_supplied_it() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert source.count('"provoked_rollback"') == 0
    assert "negative_proof.ROW_CODE" in source
    blocked = source.index("BLOCKED,", source.index("negative_proof.ROW_CODE not in"))
    assert blocked > 0


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


# ── the placement gate blocks (H1) and the lease precedes the grant (H2) ────


def _gate(tmp_pairs):
    """A session whose launcher end is returned for scripting."""
    import os

    from lane3_session_channel import Channel

    to_target_read, to_target_write = os.pipe()
    to_launcher_read, to_launcher_write = os.pipe()
    launcher = Channel(to_launcher_read, to_target_write, role="launcher")
    target = Channel(to_target_read, to_launcher_write, role="target")
    tmp_pairs.extend([launcher, target])
    return launcher, session.Session(target)


@pytest.fixture
def gate():
    ends: list[Any] = []
    yield lambda: _gate(ends)
    for end in ends:
        end.close()


def _start(**overrides: Any):
    data = copy.deepcopy(START)
    data.update(overrides)
    return session.parse_start(data)


# ── S8b: the negative role's post-compensation window ──────────────────────


def _compensated_records() -> list[Any]:
    from lane3_exposure_recording import ExposureCallRecord

    return [
        ExposureCallRecord(0, "observe"),
        ExposureCallRecord(1, "restore_chains"),
        ExposureCallRecord(2, "observe"),
    ]


def test_the_window_opens_after_a_compensation_and_waits_for_done(gate) -> None:
    import threading

    launcher, live = gate()
    live.ctx.exposure_calls.extend(_compensated_records())
    seen: dict[str, Any] = {}

    def launcher_side() -> None:
        kind, data = launcher.receive("compensation_window_open", timeout=5)
        seen.update(kind=kind, data=data)
        launcher.send("compensation_probe_done", {})

    thread = threading.Thread(target=launcher_side)
    thread.start()
    live._compensation_window(_start(role="negative"))
    thread.join(5)
    assert seen["data"]["deadline_seconds"] == 300
    assert live.ctx.notes == [session.WINDOW_DONE_NOTE]


def test_the_window_is_not_opened_without_a_compensation(gate) -> None:
    launcher, live = gate()
    records = _compensated_records()
    live.ctx.exposure_calls.extend(records[:2])
    live._compensation_window(_start(role="negative"))
    assert live.ctx.notes == ["compensation-window-not-opened"]
    with pytest.raises(Exception):  # noqa: B017 - nothing was sent
        launcher.receive("compensation_window_open", timeout=0.2)


def test_an_abort_in_the_window_is_no_probe_and_does_not_raise(gate) -> None:
    launcher, live = gate()
    live.ctx.exposure_calls.extend(_compensated_records())
    launcher.send("abort", {"reason": "probe-measurement-refused"})
    live._compensation_window(_start(role="negative"))
    assert live.ctx.notes == ["compensation-window-aborted"]


def test_a_timeout_in_the_window_is_no_probe_and_does_not_raise(gate) -> None:
    _, live = gate()
    live.ctx.exposure_calls.extend(_compensated_records())
    live._compensation_window(_start(role="negative", window_seconds=1))
    assert live.ctx.notes == ["compensation-window-failed:channel-timeout"]


def test_the_window_is_driven_from_the_executor_failure_only_for_negative() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    window = source.index("self._compensation_window(start)")
    assert source.rindex('start.role == "negative"', 0, window) > source.rindex(
        "except DeploymentFoundationError", 0, window
    )
    # the original refusal is re-raised after the window, never swallowed
    assert source[window:].lstrip().splitlines()[1].strip() == "raise"


def test_a_proceed_for_this_plan_releases_the_gate(gate) -> None:
    launcher, live = gate()
    launcher.send("proceed", {"plan_digest": "sha256:plan"})
    live._await_proceed(_start(), "sha256:plan")


def test_an_abort_instead_of_proceed_is_a_clean_refusal(gate) -> None:
    launcher, live = gate()
    launcher.send("abort", {"reason": "placement-mismatch"})
    with pytest.raises(session.controller_half.PreconditionUnfit):
        live._await_proceed(_start(), "sha256:plan")
    assert live.ctx.host_mutated is False


def test_a_proceed_naming_another_plan_is_refused(gate) -> None:
    launcher, live = gate()
    launcher.send("proceed", {"plan_digest": "sha256:other"})
    with pytest.raises(session.controller_half.PreconditionUnfit):
        live._await_proceed(_start(), "sha256:plan")


def test_silence_is_a_refusal_not_a_hang(gate) -> None:
    _, live = gate()
    with pytest.raises(session.controller_half.PreconditionUnfit):
        live._await_proceed(_start(proceed_seconds=1), "sha256:plan")


def test_eof_is_a_refusal(gate) -> None:
    launcher, live = gate()
    launcher.close()
    with pytest.raises(session.controller_half.PreconditionUnfit):
        live._await_proceed(_start(), "sha256:plan")


def test_nothing_is_consumed_or_locked_before_the_launcher_says_proceed() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    proceed = source.index("self._await_proceed(")
    assert proceed < source.index("establish_authorization(")
    assert proceed < source.index("deployment_lock(")
    assert source.index('"ready"') < proceed


def test_the_lease_is_checked_before_the_dispatch_is_consumed() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    grant = source.index("establish_authorization(")
    assert source.index("load_lease(") < grant
    assert source.index(".covers(") < grant
    assert source.index("lease.controller_identity_fingerprint") < grant


def test_the_lease_is_held_in_the_terminal_context_only_after_the_grant() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert source.index("establish_authorization(") < source.index("ctx.lease = lease")


# ── U5: lease liveness is judged on store time, never this host's wall clock ──


def test_every_covers_call_reads_the_store_anchored_clock() -> None:
    calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "covers"
    ]
    assert len(calls) == 2
    for call in calls:
        now = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}["now"]
        assert now in {"clock.now()", "self.clock.now()"}, now


def test_the_session_does_not_read_the_wall_clock_for_a_liveness_decision() -> None:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    liveness = [ln for ln in lines if "covers(" in ln or "now=datetime" in ln]
    assert not any("datetime.now" in ln for ln in liveness)


def test_the_start_frames_store_time_is_parsed_as_an_aware_instant() -> None:
    start = session.parse_start(copy.deepcopy(START))
    assert start.store_time.tzinfo is not None
    assert start.store_time.isoformat() == "2026-11-17T05:00:00+00:00"


# ── LOW: a safety margin before expiry; ready carries the host incarnation ──


def _lease_expiring_in(seconds: float):
    import types
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 17, 5, 0, 0, tzinfo=UTC)
    lease = types.SimpleNamespace(
        expires_at=(now + timedelta(seconds=seconds)).isoformat()
    )
    return lease, now


def test_a_lease_with_the_full_margin_left_is_accepted() -> None:
    lease, now = _lease_expiring_in(session.LEASE_SAFETY_MARGIN.total_seconds())
    session.require_lease_margin(lease, now)


@pytest.mark.parametrize("seconds", [0, 1, 59.9, -5])
def test_a_lease_that_would_lapse_inside_the_margin_is_refused(seconds: float) -> None:
    lease, now = _lease_expiring_in(seconds)
    with pytest.raises(session.controller_half.PreconditionUnfit):
        session.require_lease_margin(lease, now)


def test_a_lease_with_an_unreadable_or_naive_expiry_is_refused() -> None:
    import types
    from datetime import UTC, datetime

    now = datetime(2026, 11, 17, 5, 0, 0, tzinfo=UTC)
    for expires in ("not a time", "2026-11-17T09:00:00", ""):
        with pytest.raises(session.controller_half.PreconditionUnfit):
            session.require_lease_margin(types.SimpleNamespace(expires_at=expires), now)


def test_the_margin_is_enforced_after_both_covers_checks() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert source.count("require_lease_margin(lease,") == 2
    first = source.index(".covers(")
    second = source.index(".covers(", first + 1)
    assert first < source.index("require_lease_margin(lease,") < second
    assert second < source.rindex("require_lease_margin(lease,")


def test_ready_carries_the_plans_host_incarnation() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"host_incarnation": str(execution_plan.host_incarnation)' in source
