"""E7 / T5: pinned host key object, full SSH argv, closed artifact set, bytes
hashed once and sent as hashed, Foundation lease filename, hash-pinned install,
a fixed launch wrapper with an allowlisted environment, and the CP skew command."""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import shlex
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_stage as stage  # noqa: E402

BLOB = b"not a real key, a stand-in blob"
RUN_DIR = "/run/lane3.ABCDEFGH12"
KEY_B64 = base64.b64encode(BLOB).decode("ascii")
FINGERPRINT = "SHA256:" + base64.b64encode(
    hashlib.sha256(BLOB).digest()
).decode().rstrip("=")
DESTINATION = "root@target.example.invalid"


def _known_hosts(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "source_known_hosts"
    path.write_text(text, encoding="ascii")
    return path


def _line(host: str = "target.example.invalid") -> str:
    return f"{host} ssh-ed25519 {KEY_B64}\n"


def _pin(tmp_path: pathlib.Path, text: str | None = None) -> stage.PinnedHostKey:
    source = _known_hosts(tmp_path, _line() if text is None else text)
    return stage.PinnedHostKey.from_file(
        source, FINGERPRINT, private_dir=tmp_path / "pin"
    )


# ── the pinned host key object ──────────────────────────────────────────────


def test_a_matching_single_key_is_pinned_into_a_private_copy(
    tmp_path: pathlib.Path,
) -> None:
    pin = _pin(tmp_path, "# comment\n" + _line())
    assert pin.fingerprint == FINGERPRINT
    assert pin.alias.startswith("lane3-")
    copy = pin.known_hosts
    assert copy.read_text() == f"{pin.alias} ssh-ed25519 {KEY_B64}\n"
    assert "target.example.invalid" not in copy.read_text()
    assert (copy.stat().st_mode & 0o777) == 0o600
    assert (copy.parent.stat().st_mode & 0o777) == 0o700
    assert copy != tmp_path / "source_known_hosts"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (_line() + _line("other.example.invalid"), "known-hosts-not-exactly-one-key"),
        ("", "known-hosts-not-exactly-one-key"),
        (f"@cert-authority * ssh-ed25519 {KEY_B64}\n", "known-hosts-line-shape"),
        (f"|1|abc|def ssh-ed25519 {KEY_B64}\n", "known-hosts-line-shape"),
        (_line("*.example.invalid"), "known-hosts-host-pattern"),
        (_line("a.example.invalid,b.example.invalid"), "known-hosts-host-pattern"),
        ("host ssh-ed25519\n", "known-hosts-line-shape"),
        ("host ssh-ed25519 !!!notbase64\n", "known-hosts-key-invalid"),
        (f"host ssh-ed25519; {KEY_B64}\n", "known-hosts-key-type"),
    ],
)
def test_a_file_that_is_not_a_pin_is_refused(
    tmp_path: pathlib.Path, text: str, code: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _pin(tmp_path, text)
    assert refusal.value.code == code
    assert not (tmp_path / "pin" / "known_hosts").exists()


def test_another_keys_fingerprint_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.PinnedHostKey.from_file(
            _known_hosts(tmp_path, _line()),
            "SHA256:" + "A" * 43,
            private_dir=tmp_path / "pin",
        )
    assert refusal.value.code == "host-key-fingerprint-mismatch"


@pytest.mark.parametrize("expected", ["", None, "SHA256:short", "MD5:aa", 5])
def test_the_expected_fingerprint_is_required(
    tmp_path: pathlib.Path, expected: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.PinnedHostKey.from_file(
            _known_hosts(tmp_path, _line()),
            expected,  # type: ignore[arg-type]
            private_dir=tmp_path / "pin",
        )
    assert refusal.value.code == "host-key-fingerprint-required"


def test_the_private_copy_is_create_only(tmp_path: pathlib.Path) -> None:
    _pin(tmp_path)
    with pytest.raises(stage.StageRefused) as refusal:
        stage.PinnedHostKey.from_file(
            tmp_path / "source_known_hosts", FINGERPRINT, private_dir=tmp_path / "pin"
        )
    assert refusal.value.code == "private-file-unavailable"


def test_a_private_directory_others_can_enter_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    with pytest.raises(stage.StageRefused) as refusal:
        stage.PinnedHostKey.from_file(
            _known_hosts(tmp_path, _line()), FINGERPRINT, private_dir=open_dir
        )
    assert refusal.value.code == "private-directory-unsafe"


def test_a_swapped_private_copy_is_caught_at_use_not_just_at_check(
    tmp_path: pathlib.Path,
) -> None:
    pin = _pin(tmp_path)
    pin.known_hosts.write_text(f"{pin.alias} ssh-ed25519 AAAAdifferentkey\n")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.ssh_argv(
            destination=DESTINATION,
            identity="/run/key",
            pin=pin,
            connect_timeout=20,
            remote="true",
        )
    assert refusal.value.code == "known-hosts-copy-changed"


def test_a_private_copy_that_became_group_readable_is_caught(
    tmp_path: pathlib.Path,
) -> None:
    pin = _pin(tmp_path)
    os.chmod(pin.known_hosts, 0o640)
    with pytest.raises(stage.StageRefused) as refusal:
        pin.reverify()
    assert refusal.value.code == "known-hosts-copy-changed"


# ── ssh: the whole argv ─────────────────────────────────────────────────────


def test_the_full_ssh_argv_is_exactly_this(tmp_path: pathlib.Path) -> None:
    pin = _pin(tmp_path)
    assert stage.ssh_argv(
        destination=DESTINATION,
        identity="/run/key",
        pin=pin,
        connect_timeout=20,
        remote="true",
    ) == [
        "ssh",
        "-T",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "VerifyHostKeyDNS=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "KnownHostsCommand=none",
        "-o",
        "ProxyCommand=none",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "ControlMaster=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        f"HostKeyAlias={pin.alias}",
        "-o",
        f"UserKnownHostsFile={pin.known_hosts}",
        "-i",
        "/run/key",
        "--",
        DESTINATION,
        "true",
    ]


@pytest.mark.parametrize("bad", ["", "-oProxyCommand=x", "has space"])
def test_ssh_inputs_that_could_inject_options_are_refused(
    tmp_path: pathlib.Path, bad: str
) -> None:
    with pytest.raises(stage.StageRefused):
        stage.ssh_argv(
            destination=bad,
            identity="/k",
            pin=_pin(tmp_path),
            connect_timeout=20,
            remote="true",
        )


@pytest.mark.parametrize("timeout", [0, 121, -1, True, 1.5, None])
def test_the_connect_timeout_is_required_and_bounded(
    tmp_path: pathlib.Path, timeout: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.ssh_argv(
            destination=DESTINATION,
            identity="/k",
            pin=_pin(tmp_path),
            connect_timeout=timeout,  # type: ignore[arg-type]
            remote="true",
        )
    assert refusal.value.code == "ssh-connect-timeout"


# ── staged files: hashed once, sent as hashed ───────────────────────────────


def test_a_file_is_read_once_and_the_hashed_bytes_are_the_staged_bytes(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "a.whl"
    path.write_bytes(b"first")
    staged = stage.load_staged_file(path, hashlib.sha256(b"first").hexdigest())
    path.write_bytes(b"swapped after hashing")
    assert staged.data == b"first"
    assert hashlib.sha256(staged.data).hexdigest() == staged.sha256


def test_a_file_that_does_not_hash_to_the_pin_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "a.whl"
    path.write_bytes(b"content")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.load_staged_file(path, "0" * 64)
    assert refusal.value.code == "artifact-digest-mismatch"
    with pytest.raises(stage.StageRefused) as refusal:
        stage.load_staged_file(path, "short")
    assert refusal.value.code == "artifact-digest-required"


# ── staging ─────────────────────────────────────────────────────────────────


class FakeTarget:
    """A target that records what was staged and answers sha256sum honestly."""

    def __init__(self, *, corrupt: str = "", run_dir: str = RUN_DIR) -> None:
        self.commands: list[str] = []
        self.argvs: list[list[str]] = []
        self.files: dict[str, bytes] = {}
        self.corrupt = corrupt
        self.run_dir = run_dir

    def __call__(self, argv, stdin):
        command = argv[-1]
        self.argvs.append(list(argv))
        self.commands.append(command)
        if command.startswith("umask 077 && mktemp"):
            return stage.RunResult(0, (self.run_dir + "\n").encode())
        if command.startswith("umask 077 && cat >"):
            path = shlex.split(command)[5]
            self.files[path] = stdin or b""
            return stage.RunResult(0)
        if command.startswith("sha256sum"):
            path = shlex.split(command)[2]
            data = self.files[path]
            if self.corrupt and path.endswith(self.corrupt):
                data += b"x"
            line = f"{hashlib.sha256(data).hexdigest()}  {path}\n"
            return stage.RunResult(0, line.encode())
        return stage.RunResult(0)


def _file(content: bytes) -> stage.StagedFile:
    return stage.StagedFile(data=content, sha256=hashlib.sha256(content).hexdigest())


def _artifacts() -> dict[str, stage.StagedFile]:
    return {name: _file(f"content of {name}".encode()) for name in stage.ARTIFACT_NAMES}


WHEELS = {
    "dotmac_deployment_foundation-0.4.0a2-py3-none-any.whl": _file(b"foundation"),
    "vendor_cp-0.1.0-py3-none-any.whl": _file(b"cp bindings"),
    "dotmac_kernel-0.1.0a109-py3-none-any.whl": _file(b"kernel"),
    "dotmac_deployment_control-0.1.0a17-py3-none-any.whl": _file(b"control"),
}


def _stage(target: FakeTarget, tmp_path: pathlib.Path, **overrides):
    arguments = {
        "run": target,
        "destination": DESTINATION,
        "identity": "/run/key",
        "pin": _pin(tmp_path),
        "connect_timeout": 20,
        "host_id": "host-a",
        "artifacts": _artifacts(),
        "lease": _file(b"lease record"),
        "lease_filename": "host-a.json",
        "wheelhouse": WHEELS,
    }
    arguments.update(overrides)
    return stage.stage_artifacts(**arguments)


def test_every_artifact_is_shipped_and_verified_by_digest(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    assert _stage(target, tmp_path) == RUN_DIR
    expected = {f"{RUN_DIR}/{n}" for n in stage.ARTIFACT_NAMES}
    expected |= {f"{RUN_DIR}/wheelhouse/{n}" for n in WHEELS}
    expected |= {f"{RUN_DIR}/lease/host-a.json", f"{RUN_DIR}/requirements.txt"}
    assert set(target.files) == expected
    secret = [c for c in target.commands if "consumption-dsn" in c and "chmod" in c]
    assert secret and "chmod 0600" in secret[0]


def test_every_remote_call_uses_the_pinned_argv_shape(tmp_path: pathlib.Path) -> None:
    target = FakeTarget()
    _stage(target, tmp_path)
    for argv in target.argvs:
        assert argv[:4] == ["ssh", "-T", "-F", "/dev/null"]
        assert "StrictHostKeyChecking=yes" in argv
        assert any(a.startswith("HostKeyAlias=lane3-") for a in argv)


def test_the_lease_is_staged_under_foundations_filename_not_lease_json(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    _stage(target, tmp_path)
    assert f"{RUN_DIR}/lease/host-a.json" in target.files
    assert f"{RUN_DIR}/lease.json" not in target.files
    assert not any(path.endswith("/lease.json") for path in target.files)


def test_foundations_own_naming_convention_decides_the_filename() -> None:
    assert stage.expected_lease_filename("host-a") == "host-a.json"
    assert stage.expected_lease_filename("a/b c") == "a_b_c.json"


@pytest.mark.parametrize("name", ["lease.json", "host-b.json", "host-a.json.bak", ""])
def test_a_lease_name_other_than_foundations_is_refused(
    tmp_path: pathlib.Path, name: str
) -> None:
    target = FakeTarget()
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, tmp_path, lease_filename=name)
    assert refusal.value.code == "lease-filename-not-foundation-convention"
    assert not target.commands


def test_the_install_is_isolated_hash_pinned_and_from_staged_files_only(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    _stage(target, tmp_path)
    install = next(c for c in target.commands if "pip install" in c)
    for flag in ("--no-deps", "--no-index", "--require-hashes", "-r requirements.txt"):
        assert flag in install
    requirements = target.files[f"{RUN_DIR}/requirements.txt"].decode()
    for name, wheel in WHEELS.items():
        assert f"./wheelhouse/{name} --hash=sha256:{wheel.sha256}\n" in requirements
    assert len(requirements.splitlines()) == len(WHEELS)


def test_the_wheelhouse_is_required_and_holds_only_wheels(
    tmp_path: pathlib.Path,
) -> None:
    cases = ({}, {"evil.tar.gz": _file(b"x")}, {"../x.whl": _file(b"x")})
    for index, wheelhouse in enumerate(cases):
        directory = tmp_path / f"case{index}"
        directory.mkdir()
        target = FakeTarget()
        with pytest.raises(stage.StageRefused) as refusal:
            _stage(target, directory, wheelhouse=wheelhouse)
        assert refusal.value.code == "wheelhouse-required"
        assert not target.commands


def test_a_staged_file_whose_bytes_changed_after_hashing_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    artifacts = _artifacts()
    artifacts["scripts.tar"] = stage.StagedFile(
        data=b"changed", sha256=artifacts["scripts.tar"].sha256
    )
    target = FakeTarget()
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, tmp_path, artifacts=artifacts)
    assert refusal.value.code == "artifact-digest-mismatch:scripts.tar"
    assert not target.commands


def test_the_bytes_sent_are_exactly_the_bytes_that_were_hashed(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    artifacts = _artifacts()
    _stage(target, tmp_path, artifacts=artifacts)
    for name, staged in artifacts.items():
        assert target.files[f"{RUN_DIR}/{name}"] == staged.data


def test_a_target_side_digest_mismatch_is_refused(tmp_path: pathlib.Path) -> None:
    target = FakeTarget(corrupt="vendor_cp-0.1.0-py3-none-any.whl")
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, tmp_path)
    assert refusal.value.code.startswith("stage-digest-mismatch:wheelhouse/vendor_cp")


def test_a_name_outside_the_closed_set_is_refused(tmp_path: pathlib.Path) -> None:
    artifacts = _artifacts()
    artifacts["../evil"] = artifacts["scripts.tar"]
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(), tmp_path, artifacts=artifacts)
    assert refusal.value.code == "artifact-set-not-the-closed-set"


def test_a_missing_artifact_is_refused(tmp_path: pathlib.Path) -> None:
    artifacts = _artifacts()
    del artifacts["consumption-dsn"]
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(), tmp_path, artifacts=artifacts)
    assert refusal.value.code == "artifact-set-not-the-closed-set"


def test_an_unexpected_run_directory_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(run_dir="/tmp/elsewhere"), tmp_path)  # noqa: S108
    assert refusal.value.code == "run-directory-unexpected"


# ── launch and clock ────────────────────────────────────────────────────────


def _common(tmp_path: pathlib.Path) -> dict:
    return {
        "run_dir": RUN_DIR,
        "destination": DESTINATION,
        "identity": "/run/key",
        "pin": _pin(tmp_path),
        "connect_timeout": 20,
    }


def test_the_session_launches_isolated_with_materials_by_path(
    tmp_path: pathlib.Path,
) -> None:
    remote = stage.launch_argv(
        **_common(tmp_path),
        extra_env={"AUTHORIZATION_VERIFICATION_IDENTITY": "/run/lane3.x/id.json"},
    )[-1]
    assert "venv/bin/python -E -P scripts/lane3_target_session.py" in remote
    assert f"HOST_ADMISSION_MATERIALS={RUN_DIR}/host-admission.json" in remote
    assert f"EXECUTION_CONTEXT_MATERIALS={RUN_DIR}/execution-context.json" in remote
    assert "AUTHORIZATION_VERIFICATION_IDENTITY=/run/lane3.x/id.json" in remote
    # The secret is read from its file by the wrapper, never placed in the argv.
    assert '"$(cat consumption-dsn)"' in remote


def test_cps_skew_command_runs_on_the_target_before_the_session(
    tmp_path: pathlib.Path,
) -> None:
    remote = stage.clock_skew_argv(**_common(tmp_path), extra_env={})[-1]
    assert "-m vendor_cp.deployment.database_clock" in remote


def test_the_environment_allowlist_is_exactly_the_cp_pin_names() -> None:
    assert stage.ENVIRONMENT_ALLOWLIST == {
        "AUTHORIZATION_VERIFICATION_IDENTITY",
        "DISPATCH_VERIFICATION_IDENTITY",
        "RELEASE_EVIDENCE_VERIFICATION_IDENTITY",
        "RELEASE_EVIDENCE_REPOSITORY",
        "DISPATCH_CLOCK_SKEW_LIMIT_SECONDS",
    }


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "LD_PRELOAD",
        "PYTHONPATH",
        "DATABASE_URL",
        "DISPATCH_CONSUMPTION_DATABASE_URL",
        "SSH_AUTH_SOCK",
        "HTTPS_PROXY",
        "SOME_UNLISTED_NAME",
        "lowercase",
        "A",
    ],
)
def test_a_name_not_on_the_allowlist_is_refused(
    tmp_path: pathlib.Path, name: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.launch_argv(**_common(tmp_path), extra_env={name: "value"})
    assert refusal.value.code == "environment-name-refused"


def test_an_environment_value_with_a_control_character_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.launch_argv(
            **_common(tmp_path),
            extra_env={"DISPATCH_VERIFICATION_IDENTITY": "a\nb"},
        )
    assert refusal.value.code == "environment-value-refused"


def test_a_run_directory_outside_run_lane3_is_refused_everywhere(
    tmp_path: pathlib.Path,
) -> None:
    bad = dict(_common(tmp_path), run_dir="/run/other")
    with pytest.raises(stage.StageRefused):
        stage.launch_argv(**bad, extra_env={})
    with pytest.raises(stage.StageRefused):
        stage.clock_skew_argv(**bad, extra_env={})
    with pytest.raises(stage.StageRefused):
        stage.teardown_argv(**bad)
