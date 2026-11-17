"""Lane 3, the LAUNCHER half of staging and launch over SSH (E7 / T5).

Everything the target session needs is shipped BY DIGEST into a per-run
directory on the target's tmpfs, installed isolated, and launched over the
controller's one SSH seam. This module builds those commands and checks what it
can check on this side; it never decides anything about the host.

## What is pinned, and what is a required input

* **The host key.** A :class:`PinnedHostKey` is built from a `known_hosts` file
  the launcher is GIVEN, together with the fingerprint it is expected to carry.
  It refuses unless the file holds exactly one plain key line whose derived
  fingerprint equals the expected one, then writes its OWN private copy keyed by
  a `HostKeyAlias`. The same object is what `ssh_argv` uses, and `ssh_argv`
  re-reads the private copy and re-derives the fingerprint on every call, so the
  key that was checked is the key that is used. Which enrolled value is the pin
  (exit-list U3) is not decided here: the fingerprint is a required input with no
  default.
* **The artifacts.** Each file is read ONCE into a :class:`StagedFile`, hashed
  and compared with the digest the launcher pinned; the bytes that were hashed
  are the bytes that are sent. Names come from a closed allowlist, so a name can
  never be a path.
* **The install.** `pip install --no-deps --no-index --require-hashes` from a
  hash-pinned wheelhouse (names and sha256 only) that covers the candidate, the
  CP bindings, the kernel and the Control wheels; the session runs `-E -P` from
  the venv.
* **The lease.** Staged under Foundation's own filename convention
  (`<host_id>.json`), because that is the only name `load_lease` reads.

Secrets (the scoped consumption DSN) are staged as a `0600` file and exported by
a fixed wrapper that reads it, so a secret value is never in an argv.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import os
import pathlib
import re
import shlex
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from dotmac_deployment_foundation.lease import release_path

__all__ = [
    "ARTIFACT_NAMES",
    "ENVIRONMENT_ALLOWLIST",
    "PinnedHostKey",
    "RunResult",
    "StageRefused",
    "StagedFile",
    "clock_skew_argv",
    "launch_argv",
    "load_staged_file",
    "ssh_argv",
    "stage_artifacts",
    "teardown_argv",
]

#: The only names a staged (non-wheel, non-lease) file may have. Closed.
ARTIFACT_NAMES: Final = (
    "scripts.tar",
    "product.toml",
    "authorization.json",
    "execution-context.json",
    "host-admission.json",
    "consumption-dsn",
)
_SECRET_NAMES: Final = frozenset({"consumption-dsn"})

#: The ONLY extra environment names the launch wrapper will set. The CP pins its
#: verification identities and one tuning knob by these names (CP
#: `control_security`, `release_evidence_policy`, `database_clock`); anything
#: else, including every name that could redirect an interpreter, a library or a
#: database, is refused by not being listed.
ENVIRONMENT_ALLOWLIST: Final = frozenset(
    {
        "AUTHORIZATION_VERIFICATION_IDENTITY",
        "DISPATCH_VERIFICATION_IDENTITY",
        "RELEASE_EVIDENCE_VERIFICATION_IDENTITY",
        "RELEASE_EVIDENCE_REPOSITORY",
        "DISPATCH_CLOCK_SKEW_LIMIT_SECONDS",
    }
)

_RUN_DIR: Final = re.compile(r"/run/lane3\.[A-Za-z0-9]{6,32}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_FINGERPRINT: Final = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
_KEY_TYPE: Final = re.compile(r"ssh-[a-z0-9]+|ecdsa-sha2-nistp[0-9]+")
_WHEEL_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.whl")
_LEASE_NAME: Final = re.compile(r"[A-Za-z0-9._-]{1,200}\.json")

#: Options fixed for every SSH invocation, in order. `-F /dev/null` and the
#: options below it mean nothing in the invoking user's configuration, DNS, a
#: known-hosts command or a proxy can alter which host key is trusted or where
#: the connection goes.
_FIXED_OPTIONS: Final = (
    "BatchMode=yes",
    "StrictHostKeyChecking=yes",
    "VerifyHostKeyDNS=no",
    "UpdateHostKeys=no",
    "KnownHostsCommand=none",
    "ProxyCommand=none",
    "GlobalKnownHostsFile=/dev/null",
    "IdentitiesOnly=yes",
    "ClearAllForwardings=yes",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ControlMaster=no",
    "RequestTTY=no",
)
_KEEPALIVE_OPTIONS: Final = ("ServerAliveInterval=15", "ServerAliveCountMax=4")


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


@dataclasses.dataclass(frozen=True, slots=True)
class PinnedHostKey:
    """A host key checked once and used from a private copy, by one object."""

    alias: str
    key_type: str
    key_b64: str
    fingerprint: str
    known_hosts: pathlib.Path

    @property
    def line(self) -> str:
        return f"{self.alias} {self.key_type} {self.key_b64}\n"

    @classmethod
    def from_file(
        cls,
        source: pathlib.Path,
        expected_fingerprint: str,
        *,
        private_dir: pathlib.Path,
    ) -> PinnedHostKey:
        """Check `source` against `expected_fingerprint`; write the private copy.

        Exactly one plain key line: no marker, no wildcard or negated pattern, no
        comma-separated host list, no hashed host. A file that could match another
        host, or hold a second key, is not a pin. The copy is keyed by an alias
        derived from the key itself, never by the address in the source file.
        """
        if not isinstance(expected_fingerprint, str) or not _FINGERPRINT.fullmatch(
            expected_fingerprint
        ):
            raise StageRefused("host-key-fingerprint-required")
        try:
            text = source.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            raise StageRefused("known-hosts-unreadable") from None
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.startswith("#")]
        if len(lines) != 1:
            raise StageRefused("known-hosts-not-exactly-one-key")
        fields = lines[0].split()
        if len(fields) not in (3, 4) or fields[0].startswith(("@", "|")):
            raise StageRefused("known-hosts-line-shape")
        if any(ch in fields[0] for ch in "*?!,"):
            raise StageRefused("known-hosts-host-pattern")
        if not _KEY_TYPE.fullmatch(fields[1]):
            raise StageRefused("known-hosts-key-type")
        actual = _fingerprint_of(fields[2])
        if actual != expected_fingerprint:
            raise StageRefused("host-key-fingerprint-mismatch")
        alias = "lane3-" + hashlib.sha256(fields[2].encode("ascii")).hexdigest()[:16]
        path = _write_private(private_dir, "known_hosts", None)
        pin = cls(
            alias=alias,
            key_type=fields[1],
            key_b64=fields[2],
            fingerprint=actual,
            known_hosts=path,
        )
        with path.open("w", encoding="ascii") as stream:
            stream.write(pin.line)
        return pin

    def reverify(self) -> None:
        """Refuse unless the private copy is still exactly the checked key."""
        try:
            metadata = self.known_hosts.lstat()
            text = self.known_hosts.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            raise StageRefused("known-hosts-copy-unreadable") from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or text != self.line
        ):
            raise StageRefused("known-hosts-copy-changed")


def _write_private(
    directory: pathlib.Path, name: str, data: bytes | None
) -> pathlib.Path:
    """Create `directory/name` create-only, `0600`, in an owner-only directory."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise StageRefused("private-directory-unsafe")
    path = directory / name
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise StageRefused("private-file-unavailable") from None
    with os.fdopen(descriptor, "wb") as stream:
        if data is not None:
            stream.write(data)
    return path


