"""E7 / T5: pinned host key, closed artifact set, digests on both ends, isolated
install, a fixed launch wrapper and the CP clock-skew command."""

from __future__ import annotations

import base64
import hashlib
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


def _known_hosts(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "known_hosts"
    path.write_text(text, encoding="ascii")
    return path


def _line(host: str = "target.example.invalid") -> str:
    return f"{host} ssh-ed25519 {KEY_B64}\n"


# ── the pinned host key ─────────────────────────────────────────────────────


def test_a_matching_single_key_is_the_pin(tmp_path: pathlib.Path) -> None:
    path = _known_hosts(tmp_path, "# comment\n" + _line())
    assert stage.require_pinned_host_key(path, FINGERPRINT) == FINGERPRINT


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
    ],
)
def test_a_file_that_is_not_a_pin_is_refused(
    tmp_path: pathlib.Path, text: str, code: str
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.require_pinned_host_key(_known_hosts(tmp_path, text), FINGERPRINT)
    assert refusal.value.code == code


def test_another_keys_fingerprint_is_refused(tmp_path: pathlib.Path) -> None:
    other = "SHA256:" + "A" * 43
    with pytest.raises(stage.StageRefused) as refusal:
        stage.require_pinned_host_key(_known_hosts(tmp_path, _line()), other)
    assert refusal.value.code == "host-key-fingerprint-mismatch"


@pytest.mark.parametrize("expected", ["", None, "SHA256:short", "MD5:aa", 5])
def test_the_expected_fingerprint_is_required(
    tmp_path: pathlib.Path, expected: object
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.require_pinned_host_key(
            _known_hosts(tmp_path, _line()),
            expected,  # type: ignore[arg-type]
        )
    assert refusal.value.code == "host-key-fingerprint-required"


# ── ssh ─────────────────────────────────────────────────────────────────────


def test_ssh_uses_only_the_pinned_file_and_strict_checking() -> None:
    argv = stage.ssh_argv(
        destination="root@target.example.invalid",
        identity="/run/key",
        known_hosts="/run/known_hosts",
        remote="true",
    )
    joined = " ".join(argv)
    for option in (
        "StrictHostKeyChecking=yes",
        "GlobalKnownHostsFile=/dev/null",
        "UserKnownHostsFile=/run/known_hosts",
        "IdentitiesOnly=yes",
        "ClearAllForwardings=yes",
        "BatchMode=yes",
    ):
        assert option in joined
    assert argv[-3:] == ["--", "root@target.example.invalid", "true"]
    assert "StrictHostKeyChecking=no" not in joined


@pytest.mark.parametrize("bad", ["", "-oProxyCommand=x", "has space"])
def test_ssh_inputs_that_could_inject_options_are_refused(bad: str) -> None:
    with pytest.raises(stage.StageRefused):
        stage.ssh_argv(destination=bad, identity="/k", known_hosts="/h", remote="true")


# ── staging ─────────────────────────────────────────────────────────────────


class FakeTarget:
    """A target that records what was staged and answers sha256sum honestly."""

    def __init__(self, *, corrupt: str = "", run_dir: str = RUN_DIR) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self.corrupt = corrupt
        self.run_dir = run_dir

    def __call__(self, argv, stdin):
        command = argv[-1]
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
            return stage.RunResult(
                0, f"{hashlib.sha256(data).hexdigest()}  {path}\n".encode()
            )
        return stage.RunResult(0)


def _artifacts(tmp_path: pathlib.Path) -> dict[str, tuple[pathlib.Path, str]]:
    result = {}
    for name in stage.ARTIFACT_NAMES:
        path = tmp_path / name
        path.write_bytes(f"content of {name}".encode())
        result[name] = (path, hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def _stage(target: FakeTarget, artifacts) -> str:
    return stage.stage_artifacts(
        run=target,
        destination="root@target.example.invalid",
        identity="/run/key",
        known_hosts="/run/known_hosts",
        artifacts=artifacts,
    )


def test_every_artifact_is_shipped_and_verified_by_digest(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    assert _stage(target, _artifacts(tmp_path)) == RUN_DIR
    assert set(target.files) == {f"{RUN_DIR}/{n}" for n in stage.ARTIFACT_NAMES}
    secret = [c for c in target.commands if "consumption-dsn" in c and "chmod" in c]
    assert secret and "chmod 0600" in secret[0]
    others = [c for c in target.commands if "chmod 0400" in c]
    assert len(others) == len(stage.ARTIFACT_NAMES) - 1


def test_the_install_is_isolated_and_from_staged_files_only(
    tmp_path: pathlib.Path,
) -> None:
    target = FakeTarget()
    _stage(target, _artifacts(tmp_path))
    install = next(c for c in target.commands if "pip install" in c)
    assert "--no-deps" in install
    assert "--no-index" in install
    assert "candidate.whl bindings.whl" in install


def test_a_local_digest_mismatch_sends_nothing(tmp_path: pathlib.Path) -> None:
    artifacts = _artifacts(tmp_path)
    path, _ = artifacts["candidate.whl"]
    artifacts["candidate.whl"] = (path, "0" * 64)
    target = FakeTarget()
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, artifacts)
    assert refusal.value.code == "artifact-digest-mismatch:candidate.whl"
    assert not target.commands


def test_a_target_side_digest_mismatch_is_refused(tmp_path: pathlib.Path) -> None:
    target = FakeTarget(corrupt="bindings.whl")
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(target, _artifacts(tmp_path))
    assert refusal.value.code == "stage-digest-mismatch:bindings.whl"


def test_a_name_outside_the_closed_set_is_refused(tmp_path: pathlib.Path) -> None:
    artifacts = _artifacts(tmp_path)
    artifacts["../evil"] = artifacts["scripts.tar"]
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(), artifacts)
    assert refusal.value.code == "artifact-set-not-the-closed-set"


def test_a_missing_artifact_is_refused(tmp_path: pathlib.Path) -> None:
    artifacts = _artifacts(tmp_path)
    del artifacts["consumption-dsn"]
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(), artifacts)
    assert refusal.value.code == "artifact-set-not-the-closed-set"


def test_an_unexpected_run_directory_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        _stage(FakeTarget(run_dir="/tmp/elsewhere"), _artifacts(tmp_path))  # noqa: S108
    assert refusal.value.code == "run-directory-unexpected"


# ── launch and clock ────────────────────────────────────────────────────────

COMMON = {
    "run_dir": RUN_DIR,
    "destination": "root@target.example.invalid",
    "identity": "/run/key",
    "known_hosts": "/run/known_hosts",
}


def test_the_session_launches_isolated_with_materials_by_path() -> None:
    remote = stage.launch_argv(
        **COMMON, extra_env={"AUTHORIZATION_IDENTITY": "/run/lane3.x/id.json"}
    )[-1]
    assert "venv/bin/python -E -P scripts/lane3_target_session.py" in remote
    assert f"HOST_ADMISSION_MATERIALS={RUN_DIR}/host-admission.json" in remote
    assert f"EXECUTION_CONTEXT_MATERIALS={RUN_DIR}/execution-context.json" in remote
    assert "AUTHORIZATION_IDENTITY=/run/lane3.x/id.json" in remote
    # The secret is read from its file by the wrapper, never placed in the argv.
    assert '"$(cat consumption-dsn)"' in remote


def test_cps_skew_command_runs_on_the_target_before_the_session() -> None:
    remote = stage.clock_skew_argv(**COMMON, extra_env={})[-1]
    assert "-m vendor_cp.deployment.database_clock" in remote


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "LD_PRELOAD",
        "PYTHONPATH",
        "DATABASE_URL",
        "DISPATCH_CONSUMPTION_DATABASE_URL",
        "SSH_AUTH_SOCK",
        "lowercase",
        "A",
    ],
)
def test_environment_names_that_could_redirect_the_session_are_refused(
    name: str,
) -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.launch_argv(**COMMON, extra_env={name: "value"})
    assert refusal.value.code == "environment-name-refused"


def test_an_environment_value_with_a_control_character_is_refused() -> None:
    with pytest.raises(stage.StageRefused) as refusal:
        stage.launch_argv(**COMMON, extra_env={"SOME_NAME": "a\nb"})
    assert refusal.value.code == "environment-value-refused"


def test_a_run_directory_outside_run_lane3_is_refused_everywhere() -> None:
    bad = dict(COMMON, run_dir="/run/other")
    with pytest.raises(stage.StageRefused):
        stage.launch_argv(**bad, extra_env={})
    with pytest.raises(stage.StageRefused):
        stage.clock_skew_argv(**bad, extra_env={})
    with pytest.raises(stage.StageRefused):
        stage.teardown_argv(
            run_dir="/run/other",
            destination="d",
            identity="/k",
            known_hosts="/h",
        )
