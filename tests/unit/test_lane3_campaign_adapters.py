"""Canonical campaign adapters: synthetic contracts, executed only by hosted CI."""

from __future__ import annotations

import contextlib
import importlib
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))


def adapter(name):
    return importlib.import_module("lane3_campaign_" + name)


def public_record():
    return {
        **dict.fromkeys(adapter("composition").REQUIRED_CLEANUP, True),
        "temporary_roles_removed": 4,
        "certificate_count": 7,
        "packet": "B8-scoped-issuer",
        "matrix_executed": False,
        "breakglass_cleanup_pending": False,
    }


def campaign(failure=None, receipt=None):
    events = []

    def step(name, value=None):
        def call(*args, **kwargs):
            events.append(name)
            if failure == name:
                raise RuntimeError("synthetic-stage-failure")
            return value

        return call

    client = SimpleNamespace(
        initialize_closed=step("client.initialize"),
        prepare=step("client.prepare", {"public_keys": {}}),
        remaining=step("client.remaining", 100),
        install_certificates=step("client.install"),
        execute=step("client.execute", {"classification": "synthetic"}),
        cleanup=step(
            "client.cleanup",
            receipt
            if receipt is not None
            else {"resource_closed": True, "material_cap": "ATTESTED"},
        ),
        window=SimpleNamespace(execution_deadline_ns=123456789),
    )
    transport = SimpleNamespace(
        initialize_closed=step("transport.initialize"),
        open=step("transport.open"),
        contain=step("transport.contain"),
        cleanup=step("transport.cleanup"),
    )
    issued = {
        "public_record": public_record(),
        "certificates": {},
        "valid_until_epoch": 1000,
        "breakglass_closed": True,
    }
    arguments = {
        "client": client,
        "transport": transport,
        "owned_identity": SimpleNamespace(cleanup=step("identity.cleanup")),
        "issuer": step("issuer", issued),
        "write_execution_deadline": step("deadline.write"),
        "principal_input": {"synthetic": True},
    }
    return events, arguments, issued


@pytest.mark.parametrize(
    "failure",
    [
        "transport.initialize",
        "client.initialize",
        "client.prepare",
        "client.remaining",
        "issuer",
        "client.install",
        "transport.open",
        "deadline.write",
        "client.execute",
        "transport.contain",
        "client.cleanup",
        "transport.cleanup",
        "identity.cleanup",
    ],
)
def test_every_campaign_failure_attempts_all_later_cleanup_in_order(failure):
    events, arguments, _ = campaign(failure)
    with pytest.raises(RuntimeError, match="synthetic-stage-failure"):
        adapter("composition").run_b8(**arguments)
    assert events[-4:] == [
        "transport.contain",
        "client.cleanup",
        "transport.cleanup",
        "identity.cleanup",
    ]
    assert "client.execute" not in events or failure in {
        "client.execute",
        "transport.contain",
        "client.cleanup",
        "transport.cleanup",
        "identity.cleanup",
    }


@pytest.mark.parametrize(
    "receipt",
    [
        {"resource_closed": True, "material_cap": "UNPROVEN"},
        {"resource_closed": 1, "material_cap": "ATTESTED"},
        {"resource_closed": True, "material_cap": "ATTESTED", "extra": True},
        None,
    ],
)
def test_material_closure_requires_exact_attested_receipt(receipt):
    events, arguments, _ = campaign()
    arguments["client"].cleanup = lambda: receipt
    with pytest.raises(RuntimeError, match="client_material_cap_unproven"):
        adapter("composition").run_b8(**arguments)
    assert events[-2:] == ["transport.cleanup", "identity.cleanup"]


def test_complete_campaign_preserves_absolute_deadline_and_matrix_result():
    events, arguments, _ = campaign()
    writes = []
    arguments["write_execution_deadline"] = lambda client, data: writes.append(
        json.loads(data)
    )
    result = adapter("composition").run_b8(**arguments)
    assert writes == [{"execution_deadline_ns": 123456789}]
    assert result == {
        "matrix_result": {"classification": "synthetic"},
        "full_b8_complete": False,
        "admission_changed": False,
    }
    assert (
        events.index("client.install")
        < events.index("transport.open")
        < events.index("client.execute")
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("temporary_policy_removed", 1),
        ("temporary_roles_removed", True),
        ("certificate_count", True),
        ("breakglass_closed", 1),
    ],
)
def test_issuer_near_miss_never_opens_transport(field, value):
    events, arguments, issued = campaign()
    target = issued if field == "breakglass_closed" else issued["public_record"]
    target[field] = value
    with pytest.raises(RuntimeError, match="issuer_cleanup_unproved"):
        adapter("composition").run_b8(**arguments)
    assert "transport.open" not in events and "client.execute" not in events


