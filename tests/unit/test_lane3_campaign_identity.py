"""Identity ownership contracts; tests execute only on hosted CI."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import lane3_campaign_identity as identity


@pytest.mark.parametrize(
    "uid,gid",
    [
        (True, 30001),
        (0, 30001),
        (-1, 30001),
        (2**32 - 1, 30001),
        (30001, True),
        (30001, 0),
    ],
)
def test_nonzero_exact_identity(uid, gid):
    with pytest.raises(identity.IdentityRefused):
        identity.OwnedIdentity("a" * 32, uid, gid)


@pytest.mark.parametrize("session", ["", "a" * 12, "A" * 32, "../" + "a" * 29])
def test_session_scoped_name_only(session):
    with pytest.raises(identity.IdentityRefused):
        identity.OwnedIdentity(session, 30001, 30002)


def test_exact_name_and_no_caller_ownership_flag():
    owned = identity.OwnedIdentity("a" * 32, 30001, 30002)
    assert owned.name == "l3b8-" + "a" * 12
    with pytest.raises(TypeError):
        identity.OwnedIdentity("a" * 32, 30001, 30002, owned=True)


def test_group_and_user_creation_argv_are_fixed_and_locked(monkeypatch):
    calls = []
    monkeypatch.setattr(identity, "command", lambda argv, **kw: calls.append(argv))
    backend = identity.SystemBackend()
    backend.create_group("l3b8-" + "a" * 12, 30002)
    backend.create_user("l3b8-" + "a" * 12, 30001, 30002)
    assert calls[0] == ["/usr/sbin/groupadd", "--gid", "30002", "l3b8-" + "a" * 12]
    assert calls[1] == [
        "/usr/sbin/useradd",
        "--uid",
        "30001",
        "--gid",
        "30002",
        "--no-user-group",
        "--no-create-home",
        "--home-dir",
        "/nonexistent",
        "--shell",
        "/usr/sbin/nologin",
        "--password",
        "!",
        "--no-log-init",
        "l3b8-" + "a" * 12,
    ]


def test_cleanup_never_requests_home_deletion_or_process_kill(monkeypatch):
    calls = []
    monkeypatch.setattr(identity, "command", lambda argv, **kw: calls.append(argv))
    backend = identity.SystemBackend()
    backend.remove_user("l3b8-" + "a" * 12)
    backend.remove_group("l3b8-" + "a" * 12)
    assert calls == [
        ["/usr/sbin/userdel", "l3b8-" + "a" * 12],
        ["/usr/sbin/groupdel", "l3b8-" + "a" * 12],
    ]


def test_refusal_contains_no_raw_values():
    assert str(identity.IdentityRefused("identity.uncertain")) == "identity.uncertain"


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class Backend:
        root = tmp_path / "identities"
        armed = False
        uncertain = None

        def __init__(self):
            self.users, self.groups, self.removed = {}, {}, []

        def operation_lock(self):
            import contextlib

            return contextlib.nullcontext()

        def verify_closure(self, record):
            if not getattr(self, "resources_closed", True):
                raise identity.IdentityRefused("identity.closure")

        def arm(self, session, seconds):
            record = identity.read_record(self.root / session)
            assert not record["group_attempted"] and not record["user_attempted"]
            assert 1 <= seconds <= 240
            self.armed = True

        def disarm(self, session):
            assert not self.users and not self.groups
            self.armed = False

        def create_group(self, name, gid, **kwargs):
            assert self.armed
            record = identity.read_record(owned.directory)
            assert record["group_attempted"] and not record["group_created"]
            self.groups[name] = SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=[])
            if self.uncertain == "group":
                raise identity.IdentityRefused("identity.lost_response")

        def create_user(self, name, uid, gid, **kwargs):
            assert self.armed
            record = identity.read_record(owned.directory)
            assert (
                record["group_created"]
                and record["user_attempted"]
                and not record["user_created"]
            )
            self.users[name] = SimpleNamespace(
                pw_name=name,
                pw_uid=uid,
                pw_gid=gid,
                pw_dir="/nonexistent",
                pw_shell="/usr/sbin/nologin",
            )
            if self.uncertain == "user":
                raise identity.IdentityRefused("identity.lost_response")

        def remove_user(self, name):
            self.removed.append(("user", name))
            del self.users[name]

        def remove_group(self, name):
            self.removed.append(("group", name))
            del self.groups[name]

    backend = Backend()
    monkeypatch.setattr(identity.os, "geteuid", lambda: 0)
    monkeypatch.setattr(identity, "trusted_directory", lambda *a, **kw: None)
    real_fstat = identity.os.fstat

    def root_fstat(fd):
        s = real_fstat(fd)
        return SimpleNamespace(
            st_mode=s.st_mode, st_uid=0, st_nlink=s.st_nlink, st_size=s.st_size
        )

    monkeypatch.setattr(identity.os, "fstat", root_fstat)

    def by_name(entries, name):
        return entries[name]

    def by_number(entries, number, field):
        for entry in entries.values():
            if getattr(entry, field) == number:
                return entry
        raise KeyError(number)

    monkeypatch.setattr(
        identity.pwd, "getpwnam", lambda name: by_name(backend.users, name)
    )
    monkeypatch.setattr(
        identity.pwd, "getpwuid", lambda uid: by_number(backend.users, uid, "pw_uid")
    )
    monkeypatch.setattr(identity.pwd, "getpwall", lambda: list(backend.users.values()))
    monkeypatch.setattr(
        identity.grp, "getgrnam", lambda name: by_name(backend.groups, name)
    )
    monkeypatch.setattr(
        identity.grp, "getgrgid", lambda gid: by_number(backend.groups, gid, "gr_gid")
    )
    monkeypatch.setattr(identity.os, "getgrouplist", lambda name, gid: [gid])
    monkeypatch.setattr(identity, "no_uid_processes", lambda uid: None)
    owned = identity.OwnedIdentity("b" * 32, 30011, 30012, backend=backend)
    return owned, backend


def test_durable_attempts_timer_and_confirmed_ownership_precede_each_effect(lifecycle):
    owned, backend = lifecycle
    result = owned.create()
    assert result == {"user": owned.name, "uid": 30011, "gid": 30012}
    record = identity.read_record(owned.directory)
    assert record["group_created"] and record["user_created"]
    owned.cleanup()
    assert backend.removed == [("user", owned.name), ("group", owned.name)]
    assert not owned.directory.exists() and not backend.armed


@pytest.mark.parametrize("effect", ["group", "user"])
def test_lost_creation_response_never_grants_deletion_authority(lifecycle, effect):
    owned, backend = lifecycle
    backend.uncertain = effect
    with pytest.raises(identity.IdentityRefused):
        owned.create()
    with pytest.raises(identity.IdentityRefused, match="identity.uncertain"):
        owned.cleanup()
    assert backend.removed == [] and backend.armed and owned.directory.exists()


def test_preexisting_identity_refuses_without_journal_or_timer(lifecycle):
    from types import SimpleNamespace

    owned, backend = lifecycle
    backend.users[owned.name] = SimpleNamespace(pw_uid=owned.uid)
    with pytest.raises(identity.IdentityRefused, match="identity.not_absent"):
        owned.create()
    assert not owned.directory.exists() and not backend.armed


def test_live_uid_processes_retain_confirmed_ownership_without_any_kill(
    lifecycle, monkeypatch
):
    owned, backend = lifecycle
    owned.create()

    def process_exists(uid):
        raise identity.IdentityRefused("identity.processes")

    monkeypatch.setattr(identity, "no_uid_processes", process_exists)
    with pytest.raises(identity.IdentityRefused, match="identity.processes"):
        owned.cleanup()
    assert backend.removed == [] and backend.armed and owned.directory.exists()


@pytest.mark.parametrize("drift", ["user", "member", "foreign_primary"])
def test_drift_keeps_journal_timer_and_foreign_entities(lifecycle, drift):
    from types import SimpleNamespace

    owned, backend = lifecycle
    owned.create()
    if drift == "user":
        backend.users[owned.name].pw_uid += 1
    elif drift == "member":
        backend.groups[owned.name].gr_mem.append("foreign")
    else:
        backend.users["foreign"] = SimpleNamespace(
            pw_name="foreign", pw_uid=30013, pw_gid=owned.gid
        )
    with pytest.raises(identity.IdentityRefused, match="identity.drift"):
        owned.cleanup()
    assert backend.removed == [] and backend.armed and owned.directory.exists()


def test_active_or_unknown_resource_proof_keeps_uid_reserved(lifecycle):
    owned, backend = lifecycle
    owned.create()
    backend.resources_closed = False
    with pytest.raises(identity.IdentityRefused, match="identity.closure"):
        owned.cleanup()
    assert backend.removed == [] and backend.armed and owned.directory.exists()
    backend.resources_closed = True
    owned.cleanup()
    assert backend.removed == [("user", owned.name), ("group", owned.name)]


def test_independent_timer_limits_total_start_attempts_including_runtime_kills(
    monkeypatch,
):
    import stat
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(identity, "trusted_directory", lambda *a, **kw: None)
    monkeypatch.setattr(
        identity.Path,
        "lstat",
        lambda p: SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_nlink=1),
    )
    monkeypatch.setattr(identity, "command", lambda argv, **kwargs: calls.append(argv))
    identity.SystemBackend().arm("d" * 32, 240)
    argv = calls[0]
    assert "--property=StartLimitIntervalSec=infinity" in argv
    assert "--property=StartLimitBurst=30" in argv
    assert "--property=RuntimeMaxSec=30" in argv
    assert "--property=Restart=on-failure" in argv
    assert all("reset-failed" not in call for call in calls)
