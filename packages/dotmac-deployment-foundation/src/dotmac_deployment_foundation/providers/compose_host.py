"""`ComposeHostEffects` — the dedicated-VM Docker Compose `Effects` provider.

`engine/run.py`'s `Effects` Protocol is fourteen methods and no implementation.
This module is the first implementation: the profile every inventoried product
(`dotmac_sub`, `dotmac_erp`, `dotmac_integrator`, `dotmac_starter_mt`) already
runs — one host, `docker compose`, an Nginx warm-candidate handoff. It is the
faithful, tested translation of `dotmac_sub/scripts/deploy.sh` (880 lines of
`if`-statements interleaved with `docker` invocations that can only be
exercised on a real host) into something `engine/run.py`'s failure-injection
matrix can drive without one.

## The one seam

Every `docker`, `git` and `nginx` invocation in this file goes through
`_run`, which shells out via `subprocess.run` with `shell=False` and an
argument LIST — never a shell string. That is not a style preference: a
descriptor value containing `;` or `$(rm -rf /)` must reach the child process
as one literal argument no shell ever re-parses. `shell=True` (or building a
command string and handing it to `shell=True`, or to `os.system`) turns every
`str_list` field `spec.py` accepts into a command-injection surface the
moment a product's own descriptor — not even an attacker's input — contains a
character a shell treats specially. `_run` is injected as the `runner`
constructor argument specifically so a test can drive this provider with a
scripted fake and assert on the exact argv it was given, with no Docker
daemon, no git repository and no network required.

`backup`/`verify_backup` cannot share that seam: they need to stream bytes
through Python (hash while writing; decompress while discarding) without
holding a multi-gigabyte dump in memory, which `subprocess.run`'s
capture-everything-then-return model cannot do. They get their OWN narrow
seam, `popen_factory`, for exactly that reason — stated here so a reader does
not read `_run` as "the" seam and then find a second one and wonder why.

## Everything by config

Every path, binary name, timeout, interval and naming convention below is a
constructor argument with a documented default. Nothing here hardcodes
`docker`, `/var/backups`, a port or a host in a way a product cannot override
— see `AGENTS.md` § "Everything by config".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess  # nosec B404 -- argv lists, shell=False; see the module docstring
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import IO

from ..engine.run import BackupResult, CommandResult, RoleObservation
from ..errors import PreconditionFailed, StepFailed
from ..evidence import SignedEvidenceEnvelope
from ..recovery import refuse_identity_stripping
from ..render.compose import render_compose
from ..render.nginx import _ingress_roles as _nginx_ingress_roles
from ..render.nginx import handoff_contract_pattern, render_nginx
from ..spec import BackupDataset, ProductDeploymentSpec
from ..toolchain import DEFAULT_TOOLS, require_absolute_tool

__all__ = ["ComposeHostEffects", "NginxInstaller"]

Runner = Callable[..., CommandResult]
PopenFactory = Callable[
    [Sequence[str], "Mapping[str, str] | None"], "subprocess.Popen[bytes]"
]
ReadinessProbe = Callable[[str, float], bool]


# ── the default runner and popen factory ────────────────────────────────────


def _default_runner(
    argv: Sequence[str],
    *,
    timeout: int,
    env: Mapping[str, str] | None = None,
    capture: bool = True,
) -> CommandResult:
    """`subprocess.run`, `shell=False`, an argv LIST — never a shell string.

    Failures that never reach an exit code (a missing binary, a permission
    error) are turned into a `CommandResult` rather than raised, so a caller
    that only branches on `.ok`/`.exit_code` (`Executor._do_migrate`'s
    lock-contention retry, in particular) sees one uniform shape regardless
    of whether the child ran at all.
    """
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 -- argv LIST, shell=False
            list(argv),
            shell=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            capture_output=capture,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            124, "", f"timed out after {timeout}s{': ' + stderr if stderr else ''}"
        )
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(
        completed.returncode, completed.stdout or "", completed.stderr or ""
    )


def _default_popen_factory(
    argv: Sequence[str], env: Mapping[str, str] | None
) -> subprocess.Popen[bytes]:
    """`subprocess.Popen`, `shell=False` — the streaming half of the seam.

    Used only by `backup`, which must read the child's stdout incrementally
    (hash + write, or hash + discard) rather than buffer the whole dump.
    """
    return subprocess.Popen(  # nosec B603 -- argv LIST, shell=False  # noqa: S603 - argv list, shell=False, see module docstring
        list(argv),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )


def _default_readiness_probe(url: str, timeout: float) -> bool:
    """A bare GET, 2xx counts as ready. No `requests`/`httpx` — stdlib only."""
    try:
        # nosec B310 -- the URL is built by this module from a loopback address
        # and a port the descriptor declares; no caller-supplied scheme reaches
        # it, which is the thing B310 exists to catch.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            url, timeout=timeout
        ) as response:
            status = getattr(response, "status", 200)
            return 200 <= status < 300
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _output(result: CommandResult) -> str:
    """stderr, falling back to stdout — whichever actually says something.

    A shared one-liner rather than `(result.stderr or result.stdout).strip()`
    repeated at every call site, which is both noise and, at 88 columns, the
    difference between fitting a failure message on one line or not.
    """
    return (result.stderr or result.stdout).strip()


def _candidate_ports(
    spec: ProductDeploymentSpec, role: str, *, candidate_port_base: int
) -> tuple[int, int]:
    """(candidate loopback port, container port) for `role`.

    Mirrors `render/nginx.py`'s `_ingress_roles` + candidate-port derivation
    EXACTLY — role-sorted ordinal, `candidate_port_base + ordinal` — on
    purpose: `render_nginx`'s `backup` upstream member for this role points
    at this exact port (see that module's `handoff_contract_pattern`
    docstring), and a provider that derived candidate ports differently would
    start a candidate nginx never proxies to. `_ingress_roles` is imported
    (not re-derived) from `render/nginx.py` for the same reason this module
    imports `handoff_contract_pattern` rather than re-deriving its regex: two
    independent copies of "which port is role X's candidate on" is exactly
    the shape of drift this whole facility exists to prevent.
    """
    for ordinal, (candidate_role, container_port) in enumerate(
        _nginx_ingress_roles(spec), start=1
    ):
        if candidate_role == role:
            return candidate_port_base + ordinal, container_port
    raise PreconditionFailed(
        f"role {role!r} is not named by an ingress route, so it has no "
        "warm-candidate port — only roles the plan actually starts a "
        "candidate for (`Strategy.WARM_CANDIDATE`) reach this method"
    )


class _HashingWriter:
    """A `write`-only file-like object that hashes exactly what it writes.

    Wrapping the DESTINATION file (rather than hashing the input pg_dump
    stream before compression) means the checksum characterises the bytes
    that actually land on disk — the artefact `verify_backup` re-reads later
    — which is the "write-time checksum" `engine/run.py`'s `_do_backup`
    refuses to accept an empty one for.
    """

    __slots__ = ("_fh", "_hasher", "size")

    def __init__(self, fh: IO[bytes], hasher: hashlib._Hash) -> None:
        self._fh = fh
        self._hasher = hasher
        self.size = 0

    def write(self, data: bytes) -> int:
        self._hasher.update(data)
        self.size += len(data)
        return self._fh.write(data)

    def flush(self) -> None:
        self._fh.flush()


# ── the provider ─────────────────────────────────────────────────────────────


class ComposeHostEffects:
    """`Effects` for one product's dedicated-VM `docker compose` deployment.

    Every `Effects` method is genuinely implemented against `docker`, `git`
    and `nginx` — none raises `NotImplementedError`. Two methods lean on a
    documented, per-product-overridable CONVENTION because the descriptor
    (`spec.py`) declares no command for them:

    - `migration_heads` — see `_infer_heads_command`; override via
      `migration_heads_command`.
    - `scheduler_last_tick_age_seconds` — no `Role` field records how a
      scheduler proves its last tick short of `docker exec`-ing a probe
      command, which the DESCRIPTOR declares; the constructor argument is an
      override with no default, because there is no fleet-wide way to ask a
      scheduler when it last succeeded.

    Everything else — image presence/labels, release evidence, role
    observation, dirty-checkout/override/source-mount detection, material
    resolution, command execution, backup/verify, migration head
    verification, candidate start/gate, switch, worker/scheduler health,
    evidence, and image retention — is a direct, faithful port of the
    corresponding `dotmac_sub/scripts/deploy.sh` gate, restated as a method
    an ordinary unit test can drive with a scripted fake runner.
    """

    def __init__(
        self,
        spec: ProductDeploymentSpec,
        deploy_dir: Path | str,
        *,
        compose_file: Path | str | None = None,
        env_file: Path | str | None = None,
        extra_env_files: tuple[Path | str, ...] = (),
        runner: Runner = _default_runner,
        popen_factory: PopenFactory = _default_popen_factory,
        readiness_probe: ReadinessProbe = _default_readiness_probe,
        clock: Callable[[], float] = time.time,
        docker_bin: str = DEFAULT_TOOLS["docker"],
        git_bin: str = DEFAULT_TOOLS["git"],
        loopback: str = "127.0.0.1",
        app_root: str = "/app",
        image_env_var: str = "APP_IMAGE",
        manage_compose_file: bool = True,
        candidate_port_base: int = 18000,
        candidate_container_prefix: str | None = None,
        db_service: str = "db",
        pg_dump_bin: str = "pg_dump",
        pg_dump_user: str = "postgres",
        pg_dump_database: str | None = None,
        pg_dump_extra_args: tuple[str, ...] = ("--no-owner", "--no-privileges"),
        backup_dir: Path | str = Path("/var/backups"),
        backup_chunk_bytes: int = 1024 * 1024,
        migration_service: str = "migrate",
        migration_heads_command: tuple[str, ...] | None = None,
        annotations_path: Path | None = None,
        release_evidence_file: Path | str | None = None,
        evidence_path: Path | str | None = None,
        approved_compose_overrides: frozenset[str] = frozenset(),
        extra_override_globs: tuple[str, ...] = (),
        image_repository: str | None = None,
        prune_tag_pattern: str = r"^sha-[0-9a-f]+$",
        scheduler_tick_command: tuple[str, ...] | None = None,
        inspect_timeout_seconds: int = 30,
        manifest_inspect_timeout_seconds: int = 30,
        pull_timeout_seconds: int = 600,
        pull_missing_images: bool = True,
        check_registry_for_missing_images: bool = True,
        git_timeout_seconds: int = 15,
        worker_ping_timeout_seconds: int = 30,
        migration_heads_timeout_seconds: int = 60,
    ) -> None:
        self._spec = spec
        self._deploy_dir = Path(deploy_dir)
        self._compose_file = (
            Path(compose_file)
            if compose_file
            else self._deploy_dir / "docker-compose.yml"
        )
        self._env_file = Path(env_file) if env_file else self._deploy_dir / ".env"
        self._extra_env_files = tuple(Path(p) for p in extra_env_files)
        self._runner = runner
        self._popen_factory = popen_factory
        self._readiness_probe = readiness_probe
        self._clock = clock
        # Absolute-path enforcement is unconditional and runs everywhere,
        # including under a scripted fake runner: it is a pure string check,
        # and it removes the whole PATH-resolution class. The filesystem
        # integrity checks live in `toolchain.resolve_tool` and run on the
        # production path, because a unit test that never execs must not need
        # a real /usr/bin/docker to exist.
        self._docker_bin = require_absolute_tool(docker_bin, what="docker_bin")
        self._git_bin = require_absolute_tool(git_bin, what="git_bin")
        self._loopback = loopback
        self._app_root = PurePosixPath(app_root)
        self._image_env_var = image_env_var
        # True: this facility RENDERS the compose file, so `switch` re-renders
        # it for the target image. False: the host has its own compose file
        # that reads `${APP_IMAGE}` and this facility must not overwrite it —
        # the shape Sub needs while its real engine is still the live path.
        self._manage_compose_file = manage_compose_file
        self._candidate_port_base = candidate_port_base
        self._candidate_container_prefix = (
            candidate_container_prefix
            if candidate_container_prefix is not None
            else f"{spec.product}_"
        )
        self._db_service = db_service
        # NOT `require_absolute_tool`. This one runs INSIDE the db container
        # (`docker compose exec -T <db> pg_dump …`), so the CONTAINER's PATH
        # resolves it and the host's cannot influence which binary it is — the
        # premise behind pinning does not hold here. A host path would also be
        # wrong on its own terms: /usr/bin/pg_dump need not exist in a postgres
        # image, which commonly ships it under a versioned directory. The image
        # digest owns this binary's identity.
        self._pg_dump_bin = pg_dump_bin
        self._pg_dump_user = pg_dump_user
        self._pg_dump_database = pg_dump_database or spec.product
        self._pg_dump_extra_args = pg_dump_extra_args
        self._backup_dir = Path(backup_dir) / spec.product
        self._backup_chunk_bytes = backup_chunk_bytes
        self._migration_service = migration_service
        # The DESCRIPTOR answers this. An earlier version inferred it by
        # swapping the `upgrade` token in the migration command for `current`,
        # which is right for `alembic upgrade heads` and silently wrong for
        # `python -m x.migrate upgrade heads` — whose read verb sits in a
        # different argv position — and meaningless for a non-Alembic entry
        # point. `verify_heads` would then have compared the declared heads
        # against the output of a command that did something else.
        self._migration_heads_command = (
            migration_heads_command or spec.migration.heads_command
        )
        self._release_evidence_file = (
            Path(release_evidence_file)
            if release_evidence_file
            else self._deploy_dir / "release-evidence.json"
        )
        self._annotations_path = annotations_path or (
            self._deploy_dir / "deploy-annotations.jsonl"
        )
        self._evidence_path = (
            Path(evidence_path)
            if evidence_path
            else self._deploy_dir / "deploy-evidence.json"
        )
        self._approved_compose_overrides = approved_compose_overrides
        self._extra_override_globs = extra_override_globs
        self._image_repository = image_repository or spec.image.rsplit("@", 1)[0]
        self._prune_tag_pattern = re.compile(prune_tag_pattern)
        self._scheduler_tick_command = scheduler_tick_command
        self._inspect_timeout_seconds = inspect_timeout_seconds
        self._manifest_inspect_timeout_seconds = manifest_inspect_timeout_seconds
        self._pull_timeout_seconds = pull_timeout_seconds
        self._pull_missing_images = pull_missing_images
        self._check_registry_for_missing_images = check_registry_for_missing_images
        self._git_timeout_seconds = git_timeout_seconds
        self._worker_ping_timeout_seconds = worker_ping_timeout_seconds
        self._migration_heads_timeout_seconds = migration_heads_timeout_seconds

    # ── the seam ─────────────────────────────────────────────────────────────

    def _run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str] | None = None,
        capture: bool = True,
    ) -> CommandResult:
        return self._runner(
            list(argv), timeout=timeout_seconds, env=env, capture=capture
        )

    @property
    def _compose_argv(self) -> list[str]:
        return [
            self._docker_bin,
            "compose",
            "--project-name",
            self._spec.product,
            "--project-directory",
            str(self._deploy_dir),
            "--env-file",
            str(self._env_file),
            "-f",
            str(self._compose_file),
        ]

    # ── materials: names resolve, values never leave this file ─────────────

    def _env_file_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for path in (self._env_file, *self._extra_env_files):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
        return values

    def _materials_env(self, materials: Iterable[str]) -> dict[str, str]:
        """The subprocess environment for a command that needs `materials`.

        Starts from the parent process environment (PATH and friends) and
        overlays each requested material's value FROM THE ENV FILE, when the
        env file has one — the file is the deployment's own source, and a
        material only the parent process happens to have exported is not
        something this deployment declared.
        """
        file_values = self._env_file_values()
        env = dict(os.environ)
        for name in materials:
            if name in file_values:
                env[name] = file_values[name]
        return env

    def resolved_materials(self) -> Sequence[str]:
        """NAMES with a non-empty value — never a value, never logged.

        A test proves this by planting a value and asserting it appears
        NOWHERE in the return value or its `repr()`.
        """
        names: set[str] = set()
        for key, value in self._env_file_values().items():
            if value:
                names.add(key)
        for key, value in os.environ.items():
            if value:
                names.add(key)
        return sorted(names)

    def _write_env_value(self, key: str, value: str) -> None:
        """Set `KEY=value` in the deploy dir's env file, atomically.

        Ported from `deploy.sh:set_env_value` (sed-in-place with a backup
        suffix); here it is temp-file-plus-`os.replace` instead, which is
        atomic on POSIX and leaves no window where a reader sees a
        half-written file.
        """
        lines = (
            self._env_file.read_text(encoding="utf-8").splitlines()
            if self._env_file.is_file()
            else []
        )
        pattern = re.compile(rf"^{re.escape(key)}=")
        replaced = False
        new_lines: list[str] = []
        for line in lines:
            if pattern.match(line):
                new_lines.append(f"{key}={value}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"{key}={value}")
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._env_file.parent),
            prefix=f".{self._env_file.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(new_lines))
                if new_lines:
                    fh.write("\n")
            os.replace(tmp_path, self._env_file)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    # ── gates ────────────────────────────────────────────────────────────────

    def image_present(self, reference: str) -> bool:
        """Local `docker image inspect`, falling back to a registry check.

        Distinguishes "absent locally but present in the registry" (pull,
        when `pull_missing_images`) from "absent everywhere" (`False`,
        `verify_image` refuses) — `docker manifest inspect` never mutates
        anything, so this stays a GATE even though it may pull.
        """
        if self._run(
            [self._docker_bin, "image", "inspect", reference],
            timeout_seconds=self._inspect_timeout_seconds,
        ).ok:
            return True
        if not self._check_registry_for_missing_images:
            return False
        manifest = self._run(
            [self._docker_bin, "manifest", "inspect", reference],
            timeout_seconds=self._manifest_inspect_timeout_seconds,
        )
        if not manifest.ok:
            return False  # absent everywhere
        if not self._pull_missing_images:
            return False  # present in the registry; not pulled by policy
        pulled = self._run(
            [self._docker_bin, "pull", reference],
            timeout_seconds=self._pull_timeout_seconds,
        )
        if not pulled.ok:
            raise PreconditionFailed(
                f"`{self._docker_bin} pull {reference}` failed: " f"{_output(pulled)}"
            )
        return True

    def image_labels(self, reference: str) -> Mapping[str, str]:
        result = self._run(
            [
                self._docker_bin,
                "image",
                "inspect",
                "--format",
                "{{json .Config.Labels}}",
                reference,
            ],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not result.ok:
            raise PreconditionFailed(
                f"`{self._docker_bin} image inspect --format {{json .Config.Labels}} "
                f"{reference}` failed: {_output(result)}"
            )
        try:
            labels = json.loads(result.stdout.strip() or "null")
        except json.JSONDecodeError as exc:
            raise PreconditionFailed(
                f"`{self._docker_bin} image inspect` for {reference} did not return "
                f"valid JSON labels: {exc}"
            ) from exc
        return dict(labels) if labels else {}

    def release_evidence(self, revision: str) -> SignedEvidenceEnvelope | None:
        """Reads a checked-in evidence FILE — no network call, ever.

        The facility does not reach GitHub; a product's CI writes this file
        (envelope keyed by revision) as part of publishing a release, and this
        method only reads it.

        PARSED ONCE AND NEVER RESTATED. What stood here was

            return {str(key): str(value) for key, value in entry.items()}

        — written to satisfy the seam's old ``Mapping[str, str]`` type, and it
        flattened the envelope's nested `document` (the very thing the
        signature covers) into a Python repr. Against a GENUINE signed
        envelope, the verifier then judged a restatement and the gate could
        never pass; this is the corruption that made 0.3.0a4 inadmissible.
        The typed envelope refuses a stringified document at construction, so
        this method now has no way to repeat the mistake and still typecheck.
        """
        if not self._release_evidence_file.is_file():
            return None
        try:
            data = json.loads(self._release_evidence_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PreconditionFailed(
                f"release evidence file {self._release_evidence_file} could not be "
                f"read: {exc}"
            ) from exc
        entry = data.get(revision) if isinstance(data, dict) else None
        if entry is None:
            return None
        return SignedEvidenceEnvelope.from_payload(entry)

    def _parse_compose_ps(self, stdout: str) -> list[dict]:
        """`docker compose ps --format json` — one JSON array on older
        Compose, one JSON object per line on newer. Tolerant of both."""
        text = stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        rows: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _is_within_app_root(self, destination: str) -> bool:
        if not destination:
            return False
        dest = PurePosixPath(destination)
        if dest == self._app_root:
            return True
        try:
            dest.relative_to(self._app_root)
            return True
        except ValueError:
            return False

    def _resolve_repo_digest(self, image_id: str) -> str:
        """`RepoDigests`, resolved via a SECOND inspect on the image ID.

        `.Image` on a CONTAINER (a local image ID — a content hash of the
        image's own config) is not the digest this facility compares
        against: two hosts that reach the same registry content by different
        paths (a local build versus a registry pull) can report different
        `.Image` values for byte-identical content. `RepoDigests` — a field
        on the IMAGE object, populated once an image has been pulled from or
        pushed to a registry — is the `repo@sha256:...` reference
        `plan.image_digest` actually compares against, so it takes this
        extra `docker image inspect` hop rather than reading `.Image` off
        the container inspect directly.
        """
        if not image_id:
            return ""
        result = self._run(
            [
                self._docker_bin,
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                image_id,
            ],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not result.ok:
            return ""
        try:
            digests = json.loads(result.stdout.strip() or "[]")
        except json.JSONDecodeError:
            return ""
        for ref in digests:
            if "@sha256:" in ref:
                return ref.rsplit("@", 1)[1]
        return ""

    def _observe_one(self, service: str, container_id: str) -> RoleObservation:
        inspect = self._run(
            [self._docker_bin, "inspect", container_id],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not inspect.ok:
            raise PreconditionFailed(
                f"`{self._docker_bin} inspect {container_id}` ({service}) failed: "
                f"{_output(inspect)}"
            )
        try:
            data = json.loads(inspect.stdout)[0]
        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            raise PreconditionFailed(
                f"`{self._docker_bin} inspect {container_id}` ({service}) did not "
                f"return the expected JSON: {exc}"
            ) from exc
        state = data.get("State", {}) or {}
        running = bool(state.get("Running", False))
        restarts = int(data.get("RestartCount", 0) or 0)
        digest = self._resolve_repo_digest(str(data.get("Image", "")))
        mounts = data.get("Mounts", []) or []
        source_mounted = any(
            self._is_within_app_root(str(mount.get("Destination", "")))
            for mount in mounts
        )
        return RoleObservation(
            service, running, digest, restarts, source_mounted=source_mounted
        )

    def manifest_digest(self, manifest_path: str) -> str:
        """`sha256:<hex>` of the composed product manifest on this host.

        Read from the deploy directory rather than from the repository: the
        question the gate asks is what THIS HOST is about to run, and a manifest
        that is correct in Git and stale on disk is exactly the case worth
        catching. Returns an empty string when the file is absent, which the
        executor treats as a refusal rather than a match — an unreadable
        manifest establishes nothing, and "nothing" is not "agrees".
        """
        root = Path(self._deploy_dir).resolve()
        path = (root / manifest_path).resolve()
        # Containment, checked AFTER resolving. `spec.py` already refuses a
        # `..` or absolute `manifest_path`, but only this side can see a
        # symlink planted inside the deploy directory — and `.resolve()`
        # follows those, so a link named `manifest.json` pointing at
        # `/etc/anything` would otherwise supply the digest that answers this
        # gate. An escape returns "" for the same reason an absent file does:
        # the executor treats it as a refusal, and a file outside the staged
        # release establishes nothing about the staged release.
        if path != root and root not in path.parents:
            return ""
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def observe_roles(self) -> Sequence[RoleObservation]:
        ps = self._run(
            [*self._compose_argv, "ps", "--format", "json"],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not ps.ok:
            raise PreconditionFailed(
                f"`{' '.join(self._compose_argv)} ps --format json` failed: "
                f"{_output(ps)}"
            )
        observations: list[RoleObservation] = []
        for row in self._parse_compose_ps(ps.stdout):
            service = row.get("Service") or row.get("Name")
            container_id = row.get("ID") or row.get("Name")
            if not service or not container_id:
                continue
            observations.append(self._observe_one(str(service), str(container_id)))
        return observations

    def working_tree_dirty(self) -> bool:
        """`git status --porcelain`; a non-git directory is NOT dirty.

        A deployment directory that is not a git checkout at all (a bare
        release drop, an image-only host) has no "uncommitted changes"
        concept to violate — refusing every deploy on such a host over a
        property it structurally cannot have would be a false gate, not a
        safety one.
        """
        if not (self._deploy_dir / ".git").exists():
            return False
        result = self._run(
            [self._git_bin, "-C", str(self._deploy_dir), "status", "--porcelain"],
            timeout_seconds=self._git_timeout_seconds,
        )
        if not result.ok:
            raise PreconditionFailed(
                f"`{self._git_bin} -C {self._deploy_dir} status --porcelain` failed: "
                f"{_output(result)}"
            )
        return bool(result.stdout.strip())

    def _matches_override_glob(self, name: str) -> bool:
        if fnmatch(name, "docker-compose*.y*ml"):
            return True
        return any(fnmatch(name, pattern) for pattern in self._extra_override_globs)

    def untracked_compose_overrides(self) -> Sequence[str]:
        """Untracked `docker-compose*.y*ml` files, minus the approved ones.

        `seabone-staging-dotmac-sub-deploy-landmines` recorded a LOAD-BEARING
        untracked override on a live Sub host — deployment configuration
        (a profile gate, staging-only object storage) that lived only on the
        host and was reverted, twice, by the next re-render. The fix is not
        "ignore all overrides" (that resurrects exactly the incident the
        check exists to catch) — it is a per-file ADR-0018 exemption: the
        override's FILENAME is named in `approved_compose_overrides` by an
        operator who has looked at it and confirmed it is a deliberate,
        reviewed host difference, not an accident nobody noticed. Anything
        NOT on that allowlist is still refused.
        """
        if not (self._deploy_dir / ".git").exists():
            return ()
        result = self._run(
            [
                self._git_bin,
                "-C",
                str(self._deploy_dir),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            timeout_seconds=self._git_timeout_seconds,
        )
        if not result.ok:
            raise PreconditionFailed(
                f"`{self._git_bin} -C {self._deploy_dir} status --porcelain "
                f"--untracked-files=all` failed: {_output(result)}"
            )
        found: list[str] = []
        for line in result.stdout.splitlines():
            if not line.startswith("??"):
                continue
            path = line[3:].strip().strip('"')
            name = Path(path).name
            if not self._matches_override_glob(name):
                continue
            if name in self._approved_compose_overrides:
                continue
            found.append(path)
        return found

    # ── mutation ─────────────────────────────────────────────────────────────

    def run_command(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        materials: Sequence[str] = (),
    ) -> CommandResult:
        """Runs `command` exactly as given — the descriptor's `command`
        arrays already say whatever wrapping (`docker compose run ...`) a
        product needs; this method's only job is materials-into-env and the
        argv-list seam. Never raises for a non-zero exit: `Executor._do_migrate`
        depends on inspecting a REAL `CommandResult` to decide whether a
        failure is lock contention worth retrying.
        """
        env = self._materials_env(materials)
        return self._run(list(command), timeout_seconds=timeout_seconds, env=env)

    # ── backup / verify: the second, streaming seam ─────────────────────────

    def _backup_dataset(self, code: str) -> BackupDataset:
        for dataset in self._spec.backup_datasets:
            if dataset.code == code:
                return dataset
        raise StepFailed("backup", f"no backup dataset {code!r} is declared")

    def _backup_command(self, dataset: BackupDataset) -> list[str]:
        # A product that declares a [database] contract is asking for a recovery
        # bundle, and a recovery bundle carries ownership and ACL evidence. This
        # provider's historical default was ("--no-owner", "--no-privileges"),
        # which deletes exactly that at capture time - after which no downstream
        # check can notice, because the evidence never existed. Refuse here
        # rather than produce an artefact that will be labelled a backup.
        if self._spec.database is not None:
            refuse_identity_stripping(
                self._pg_dump_extra_args,
                where=f"backup dataset {dataset.code!r}",
            )
        return [
            *self._compose_argv,
            "exec",
            "-T",
            self._db_service,
            self._pg_dump_bin,
            "-U",
            self._pg_dump_user,
            "-d",
            self._pg_dump_database,
            *self._pg_dump_extra_args,
        ]

    def backup(self, dataset_code: str, *, timeout_seconds: int) -> BackupResult:
        """`docker compose exec -T <db> pg_dump ...`, streamed through gzip.

        Never a shell pipeline (`pg_dump | gzip > file`): the child's stdout
        is read here, in Python, chunk by chunk, and each chunk is fed to
        BOTH the gzip compressor and (via `_HashingWriter`) the checksum in
        the same pass — so the checksum this returns is computed from the
        exact bytes landing on disk, at write time, not re-derived by
        opening the file again afterward.
        """
        dataset = self._backup_dataset(dataset_code)
        if dataset.kind != "postgres":
            raise StepFailed(
                "backup",
                f"dataset {dataset_code!r} is {dataset.kind!r}; this provider backs "
                "up postgres datasets only — an object_store or volume dataset "
                "needs its own provider-side implementation",
            )
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime(self._clock()))
        out_path = (
            self._backup_dir / f"{self._spec.product}_{dataset_code}_{stamp}.sql.gz"
        )
        tmp_path = out_path.with_name(out_path.name + ".part")
        argv = self._backup_command(dataset)
        env = self._materials_env((dataset.material,))
        deadline = self._clock() + timeout_seconds
        proc = self._popen_factory(argv, env)
        try:
            hasher = hashlib.new(dataset.checksum)
        except ValueError as exc:
            raise StepFailed(
                "backup", f"unknown checksum algorithm {dataset.checksum!r}"
            ) from exc
        try:
            with open(tmp_path, "wb") as raw:
                tee = _HashingWriter(raw, hasher)
                if proc.stdout is None:
                    # A real check, not an assert: `python -O` strips asserts,
                    # and this one guards a backup. Failing here is loud;
                    # continuing would write a zero-byte archive and hash it.
                    raise StepFailed(
                        "backup", "the dump process exposed no stdout to read"
                    )
                with gzip.GzipFile(fileobj=tee, mode="wb") as gz:
                    while True:
                        chunk = proc.stdout.read(self._backup_chunk_bytes)
                        if not chunk:
                            break
                        gz.write(chunk)
                        if self._clock() > deadline:
                            proc.kill()
                            proc.wait(timeout=5)
                            raise StepFailed(
                                "backup",
                                f"`{' '.join(argv)}` exceeded {timeout_seconds}s and "
                                "was killed",
                            )
                size = tee.size
            remaining = max(0.1, deadline - self._clock())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                raise StepFailed(
                    "backup",
                    f"`{' '.join(argv)}` did not exit within {timeout_seconds}s",
                ) from None
            stderr_output = (
                proc.stderr.read().decode("utf-8", "replace").strip()
                if proc.stderr
                else ""
            )
            if proc.returncode != 0:
                raise StepFailed(
                    "backup",
                    f"`{' '.join(argv)}` exited {proc.returncode}: {stderr_output}",
                )
            if size <= 0:
                raise StepFailed(
                    "backup", f"backup of {dataset_code!r} produced an empty archive"
                )
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        os.replace(tmp_path, out_path)
        return BackupResult(
            dataset_code, str(out_path), size, hasher.hexdigest(), dataset.checksum
        )

    def verify_backup(self, result: BackupResult) -> bool:
        """Size, a full re-hash, AND a full decompression to `/dev/null`.

        Three checks, not one, because they catch three different failures:
        the SIZE check catches a short write nothing else would; the RE-HASH
        catches any byte changing since the write-time checksum was
        recorded (bit rot, a bad transfer, tampering); the DECOMPRESSION
        catches a stream that hashes and sizes correctly yet is not actually
        a complete, valid gzip archive — the exact shape `backup.py`'s
        docstring describes as invisible to `pipefail` and a size check
        alone.
        """
        path = Path(result.path)
        try:
            if path.stat().st_size != result.size_bytes:
                return False
        except OSError:
            return False
        try:
            hasher = hashlib.new(result.checksum_algorithm)
        except ValueError:
            return False
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(self._backup_chunk_bytes)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return False
        if hasher.hexdigest() != result.checksum:
            return False
        try:
            with gzip.open(path, "rb") as gz:
                while gz.read(self._backup_chunk_bytes):
                    pass
        except (OSError, EOFError, zlib.error):
            # `gzip.BadGzipFile` (a bad magic number or CRC) is an `OSError`
            # subclass, but a stream truncated mid-member raises a bare
            # `EOFError` — NOT an `OSError` — from `GzipFile._read_eof`, and
            # `zlib.error` can surface from the underlying decompressor on
            # some corrupt inputs. All three mean "not a complete, valid
            # archive," which is exactly what this check exists to catch.
            return False
        return True

    def _candidate_image_env(
        self, materials: Sequence[str], *, image: str
    ) -> dict[str, str]:
        """Materials env plus the CANDIDATE image, injected per invocation.

        The compose file interpolates `${<image_env_var>}`; a process-env
        value overrides the on-disk env file for exactly one invocation, so a
        `compose run` here uses the candidate image while the RUNNING
        containers — and the file `switch` will later pin durably — stay
        untouched. That is the whole of item 7's injection: no early file
        mutation, no window where the on-disk state points at an image nothing
        verified, and no pre-switch command left running old code.
        """
        if not str(image).strip():
            raise PreconditionFailed(
                "a migration-family command needs the image it is about; an "
                "empty image would fall back to whatever the env file still "
                "pins, which is the previous release — the exact drift this "
                "parameter removes"
            )
        return {**self._materials_env(materials), self._image_env_var: str(image)}

    def run_migration_command(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        materials: Sequence[str] = (),
        image: str,
    ) -> CommandResult:
        """Migration-family work runs IN the candidate image, via compose.

        `run_command` runs the descriptor's argv on the bare host — right for
        host tools, and how the migration itself used to run: the HOST's
        alembic and code, mutating a schema the candidate's code owns. This
        runs the same argv inside a one-off container of the candidate image,
        on the compose project's network, with the migration owner material.
        """
        argv = [
            *self._compose_argv,
            "run",
            "--rm",
            "--no-deps",
            self._migration_service,
            *command,
        ]
        env = self._candidate_image_env(materials, image=image)
        return self._run(argv, timeout_seconds=timeout_seconds, env=env)

    def migration_heads(self, *, image: str) -> Sequence[str]:
        argv = [
            *self._compose_argv,
            "run",
            "--rm",
            "--no-deps",
            self._migration_service,
            *self._migration_heads_command,
        ]
        result = self._run(
            argv,
            timeout_seconds=self._migration_heads_timeout_seconds,
            # The heads READ runs in the candidate image too: the previous
            # image's alembic may not know the new lineage's branch labels.
            env=self._candidate_image_env(
                (self._spec.migration.owner_material,), image=image
            ),
        )
        if not result.ok:
            raise StepFailed(
                "verify_heads",
                f"`{' '.join(argv)}` failed: {_output(result)}",
            )
        return self._parse_heads(result.stdout)

    @staticmethod
    def _parse_heads(output: str) -> list[str]:
        """Tolerant of `alembic current`/`heads`'s `<rev> (head)` formatting
        — the revision id is always the first whitespace-separated token."""
        heads: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heads.append(stripped.split()[0])
        return heads

    def stop_roles(self, roles: Sequence[str], *, timeout_seconds: int) -> None:
        if not roles:
            return
        argv = [*self._compose_argv, "stop", "--timeout", str(timeout_seconds), *roles]
        result = self._run(argv, timeout_seconds=timeout_seconds)
        if not result.ok:
            raise StepFailed(
                "stop_for_maintenance",
                f"`{' '.join(argv)}` failed: {_output(result)}",
            )

    def _candidate_container_name(self, role: str) -> str:
        return f"{self._candidate_container_prefix}{role}_candidate"

    def _container_image_id(self, container: str) -> str:
        result = self._run(
            [self._docker_bin, "inspect", "--format", "{{.Image}}", container],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not result.ok:
            raise StepFailed(
                "start_candidate",
                f"could not resolve the image id for {container!r}: "
                f"{_output(result)}",
            )
        return result.stdout.strip()

    def start_candidate(self, role: str, *, timeout_seconds: int, image: str) -> str:
        role_spec = self._spec.role(role)
        port, container_port = _candidate_ports(
            self._spec, role, candidate_port_base=self._candidate_port_base
        )
        name = self._candidate_container_name(role)
        # Best-effort cleanup of a candidate left behind by a prior failed
        # run — its exit code is deliberately not checked, matching
        # `deploy.sh:821` (`docker rm -f ... || true`).
        self._run(
            [self._docker_bin, "rm", "-f", name],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        argv = [
            *self._compose_argv,
            "run",
            "--no-deps",
            "-d",
            "--name",
            name,
            "-p",
            f"{self._loopback}:{port}:{container_port}",
            role,
        ]
        result = self._run(
            argv,
            timeout_seconds=timeout_seconds,
            # The candidate starts on the CANDIDATE image, injected for this
            # one invocation — before this, it started from the on-disk
            # compose file, which still pins the previous image until `switch`
            # re-renders it, and only the engine's post-hoc digest comparison
            # stood between that and gating traffic onto the old release.
            env=self._candidate_image_env(role_spec.materials, image=image),
        )
        if not result.ok:
            raise StepFailed(
                "start_candidate",
                f"`{' '.join(argv)}` failed: {_output(result)}",
            )
        return self._resolve_repo_digest(self._container_image_id(name))

    def candidate_ready(self, role: str) -> bool:
        """Polls the candidate's READINESS probe — never liveness, which by
        `spec.py`'s `HealthCheck` contract cannot fail and would defeat the
        whole point of gating a handoff on it."""
        role_spec = self._spec.role(role)
        probe = role_spec.ready
        if probe is None:
            return False
        port, _ = _candidate_ports(
            self._spec, role, candidate_port_base=self._candidate_port_base
        )
        url = f"http://{self._loopback}:{port}{probe.path}"
        return self._readiness_probe(url, float(probe.timeout_seconds))

    def role_ready(self, role: str) -> bool:
        """The ROLE's own readiness probe, on its own upstream port.

        `candidate_ready` probes the candidate's derived port before traffic;
        this probes the real role after the switch, and exists because two of
        the three strategies never create a candidate — for them, "ready" used
        to mean docker-inspect facts plus a sleep.
        """
        role_spec = self._spec.role(role)
        probe = role_spec.ready
        if probe is None:
            return False
        for ingress_role, port in _nginx_ingress_roles(self._spec):
            if ingress_role == role:
                url = f"http://{self._loopback}:{port}{probe.path}"
                return self._readiness_probe(url, float(probe.timeout_seconds))
        return False

    def switch(self, *, timeout_seconds: int, image: str) -> None:
        """Pins `image` into the env file, then `up -d --force-recreate`.

        Correct for both directions by construction: a deploy calls this
        with the NEW digest, a rollback calls it with the PREVIOUS one
        (`Executor._do_switch` picks which), and this method has no opinion
        about which — it writes whatever it is given.

        With `manage_compose_file` (the default), the compose file is
        RE-RENDERED for the target image before the recreate. Without it, only
        the env file is repointed — which is correct for a host whose
        hand-written compose file interpolates `${APP_IMAGE}` (Sub's
        convention during a shadow or parity phase, where this facility must
        not overwrite the file that is actually protecting production).
        """
        self._write_env_value(self._image_env_var, image)
        if self._manage_compose_file:
            # RE-RENDER, do not just repoint an environment variable. This
            # package's own `render_compose` bakes a literal digest — which is
            # what makes `render --check` a pin and lets drift compare bytes —
            # so a compose file rendered by this facility does not read
            # `${APP_IMAGE}` at all. Writing that variable and recreating would
            # bring the containers back on the SAME baked digest and report a
            # successful rollback that changed nothing.
            #
            # Re-rendering also makes the host's file disagree with the
            # descriptor after a rollback, and that is correct: a host running
            # the previous image IS drift from the approved plan, and
            # `dotmac-deploy drift` should say so rather than be talked out of
            # it here.
            self._write_atomic(
                self._compose_file, render_compose(self._spec, image=image)
            )
        services = list(self._spec.role_codes)
        argv = [*self._compose_argv, "up", "-d", "--force-recreate", *services]
        result = self._run(argv, timeout_seconds=timeout_seconds)
        if not result.ok:
            raise StepFailed(
                "switch",
                f"`{' '.join(argv)}` failed: {_output(result)}",
            )

    def worker_responds(self, role: str) -> bool:
        role_spec = self._spec.role(role)
        worker = role_spec.worker
        if worker is None:
            return False
        argv = [*self._compose_argv, "exec", "-T", role, *worker.ping_command]
        result = self._run(argv, timeout_seconds=self._worker_ping_timeout_seconds)
        return result.ok

    def scheduler_last_tick_age_seconds(self, role: str) -> int | None:
        """Run the role's DECLARED tick command and read a UNIX timestamp.

        The command comes from `[roles.scheduler].tick_command` in the
        descriptor. An earlier version defaulted to `stat -c %Y
        /tmp/celerybeat-schedule` — a Celery Beat implementation detail that is
        wrong for every other scheduler, and that reports the schedule FILE's
        mtime rather than a successful tick even for Celery: a Beat running and
        failing every task still touches it.

        The constructor argument remains, but only as an override; there is no
        fleet-wide default, because there is no fleet-wide way to ask a
        scheduler when it last succeeded.
        """
        declared = (
            self._scheduler_tick_command or self._spec.role(role).scheduler_tick_command
        )
        if not declared:
            # Not a zero and not an exception: UNKNOWN. A role with no declared
            # tick command has not told anyone how to ask, and returning 0 would
            # read as "ticked just now" (ADR-0032: unobserved is UNKNOWN, never
            # ABSENT).
            return None
        argv = [*self._compose_argv, "exec", "-T", role, *declared]
        result = self._run(argv, timeout_seconds=self._inspect_timeout_seconds)
        if not result.ok:
            return None
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            return None
        try:
            mtime = int(lines[-1].strip())
        except ValueError:
            return None
        return max(0, int(self._clock()) - mtime)

    def emit_annotation(self, annotation: Mapping[str, str]) -> None:
        """Append one JSON line to the annotations file, and print it.

        Deliberately a FILE and stdout rather than an HTTP POST. This package
        declares zero runtime dependencies and has no HTTP client, and inventing
        one here would put a network call on the deployment path for something
        that must never be able to fail a deployment. The collector already
        tails files and container logs; an annotation written this way reaches
        the Observability platform through the pipeline that is already carrying
        everything else, rather than through a second path with its own
        credentials and its own failure modes.

        Append-only, one JSON object per line, and never rewritten: the sequence
        of annotations for a host IS the deployment history, and a file that
        gets rewritten loses the run before last exactly when somebody is asking
        what changed.
        """
        line = json.dumps(dict(annotation), sort_keys=True, separators=(",", ":"))
        path = self._annotations_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Also on stdout, so a deployment run's own log carries it even when
        # nothing is tailing the file yet — which is every deployment today.
        print(f"annotation {line}")

    def _write_atomic(self, path: Path, text: str) -> None:
        """Temp file in the SAME directory, fsync, then `os.replace`.

        Same directory because `os.replace` is only atomic within one
        filesystem, and a temp file under `/tmp` may not be on the same one as
        the deploy directory. A half-written compose file is a host that cannot
        start at all.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def write_evidence(self, evidence: Mapping[str, object]) -> str:
        """An IMMUTABLE, content-addressed record, read back before believed.

        What stood here wrote one well-known file with `os.replace` — so every
        deployment's evidence REPLACED the previous deployment's, and the only
        account of what happened to this host had a memory exactly one release
        deep. An incident review that needs "what ran here on Tuesday" found
        whatever ran on Wednesday.

        Three properties now, each carried by mechanism rather than promise:

        * **Immutable** — the record's name IS the sha256 of its canonical
          bytes, under `evidence-records/`. A record can never change, because
          changed bytes are a different name; a name already present with
          DIFFERENT bytes is refused as tampering, not overwritten. Writing
          the same outcome twice is idempotent by construction.
        * **Read back** — the record is re-read from disk and byte-compared
          before this returns. A write the filesystem quietly lost or
          truncated is a refusal here, not a surprise during an incident.
        * **The latest pointer survives** — operators keep the well-known
          path; it is updated (atomically) only AFTER the immutable record is
          proven, and it is a POINTER, never the record of anything.
        """
        canonical = (
            json.dumps(dict(evidence), indent=2, sort_keys=True, default=str) + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        records = self._evidence_path.parent / "evidence-records"
        records.mkdir(parents=True, exist_ok=True)
        record = records / f"{digest}.json"

        if record.exists():
            existing = record.read_bytes()
            if existing != canonical:
                raise PreconditionFailed(
                    f"evidence record {record} exists with DIFFERENT bytes than "
                    "its own content address. A content-addressed name can only "
                    "disagree with its content if the file was edited in place, "
                    "and an evidence store that can be edited is not evidence"
                )
            # Same bytes, same name: recording the same outcome twice is a
            # no-op, not an error.
        else:
            fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(canonical)
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                record.unlink(missing_ok=True)
                raise

        read_back = record.read_bytes()
        if read_back != canonical:
            raise PreconditionFailed(
                f"evidence record {record} did not read back as written "
                f"({len(read_back)} bytes back, {len(canonical)} written). "
                "Persistence that cannot be proven is a hope, not a record"
            )

        # The operator-facing LATEST pointer, updated only after the record is
        # proven. Atomic replace, exactly as before.
        path = self._evidence_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(canonical)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return str(record)

    def read_evidence(self, path: str) -> Mapping[str, object]:
        """Read one immutable record back. The engine's round-trip proof."""
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PreconditionFailed(
                f"evidence record {path} cannot be read back: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise PreconditionFailed(f"evidence record {path} is not a JSON object")
        return document

    def _used_image_ids(self) -> set[str]:
        result = self._run(
            [self._docker_bin, "ps", "-a", "--format", "{{.Image}}"],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not result.ok:
            raise StepFailed(
                "prune_images",
                f"`{self._docker_bin} ps -a` failed: {_output(result)}",
            )
        ids: set[str] = set()
        for ref in {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }:
            inspected = self._run(
                [self._docker_bin, "image", "inspect", "--format", "{{.Id}}", ref],
                timeout_seconds=self._inspect_timeout_seconds,
            )
            if inspected.ok:
                ids.add(inspected.stdout.strip())
        return ids

    def _image_rows(self) -> list[tuple[str, str]]:
        result = self._run(
            [
                self._docker_bin,
                "image",
                "ls",
                self._image_repository,
                "--format",
                "{{.CreatedAt}}\t{{.ID}}\t{{.Repository}}:{{.Tag}}",
            ],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if not result.ok:
            raise StepFailed(
                "prune_images",
                f"`{self._docker_bin} image ls` failed: {_output(result)}",
            )
        rows: list[tuple[str, str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            created_at, image_id, ref = parts
            tag = ref.rsplit(":", 1)[-1]
            if not self._prune_tag_pattern.match(tag):
                continue
            rows.append((created_at, image_id, ref))
        rows.sort(key=lambda row: row[0], reverse=True)
        return [(image_id, ref) for _, image_id, ref in rows]

    def bootstrap_principal_credential(self, bootstrap):  # type: ignore[no-untyped-def]
        """NOT IMPLEMENTED HERE, and the refusal is the correct answer.

        `ADR-0070` puts PostgreSQL mechanics in the product, not in this
        facility: the Foundation plans, invokes and judges, and the assembly
        implements the effect. This provider renders and drives Compose on a
        host — it holds no database driver, resolves no OpenBao reference, and
        must not learn to.

        So the in-package provider conforms to the protocol by REFUSING, which
        is different from not having the method at all. A missing method makes
        this class non-conforming and the failure arrives as an
        `AttributeError` mid-deployment; a present one that refuses arrives as a
        `PreconditionFailed` before any effect, naming what the operator has to
        install. An assembly supplies a provider that can do this through its
        execution bindings.
        """
        raise PreconditionFailed(
            "the compose-host provider cannot bootstrap a database principal's "
            f"credential ({bootstrap.principal!r} on {bootstrap.service!r}). "
            "PostgreSQL mechanics belong to the product (ADR-0070): this "
            "facility plans, invokes and judges, and holds no database driver "
            "and no secret-store client. Supply an assembly provider through "
            "the execution-bindings entry point"
        )

    def prune_images(self, *, retain: int) -> None:
        """Keeps the `retain` most recent UNUSED images; in-use ones are
        always kept regardless of age or count — ported from
        `docker_image_retention.sh`."""
        used_ids = self._used_image_ids()
        kept_unused = 0
        for image_id, ref in self._image_rows():
            if image_id in used_ids:
                continue
            if kept_unused < retain:
                kept_unused += 1
                continue
            result = self._run(
                [self._docker_bin, "image", "rm", ref],
                timeout_seconds=self._inspect_timeout_seconds,
            )
            if not result.ok:
                raise StepFailed(
                    "prune_images",
                    f"`{self._docker_bin} image rm {ref}` failed: "
                    f"{_output(result)}",
                )


# ── the Nginx installer ──────────────────────────────────────────────────────


class NginxInstaller:
    """Atomic install of `render_nginx`'s output, plus the handoff check.

    NOT part of the `Effects` Protocol — `engine/plan.py`'s step list has no
    ingress-install step. The candidate upstream member is present in EVERY
    render and nginx itself promotes it (see `render/nginx.py`'s module
    docstring); nothing in a deployment's own step sequence re-installs the
    vhost. This class exists so a product's host-bootstrap tooling, and any
    future ingress-focused CLI command, share ONE atomic-install
    implementation with the rest of this provider rather than each
    hand-rolling `nginx -t` / `os.replace` again — and so `cli.py`'s
    `cmd_drift` (a command this task does not own) has a `config_digest()`
    to call for the OBSERVED half of a drift comparison, sourced from the
    running host rather than re-reading the file this class itself wrote.
    """

    def __init__(
        self,
        spec: ProductDeploymentSpec,
        site_path: Path | str,
        *,
        nginx_bin: str = "nginx",
        runner: Runner = _default_runner,
        candidate_port_base: int = 18000,
        test_timeout_seconds: int = 30,
        reload_timeout_seconds: int = 30,
    ) -> None:
        self._spec = spec
        self._site_path = Path(site_path)
        self._nginx_bin = nginx_bin
        self._runner = runner
        self._candidate_port_base = candidate_port_base
        self._test_timeout_seconds = test_timeout_seconds
        self._reload_timeout_seconds = reload_timeout_seconds

    def _run(self, argv: Sequence[str], *, timeout_seconds: int) -> CommandResult:
        return self._runner(list(argv), timeout=timeout_seconds, env=None, capture=True)

    def render(self) -> str:
        return render_nginx(self._spec, candidate_port_base=self._candidate_port_base)

    def install(self) -> None:
        """Write temp, `nginx -t`, `os.replace`, reload — roll back the
        previous file on a failed test rather than leaving a config on disk
        that `nginx -t` has already condemned."""
        rendered = self.render()
        previous = (
            self._site_path.read_text(encoding="utf-8")
            if self._site_path.is_file()
            else None
        )
        self._site_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._site_path.parent),
            prefix=f".{self._site_path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(rendered)
            os.replace(tmp_path, self._site_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        test = self._run(
            [self._nginx_bin, "-t"], timeout_seconds=self._test_timeout_seconds
        )
        if not test.ok:
            if previous is not None:
                self._site_path.write_text(previous, encoding="utf-8")
            else:
                self._site_path.unlink(missing_ok=True)
            raise StepFailed(
                "switch",
                f"`{self._nginx_bin} -t` failed after installing the rendered site "
                f"(previous config restored): {_output(test)}",
            )
        reload_result = self._run(
            [self._nginx_bin, "-s", "reload"],
            timeout_seconds=self._reload_timeout_seconds,
        )
        if not reload_result.ok:
            raise StepFailed(
                "switch",
                f"`{self._nginx_bin} -s reload` failed: " f"{_output(reload_result)}",
            )

    def _live_config(self) -> str:
        result = self._run(
            [self._nginx_bin, "-T"], timeout_seconds=self._test_timeout_seconds
        )
        if not result.ok:
            raise PreconditionFailed(
                f"`{self._nginx_bin} -T` failed: {_output(result)}"
            )
        return result.stdout

    def config_digest(self) -> str:
        """`sha256:<hex>` of the LIVE `nginx -T` output — what `drift`
        compares against the approved render, sourced from the host rather
        than from the file this class itself last wrote."""
        return (
            f"sha256:{hashlib.sha256(self._live_config().encode('utf-8')).hexdigest()}"
        )

    def verify_handoff(self, role: str) -> bool:
        """`nginx -T`, searched for `handoff_contract_pattern` — imported
        from `render/nginx.py`, never re-derived, per that function's own
        docstring on why a second copy of the regex is the risk."""
        pattern = handoff_contract_pattern(self._spec, role)
        return re.search(pattern, self._live_config()) is not None
