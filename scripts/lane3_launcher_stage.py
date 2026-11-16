"""Lane 3, the LAUNCHER half of staging and launch over SSH (E7 / T5).

Everything the target session needs is shipped BY DIGEST into a per-run
directory on the target's tmpfs, installed isolated, and launched over the
controller's one SSH seam. This module builds those commands and checks what it
can check on this side; it never decides anything about the host.

## What is pinned, and what is a required input

* **The host key.** `known_hosts` is a file the launcher is GIVEN, together with
  the fingerprint it is expected to carry. :func:`require_pinned_host_key`
  refuses unless the file holds exactly one plain key line whose derived
  fingerprint equals the expected one. OpenSSH is then run with only that file,
  `StrictHostKeyChecking=yes` and the global file disabled. Which enrolled value
  is the pin (exit-list U3) is not decided here: both inputs are required, with
  no default.
* **The artifacts.** Each is a named item with a SHA-256 the launcher verified
  locally before sending and the target verified after receiving. Names come from
  a closed allowlist, so a name can never be a path.
* **The install.** `pip install --no-deps --no-index`, from the staged files
  only, into a venv under the run directory; the session runs `-E -P` from it.
* **The clock.** `python -m vendor_cp.deployment.database_clock` (CP) runs on the
  target before the session; a non-zero exit refuses the run. CP's own
  `DatabaseClock` is the freshness clock inside the session.

Secrets (the scoped consumption DSN) are staged as a `0600` file and exported by
a fixed wrapper that reads it, so a secret value is never in an argv.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import pathlib
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import Final

__all__ = [
    "ARTIFACT_NAMES",
    "SSH_OPTIONS",
    "RunResult",
    "StageRefused",
    "clock_skew_argv",
    "launch_argv",
    "require_pinned_host_key",
    "ssh_argv",
    "stage_artifacts",
    "teardown_argv",
]

#: The only names a staged file may have. A closed set: never a caller's path.
ARTIFACT_NAMES: Final = (
    "scripts.tar",
    "candidate.whl",
    "bindings.whl",
    "product.toml",
    "authorization.json",
    "execution-context.json",
    "host-admission.json",
    "lease.json",
    "consumption-dsn",
)
#: Mode each staged file gets on the target.
_SECRET_NAMES: Final = frozenset({"consumption-dsn"})

_RUN_DIR: Final = re.compile(r"/run/lane3\.[A-Za-z0-9]{6,32}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_ENV_NAME: Final = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_FORBIDDEN_ENV: Final = re.compile(
    r"(PATH|HOME|IFS|LD_.*|PYTHON.*|PG.*|DATABASE_URL|PLATFORM_DATABASE_URL"
    r"|DISPATCH_CONSUMPTION_DATABASE_URL|BASH_ENV|ENV|SSH_.*)"
)
_FINGERPRINT: Final = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

SSH_OPTIONS: Final = (
    "BatchMode=yes",
    "StrictHostKeyChecking=yes",
    "GlobalKnownHostsFile=/dev/null",
    "IdentitiesOnly=yes",
    "ClearAllForwardings=yes",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ControlMaster=no",
    "RequestTTY=no",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=4",
)


class StageRefused(Exception):
    """Staging or launch is refused. A CODE, never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RunResult:
    """What one remote command answered."""

    __slots__ = ("exit_code", "stdout")

    def __init__(self, exit_code: int, stdout: bytes = b"") -> None:
        self.exit_code = exit_code
        self.stdout = stdout


Runner = Callable[[Sequence[str], bytes | None], RunResult]


# ── the pinned host key ─────────────────────────────────────────────────────


def _fingerprint_of(blob_b64: str) -> str:
    try:
        blob = base64.b64decode(blob_b64.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise StageRefused("known-hosts-key-invalid") from None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return "SHA256:" + digest.rstrip("=")


def require_pinned_host_key(known_hosts: pathlib.Path, expected: str) -> str:
    """The fingerprint the file carries, refused unless it is `expected`.

    Exactly one plain key line: no marker, no wildcard or negated pattern, no
    comma-separated host list, no hashed host. A file that could match another
    host, or hold a second key, is not a pin.
    """
    if not isinstance(expected, str) or not _FINGERPRINT.fullmatch(expected):
        raise StageRefused("host-key-fingerprint-required")
    try:
        text = known_hosts.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        raise StageRefused("known-hosts-unreadable") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith("#")]
    if len(lines) != 1:
        raise StageRefused("known-hosts-not-exactly-one-key")
    fields = lines[0].split()
    if len(fields) not in (3, 4) or fields[0].startswith(("@", "|")):
        raise StageRefused("known-hosts-line-shape")
    host = fields[0]
    if any(ch in host for ch in "*?!,"):
        raise StageRefused("known-hosts-host-pattern")
    actual = _fingerprint_of(fields[2])
    if actual != expected:
        raise StageRefused("host-key-fingerprint-mismatch")
    return actual


# ── ssh ─────────────────────────────────────────────────────────────────────


def ssh_argv(
    *, destination: str, identity: str, known_hosts: str, remote: str
) -> list[str]:
    """The one SSH invocation shape. Every option is fixed; the inputs are three."""
    for value in (destination, identity, known_hosts):
        if not value or value.startswith("-") or any(c.isspace() for c in value):
            raise StageRefused("ssh-input-unusable")
    argv = ["ssh", "-T"]
    for option in SSH_OPTIONS:
        argv += ["-o", option]
    argv += ["-o", f"UserKnownHostsFile={known_hosts}", "-i", identity]
    argv += ["--", destination, remote]
    return argv


# ── staging ─────────────────────────────────────────────────────────────────


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        raise StageRefused("artifact-unreadable") from None
    return digest.hexdigest()


