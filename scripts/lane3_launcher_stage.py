"""Lane 3, the LAUNCHER half of staging and launch over SSH (E7 / T5).

Everything the target session needs is shipped BY DIGEST into a per-run
directory on the target's tmpfs, installed isolated, and launched over the
controller's one SSH seam. This module builds those commands and checks what it
can check on this side; it never decides anything about the host.

## What is pinned, and what is a required input

* **The host key (U3, amended: option B).** The target's SSH Ed25519 host key is
  pinned on its own. A :class:`PinnedHostKey` is built from a `known_hosts` file
  the launcher is GIVEN, with required typed inputs and no defaults: the
  fingerprint pin and the VM alias. It refuses unless the file holds exactly one
  plain Ed25519 key line (any other key type is refused) whose OpenSSH
  fingerprint equals the pin, then writes its OWN private copy keyed by the alias,
  which is the `HostKeyAlias`. All of that happens before any SSH connection is
  made. `ssh_argv` re-reads the private copy on every call, so the key that was
  checked is the key that is used. `host_incarnation` is NOT this key: it is a
  separate incarnation key linked to it only by the per-VM E10 receipt
  (`lane3_e10_receipt`).
* **The launch identity.** The SSH key the launcher authenticates with must be the
  held lease's controller key. A :class:`LaunchIdentity` is built from the key's
  public half and refuses unless its fingerprint equals the lease's controller
  fingerprint; `ssh_argv` takes that object and re-verifies it on every call.
* **The clock.** Freshness is judged on the lease store's database time (U5).
  The session still must not start on a host whose own clock has drifted, so CP's
  skew command runs on the target and :func:`require_clock_skew` turns its exit
  status into a :class:`ClockSkewPassed` proof that :func:`launch_argv` demands.
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
import hmac
import os
import pathlib
import re
import shlex
import stat
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import lane3_e10_receipt
from dotmac_deployment_foundation.lease import release_path

__all__ = [
    "ARTIFACT_NAMES",
    "ENVIRONMENT_ALLOWLIST",
    "ClockSkewPassed",
    "LaunchIdentity",
    "PinnedHostKey",
    "RunResult",
    "StageRefused",
    "StagedFile",
    "StagedRun",
    "clock_skew_argv",
    "launch_argv",
    "load_staged_file",
    "require_clock_skew",
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
_ED25519: Final = "ssh-ed25519"
_ALIAS: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
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


def _ed25519_raw_key(blob_b64: str) -> bytes:
    """The raw 32-byte Ed25519 public key inside an OpenSSH wire-format blob."""
    try:
        blob = base64.b64decode(blob_b64.encode("ascii"), validate=True)
        (name_length,) = struct.unpack(">I", blob[:4])
        name = blob[4 : 4 + name_length]
        offset = 4 + name_length
        (key_length,) = struct.unpack(">I", blob[offset : offset + 4])
        raw = blob[offset + 4 : offset + 4 + key_length]
    except (binascii.Error, UnicodeEncodeError, struct.error):
        raise StageRefused("known-hosts-key-invalid") from None
    if (
        name != b"ssh-ed25519"
        or key_length != 32
        or len(raw) != 32
        or offset + 4 + key_length != len(blob)
    ):
        raise StageRefused("known-hosts-key-invalid")
    return raw


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
        alias: str,
        private_dir: pathlib.Path,
    ) -> PinnedHostKey:
        """Check `source` against the pin; write the private copy. No SSH.

        Exactly one plain Ed25519 key line: no marker, no wildcard or negated
        pattern, no comma-separated host list, no hashed host, no other key type.
        The copy is keyed by `alias` (the VM alias from the provisioning receipt),
        never by the address in the source file.
        """
        if not isinstance(expected_fingerprint, str) or not _FINGERPRINT.fullmatch(
            expected_fingerprint
        ):
            raise StageRefused("host-key-fingerprint-required")
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            raise StageRefused("host-key-alias-required")
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
        if fields[1] != _ED25519:
            raise StageRefused("host-key-not-ed25519")
        _ed25519_raw_key(fields[2])
        actual = _fingerprint_of(fields[2])
        if actual != expected_fingerprint:
            raise StageRefused("host-key-fingerprint-mismatch")
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


# ── the launch identity ─────────────────────────────────────────────────────


def _public_key_fingerprint(public_key: pathlib.Path) -> str:
    try:
        text = public_key.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        raise StageRefused("identity-public-key-unreadable") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise StageRefused("identity-public-key-not-exactly-one-key")
    fields = lines[0].split()
    if len(fields) < 2:
        raise StageRefused("identity-public-key-shape")
    return _fingerprint_of(fields[1])


_IDENTITY_KEY: Final = os.urandom(32)


def _identity_token(
    private_key: str, controller_fingerprint: str, host_id: str, run_id: str
) -> str:
    return hmac.new(
        _IDENTITY_KEY,
        "\x00".join((private_key, controller_fingerprint, host_id, run_id)).encode(),
        "sha256",
    ).hexdigest()


@dataclasses.dataclass(frozen=True, slots=True)
class LaunchIdentity:
    """The SSH key the launcher authenticates with, proven to be THE lease's.

    Built ONLY by :meth:`for_lease`, for one held lease: it reads the key's public
    half (`<private>.pub`), refuses unless its OpenSSH fingerprint equals the
    lease's controller fingerprint, and carries a private token over the key, that
    fingerprint, the lease's host and the lease's authorization run. A hand-built
    instance, or `dataclasses.replace` onto another host or run, fails the token at
    construction and at every use. `stage_artifacts` and `launch_argv` check the
    host (and, for launch, the run) the identity was built for.
    """

    private_key: str
    controller_fingerprint: str
    host_id: str
    run_id: str
    _token: str = dataclasses.field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        self.verify()

    def verify(self) -> None:
        expected = _identity_token(
            self.private_key, self.controller_fingerprint, self.host_id, self.run_id
        )
        if not hmac.compare_digest(str(self._token), expected):
            raise StageRefused("identity-not-built-for-the-lease")

    @classmethod
    def for_lease(
        cls, private_key: pathlib.Path | str, lease: object
    ) -> LaunchIdentity:
        """The identity for THIS held lease: its controller key, nothing else."""
        path = str(private_key)
        if (
            not path
            or path.startswith("-")
            or any(c.isspace() for c in path)
            or not pathlib.Path(path).is_file()
        ):
            raise StageRefused("identity-key-unusable")
        fingerprint = str(getattr(lease, "controller_identity_fingerprint", ""))
        host_id = str(getattr(lease, "target", ""))
        run_id = str(getattr(lease, "authorization_run_id", ""))
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise StageRefused("identity-controller-fingerprint-required")
        if not host_id or not run_id:
            raise StageRefused("identity-lease-binding-required")
        if _public_key_fingerprint(pathlib.Path(path + ".pub")) != fingerprint:
            raise StageRefused("identity-key-not-the-leases-controller")
        return cls(
            private_key=path,
            controller_fingerprint=fingerprint,
            host_id=host_id,
            run_id=run_id,
            _token=_identity_token(path, fingerprint, host_id, run_id),
        )

    def reverify(self) -> None:
        self.verify()
        if (
            _public_key_fingerprint(pathlib.Path(self.private_key + ".pub"))
            != self.controller_fingerprint
        ):
            raise StageRefused("identity-key-not-the-leases-controller")

    def require_for(self, *, host_id: str, run_id: str | None = None) -> None:
        """Refuse unless built for this host (and, when given, this run)."""
        self.verify()
        if self.host_id != host_id or (run_id is not None and self.run_id != run_id):
            raise StageRefused("identity-built-for-another-lease")


# ── ssh ─────────────────────────────────────────────────────────────────────


def ssh_argv(
    *,
    destination: str,
    identity: LaunchIdentity,
    pin: PinnedHostKey,
    connect_timeout: int,
    remote: str,
) -> list[str]:
    """The one SSH invocation shape. Every option is fixed; the inputs are four.

    The pin and the launch identity are re-verified HERE, on every call, so the
    host key that was checked is the key SSH will trust and the key that
    authenticates is the lease's controller key.
    """
    if not isinstance(identity, LaunchIdentity):
        raise StageRefused("ssh-identity-required")
    if (
        not destination
        or destination.startswith("-")
        or any(c.isspace() for c in destination)
    ):
        raise StageRefused("ssh-input-unusable")
    if type(connect_timeout) is not int or not 1 <= connect_timeout <= 120:
        raise StageRefused("ssh-connect-timeout")
    pin.reverify()
    identity.reverify()
    argv = ["ssh", "-T", "-F", "/dev/null"]
    for option in _FIXED_OPTIONS:
        argv += ["-o", option]
    argv += ["-o", f"ConnectTimeout={connect_timeout}"]
    for option in _KEEPALIVE_OPTIONS:
        argv += ["-o", option]
    argv += ["-o", f"HostKeyAlias={pin.alias}"]
    argv += ["-o", f"UserKnownHostsFile={pin.known_hosts}", "-i", identity.private_key]
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


def _require_receipt_for_target(
    receipt: object,
    *,
    pin: PinnedHostKey,
    host_id: str,
    execution_context: StagedFile | None,
) -> None:
    """The receipt must be for THIS pin, alias and host (and context), pre-network."""
    if not isinstance(receipt, lane3_e10_receipt.E10ReceiptRef):
        raise StageRefused("e10-receipt-required")
    try:
        receipt.require_pin_matches(
            ssh_fingerprint=pin.fingerprint, alias=pin.alias, host_id=host_id
        )
        if execution_context is not None:
            lane3_e10_receipt.require_enrolment_matches(execution_context.data, receipt)
    except lane3_e10_receipt.ReceiptRefused as exc:
        raise StageRefused(exc.code) from None


_STAGED_KEY: Final = os.urandom(32)


def _staged_token(
    run_dir: str,
    host_id: str,
    run_id: str,
    receipt_digest: str,
    files: tuple[tuple[str, str], ...],
) -> str:
    material = ["staged-run", run_dir, host_id, run_id, receipt_digest]
    material += [f"{name}={digest}" for name, digest in files]
    return hmac.new(_STAGED_KEY, "\x00".join(material).encode(), "sha256").hexdigest()


@dataclasses.dataclass(frozen=True, slots=True)
class StagedRun:
    """What :func:`stage_artifacts` staged: the run directory, host, run and files.

    Token-bound like :class:`ClockSkewPassed`: it cannot be built by a caller, and
    `dataclasses.replace` onto another host, run, directory or digest fails the
    token at construction and at every use. `drive` and `launch_argv` take THIS,
    never a caller-supplied mapping of what was staged.
    """

    run_dir: str
    host_id: str
    run_id: str
    #: `E10ReceiptRef.digest()` of the receipt staging checked. Token-covered, so
    #: the skew step and `drive` can refuse a different receipt.
    receipt_digest: str
    #: `(relative name, sha256)` for every file staged, sorted.
    files: tuple[tuple[str, str], ...]
    _token: str = dataclasses.field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        self.verify()

    def verify(self) -> None:
        try:
            expected = _staged_token(
                self.run_dir,
                self.host_id,
                self.run_id,
                self.receipt_digest,
                tuple(self.files),
            )
        except (TypeError, ValueError, AttributeError):
            raise StageRefused("staged-run-forged") from None
        if not hmac.compare_digest(str(self._token), expected):
            raise StageRefused("staged-run-forged")

    def digest_of(self, name: str) -> str | None:
        self.verify()
        for staged_name, digest in self.files:
            if staged_name == name:
                return digest
        return None

    def require_for(self, *, run_dir: str, host_id: str, run_id: str) -> None:
        """Refuse unless this is the staging of exactly this directory, host, run."""
        self.verify()
        if (self.run_dir, self.host_id, self.run_id) != (run_dir, host_id, run_id):
            raise StageRefused("staged-run-not-this-lease")

    def require_receipt(self, receipt: object) -> None:
        """Refuse unless `receipt` is the very receipt staging checked."""
        self.verify()
        if not isinstance(receipt, lane3_e10_receipt.E10ReceiptRef):
            raise StageRefused("e10-receipt-required")
        if receipt.digest() != self.receipt_digest:
            raise StageRefused("receipt-not-the-stagings")


def stage_artifacts(
    *,
    run: Runner,
    destination: str,
    identity: LaunchIdentity,
    pin: PinnedHostKey,
    connect_timeout: int,
    host_id: str,
    run_id: str,
    receipt: lane3_e10_receipt.E10ReceiptRef,
    artifacts: Mapping[str, StagedFile],
    lease: StagedFile,
    lease_filename: str,
    wheelhouse: Mapping[str, StagedFile],
) -> StagedRun:
    """Create the per-run directory and ship every artifact by digest.

    Each file lands, is hashed again ON THE TARGET, and must equal the digest the
    launcher computed from the very bytes it sent. Returns the :class:`StagedRun`
    (run directory, host, run and every file's digest, token-bound).

    The E10 receipt is checked against the pin, the alias, the host and the
    staged execution context BEFORE the first remote call, so one target's DSN or
    context can never be shipped to a host the receipt is not for.
    """
    if not isinstance(identity, LaunchIdentity):
        raise StageRefused("ssh-identity-required")
    identity.require_for(host_id=host_id, run_id=run_id)
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
    _require_receipt_for_target(
        receipt,
        pin=pin,
        host_id=host_id,
        execution_context=artifacts["execution-context.json"],
    )

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
    files = tuple(sorted((relative, staged.sha256) for relative, staged, _ in plan))
    receipt_digest = receipt.digest()
    return StagedRun(
        run_dir=run_dir,
        host_id=host_id,
        run_id=run_id,
        receipt_digest=receipt_digest,
        files=files,
        _token=_staged_token(run_dir, host_id, run_id, receipt_digest, files),
    )


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
    identity: LaunchIdentity,
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


_PROOF_KEY: Final = os.urandom(32)


def _proof_token(run_dir: str, run_id: str, host_id: str) -> str:
    return hmac.new(
        _PROOF_KEY, "\x00".join((run_dir, run_id, host_id)).encode(), "sha256"
    ).hexdigest()


@dataclasses.dataclass(frozen=True, slots=True)
class ClockSkewPassed:
    """Proof that CP's skew check passed for THIS run, on THIS host.

    Carries a private token over (run directory, run id, host). It is checked when
    the object is built AND at every use, so `dataclasses.replace` to re-bind a
    genuine proof to another run, directory or host fails: the copied token no
    longer covers the new values.
    """

    run_dir: str
    run_id: str
    host_id: str
    _token: str = dataclasses.field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        self.verify()

    def verify(self) -> None:
        expected = _proof_token(self.run_dir, self.run_id, self.host_id)
        if not hmac.compare_digest(str(self._token), expected):
            raise StageRefused("clock-skew-proof-forged")


def require_clock_skew(
    *,
    run: Runner,
    run_dir: str,
    run_id: str,
    host_id: str,
    destination: str,
    identity: LaunchIdentity,
    pin: PinnedHostKey,
    connect_timeout: int,
    extra_env: Mapping[str, str],
    staged: StagedRun,
    receipt: lane3_e10_receipt.E10ReceiptRef,
    execution_context: StagedFile,
) -> ClockSkewPassed:
    """Run CP's skew check on the target; only exit 0 yields the proof.

    CP's command exits 0 within the limit and 2 on any refusal; anything else
    (an SSH failure, a missing module) is a failed check, never a pass. The proof
    is bound to the run directory, the run id and the host.
    """
    if not run_id or not host_id:
        raise StageRefused("clock-skew-binding-required")
    # The identity must be the one built for THIS lease: its host and its run.
    # Refused BEFORE anything is run on the target.
    if not isinstance(identity, LaunchIdentity):
        raise StageRefused("ssh-identity-required")
    identity.require_for(host_id=host_id, run_id=run_id)
    # The skew command exports the DSN on the target: the staging must be this
    # run's, and the receipt must be for this pin, alias and host, BEFORE it runs.
    if not isinstance(staged, StagedRun):
        raise StageRefused("staged-run-required")
    staged.require_for(run_dir=run_dir, host_id=host_id, run_id=run_id)
    # The same receipt staging checked, and the same context: the enrolment is
    # checked here exactly as staging checked it.
    try:
        _require_staged("execution-context.json", execution_context)
    except StageRefused:
        raise StageRefused("execution-context-digest-mismatch") from None
    if staged.digest_of("execution-context.json") != execution_context.sha256:
        raise StageRefused("execution-context-not-the-staged-file")
    _require_receipt_for_target(
        receipt, pin=pin, host_id=host_id, execution_context=execution_context
    )
    staged.require_receipt(receipt)
    result = run(
        clock_skew_argv(
            run_dir=run_dir,
            destination=destination,
            identity=identity,
            pin=pin,
            connect_timeout=connect_timeout,
            extra_env=extra_env,
        ),
        None,
    )
    if result.exit_code == 0:
        return ClockSkewPassed(
            run_dir=run_dir,
            run_id=run_id,
            host_id=host_id,
            _token=_proof_token(run_dir, run_id, host_id),
        )
    if result.exit_code == 2:
        raise StageRefused("clock-skew-exceeded")
    raise StageRefused("clock-skew-check-failed")


def launch_argv(
    *,
    run_dir: str,
    destination: str,
    identity: LaunchIdentity,
    pin: PinnedHostKey,
    connect_timeout: int,
    extra_env: Mapping[str, str],
    skew: ClockSkewPassed,
    run_id: str,
    host_id: str,
    staged: StagedRun,
) -> list[str]:
    """The session, in the candidate venv, `-E -P`, with the channel on stdio.

    Refused unless `skew` is the proof for this very run directory, run id and
    host: a session cannot be launched on a target whose clock was not checked. The
    identity must also be one built by `LaunchIdentity.for_lease` for this host and
    run.
    """
    if not isinstance(identity, LaunchIdentity):
        raise StageRefused("ssh-identity-required")
    identity.require_for(host_id=host_id, run_id=run_id)
    if not isinstance(skew, ClockSkewPassed) or (
        skew.run_dir,
        skew.run_id,
        skew.host_id,
    ) != (run_dir, run_id, host_id):
        raise StageRefused("clock-skew-not-checked")
    # The binding matches; the TOKEN must still cover exactly these values, which
    # catches a proof whose fields were changed after it was issued.
    skew.verify()
    # What is launched is what was staged: same directory, host and run.
    if not isinstance(staged, StagedRun):
        raise StageRefused("staged-run-required")
    staged.require_for(run_dir=run_dir, host_id=host_id, run_id=run_id)
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
    identity: LaunchIdentity,
    pin: PinnedHostKey,
    connect_timeout: int,
    host_id: str,
    run_id: str,
) -> list[str]:
    """Remove the per-run directory. Best effort; the run's verdict is fixed.

    The identity must be the one built for this host AND run, so a key that was
    proven for another lease can never remove this run's directory.
    """
    if not isinstance(identity, LaunchIdentity):
        raise StageRefused("ssh-identity-required")
    if not isinstance(host_id, str) or not host_id:
        raise StageRefused("teardown-binding-required")
    if not isinstance(run_id, str) or not run_id:
        raise StageRefused("teardown-binding-required")
    identity.require_for(host_id=host_id, run_id=run_id)
    if not _RUN_DIR.fullmatch(run_dir):
        raise StageRefused("run-directory-unexpected")
    return ssh_argv(
        destination=destination,
        identity=identity,
        pin=pin,
        connect_timeout=connect_timeout,
        remote=f"rm -rf -- {shlex.quote(run_dir)}",
    )