# ── ssh ─────────────────────────────────────────────────────────────────────


def ssh_argv(
    *,
    destination: str,
    identity: str,
    pin: PinnedHostKey,
    connect_timeout: int,
    remote: str,
) -> list[str]:
    """The one SSH invocation shape. Every option is fixed; the inputs are four.

    The pin is re-verified HERE, on every call, so the key that was checked is
    the key SSH will be handed.
    """
    for value in (destination, identity):
        if not value or value.startswith("-") or any(c.isspace() for c in value):
            raise StageRefused("ssh-input-unusable")
    if type(connect_timeout) is not int or not 1 <= connect_timeout <= 120:
        raise StageRefused("ssh-connect-timeout")
    pin.reverify()
    argv = ["ssh", "-T", "-F", "/dev/null"]
    for option in _FIXED_OPTIONS:
        argv += ["-o", option]
    argv += ["-o", f"ConnectTimeout={connect_timeout}"]
    for option in _KEEPALIVE_OPTIONS:
        argv += ["-o", option]
    argv += ["-o", f"HostKeyAlias={pin.alias}"]
    argv += ["-o", f"UserKnownHostsFile={pin.known_hosts}", "-i", identity]
    argv += ["--", destination, remote]
    return argv


# ── staging ─────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class StagedFile:
    """Bytes read ONCE, hashed, and checked. What was hashed is what is sent."""

    data: bytes
    sha256: str


