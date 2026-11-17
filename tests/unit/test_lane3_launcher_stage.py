"""E7 / T5: pinned host key object, full SSH argv, closed artifact set, bytes
hashed once and sent as hashed, Foundation lease filename, hash-pinned install,
a fixed launch wrapper with an allowlisted environment, and the CP skew command."""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import shlex
import struct
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_stage as stage  # noqa: E402

RAW_KEY = bytes(range(32))
# An OpenSSH wire-format ed25519 blob: string "ssh-ed25519", string <32 raw bytes>.
BLOB = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + RAW_KEY
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


ALIAS = "vm-alpha"
OTHER_FINGERPRINT = "SHA256:" + "B" * 43


def _lease(fingerprint: str = FINGERPRINT, host: str = "host-a", run: str = "run-1"):
    import types

    return types.SimpleNamespace(
        controller_identity_fingerprint=fingerprint,
        target=host,
        authorization_run_id=run,
    )


def _identity(
    tmp_path: pathlib.Path,
    fingerprint: str = FINGERPRINT,
    *,
    host: str = "host-a",
    run: str = "run-1",
):
    """The identity `for_lease` builds for a held lease (idempotent per dir)."""
    key = tmp_path / "id_launch"
    key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="ascii")
    pub = tmp_path / "id_launch.pub"
    pub.write_text(f"ssh-ed25519 {KEY_B64} launch-key\n", encoding="ascii")
    return stage.LaunchIdentity.for_lease(key, _lease(fingerprint, host, run))


def _from_file(
    tmp_path: pathlib.Path,
    text: str | None = None,
    *,
    fingerprint: object = FINGERPRINT,
    alias: object = ALIAS,
    private_dir: pathlib.Path | None = None,
) -> stage.PinnedHostKey:
    source = tmp_path / "source_known_hosts"
    if text is not None or not source.exists():
        source.write_text(_line() if text is None else text, encoding="ascii")
    return stage.PinnedHostKey.from_file(
        source,
        fingerprint,  # type: ignore[arg-type]
        alias=alias,  # type: ignore[arg-type]
        private_dir=tmp_path / "pin" if private_dir is None else private_dir,
    )


def _pin(tmp_path: pathlib.Path, text: str | None = None) -> stage.PinnedHostKey:
    return _from_file(tmp_path, text)


# ── the pinned host key object ──────────────────────────────────────────────


def test_a_matching_single_key_is_pinned_into_a_private_copy(
    tmp_path: pathlib.Path,
) -> None:
    pin = _pin(tmp_path, "# comment\n" + _line())
    assert pin.fingerprint == FINGERPRINT
    assert pin.alias == ALIAS
    copy = pin.known_hosts
    assert copy.read_text() == f"{ALIAS} ssh-ed25519 {KEY_B64}\n"
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
        (f"host ssh-ed25519; {KEY_B64}\n", "host-key-not-ed25519"),
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
        _from_file(tmp_path, fingerprint="SHA256:" + "A" * 43)
    assert refusal.value.code == "host-key-fingerprint-mismatch"


@pytest.mark.parametrize("expected", ["", None, "SHA256:short", "MD5:aa", 5])
def test_the_expected_fingerprint_is_required(
    tmp_path: pathlib.Path, expected: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path, fingerprint=expected)
    assert refusal.value.code == "host-key-fingerprint-required"


def test_the_private_copy_is_create_only(tmp_path: pathlib.Path) -> None:
    _pin(tmp_path)
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path)
    assert refusal.value.code == "private-file-unavailable"


def test_a_private_directory_others_can_enter_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path, private_dir=open_dir)
    assert refusal.value.code == "private-directory-unsafe"


def test_a_swapped_private_copy_is_caught_at_use_not_just_at_check(
    tmp_path: pathlib.Path,
) -> None:
    pin = _pin(tmp_path)
    pin.known_hosts.write_text(f"{pin.alias} ssh-ed25519 AAAAdifferentkey\n")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.ssh_argv(
            destination=DESTINATION,
            identity=_identity(tmp_path),
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
        identity=_identity(tmp_path),
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
        str(_identity(tmp_path).private_key),
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
            identity=_identity(tmp_path),
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
            identity=_identity(tmp_path),
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
        "identity": _identity(tmp_path),
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
        assert f"HostKeyAlias={ALIAS}" in argv


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
        "identity": _identity(tmp_path),
        "pin": _pin(tmp_path),
        "connect_timeout": 20,
    }