@pytest.mark.parametrize("module", ["bridge", "remote"])
@pytest.mark.parametrize("raw", [b'{"op":"retain","op":"finish"}', b"[]", b"null"])
def test_protocol_rejects_duplicate_keys_and_nonobjects(module, raw):
    with pytest.raises(RuntimeError):
        adapter(module).decode(raw)


def bridge_fixture(monkeypatch):
    module = adapter("bridge")
    bridge = object.__new__(module.Bridge)
    calls = []
    bridge.receipt = None
    bridge.phase = "initial"
    bridge.finish_attempted = False
    bridge.original_deadline_ns = 1000
    bridge.backend = SimpleNamespace(clock_ns=lambda: 100)
    receipt = SimpleNamespace(deadline_ns=900, expiry_ns=800, verified_ns=50)
    bridge.management = SimpleNamespace(
        binding=SimpleNamespace(deadline_ns=1000),
        retain=lambda: calls.append("retain") or receipt,
        gate_prepare=lambda value: calls.append("prepare"),
        gate_bootstrap=lambda value: calls.append("bootstrap"),
        finish=lambda value, active_lease: calls.append("finish"),
    )
    monkeypatch.setattr(bridge, "no_local_lease", contextlib.nullcontext)
    return module, bridge, calls


def test_bridge_order_and_single_use_finish(monkeypatch):
    module, bridge, calls = bridge_fixture(monkeypatch)
    with pytest.raises(module.BridgeRefused):
        bridge.operate("gate_prepare")
    for op in ("retain", "gate_prepare", "gate_bootstrap", "finish"):
        result = bridge.operate(op)
        assert result["op"] == op
    bridge.on_exit()
    with pytest.raises(module.BridgeRefused):
        bridge.operate("finish")
    assert calls == ["retain", "prepare", "bootstrap", "finish"]


def test_bridge_failed_finish_keeps_receipt_and_never_retries(monkeypatch):
    module, bridge, calls = bridge_fixture(monkeypatch)
    bridge.operate("retain")

    @contextlib.contextmanager
    def active_journal():
        calls.append("active-journal")
        raise module.BridgeRefused("synthetic-active-journal")
        yield

    monkeypatch.setattr(bridge, "no_local_lease", active_journal)
    with pytest.raises(module.BridgeRefused):
        bridge.operate("finish")
    bridge.on_exit()
    assert bridge.receipt is not None and bridge.finish_attempted is True
    assert calls == ["retain", "active-journal"]


@pytest.mark.parametrize(
    "messages,expected",
    [
        (
            [
                b'{"op":"retain"}\n',
                b'{"op":"gate_prepare"}\n',
                b'{"op":"gate_bootstrap"}\n',
                b'{"op":"finish"}\n',
            ],
            0,
        ),
        ([b'{"op":"retain"}\n', None], 1),
        ([b'{"op":"retain","op":"finish"}\n'], 1),
    ],
)
def test_actual_bridge_main_protocol_and_eof_cleanup(
    monkeypatch, capsys, messages, expected
):
    module, bridge, calls = bridge_fixture(monkeypatch)
    stream = iter(messages)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.sys, "flags", SimpleNamespace(isolated=1))
    monkeypatch.setattr(module.sys, "argv", ["bridge"])
    monkeypatch.setattr(module, "exclusive", contextlib.nullcontext)
    monkeypatch.setattr(module, "DEPLOYMENT", {})
    monkeypatch.setattr(module, "parameters", lambda: {})
    monkeypatch.setattr(module, "Bridge", lambda doc: bridge)
    monkeypatch.setattr(module, "line", lambda deadline: next(stream))
    assert module.main() == expected
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    if messages[0].count(b'"op"') == 1:
        assert calls.count("finish") == 1
        assert output[0]["op"] == "retain"
    else:
        assert output == [
            {
                "status": "REFUSED",
                "diagnostic": {
                    "phase": "unproven",
                    "stage": "bridge",
                    "category": "duplicate-key",
                    "cleanup": "not_attempted",
                },
            }
        ]


