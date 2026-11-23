"""Docker construction, read-back and effects for ONE operator job.

Three parts, none of which shares code with ``compose_host.py``:

* :func:`build_create_argv` - the ``docker create`` argument list, built ONLY
  from the typed plan (spec security and networks, the plan image, the mounts
  and argv). It never emits ``-v``, ``--privileged``, a host network, a docker
  socket, a restart policy other than ``no``, or an ``--env`` value. It always
  emits ``--pull never``.
* :func:`verify_container_shape` - the inspect read-back that must pass before
  ``docker start`` (DESIGN section 6.4). Anything else is ``job_shape_refused``.
* :class:`ComposeOperatorJobEffects` - the real effects behind
  ``OperatorJobEffects``: subprocess with ``shell=False`` and argv lists, and
  no read of any secret value or ``secrets/`` directory.
"""

from __future__ import annotations

import grp
import hashlib
import io
import json
import os
import pwd
import signal
import stat
import subprocess  # nosec B404 -- argv lists, shell=False
import tarfile
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from .. import operator_job_contract as contract
from ..engine.run import CommandResult
from ..operator_job import (
    MAX_INPUT_BYTES,
    OperatorJobRefusal,
    WaitResult,
)
from ..toolchain import require_absolute_tool

if TYPE_CHECKING:
    from ..operator_job import OperatorJobPlan

__all__ = [
    "ComposeOperatorJobEffects",
    "build_create_argv",
    "verify_container_shape",
]

STOP_GRACE_SECONDS: Final = 60
POLL_SECONDS: Final = 0.5
#: `docker stop -t` on an interrupt: stop within 30 s, then kill (P-SEC rev7).
INTERRUPT_STOP_SECONDS: Final = 20
TRAPPED_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
MAX_DIR_TAR_BYTES: Final = 4 * 1024 * 1024
MAX_IDENTITY_BYTES: Final = 64 * 1024
MAX_PARAMS_ENV_BYTES: Final = 1024 * 1024
_FORBIDDEN_MOUNT_CHARS = (",", '"', "'", "\n", "\r", "=")

Runner = Callable[..., CommandResult]


def _mount_text(kind: str, source: str, target: str, *, read_only: bool) -> str:
    for part in (source, target):
        if not part.startswith("/") or any(c in part for c in _FORBIDDEN_MOUNT_CHARS):
            raise OperatorJobRefusal(
                "job_shape_refused", "a mount path is not a plain absolute path"
            )
    text = f"type={kind},source={source},target={target}"
    return text + (",readonly" if read_only else "")


def build_create_argv(
    docker_bin: str, plan: OperatorJobPlan, *, env_file: str | None
) -> list[str]:
    argv = [
        docker_bin,
        "create",
        "--pull",
        "never",
        "--restart",
        "no",
        "--name",
        plan.container_name,
        "--label",
        f"{contract.LABEL_DISPATCH_ID}={plan.dispatch_id}",
        "--label",
        f"{contract.LABEL_JOB}={plan.name}",
        "--label",
        f"{contract.LABEL_ROLE_JOB}=1",
        "--user",
        plan.user,
        "--entrypoint",
        plan.entrypoint[0] if plan.entrypoint else "",
        "--network",
        plan.networks[0],
    ]
    if plan.read_only_root:
        argv.append("--read-only")
    if plan.no_new_privileges:
        argv.extend(["--security-opt", "no-new-privileges"])
    for cap in plan.cap_drop:
        argv.extend(["--cap-drop", cap])
    for path in plan.tmpfs:
        argv.extend(["--tmpfs", path])
    if env_file is not None:
        argv.extend(["--env-file", env_file])
    for secret in plan.secret_mounts:
        argv.extend(
            [
                "--mount",
                _mount_text("bind", secret.source, secret.target, read_only=True),
            ]
        )
    for item in plan.input_mounts:
        argv.extend(
            ["--mount", _mount_text("bind", item.source, item.target, read_only=True)]
        )
    argv.extend(
        [
            "--mount",
            f"type=volume,source={plan.volume_name},target={plan.outputs_target}",
            plan.image_ref,
            *plan.argv,
        ]
    )
    return argv