def _proof(tmp_path: pathlib.Path, **overrides) -> stage.ClockSkewPassed:
    """A skew proof obtained the only way there is: through the check."""
    return _skew(tmp_path, 0, **overrides)


def _launch(tmp_path: pathlib.Path, **overrides):
    arguments = {
        "run_dir": RUN_DIR,
        "destination": DESTINATION,
        "connect_timeout": 20,
        "extra_env": {},
        "run_id": "run-1",
        "host_id": "host-a",
    }
    arguments.update(overrides)
    if "identity" not in arguments:
        arguments["identity"] = _identity(
            tmp_path, host=arguments["host_id"], run=arguments["run_id"]
        )
    if "pin" not in arguments:
        arguments["pin"] = _pin(tmp_path)
    if "skew" not in arguments:
        keys = (
            "run_dir",
            "run_id",
            "host_id",
            "destination",
            "identity",
            "pin",
            "connect_timeout",
        )
        arguments["skew"] = stage.require_clock_skew(
            run=lambda argv, stdin: stage.RunResult(0),
            extra_env={},
            **{k: arguments[k] for k in keys},
        )
    return stage.launch_argv(**arguments)


def test_the_session_launches_isolated_with_materials_by_path(
    tmp_path: pathlib.Path,
) -> None:
    remote = _launch(
        tmp_path,
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
        _launch(tmp_path, extra_env={name: "value"})
    assert refusal.value.code == "environment-name-refused"


def test_an_environment_value_with_a_control_character_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(tmp_path, extra_env={"DISPATCH_VERIFICATION_IDENTITY": "a\nb"})
    assert refusal.value.code == "environment-value-refused"


def test_a_run_directory_outside_run_lane3_is_refused_everywhere(
    tmp_path: pathlib.Path,
) -> None:
    bad = dict(_common(tmp_path), run_dir="/run/other")
    with pytest.raises(stage.StageRefused):
        _launch(tmp_path, run_dir="/run/other", pin=bad["pin"])
    with pytest.raises(stage.StageRefused):
        stage.clock_skew_argv(**bad, extra_env={})
    with pytest.raises(stage.StageRefused):
        stage.teardown_argv(**bad)


# ── U3 (option B): the SSH host key is pinned on its own ────────────────────


@pytest.mark.parametrize(
    "key_type", ["ssh-rsa", "ecdsa-sha2-nistp256", "sk-ssh-ed25519@openssh.com"]
)
def test_a_key_that_is_not_ed25519_refuses(
    tmp_path: pathlib.Path, key_type: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path, f"target.example.invalid {key_type} {KEY_B64}\n")
    assert refusal.value.code == "host-key-not-ed25519"
    assert not (tmp_path / "pin" / "known_hosts").exists()


@pytest.mark.parametrize(
    "alias", ["", None, "Has Space", "UPPER", "-lead", "a" * 64, 5]
)
def test_the_vm_alias_is_a_required_typed_input(
    tmp_path: pathlib.Path, alias: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path, alias=alias)
    assert refusal.value.code == "host-key-alias-required"


def test_the_vm_alias_is_the_host_key_alias_and_the_copys_host_token(
    tmp_path: pathlib.Path,
) -> None:
    pin = _from_file(tmp_path, alias="vm-0042.lane3")
    argv = stage.ssh_argv(
        destination=DESTINATION,
        identity=_identity(tmp_path),
        pin=pin,
        connect_timeout=20,
        remote="true",
    )
    assert "HostKeyAlias=vm-0042.lane3" in argv
    assert pin.known_hosts.read_text().split()[0] == "vm-0042.lane3"


def test_pinning_runs_no_ssh_at_all(tmp_path: pathlib.Path) -> None:
    """The check is pure: constructing the pin takes no runner and connects to
    nothing, so a mismatch is refused before any SSH beyond it."""
    import inspect

    assert "run" not in inspect.signature(stage.PinnedHostKey.from_file).parameters
    with pytest.raises(stage.StageRefused):
        _from_file(tmp_path, fingerprint=OTHER_FINGERPRINT)


# ── the clock: CP's skew check gates the launch ─────────────────────────────


def _skew(tmp_path: pathlib.Path, exit_code: int, **overrides):
    calls: list[list[str]] = []

    def run(argv, stdin):
        calls.append(list(argv))
        return stage.RunResult(exit_code)

    arguments = {"run_id": "run-1", "host_id": "host-a", **_common(tmp_path)}
    arguments.update(overrides)
    try:
        return stage.require_clock_skew(run=run, extra_env={}, **arguments)
    finally:
        _skew.calls = calls  # type: ignore[attr-defined]


def test_a_passing_skew_check_yields_the_proof_for_this_run_directory(
    tmp_path: pathlib.Path,
) -> None:
    proof = _skew(tmp_path, 0)
    assert (proof.run_dir, proof.run_id, proof.host_id) == (RUN_DIR, "run-1", "host-a")
    assert "-m vendor_cp.deployment.database_clock" in _skew.calls[0][-1]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("exit_code", "code"),
    [
        (2, "clock-skew-exceeded"),
        (255, "clock-skew-check-failed"),
        (1, "clock-skew-check-failed"),
    ],
)
def test_a_failing_skew_check_never_yields_a_proof(
    tmp_path: pathlib.Path, exit_code: int, code: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _skew(tmp_path, exit_code)
    assert refusal.value.code == code


@pytest.mark.parametrize("skew", [None, "ok", object()])
def test_a_session_cannot_launch_without_the_skew_proof(
    tmp_path: pathlib.Path, skew: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(tmp_path, skew=skew)
    assert refusal.value.code == "clock-skew-not-checked"


def test_a_skew_proof_cannot_be_constructed_by_a_caller() -> None:
    base = {"run_dir": RUN_DIR, "run_id": "run-1", "host_id": "host-a"}
    for arguments in (base, {**base, "_token": object()}):
        with pytest.raises(stage.StageRefused) as refusal:
            stage.ClockSkewPassed(**arguments)
        assert refusal.value.code == "clock-skew-proof-forged"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_dir", "/run/lane3.OTHERRUN12"),
        ("run_id", "run-2"),
        ("host_id", "host-b"),
    ],
)
def test_a_skew_proof_for_another_run_directory_run_or_host_is_refused(
    tmp_path: pathlib.Path, field: str, value: str
) -> None:
    proof = _proof(tmp_path)  # issued for RUN_DIR / run-1 / host-a
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(other, skew=proof, **{field: value})
    assert refusal.value.code == "clock-skew-not-checked"


