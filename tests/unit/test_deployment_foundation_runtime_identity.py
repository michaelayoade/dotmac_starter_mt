"""Canaries for live, service-scoped deployment identity observation.

The controller-owned state file is evidence of a completed deployment, not a
source from which missing runtime identity may be reconstructed.  These tests
therefore build Docker's container and image inspection documents separately:
the container supplies Compose topology and live configuration labels, while
the immutable image object supplies source and manifest provenance.

The fake project deliberately contains release-bearing application roles next
to heterogeneous auxiliary images.  A check that accidentally selects every
Compose container as an application replica fails the positive control before
any of the negative cases can pass for the wrong reason.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from dotmac_deployment_foundation.controller import (
    ControllerStateStore,
    DockerCurrentReleaseObserver,
)
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution import ApplicationReleaseIdentityV1
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

PRODUCT = "example"
REVISION = "1" * 40
OTHER_REVISION = "2" * 40
IMAGE_DIGEST = "sha256:" + "a" * 64
CONFIGURATION_DIGEST = "sha256:" + "b" * 64
OTHER_CONFIGURATION_DIGEST = "sha256:" + "c" * 64
MANIFEST_DIGEST = "sha256:" + "d" * 64
OTHER_MANIFEST_DIGEST = "sha256:" + "e" * 64
RUNTIME_DIGEST = "sha256:" + "f" * 64

IDENTITY = ApplicationReleaseIdentityV1(
    image_digest=IMAGE_DIGEST,
    source_revision=REVISION,
    configuration_digest=CONFIGURATION_DIGEST,
    manifest_digest=MANIFEST_DIGEST,
)

SERVICE_KIND_LABEL = "io.dotmac.deployment.service.kind"
PRODUCT_LABEL = "io.dotmac.deployment.product"
IDENTITY_SCHEMA_LABEL = "io.dotmac.deployment.identity.schema"
ROLE_LABEL = "io.dotmac.deployment.role"
CONFIGURATION_DIGEST_LABEL = "io.dotmac.deployment.configuration.digest"
MANIFEST_DIGEST_LABEL = "io.dotmac.deployment.manifest.digest"
ROSTER_LABEL = "io.dotmac.deployment.release.roster"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_CONFIG_HASH_LABEL = "com.docker.compose.config-hash"
COMPOSE_ONEOFF_LABEL = "com.docker.compose.oneoff"
SOURCE_REVISION_LABEL = "org.opencontainers.image.revision"
IMAGE_MANIFEST_DIGEST_LABEL = "org.dotmac.product.manifest.digest"


@dataclass(slots=True)
class _StateStore:
    state: object | None = None

    def load(self) -> object | None:
        return self.state


class _DockerRunner:
    """Return exact, independently supplied container and image documents."""

    def __init__(
        self,
        containers: dict[str, dict[str, object]],
        images: dict[str, dict[str, object]],
    ) -> None:
        self.containers = containers
        self.images = images

    def __call__(
        self, argv: Sequence[str], timeout_seconds: int
    ) -> subprocess.CompletedProcess[str]:
        arguments = list(argv)
        assert timeout_seconds == 30
        if arguments[1] == "ps":
            return _completed(arguments, "".join(f"{key}\n" for key in self.containers))
        if arguments[1] == "inspect":
            return _completed(arguments, json.dumps([self.containers[arguments[2]]]))
        assert arguments[1:3] == ["image", "inspect"]
        return _completed(arguments, json.dumps([self.images[arguments[3]]]))


def _completed(argv: Sequence[str], stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), 0, stdout=stdout, stderr="")


def _image(
    *,
    image_digest: str = IMAGE_DIGEST,
    source_revision: str = REVISION,
    manifest_digest: str = MANIFEST_DIGEST,
) -> dict[str, object]:
    return {
        "RepoDigests": [f"registry.invalid/example@{image_digest}"],
        "Config": {
            "Labels": {
                SOURCE_REVISION_LABEL: source_revision,
                IMAGE_MANIFEST_DIGEST_LABEL: manifest_digest,
            }
        },
    }


def _release_container(
    name: str,
    role: str,
    *,
    image_digest: str = IMAGE_DIGEST,
    configuration_digest: str = CONFIGURATION_DIGEST,
    manifest_digest: str = MANIFEST_DIGEST,
    roster: dict[str, int] | None = None,
    config_hash: str | None = None,
    oneoff: bool = False,
    container_source_revision: str = REVISION,
    include_live_manifest: bool = True,
) -> dict[str, object]:
    labels = {
        SERVICE_KIND_LABEL: "release",
        PRODUCT_LABEL: PRODUCT,
        IDENTITY_SCHEMA_LABEL: "ApplicationReleaseIdentityV1",
        ROLE_LABEL: role,
        CONFIGURATION_DIGEST_LABEL: configuration_digest,
        ROSTER_LABEL: json.dumps(
            roster or {"app": 1, "worker": 2},
            sort_keys=True,
            separators=(",", ":"),
        ),
        COMPOSE_SERVICE_LABEL: role,
        COMPOSE_CONFIG_HASH_LABEL: config_hash or f"config-hash-{name}",
        COMPOSE_ONEOFF_LABEL: "True" if oneoff else "False",
        # A mutable container label must never win over the image-object label.
        SOURCE_REVISION_LABEL: container_source_revision,
    }
    if include_live_manifest:
        labels[MANIFEST_DIGEST_LABEL] = manifest_digest
    return {
        "Image": f"image-id-{name}",
        "Config": {
            "Image": f"registry.invalid/example@{image_digest}",
            "Labels": labels,
        },
    }


def _non_release_container(name: str, kind: str = "auxiliary") -> dict[str, object]:
    return {
        "Image": f"foreign-image-id-{name}",
        "Config": {
            "Image": f"registry.invalid/{name}:foreign",
            "Labels": {
                SERVICE_KIND_LABEL: kind,
                PRODUCT_LABEL: PRODUCT,
                COMPOSE_SERVICE_LABEL: name,
                COMPOSE_ONEOFF_LABEL: "False",
            },
        },
    }


def _project(
    *,
    app_configuration: str = CONFIGURATION_DIGEST,
    worker_configuration: str = CONFIGURATION_DIGEST,
    app_manifest: str = MANIFEST_DIGEST,
    worker_manifest: str = MANIFEST_DIGEST,
    app_image_manifest: str = MANIFEST_DIGEST,
    worker_image_manifest: str = MANIFEST_DIGEST,
    app_roster: dict[str, int] | None = None,
    worker_roster: dict[str, int] | None = None,
    worker_replicas: int = 2,
    app_config_hash: str | None = None,
    worker_config_hash: str | None = None,
    app_oneoff: bool = False,
    include_migration: bool = False,
    app_container_revision: str = REVISION,
    app_image_revision: str = REVISION,
    include_app_manifest: bool = True,
) -> _DockerRunner:
    expected_roster = {"app": 1, "worker": worker_replicas}
    containers = {
        "app-1": _release_container(
            "app-1",
            "app",
            configuration_digest=app_configuration,
            manifest_digest=app_manifest,
            roster=app_roster or expected_roster,
            config_hash=app_config_hash,
            oneoff=app_oneoff,
            container_source_revision=app_container_revision,
            include_live_manifest=include_app_manifest,
        ),
        "worker-1": _release_container(
            "worker-1",
            "worker",
            configuration_digest=worker_configuration,
            manifest_digest=worker_manifest,
            roster=worker_roster or expected_roster,
            config_hash=worker_config_hash,
        ),
        "postgres-1": _non_release_container("postgres"),
        "redis-1": _non_release_container("redis"),
        "otel-collector-1": _non_release_container("otel-collector"),
    }
    images = {
        "image-id-app-1": _image(
            source_revision=app_image_revision,
            manifest_digest=app_image_manifest,
        ),
        "image-id-worker-1": _image(manifest_digest=worker_image_manifest),
    }
    for replica in range(2, worker_replicas + 1):
        name = f"worker-{replica}"
        containers[name] = _release_container(
            name,
            "worker",
            configuration_digest=worker_configuration,
            manifest_digest=worker_manifest,
            roster=worker_roster or expected_roster,
            config_hash=worker_config_hash,
        )
        images[f"image-id-{name}"] = _image(manifest_digest=worker_image_manifest)
    if include_migration:
        containers["migrate-1"] = _non_release_container("migrate", "migration")
    return _DockerRunner(containers, images)


def _observer(
    runner: _DockerRunner, store: _StateStore | None = None
) -> DockerCurrentReleaseObserver:
    state_store = cast(ControllerStateStore, store or _StateStore())
    return DockerCurrentReleaseObserver(
        docker_binary=Path("/usr/bin/docker"),
        product=PRODUCT,
        state_store=state_store,
        runner=runner,
    )


def test_heterogeneous_project_observes_only_declared_release_roles() -> None:
    observation = _observer(_project()).observe(expected_current=IDENTITY)

    assert observation.identity == IDENTITY
    assert observation.relation_unprovable is False
    assert observation.runtime_configuration_digest is not None


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (
            _project(worker_configuration=OTHER_CONFIGURATION_DIGEST),
            "four release identity coordinates",
        ),
        (
            _project(
                worker_manifest=OTHER_MANIFEST_DIGEST,
                worker_image_manifest=OTHER_MANIFEST_DIGEST,
            ),
            "four release identity coordinates",
        ),
    ],
)
def test_release_roles_must_agree_on_configuration_and_manifest(
    runner: _DockerRunner, message: str
) -> None:
    with pytest.raises(PreconditionFailed, match=message):
        _observer(runner).observe(expected_current=IDENTITY)


def test_image_object_provenance_wins_over_mutable_container_labels() -> None:
    observation = _observer(_project(app_container_revision=OTHER_REVISION)).observe(
        expected_current=IDENTITY
    )

    assert observation.identity == IDENTITY


def test_expected_container_provenance_cannot_mask_a_different_image_revision() -> None:
    runner = _project(
        app_container_revision=REVISION,
        app_image_revision=OTHER_REVISION,
    )

    with pytest.raises(PreconditionFailed, match="four release identity coordinates"):
        _observer(runner).observe(expected_current=IDENTITY)


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (
            _project(
                app_roster={"app": 1, "worker": 2},
                worker_roster={"app": 1, "worker": 3},
            ),
            "role roster",
        ),
        (
            _project(
                app_roster={"app": 1, "worker": 2},
                worker_roster={"app": 1, "worker": 2},
                worker_replicas=1,
            ),
            "quorum",
        ),
    ],
)
def test_release_roster_and_replica_quorum_are_exact(
    runner: _DockerRunner, message: str
) -> None:
    with pytest.raises(PreconditionFailed, match=message):
        _observer(runner).observe(expected_current=IDENTITY)


def test_completed_runtime_matches_canonical_roster_and_hashes() -> None:
    expected_hashes = {"app": "canonical-app", "worker": "canonical-worker"}
    observation = _observer(
        _project(
            app_config_hash=expected_hashes["app"],
            worker_config_hash=expected_hashes["worker"],
        )
    ).observe_completed(
        expected=IDENTITY,
        expected_roster={"app": 1, "worker": 2},
        expected_compose_hashes=expected_hashes,
    )

    assert observation.identity == IDENTITY
    assert observation.runtime_configuration_digest is not None


def test_self_consistent_reduced_roster_cannot_replace_descriptor_roster() -> None:
    reduced = {"app": 1, "worker": 1}
    runner = _project(
        worker_replicas=1,
        app_roster=reduced,
        worker_roster=reduced,
        app_config_hash="canonical-app",
        worker_config_hash="canonical-worker",
    )

    with pytest.raises(PreconditionFailed, match="canonical descriptor requires"):
        _observer(runner).observe_completed(
            expected=IDENTITY,
            expected_roster={"app": 1, "worker": 2},
            expected_compose_hashes={
                "app": "canonical-app",
                "worker": "canonical-worker",
            },
        )


def test_hand_edited_service_configuration_cannot_preserve_approved_labels() -> None:
    runner = _project(
        app_config_hash="hand-edited-app",
        worker_config_hash="canonical-worker",
    )

    with pytest.raises(PreconditionFailed, match="canonical render"):
        _observer(runner).observe_completed(
            expected=IDENTITY,
            expected_roster={"app": 1, "worker": 2},
            expected_compose_hashes={
                "app": "canonical-app",
                "worker": "canonical-worker",
            },
        )


def test_expected_hashes_come_from_compose_config_hash_of_private_render(
    tmp_path: Path,
) -> None:
    project_directory = (tmp_path / "application").resolve()
    project_directory.mkdir()
    env_file = project_directory / ".env"
    env_file.write_text("DATABASE_URL=held\n", encoding="utf-8")
    rendered = "services:\n  app:\n    image: example\n  worker:\n    image: example\n"
    invocations: list[list[str]] = []

    def compose_runner(
        argv: Sequence[str], timeout_seconds: int
    ) -> subprocess.CompletedProcess[str]:
        arguments = list(argv)
        invocations.append(arguments)
        assert timeout_seconds == 30
        assert arguments[1:3] == ["compose", "--project-name"]
        assert arguments[-4:] == ["config", "--hash", "app", "worker"]
        compose_path = Path(arguments[arguments.index("-f") + 1])
        assert compose_path.read_text(encoding="utf-8") == rendered
        assert compose_path.stat().st_mode & 0o777 == 0o600
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout="app canonical-app\nworker canonical-worker\n",
            stderr="",
        )

    observer = DockerCurrentReleaseObserver(
        docker_binary=Path("/usr/bin/docker"),
        product=PRODUCT,
        state_store=ControllerStateStore(
            (tmp_path / "state").resolve(),
            product=PRODUCT,
            target_ref="observe:example",
        ),
        runner=compose_runner,
    )

    hashes = observer.expected_compose_hashes(
        rendered_compose=rendered,
        project_directory=project_directory,
        env_file=env_file,
        roles=("app", "worker"),
    )

    assert hashes == {"app": "canonical-app", "worker": "canonical-worker"}
    assert len(invocations) == 1
    assert invocations[0][-4:] == ["config", "--hash", "app", "worker"]


def test_controller_state_cannot_supply_a_missing_live_manifest() -> None:
    state = SimpleNamespace(
        product=PRODUCT,
        current=IDENTITY,
        runtime_configuration_digest=RUNTIME_DIGEST,
    )

    with pytest.raises(PreconditionFailed, match="manifest label disagrees"):
        _observer(
            _project(include_app_manifest=False),
            _StateStore(state),
        ).observe(expected_current=IDENTITY)


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (_project(app_oneoff=True), "one-off containers remain"),
        (_project(include_migration=True), "migration service is still running"),
    ],
)
def test_non_steady_state_containers_are_refused(
    runner: _DockerRunner, message: str
) -> None:
    with pytest.raises(PreconditionFailed, match=message):
        _observer(runner).observe(expected_current=IDENTITY)


def test_runtime_configuration_hash_must_match_controller_state() -> None:
    runner = _project()
    baseline = _observer(runner).observe(expected_current=IDENTITY)
    assert baseline.runtime_configuration_digest is not None
    matching_state = SimpleNamespace(
        product=PRODUCT,
        current=IDENTITY,
        runtime_configuration_digest=baseline.runtime_configuration_digest,
    )

    matching = _observer(runner, _StateStore(matching_state)).observe(
        expected_current=IDENTITY
    )
    assert (
        matching.runtime_configuration_digest == baseline.runtime_configuration_digest
    )

    drifted_state = SimpleNamespace(
        product=PRODUCT,
        current=IDENTITY,
        runtime_configuration_digest=RUNTIME_DIGEST,
    )
    with pytest.raises(PreconditionFailed, match="runtime configuration state"):
        _observer(runner, _StateStore(drifted_state)).observe(expected_current=IDENTITY)


DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "{PRODUCT}"
environment = "test"

[assembly]
manifest_path = "deploy/product-manifest.json"
manifest_digest = "{MANIFEST_DIGEST}"

[image]
reference = "registry.invalid/example@{IMAGE_DIGEST}"
source_revision = "{REVISION}"

[runtime_materials]
names = ["DATABASE_URL", "OTEL_EXPORTER_OTLP_ENDPOINT"]

[[roles]]
code = "app"
command = ["python", "-m", "app"]
replicas = 1
depends_on = ["postgres", "redis"]
materials = ["DATABASE_URL"]
[roles.resources]
cpus = "1.0"
memory = "512m"
[roles.health.live]
path = "/health/live"
port = 8000
[roles.health.ready]
path = "/health/ready"
port = 8000

[[roles]]
code = "worker"
command = ["python", "-m", "worker"]
replicas = 2
depends_on = ["postgres", "redis"]
[roles.resources]
cpus = "0.5"
memory = "256m"
[roles.health.live]
path = "/health/live"
port = 8001

[[external_dependencies]]
code = "postgres"
kind = "postgres"
required_for = ["ready", "migrate"]
image = "postgres:16"
health_probe = ["pg_isready"]

[[external_dependencies]]
code = "redis"
kind = "redis"
required_for = ["ready"]
image = "redis:7"
health_probe = ["redis-cli", "ping"]

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["head"]
compatibility = "online"

[telemetry]
collector_image = "otel/opentelemetry-collector-contrib:0.109.0"
endpoint_material = "OTEL_EXPORTER_OTLP_ENDPOINT"
"""


def test_compose_declares_release_bearing_and_auxiliary_service_roles() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR, source="<runtime-identity-test>")
    document = yaml.safe_load(render_compose(spec, release_identity=IDENTITY))
    services = document["services"]

    for name in ("app", "worker"):
        labels = services[name]["labels"]
        assert labels[SERVICE_KIND_LABEL] == "release"
        assert labels[ROLE_LABEL] == name
        assert labels[IDENTITY_SCHEMA_LABEL] == "ApplicationReleaseIdentityV1"
        assert labels[CONFIGURATION_DIGEST_LABEL] == CONFIGURATION_DIGEST
        assert labels[MANIFEST_DIGEST_LABEL] == MANIFEST_DIGEST
        assert json.loads(labels[ROSTER_LABEL]) == {"app": 1, "worker": 2}

    for name in ("postgres", "redis", "otel-collector"):
        labels = services[name]["labels"]
        assert labels[SERVICE_KIND_LABEL] == "auxiliary"
        assert IDENTITY_SCHEMA_LABEL not in labels

    migrate_labels = services["migrate"]["labels"]
    assert migrate_labels[SERVICE_KIND_LABEL] == "migration"
    assert IDENTITY_SCHEMA_LABEL not in migrate_labels