def test_prepared_custody_repr_serialization_and_one_shot(monkeypatch):
    module = adapter("issuer")
    proc = SimpleNamespace(poll=lambda: 0)
    prepared = module.PreparedCustody(
        proc, ["SECRET-CANARY-A", "SECRET-CANARY-B"], None
    )
    assert "SECRET-CANARY" not in repr(prepared)
    with pytest.raises(module.IssuerRefused):
        prepared.__getstate__()
    for _ in range(2):
        with pytest.raises(module.IssuerRefused) as error:
            module.issue({}, remaining_seconds=100, prepared_custody=prepared)
        assert "SECRET-CANARY" not in str(error.value)
    assert prepared._consumed is True and prepared._shares is None


@pytest.mark.parametrize("budget", [True, 0, -1, 1, 241, float("inf"), float("nan")])
def test_issuer_bad_budget_never_consumes_custody(budget):
    module = adapter("issuer")
    prepared = module.PreparedCustody(None, ["synthetic"], None)
    with pytest.raises(module.IssuerRefused):
        module.issue({}, remaining_seconds=budget, prepared_custody=prepared)
    assert prepared._consumed is False


def test_wrapper_remaining_uses_inherited_absolute_deadline(monkeypatch):
    module = adapter("runner")
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 100)
    view = module.WrapperView(pathlib.Path("/synthetic"), 110)
    assert view.remaining() == 10 / 1_000_000_000
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 110)
    with pytest.raises(TimeoutError):
        view.remaining()
    with pytest.raises(ValueError):
        module.WrapperView(pathlib.Path("/synthetic"), True)


@pytest.mark.parametrize("name", ["issuer", "bridge", "remote", "runner"])
def test_configuration_is_required_and_exact_schema(name, monkeypatch):
    module = adapter(name)
    monkeypatch.setattr(module, "DEPLOYMENT", None)
    with pytest.raises(RuntimeError, match="protected-deployment-required"):
        module.deployment_inputs()
    for invalid in ({}, {"unrecognized": "SECRET-CANARY"}, [], None):
        with pytest.raises(ValueError) as error:
            module.configure(invalid)
        assert "SECRET-CANARY" not in str(error.value)
    assert module.DEPLOYMENT is None


@pytest.mark.parametrize("defect", ["reordered", "extra", "boolean-count", "late"])
def test_remote_certificate_handoff_refuses_reordered_near_miss_and_late_results(
    monkeypatch, defect
):
    module = adapter("remote")
    issued = {
        "public_record": public_record(),
        "certificates": {
            name: "ssh-ed25519-cert-v01@openssh.com synthetic" for name in module.NAMES
        },
        "breakglass_closed": True,
        "valid_until_epoch": 1000,
    }
    message = {"op": "issued", "issued": issued}
    if defect == "reordered":
        message = {"op": "prepare"}
    elif defect == "extra":
        issued["unexpected"] = True
    elif defect == "boolean-count":
        issued["public_record"]["certificate_count"] = True
    emissions = []
    monkeypatch.setattr(
        module, "emit", lambda value, deadline: emissions.append((value, deadline))
    )
    monkeypatch.setattr(module, "receive", lambda deadline, limit: message)

    def remaining():
        if defect == "late":
            raise TimeoutError("synthetic-late")
        return 10

    client = SimpleNamespace(
        window=SimpleNamespace(execution_deadline_ns=123000000000), remaining=remaining
    )
    with pytest.raises((module.RemoteRefused, TimeoutError)):
        module.issuer_exchange(client, {"public_keys": {}}, remaining_seconds=10)
    assert emissions[0][0]["op"] == "requests"
    assert emissions[0][1] == 123


