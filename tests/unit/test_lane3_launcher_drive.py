"""E7: the launcher's protocol driver, against a scripted target over pipes.

The target here is a thread speaking the real framed protocol. The probe and
evidence collaborators are replaced by recorders, so what is pinned is the
launcher's ordering and its fail-closed handling: any refusal aborts the target
and reads its terminal report, and silence returns no terminal at all.
"""

from __future__ import annotations

import base64
import json
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
        "fixture_digest": "sha256:" + "f" * 64,
        "foundation_revision": "a" * 40,
        "foundation_artifact": "sha256:" + "c" * 64,
        "probe_vantage_ref": "vantage@1",
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


PLAN = "sha256:" + "d" * 64


def front(chan: Channel, seen: dict[str, Any], ready: dict[str, str] | None = None):
    """The real session's order up to the lock: start, ready, WAIT for proceed.

    Returns True only when the launcher said proceed for this plan. `lock_taken`
    is recorded only then, exactly where the real session takes the product lock
    and consumes the dispatch, so a test can prove a mismatch never gets there.
    """
    chan.receive("start", timeout=5)
    chan.send("ready", ready if ready is not None else _ready())
    kind, data = chan.receive("proceed", "abort", timeout=5)
    seen["gate_frame"] = kind
    if kind != "proceed" or data["plan_digest"] != PLAN:
        seen["refused_before_lock"] = True
        chan.send(
            "terminal",
            terminal_data(
                receipt_digest="",
                lease_in_hand=False,
                host_mutated=False,
                machine_id="",
            ),
        )
        return False
    seen["lock_taken"] = True
    return True


