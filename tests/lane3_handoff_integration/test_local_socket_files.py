"""Design row "Local socket/files" (hosted Linux integration, as root).

Wrong UID/cgroup, symlink/socket replacement, writable parent, forged grant,
duplicate JSON keys, huge/truncated message, stalled peer and second consumer
refuse; the runner cannot invoke root operations. Each refusal is paired with
the positive path through the same real socket and files.
"""

from __future__ import annotations

import os
import pathlib
import pwd
import stat
import subprocess

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import pytest
from conftest import (
    BOOTSTRAP_ADDRESS,
    OTHER_ADDRESS,
    TLS_ADDRESS,
    Env,
    owned_rules,
)
from lane3_broker_origin import OriginPolicy


def _ids(env: Env, lease: str) -> tuple[int, int, str]:
    j = env.journal()
    assert j is not None and j["lease_id"] == lease
    return j["uid"], j["gid"], j["units"]["runner"]


def test_positive_report_grant_consume_and_pinned_tls(env: Env) -> None:
    lease = env.start(["obtain", "tls", OTHER_ADDRESS])
    reply = env.grant(lease)
    assert reply["state"] == "GRANTED"
    out = env.job_output()
    assert out == {
        "address": TLS_ADDRESS,
        "other": "blocked",
        "result": "granted",
        "tls": "ok",
        "wrong_sni": "tls.refused",
        "wrong_ca": "tls.refused",
        "stale": "tls.refused",
        "delayed": "tls.refused",
    }
    j = env.wait_state(lease, {"CONSUMED"})
    addresses = sorted(
        e["match"]["right"]
        for r in owned_rules()
        for e in r["expr"]
        if "match" in e
        and e["match"]["left"].get("payload", {}).get("field") == "daddr"
    )
    assert addresses == sorted([BOOTSTRAP_ADDRESS, TLS_ADDRESS])
    assert j["grant"]["snapshot"]["addresses"] == [
        {"family": 4, "address": TLS_ADDRESS}
    ]
    origins = OriginPolicy.from_mapping(env.policy["origins"])
    assert j["grant"]["policy_digest"] == origins.digest
    closed = env.cleanup(lease)
    assert closed["evidence"]["outcome"] == "COMPLETED"
    assert closed["evidence"]["gate0_accepted"] is False
    env.assert_clean(lease)