@pytest.mark.parametrize("response", ["valid", "eof", "oversized", "late", "timeout"])
def test_direct_issuer_partial_writes_and_complete_bounded_handoff(
    monkeypatch, response, tmp_path
):
    import os
    import subprocess
    import time

    module = adapter("issuer")
    envelope = {
        "public_record": public_record(),
        "certificates": dict.fromkeys(module.NAMES, "synthetic-certificate"),
    }
    envelope["certificates"]["expired_observer"] = "expired"
    expression = (
        repr(json.dumps(envelope).encode())
        if response in {"valid", "late"}
        else ("b'x'*262145" if response == "oversized" else "b''")
    )
    code = (
        "import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write("
        + expression
        + ")\nsys.stdout.buffer.flush()\n"
    )
    late_cleanup = tmp_path / "late-cleanup"
    if response == "timeout":
        code = (
            "import sys,time,pathlib\nsys.stdin.buffer.read()\ntime.sleep(2.3)\n"
            + "pathlib.Path("
            + repr(str(late_cleanup))
            + ").write_text('closed')\n"
            + "sys.stdout.buffer.write("
            + repr(json.dumps(envelope).encode())
            + ")\n"
        )
        monkeypatch.setattr(module, "RECOVERY_SECONDS", 2)
        monkeypatch.setattr(module, "RECOVERY_TRANSIT_SECONDS", 0)
    proc = subprocess.Popen(  # noqa: S603 - synthetic owned interpreter, no SSH
        [sys.executable, "-I", "-B", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        bufsize=0,
    )
    expiry = time.time() + 1000
    parser = SimpleNamespace(
        certificate_interval=lambda certificate: (
            0,
            1 if certificate == "expired" else expiry,
        )
    )
    prepared = module.PreparedCustody(
        proc, ["SECRET-CANARY-A", "SECRET-CANARY-B"], parser
    )
    original_write = os.write
    writes = []

    def partial(fd, data):
        count = original_write(fd, data[:3])
        writes.append(count)
        return count

    monkeypatch.setattr(module.os, "write", partial)
    if response == "late":
        original_receive = module.receive

        def late_receive(fd, deadline, limit):
            value = original_receive(fd, deadline, limit)
            monkeypatch.setattr(module.time, "monotonic", lambda: deadline + 1)
            return value

        monkeypatch.setattr(module, "receive", late_receive)
    requests = {
        "jump_principal": "lane3jump",
        "probe_principal": "synthetic",
        "public_keys": dict.fromkeys(module.NAMES, "ssh-ed25519 synthetic"),
    }
    try:
        if response == "valid":
            result = module.issue(
                requests, remaining_seconds=10, prepared_custody=prepared
            )
            assert (
                result["breakglass_closed"] is True
                and result["valid_until_epoch"] == expiry
            )
        else:
            with pytest.raises(module.IssuerRefused) as error:
                module.issue(
                    requests,
                    remaining_seconds=2.1 if response == "timeout" else 10,
                    prepared_custody=prepared,
                )
            assert "SECRET-CANARY" not in str(error.value)
        if response == "timeout":
            assert late_cleanup.read_text() == "closed"
        assert len(writes) > 1 and max(writes) <= 3
        assert prepared._consumed is True and prepared._shares is None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_configuration_freezes_after_first_success(monkeypatch):
    module = adapter("runner")
    monkeypatch.setattr(module, "DEPLOYMENT", None)
    original = {
        "code_directory": "/synthetic",
        "pins": {
            "b8-proof-matrix.py": "a" * 64,
            "b8-source-qualification.py": "b" * 64,
        },
    }
    module.configure(original)
    original["pins"]["b8-proof-matrix.py"] = "c" * 64
    assert module.deployment_inputs()["pins"]["b8-proof-matrix.py"] == "a" * 64
    with pytest.raises(RuntimeError, match="deployment-already-configured"):
        module.configure(original)


def test_remote_supervisor_source_drift_fails_before_rendering(monkeypatch):
    module = adapter("issuer")
    monkeypatch.setattr(module, "DEPLOYMENT", {"issuer_source_sha256": "0" * 64})
    with pytest.raises(module.IssuerRefused):
        module.remote_code()


@pytest.mark.parametrize("defect", [None, "bad-ready", "pin"])
def test_custody_preparation_measures_pins_and_ready_before_any_decryption(
    tmp_path, monkeypatch, defect
):
    import os

    module = adapter("issuer")
    notes = [tmp_path / name for name in ("A-note", "B-note")]
    for note in notes:
        note.write_text("synthetic-note")
        note.chmod(0o600)
    bundle = tmp_path / "encrypted.json"
    bundle.write_text(
        json.dumps(
            {
                "recipient_order": ["A", "B", "C"],
                "encrypted_shares_base64": ["QQ==", "Qg==", "Qw=="],
            }
        )
    )
    monkeypatch.setattr(
        module,
        "DEPLOYMENT",
        {
            "custody_notes": tuple(notes),
            "client_module": tmp_path / "client.py",
            "custody_module": tmp_path / "custody.py",
            "ssh_destination": "synthetic-host",
            "encrypted_shares": bundle,
        },
    )
    events = []
    parser = object()

    def decrypt(slot, encrypted):
        assert "READY-read" in events
        events.append("decrypt-" + slot)
        return "SECRET-CANARY-" + slot

    def load(name, path):
        events.append("pin-" + name)
        if defect == "pin":
            raise module.IssuerRefused("issuer.local_pin")
        return (
            parser
            if name == "b8_pinned_client_parser"
            else SimpleNamespace(decrypt=decrypt)
        )

    readfd, writefd = os.pipe()
    os.write(
        writefd, b"B8_DIRECT_READY_V1\n" if defect != "bad-ready" else b"BAD_READY\n"
    )
    stdout = os.fdopen(readfd, "rb", buffering=0)
    process = SimpleNamespace(
        stdout=stdout, pid=123456789, poll=lambda: None, wait=lambda timeout: None
    )

    def popen(argv, **kwargs):
        assert events == ["pin-b8_pinned_client_parser", "pin-b8_approved_custody"]
        assert "ProxyCommand=none" in argv and "ProxyJump=none" in argv
        events.append("channel-open")
        return process

    original_read = os.read

    def measured_read(fd, count):
        value = original_read(fd, count)
        if value == b"\n":
            events.append("READY-read")
        return value

    monkeypatch.setattr(module, "load_pinned", load)
    monkeypatch.setattr(module, "remote_code", lambda: "synthetic-supervisor")
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.os, "read", measured_read)
    monkeypatch.setattr(
        module.os, "killpg", lambda pid, sig: events.append("channel-close")
    )
    try:
        if defect is None:
            prepared = module.prepare_custody()
            assert prepared._parser is parser and prepared._consumed is False
            assert events[-2:] == ["decrypt-A", "decrypt-B"]
            assert "SECRET-CANARY" not in repr(prepared)
            prepared.close()
        else:
            with pytest.raises(module.IssuerRefused) as error:
                module.prepare_custody()
            assert not any(event.startswith("decrypt-") for event in events)
            assert "SECRET-CANARY" not in str(error.value)
        assert all(not note.exists() for note in notes)
    finally:
        stdout.close()
        os.close(writefd)


