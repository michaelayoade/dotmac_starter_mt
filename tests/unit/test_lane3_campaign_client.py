"""Synthetic contracts; execution belongs to Git-hosted CI only."""

import dataclasses
import hashlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import lane3_campaign_client as c

IDENTITY = c.RunnerIdentity("b8reviewed", 34001, 34002, "a" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", True),
        ("uid", 0),
        ("uid", -1),
        ("uid", 2**32 - 1),
        ("gid", True),
        ("gid", 0),
        ("user", "root"),
        ("user", "--bad"),
        ("manifest_sha256", "bad"),
        ("ownership", "unknown"),
        ("ownership", "session-created"),
    ],
)
def test_exact_nonroot_identity(field, value):
    with pytest.raises(c.ClientRefused):
        dataclasses.replace(IDENTITY, **{field: value}).validate()


def test_identity_and_manifest_are_in_approval_digest():
    cfg = {"approved_proof_client": True}
    digest = c.approval_digest(IDENTITY, cfg, "b" * 64)
    for field, value in [
        ("uid", 34003),
        ("gid", 34004),
        ("user", "other"),
        ("manifest_sha256", "c" * 64),
    ]:
        assert (
            c.approval_digest(
                dataclasses.replace(IDENTITY, **{field: value}), cfg, "b" * 64
            )
            != digest
        )
    assert (
        c.approval_digest(IDENTITY, {"approved_proof_client": False}, "b" * 64)
        != digest
    )


def test_approval_is_canonical_and_has_no_material():
    assert c.approval_digest(IDENTITY, {"a": 1, "b": 2}, "b" * 64) == c.approval_digest(
        IDENTITY, {"b": 2, "a": 1}, "b" * 64
    )
    assert len(c.approval_digest(IDENTITY, {}, "b" * 64)) == 64


@pytest.mark.parametrize(
    "remaining,certificate", [(0, 60), (30, 30), (239, 29), (4, 90)]
)
def test_budget_refuses_before_launch(remaining, certificate):
    with pytest.raises(c.ClientRefused):
        c.launch_budget(remaining, certificate)


def test_runtime_is_bounded_by_both_material_and_session():
    assert c.launch_budget(240, 100) == 69
    assert c.launch_budget(99, 1000) == 99
    assert c.launch_budget(500, 1000) == 240


def test_pinned_file_rejects_symlink_and_writable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "trusted_parent", lambda p: None)
    file = tmp_path / "runner.py"
    file.write_bytes(b"synthetic")
    file.chmod(0o644)
    monkeypatch.setattr(c.os, "geteuid", lambda: file.stat().st_uid)
    # File ownership is always root: changing effective-UID cannot widen trust.
    if file.stat().st_uid != 0:
        with pytest.raises(c.ClientRefused):
            c.pinned_file(file, hashlib.sha256(b"synthetic").hexdigest())
    link = tmp_path / "link.py"
    link.symlink_to(file)
    with pytest.raises(c.ClientRefused):
        c.pinned_file(link, hashlib.sha256(b"synthetic").hexdigest())
    file.chmod(0o666)
    with pytest.raises(c.ClientRefused):
        c.pinned_file(file, hashlib.sha256(b"synthetic").hexdigest())


def test_refusal_never_relays_private_details():
    assert str(c.ClientRefused("client.identity")) == "client.identity"


def test_systemd_launch_is_nonroot_bounded_and_joins_explicit_namespace(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "command", lambda argv, **kwargs: calls.append(argv))
    record = {
        "identity": dataclasses.asdict(IDENTITY),
        "unit": c.PREFIX + "b" * 32 + "-matrix",
        "work": "/synthetic/work",
        "launcher": "/synthetic/lane3_campaign_client.py",
        "runner": "/synthetic/matrix.py",
        "runner_sha256": "f" * 64,
        "ready_socket": "synthetic-ready",
    }
    c.SystemBackend().start(record, 69, "/proc/123/ns/net")
    argv = calls[0]
    for value in (
        "User=34001",
        "Group=34002",
        "SupplementaryGroups=",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "NoNewPrivileges=yes",
        "RuntimeMaxSec=69",
        "NetworkNamespacePath=/proc/123/ns/net",
        "KillMode=control-group",
    ):
        assert "--property=" + value in argv
    assert argv[-8:] == [
        "/usr/bin/python3",
        "-I",
        record["launcher"],
        "child",
        record["work"],
        record["runner"],
        record["runner_sha256"],
        record["ready_socket"],
    ]


def test_namespace_cannot_be_changed_outside_bound_configuration():
    with pytest.raises(c.ClientRefused):
        c.Session(
            IDENTITY,
            "/synthetic/manifest",
            "/synthetic/runner",
            "f" * 64,
            "e" * 64,
            {"session_id": "c" * 32},
            network_namespace="/proc/999/ns/net",
        )