def _ok(result: RunResult, code: str) -> RunResult:
    if result.exit_code != 0:
        raise StageRefused(code)
    return result


def stage_artifacts(
    *,
    run: Runner,
    destination: str,
    identity: str,
    known_hosts: str,
    artifacts: Mapping[str, tuple[pathlib.Path, str]],
) -> str:
    """Create the per-run directory and ship every artifact by digest.

    `artifacts` maps an allowlisted name to `(local path, expected sha256)`. Each
    file is hashed locally BEFORE it is sent and hashed again ON THE TARGET after
    it lands; either disagreement refuses. Returns the run directory.
    """
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise StageRefused("artifact-set-not-the-closed-set")
    for name, (path, expected) in artifacts.items():
        if not _SHA256.fullmatch(expected):
            raise StageRefused("artifact-digest-required")
        if _sha256_file(path) != expected:
            raise StageRefused(f"artifact-digest-mismatch:{name}")

    def remote(command: str, stdin: bytes | None = None) -> RunResult:
        return run(
            ssh_argv(
                destination=destination,
                identity=identity,
                known_hosts=known_hosts,
                remote=command,
            ),
            stdin,
        )

    created = _ok(
        remote("umask 077 && mktemp -d /run/lane3.XXXXXXXXXX"), "run-directory"
    )
    run_dir = created.stdout.decode("ascii", "replace").strip()
    if not _RUN_DIR.fullmatch(run_dir):
        raise StageRefused("run-directory-unexpected")
    quoted_dir = shlex.quote(run_dir)
    for name in sorted(artifacts):
        path, expected = artifacts[name]
        target = shlex.quote(f"{run_dir}/{name}")
        mode = "0600" if name in _SECRET_NAMES else "0400"
        _ok(
            remote(
                f"umask 077 && cat > {target} && chmod {mode} {target}",
                path.read_bytes(),
            ),
            f"stage-failed:{name}",
        )
        landed = _ok(remote(f"sha256sum -- {target}"), f"stage-unverifiable:{name}")
        remote_digest = landed.stdout.decode("ascii", "replace").split()[:1]
        if remote_digest != [expected]:
            raise StageRefused(f"stage-digest-mismatch:{name}")
    _ok(
        remote(
            f"cd {quoted_dir} && mkdir scripts && "
            "tar -xf scripts.tar -C scripts --no-same-owner --no-same-permissions"
        ),
        "scripts-extract",
    )
    # Isolated: the venv holds ONLY the two staged wheels, never an index.
    _ok(
        remote(
            f"cd {quoted_dir} && python3 -m venv venv && "
            "venv/bin/python -m pip install --no-deps --no-index "
            "--no-cache-dir candidate.whl bindings.whl"
        ),
        "isolated-install",
    )
    return run_dir


# ── clock and launch ────────────────────────────────────────────────────────


def _environment(extra: Mapping[str, str]) -> str:
    parts = []
    for name, value in sorted(extra.items()):
        if not _ENV_NAME.fullmatch(name) or _FORBIDDEN_ENV.fullmatch(name):
            raise StageRefused("environment-name-refused")
        if not value or any(ord(c) < 0x20 for c in value):
            raise StageRefused("environment-value-refused")
        parts.append(f"{name}={shlex.quote(value)}")
    return " ".join(parts)


def _wrapper(run_dir: str, extra_env: Mapping[str, str], *, tail: str) -> str:
    """The fixed remote command: materials by path, the DSN from a 0600 file."""
    if not _RUN_DIR.fullmatch(run_dir):
        raise StageRefused("run-directory-unexpected")
    base = shlex.quote(run_dir)
    environment = _environment(extra_env)
    admission = shlex.quote(f"{run_dir}/host-admission.json")
    context = shlex.quote(f"{run_dir}/execution-context.json")
    return (
        f"cd {base} && umask 077 && "
        f'DISPATCH_CONSUMPTION_DATABASE_URL="$(cat consumption-dsn)" '
        "&& export DISPATCH_CONSUMPTION_DATABASE_URL && "
        f"exec env HOST_ADMISSION_MATERIALS={admission} "
        f"EXECUTION_CONTEXT_MATERIALS={context} "
        f"{environment} {tail}"
    )


def clock_skew_argv(
    *,
    run_dir: str,
    destination: str,
    identity: str,
    known_hosts: str,
    extra_env: Mapping[str, str],
) -> list[str]:
    """CP's skew command, run ON the target. Exit 0 passes; 2 is a refusal."""
    tail = "venv/bin/python -E -P -m vendor_cp.deployment.database_clock"
    return ssh_argv(
        destination=destination,
        identity=identity,
        known_hosts=known_hosts,
        remote=_wrapper(run_dir, extra_env, tail=tail),
    )


def launch_argv(
    *,
    run_dir: str,
    destination: str,
    identity: str,
    known_hosts: str,
    extra_env: Mapping[str, str],
) -> list[str]:
    """The session, in the candidate venv, `-E -P`, with the channel on stdio."""
    tail = "venv/bin/python -E -P scripts/lane3_target_session.py"
    return ssh_argv(
        destination=destination,
        identity=identity,
        known_hosts=known_hosts,
        remote=_wrapper(run_dir, extra_env, tail=tail),
    )


def teardown_argv(
    *, run_dir: str, destination: str, identity: str, known_hosts: str
) -> list[str]:
    """Remove the per-run directory. Best effort; the run's verdict is fixed."""
    if not _RUN_DIR.fullmatch(run_dir):
        raise StageRefused("run-directory-unexpected")
    return ssh_argv(
        destination=destination,
        identity=identity,
        known_hosts=known_hosts,
        remote=f"rm -rf -- {shlex.quote(run_dir)}",
    )