@pytest.mark.parametrize("missing", ["run_id", "host_id"])
def test_the_skew_check_requires_its_bindings(
    tmp_path: pathlib.Path, missing: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _skew(tmp_path, 0, **{missing: ""})
    assert refusal.value.code == "clock-skew-binding-required"


# ── the pinned blob must be a canonical Ed25519 key ─────────────────────────


@pytest.mark.parametrize(
    "blob",
    [
        struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 31) + RAW_KEY[:31],
        struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + RAW_KEY + b"x",
        struct.pack(">I", 7) + b"ssh-rsa" + struct.pack(">I", 32) + RAW_KEY,
        b"\x00\x00",
    ],
)
def test_a_pinned_blob_that_is_not_a_canonical_ed25519_key_is_refused(
    tmp_path: pathlib.Path, blob: bytes
) -> None:
    key = base64.b64encode(blob).decode("ascii")
    with pytest.raises(stage.StageRefused) as refusal:
        _from_file(tmp_path, f"host ssh-ed25519 {key}\n")
    assert refusal.value.code == "known-hosts-key-invalid"


# ── the SSH identity must be the lease's controller key ─────────────────────


def test_the_launch_identity_matching_the_leases_controller_key_is_accepted(
    tmp_path: pathlib.Path,
) -> None:
    identity = _identity(tmp_path)
    assert identity.controller_fingerprint == FINGERPRINT
    assert identity.private_key.endswith("id_launch")