def _names(env: Sequence[str] | None) -> set[str]:
    return {item.split("=", 1)[0] for item in (env or [])}


def _refuse(detail: str) -> OperatorJobRefusal:
    return OperatorJobRefusal("job_shape_refused", detail)


def verify_container_shape(
    plan: OperatorJobPlan,
    container: Mapping[str, Any],
    image: Mapping[str, Any],
) -> None:
    """Refuse (``job_shape_refused``) unless the container is EXACTLY the plan."""
    host = container.get("HostConfig") or {}
    config = container.get("Config") or {}
    restart = host.get("RestartPolicy") or {}
    if restart.get("Name", "") not in ("no", "") or restart.get("MaximumRetryCount", 0):
        raise _refuse("restart policy is not no")
    if host.get("AutoRemove"):
        raise _refuse("AutoRemove is set; Foundation removes the container itself")
    user = str(config.get("User", ""))
    uid = user.split(":", 1)[0]
    if user != plan.user or uid in ("", "0", "root"):
        raise _refuse("container user is not the declared non-root user")
    if host.get("Privileged"):
        raise _refuse("privileged")
    if host.get("CapAdd") or host.get("Devices"):
        raise _refuse("added capabilities or devices")
    if host.get("PidMode", "") not in ("", "private") or str(
        host.get("IpcMode", "")
    ).split(":", 1)[0] in ("host", "container"):
        raise _refuse("shared pid or ipc namespace")
    if host.get("Binds"):
        raise _refuse("legacy binds present")
    if {str(c) for c in host.get("CapDrop") or []} != set(plan.cap_drop):
        raise _refuse("capability drops differ from the spec")
    options = [str(o) for o in host.get("SecurityOpt") or []]
    allowed = (
        {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}
        if plan.no_new_privileges
        else set()
    )
    if any(o not in allowed for o in options) or (
        plan.no_new_privileges and not options
    ):
        raise _refuse("security options differ from the spec")
    if bool(host.get("ReadonlyRootfs")) != plan.read_only_root:
        raise _refuse("root filesystem mode differs from the spec")
    entrypoint = [e for e in (config.get("Entrypoint") or []) if e != ""]
    if entrypoint != list(plan.entrypoint):
        raise _refuse("entrypoint differs from the spec")
    if list(config.get("Cmd") or []) != list(plan.argv):
        raise _refuse("cmd differs from the dispatched argv")
    ref = str(config.get("Image", ""))
    resolved = str(container.get("Image", ""))
    repo_digests = [str(r) for r in (image.get("RepoDigests") or [])]
    if ref != plan.image_ref and not (
        resolved == str(image.get("Id", "-")) and plan.image_ref in repo_digests
    ):
        raise _refuse("image is not the planned digest")
    networks = set((container.get("NetworkSettings") or {}).get("Networks") or {})
    if networks != set(plan.networks):
        raise _refuse("networks differ from the spec")
    expected: set[tuple[str, str, str, bool]] = set()
    for secret in plan.secret_mounts:
        expected.add(("bind", secret.source, secret.target, False))
    for item in plan.input_mounts:
        expected.add(("bind", item.source, item.target, False))
    expected.add(("volume", plan.volume_name, plan.outputs_target, True))
    actual: list[tuple[str, str, str, bool]] = []
    for mount in container.get("Mounts") or []:
        kind = str(mount.get("Type", ""))
        destination = str(mount.get("Destination", ""))
        if kind == "tmpfs" and destination in plan.tmpfs:
            continue
        key = mount.get("Name") if kind == "volume" else mount.get("Source")
        actual.append((kind, str(key), destination, bool(mount.get("RW"))))
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise _refuse("mounts differ from the declared set")
    if not set(host.get("Tmpfs") or {}) <= set(plan.tmpfs):
        raise _refuse("undeclared tmpfs")
    container_env = _names(config.get("Env"))
    image_env = _names((image.get("Config") or {}).get("Env"))
    if (container_env | image_env) & contract.JOB_ONLY_NAMES:
        raise _refuse("a job-only name is present in the environment")
    if container_env - image_env != set(plan.scoped_names):
        raise _refuse("environment names differ from the scoped materials")