def test_lease_files_have_exact_owner_group_and_modes(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    uid, gid, _ = _ids(env, lease)
    directory = env.paths.run_root / lease
    for path, mode, kind in (
        (directory, hp.LEASE_DIR_MODE, stat.S_ISDIR),
        (directory / hp.SOCKET_NAME, hp.SOCKET_MODE, stat.S_ISSOCK),
        (directory / hp.CHALLENGE_NAME, hp.FILE_MODE, stat.S_ISREG),
    ):
        info = path.lstat()
        assert kind(info.st_mode) and stat.S_IMODE(info.st_mode) == mode
        assert (info.st_uid, info.st_gid) == (0, gid)
    journal = env.paths.journal.lstat()
    assert stat.S_IMODE(journal.st_mode) == 0o600 and journal.st_uid == 0
    assert stat.S_IMODE(env.paths.state_root.lstat().st_mode) == 0o700
    assert uid != 0


def test_runner_cannot_replace_files_or_invoke_root_operations(env: Env) -> None:
    lease = env.start(["attack"])
    out = env.job_output()
    attempts = out["attempts"]
    assert attempts.pop("controller_cli_exit") == "2"
    assert set(attempts.values()) == {"denied"}, attempts
    j = env.journal()
    assert j is not None and j["state"] == "BOOTSTRAP"
    assert (env.paths.run_root / lease / hp.SOCKET_NAME).exists()


def test_wrong_uid_with_the_task_group_is_refused_and_ends_the_lease(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    _, gid, unit = _ids(env, lease)
    subprocess.run(  # noqa: S603
        [
            "/usr/sbin/useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            "l3hitother",
        ],
        check=True,
        capture_output=True,
    )
    other = pwd.getpwnam("l3hitother").pw_uid
    env.host.spawn(unit, other, gid, lease[:12], ["report"])
    assert env.job_output()["reply"] == hp.response("REFUSED", "peer.refused")
    record = env.wait_state(lease, {"CLOSED"})
    assert record["category"] == "peer.refused"
    env.assert_clean(lease)


def test_a_process_outside_the_task_group_cannot_connect(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    _, _, unit = _ids(env, lease)
    subprocess.run(  # noqa: S603
        [
            "/usr/sbin/useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "l3hitother",
        ],
        check=True,
        capture_output=True,
    )
    entry = pwd.getpwnam("l3hitother")
    env.host.spawn(unit, entry.pw_uid, entry.pw_gid, lease[:12], ["report"])
    assert env.job_output().get("exit", 0) != 0
    j = env.journal()
    assert j is not None and j["state"] == "BOOTSTRAP"  # nothing reached it


def test_right_uid_outside_the_exact_cgroup_is_refused(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    uid, gid, unit = _ids(env, lease)
    env.host.spawn(unit, uid, gid, lease[:12], ["report"], in_cgroup=False)
    assert env.job_output()["reply"] == hp.response("REFUSED", "peer.refused")
    env.wait_state(lease, {"CLOSED"})
    env.assert_clean(lease)


def test_right_uid_and_cgroup_reports_positively(env: Env) -> None:
    # Sensitivity for the two peer tests above.
    lease = env.start(["sleep", "20"])
    uid, gid, unit = _ids(env, lease)
    env.host.spawn(unit, uid, gid, lease[:12], ["report"])
    assert env.job_output()["reply"] == hp.response("WAIT")
    assert env.wait_state(lease, {"REPORTED"})["state"] == "REPORTED"


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("duplicate", "frame.invalid"),
        ("huge", "frame.invalid"),
        ("truncated", "frame.invalid"),
        ("stall", "frame.invalid"),
        ("mgmt", "schema.invalid"),
        ("wrong-nonce", "nonce.mismatch"),
    ],
)
def test_malformed_or_stalled_messages_refuse_and_end_the_lease(
    env: Env, kind: str, category: str
) -> None:
    lease = env.start(["raw", kind])
    reply = env.job_output(timeout=40)["reply"]
    assert reply in (hp.response("REFUSED", category), {"status": "NO_RESPONSE"})
    record = env.wait_state(lease, {"CLOSED"})
    assert record["outcome"] == "REFUSED"
    env.assert_clean(lease)


def test_forged_grant_digest_refuses(env: Env) -> None:
    lease = env.start(["report-forged-consume"])
    env.grant(lease)
    out = env.job_output()
    assert out["poll"] == hp.response("GRANTED")
    assert out["reply"] == hp.response("REFUSED", "grant.mismatch")
    env.wait_state(lease, {"CLOSED"})
    env.assert_clean(lease)


def test_second_consumer_in_the_same_cgroup_refuses(env: Env) -> None:
    lease = env.start(["obtain", "second-consume"])
    uid, gid, unit = _ids(env, lease)
    env.grant(lease)
    env.wait_state(lease, {"CONSUMED"})
    env.host.spawn(unit, uid, gid, lease[:12], ["report-then-consume-again"])
    assert env.job_output()["reply"] == hp.response("REFUSED", "peer.refused")
    env.wait_state(lease, {"CLOSED"})
    env.assert_clean(lease)


def test_metadata_deadline_counts_from_launch_and_never_resets(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    uid, gid, unit = _ids(env, lease)
    env.host.offset_ns = hp.METADATA_DEADLINE_NS + 10**9
    env.host.spawn(unit, uid, gid, lease[:12], ["report"])
    assert env.job_output()["reply"] == hp.response("REFUSED", "deadline.insufficient")
    env.wait_state(lease, {"CLOSED"})
    env.assert_clean(lease)


# ── controller-side path discipline (root, real filesystem) ────────────────


def test_descriptor_walk_refuses_writable_parent_symlink_and_foreign_owner(
    env: Env,
) -> None:
    base = env.base / "paths"
    base.mkdir(mode=0o755)
    good = base / "good.json"
    good.write_text("{}")
    good.chmod(0o644)
    assert hc.read_protected(good) == b"{}"

    writable = base / "writable"
    writable.mkdir()
    writable.chmod(0o777)
    (writable / "f.json").write_text("{}")
    link = base / "link"
    link.symlink_to(base)
    foreign = base / "foreign.json"
    foreign.write_text("{}")
    os.chown(foreign, 65534, 65534)
    linked = base / "hard.json"
    os.link(good, linked)
    file_link = base / "file-link.json"
    file_link.symlink_to(good)
    for path in (
        writable / "f.json",
        link / "good.json",
        foreign,
        good,  # now has two links
        file_link,
    ):
        with pytest.raises(hc.ControllerRefused):
            hc.read_protected(path)
    os.unlink(linked)
    assert hc.read_protected(good) == b"{}"  # sensitivity


def test_a_tampered_lease_directory_blocks_grant_publication(env: Env) -> None:
    lease = env.start(["report"])
    env.wait_state(lease, {"REPORTED"})
    (env.paths.run_root / lease).chmod(0o770)
    with pytest.raises(hc.ControllerRefused):
        env.grant(lease)
    assert not (env.paths.run_root / lease / hp.GRANT_NAME).exists()
    # Cleanup contained the UID but refused to trust the tampered directory;
    # it stays blocked (journal kept, UID reserved) until management repairs.
    j = env.journal()
    assert j is not None and j["cleanup_blocked"] == "path.unsafe"
    assert j["grant_digest"] is None and j["state"] == "REFUSED"
    assert j["cleanup"]["processes_absent"] is True
    pwd.getpwnam(j["user"])  # UID still reserved
    (env.paths.run_root / lease).chmod(0o750)
    env.cleanup(lease)  # resumes; never new authority
    env.assert_clean(lease)


def test_a_journal_from_another_boot_only_cleans_up(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    controller = env.controller()
    with controller.locked():
        j = controller.load()
        assert j is not None
        j["boot_id"] = "00000000-0000-0000-0000-000000000000"
        controller.save(j)
    with pytest.raises(hc.ControllerRefused) as caught:
        env.controller().status(
            {"protocol": hp.PROTOCOL, "op": "status", "lease_id": lease}
        )
    assert caught.value.label == "boot.mismatch"
    result = env.controller().reconcile()
    assert result["state"] == "CLOSED"
    env.assert_clean(lease)
    assert pathlib.Path(env.paths.state_root / "closed" / f"{lease}.json").exists()
