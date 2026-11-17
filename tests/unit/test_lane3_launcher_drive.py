"""E7: the launcher's protocol driver, against a scripted target over pipes.

The target here is a thread speaking the real framed protocol. The probe and
evidence collaborators are replaced by recorders, so what is pinned is the
launcher's ordering and its fail-closed handling: any refusal aborts the target
and reads its terminal report, and silence returns no terminal at all.
"""

from __future__ import annotations

import base64
import os
import pathlib
import sys
import threading
from collections.abc import Callable
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher as launcher  # noqa: E402
import lane3_session_channel as channel_module  # noqa: E402
from dotmac_deployment_foundation.rehearsal import ExecutionRunBindingV1  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402

from tests.unit.test_lane3_launcher_lease import MACHINE, terminal_data  # noqa: E402
from tests.unit.test_lane3_launcher_probe import INPUTS, _measurers  # noqa: E402

Channel = channel_module.Channel
CIPHERTEXT = b"age-encryption.org/v1\nstand-in"


@pytest.fixture
def ends() -> Any:
    to_target_read, to_target_write = os.pipe()
    to_launcher_read, to_launcher_write = os.pipe()
    launcher_end = Channel(to_launcher_read, to_target_write, role="launcher")
    target_end = Channel(to_target_read, to_launcher_write, role="target")
    try:
        yield launcher_end, target_end
    finally:
        launcher_end.close()
        target_end.close()


def _inputs(tmp_path: pathlib.Path, **overrides: Any) -> launcher.DriveInputs:
    values: dict[str, Any] = {
        # The channel refuses a start frame that is not the closed key set, so
        # the test must send exactly that set (the values are not inspected).
        "start": dict.fromkeys(sorted(channel_module._SPEC["start"][1]), "x"),
        "expected_host_id": "host-a",
        "expected_machine_id": MACHINE,
        "execution_run": ExecutionRunBindingV1(1, 2, 1),
        "controller_identity": "SHA256:" + "A" * 43,
        "lease_id": "run-1",
        "probe_inputs": INPUTS,
        "qualified_evidence": {"vantage": {}},
        "probe_bytes": b'{"probe":true}',
        "encryption": object(),
        "evidence_output": tmp_path / "bundle.age",
        "ready_seconds": 5.0,
        "window_seconds": 5.0,
        "evidence_seconds": 5.0,
        "terminal_seconds": 5.0,
        "measurers": _measurers(),
    }
    values.update(overrides)
    return launcher.DriveInputs(**values)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _run(
    ends: Any, inputs: launcher.DriveInputs, script: Callable[[Channel], None]
) -> launcher.DriveResult:
    launcher_end, target_end = ends
    failures: list[BaseException] = []

    def target() -> None:
        try:
            script(target_end)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    result = launcher.drive(launcher_end, inputs)
    thread.join(timeout=10)
    assert not failures, failures
    return result


def _ready(host: str = "host-a", machine: str = MACHINE) -> dict[str, str]:
    return {"host_id": host, "machine_id": machine, "plan_digest": "sha256:" + "d" * 64}


@pytest.fixture(autouse=True)
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.launcher_evidence, "finalize", lambda **_: CIPHERTEXT)
    monkeypatch.setattr(
        launcher.launcher_evidence, "verify_receipt", lambda **_: "VERIFIED"
    )