def test_a_clean_run_drives_every_phase_in_order(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert front(chan, seen)
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
    assert result.ready_seen is True
    assert result.plan_digest == PLAN
    assert seen["gate_frame"] == "proceed"
    assert seen["lock_taken"] is True
    assert seen["cipher"] == CIPHERTEXT
    assert len(seen["rows"]) == 6


def test_the_proceed_frame_names_the_plan_the_target_reported(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        seen["proceed"] = chan.receive("proceed", "abort", timeout=5)
        chan.send("terminal", terminal_data(receipt_digest=""))

    _run(ends, _inputs(tmp_path), script)
    assert seen["proceed"] == ("proceed", {"plan_digest": PLAN})


def test_a_target_on_another_host_never_reaches_the_lock(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert not front(chan, seen, _ready(host="host-b"))

    result = _run(ends, _inputs(tmp_path), script)
    assert seen["gate_frame"] == "abort"
    assert seen["refused_before_lock"] is True
    assert "lock_taken" not in seen
    assert result.refusal_code == "placement-mismatch"
    assert result.ready_seen is True
    assert result.terminal is not None
    assert result.receipt is None


def test_another_machine_never_reaches_the_lock(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert not front(chan, seen, _ready(machine="f" * 32))

    result = _run(ends, _inputs(tmp_path), script)
    assert "lock_taken" not in seen
    assert result.refusal_code == "placement-mismatch"


def test_an_empty_plan_digest_never_reaches_the_lock(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        ready = _ready()
        ready["plan_digest"] = ""
        assert not front(chan, seen, ready)

    result = _run(ends, _inputs(tmp_path), script)
    assert "lock_taken" not in seen
    assert result.refusal_code == "placement-mismatch"


def test_a_measurement_that_cannot_run_aborts_the_target(
    ends, tmp_path, monkeypatch
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise launcher.controller_half.EvidenceIncomplete("not measured")

    monkeypatch.setattr(launcher.launcher_probe, "measure_external_rows", broken)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert front(chan, seen)
        chan.send("window_open", {"deadline_seconds": 5})
        seen["kind"] = chan.receive("external_results", "abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest=""))

    result = _run(ends, _inputs(tmp_path), script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "probe-measurement-refused"
    assert result.terminal is not None


def test_the_launcher_encrypts_the_window_document_with_the_probes_in_it(
    ends, tmp_path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    def record(**kwargs: Any) -> bytes:
        captured.update(kwargs)
        return CIPHERTEXT

    monkeypatch.setattr(launcher.launcher_evidence, "finalize", record)

    def script(chan: Channel) -> None:
        assert front(chan, {})
        chan.send("window_open", {"deadline_seconds": 5})
        chan.receive("external_results", timeout=5)
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        chan.receive("evidence_ciphertext", timeout=5)
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    _run(ends, _inputs(tmp_path), script)
    document = json.loads(captured["probe_bytes"])
    assert document["probes"]["positive_v6"]["reachable"] is True
    assert "inside_probe" in document


def test_verify_receipt_is_handed_every_binding_the_launcher_holds(
    ends, tmp_path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    def record(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "VERIFIED"

    monkeypatch.setattr(launcher.launcher_evidence, "verify_receipt", record)

    def script(chan: Channel) -> None:
        assert front(chan, {})
        chan.send("window_open", {"deadline_seconds": 5})
        chan.receive("external_results", timeout=5)
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        chan.receive("evidence_ciphertext", timeout=5)
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    _run(ends, _inputs(tmp_path), script)
    assert captured["plan_digest"] == PLAN
    assert captured["foundation_revision"] == "a" * 40
    assert captured["fixture_digest"] == "sha256:" + "f" * 64
    assert captured["probe_vantage_ref"] == "vantage@1"
    assert {row["code"] for row in captured["sent_rows"]} == {
        "external_positive_v6",
        "external_negative_v6",
        "external_v4",
        "closed_port_behaviour",
        "private_from_source",
        "privileged_vantage_refused",
    }


def test_a_refused_evidence_hand_back_aborts_and_carries_its_code(
    ends, tmp_path, monkeypatch
) -> None:
    def refuse(**_: Any) -> bytes:
        raise launcher.launcher_evidence.EvidenceHandBackRefused(
            "exposure-calls-not-canonical"
        )

    monkeypatch.setattr(launcher.launcher_evidence, "finalize", refuse)

    def script(chan: Channel) -> None:
        assert front(chan, {})
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
            "external-rows-mismatch"
        )

    monkeypatch.setattr(launcher.launcher_evidence, "verify_receipt", refuse)

    def script(chan: Channel) -> None:
        assert front(chan, {})
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
    assert result.refusal_code == "external-rows-mismatch"
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
    assert result.ready_seen is False


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
    assert result.ready_seen is False


# ── target stderr: a bounded private file, never the launcher's output ──────


def test_target_stderr_goes_to_a_bounded_private_file_and_is_not_echoed(
    tmp_path, capfd
) -> None:
    argv = [
        sys.executable,
        "-c",
        "import sys\n"
        "sys.stderr.write('::notice::annotation ' + 'x' * 5000)\n"
        "sys.stderr.flush()\n",
    ]
    path = tmp_path / "stderr.log"
    process, channel, sink = launcher.open_ssh_channel(
        argv, stderr_path=path, stderr_limit=100
    )
    process.wait(timeout=30)
    sink.join()
    channel.close()
    process.stdin.close()
    process.stdout.close()
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "::notice::" not in captured.err
    assert path.stat().st_size == 100
    assert (path.stat().st_mode & 0o777) == 0o600
    assert sink.truncated is True
    assert path.read_bytes().startswith(b"::notice::annotation")


def test_a_small_stderr_is_kept_whole_and_not_marked_truncated(tmp_path, capfd) -> None:
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('short')"]
    path = tmp_path / "stderr.log"
    process, channel, sink = launcher.open_ssh_channel(
        argv, stderr_path=path, stderr_limit=1000
    )
    process.wait(timeout=30)
    sink.join()
    channel.close()
    process.stdin.close()
    process.stdout.close()
    assert path.read_bytes() == b"short"
    assert sink.truncated is False
    assert capfd.readouterr().err == ""


def test_the_stderr_file_is_create_only(tmp_path) -> None:
    path = tmp_path / "stderr.log"
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        launcher.open_ssh_channel(
            [sys.executable, "-c", "pass"], stderr_path=path, stderr_limit=10
        )