def test_an_identity_that_is_not_the_leases_controller_key_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _identity(tmp_path, fingerprint="SHA256:" + "C" * 43)
    assert refusal.value.code == "identity-key-not-the-leases-controller"


@pytest.mark.parametrize(
    "fingerprint", ["", None, "sha256:" + "0" * 64, "SHA256:short"]
)
def test_the_controller_fingerprint_is_a_required_input(
    tmp_path: pathlib.Path, fingerprint: object
) -> None:
    key = tmp_path / "id_launch"
    key.write_text("k")
    (tmp_path / "id_launch.pub").write_text(f"ssh-ed25519 {KEY_B64}\n")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.LaunchIdentity.for_lease(key, _lease(fingerprint))  # type: ignore[arg-type]
    assert refusal.value.code == "identity-controller-fingerprint-required"


def test_a_missing_or_unsafe_identity_is_refused(tmp_path: pathlib.Path) -> None:
    for path in (tmp_path / "absent", "-oProxyCommand=x", "has space", ""):
        with pytest.raises(stage.StageRefused) as refusal:
            stage.LaunchIdentity.for_lease(path, _lease())
        assert refusal.value.code == "identity-key-unusable"


def test_a_missing_public_half_is_refused(tmp_path: pathlib.Path) -> None:
    key = tmp_path / "id_nopub"
    key.write_text("k")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.LaunchIdentity.for_lease(key, _lease())
    assert refusal.value.code == "identity-public-key-unreadable"


def test_ssh_refuses_a_bare_path_for_the_identity(tmp_path: pathlib.Path) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.ssh_argv(
            destination=DESTINATION,
            identity="/run/key",  # type: ignore[arg-type]
            pin=_pin(tmp_path),
            connect_timeout=20,
            remote="true",
        )
    assert refusal.value.code == "ssh-identity-required"


def test_a_public_key_swapped_after_construction_is_caught_at_use(
    tmp_path: pathlib.Path,
) -> None:
    identity = _identity(tmp_path)
    (tmp_path / "id_launch.pub").write_text(
        "ssh-ed25519 " + base64.b64encode(b"another blob").decode() + "\n"
    )
    with pytest.raises(stage.StageRefused) as refusal:
        stage.ssh_argv(
            destination=DESTINATION,
            identity=identity,
            pin=_pin(tmp_path),
            connect_timeout=20,
            remote="true",
        )
    assert refusal.value.code == "identity-key-not-the-leases-controller"


# ── the skew proof cannot be re-bound ───────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_dir", "/run/lane3.OTHERRUN12"), ("run_id", "run-2"), ("host_id", "host-b")],
)
def test_a_genuine_proof_cannot_be_rebound_with_dataclasses_replace(
    tmp_path: pathlib.Path, field: str, value: str
) -> None:
    import dataclasses

    proof = _proof(tmp_path)
    with pytest.raises(stage.StageRefused) as refusal:
        dataclasses.replace(proof, **{field: value})
    assert refusal.value.code == "clock-skew-proof-forged"


def test_a_proof_mutated_in_place_is_caught_at_every_use(
    tmp_path: pathlib.Path,
) -> None:
    proof = _proof(tmp_path)
    object.__setattr__(proof, "run_id", "run-2")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(stage.StageRefused) as refusal:
        # The binding now MATCHES the launch arguments; only the token objects.
        _launch(other, skew=proof, run_id="run-2")
    assert refusal.value.code == "clock-skew-proof-forged"


def test_a_guessed_token_is_refused() -> None:
    for token in ("", "00", "a" * 64):
        with pytest.raises(stage.StageRefused) as refusal:
            stage.ClockSkewPassed(
                run_dir=RUN_DIR, run_id="run-1", host_id="host-a", _token=token
            )
        assert refusal.value.code == "clock-skew-proof-forged"


