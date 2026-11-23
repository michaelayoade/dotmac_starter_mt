"""T15 (the Docker CLI argv builder), the S12 vectors, and T16 (real Docker)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
import uuid

import pytest
from dotmac_deployment_foundation import operator_job_contract as contract
from dotmac_deployment_foundation.operator_job import (
    InputMount,
    OperatorJobRefusal,
    SecretMount,
    material_set_id,
)
from dotmac_deployment_foundation.providers.compose_operator_job import (
    ComposeOperatorJobEffects,
    build_create_argv,
    verify_container_shape,
)

from tests.unit.foundation_operator_job_support import make_plan

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


# T16: the T5-T8 checks against a REAL `docker inspect` (needs a Docker daemon)
def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603  # nosec B603
                [shutil.which("docker") or "/usr/bin/docker", "info"],
                capture_output=True,
                timeout=30,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


needs_docker = pytest.mark.skipif(not _docker_ok(), reason="no Docker daemon")


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


@pytest.fixture
def real_container(tmp_path):
    token = uuid.uuid4().hex[:10]
    tag = f"dotmac-t16-{token}"
    network = f"dotmac-t16-net-{token}"
    dockerfile = (
        "FROM busybox:1.36\n"
        "RUN mkdir -p /job/out && chown 10001:10001 /job/out && chmod 0700 /job/out\n"
        "ENV T16_IMAGE_ENV=1\n"
    )
    _docker("build", "-t", tag, "-", stdin=dockerfile)
    image_id = _docker("image", "inspect", "--format", "{{.Id}}", tag).strip()
    _docker("network", "create", network)
    secret = tmp_path / "migration_database_url"
    secret.write_bytes(b"not-a-real-secret")
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"{}")
    for path in (secret, manifest):
        path.chmod(0o444)
    plan = dataclasses.replace(
        make_plan(),
        image_ref=image_id,
        networks=(network,),
        container_name=f"dotmac-t16-{token}",
        volume_name=f"dotmac-t16-out-{token}",
        secret_mounts=(
            SecretMount(
                source=str(secret), target="/run/secrets/dotmac/MIGRATION_DATABASE_URL"
            ),
        ),
        input_mounts=(
            InputMount(
                "--manifest",
                str(manifest),
                "/job/in/manifest.json",
                "sha256:" + "0" * 64,
            ),
        ),
        scoped_names=(),
        user="10001:10001",
    )
    effects = ComposeOperatorJobEffects(docker_bin=shutil.which("docker") or DOCKER)
    try:
        effects.create_volume(plan.volume_name, dispatch_id=plan.dispatch_id)
        yield plan, effects, image_id
    finally:
        _docker("rm", "-f", plan.container_name, check=False)
        _docker("volume", "rm", plan.volume_name, check=False)
        _docker("network", "rm", network, check=False)
        _docker("image", "rm", "-f", tag, check=False)


@needs_docker
def test_t16_a_real_container_passes_and_tampering_is_refused(real_container) -> None:
    plan, effects, _ = real_container
    assert effects.create_container(plan, env_file=None).ok
    container = effects.inspect_container(plan.container_name)
    image = effects.inspect_image(plan.image_ref)
    verify_container_shape(plan, container, image)  # passes on the real inspect
    assert container["Config"]["Labels"][contract.LABEL_DISPATCH_ID] == plan.dispatch_id

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


@needs_docker
def test_t16_a_real_privileged_or_restarting_container_is_refused(
    real_container,
) -> None:
    plan, effects, _ = real_container
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