def load_staged_file(path: pathlib.Path, expected_sha256: str) -> StagedFile:
    """Read `path` once; refuse unless its bytes hash to `expected_sha256`."""
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise StageRefused("artifact-digest-required")
    try:
        data = path.read_bytes()
    except OSError:
        raise StageRefused("artifact-unreadable") from None
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise StageRefused("artifact-digest-mismatch")
    return StagedFile(data=data, sha256=expected_sha256)


def _require_staged(name: str, staged: StagedFile) -> None:
    if (
        not isinstance(staged, StagedFile)
        or not _SHA256.fullmatch(staged.sha256)
        or hashlib.sha256(staged.data).hexdigest() != staged.sha256
    ):
        raise StageRefused(f"artifact-digest-mismatch:{name}")


def expected_lease_filename(host_id: str) -> str:
    """The name Foundation's `load_lease` reads for this host, from its own code."""
    return (
        release_path(host_id, directory=".").name.removesuffix(".release.json")
        + ".json"
    )


def requirements_for(wheelhouse: Mapping[str, StagedFile]) -> StagedFile:
    """The hash-pinned requirements file: names and sha256, nothing else."""
    lines = [
        f"./wheelhouse/{name} --hash=sha256:{wheel.sha256}\n"
        for name, wheel in sorted(wheelhouse.items())
    ]
    data = "".join(lines).encode("ascii")
    return StagedFile(data=data, sha256=hashlib.sha256(data).hexdigest())


def _ok(result: RunResult, code: str) -> RunResult:
    if result.exit_code != 0:
        raise StageRefused(code)
    return result


