"""T15 (the Docker CLI argv builder), the S12 vectors, and T16 (real Docker)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid

import pytest
from dotmac_deployment_foundation import operator_job_contract as contract
from dotmac_deployment_foundation.operator_job import (
    InputMount,
    OperatorJobRefusal,
    SecretMount,
    material_set_id,
    run_operator_job,
)
from dotmac_deployment_foundation.providers.compose_operator_job import (
    ComposeOperatorJobEffects,
    build_create_argv,
    verify_container_shape,
)

from tests.unit.foundation_operator_job_support import (
    PAIR,
    FakeAuthority,
    make_bindings,
    make_plan,
)

DOCKER = "/usr/bin/docker"

# S12 vectors confirmed by the coordinator (compact canonical JSON).
_VECTORS = {
    ("legacy_database_url", "migration_database_url"): "0dcc661ea25f",
    ("migration_database_url",): "c8cd75e18839",
    (): "4f53cda18c2b",
}


@pytest.mark.parametrize(("names", "expected"), list(_VECTORS.items()))
def test_s12_material_set_vectors(names, expected) -> None:
    assert material_set_id([n.upper() for n in names]) == expected


# Argv digest vectors, shared byte for byte with the deploy agent:
# sha256 over compact canonical JSON (sort_keys, (",", ":"), ensure_ascii).
_ARGV_VECTORS = {
    (
        "python",
        "-m",
        "importer",
        "execute",
        "--manifest",
        "/job/in/manifest.json",
    ): "sha256:ec19a528282f3ba70df7dae70043b46f44b86c4b5cff8770cf3bd77af6422891",
    (): "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ("a b", "\u00e9"): (
        "sha256:c3f7935fe57d02ff72e85b32b933a39cf98b4071e90ae9bf2025c13c01df454f"
    ),
    ("x",): "sha256:cd65ea2c2ad99e94a85b1b6df72efef9cb2ed0ae933a60c32ce16317f7d7d6aa",
}


@pytest.mark.parametrize(("argv", "expected"), list(_ARGV_VECTORS.items()))
def test_s12_argv_digest_vectors(argv, expected) -> None:
    from dotmac_deployment_foundation.operator_job import argv_digest

    assert argv_digest(list(argv)) == expected


def test_s12_spaced_json_is_not_produced() -> None:
    spaced = json.dumps(["legacy_database_url", "migration_database_url"])
    assert hashlib.sha256(spaced.encode()).hexdigest()[:12] == "a50fd593917c"
    assert material_set_id(["LEGACY_DATABASE_URL", "MIGRATION_DATABASE_URL"]) != (
        "a50fd593917c"
    )


# T15
def _variants():
    base = make_plan()
    yield base, "/run/example/params.env"
    yield base, None
    yield dataclasses.replace(base, argv=("python", "-m", "importer", "execute")), None
    yield dataclasses.replace(base, secret_mounts=(), input_mounts=()), None
    yield dataclasses.replace(base, entrypoint=("/bin/run",)), None
    yield dataclasses.replace(base, read_only_root=False, tmpfs=()), None


@pytest.mark.parametrize(("plan", "env_file"), list(_variants()))
def test_t15_builder_never_widens_and_always_pulls_never(plan, env_file) -> None:
    argv = build_create_argv(DOCKER, plan, env_file=env_file)
    assert "-v" not in argv and "--volume" not in argv
    assert "--privileged" not in argv and "--cap-add" not in argv
    assert "host" not in argv
    assert not any(a.startswith("--network=") for a in argv)
    for index, item in enumerate(argv):
        if item == "--network":
            assert argv[index + 1] != "host"
        if item == "--restart":
            assert argv[index + 1] == "no"
    assert argv.count("--restart") == 1
    assert argv[argv.index("--pull") + 1] == "never"
    assert "docker.sock" not in " ".join(argv)
    assert not any(a == "--env" or a == "-e" for a in argv)
    # argv reaches the container as Cmd with nothing appended
    assert argv[-len(plan.argv) :] == list(plan.argv)
    assert argv[argv.index(plan.image_ref) + 1 :] == list(plan.argv)
    assert f"{contract.LABEL_DISPATCH_ID}={plan.dispatch_id}" in argv
    # every secret and input mount is read-only
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert all(m.endswith(",readonly") for m in mounts if m.startswith("type=bind"))
    assert sum(m.startswith("type=volume") for m in mounts) == 1


@pytest.mark.parametrize("bad", ["/srv/a,b", "/srv/a=b", "relative", "/srv/a\nb"])
def test_t15_a_mount_path_cannot_smuggle_options(bad: str) -> None:
    plan = dataclasses.replace(
        make_plan(), secret_mounts=(SecretMount(source=bad, target="/run/x"),)
    )
    with pytest.raises(OperatorJobRefusal):
        build_create_argv(DOCKER, plan, env_file=None)
    plan = dataclasses.replace(
        make_plan(),
        input_mounts=(InputMount("--m", "/srv/ok", bad, "sha256:" + "0" * 64),),
    )
    with pytest.raises(OperatorJobRefusal):
        build_create_argv(DOCKER, plan, env_file=None)


def test_no_other_module_spells_a_provisional_key() -> None:
    import ast
    from pathlib import Path

    import dotmac_deployment_foundation as facility

    src = Path(facility.__file__).parent
    values = {
        getattr(contract, name)
        for name in contract.PROVISIONAL_KEYS
        if isinstance(getattr(contract, name), str)
        and name
        not in {
            "OPERATION_RUN_JOB",
            "RESOLVED_OPERATION",
            "RESOLVED_DISPATCH_ID",
            "JOB_NAME",
        }
        and getattr(contract, name) not in {"name", "role", "argv", "flag", "path"}
        and getattr(contract, name) not in {"sha256", "source", "image", "present"}
        and getattr(contract, name) not in {"materials", "inputs", "timeout_seconds"}
    }
    assert len(contract.PROVISIONAL_KEYS) >= 30 and values
    offenders = []
    for path in (src / "operator_job.py", src / "providers/compose_operator_job.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in values:
                offenders.append((path.name, node.value))
    assert offenders == []


# T16: the shape, ownership, run and signal checks against a REAL Docker daemon.
#
# REQUIRED in CI (review ruling 8): the `foundation-operator-job-docker` job sets
# DOTMAC_REQUIRE_DOCKER=1, under which a missing daemon FAILS and the job fails
# on any skipped test. Elsewhere, without a daemon, these skip.
REQUIRED = os.environ.get("DOTMAC_REQUIRE_DOCKER") == "1"


def _docker_ok() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603  # nosec B603
                [docker, "info"], capture_output=True, timeout=30, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


needs_docker = pytest.mark.skipif(
    not REQUIRED and not _docker_ok(), reason="no Docker daemon"
)


def test_t16_a_docker_daemon_is_present_when_the_gate_requires_it() -> None:
    if REQUIRED:
        assert _docker_ok(), "DOTMAC_REQUIRE_DOCKER=1 but no usable Docker daemon"


def _docker(*args: str, check: bool = True, stdin: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603  # nosec B603
        [shutil.which("docker") or "docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if check and done.returncode != 0:
        raise AssertionError(f"docker {args[0]} failed: {done.stderr[-400:]}")
    return done.stdout


def _build(tag: str, *, owned: bool) -> str:
    fix = "chown 10001:10001 /job/out && chmod 0700 /job/out" if owned else "true"
    _docker(
        "build",
        "-t",
        tag,
        "-",
        stdin=(
            "FROM busybox:1.36\n"
            f"RUN mkdir -p /job/out && {fix}\n"
            "ENV T16_IMAGE_ENV=1\n"
        ),
    )
    return _docker("image", "inspect", "--format", "{{.Id}}", tag).strip()


class _Env:
    def __init__(self, tmp_path) -> None:
        self.token = uuid.uuid4().hex[:10]
        self.tags: list[str] = []
        self.tmp = tmp_path
        self.product = f"t16x{self.token}"
        self.network = f"{self.product}_back"
        _docker(
            "network",
            "create",
            "--label",
            f"{contract.LABEL_COMPOSE_PROJECT}={self.product}",
            "--label",
            f"{contract.LABEL_COMPOSE_NETWORK}=back",
            self.network,
        )
        self.volumes: list[str] = []
        self.secret = tmp_path / "s1"
        self.secret.write_bytes(b"not-a-real-secret")
        self.manifest = tmp_path / "manifest.json"
        self.manifest.write_bytes(b"{}")
        for path in (self.secret, self.manifest):
            path.chmod(0o444)

    def image(self, *, owned: bool = True) -> str:
        tag = f"dotmac-t16-{self.token}-{len(self.tags)}"
        self.tags.append(tag)
        return _build(tag, owned=owned)

    def plan(self, argv, image_id: str, *, timeout: int = 60, name: str = "a"):
        sid = f"{self.token}{name}"
        digest = "sha256:" + hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        volume = f"dotmac-t16-out-{sid}"
        self.volumes.append(volume)
        return dataclasses.replace(
            make_plan(),
            product=self.product,
            dispatch_id=f"t16-{sid}",
            argv=tuple(argv),
            role="",
            materials=(),
            scoped_names=(),
            secret_files=(),
            params_env_digest="",
            render_unit="",
            secret_mounts=(
                SecretMount(source=str(self.secret), target="/run/secrets/dotmac/S1"),
            ),
            input_mounts=(
                InputMount(
                    "--manifest", str(self.manifest), "/job/in/manifest.json", digest
                ),
            ),
            image_ref=image_id,
            networks=(self.network,),
            network_codes=("back",),
            container_name=f"dotmac-t16-{sid}",
            volume_name=volume,
            timeout_seconds=timeout,
            entrypoint=(),
            job_uid=None,
            job_user_name="",
            user="10001:10001",
        )

    def effects(self, plan, cls=ComposeOperatorJobEffects):
        now = int(time.time())
        from dotmac_deployment_foundation.operator_job import argv_digest

        return cls(
            docker_bin=shutil.which("docker") or DOCKER,
            environ={
                contract.ENV_JOB: plan.name,
                contract.ENV_ARGV_DIGEST: argv_digest(plan.argv),
                contract.ENV_SECRET_FILES: "",
                contract.ENV_DEADLINE: str(now + 900),
            },
        )

    def authority(self):
        authority = FakeAuthority()
        authority.lease_expires_at = int(time.time()) + 1800
        return authority

    def leftovers(self, plan) -> str:
        return _docker(
            "ps",
            "-a",
            "-q",
            "--filter",
            f"label={contract.LABEL_DISPATCH_ID}={plan.dispatch_id}",
        ).strip()

    def close(self) -> None:
        _docker("ps", "-a", "-q", "--filter", f"network={self.network}", check=False)
        for volume in self.volumes:
            _docker("volume", "rm", "-f", volume, check=False)
        _docker("network", "rm", self.network, check=False)
        for tag in self.tags:
            _docker("image", "rm", "-f", tag, check=False)


@pytest.fixture
def env(tmp_path):
    handle = _Env(tmp_path)
    try:
        yield handle
    finally:
        # remove any container this test's dispatches left behind
        for line in _docker(
            "ps",
            "-a",
            "-q",
            "--filter",
            f"label={contract.LABEL_ROLE_JOB}=1",
            check=False,
        ).split():
            _docker("rm", "-f", line, check=False)
        handle.close()


def _run(env, plan, effects=None, authority=None):
    return run_operator_job(
        plan,
        effects or env.effects(plan),
        make_bindings(authority or env.authority()),
        pair=PAIR,
        target="host-a",
    )


@needs_docker
def test_t16_a_real_run_succeeds_writes_the_volume_and_leaves_nothing(env) -> None:
    image = env.image()
    plan = env.plan(
        ["sh", "-c", "cat /job/in/manifest.json >/dev/null && echo ok > /job/out/r"],
        image,
    )
    receipt = _run(env, plan)
    assert receipt.outcome == "succeeded", receipt.reason
    assert receipt.container_removed and env.leftovers(plan) == ""


@needs_docker
def test_t16_secret_and_input_binds_are_read_only_at_runtime(env) -> None:
    image = env.image()
    script = (
        "if echo x >> /run/secrets/dotmac/S1 2>/dev/null; then exit 9; fi; "
        "if echo x >> /job/in/manifest.json 2>/dev/null; then exit 8; fi; exit 0"
    )
    plan = env.plan(["sh", "-c", script], image)
    assert _run(env, plan).outcome == "succeeded"


@needs_docker
def test_t16_a_nonzero_exit_is_failed_and_removed(env) -> None:
    plan = env.plan(["sh", "-c", "exit 3"], env.image())
    receipt = _run(env, plan)
    assert receipt.outcome == "failed" and receipt.exit_code == 3
    assert env.leftovers(plan) == ""


@needs_docker
def test_t16_a_timeout_stops_removes_and_reports_runner_timeout(env) -> None:
    script = 'trap "exit 0" TERM; sleep 120 & wait'
    plan = env.plan(["sh", "-c", script], env.image(), timeout=3)
    receipt = _run(env, plan)
    assert receipt.outcome == "runner_timeout" and receipt.exit_status == 3
    assert env.leftovers(plan) == ""


@needs_docker
def test_t16_sigterm_mid_run_stops_removes_and_reports_interrupted(env) -> None:
    script = 'trap "exit 0" TERM; sleep 120 & wait'
    plan = env.plan(["sh", "-c", script], env.image(), timeout=100)

    class Interrupting(ComposeOperatorJobEffects):
        def start_and_wait(self, name, *, timeout_seconds):
            threading.Timer(1.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return super().start_and_wait(name, timeout_seconds=timeout_seconds)

    started = time.monotonic()
    receipt = _run(env, plan, env.effects(plan, Interrupting))
    assert receipt.outcome == "interrupted" and receipt.exit_status == 3
    assert time.monotonic() - started < 60  # (o): all within 60 s
    assert receipt.container_removed and env.leftovers(plan) == ""


@needs_docker
def test_t16_the_image_out_dir_owner_is_read_from_the_image(env) -> None:
    owned = env.image(owned=True)
    plan = env.plan(["sh", "-c", "exit 0"], owned)
    effects = env.effects(plan)
    assert effects.stat_image_dir(
        owned,
        "/job/out",
        probe_name=f"{plan.container_name}-p",
        dispatch_id=plan.dispatch_id,
    ) == (10001, 10001, 0o700)
    assert env.leftovers(plan) == ""
    # an image whose /job/out is root-owned is refused before any container
    bad_image = env.image(owned=False)
    bad = env.plan(["sh", "-c", "exit 0"], bad_image, name="b")
    receipt = _run(env, bad)
    assert receipt.outcome == "refused" and receipt.reason == "job_shape_refused"
    assert env.leftovers(bad) == ""


@needs_docker
def test_t16_network_labels_are_read_and_a_plain_network_is_refused(env) -> None:
    plan = env.plan(["sh", "-c", "exit 0"], env.image())
    effects = env.effects(plan)
    labels = effects.network_labels(env.network)
    assert labels is not None and labels[contract.LABEL_COMPOSE_PROJECT] == env.product
    assert effects.network_labels("bridge") is not None
    other = dataclasses.replace(
        plan, networks=("bridge",), network_codes=("back",), dispatch_id="t16-plain"
    )
    receipt = _run(env, other)
    assert receipt.reason == "job_shape_refused"


@needs_docker
def test_t16_a_real_container_passes_and_tampering_is_refused(env) -> None:
    plan = env.plan(["sh", "-c", "exit 0"], env.image(), name="c")
    effects = env.effects(plan)
    _docker("volume", "create", plan.volume_name)
    assert effects.create_container(plan, env_file=None).ok
    container = effects.inspect_container(plan.container_name)
    image = effects.inspect_image(plan.image_ref)
    verify_container_shape(plan, container, image)  # passes on the real inspect
    assert container["Config"]["Labels"][contract.LABEL_DISPATCH_ID] == plan.dispatch_id
    assert container["Config"]["Labels"][contract.LABEL_ROLE_JOB] == "1"

    def refused(mutate) -> None:
        copy = json.loads(json.dumps(container))
        mutate(copy)
        with pytest.raises(OperatorJobRefusal) as excinfo:
            verify_container_shape(plan, copy, image)
        assert excinfo.value.code == "job_shape_refused"

    refused(
        lambda c: c["Mounts"].append(
            {"Type": "bind", "Source": "/", "Destination": "/h", "RW": True}
        )
    )
    refused(lambda c: c["Mounts"].pop(0))
    refused(lambda c: c["HostConfig"]["RestartPolicy"].update(Name="always"))
    refused(lambda c: c["NetworkSettings"]["Networks"].update(bridge={}))
    refused(lambda c: c["Config"]["Env"].append("EXTRA_NAME=1"))
    refused(lambda c: c["Config"].update(User="0:0"))
    refused(lambda c: c["HostConfig"].update(CapDrop=[]))
    refused(lambda c: c["HostConfig"].update(SecurityOpt=["seccomp=unconfined"]))


@needs_docker
def test_t16_a_real_privileged_or_restarting_container_is_refused(env) -> None:
    plan = env.plan(["sh", "-c", "exit 0"], env.image(), name="d")
    effects = env.effects(plan)
    _docker("volume", "create", plan.volume_name)
    argv = build_create_argv(shutil.which("docker") or DOCKER, plan, env_file=None)
    index = argv.index("--restart")
    tampered = [
        *argv[: index + 2],
        "--privileged",
        "--restart",
        "always",
        *argv[index + 2 :],
    ]
    done = subprocess.run(  # noqa: S603  # nosec B603
        tampered, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr[-300:]
    container = effects.inspect_container(plan.container_name)
    with pytest.raises(OperatorJobRefusal) as excinfo:
        verify_container_shape(plan, container, effects.inspect_image(plan.image_ref))
    assert excinfo.value.code == "job_shape_refused"
