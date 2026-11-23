"""E7: the launcher's protocol driver, against a scripted target over pipes.

The target here is a thread speaking the real framed protocol. The probe and
evidence collaborators are replaced by recorders, so what is pinned is the
launcher's ordering and its fail-closed handling: any refusal aborts the target
and reads its terminal report, and silence returns no terminal at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import sys
import threading
import types
from collections.abc import Callable
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher as launcher  # noqa: E402
import lane3_session_channel as channel_module  # noqa: E402
from dotmac_deployment_foundation.rehearsal import ExecutionRunBindingV1  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402

from tests.unit import lane3_e10_support as e10_support  # noqa: E402
from tests.unit import test_lane3_launcher_stage as stage_tests  # noqa: E402
from tests.unit.lane3_store_support import EPOCH, FakeStore  # noqa: E402
from tests.unit.test_lane3_launcher_lease import MACHINE, terminal_data  # noqa: E402
from tests.unit.test_lane3_launcher_probe import INPUTS, _measurers  # noqa: E402

Channel = channel_module.Channel
RUN_DIR = stage_tests.RUN_DIR
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


def _plan(**overrides: Any) -> launcher.SessionPlan:
    values: dict[str, Any] = {
        "run_dir": RUN_DIR,
        "deploy_dir": "/srv/lane3",
        "lock_dir": "/var/lock/dotmac",
        "foundation_revision": "a" * 40,
        "candidate_source_revision": "b" * 40,
        "foundation_artifact": "sha256:" + "c" * 64,
        "candidate_version": "0.4.0a2",
        "probe_vantage_ref": "vantage@1",
        "execution_run": ExecutionRunBindingV1(1, 2, 1),
        "expected_machine_id": MACHINE,
        "timeout": 120,
        "window_seconds": 5,
        "evidence_seconds": 5,
        "proceed_seconds": 5,
    }
    values.update(overrides)
    return launcher.SessionPlan(**values)


def _default_receipt() -> Any:
    return e10_support.make_receipt(
        ssh_host_key_fingerprint=stage_tests.FINGERPRINT,
        vm_alias=stage_tests.ALIAS,
        machine_id=MACHINE,
    )


def _staged_run(
    context: Any,
    *,
    run_dir: str = RUN_DIR,
    host: str = "host-a",
    run: str = "run-1",
    receipt: Any = None,
) -> Any:
    """What `stage_artifacts` returns, for a context, without any SSH."""
    stage = launcher.lane3_stage
    digest = (receipt if receipt is not None else _default_receipt()).digest()
    files = (("execution-context.json", context.sha256),)
    return stage.StagedRun(
        run_dir=run_dir,
        host_id=host,
        run_id=run,
        receipt_digest=digest,
        files=files,
        _token=stage._staged_token(run_dir, host, run, digest, files),
    )


def _inputs(tmp_path: pathlib.Path, **overrides: Any) -> launcher.DriveInputs:
    store = overrides.pop("store", None) or FakeStore()
    # ONE receipt: the lease is taken under the very receipt drive is given.
    receipt = _default_receipt()
    held = overrides.pop("held", None) or store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=receipt,
    )
    context = stage_tests._file(
        json.dumps({"host_enrolment_ref": e10_support.ENROLMENT_UUID}).encode()
    )
    values: dict[str, Any] = {
        "pin": stage_tests._pin(tmp_path),
        "expected_host_incarnation": e10_support.INCARNATION_FINGERPRINT,
        "e10": receipt,
        "execution_context": context,
        "staged": _staged_run(context),
        "store": store,
        "held": held,
        "plan": _plan(),
        "probe_inputs": INPUTS,
        "qualified_evidence": {"vantage": {}},
        "fixture_digest": "sha256:" + "f" * 64,
        "encryption": object(),
        "evidence_output": tmp_path / "bundle.age",
        "ready_seconds": 5.0,
        "terminal_seconds": 5.0,
        "measurers": _measurers(),
    }
    if "execution_context" in overrides and "staged" not in overrides:
        # Unless a test says otherwise, the context under test IS the staged one.
        overrides["staged"] = _staged_run(overrides["execution_context"])
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


def _ready(
    host: str = "host-a",
    machine: str = MACHINE,
    incarnation: str | None = None,
) -> dict[str, str]:
    return {
        "host_id": host,
        "machine_id": machine,
        "host_incarnation": (
            e10_support.INCARNATION_FINGERPRINT if incarnation is None else incarnation
        ),
        "plan_digest": "sha256:" + "d" * 64,
    }


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


# ── U5: the store's time, and its covers(), gate the run ────────────────────


def test_the_start_frame_carries_the_stores_time_not_the_runners(
    ends, tmp_path
) -> None:
    store = FakeStore()
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        _, data = chan.receive("start", timeout=5)
        seen["store_time"] = data["store_time"]
        chan.send("terminal", terminal_data(receipt_digest=""))

    _run(ends, _inputs(tmp_path, store=store), script)
    assert seen["store_time"] == store.database_time().isoformat()


def test_a_store_that_cannot_supply_database_time_sends_nothing(ends, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    store = FakeStore(authority="runner")
    held = FakeStore().acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="x",
        ttl_seconds=60,
        e10_receipt=e10_support.make_receipt(),
    )
    result = _run(ends, _inputs(tmp_path, store=store, held=held), script)
    assert seen["no_start"] is True
    assert result.refusal_code == "store-lacks-database-time"
    assert result.terminal is None


def test_covers_is_judged_on_store_time_before_the_target_is_let_go(
    ends, tmp_path
) -> None:
    store = FakeStore()
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert front(chan, seen)
        # By the time the target reads `proceed`, the store has already judged it.
        seen["covers_before_proceed"] = list(store.covers_calls)
        chan.send("terminal", terminal_data(receipt_digest=""))

    _run(ends, _inputs(tmp_path, store=store), script)
    assert seen["covers_before_proceed"] == [("run-1", store.database_time())]


def test_a_lease_the_store_says_has_expired_aborts_the_target_before_the_lock(
    ends, tmp_path
) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        store.advance(10_000)  # the lease lapses on STORE time while it starts
        chan.send("ready", _ready())
        seen["kind"] = chan.receive("proceed", "abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest="", lease_in_hand=False))

    result = _run(ends, inputs, script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "lease-expired"
    assert result.ready_seen is True


# ── M3: identity is DERIVED from the pin and the lease, not a free dict ─────


def test_drive_inputs_have_no_free_start_dict_or_identity_fields() -> None:
    names = set(launcher.DriveInputs.__slots__)
    for derived in (
        "start",
        "expected_host_id",
        "controller_identity",
        "lease_id",
        "lease_dir",
        "execution_run",
        "authorization_run",
    ):
        assert derived not in names, derived
    assert {"pin", "held", "store", "plan", "staged"} <= names
    # ...except the incarnation, which is its own required typed input.
    assert {"expected_host_incarnation", "e10"} <= names


def test_the_start_frame_is_derived_from_the_lease_the_pin_and_the_plan(
    ends, tmp_path
) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        _, data = chan.receive("start", timeout=5)
        seen["start"] = data
        chan.send("terminal", terminal_data(receipt_digest=""))

    _run(ends, inputs, script)
    start = seen["start"]
    assert start["host_id"] == "host-a"
    assert start["authorization_run"] == "run-1"
    assert start["controller_identity"] == "SHA256:" + "A" * 43
    assert start["lease_dir"] == f"{RUN_DIR}/lease"
    assert start["descriptor"] == f"{RUN_DIR}/product.toml"
    assert start["authorization_document"] == f"{RUN_DIR}/authorization.json"
    assert start["execution_run"] == {
        "repository_id": 1,
        "run_id": 2,
        "run_attempt": 1,
    }
    # The incarnation is its OWN input (CP's `sha256:<hex>` of the incarnation
    # key); it is not the SSH pin's fingerprint and is not derived from it.
    assert start["expected_host_incarnation"] == e10_support.INCARNATION_FINGERPRINT
    assert start["expected_host_incarnation"] != stage_tests.FINGERPRINT
    assert start["store_time"] == store.database_time().isoformat()


@pytest.mark.parametrize(
    "incarnation",
    [
        "sha256:" + "0" * 64,  # another key
        stage_tests.FINGERPRINT,  # the SSH host key's fingerprint, NOT the incarnation
        "",
    ],
)
def test_a_ready_whose_incarnation_is_not_the_pinned_key_never_reaches_the_lock(
    ends, tmp_path, incarnation: str
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        assert not front(chan, seen, _ready(incarnation=incarnation))

    result = _run(ends, _inputs(tmp_path), script)
    assert "lock_taken" not in seen
    assert seen["gate_frame"] == "abort"
    assert result.refusal_code == "placement-mismatch"


# ── LOW: store_time is sanity-checked against the held lease's window ───────


def test_a_store_time_outside_the_lease_window_sends_nothing(ends, tmp_path) -> None:
    import dataclasses
    from datetime import timedelta

    store = FakeStore()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=e10_support.make_receipt(),
    )
    future = dataclasses.replace(
        held, acquired_at=store.database_time() + timedelta(hours=1)
    )
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, _inputs(tmp_path, store=store, held=future), script)
    assert seen["no_start"] is True
    assert result.refusal_code == "store-time-outside-lease-window"


def test_a_store_time_after_the_lease_expired_sends_nothing(ends, tmp_path) -> None:
    store = FakeStore()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=60,
        e10_receipt=e10_support.make_receipt(),
    )
    store.advance(120)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, _inputs(tmp_path, store=store, held=held), script)
    assert seen["no_start"] is True
    assert result.refusal_code == "store-time-outside-lease-window"


# ── M4: a terminal record for every outcome, create-only ────────────────────


class _Receipt:
    def sha256_digest(self) -> str:
        return DIGEST


DIGEST = "sha256:" + "a" * 64


def _conclude(tmp_path, result, store=None, *, held=None, name="out"):
    store = store or FakeStore()
    held = held or store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=e10_support.make_receipt(),
    )
    return launcher.conclude(
        result,
        store=store,
        held=held,
        controller_identity="SHA256:" + "A" * 43,
        expected_machine_id=MACHINE,
        release_args=object(),
        output_dir=tmp_path / name,
    )


def _evidence(tmp_path, name="out") -> dict[str, Any]:
    return json.loads((tmp_path / name / "lane3-terminal-evidence.json").read_text())


@pytest.fixture
def builds(monkeypatch):
    from tests.unit.lane3_store_support import release_for

    def fake(ctx, args, outcome):
        lease = ctx.lease
        return release_for(
            FakeStore(),
            types.SimpleNamespace(lease=lease),
        )

    monkeypatch.setattr(launcher.launcher_lease.controller_half, "build_release", fake)


def test_no_terminal_report_still_leaves_a_record_and_no_release(tmp_path) -> None:
    result = launcher.DriveResult(terminal=None, refusal_code="channel-timeout")
    conclusion = _conclude(tmp_path, result)
    assert conclusion.outcome == "no_terminal"
    assert conclusion.release_digest == ""
    evidence = _evidence(tmp_path)
    assert evidence["outcome"] == "no_terminal"
    assert evidence["drive_refusal_code"] == "channel-timeout"
    assert evidence["release_written"] is False
    assert evidence["lease_left_to_expire_as_mutated"] is True
    assert not (tmp_path / "out" / "lane3-lease-release.json").exists()


def test_a_report_the_launcher_will_not_reconcile_is_a_recorded_refusal(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),  # names a receipt nobody verified
        ready_seen=True,
        plan_digest=PLAN,
    )
    conclusion = _conclude(tmp_path, result)
    assert conclusion.outcome == "refused"
    assert conclusion.custody_code == "terminal-receipt-unverified"
    evidence = _evidence(tmp_path)
    assert evidence["outcome"] == "refused"
    assert evidence["custody_code"] == "terminal-receipt-unverified"
    assert evidence["release_written"] is False
    assert evidence["ready_seen"] is True
    assert evidence["host_mutated_recorded"] is True


def test_a_verified_receipt_leaves_the_release_and_the_evidence(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    store = FakeStore()
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),
        receipt=_Receipt(),
        ready_seen=True,
        plan_digest=PLAN,
    )
    conclusion = _conclude(tmp_path, result, store)
    assert conclusion.outcome == "released"
    assert conclusion.release_digest == "release-digest"
    assert {p.name for p in conclusion.paths} == {
        "lane3-lease-release.json",
        "lane3-terminal-evidence.json",
    }
    evidence = _evidence(tmp_path)
    assert evidence["outcome"] == "released"
    assert evidence["release_written"] is True
    assert evidence["receipt_digest"] == DIGEST
    # and the release went into the SAME store the lease was taken from
    assert store.standing("host-a").value == "released"


def test_a_target_report_naming_no_member_leaves_evidence_and_no_release(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data(receipt_digest="", lease_in_hand=False)),
        ready_seen=False,
    )
    conclusion = _conclude(tmp_path, result)
    assert conclusion.outcome == "no_release"
    evidence = _evidence(tmp_path)
    assert evidence["outcome"] == "no_release"
    assert evidence["target_reported"] is True
    assert evidence["lease_left_to_expire_as_mutated"] is True


def test_an_unexpected_failure_still_leaves_its_record_then_propagates(
    tmp_path, monkeypatch
) -> None:
    from lane3_launcher_lease import parse_terminal

    def boom(*_: Any) -> Any:
        raise RuntimeError("builder exploded")

    monkeypatch.setattr(launcher.launcher_lease.controller_half, "build_release", boom)
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()), receipt=_Receipt(), ready_seen=True
    )
    with pytest.raises(RuntimeError):
        _conclude(tmp_path, result)
    assert _evidence(tmp_path)["outcome"] == "error"


def test_the_terminal_records_are_create_only(tmp_path) -> None:
    result = launcher.DriveResult(terminal=None)
    _conclude(tmp_path, result)
    with pytest.raises(FileExistsError):
        _conclude(tmp_path, result)


def test_the_evidence_carries_no_secret_or_address_fields(tmp_path) -> None:
    result = launcher.DriveResult(terminal=None, ready_seen=True, plan_digest=PLAN)
    _conclude(tmp_path, result)
    assert set(_evidence(tmp_path)) == {
        "document",
        "version",
        "outcome",
        "drive_refusal_code",
        "custody_code",
        "release_written",
        "release_digest",
        "lease_left_to_expire_as_mutated",
        "store_failure",
        "standing_read_required",
        "ready_seen",
        "plan_digest",
        "receipt_digest",
        "target_reported",
        "target_refusal",
        "target_host_mutated",
        "host_mutated_recorded",
        "cleanup",
        "target_exit_status",
    }


# ── U3 (option B): the E10 receipt links the SSH pin and the incarnation ────


@pytest.mark.parametrize(
    ("override", "code"),
    [
        (
            {"ssh_host_key_fingerprint": "SHA256:" + "C" * 43},
            "receipt-ssh-fingerprint-mismatch",
        ),
        ({"vm_alias": "vm-beta"}, "receipt-alias-mismatch"),
        (
            {
                "incarnation_public_key_b64url": "A" * 43,
                "incarnation_fingerprint": "sha256:"
                + hashlib.sha256(bytes(32)).hexdigest(),
            },
            "receipt-incarnation-mismatch",
        ),
        ({"host_id": "host-b"}, "receipt-host-id-mismatch"),
    ],
)
def test_a_receipt_that_disagrees_with_an_input_sends_nothing(
    ends, tmp_path, override: dict[str, Any], code: str
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    receipt = e10_support.make_receipt(
        **{
            "ssh_host_key_fingerprint": stage_tests.FINGERPRINT,
            "vm_alias": stage_tests.ALIAS,
            "machine_id": MACHINE,
            **override,
        }
    )
    result = _run(ends, _inputs(tmp_path, e10=receipt), script)
    assert seen["no_start"] is True
    assert result.refusal_code == code
    assert result.ready_seen is False


def test_a_key_destroyed_after_the_store_time_sends_nothing(ends, tmp_path) -> None:
    from datetime import timedelta

    store = FakeStore()
    receipt = e10_support.make_receipt(
        ssh_host_key_fingerprint=stage_tests.FINGERPRINT,
        vm_alias=stage_tests.ALIAS,
        machine_id=MACHINE,
        incarnation_key_destroyed_at=store.database_time() + timedelta(minutes=5),
    )
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, _inputs(tmp_path, store=store, e10=receipt), script)
    assert seen["no_start"] is True
    assert result.refusal_code == "incarnation-key-not-destroyed-before-launch"


@pytest.mark.parametrize("value", ["", "SHA256:" + "A" * 43, "sha256:short"])
def test_a_malformed_expected_incarnation_sends_nothing(
    ends, tmp_path, value: str
) -> None:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, _inputs(tmp_path, expected_host_incarnation=value), script)
    assert seen["no_start"] is True
    assert result.refusal_code == "host-incarnation-required"


def test_ready_is_compared_with_the_incarnation_input_not_the_ssh_pin(
    ends, tmp_path
) -> None:
    """A target presenting the SSH host key's fingerprint as its incarnation is
    refused: the SSH key and the incarnation key are different keys."""
    seen: dict[str, Any] = {}

    def refusing(chan: Channel) -> None:
        assert not front(chan, seen, _ready(incarnation=stage_tests.FINGERPRINT))

    result = _run(ends, _inputs(tmp_path), refusing)
    assert "lock_taken" not in seen
    assert result.refusal_code == "placement-mismatch"


# ── third check: released_at in the uploaded copy is the STORE's ────────────


def test_the_uploaded_release_copy_carries_store_time_not_the_runners(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    store = FakeStore()
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),
        receipt=_Receipt(),
        ready_seen=True,
        plan_digest=PLAN,
    )
    # The runner's own stamp (what build_release wrote) is within the skew bound
    # but is not the store's.
    conclusion = _conclude(tmp_path, result, store)
    document = json.loads((tmp_path / "out" / "lane3-lease-release.json").read_text())
    assert document["released_at"] == store.released_at("host-a").isoformat()
    evidence = _evidence(tmp_path)
    assert evidence["store_released_at"] == store.released_at("host-a").isoformat()
    assert evidence["runner_released_at"] != ""
    assert conclusion.outcome == "released"


def test_a_runner_clock_stamp_never_reaches_the_uploaded_copy(
    tmp_path, monkeypatch
) -> None:
    from datetime import timedelta

    from lane3_launcher_lease import parse_terminal

    from tests.unit.lane3_store_support import release_for

    def skewed(ctx, args, outcome):
        lease = ctx.lease
        runner_stamp = (EPOCH + timedelta(seconds=30)).isoformat()
        return release_for(
            FakeStore(),
            types.SimpleNamespace(lease=lease),
            released_at=runner_stamp,
        )

    monkeypatch.setattr(
        launcher.launcher_lease.controller_half, "build_release", skewed
    )
    store = FakeStore()
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()), receipt=_Receipt(), ready_seen=True
    )
    _conclude(tmp_path, result, store)
    document = json.loads((tmp_path / "out" / "lane3-lease-release.json").read_text())
    assert document["released_at"] == store.database_time().isoformat()
    assert document["released_at"] != _evidence(tmp_path)["runner_released_at"]


# ── third check: a write failure never hides the original error ─────────────


def test_a_record_write_failure_keeps_the_original_error_and_notes_both(
    tmp_path, monkeypatch
) -> None:
    from lane3_launcher_lease import parse_terminal

    def boom(*_: Any) -> Any:
        raise RuntimeError("builder exploded")

    def disk_full(*_: Any, **__: Any) -> Any:
        raise OSError("no space left on device")

    monkeypatch.setattr(launcher.launcher_lease.controller_half, "build_release", boom)
    monkeypatch.setattr(launcher.launcher_lease, "write_terminal_records", disk_full)
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()), receipt=_Receipt(), ready_seen=True
    )
    with pytest.raises(RuntimeError) as failure:
        _conclude(tmp_path, result)
    assert "builder exploded" in str(failure.value)
    notes = "\n".join(failure.value.__notes__)
    assert "terminal records also failed" in notes
    assert "no space left on device" in notes


def test_a_record_write_failure_alone_propagates_as_itself(
    tmp_path, monkeypatch
) -> None:
    def disk_full(*_: Any, **__: Any) -> Any:
        raise OSError("no space left on device")

    monkeypatch.setattr(launcher.launcher_lease, "write_terminal_records", disk_full)
    with pytest.raises(OSError):
        _conclude(tmp_path, launcher.DriveResult(terminal=None))


# ── L1 / L2a: machine id and enrolment are checked before anything is sent ──


def _sends_nothing(ends, inputs) -> launcher.DriveResult:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, inputs, script)
    assert seen["no_start"] is True
    assert result.ready_seen is False
    return result


def test_a_receipt_whose_machine_id_is_not_the_plans_sends_nothing(
    ends, tmp_path
) -> None:
    result = _sends_nothing(
        ends, _inputs(tmp_path, plan=_plan(expected_machine_id="f" * 32))
    )
    assert result.refusal_code == "receipt-machine-id-mismatch"


def test_a_context_naming_another_enrolment_sends_nothing(ends, tmp_path) -> None:
    other = json.dumps(
        {"host_enrolment_ref": "11111111-2222-4333-8444-555555555555"}
    ).encode()
    result = _sends_nothing(
        ends, _inputs(tmp_path, execution_context=stage_tests._file(other))
    )
    assert result.refusal_code == "receipt-enrolment-mismatch"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"{}", "execution-context-enrolment-missing"),
        (b"not json", "execution-context-unreadable"),
    ],
)
def test_an_unusable_staged_context_sends_nothing(
    ends, tmp_path, content: bytes, code: str
) -> None:
    result = _sends_nothing(
        ends, _inputs(tmp_path, execution_context=stage_tests._file(content))
    )
    assert result.refusal_code == code


def test_the_enrolment_is_checked_against_the_context_that_is_staged() -> None:
    assert "execution_context" in launcher.DriveInputs.__slots__


# ── S5: the context checked is the file that was staged ─────────────────────


def _no_start(ends, tmp_path, **overrides) -> tuple[launcher.DriveResult, dict]:
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    return _run(ends, _inputs(tmp_path, **overrides), script), seen


def _context(**extra: Any) -> Any:
    document = {"host_enrolment_ref": e10_support.ENROLMENT_UUID, **extra}
    return stage_tests._file(json.dumps(document).encode())


def test_a_context_that_is_not_the_staged_file_sends_nothing(ends, tmp_path) -> None:
    result, seen = _no_start(ends, tmp_path, staged=_staged_run(_context(x=1)))
    assert seen["no_start"] is True
    assert result.refusal_code == "execution-context-not-the-staged-file"
    assert result.ready_seen is False


def test_a_context_that_was_never_staged_sends_nothing(ends, tmp_path) -> None:
    stage = launcher.lane3_stage
    empty = stage.StagedRun(
        run_dir=RUN_DIR,
        host_id="host-a",
        run_id="run-1",
        receipt_digest=_default_receipt().digest(),
        files=(),
        _token=stage._staged_token(
            RUN_DIR, "host-a", "run-1", _default_receipt().digest(), ()
        ),
    )
    result, seen = _no_start(ends, tmp_path, staged=empty)
    assert seen["no_start"] is True
    assert result.refusal_code == "execution-context-not-staged"


def test_a_context_whose_bytes_do_not_hash_to_its_digest_sends_nothing(
    ends, tmp_path
) -> None:
    good = _context()
    forged = launcher.lane3_stage.StagedFile(data=b"{}", sha256=good.sha256)
    result, seen = _no_start(
        ends, tmp_path, execution_context=forged, staged=_staged_run(good)
    )
    assert seen["no_start"] is True
    assert result.refusal_code == "execution-context-digest-mismatch"


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda c: _staged_run(c, host="host-b"), "staged-run-not-this-lease"),
        (lambda c: _staged_run(c, run="run-9"), "staged-run-not-this-lease"),
        (
            lambda c: _staged_run(c, run_dir="/run/lane3.OTHERRUN12"),
            "staged-run-not-this-lease",
        ),
        (lambda c: {"execution-context.json": c}, "staged-run-required"),
        (lambda c: None, "staged-run-required"),
    ],
)
def test_a_staging_that_is_not_this_leases_sends_nothing(
    ends, tmp_path, make, code: str
) -> None:
    context = _context()
    result, seen = _no_start(
        ends, tmp_path, execution_context=context, staged=make(context)
    )
    assert seen["no_start"] is True
    assert result.refusal_code == code


def test_a_forged_staged_run_sends_nothing(ends, tmp_path) -> None:
    context = _context()
    staged = _staged_run(context)
    object.__setattr__(
        staged, "files", (("execution-context.json", _context(x=2).sha256),)
    )
    result, seen = _no_start(ends, tmp_path, execution_context=context, staged=staged)
    assert seen["no_start"] is True
    assert result.refusal_code == "staged-run-forged"


def test_a_receipt_that_is_not_the_one_the_lease_was_taken_under_sends_nothing(
    ends, tmp_path
) -> None:
    # Agrees with the pin, alias, host, machine and incarnation; only its ref differs.
    other = e10_support.make_receipt(
        ssh_host_key_fingerprint=stage_tests.FINGERPRINT,
        vm_alias=stage_tests.ALIAS,
        machine_id=MACHINE,
        receipt_ref="e10-receipt-0099",
    )
    result, seen = _no_start(ends, tmp_path, e10=other)
    assert seen["no_start"] is True
    assert result.refusal_code == "receipt-not-the-leases"
    assert result.ready_seen is False


# ── S5: a dropped store maps to the fail-closed paths of design 3.6 ─────────


def test_a_store_that_drops_before_start_sends_nothing(ends, tmp_path) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    store.sever("before_write")
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        try:
            chan.receive("start", timeout=0.3)
        except channel_module.ChannelTimeout:
            seen["no_start"] = True

    result = _run(ends, inputs, script)
    assert seen["no_start"] is True
    # its OWN code, not "no database time"
    assert result.refusal_code == "store-unreachable"
    assert result.store_failure == "store-unreachable"
    assert result.terminal is None and result.ready_seen is False


def test_a_store_that_drops_before_proceed_aborts_the_target(ends, tmp_path) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    seen: dict[str, Any] = {}

    def script(chan: Channel) -> None:
        chan.receive("start", timeout=5)
        chan.send("ready", _ready())
        store.sever("before_write")  # gone before the launcher judges `covers`
        seen["kind"] = chan.receive("proceed", "abort", timeout=5)[0]
        chan.send("terminal", terminal_data(receipt_digest="", lease_in_hand=False))

    result = _run(ends, inputs, script)
    assert seen["kind"] == "abort"
    assert result.refusal_code == "store-unreachable"
    assert result.store_failure == "store-unreachable"
    assert result.ready_seen is True
    assert result.terminal is not None


def _full_run(ends, inputs, *, after_proceed) -> launcher.DriveResult:
    def script(chan: Channel) -> None:
        assert front(chan, {})
        after_proceed()
        chan.send("window_open", {"deadline_seconds": 5})
        chan.receive("external_results", "abort", timeout=5)
        chan.send(
            "evidence_request",
            {"exposure_calls_b64": _b64(serialize_exposure_calls(()))},
        )
        chan.receive("evidence_ciphertext", "abort", timeout=5)
        chan.send("receipt", {"receipt_b64": "x", "receipt_digest": "y"})
        chan.send("terminal", terminal_data())

    return _run(ends, inputs, script)


def test_a_store_that_drops_after_proceed_does_not_stop_the_run(
    ends, tmp_path, builds
) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    result = _full_run(ends, inputs, after_proceed=lambda: store.sever("before_write"))
    # the target needs no store: the run completes and the evidence is produced
    assert result.refusal_code == ""
    assert result.receipt == "VERIFIED"
    assert result.ciphertext == CIPHERTEXT
    # ...but the release write meets the dead store: sent once, never retried
    result.receipt = _Receipt()  # the stand-in digest the terminal names
    writes_before = store.backing.write_attempts
    conclusion = _conclude(tmp_path, result, store, held=inputs.held)
    assert conclusion.outcome == "store_unreachable"
    assert conclusion.release_digest == ""
    assert store.backing.write_attempts == writes_before
    evidence = _evidence(tmp_path)
    assert evidence["store_failure"] == "store-unreachable"
    assert evidence["release_written"] is False
    assert evidence["lease_left_to_expire_as_mutated"] is True
    assert evidence["standing_read_required"] is False
    assert not (tmp_path / "out" / "lane3-lease-release.json").exists()


def test_a_commit_that_is_unknown_is_reported_and_standing_is_read_later(
    ends, tmp_path, builds
) -> None:
    store = FakeStore()
    inputs = _inputs(tmp_path, store=store)
    result = _full_run(ends, inputs, after_proceed=lambda: None)
    result.receipt = _Receipt()
    store.sever("after_commit")
    conclusion = _conclude(tmp_path, result, store, held=inputs.held)
    assert conclusion.outcome == "store_commit_unknown"
    assert conclusion.custody_code == "store-commit-unknown"
    # exactly one write was sent; nothing further is written
    assert len(store.releases) == 1
    assert {p.name for p in conclusion.paths} == {
        "lane3-lease-release-unconfirmed.json",
        "lane3-terminal-evidence.json",
    }
    assert not (tmp_path / "out" / "lane3-lease-release.json").exists()
    copy = json.loads(
        (tmp_path / "out" / "lane3-lease-release-unconfirmed.json").read_text()
    )
    assert copy["unconfirmed"] is True and "release" in copy
    evidence = _evidence(tmp_path)
    assert evidence["store_failure"] == "store-commit-unknown"
    assert evidence["standing_read_required"] is True
    # unknown, not "false": the commit may have happened
    assert evidence["release_written"] is None
    assert evidence["lease_left_to_expire_as_mutated"] is None
    assert evidence["release_digest"] == ""


def test_conclude_sends_nothing_to_a_store_that_already_dropped(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    store = FakeStore()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=e10_support.make_receipt(),
    )
    writes = store.backing.write_attempts
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),
        receipt=_Receipt(),
        ready_seen=True,
        plan_digest=PLAN,
        store_failure="store-commit-unknown",
    )
    conclusion = _conclude(tmp_path, result, store, held=held)
    assert conclusion.outcome == "store_commit_unknown"
    assert store.backing.write_attempts == writes
    assert not store.releases
    assert {p.name for p in conclusion.paths} == {"lane3-terminal-evidence.json"}
    evidence = _evidence(tmp_path)
    assert evidence["store_failure"] == "store-commit-unknown"
    assert evidence["standing_read_required"] is True
    assert evidence["release_written"] is False


def test_a_drop_after_the_release_committed_still_leaves_every_record(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal
    from lane3_lease_store import StoreRefused

    class Drops(FakeStore):
        def released_at(self, host_id):
            raise StoreRefused("store-unreachable")

    store = Drops()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=e10_support.make_receipt(),
    )
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),
        receipt=_Receipt(),
        ready_seen=True,
        plan_digest=PLAN,
    )
    conclusion = _conclude(tmp_path, result, store, held=held)
    assert conclusion.outcome == "released"
    assert {p.name for p in conclusion.paths} == {
        "lane3-lease-release.json",
        "lane3-terminal-evidence.json",
    }
    evidence = _evidence(tmp_path)
    assert evidence["release_written"] is True
    assert evidence["store_failure"] == "store-unreachable"
    assert evidence["standing_read_required"] is True
    assert evidence["store_released_at"] == ""


def test_a_receipt_with_no_vm_slot_sends_nothing(ends, tmp_path) -> None:
    import dataclasses

    receipt = e10_support.make_receipt(
        ssh_host_key_fingerprint=stage_tests.FINGERPRINT,
        vm_alias=stage_tests.ALIAS,
        machine_id=MACHINE,
    )
    receipt = dataclasses.replace(receipt, vm_slot=None)
    result, seen = _no_start(ends, tmp_path, e10=receipt)
    assert seen["no_start"] is True
    assert result.refusal_code == "receipt-vm-slot-required"


def test_a_staging_that_checked_another_receipt_sends_nothing(ends, tmp_path) -> None:
    # Same pin, alias, host and machine; a different receipt (its ref differs).
    other = e10_support.make_receipt(
        ssh_host_key_fingerprint=stage_tests.FINGERPRINT,
        vm_alias=stage_tests.ALIAS,
        machine_id=MACHINE,
        receipt_ref="e10-receipt-0099",
    )
    context = _context()
    result, seen = _no_start(
        ends,
        tmp_path,
        execution_context=context,
        staged=_staged_run(context, receipt=other),
    )
    assert seen["no_start"] is True
    assert result.refusal_code == "receipt-not-the-stagings"


def test_a_non_store_failure_reading_the_release_stamp_propagates_after_the_evidence(
    tmp_path, builds
) -> None:
    from lane3_launcher_lease import parse_terminal

    class Bug(FakeStore):
        def released_at(self, host_id):
            raise RuntimeError("a code bug, not a store drop")

    store = Bug()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="SHA256:" + "A" * 43,
        ttl_seconds=600,
        e10_receipt=e10_support.make_receipt(),
    )
    result = launcher.DriveResult(
        terminal=parse_terminal(terminal_data()),
        receipt=_Receipt(),
        ready_seen=True,
        plan_digest=PLAN,
    )
    with pytest.raises(RuntimeError):
        _conclude(tmp_path, result, store, held=held)
    # not mislabelled as a store drop, and the evidence is still on record
    evidence = _evidence(tmp_path)
    assert evidence["outcome"] == "error"
    assert evidence["store_failure"] == ""
    assert evidence["standing_read_required"] is False


def test_the_evidence_survives_a_failure_building_the_unconfirmed_copy(
    tmp_path,
) -> None:
    import lane3_launcher_lease as lease_module

    class Broken:
        def as_document(self):
            raise ValueError("cannot serialise")

    with pytest.raises(ValueError):
        lease_module.write_terminal_records(
            tmp_path / "out",
            evidence={"document": "x"},
            release_record=None,
            unconfirmed_record=Broken(),
        )
    assert (tmp_path / "out" / "lane3-terminal-evidence.json").exists()
