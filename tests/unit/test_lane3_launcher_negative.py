# ruff: noqa: F811
"""S8b: the launcher's drive of the NEGATIVE target and the positive row 8 hand-in.

A scripted target over pipes speaks the real framed protocol; the probe
measurers are injected. Refusal cases: a probe timeout, an abort, a measurement
that cannot run, no window, a role/pair mismatch, and a proof the launcher will
not assert.
"""

from __future__ import annotations

import base64
import threading
from typing import Any

from tests.unit import lane3_negative_support as nsupport
from tests.unit.lane3_negative_support import nproof, serialize_exposure_calls
from tests.unit.test_lane3_launcher_drive import (
    CIPHERTEXT,
    PLAN,
    _inputs,
    _plan,
    _run,
    ends,  # noqa: F401  (fixture)
    front,
    launcher,
    launcher_negative,
    stand_ins,  # noqa: F401  (autouse fixture)
)
from tests.unit.test_lane3_launcher_lease import terminal_data
from tests.unit.test_lane3_launcher_probe import _measurers

CLAIMS = launcher_negative.NegativeClaims(
    positive_dispatch_id="11111111-1111-4111-8111-111111111111",
    dispatch_id="22222222-2222-4222-8222-222222222222",
    target_id="target-n",
    execution_sequence=1,
    attempt_no=1,
)
REFUSED = terminal_data(
    receipt_digest="",
    refusal="host_state_uncertified",
    exit_status=1,
    notes=["compensation-window-done"],
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _negative_inputs(tmp_path: Any, **overrides: Any) -> Any:
    return _inputs(
        tmp_path,
        plan=_plan(role="negative"),
        pair=CLAIMS,
        measurers=_measurers(),
        **overrides,
    )


def _window(chan: Any, deadline: int = 5) -> None:
    chan.send(
        "compensation_window_open",
        {
            "deadline_seconds": deadline,
            "exposure_calls_b64": _b64(serialize_exposure_calls(())),
        },
    )


def test_a_negative_run_yields_the_proof_from_the_launchers_own_bytes(
    ends,
    tmp_path,
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        assert front(chan, seen)
        _window(chan)
        seen["answer"] = chan.receive("compensation_probe_done", "abort", timeout=5)
        chan.send("terminal", REFUSED)

    result = _run(ends, _negative_inputs(tmp_path), script)
    assert seen["answer"] == ("compensation_probe_done", {})
    assert result.refusal_code == ""
    proof = nproof.parse_proof(result.negative_proof)
    assert proof.dispatch_id == CLAIMS.dispatch_id
    assert proof.terminal.refusal == nproof.NEGATIVE_TERMINAL_REFUSAL
    assert proof.terminal.host_mutated is True
    assert proof.probe.dispatch_id == CLAIMS.dispatch_id
    assert [row.status for row in proof.probe.rows] == ["executed_passed"] * 2


def test_the_negative_start_frame_names_its_role(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        _, data = chan.receive("start", timeout=5)
        seen["role"] = data["role"]
        chan.send(
            "terminal",
            terminal_data(receipt_digest="", lease_in_hand=False, host_mutated=False),
        )

    _run(ends, _negative_inputs(tmp_path), script)
    assert seen["role"] == "negative"


def test_a_probe_that_overran_the_window_is_no_probe(ends, tmp_path) -> None:
    launcher_end, target_end = ends
    ticks = iter([0.0, 100.0])
    seen: dict[str, Any] = {}

    def script() -> None:
        _window(target_end, deadline=5)
        seen["answer"] = target_end.receive(
            "compensation_probe_done", "abort", timeout=5
        )[0]
        target_end.send("terminal", REFUSED)

    thread = threading.Thread(target=script, daemon=True)
    thread.start()
    result = launcher.DriveResult(terminal=None)
    launcher_negative.drive_negative(
        launcher_end,
        _negative_inputs(tmp_path),
        result,
        monotonic=lambda: next(ticks),
    )
    thread.join(10)
    assert seen["answer"] == "abort"
    assert result.negative_proof is None
    assert result.refusal_code == "compensation-window-timeout"
    assert result.terminal is not None


def test_a_measurement_that_cannot_run_aborts_and_yields_no_proof(
    ends,
    tmp_path,
    monkeypatch,
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise launcher.controller_half.EvidenceIncomplete("not measured")

    monkeypatch.setattr(launcher.launcher_probe, "measure_post_compensation", broken)
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        assert front(chan, seen)
        _window(chan)
        seen["answer"] = chan.receive("compensation_probe_done", "abort", timeout=5)[0]
        chan.send("terminal", REFUSED)

    result = _run(ends, _negative_inputs(tmp_path), script)
    assert seen["answer"] == "abort"
    assert result.negative_proof is None
    assert result.refusal_code == "probe-measurement-refused"


def test_no_window_means_no_proof(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        assert front(chan, seen)
        chan.send("terminal", REFUSED)

    result = _run(ends, _negative_inputs(tmp_path), script)
    assert result.negative_proof is None
    assert result.refusal_code == "no-compensation-window"
    assert result.terminal is not None


def test_a_malformed_window_aborts(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        assert front(chan, seen)
        chan.send(
            "compensation_window_open",
            {"deadline_seconds": 0, "exposure_calls_b64": "not base64!"},
        )
        seen["answer"] = chan.receive("compensation_probe_done", "abort", timeout=5)[0]
        chan.send("terminal", REFUSED)

    result = _run(ends, _negative_inputs(tmp_path), script)
    assert seen["answer"] == "abort"
    assert result.negative_proof is None
    assert result.refusal_code == "compensation-window-invalid"


def test_the_role_and_the_pair_must_agree(ends, tmp_path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    result = _run(
        ends,
        _inputs(first, plan=_plan(role="negative")),  # a PositivePair
        lambda _chan: None,
    )
    assert result.refusal_code == "role-pair-mismatch"
    result = _run(
        ends,
        _inputs(second, pair=CLAIMS),  # positive role, negative claims
        lambda _chan: None,
    )
    assert result.refusal_code == "role-pair-mismatch"


# ── the positive drive takes row 8 from the proof ───────────────────────────


def _positive_script(seen: dict[str, Any]) -> Any:
    def script(chan: Any) -> None:
        assert front(chan, seen)
        chan.send("window_open", {"deadline_seconds": 5})
        _, rows = chan.receive("external_results", "abort", timeout=5)
        seen["rows"] = rows["rows"]
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        chan.receive("evidence_ciphertext", "abort", timeout=5)
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    return script


def test_the_positive_drive_asserts_row_8_and_encrypts_the_proof(
    ends,
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def record(**kwargs: Any) -> bytes:
        captured.update(kwargs)
        return CIPHERTEXT

    monkeypatch.setattr(launcher.launcher_evidence, "finalize", record)
    proof = nsupport.proof_bytes()
    seen: dict[str, Any] = {}
    inputs = _inputs(tmp_path, pair=launcher_negative.PositivePair(proof))
    result = _run(ends, inputs, _positive_script(seen))
    assert result.refusal_code == ""
    assert len(seen["rows"]) == 7
    row8 = seen["rows"][-1]
    assert row8["code"] == "provoked_rollback"
    assert row8["evidence"] == [nproof.row_evidence(proof)]
    assert captured["negative_compensation"] == proof


def test_without_a_proof_row_8_is_absent_and_the_bundle_cannot_be_built(
    ends,
    tmp_path,
) -> None:
    seen: dict[str, Any] = {}
    _run(ends, _inputs(tmp_path), _positive_script(seen))
    assert len(seen["rows"]) == 6


def test_a_proof_the_launcher_cannot_parse_is_never_asserted(
    ends,
    tmp_path,
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Any) -> None:
        assert front(chan, seen)
        chan.send("window_open", {"deadline_seconds": 5})
        seen["kind"] = chan.receive("external_results", "abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest=""))

    inputs = _inputs(tmp_path, pair=launcher_negative.PositivePair(b"{}"))
    result = _run(ends, inputs, script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "negative-proof-invalid"
    assert PLAN


# ── M1: the safety margin and the acknowledgement ───────────────────────────


def _timed_drive(ends, tmp_path, elapsed: float, *, terminal=REFUSED):
    launcher_end, target_end = ends
    ticks = iter([0.0, elapsed])
    seen: dict[str, Any] = {}

    def script() -> None:
        _window(target_end, deadline=5)
        seen["answer"] = target_end.receive(
            "compensation_probe_done", "abort", timeout=5
        )[0]
        target_end.send("terminal", terminal)

    thread = threading.Thread(target=script, daemon=True)
    thread.start()
    result = launcher.DriveResult(terminal=None)
    launcher_negative.drive_negative(
        launcher_end,
        _negative_inputs(tmp_path),
        result,
        monotonic=lambda: next(ticks),
    )
    thread.join(10)
    return result, seen


def test_a_probe_within_the_margin_is_sent_done(ends, tmp_path) -> None:
    result, seen = _timed_drive(ends, tmp_path, elapsed=4.4)
    assert seen["answer"] == "compensation_probe_done"
    assert nproof.parse_proof(result.negative_proof).terminal.window_done is True


def test_a_late_probe_inside_the_deadline_but_past_the_margin_is_aborted(
    ends, tmp_path
) -> None:
    assert launcher_negative.DEADLINE_MARGIN == 0.9
    result, seen = _timed_drive(ends, tmp_path, elapsed=4.6)  # 5s deadline
    assert seen["answer"] == "abort"
    assert result.negative_proof is None
    assert result.refusal_code == "compensation-window-timeout"


def test_a_terminal_without_the_acknowledgement_yields_an_unacknowledged_proof(
    ends, tmp_path
) -> None:
    silent = terminal_data(
        receipt_digest="", refusal="host_state_uncertified", exit_status=1
    )
    result, _ = _timed_drive(ends, tmp_path, elapsed=1.0, terminal=silent)
    assert nproof.parse_proof(result.negative_proof).terminal.window_done is False


def test_the_acknowledgement_note_is_one_string_in_both_modules() -> None:
    import lane3_target_session as session

    assert launcher_negative.WINDOW_DONE_NOTE == session.WINDOW_DONE_NOTE