@pytest.mark.parametrize("defect", [None, "active-journal", "not-ready", "pin"])
def test_bridge_checks_installer_under_same_lock_and_preserves_backstop_on_refusal(
    monkeypatch, defect
):
    module = adapter("bridge")
    bridge = object.__new__(module.Bridge)
    bridge.receipt = object()
    bridge.phase = "retained"
    bridge.finish_attempted = False
    events = []
    locked = False
    monkeypatch.setattr(
        module, "DEPLOYMENT", {"bundle_digest": "a" * 64, "config_digest": "b" * 64}
    )

    @contextlib.contextmanager
    def lock():
        nonlocal locked
        assert not locked
        locked = True
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")
            locked = False

    def check(name, result=None):
        def operation(*args, **kwargs):
            assert locked
            events.append(name)
            if (defect == "active-journal" and name == "no_lease") or (
                defect == "not-ready" and name == "ready"
            ):
                raise module.BridgeRefused("synthetic-refusal")
            return result

        return operation

    bridge.installer = SimpleNamespace(
        locked=lock,
        no_transaction=check("no_transaction"),
        no_uninstall=check("no_uninstall"),
        no_lease=check("no_lease"),
        verify=check(
            "verify",
            {
                "bundle_digest": "c" * 64 if defect == "pin" else "a" * 64,
                "config_digest": "b" * 64,
            },
        ),
        entrypoint=check("entrypoint"),
        require_ready=check("ready"),
    )
    bridge.management = SimpleNamespace(finish=check("remove-trust"))
    if defect is None:
        bridge.finish()
        assert events == [
            "lock",
            "no_transaction",
            "no_uninstall",
            "no_lease",
            "verify",
            "entrypoint",
            "ready",
            "no_lease",
            "remove-trust",
            "unlock",
        ]
        assert bridge.receipt is None
    else:
        receipt = bridge.receipt
        with pytest.raises(module.BridgeRefused):
            bridge.finish()
        bridge.on_exit()
        assert "remove-trust" not in events
        assert bridge.receipt is receipt and bridge.finish_attempted is True
        assert events.count("lock") == 1 and events[-1] == "unlock"
        if defect == "active-journal":
            assert "verify" not in events and "ready" not in events


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "extra",
        "missing",
        "source",
        "manifest",
        "session",
        "namespace",
        "endpoint",
        "port-bool",
        "approval",
        "binding-extra",
    ],
)
def test_remote_inputs_exact_schema_and_canonical_binding(monkeypatch, defect):
    import hashlib

    remote, client, transport = (
        adapter("remote"),
        adapter("client"),
        adapter("transport"),
    )
    session = "a" * 32
    manifest = transport.Manifest.parse(
        {
            "scope": "b8-ssh-vm124",
            "runner_user": "l3b8-" + session[:12],
            "runner_uid": 35001,
            "runner_gid": 35002,
            "destinations": [
                {"purpose": purpose, "address": address, "port": 22}
                for purpose, address in zip(
                    ("target", "jump", "probe"),
                    ("192.0.2.1", "192.0.2.2", "192.0.2.3"),
                    strict=True,
                )
            ],
        }
    )
    raw = client.canonical(manifest.document())
    assert hashlib.sha256(raw).hexdigest() == manifest.digest
    pins = {
        "lane3_campaign_transport.py": "b" * 64,
        "lane3_campaign_runner.py": "c" * 64,
    }
    cfg = {
        "session_id": session,
        "network_namespace": "/proc/1/ns/net",
        **{d.purpose: {"host": d.address, "port": 22} for d in manifest.destinations},
    }
    identity = client.RunnerIdentity(
        manifest.user, manifest.uid, manifest.gid, manifest.digest
    )
    binding = transport.Binding(
        session,
        manifest.digest,
        "d" * 64,
        "00000000-0000-0000-0000-000000000000",
        pins["lane3_campaign_transport.py"],
        "e" * 64,
    )
    doc = {
        "schema": "dotmac.b8.remote-session.v1",
        "source_head": "f" * 40,
        "manifest_sha256": manifest.digest,
        "client_approval_sha256": client.approval_digest(
            identity, cfg, pins["lane3_campaign_runner.py"]
        ),
        "transport_binding": binding.document(),
        "cfg": cfg,
        "principals": {"jump_principal": "lane3jump", "probe_principal": "synthetic"},
    }
    if defect == "extra":
        doc["extra"] = True
    elif defect == "missing":
        del doc["principals"]
    elif defect == "source":
        doc["source_head"] = "0" * 40
    elif defect == "manifest":
        doc["manifest_sha256"] = "0" * 64
    elif defect == "session":
        cfg["session_id"] = "0" * 32
    elif defect == "namespace":
        cfg["network_namespace"] = "/proc/2/ns/net"
    elif defect == "endpoint":
        cfg["target"]["host"] = "192.0.2.99"
    elif defect == "port-bool":
        cfg["target"]["port"] = True
    elif defect == "approval":
        doc["client_approval_sha256"] = "0" * 64
    elif defect == "binding-extra":
        doc["transport_binding"]["extra"] = True
    monkeypatch.setattr(
        remote,
        "DEPLOYMENT",
        {
            "input": pathlib.Path("/synthetic/input"),
            "manifest": pathlib.Path("/synthetic/manifest"),
            "source_head": "f" * 40,
            "pins": pins,
        },
    )
    monkeypatch.setattr(
        remote,
        "read",
        lambda path, mode, limit: json.dumps(doc).encode()
        if path.name == "input"
        else raw,
    )
    if defect is None:
        result = remote.inputs(client, transport)
        assert result[1:] == (manifest, binding, identity)
    else:
        with pytest.raises((remote.RemoteRefused, transport.Refused, TypeError)):
            remote.inputs(client, transport)


@pytest.mark.parametrize("phase", ["retain", "gate_prepare", "gate_bootstrap"])
def test_bridge_uses_measured_management_exception_identity_and_safe_labels(
    monkeypatch, phase
):
    bridge_module = adapter("bridge")
    management_module = adapter("management")
    bridge = SimpleNamespace(mgmt_module=management_module)
    error = management_module.Refused("timer-unproven", stage="first-retained-check")
    diagnostic = bridge_module.failure_diagnostic(error, bridge, phase, "unproven")
    assert diagnostic == {
        "phase": phase,
        "stage": "first-retained-check",
        "category": "timer-unproven",
        "cleanup": "unproven",
    }

    class Imposter(RuntimeError):
        category = "timer-unproven"
        failure_category = "PRIVATE-CANARY"

    assert (
        bridge_module.failure_diagnostic(
            Imposter("PRIVATE-CANARY"), bridge, phase, "not_attempted"
        )["category"]
        == "unexpected"
    )
    earlier = management_module.Refused("timer-unproven", cleanup="unproven")
    assert (
        bridge_module.failure_diagnostic(earlier, bridge, phase, "completed")["cleanup"]
        == "unproven"
    )