def test_a_clean_run_drives_every_phase_in_order(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        chan.send("window_open", {"deadline_seconds": 5})
        _, rows = chan.receive("external_results", "abort", timeout=5)
        seen["rows"] = {row["code"] for row in rows["rows"]}
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        _, cipher = chan.receive("evidence_ciphertext", "abort", timeout=5)
        seen["cipher"] = base64.b64decode(cipher["ciphertext_b64"])
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    result = _run(ends, _inputs(tmp_path), script)
    assert result.refusal_code == ""
    assert result.receipt == "VERIFIED"
    assert result.terminal is not None
    assert seen["cipher"] == CIPHERTEXT
    assert len(seen["rows"]) == 6


def test_a_target_on_another_host_is_aborted_and_its_terminal_read(
    ends, tmp_path
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready(host="host-b"))
        seen["kind"] = chan.receive("abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest=""))

    result = _run(ends, _inputs(tmp_path), script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "placement-mismatch"
    assert result.terminal is not None
    assert result.receipt is None


def test_another_machine_is_refused_the_same_way(ends, tmp_path) -> None:
    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready(machine="f" * 32))
        chan.receive("abort", timeout=5)
        chan.send("terminal", terminal_data(receipt_digest=""))

    assert _run(ends, _inputs(tmp_path), script).refusal_code == "placement-mismatch"


def test_a_measurement_that_cannot_run_aborts_the_target(
    ends, tmp_path, monkeypatch
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise launcher.controller_half.EvidenceIncomplete("not measured")

    monkeypatch.setattr(launcher.launcher_probe, "measure_external_rows", broken)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        chan.send("window_open", {"deadline_seconds": 5})
        seen["kind"] = chan.receive("external_results", "abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest=""))

    result = _run(ends, _inputs(tmp_path), script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "probe-measurement-refused"
    assert result.terminal is not None


def test_a_refused_evidence_hand_back_aborts_and_carries_its_code(
    ends, tmp_path, monkeypatch
) -> None:
    def refuse(**_: Any) -> bytes:
        raise launcher.launcher_evidence.EvidenceHandBackRefused(
            "exposure-calls-not-canonical"
        )

    monkeypatch.setattr(launcher.launcher_evidence, "finalize", refuse)

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        chan.send("window_open", {"deadline_seconds": 5})
        chan.receive("external_results", timeout=5)
        chan.send("evidence_request", {"exposure_calls_b64": "AAAA"})
        assert chan.receive("abort", timeout=5)[0] == "abort"
        chan.send("terminal", terminal_data(receipt_digest=""))

    result = _run(ends, _inputs(tmp_path), script)
    assert result.refusal_code == "exposure-calls-not-canonical"
    assert result.receipt is None


def test_a_receipt_that_does_not_bind_is_recorded_but_the_terminal_is_still_read(
    ends, tmp_path, monkeypatch
) -> None:
    def refuse(**_: Any) -> Any:
        raise launcher.launcher_evidence.EvidenceHandBackRefused(
            "evidence-bundle-digest-mismatch"
        )

    monkeypatch.setattr(launcher.launcher_evidence, "verify_receipt", refuse)

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        chan.send("window_open", {"deadline_seconds": 5})
        chan.receive("external_results", timeout=5)
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        chan.receive("evidence_ciphertext", timeout=5)
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    result = _run(ends, _inputs(tmp_path), script)
    assert result.refusal_code == "evidence-bundle-digest-mismatch"
    assert result.receipt is None
    assert result.terminal is not None


def test_a_target_that_refuses_early_is_a_terminal_not_a_hang(ends, tmp_path) -> None:
    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("terminal", terminal_data(receipt_digest="", lease_in_hand=False))

    result = _run(ends, _inputs(tmp_path), script)
    assert result.terminal is not None
    assert result.terminal.receipt_digest == ""
    assert result.refusal_code == ""


def test_a_malformed_terminal_is_a_refusal_code_not_a_terminal(ends, tmp_path) -> None:
    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("terminal", terminal_data(refusal="made_up"))

    result = _run(ends, _inputs(tmp_path), script)
    assert result.terminal is None
    assert result.refusal_code == "terminal-refusal"


def test_silence_returns_no_terminal_so_the_caller_writes_no_release(
    ends, tmp_path
) -> None:
    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)

    result = _run(ends, _inputs(tmp_path, ready_seconds=0.2), script)
    assert result.terminal is None
    assert result.refusal_code == "channel-timeout"