# ── real effects ────────────────────────────────────────────────────────────


def _default_runner(
    argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None
) -> CommandResult:
    try:
        done = subprocess.run(  # noqa: S603  # nosec B603 -- argv list, shell=False
            list(argv),
            shell=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(done.returncode, done.stdout or "", done.stderr or "")


def _default_popen(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603  # nosec B603 -- argv list, shell=False
        list(argv),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _open_regular(
    path: str, *, code: str, max_bytes: int
) -> tuple[int, os.stat_result]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise OperatorJobRefusal(
            code, "cannot open without following symlinks"
        ) from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        os.close(fd)
        raise OperatorJobRefusal(code, "not a regular file within the size limit")
    return fd, info


class ComposeOperatorJobEffects:
    def __init__(
        self,
        *,
        docker_bin: str,
        systemctl_bin: str = "/usr/bin/systemctl",
        runner: Runner = _default_runner,
        popen: Callable[[Sequence[str]], Any] = _default_popen,
        environ: Mapping[str, str] | None = None,
        command_timeout_seconds: int = 120,
        monotonic: Callable[[], float] = time.monotonic,
        identity_path: str = contract.JOB_IDENTITY_PATH,
        identity_owner_uid: int = 0,
    ) -> None:
        self._docker = require_absolute_tool(docker_bin, what="docker_bin")
        self._systemctl = require_absolute_tool(systemctl_bin, what="systemctl_bin")
        self._runner = runner
        self._popen = popen
        self._environ = dict(os.environ if environ is None else environ)
        self._timeout = command_timeout_seconds
        self._monotonic = monotonic
        self._identity_path = identity_path
        self._identity_owner_uid = identity_owner_uid
        self._terminated = threading.Event()

    def _docker_run(self, *args: str) -> CommandResult:
        return self._runner([self._docker, *args], timeout=self._timeout)

    def environ(self) -> Mapping[str, str]:
        return dict(self._environ)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def read_params_env(self, path: str) -> bytes:
        fd, info = _open_regular(
            path, code="job_env_mismatch", max_bytes=MAX_PARAMS_ENV_BYTES
        )
        try:
            if stat.S_IMODE(info.st_mode) != 0o400 or info.st_uid != os.geteuid():
                raise OperatorJobRefusal(
                    "job_env_mismatch", "params env must be 0400 and owned by the agent"
                )
            return os.read(fd, MAX_PARAMS_ENV_BYTES + 1)
        finally:
            os.close(fd)

    def docker_group_ids(self) -> tuple[frozenset[int], frozenset[int]]:
        try:
            group = grp.getgrnam("docker")
        except KeyError:
            return frozenset(), frozenset()
        uids: set[int] = set()
        for member in group.gr_mem:
            try:
                uids.add(pwd.getpwnam(member).pw_uid)
            except KeyError:
                continue
        uids.update(p.pw_uid for p in pwd.getpwall() if p.pw_gid == group.gr_gid)
        return frozenset(uids), frozenset({group.gr_gid})

    def systemctl_show(self, unit: str) -> Mapping[str, str]:
        result = self._runner(
            [
                self._systemctl,
                "show",
                "-p",
                "Result",
                "-p",
                "ExecMainStatus",
                "-p",
                "InvocationID",
                "--",
                unit,
            ],
            timeout=self._timeout,
        )
        shown: dict[str, str] = {}
        if result.ok:
            for line in result.stdout.splitlines():
                key, _, value = line.partition("=")
                shown[key] = value
        return shown

    def image_present(self, ref: str) -> bool:
        return self._docker_run("image", "inspect", ref).ok

    def inspect_image(self, ref: str) -> Mapping[str, Any]:
        return self._inspect("image", "inspect", ref)

    def _inspect(self, *args: str) -> Mapping[str, Any]:
        result = self._docker_run(*args)
        try:
            document = json.loads(result.stdout)
            first = document[0]
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise OperatorJobRefusal("job_shape_refused", "inspect unreadable") from exc
        if not result.ok or not isinstance(first, dict):
            raise OperatorJobRefusal("job_shape_refused", "inspect refused")
        return first

    def hash_input(self, path: str) -> str:
        fd, _ = _open_regular(
            path, code="job_input_digest_mismatch", max_bytes=MAX_INPUT_BYTES
        )
        digest = hashlib.sha256()
        try:
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest()

    def volume_exists(self, name: str) -> bool:
        return self._docker_run("volume", "inspect", name).ok

    def create_volume(self, name: str, *, dispatch_id: str) -> None:
        result = self._docker_run(
            "volume",
            "create",
            "--label",
            f"{contract.LABEL_DISPATCH_ID}={dispatch_id}",
            name,
        )
        if not result.ok:
            raise OperatorJobRefusal("job_create_failed", "volume create refused")

    def create_container(
        self, plan: OperatorJobPlan, *, env_file: str | None
    ) -> CommandResult:
        return self._runner(
            build_create_argv(self._docker, plan, env_file=env_file),
            timeout=self._timeout,
        )

    def connect_networks(self, plan: OperatorJobPlan) -> CommandResult:
        for network in plan.networks[1:]:
            result = self._docker_run(
                "network", "connect", network, plan.container_name
            )
            if not result.ok:
                return result
        return CommandResult(0)

    def inspect_container(self, name: str) -> Mapping[str, Any]:
        return self._inspect("inspect", name)

    @contextmanager
    def sigterm_trap(self) -> Iterator[None]:
        self._terminated.clear()
        previous: dict[int, Any] = {}
        if threading.current_thread() is threading.main_thread():
            for number in TRAPPED_SIGNALS:
                previous[number] = signal.signal(
                    number, lambda _signum, _frame: self._terminated.set()
                )
        try:
            yield
        finally:
            for number, handler in previous.items():
                if handler is not None:
                    signal.signal(number, handler)

    def terminate_requested(self) -> bool:
        return self._terminated.is_set()

    def _stop_and_kill(self, name: str, *, grace: int) -> None:
        self._runner([self._docker, "stop", "-t", str(grace), name], timeout=grace + 30)
        self._runner([self._docker, "kill", name], timeout=self._timeout)

    def start_and_wait(self, name: str, *, timeout_seconds: int) -> WaitResult:
        process = self._popen([self._docker, "start", "-a", name])
        deadline = self._monotonic() + timeout_seconds
        while True:
            if self._terminated.is_set():
                reason = "interrupted"
                break
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                reason = "timeout"
                break
            try:
                return WaitResult(
                    process.wait(timeout=min(POLL_SECONDS, remaining)), False
                )
            except subprocess.TimeoutExpired:
                continue
        self._stop_and_kill(
            name,
            grace=STOP_GRACE_SECONDS if reason == "timeout" else INTERRUPT_STOP_SECONDS,
        )
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        return WaitResult(
            None, reason == "timeout", interrupted=reason == "interrupted"
        )

    def read_job_identity(self) -> Mapping[str, Any] | None:
        """The root-owned enrolment file: never CP, never the agent's env.

        Refused unless a regular, non-symlink file owned by root and not
        writable by group or other. Absent returns None (a role job then
        refuses at plan time).
        """
        try:
            fd = os.open(
                self._identity_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OperatorJobRefusal(
                "job_identity_invalid", "cannot open the job identity file"
            ) from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._identity_owner_uid
                or info.st_mode & 0o022
                or info.st_size > MAX_IDENTITY_BYTES
            ):
                raise OperatorJobRefusal(
                    "job_identity_invalid",
                    "the job identity file must be a regular root-owned file, "
                    "not group- or world-writable",
                )
            data = os.read(fd, MAX_IDENTITY_BYTES + 1)
        finally:
            os.close(fd)
        try:
            return tomllib.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OperatorJobRefusal(
                "job_identity_invalid", "the job identity file is not valid TOML"
            ) from exc

    def host_uid(self, account: str) -> int | None:
        try:
            return pwd.getpwnam(account).pw_uid
        except KeyError:
            return None

    def stat_image_dir(
        self, image_ref: str, path: str, *, probe_name: str, dispatch_id: str
    ) -> tuple[int, int, int] | None:
        """Owner and mode of a directory in the IMAGE, via a throwaway container.

        The probe has NO volume, so it shows the image's own `/job/out` (the
        layer the volume is seeded from), never an unseeded volume root.
        """
        created = self._docker_run(
            "create",
            "--pull",
            "never",
            "--name",
            probe_name,
            "--entrypoint",
            "",
            "--label",
            f"{contract.LABEL_DISPATCH_ID}={dispatch_id}",
            "--label",
            f"{contract.LABEL_ROLE_JOB}=1",
            image_ref,
            "/nonexistent-never-started",
        )
        if not created.ok:
            return None
        try:
            done = subprocess.run(  # noqa: S603  # nosec B603 -- argv list, shell=False
                [self._docker, "cp", f"{probe_name}:{path}", "-"],
                shell=False,
                capture_output=True,
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            if done.returncode != 0 or len(done.stdout) > MAX_DIR_TAR_BYTES:
                return None
            with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
                member = archive.next()
        except (OSError, subprocess.SubprocessError, tarfile.TarError):
            return None
        finally:
            self._docker_run("rm", "-f", probe_name)
        if member is None or not member.isdir():
            return None
        return member.uid, member.gid, member.mode & 0o7777

    def network_labels(self, name: str) -> Mapping[str, str] | None:
        result = self._docker_run(
            "network", "inspect", "--format", "{{json .Labels}}", name
        )
        if not result.ok:
            return None
        try:
            labels = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(labels, dict):
            return None
        return {str(k): str(v) for k, v in labels.items()}

    def _label_of(self, name: str) -> str | None:
        result = self._docker_run(
            "inspect",
            "--format",
            '{{index .Config.Labels "' + contract.LABEL_DISPATCH_ID + '"}}',
            name,
        )
        return result.stdout.strip() if result.ok else None

    def remove_container(self, name: str, *, dispatch_id: str) -> bool:
        """Remove ONE container by exact name, only if it is this dispatch's."""
        label = self._label_of(name)
        if label is None:
            return True  # nothing by that name; nothing to leave behind
        if label != dispatch_id:
            return False
        return self._docker_run("rm", "-f", name).ok

    def list_dispatch_containers(self, dispatch_id: str) -> tuple[str, ...]:
        """Containers carrying BOTH labels for this dispatch. Read-only."""
        listed = self._docker_run(
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"label={contract.LABEL_DISPATCH_ID}={dispatch_id}",
            "--filter",
            f"label={contract.LABEL_ROLE_JOB}=1",
            "--format",
            "{{.ID}}",
        )
        if not listed.ok:
            raise OperatorJobRefusal("job_remove_failed", "docker ps refused")
        return tuple(listed.stdout.split())

    def remove_dispatch_containers(self, dispatch_id: str) -> tuple[str, ...]:
        """Label-scoped removal: BOTH labels must match. Never creates or starts."""
        removed: list[str] = []
        for identifier in self.list_dispatch_containers(dispatch_id):
            if self._docker_run("rm", "-f", identifier).ok:
                removed.append(identifier)
        return tuple(removed)