def test_an_identity_is_built_for_the_lease_it_must_match(
    tmp_path: pathlib.Path,
) -> None:
    import types

    key = tmp_path / "id_launch"
    key.write_text("k")
    (tmp_path / "id_launch.pub").write_text(f"ssh-ed25519 {KEY_B64}\n")
    identity = stage.LaunchIdentity.for_lease(key, _lease())
    assert identity.controller_fingerprint == FINGERPRINT
    assert (identity.host_id, identity.run_id) == ("host-a", "run-1")
    with pytest.raises(stage.StageRefused) as refusal:
        stage.LaunchIdentity.for_lease(key, _lease("SHA256:" + "C" * 43))
    assert refusal.value.code == "identity-key-not-the-leases-controller"
    with pytest.raises(stage.StageRefused) as refusal:
        stage.LaunchIdentity.for_lease(key, types.SimpleNamespace())
    assert refusal.value.code == "identity-controller-fingerprint-required"
    partial = types.SimpleNamespace(controller_identity_fingerprint=FINGERPRINT)
    with pytest.raises(stage.StageRefused) as refusal:
        stage.LaunchIdentity.for_lease(key, partial)
    assert refusal.value.code == "identity-lease-binding-required"


# ── L2b: only for_lease builds an identity, and entry points check its lease ──


def test_there_is_no_other_public_constructor_for_an_identity() -> None:
    assert not hasattr(stage.LaunchIdentity, "from_files")


def test_a_hand_built_identity_is_refused_at_construction(
    tmp_path: pathlib.Path,
) -> None:
    key = _identity(tmp_path).private_key
    for token in ("", "00", "a" * 64):
        with pytest.raises(stage.StageRefused) as refusal:
            stage.LaunchIdentity(
                private_key=key,
                controller_fingerprint=FINGERPRINT,
                host_id="host-a",
                run_id="run-1",
                _token=token,
            )
        assert refusal.value.code == "identity-not-built-for-the-lease"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host_id", "host-b"),
        ("run_id", "run-2"),
        ("controller_fingerprint", "SHA256:" + "C" * 43),
        ("private_key", "/run/other-key"),
    ],
)
def test_a_genuine_identity_cannot_be_rebound_with_dataclasses_replace(
    tmp_path: pathlib.Path, field: str, value: str
) -> None:
    import dataclasses

    identity = _identity(tmp_path)
    with pytest.raises(stage.StageRefused) as refusal:
        dataclasses.replace(identity, **{field: value})
    assert refusal.value.code == "identity-not-built-for-the-lease"


def test_staging_refuses_an_identity_built_for_another_host(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, tmp_path, identity=_identity(tmp_path, host="host-b"))
    assert refusal.value.code == "identity-built-for-another-lease"
    assert not target.commands


def test_staging_refuses_a_bare_identity(tmp_path: pathlib.Path) -> None:
    target = FakeTarget()
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, tmp_path, identity="/run/key")
    assert refusal.value.code == "ssh-identity-required"
    assert not target.commands


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"host": "host-b"}, "identity-built-for-another-lease"),
        ({"run": "run-2"}, "identity-built-for-another-lease"),
    ],
)
def test_launch_refuses_an_identity_built_for_another_host_or_run(
    tmp_path: pathlib.Path, kwargs: dict, code: str
) -> None:
    identity = _identity(tmp_path, **kwargs)
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(tmp_path, identity=identity)  # launch is for host-a / run-1
    assert refusal.value.code == code


def test_launch_refuses_a_bare_identity(tmp_path: pathlib.Path) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(tmp_path, identity="/run/key", skew=_proof(tmp_path))
    assert refusal.value.code == "ssh-identity-required"


def test_an_identity_changed_in_place_is_caught_at_use(tmp_path: pathlib.Path) -> None:
    identity = _identity(tmp_path)
    object.__setattr__(identity, "host_id", "host-b")
    with pytest.raises(stage.StageRefused) as refusal:
        _launch(tmp_path, identity=identity, host_id="host-b", skew=_proof(tmp_path))
    assert refusal.value.code == "identity-not-built-for-the-lease"