def stage_artifacts(
    *,
    run: Runner,
    destination: str,
    identity: str,
    pin: PinnedHostKey,
    connect_timeout: int,
    host_id: str,
    artifacts: Mapping[str, StagedFile],
    lease: StagedFile,
    lease_filename: str,
    wheelhouse: Mapping[str, StagedFile],
) -> str:
    """Create the per-run directory and ship every artifact by digest.

    Each file lands, is hashed again ON THE TARGET, and must equal the digest the
    launcher computed from the very bytes it sent. Returns the run directory.
    """
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise StageRefused("artifact-set-not-the-closed-set")
    if lease_filename != expected_lease_filename(host_id) or not _LEASE_NAME.fullmatch(
        lease_filename
    ):
        raise StageRefused("lease-filename-not-foundation-convention")
    if not wheelhouse or any(not _WHEEL_NAME.fullmatch(n) for n in wheelhouse):
        raise StageRefused("wheelhouse-required")
    for name, staged in artifacts.items():
        _require_staged(name, staged)
    _require_staged("lease", lease)
    for name, staged in wheelhouse.items():
        _require_staged(name, staged)
    requirements = requirements_for(wheelhouse)

    def remote(command: str, stdin: bytes | None = None) -> RunResult:
        return run(
            ssh_argv(
                destination=destination,
                identity=identity,
                pin=pin,
                connect_timeout=connect_timeout,
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
    _ok(
        remote(f"cd {quoted_dir} && mkdir lease wheelhouse scripts"),
        "run-directory-layout",
    )

    plan: list[tuple[str, StagedFile, str]] = [
        (name, staged, "0600" if name in _SECRET_NAMES else "0400")
        for name, staged in sorted(artifacts.items())
    ]
    plan.append((f"lease/{lease_filename}", lease, "0400"))
    plan.append(("requirements.txt", requirements, "0400"))
    plan += [(f"wheelhouse/{n}", w, "0400") for n, w in sorted(wheelhouse.items())]
    for relative, staged, mode in plan:
        target = shlex.quote(f"{run_dir}/{relative}")
        _ok(
            remote(
                f"umask 077 && cat > {target} && chmod {mode} {target}", staged.data
            ),
            f"stage-failed:{relative}",
        )
        landed = _ok(remote(f"sha256sum -- {target}"), f"stage-unverifiable:{relative}")
        if landed.stdout.decode("ascii", "replace").split()[:1] != [staged.sha256]:
            raise StageRefused(f"stage-digest-mismatch:{relative}")
    _ok(
        remote(
            f"cd {quoted_dir} && "
            "tar -xf scripts.tar -C scripts --no-same-owner --no-same-permissions "
            "&& chmod 0500 lease"
        ),
        "scripts-extract",
    )
    # Isolated and hash-pinned: only the staged wheels, never an index, and every
    # one of them named with its sha256 in requirements.txt.
    _ok(
        remote(
            f"cd {quoted_dir} && python3 -m venv venv && "
            "venv/bin/python -m pip install --no-deps --no-index "
            "--no-cache-dir --require-hashes -r requirements.txt"
        ),
        "isolated-install",
    )
    return run_dir


# ── clock and launch ────────────────────────────────────────────────────────


def _environment(extra: Mapping[str, str]) -> str:
    parts = []
    for name, value in sorted(extra.items()):
        if name not in ENVIRONMENT_ALLOWLIST:
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
    pin: PinnedHostKey,
    connect_timeout: int,
    extra_env: Mapping[str, str],
) -> list[str]:
    """CP's skew command, run ON the target. Exit 0 passes; 2 is a refusal."""
    tail = "venv/bin/python -E -P -m vendor_cp.deployment.database_clock"
    return ssh_argv(
        destination=destination,
        identity=identity,
        pin=pin,
        connect_timeout=connect_timeout,
        remote=_wrapper(run_dir, extra_env, tail=tail),
    )


def launch_argv(
    *,
    run_dir: str,
    destination: str,
    identity: str,
    pin: PinnedHostKey,
    connect_timeout: int,
    extra_env: Mapping[str, str],
) -> list[str]:
    """The session, in the candidate venv, `-E -P`, with the channel on stdio."""
    tail = "venv/bin/python -E -P scripts/lane3_target_session.py"
    return ssh_argv(
        destination=destination,
        identity=identity,
        pin=pin,
        connect_timeout=connect_timeout,
        remote=_wrapper(run_dir, extra_env, tail=tail),
    )


def teardown_argv(
    *,
    run_dir: str,
    destination: str,
    identity: str,
    pin: PinnedHostKey,
    connect_timeout: int,
) -> list[str]:
    """Remove the per-run directory. Best effort; the run's verdict is fixed."""
    if not _RUN_DIR.fullmatch(run_dir):
        raise StageRefused("run-directory-unexpected")
    return ssh_argv(
        destination=destination,
        identity=identity,
        pin=pin,
        connect_timeout=connect_timeout,
        remote=f"rm -rf -- {shlex.quote(run_dir)}",
    )
