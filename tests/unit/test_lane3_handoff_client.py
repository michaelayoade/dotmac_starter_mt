"""Job-side handoff client; synthetic values, hosted Linux CI only.

Documentation ranges and ``*.example`` names only. Every refusal test has a
positive control built by the same helper, so a refusal can only come from the
single predicate the case changes.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import os
import pathlib
import socket
import sys
import threading
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux /proc/self/fd sockets"
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hp = _load("lane3_handoff_protocol")
c = _load("lane3_handoff_client")

UID = os.getuid()
LEASE = "0123456789abcdef0123456789abcdef"
NONCE = "ab" * 32
BOOT = "01234567-89ab-cdef-0123-456789abcdef"
SHA = "1" * 40
BLOB = "2" * 40
STARTER = "3" * 40
HOST_URL = "https://broker-1.region.example"
REQUEST_URL = (
    HOST_URL + "/PRIVATE-PATH-CANARY/idtoken?api-version=2.0&x=PRIVATE-QUERY-CANARY"
)
LAUNCH = 1_000_000_000_000
NS = 1_000_000_000
LEASE_EXPIRY = LAUNCH + 300 * NS
GRANT_EXPIRY = LAUNCH + 120 * NS
SNAP_EXPIRY = LAUNCH + 150 * NS

EXPECT = c.LocalExpectation(
    repository_id=11,
    run_id=22,
    run_attempt=1,
    workflow_sha=SHA,
    starter_commit=STARTER,
)
FULL_EXPECT = c.LocalExpectation(
    repository_id=11,
    run_id=22,
    run_attempt=1,
    workflow_sha=SHA,
    starter_commit=STARTER,
    job_id=33,
    runner_id=44,
    workflow_blob=BLOB,
)


def challenge_doc(**over: Any) -> dict[str, Any]:
    doc = {
        "protocol": hp.PROTOCOL,
        "lease_id": LEASE,
        "nonce": NONCE,
        "boot_id": BOOT,
        "expires_at_monotonic_ns": LEASE_EXPIRY,
    }
    doc.update(over)
    return doc


def grant_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "protocol": hp.PROTOCOL,
        "state": "GRANTED",
        "lease_id": LEASE,
        "nonce": NONCE,
        "boot_id": BOOT,
        "sequence": 1,
        "expires_at_monotonic_ns": GRANT_EXPIRY,
        "binding": {
            "repository_id": 11,
            "run_id": 22,
            "run_attempt": 1,
            "job_id": 33,
            "runner_id": 44,
            "workflow_sha": SHA,
            "workflow_blob": BLOB,
            "starter_commit": STARTER,
            "admission_digest": "8" * 64,
            "supplier_digest": "9" * 64,
        },
        "origin": HOST_URL,
        "snapshot": {
            "digest": "4" * 64,
            "expires_at_monotonic_ns": SNAP_EXPIRY,
            "port": 443,
            "addresses": [{"family": 4, "address": "192.0.2.10"}],
        },
        "policy_digest": "5" * 64,
        "manifest_digest": "6" * 64,
        "controller_digest": "7" * 64,
    }
    doc.update(over)
    return doc


class Clock:
    def __init__(self, now: int = LAUNCH + 2 * NS) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += int(seconds * NS)


def chmod(path: Any, mode: int, **kw: Any) -> None:
    os.chmod(path, mode, **kw)


def put(path: pathlib.Path, data: Any, mode: int = 0o640) -> None:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    path.write_bytes(raw)
    chmod(path, mode)


class Env:
    """A synthetic run root with one lease directory."""

    def __init__(self, tmp: pathlib.Path) -> None:
        self.root = tmp.resolve() / "run"
        self.root.mkdir(mode=0o755)
        chmod(self.root, 0o755)
        self.lease = self.root / LEASE
        self.lease.mkdir()
        chmod(self.lease, 0o750)
        put(self.lease / "challenge.json", challenge_doc())
        self.clock = Clock()
        self.sent: list[dict[str, Any]] = []
        self.script: list[dict[str, Any]] = [
            hp.response("WAIT"),
            hp.response("GRANTED"),
            hp.response("CONSUMED"),
        ]

    def client(self, **over: Any) -> Any:
        args: dict[str, Any] = {
            "lease_id": LEASE,
            "run_root": str(self.root),
            "expected_uid": UID,
            "clock_ns": self.clock,
            "sleep": self.clock.sleep,
            "boot_id_reader": lambda: BOOT,
        }
        args.update(over)
        client = c.HandoffClient(**args)
        client._exchange = self._exchange  # type: ignore[method-assign]
        return client

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(copy.deepcopy(request))
        reply = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if reply["status"] == "GRANTED" and not (self.lease / "grant.json").exists():
            put(self.lease / "grant.json", grant_doc())
        return reply

    def obtain(self, expectation: Any = FULL_EXPECT, **over: Any) -> Any:
        client = over.pop("client", None) or self.client()
        return client.obtain(
            request_url=over.pop("request_url", REQUEST_URL),
            expectation=expectation,
            launch_ns=over.pop("launch_ns", LAUNCH),
        )


@pytest.fixture
def env(tmp_path: pathlib.Path) -> Env:
    e = Env(tmp_path)
    # The report answer is WAIT, then the poll is GRANTED (with the grant file
    # published first), then consume answers CONSUMED.
    e.script = [hp.response("WAIT"), hp.response("GRANTED"), hp.response("CONSUMED")]
    return e


def refusal(excinfo: Any) -> str:
    return str(excinfo.value)


# -- positive path ------------------------------------------------------------


def test_positive_control_returns_pinned_target(env: Env) -> None:
    target = env.obtain()
    assert target.origin == HOST_URL
    assert target.family == socket.AF_INET and target.address == "192.0.2.10"
    assert target.deadline_ns == GRANT_EXPIRY
    assert [r["op"] for r in env.sent] == ["report", "poll", "consume"]


def test_report_has_only_the_contract_fields_and_no_url_parts_or_token(
    env: Env,
) -> None:
    env.obtain()
    report = env.sent[0]
    assert set(report) == {
        "protocol",
        "op",
        "lease_id",
        "nonce",
        "origin",
        "flags",
        "expected",
    }
    assert report["origin"] == HOST_URL
    assert report["flags"] == {
        "explicit_port_present": False,
        "userinfo_present": False,
    }
    blob = json.dumps(env.sent)
    for canary in (
        "PRIVATE-PATH-CANARY",
        "PRIVATE-QUERY-CANARY",
        "idtoken",
        "api-version",
    ):
        assert canary not in blob
    hp.validate_report(report)
    hp.validate_poll(env.sent[1])
    hp.validate_consume(env.sent[2])


def test_consume_sends_digest_of_the_whole_grant(env: Env) -> None:
    env.obtain()
    on_disk = json.loads((env.lease / "grant.json").read_bytes())
    assert env.sent[2]["grant_digest"] == hp.grant_digest(on_disk)
    assert env.sent[2]["nonce"] == NONCE


def test_optional_expectations_are_not_checked_when_absent(env: Env) -> None:
    put(env.lease / "grant.json", grant_doc())
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    assert env.obtain(EXPECT).address == "192.0.2.10"


def test_ipv6_target(env: Env) -> None:
    g = grant_doc()
    g["snapshot"]["addresses"] = [{"family": 6, "address": "2001:db8::10"}]
    put(env.lease / "grant.json", g)
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    target = env.obtain()
    assert target.family == socket.AF_INET6 and target.address == "2001:db8::10"


# -- job binding / replay (client half) --------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository_id", 12),
        ("run_id", 23),
        ("run_attempt", 2),
        ("job_id", 34),
        ("runner_id", 45),
        ("workflow_sha", "a" * 40),
        ("workflow_blob", "a" * 40),
        ("starter_commit", "a" * 40),
    ],
)
def test_each_binding_field_independently_refuses_before_consume(
    env: Env, key: str, value: Any
) -> None:
    g = grant_doc()
    g["binding"][key] = value
    put(env.lease / "grant.json", g)
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.binding"
    assert "consume" not in [r["op"] for r in env.sent]


@pytest.mark.parametrize(
    "over",
    [
        {"lease_id": "f" * 32},
        {"nonce": "cd" * 32},
        {"boot_id": "99999999-89ab-cdef-0123-456789abcdef"},
        {"origin": "https://broker-2.region.example"},
        {"expires_at_monotonic_ns": LEASE_EXPIRY + 1, "snapshot": None},
    ],
)
def test_grant_identity_origin_and_lifetime_mismatch_refuses(
    env: Env, over: dict[str, Any]
) -> None:
    g = grant_doc()
    for key, value in over.items():
        if value is None and key == "snapshot":
            g["snapshot"]["expires_at_monotonic_ns"] = LEASE_EXPIRY + 5
        else:
            g[key] = value
    put(env.lease / "grant.json", g)
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) in {"handoff.binding", "handoff.grant"}
    assert "consume" not in [r["op"] for r in env.sent]


def test_expired_grant_refuses_and_unexpired_control_passes(env: Env) -> None:
    put(env.lease / "grant.json", grant_doc())
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    env.clock.now = GRANT_EXPIRY - 1
    assert env.obtain(launch_ns=env.clock.now - NS)


def test_expiry_equal_to_now_refuses(tmp_path: pathlib.Path) -> None:
    e = Env(tmp_path)
    put(e.lease / "grant.json", grant_doc())
    e.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    e.clock.now = GRANT_EXPIRY
    with pytest.raises(c.HandoffRefused) as err:
        e.obtain(launch_ns=GRANT_EXPIRY - 40 * NS)
    assert refusal(err) == "handoff.expired"
    assert "consume" not in [r["op"] for r in e.sent]


def test_second_obtain_on_same_client_refuses(env: Env) -> None:
    client = env.client()
    env.obtain(client=client)
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain(client=client)
    assert refusal(e) == "handoff.reused"
    assert [r["op"] for r in env.sent].count("consume") == 1


def test_consume_not_confirmed_refuses(env: Env) -> None:
    env.script = [hp.response("WAIT"), hp.response("GRANTED"), hp.response("GRANTED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.consume"


def test_consume_refused_by_controller_carries_fixed_category(env: Env) -> None:
    env.script = [
        hp.response("WAIT"),
        hp.response("GRANTED"),
        hp.response("REFUSED", "grant.consumed"),
    ]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert e.value.category == "grant.consumed"
    assert refusal(e) == "handoff.refused:grant.consumed"


@pytest.mark.parametrize(
    "category", sorted(["origin.not_admitted", "binding.mismatch", "approval.missing"])
)
def test_report_refusal_stops_without_poll_or_grant_read(
    env: Env, category: str
) -> None:
    env.script = [hp.response("REFUSED", category)]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert e.value.category == category
    assert [r["op"] for r in env.sent] == ["report"]


def test_poll_refusal_stops(env: Env) -> None:
    env.script = [hp.response("WAIT"), hp.response("REFUSED", "deadline.insufficient")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert e.value.category == "deadline.insufficient"
    assert [r["op"] for r in env.sent] == ["report", "poll"]


def test_unexpected_consumed_status_while_waiting_refuses(env: Env) -> None:
    env.script = [hp.response("CONSUMED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.grant"


def test_granted_directly_on_report_skips_polling(env: Env) -> None:
    put(env.lease / "grant.json", grant_doc())
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    env.obtain()
    assert [r["op"] for r in env.sent] == ["report", "consume"]


# -- deadline -----------------------------------------------------------------


def test_waiting_ends_at_45_seconds_from_launch_and_reads_no_grant(env: Env) -> None:
    env.script = [hp.response("WAIT")]
    put(env.lease / "grant.json", grant_doc())  # present but never announced
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.timeout"
    assert env.clock.now - LAUNCH <= 45 * NS + c.POLL_INTERVAL_S * NS
    assert "consume" not in [r["op"] for r in env.sent]
    assert [r["op"] for r in env.sent].count("poll") > 5


def test_launch_older_than_metadata_deadline_refuses_before_report(env: Env) -> None:
    env.clock.now = LAUNCH + 45 * NS + 1
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.timeout"
    assert env.sent == []


def test_launch_in_the_future_refuses(env: Env) -> None:
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain(launch_ns=env.clock.now + NS)
    assert refusal(e) == "handoff.timeout"


def test_deadline_is_clamped_to_lease_expiry(tmp_path: pathlib.Path) -> None:
    e = Env(tmp_path)
    put(
        e.lease / "challenge.json",
        challenge_doc(expires_at_monotonic_ns=LAUNCH + 10 * NS),
    )
    e.script = [hp.response("WAIT")]
    with pytest.raises(c.HandoffRefused) as err:
        e.obtain()
    assert refusal(err) == "handoff.timeout"
    assert e.clock.now - LAUNCH <= 10 * NS + c.POLL_INTERVAL_S * NS


# -- local origin -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://user@broker-1.region.example/x/idtoken?api-version=2.0",
        "https://broker-1.region.example:443/x/idtoken?api-version=2.0",
        "https://broker-1.region.example:8443/x/idtoken?api-version=2.0",
        "http://broker-1.region.example/x/idtoken?api-version=2.0",
        "https://Broker-1.region.example/x/idtoken?api-version=2.0",
        "https://192.0.2.1/x/idtoken?api-version=2.0",
        "https://broker-1.region.example./x/idtoken?api-version=2.0",
        "https://[2001:db8::1]/x/idtoken?api-version=2.0",
        "/x/idtoken?api-version=2.0",
        "",
    ],
)
def test_bad_local_origin_refuses_without_contacting_controller(
    env: Env, url: str
) -> None:
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain(request_url=url)
    assert refusal(e) in {"handoff.origin", "handoff.input"}
    assert env.sent == []


def test_userinfo_and_port_are_never_stripped(env: Env) -> None:
    # Sensitivity: the host-only spelling of the same URL is accepted, so the
    # two refusals above come from the userinfo/port predicate alone.
    assert c.derive_origin(REQUEST_URL) == HOST_URL
    for url in (
        "https://u@broker-1.region.example/p",
        "https://broker-1.region.example:443/p",
    ):
        with pytest.raises(c.HandoffRefused):
            c.derive_origin(url)


@pytest.mark.parametrize("value", [None, 5, b"https://a.example"])
def test_non_string_request_url_refuses(value: Any) -> None:
    with pytest.raises(c.HandoffRefused):
        c.derive_origin(value)


# -- trusted files ------------------------------------------------------------


def test_challenge_boot_mismatch_refuses(env: Env) -> None:
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain(
            client=env.client(
                boot_id_reader=lambda: "00000000-0000-0000-0000-000000000000"
            )
        )
    assert refusal(e) == "handoff.challenge"
    assert env.sent == []


def test_challenge_for_other_lease_or_expired_refuses(env: Env) -> None:
    put(env.lease / "challenge.json", challenge_doc(lease_id="e" * 32))
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.challenge"
    put(
        env.lease / "challenge.json",
        challenge_doc(expires_at_monotonic_ns=env.clock.now),
    )
    with pytest.raises(c.HandoffRefused) as e2:
        env.obtain(client=env.client())
    assert refusal(e2) == "handoff.expired"


def test_challenge_with_extra_missing_or_wrong_type_fields_refuses(env: Env) -> None:
    for doc in (
        {**challenge_doc(), "extra": 1},
        {k: v for k, v in challenge_doc().items() if k != "nonce"},
        challenge_doc(nonce="AB" * 32),
        challenge_doc(expires_at_monotonic_ns="soon"),
        challenge_doc(protocol="other"),
    ):
        put(env.lease / "challenge.json", doc)
        with pytest.raises(c.HandoffRefused):
            env.obtain(client=env.client())
    assert env.sent == []


def test_duplicate_keys_trailing_bytes_and_bad_encoding_refuse(env: Env) -> None:
    good = json.dumps(challenge_doc()).encode()
    dup = good[:-1] + b',"nonce":"' + b"cd" * 32 + b'"}'
    for raw in (dup, good + b"x", good + b"\n{}", b"\xff\xfe", b"[]", b"", b"{"):
        put(env.lease / "challenge.json", raw)
        with pytest.raises(c.HandoffRefused):
            env.obtain(client=env.client())
    put(env.lease / "challenge.json", good)
    assert env.obtain()


def _break_file(env: Env, how: str) -> None:
    path = env.lease / "challenge.json"
    if how == "mode_644":
        chmod(path, 0o644)
    elif how == "mode_600":
        chmod(path, 0o600)
    elif how == "mode_660":
        chmod(path, 0o660)
    elif how == "symlink":
        real = env.lease / "real.json"
        path.rename(real)
        path.symlink_to(real)
    elif how == "hardlink":
        os.link(path, env.lease / "second.json")
    elif how == "directory":
        path.unlink()
        path.mkdir()
        chmod(path, 0o640)
    elif how == "fifo":
        path.unlink()
        os.mkfifo(path, 0o640)
    elif how == "oversize":
        put(path, b"{" + b" " * c.MAX_FILE + b"}")
    elif how == "empty":
        put(path, b"")
    elif how == "missing":
        path.unlink()
    elif how == "lease_dir_755":
        chmod(env.lease, 0o755)
    elif how == "lease_dir_770":
        chmod(env.lease, 0o770)
    elif how == "lease_dir_symlink":
        moved = env.root / "moved"
        env.lease.rename(moved)
        env.lease.symlink_to(moved)
    elif how == "root_group_writable":
        chmod(env.root, 0o775)
    elif how == "root_other_writable":
        chmod(env.root, 0o757)
    else:  # pragma: no cover
        raise AssertionError(how)


@pytest.mark.parametrize(
    "how",
    [
        "mode_644",
        "mode_600",
        "mode_660",
        "symlink",
        "hardlink",
        "directory",
        "fifo",
        "oversize",
        "empty",
        "missing",
        "lease_dir_755",
        "lease_dir_770",
        "lease_dir_symlink",
        "root_group_writable",
        "root_other_writable",
    ],
)
def test_untrusted_challenge_file_or_directory_refuses(env: Env, how: str) -> None:
    assert env.client().read_challenge()  # positive control, same fixture
    _break_file(env, how)
    with pytest.raises(c.HandoffRefused) as e:
        env.client().read_challenge()
    assert refusal(e) in {"handoff.files", "handoff.challenge"}
    assert env.sent == []


def test_wrong_owner_uid_refuses(env: Env) -> None:
    assert env.client().read_challenge()
    with pytest.raises(c.HandoffRefused) as e:
        env.client(expected_uid=UID + 1).read_challenge()
    assert refusal(e) == "handoff.files"


def test_wrong_group_refuses_only_when_a_group_is_required(env: Env) -> None:
    gid = os.getgid()
    assert env.client(expected_gid=gid).read_challenge()
    with pytest.raises(c.HandoffRefused):
        env.client(expected_gid=gid + 1).read_challenge()


def test_grant_file_is_checked_like_the_challenge(env: Env) -> None:
    for how in ("mode", "symlink", "hardlink", "oversize"):
        sub = env.root.parent / f"g-{how}"
        sub.mkdir()
        e = Env(sub)
        path = e.lease / "grant.json"
        put(path, grant_doc())
        e.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
        if how == "mode":
            chmod(path, 0o644)
        elif how == "symlink":
            real = e.lease / "real.json"
            path.rename(real)
            path.symlink_to(real)
        elif how == "hardlink":
            os.link(path, e.lease / "second.json")
        else:
            put(path, b"{" + b" " * c.MAX_FILE + b"}")
        with pytest.raises(c.HandoffRefused) as err:
            e.obtain()
        assert refusal(err) == "handoff.files"
        assert "consume" not in [r["op"] for r in e.sent]


def test_forged_grant_content_is_refused(env: Env) -> None:
    bad = grant_doc()
    bad["extra"] = 1
    put(env.lease / "grant.json", bad)
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    assert refusal(e) == "handoff.grant"


def test_refusals_never_echo_inputs(env: Env) -> None:
    g = grant_doc()
    g["binding"]["run_id"] = 99999
    put(env.lease / "grant.json", g)
    env.script = [hp.response("GRANTED"), hp.response("CONSUMED")]
    with pytest.raises(c.HandoffRefused) as e:
        env.obtain()
    text = repr(e.value) + str(e.value)
    for needle in (
        str(env.root),
        LEASE,
        NONCE,
        "192.0.2",
        "99999",
        "PRIVATE",
        "broker-1",
    ):
        assert needle not in text


def test_bad_lease_id_or_expectation_types_refuse(tmp_path: pathlib.Path) -> None:
    for lease in ("", "G" * 32, LEASE.upper(), LEASE + "0", "../" + LEASE[3:]):
        with pytest.raises(c.HandoffRefused):
            c.HandoffClient(lease_id=lease)
    bad = c.LocalExpectation(11, True, 1, SHA, STARTER)  # type: ignore[arg-type]
    with pytest.raises(c.HandoffRefused):
        bad.validate()
    with pytest.raises(c.HandoffRefused):
        c.LocalExpectation(11, 22, 1, "x", STARTER).validate()


# -- real Unix socket transport ----------------------------------------------


class FakeController:
    """A threaded root-controller stand-in on a real AF_UNIX socket."""

    def __init__(self, env: Env, replies: list[Any]) -> None:
        self.env = env
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        fd = os.open(env.lease, os.O_RDONLY | os.O_DIRECTORY)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(f"/proc/self/fd/{fd}/control.sock")
        chmod("control.sock", 0o660, dir_fd=fd)
        os.close(fd)
        self.server.listen(4)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        while self.replies:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                try:
                    self.requests.append(hp.read_frame(conn))
                except hp.ProtocolRefused:
                    continue
                reply = self.replies.pop(0)
                if isinstance(reply, bytes):
                    conn.sendall(reply)
                else:
                    hp.write_frame(conn, reply)

    def close(self) -> None:
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.server.close()
        self.thread.join(timeout=5)


def real_client(env: Env) -> Any:
    return c.HandoffClient(
        lease_id=LEASE,
        run_root=str(env.root),
        expected_uid=UID,
        clock_ns=env.clock,
        sleep=env.clock.sleep,
        boot_id_reader=lambda: BOOT,
    )


def test_real_socket_round_trip(env: Env) -> None:
    put(env.lease / "grant.json", grant_doc())
    ctl = FakeController(env, [hp.response("GRANTED"), hp.response("CONSUMED")])
    try:
        target = real_client(env).obtain(
            request_url=REQUEST_URL, expectation=FULL_EXPECT, launch_ns=LAUNCH
        )
    finally:
        ctl.close()
    assert target.address == "192.0.2.10"
    assert [r["op"] for r in ctl.requests] == ["report", "consume"]


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"\x00\x00",
        b"\x00\x00\x00\x00",
        b"\xff\xff\xff\xff",
        b"\x00\x00\x00\x05{}",
        hp.frame(hp.response("GRANTED")) + b"x",
        hp.frame({**hp.response("WAIT"), "extra": 1}),
    ],
)
def test_real_socket_malformed_or_trailing_response_refuses(
    env: Env, reply: bytes
) -> None:
    ctl = FakeController(env, [reply])
    try:
        with pytest.raises(c.HandoffRefused) as e:
            real_client(env).obtain(
                request_url=REQUEST_URL, expectation=FULL_EXPECT, launch_ns=LAUNCH
            )
    finally:
        ctl.close()
    assert refusal(e) == "handoff.transport"


def test_real_socket_wrong_mode_or_symlinked_socket_refuses(env: Env) -> None:
    ctl = FakeController(env, [hp.response("WAIT")])
    try:
        chmod(env.lease / "control.sock", 0o666)
        with pytest.raises(c.HandoffRefused) as e:
            real_client(env).obtain(
                request_url=REQUEST_URL, expectation=FULL_EXPECT, launch_ns=LAUNCH
            )
        assert refusal(e) == "handoff.files"
        chmod(env.lease / "control.sock", 0o660)
        (env.lease / "control.sock").rename(env.lease / "real.sock")
        (env.lease / "control.sock").symlink_to(env.lease / "real.sock")
        with pytest.raises(c.HandoffRefused) as e2:
            real_client(env).obtain(
                request_url=REQUEST_URL, expectation=FULL_EXPECT, launch_ns=LAUNCH
            )
        assert refusal(e2) == "handoff.files"
    finally:
        ctl.close()
    assert ctl.requests == []


def test_real_socket_missing_controller_refuses(env: Env) -> None:
    with pytest.raises(c.HandoffRefused) as e:
        real_client(env).obtain(
            request_url=REQUEST_URL, expectation=FULL_EXPECT, launch_ns=LAUNCH
        )
    assert refusal(e) == "handoff.files"


def test_root_launch_time_preserves_metadata_deadline(env: Env):
    expectation = dataclasses.replace(
        FULL_EXPECT, admission_digest="8" * 64, supplier_digest="9" * 64
    )
    put(
        env.lease / hp.LAUNCH_NAME,
        {
            "protocol": hp.PROTOCOL,
            "lease_id": LEASE,
            "boot_id": BOOT,
            "launched_at_monotonic_ns": LAUNCH,
            "expires_at_monotonic_ns": LEASE_EXPIRY,
            "admission_digest": "8" * 64,
            "supplier_digest": "9" * 64,
        },
    )
    env.clock.now = LAUNCH + 46 * NS
    with pytest.raises(c.HandoffRefused) as caught:
        env.client().obtain_from_launch(
            request_url=REQUEST_URL, expectation=expectation
        )
    assert caught.value.label == "handoff.timeout" and env.sent == []


def test_root_launch_digest_mismatch_prevents_report(env: Env):
    expectation = dataclasses.replace(
        FULL_EXPECT, admission_digest="8" * 64, supplier_digest="9" * 64
    )
    put(
        env.lease / hp.LAUNCH_NAME,
        {
            "protocol": hp.PROTOCOL,
            "lease_id": LEASE,
            "boot_id": BOOT,
            "launched_at_monotonic_ns": LAUNCH,
            "expires_at_monotonic_ns": LEASE_EXPIRY,
            "admission_digest": "a" * 64,
            "supplier_digest": "9" * 64,
        },
    )
    with pytest.raises(c.HandoffRefused) as caught:
        env.client().obtain_from_launch(
            request_url=REQUEST_URL, expectation=expectation
        )
    assert caught.value.label == "handoff.binding" and env.sent == []