@pytest.mark.parametrize(
    "text", ["", "ssh-ed25519-cert-v01@openssh.com !!!!", "ssh-rsa aGVsbG8="]
)
def test_certificate_parser_refuses_malformed_public_data(text):
    with pytest.raises(c.ClientRefused):
        c.certificate_interval(text)


def test_account_lookup_rejects_any_additional_group(monkeypatch):
    from types import SimpleNamespace

    entry = SimpleNamespace(
        pw_uid=IDENTITY.uid, pw_gid=IDENTITY.gid, pw_name=IDENTITY.user
    )
    monkeypatch.setattr(c.pwd, "getpwnam", lambda user: entry)
    monkeypatch.setattr(c.pwd, "getpwuid", lambda uid: entry)
    monkeypatch.setattr(c.os, "getgrouplist", lambda user, gid: [gid])
    c.identity_matches(IDENTITY)
    monkeypatch.setattr(c.os, "getgrouplist", lambda user, gid: [gid, gid + 1])
    with pytest.raises(c.ClientRefused):
        c.identity_matches(IDENTITY)


def test_custody_gate_precedes_identity_code_or_material(monkeypatch):
    client = c.Session(
        IDENTITY,
        "/synthetic/manifest",
        "/synthetic/runner",
        "f" * 64,
        "e" * 64,
        {"session_id": "c" * 32, "probe": {"account": "synthetic"}},
    )
    monkeypatch.setattr(
        c,
        "identity_matches",
        lambda _: pytest.fail("identity lookup reached before custody readiness"),
    )
    with pytest.raises(c.ClientRefused, match="client.custody_ready"):
        client.prepare({"jump_principal": "lane3jump", "probe_principal": "synthetic"})
    assert client.control is None and client.work is None


def test_public_initializer_serializes_prepare_and_sensitive_to_missing_lock(
    monkeypatch,
):
    import contextlib
    import threading

    def exercise(unlocked=False):
        client = c.Session(
            IDENTITY,
            "/synthetic/manifest",
            "/synthetic/runner",
            "f" * 64,
            "e" * 64,
            {"session_id": "d" * 32, "probe": {"account": "synthetic"}},
        )
        lock = threading.Lock()
        read = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        prepare_entered = threading.Event()
        state = {"status": "closed", "phase": "not-started"}
        errors = []

        @contextlib.contextmanager
        def operation():
            if threading.current_thread().name == "prepare":
                attempted.set()
            with lock:
                yield

        def initialize_body():
            before = dict(state)
            if threading.current_thread().name == "initializer":
                read.set()
                assert release.wait(5)
            else:
                prepare_entered.set()
            assert before == {"status": "closed", "phase": "not-started"}
            state.update(before)
            return {"synthetic": True}

        class Prepared(Exception):
            pass

        def write(record, status, phase):
            state.update(status=status, phase=phase)
            if status == "active":
                raise Prepared

        monkeypatch.setattr(c, "transport_operation_lock", operation)
        monkeypatch.setattr(c, "write_closure", write)
        monkeypatch.setattr(client, "preflight", lambda: None)
        monkeypatch.setattr(client, "cleanup", lambda: None)
        monkeypatch.setattr(client, "_initialize_closed_locked", initialize_body)

        def start():
            try:
                client.prepare(
                    {"jump_principal": "lane3jump", "probe_principal": "synthetic"}
                )
            except Prepared:
                pass
            except BaseException as exc:
                errors.append(exc)

        initialize = initialize_body if unlocked else client.initialize_closed
        first = threading.Thread(target=initialize, name="initializer")
        second = threading.Thread(target=start, name="prepare")
        first.start()
        try:
            assert read.wait(5)
            # Deterministic sensitivity: an unlocked public initializer cannot
            # satisfy this invariant, even before the second thread is started.
            assert lock.locked()
            second.start()
            assert attempted.wait(5)
            assert not prepare_entered.is_set()
        finally:
            release.set()
            first.join(5)
            if second.ident is not None:
                second.join(5)
        assert not first.is_alive() and not second.is_alive() and not errors
        assert state == {"status": "active", "phase": "started"}

    exercise()
    with pytest.raises(AssertionError):
        exercise(unlocked=True)


def test_client_cleanup_timer_has_total_start_attempt_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "command", lambda argv, **kwargs: calls.append(argv))
    control = pathlib.Path("/synthetic") / (c.PREFIX + "e" * 32)
    c.SystemBackend().arm(control, 240)
    argv = calls[0]
    assert "--property=StartLimitIntervalSec=infinity" in argv
    assert "--property=StartLimitBurst=30" in argv
    assert "--property=RuntimeMaxSec=30" in argv
    assert "--collect" not in argv
    assert all("reset-failed" not in call for call in calls)
