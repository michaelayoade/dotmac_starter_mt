"""Canaries for the independent controller's Foundation-owned execution path.

The application supplies only a strict descriptor. The controller observes the
current release, builds the canonical typed plan, authorises it, runs the
Foundation executor, observes the completed runtime, and only then records its
state. These tests keep those operations inside one lock and pair refusals with
a successful control.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import pytest
from dotmac_deployment_foundation import cli as cli_module
from dotmac_deployment_foundation import controller as controller_module
from dotmac_deployment_foundation.authenticity import (
    ApplicationHistorySnapshotV1,
    DeploymentAuthorizationEvidenceV1,
    GitHubWorkflowRunV1,
)
from dotmac_deployment_foundation.cli import (
    build_parser,
    cmd_deploy,
    cmd_execute_authorized,
    cmd_rollback,
)
from dotmac_deployment_foundation.controller import (
    CONTROLLER_LOCK_ROOT,
    CONTROLLER_STATE_ROOT,
    ControllerStateStore,
    ControllerStateV1,
    CurrentReleaseObservation,
    DockerCurrentReleaseObserver,
    deployment_plan_digest,
    deployment_plan_document,
    digest_file,
    execute_authorized,
)
from dotmac_deployment_foundation.engine.lock import deployment_lock
from dotmac_deployment_foundation.engine.plan import DeploymentPlan, build_plan
from dotmac_deployment_foundation.engine.run import DeploymentOutcome
from dotmac_deployment_foundation.errors import (
    LockUnavailableError,
    PreconditionFailed,
    SpecError,
)
from dotmac_deployment_foundation.execution import (
    ApplicationReleaseIdentityV1,
    AuthorizerProvenanceV1,
    ControllerProvenanceV1,
    DeploymentExecutionEnvelopeV1,
    RevisionEvidenceV1,
    RevisionRelation,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

REV_A = "1" * 40
REV_B = "2" * 40
REV_C = "3" * 40
CONTROLLER_REV = "4" * 40
WORKFLOW_REV = "5" * 40
HISTORY_BUNDLE_SHA256 = "sha256:" + "3" * 64
WORKFLOW_BLOB_SHA256 = "sha256:" + "4" * 64
CONTROLLER_RELEASE_EVIDENCE_DIGEST = "sha256:" + "5" * 64

IMAGE_A = "sha256:" + "a" * 64
IMAGE_B = "sha256:" + "b" * 64
IMAGE_C = "sha256:" + "c" * 64
MANIFEST_A = "sha256:" + "d" * 64
MANIFEST_B = "sha256:" + "e" * 64
MANIFEST_C = "sha256:" + "f" * 64
CONFIG_A = "sha256:" + "6" * 64
PLAN_DIGEST = "sha256:" + "7" * 64
RUNTIME_CONFIGURATION_DIGEST = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            {
                "schema": "ComposeReleaseRuntimeV1",
                "services": [
                    {"role": "app", "config_hash": "config-app"},
                    {"role": "worker", "config_hash": "config-worker"},
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
)

PRODUCT = "dotmac_starter"
TARGET_REF = "observe:dotmac-starter"

DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "{PRODUCT}"
environment = "test"

[assembly]
manifest_path = "deploy/product-manifest.json"
manifest_digest = "{MANIFEST_B}"

[image]
reference = "ghcr.io/example/app@{IMAGE_B}"
source_revision = "{REV_B}"

[runtime_materials]
names = ["DATABASE_URL"]

[[roles]]
code = "app"
command = ["uvicorn", "app.main:app"]
replicas = 1
materials = ["DATABASE_URL"]
[roles.resources]
cpus = "1.0"
memory = "1g"
[roles.health.live]
path = "/health/live"
port = 8000
[roles.health.ready]
path = "/health/ready"
port = 8000

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["a003", "k012"]
compatibility = "online"

[ingress]
host = "example.dotmac.io"
# IngressPolicy.v1: the edge declares its own exposure and address family,
# and a public edge carries the approval LOCATOR (never an approval).
exposure = "public"
address_family = "ipv4"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"
[[ingress.routes]]
path = "/"
role = "app"
port = 8000

[rollout]
stability_window_seconds = 10
"""


def _application(
    *,
    source_revision: str = REV_B,
    image_digest: str = IMAGE_B,
    configuration_digest: str = PLAN_DIGEST,
    manifest_digest: str = MANIFEST_B,
) -> ApplicationReleaseIdentityV1:
    return ApplicationReleaseIdentityV1(
        image_digest=image_digest,
        source_revision=source_revision,
        configuration_digest=configuration_digest,
        manifest_digest=manifest_digest,
    )


def _controller() -> ControllerProvenanceV1:
    return ControllerProvenanceV1(
        distribution="dotmac-deployment-foundation",
        exact_version="0.1.0",
        artifact_sha256="sha256:" + "8" * 64,
        launcher_sha256="sha256:" + "9" * 64,
        source_revision=CONTROLLER_REV,
        release_run_id=60366,
        tag="dotmac-deployment-foundation-v0.1.0",
    )


def _authorizer() -> AuthorizerProvenanceV1:
    return AuthorizerProvenanceV1(
        repository="michaelayoade/dotmac_starter_mt",
        workflow_path=".github/workflows/deployment-release.yml",
        workflow_revision=WORKFLOW_REV,
        run_id=987654321,
    )


def _history_snapshot(
    *, from_revision: str | None, to_revision: str
) -> ApplicationHistorySnapshotV1:
    return ApplicationHistorySnapshotV1(
        server_origin="https://github.com",
        api_origin="https://api.github.com",
        repository_id=60366,
        repository="michaelayoade/dotmac_sub",
        object_format="sha1",
        from_revision=from_revision,
        to_revision=to_revision,
        bundle_name="dotmac-sub-application-history.bundle",
        bundle_size=4096,
        bundle_sha256=HISTORY_BUNDLE_SHA256,
    )


def _relation(
    kind: RevisionRelation,
    *,
    current: ApplicationReleaseIdentityV1 | None,
    candidate: ApplicationReleaseIdentityV1,
) -> RevisionEvidenceV1:
    from_revision = None if current is None else current.source_revision
    return RevisionEvidenceV1(
        relation=kind,
        from_revision=from_revision,
        to_revision=candidate.source_revision,
        history_snapshot_digest=_history_snapshot(
            from_revision=from_revision,
            to_revision=candidate.source_revision,
        ).snapshot_digest,
    )


def _envelope(
    *,
    current: ApplicationReleaseIdentityV1 | None,
    candidate: ApplicationReleaseIdentityV1,
    relation: RevisionRelation,
    plan_digest: str,
    execution_id: str = "deployment-run-60366",
) -> DeploymentExecutionEnvelopeV1:
    return DeploymentExecutionEnvelopeV1(
        execution_id=execution_id,
        product=PRODUCT,
        target_ref=TARGET_REF,
        plan_digest=plan_digest,
        required_controller=_controller(),
        authorizer=_authorizer(),
        candidate=candidate,
        expected_current=current,
        relation_evidence=_relation(relation, current=current, candidate=candidate),
        override=None,
    )


def _application_history_for(
    envelope: DeploymentExecutionEnvelopeV1,
) -> ApplicationHistorySnapshotV1:
    evidence = envelope.relation_evidence
    snapshot = _history_snapshot(
        from_revision=evidence.from_revision,
        to_revision=evidence.to_revision,
    )
    assert snapshot.snapshot_digest == evidence.history_snapshot_digest
    assert snapshot.snapshot_digest != envelope.authorizer.workflow_revision
    return snapshot


def _authorization_evidence_for(
    envelope: DeploymentExecutionEnvelopeV1,
) -> DeploymentAuthorizationEvidenceV1:
    authorizer = envelope.authorizer
    return DeploymentAuthorizationEvidenceV1(
        workflow_run=GitHubWorkflowRunV1(
            server_origin="https://github.com",
            api_origin="https://api.github.com",
            repository_id=60366,
            repository=authorizer.repository,
            head_repository_id=60366,
            head_repository=authorizer.repository,
            workflow_id=2785,
            workflow_path=authorizer.workflow_path,
            workflow_revision=authorizer.workflow_revision,
            workflow_blob_sha256=WORKFLOW_BLOB_SHA256,
            head_ref="refs/heads/main",
            referenced_workflows=(),
            run_id=authorizer.run_id,
            run_attempt=1,
            event="workflow_dispatch",
            head_sha=authorizer.workflow_revision,
            status="completed",
            conclusion="success",
        ),
        execution_envelope_digest=envelope.envelope_digest,
        controller_release_evidence_digest=(CONTROLLER_RELEASE_EVIDENCE_DIGEST),
        application_history=_application_history_for(envelope),
    )


def _state(
    current: ApplicationReleaseIdentityV1 | None = None,
    *,
    execution_id: str = "deployment-run-60366",
) -> ControllerStateV1:
    identity = current or _application()
    envelope = _envelope(
        current=identity,
        candidate=identity,
        relation=RevisionRelation.SAME,
        plan_digest=identity.configuration_digest,
        execution_id=execution_id,
    )
    authorization = _authorization_evidence_for(envelope)
    return ControllerStateV1(
        product=PRODUCT,
        target_ref=TARGET_REF,
        current=identity,
        execution_id=execution_id,
        execution_envelope_digest=envelope.envelope_digest,
        controller=_controller(),
        authorizer=_authorizer(),
        plan_digest=identity.configuration_digest,
        relation_evidence=_relation(
            RevisionRelation.SAME, current=identity, candidate=identity
        ),
        decision_reason_code="exact_replay",
        overridden=False,
        override_decision_ref=None,
        runtime_configuration_digest=RUNTIME_CONFIGURATION_DIGEST,
        authorization_evidence_digest=authorization.evidence_digest,
        controller_release_evidence_digest=(
            authorization.controller_release_evidence_digest
        ),
        application_history_snapshot_digest=(
            authorization.application_history.snapshot_digest
        ),
    )


def _completed(
    argv: Sequence[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        list(argv), returncode, stdout=stdout, stderr=stderr
    )


def _assert_lock_is_held(lock_directory: Path) -> None:
    with pytest.raises(LockUnavailableError):
        with deployment_lock(PRODUCT, directory=lock_directory):
            raise AssertionError("the controller lock was not held")


# ── strict atomic controller state ─────────────────────────────────────────


def test_controller_state_round_trips_every_decision_coordinate_privately(
    tmp_path: Path,
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    state = _state()

    written = store.write(state)

    assert store.load() == state
    assert json.loads(written.read_text(encoding="utf-8")) == state.to_document()
    assert stat.S_IMODE(written.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "mutation",
    ["missing", "unknown", "nested_unknown", "wrong_schema", "duplicate"],
)
def test_controller_state_is_strict_at_root_and_nested_levels(
    tmp_path: Path, mutation: str
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    document = _state().to_document()
    text = json.dumps(document, separators=(",", ":"))
    if mutation == "missing":
        document.pop("execution_envelope_digest")
        text = json.dumps(document)
    elif mutation == "unknown":
        document["trust_staged_tree"] = True
        text = json.dumps(document)
    elif mutation == "nested_unknown":
        current = document["current"]
        assert isinstance(current, dict)
        current["unverified"] = True
        text = json.dumps(document)
    elif mutation == "wrong_schema":
        document["schema"] = "DeploymentControllerState.v2"
        text = json.dumps(document)
    else:
        assert mutation == "duplicate"
        text = text.replace(
            '"execution_id":',
            '"execution_id":"shadow-run","execution_id":',
            1,
        )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(text, encoding="utf-8")

    with pytest.raises(SpecError):
        store.load()


def test_controller_state_refuses_a_symlink_instead_of_following_it(
    tmp_path: Path,
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    target = tmp_path / "attacker-state.json"
    target.write_text(json.dumps(_state().to_document()), encoding="utf-8")
    store.path.parent.mkdir(parents=True)
    store.path.symlink_to(target)

    with pytest.raises(PreconditionFailed, match="unreadable"):
        store.load()


def test_state_store_refuses_a_state_for_another_canonical_target(
    tmp_path: Path,
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    hostile = replace(_state(), target_ref="observe:other-target")

    with pytest.raises(SpecError, match="different product or target"):
        store.write(hostile)

    assert not store.path.exists()


def test_failed_atomic_replace_leaves_no_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    temporary_paths: list[Path] = []

    def refuse_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        temporary_paths.append(Path(source))
        assert Path(source).exists()
        assert Path(target) == store.path
        raise OSError("injected atomic replacement failure")

    monkeypatch.setattr(controller_module.os, "replace", refuse_replace)

    with pytest.raises(OSError, match="injected atomic replacement failure"):
        store.write(_state())

    assert temporary_paths and not temporary_paths[0].exists()
    assert not store.path.exists()


def test_controller_state_fsyncs_parent_on_first_directory_creation_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    events: list[str] = []
    real_fsync = os.fsync
    real_open = os.open
    real_replace = os.replace
    opened_directories: dict[int, Path] = {}

    def recording_open(
        path: str | os.PathLike[str],
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        resolved = Path(path).resolve()
        if resolved.is_dir():
            opened_directories[descriptor] = resolved
        return descriptor

    def recording_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append(f"directory-fsync:{opened_directories[descriptor]}")
        else:
            events.append("file-fsync")
        real_fsync(descriptor)

    def recording_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(controller_module.os, "open", recording_open)
    monkeypatch.setattr(controller_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(controller_module.os, "replace", recording_replace)

    store.write(_state())

    assert events == [
        f"directory-fsync:{store.directory.parent}",
        "file-fsync",
        "replace",
        f"directory-fsync:{store.directory}",
    ]


def test_controller_state_refuses_success_when_parent_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    real_fsync = os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(controller_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        store.write(_state())


# ── runtime observation stays independent of controller state ──────────────


class DockerRunner:
    def __init__(
        self,
        *,
        containers: tuple[str, ...],
        source_revision: str = REV_B,
        image_digest: str = IMAGE_B,
        classified: bool = False,
    ) -> None:
        self.containers = containers
        self.source_revision = source_revision
        self.image_digest = image_digest
        self.classified = classified

    def __call__(
        self, argv: Sequence[str], timeout_seconds: int
    ) -> subprocess.CompletedProcess[str]:
        arguments = list(argv)
        assert timeout_seconds == 30
        if arguments[1] == "ps":
            return _completed(
                arguments, stdout="".join(f"{item}\n" for item in self.containers)
            )
        if arguments[1] == "inspect":
            container_id = arguments[-1]
            labels: dict[str, str] = {}
            if self.classified:
                roster = dict.fromkeys(self.containers, 1)
                labels = {
                    "com.docker.compose.config-hash": f"config-{container_id}",
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.service": container_id,
                    "io.dotmac.deployment.configuration.digest": PLAN_DIGEST,
                    "io.dotmac.deployment.identity.schema": (
                        "ApplicationReleaseIdentityV1"
                    ),
                    "io.dotmac.deployment.manifest.digest": MANIFEST_B,
                    "io.dotmac.deployment.product": PRODUCT,
                    "io.dotmac.deployment.release.roster": json.dumps(
                        roster, sort_keys=True, separators=(",", ":")
                    ),
                    "io.dotmac.deployment.role": container_id,
                    "io.dotmac.deployment.service.kind": "release",
                }
            return _completed(
                arguments,
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:runtime-image-id",
                            "State": {"Running": True},
                            "Config": {
                                "Image": f"registry.invalid/app@{self.image_digest}",
                                "Labels": labels,
                            },
                        }
                    ]
                ),
            )
        assert arguments[1:3] == ["image", "inspect"]
        return _completed(
            arguments,
            stdout=json.dumps(
                [
                    {
                        "RepoDigests": [f"registry.invalid/app@{self.image_digest}"],
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": (
                                    self.source_revision
                                ),
                                "org.dotmac.product.manifest.digest": MANIFEST_B,
                            }
                        },
                    }
                ]
            ),
        )


def _docker_observer(
    store: ControllerStateStore, runner: DockerRunner
) -> DockerCurrentReleaseObserver:
    return DockerCurrentReleaseObserver(
        docker_binary=Path("/usr/bin/docker"),
        product=PRODUCT,
        state_store=store,
        runner=runner,
    )


def test_empty_state_and_runtime_is_first_install(tmp_path: Path) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )

    observation = _docker_observer(store, DockerRunner(containers=())).observe(
        expected_current=None
    )

    assert observation == CurrentReleaseObservation(None)


def test_controller_state_and_runtime_must_agree(tmp_path: Path) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    state = _state()
    store.write(state)

    observation = _docker_observer(
        store, DockerRunner(containers=("app", "worker"), classified=True)
    ).observe(expected_current=state.current)

    assert observation == CurrentReleaseObservation(
        state.current,
        runtime_configuration_digest=RUNTIME_CONFIGURATION_DIGEST,
    )


def test_runtime_without_controller_state_forces_unprovable(tmp_path: Path) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    expected = _application()

    observation = _docker_observer(store, DockerRunner(containers=("app",))).observe(
        expected_current=expected
    )

    assert observation == CurrentReleaseObservation(expected, relation_unprovable=True)


@pytest.mark.parametrize(
    ("source_revision", "image_digest"),
    [(REV_C, IMAGE_B), (REV_B, IMAGE_C)],
)
def test_runtime_state_mismatch_refuses(
    tmp_path: Path, source_revision: str, image_digest: str
) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    state = _state()
    store.write(state)

    with pytest.raises(PreconditionFailed, match="disagree"):
        _docker_observer(
            store,
            DockerRunner(
                containers=("app",),
                source_revision=source_revision,
                image_digest=image_digest,
                classified=True,
            ),
        ).observe(expected_current=state.current)


def test_post_effect_observation_refuses_wrong_candidate_bytes(tmp_path: Path) -> None:
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )

    with pytest.raises(PreconditionFailed, match="do not match the candidate"):
        _docker_observer(
            store,
            DockerRunner(containers=("app",), image_digest=IMAGE_C, classified=True),
        ).observe_completed(expected=_application())


# ── canonical plan execution under one controller lock ────────────────────


class RecordingObserver:
    def __init__(
        self,
        current: ApplicationReleaseIdentityV1 | None,
        *,
        events: list[str],
        lock_directory: Path,
        post_failure: bool = False,
    ) -> None:
        self.current = current
        self.events = events
        self.lock_directory = lock_directory
        self.post_failure = post_failure

    def observe(
        self, *, expected_current: ApplicationReleaseIdentityV1 | None
    ) -> CurrentReleaseObservation:
        assert expected_current == self.current
        _assert_lock_is_held(self.lock_directory)
        self.events.append("observe")
        return CurrentReleaseObservation(self.current)

    def observe_completed(
        self,
        *,
        expected: ApplicationReleaseIdentityV1,
        expected_roster: dict[str, int] | None = None,
        expected_compose_hashes: dict[str, str] | None = None,
    ) -> CurrentReleaseObservation:
        _assert_lock_is_held(self.lock_directory)
        self.events.append("observe_completed")
        if expected_roster is not None or expected_compose_hashes is not None:
            assert expected_roster == {"app": 1}
            assert expected_compose_hashes == {"app": "canonical-config-app"}
        if self.post_failure:
            raise PreconditionFailed("injected post-effect runtime mismatch")
        return CurrentReleaseObservation(
            expected,
            runtime_configuration_digest=RUNTIME_CONFIGURATION_DIGEST,
        )

    def expected_compose_hashes(
        self,
        *,
        rendered_compose: str,
        project_directory: Path,
        env_file: Path,
        roles: Sequence[str],
    ) -> dict[str, str]:
        _assert_lock_is_held(self.lock_directory)
        assert "io.dotmac.deployment.release.roster" in rendered_compose
        assert project_directory.is_absolute()
        assert env_file == project_directory / ".env"
        assert tuple(roles) == ("app",)
        self.events.append("expected_compose_hashes")
        return {"app": "canonical-config-app"}


class RecordingOracle:
    def __init__(
        self,
        evidence: RevisionEvidenceV1,
        *,
        events: list[str],
        lock_directory: Path,
    ) -> None:
        self.result = evidence
        self.events = events
        self.lock_directory = lock_directory

    def evidence(
        self,
        *,
        from_revision: str | None,
        to_revision: str,
        history_snapshot_digest: str,
    ) -> RevisionEvidenceV1:
        assert (from_revision, to_revision, history_snapshot_digest) == (
            self.result.from_revision,
            self.result.to_revision,
            self.result.history_snapshot_digest,
        )
        _assert_lock_is_held(self.lock_directory)
        self.events.append("oracle")
        return self.result


class RecordingStateStore(ControllerStateStore):
    def __init__(
        self,
        directory: Path,
        *,
        events: list[str],
        lock_directory: Path,
    ) -> None:
        super().__init__(directory.resolve(), product=PRODUCT, target_ref=TARGET_REF)
        self.events = events
        self.lock_directory = lock_directory
        self.writes: list[ControllerStateV1] = []

    def write(self, state: ControllerStateV1) -> Path:
        _assert_lock_is_held(self.lock_directory)
        self.events.append("state")
        self.writes.append(state)
        return super().write(state)


class RecordingExecutor:
    instances: ClassVar[list[RecordingExecutor]] = []
    events: ClassVar[list[str]] = []
    lock_directory: Path

    def __init__(self, spec: ProductDeploymentSpec, effects: object) -> None:
        self.spec = spec
        self.effects = effects
        self.plans: list[DeploymentPlan] = []
        self.__class__.instances.append(self)

    def run(self, plan: DeploymentPlan) -> DeploymentOutcome:
        _assert_lock_is_held(self.__class__.lock_directory)
        self.__class__.events.append("executor")
        self.plans.append(plan)
        return DeploymentOutcome(plan=plan, succeeded=True)


class RecordingEffects:
    def __init__(
        self,
        *,
        events: list[str],
        lock_directory: Path,
        manifest_digest: str = MANIFEST_B,
    ) -> None:
        self.events = events
        self.lock_directory = lock_directory
        self.observed_manifest_digest = manifest_digest

    def manifest_digest(self, manifest_path: str) -> str:
        _assert_lock_is_held(self.lock_directory)
        assert manifest_path == "deploy/product-manifest.json"
        self.events.append("manifest")
        return self.observed_manifest_digest


def _execution_case(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    ApplicationReleaseIdentityV1,
    ApplicationReleaseIdentityV1,
    DeploymentExecutionEnvelopeV1,
    DeploymentPlan,
]:
    staged = (tmp_path / "staged").resolve()
    descriptor = staged / "deploy" / "product.toml"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    current = _application(
        source_revision=REV_A,
        image_digest=IMAGE_A,
        configuration_digest=CONFIG_A,
        manifest_digest=MANIFEST_A,
    )
    spec = ProductDeploymentSpec.loads(DESCRIPTOR)
    plan = build_plan(spec, previous_image=current.image_digest)
    plan_digest = deployment_plan_digest(plan)
    candidate = _application(configuration_digest=digest_file(descriptor))
    envelope = _envelope(
        current=current,
        candidate=candidate,
        relation=RevisionRelation.FORWARD,
        plan_digest=plan_digest,
    )
    return staged, descriptor, current, candidate, envelope, plan


def _install_recording_executor(
    monkeypatch: pytest.MonkeyPatch, *, events: list[str], lock_directory: Path
) -> None:
    RecordingExecutor.instances = []
    RecordingExecutor.events = events
    RecordingExecutor.lock_directory = lock_directory
    monkeypatch.setattr(controller_module, "Executor", RecordingExecutor)


def test_controller_builds_and_executes_only_the_canonical_plan_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, descriptor, current, candidate, envelope, expected_plan = _execution_case(
        tmp_path
    )
    lock_directory = (tmp_path / "locks").resolve()
    events: list[str] = []
    _install_recording_executor(
        monkeypatch, events=events, lock_directory=lock_directory
    )
    store = RecordingStateStore(
        tmp_path / "state", events=events, lock_directory=lock_directory
    )
    sentinel_effects = RecordingEffects(
        events=events,
        lock_directory=lock_directory,
    )

    def effects_factory(
        spec: object, application_root: Path, evidence_root: Path
    ) -> object:
        _assert_lock_is_held(lock_directory)
        assert isinstance(spec, ProductDeploymentSpec)
        assert application_root == staged
        assert evidence_root == store.directory
        events.append("effects_factory")
        return sentinel_effects

    result = execute_authorized(
        envelope=envelope,
        descriptor_path=descriptor,
        actual_controller=_controller(),
        actual_authorizer=_authorizer(),
        authorization_evidence=_authorization_evidence_for(envelope),
        revision_oracle=RecordingOracle(
            envelope.relation_evidence,
            events=events,
            lock_directory=lock_directory,
        ),
        observer=RecordingObserver(
            current, events=events, lock_directory=lock_directory
        ),
        state_store=store,
        staged_application_root=staged,
        lock_directory=lock_directory,
        effects_factory=effects_factory,
    )

    assert result.decision.allowed is True
    assert result.outcome is not None and result.outcome.succeeded is True
    assert events == [
        "observe",
        "oracle",
        "effects_factory",
        "executor",
        "manifest",
        "expected_compose_hashes",
        "observe_completed",
        "state",
    ]
    executor = RecordingExecutor.instances[0]
    assert executor.effects is sentinel_effects
    assert executor.plans == [expected_plan]
    assert deployment_plan_document(executor.plans[0]) == deployment_plan_document(
        expected_plan
    )
    assert candidate.configuration_digest == digest_file(descriptor)
    assert candidate.configuration_digest != envelope.plan_digest
    assert store.writes[0].current == candidate
    assert store.writes[0].plan_digest == envelope.plan_digest
    assert store.writes[0].relation_evidence == envelope.relation_evidence
    assert store.writes[0].decision_reason_code == "forward"
    with deployment_lock(PRODUCT, directory=lock_directory):
        pass


def test_no_staged_action_or_prebuilt_plan_can_bypass_canonical_construction() -> None:
    parameters = inspect.signature(execute_authorized).parameters

    assert "descriptor_path" in parameters
    assert "effects_factory" in parameters
    assert "spec" not in parameters
    assert "plan_path" not in parameters
    assert "action" not in parameters
    assert "action_runner" not in parameters


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("candidate", "candidate_identity"),
        ("plan", "plan_digest"),
        ("controller", "controller_identity"),
        ("authorizer", "authorizer_identity"),
    ],
)
def test_each_runtime_binding_mutation_refuses_before_effects(
    tmp_path: Path, mutation: str, blocker: str
) -> None:
    staged, descriptor, current, candidate, envelope, _ = _execution_case(tmp_path)
    actual_controller = _controller()
    actual_authorizer = _authorizer()
    if mutation == "candidate":
        envelope = replace(envelope, candidate=replace(candidate, image_digest=IMAGE_C))
    elif mutation == "plan":
        envelope = replace(envelope, plan_digest=PLAN_DIGEST)
    elif mutation == "controller":
        actual_controller = replace(
            actual_controller, artifact_sha256="sha256:" + "9" * 64
        )
    else:
        assert mutation == "authorizer"
        actual_authorizer = replace(actual_authorizer, run_id=987654322)
    lock_directory = (tmp_path / "locks").resolve()
    effects_calls: list[object] = []
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )

    result = execute_authorized(
        envelope=envelope,
        descriptor_path=descriptor,
        actual_controller=actual_controller,
        actual_authorizer=actual_authorizer,
        authorization_evidence=_authorization_evidence_for(envelope),
        revision_oracle=RecordingOracle(
            envelope.relation_evidence,
            events=[],
            lock_directory=lock_directory,
        ),
        observer=RecordingObserver(current, events=[], lock_directory=lock_directory),
        state_store=store,
        staged_application_root=staged,
        lock_directory=lock_directory,
        effects_factory=lambda *args: effects_calls.append(args),
    )

    assert result.decision.allowed is False
    assert result.decision.reason_code == "binding_mismatch"
    assert blocker in result.decision.blockers
    assert effects_calls == []
    assert not store.path.exists()


def test_post_effect_observation_failure_never_records_controller_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, descriptor, current, _, envelope, _ = _execution_case(tmp_path)
    lock_directory = (tmp_path / "locks").resolve()
    events: list[str] = []
    _install_recording_executor(
        monkeypatch, events=events, lock_directory=lock_directory
    )
    store = RecordingStateStore(
        tmp_path / "state", events=events, lock_directory=lock_directory
    )

    with pytest.raises(PreconditionFailed, match="post-effect"):
        execute_authorized(
            envelope=envelope,
            descriptor_path=descriptor,
            actual_controller=_controller(),
            actual_authorizer=_authorizer(),
            authorization_evidence=_authorization_evidence_for(envelope),
            revision_oracle=RecordingOracle(
                envelope.relation_evidence,
                events=events,
                lock_directory=lock_directory,
            ),
            observer=RecordingObserver(
                current,
                events=events,
                lock_directory=lock_directory,
                post_failure=True,
            ),
            state_store=store,
            staged_application_root=staged,
            lock_directory=lock_directory,
            effects_factory=lambda *args: RecordingEffects(
                events=events,
                lock_directory=lock_directory,
            ),
        )

    assert "executor" in events
    assert events[-1] == "observe_completed"
    assert store.writes == []
    assert not store.path.exists()


def test_post_effect_manifest_mutation_refuses_before_runtime_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged, descriptor, current, _, envelope, _ = _execution_case(tmp_path)
    lock_directory = (tmp_path / "locks").resolve()
    events: list[str] = []
    _install_recording_executor(
        monkeypatch, events=events, lock_directory=lock_directory
    )
    store = RecordingStateStore(
        tmp_path / "state", events=events, lock_directory=lock_directory
    )

    with pytest.raises(PreconditionFailed, match="product manifest changed"):
        execute_authorized(
            envelope=envelope,
            descriptor_path=descriptor,
            actual_controller=_controller(),
            actual_authorizer=_authorizer(),
            authorization_evidence=_authorization_evidence_for(envelope),
            revision_oracle=RecordingOracle(
                envelope.relation_evidence,
                events=events,
                lock_directory=lock_directory,
            ),
            observer=RecordingObserver(
                current,
                events=events,
                lock_directory=lock_directory,
            ),
            state_store=store,
            staged_application_root=staged,
            lock_directory=lock_directory,
            effects_factory=lambda *args: RecordingEffects(
                events=events,
                lock_directory=lock_directory,
                manifest_digest=MANIFEST_C,
            ),
        )

    assert events[-2:] == ["executor", "manifest"]
    assert "expected_compose_hashes" not in events
    assert "observe_completed" not in events
    assert store.writes == []
    assert not store.path.exists()


def test_recorded_execution_replays_without_running_effects(tmp_path: Path) -> None:
    staged, descriptor, _, candidate, envelope, _ = _execution_case(tmp_path)
    lock_directory = (tmp_path / "locks").resolve()
    events: list[str] = []
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    store.write(
        ControllerStateV1(
            product=PRODUCT,
            target_ref=TARGET_REF,
            current=candidate,
            execution_id=envelope.execution_id,
            execution_envelope_digest=envelope.envelope_digest,
            controller=_controller(),
            authorizer=_authorizer(),
            plan_digest=envelope.plan_digest,
            relation_evidence=envelope.relation_evidence,
            decision_reason_code="forward",
            overridden=False,
            override_decision_ref=None,
            runtime_configuration_digest=RUNTIME_CONFIGURATION_DIGEST,
            authorization_evidence_digest=(
                _authorization_evidence_for(envelope).evidence_digest
            ),
            controller_release_evidence_digest=(
                _authorization_evidence_for(envelope).controller_release_evidence_digest
            ),
            application_history_snapshot_digest=(
                _application_history_for(envelope).snapshot_digest
            ),
        )
    )
    descriptor.write_text("hostile old staged descriptor", encoding="utf-8")
    effects_calls: list[object] = []

    result = execute_authorized(
        envelope=envelope,
        descriptor_path=descriptor,
        actual_controller=_controller(),
        actual_authorizer=_authorizer(),
        authorization_evidence=_authorization_evidence_for(envelope),
        revision_oracle=object(),
        observer=RecordingObserver(
            candidate, events=events, lock_directory=lock_directory
        ),
        state_store=store,
        staged_application_root=staged,
        lock_directory=lock_directory,
        effects_factory=lambda *args: effects_calls.append(args),
    )

    assert result.decision.reason_code == "execution_replay"
    assert result.outcome is None
    assert result.state_path == store.path
    assert effects_calls == []
    assert events == ["observe_completed"]


def test_same_execution_id_with_altered_envelope_is_not_a_replay(
    tmp_path: Path,
) -> None:
    staged, descriptor, _, candidate, envelope, _ = _execution_case(tmp_path)
    lock_directory = (tmp_path / "locks").resolve()
    store = ControllerStateStore(
        (tmp_path / "state").resolve(), product=PRODUCT, target_ref=TARGET_REF
    )
    store.write(
        ControllerStateV1(
            product=PRODUCT,
            target_ref=TARGET_REF,
            current=candidate,
            execution_id=envelope.execution_id,
            execution_envelope_digest=envelope.envelope_digest,
            controller=_controller(),
            authorizer=_authorizer(),
            plan_digest=envelope.plan_digest,
            relation_evidence=envelope.relation_evidence,
            decision_reason_code="forward",
            overridden=False,
            override_decision_ref=None,
            runtime_configuration_digest=RUNTIME_CONFIGURATION_DIGEST,
            authorization_evidence_digest=(
                _authorization_evidence_for(envelope).evidence_digest
            ),
            controller_release_evidence_digest=(
                _authorization_evidence_for(envelope).controller_release_evidence_digest
            ),
            application_history_snapshot_digest=(
                _application_history_for(envelope).snapshot_digest
            ),
        )
    )
    hostile = replace(
        envelope,
        candidate=replace(envelope.candidate, image_digest=IMAGE_C),
    )
    descriptor.write_text("hostile old staged descriptor", encoding="utf-8")
    events: list[str] = []

    with pytest.raises(PreconditionFailed):
        execute_authorized(
            envelope=hostile,
            descriptor_path=descriptor,
            actual_controller=_controller(),
            actual_authorizer=_authorizer(),
            authorization_evidence=_authorization_evidence_for(hostile),
            revision_oracle=object(),
            observer=RecordingObserver(
                candidate, events=events, lock_directory=lock_directory
            ),
            state_store=store,
            staged_application_root=staged,
            lock_directory=lock_directory,
            effects_factory=lambda *args: pytest.fail("effects must not run"),
        )

    assert events == []


@pytest.mark.parametrize(
    "violation", ["state", "descriptor", "relative_lock", "staged_lock"]
)
def test_controller_paths_cannot_be_redirected_through_the_staged_tree(
    tmp_path: Path, violation: str
) -> None:
    staged, descriptor, current, _, envelope, _ = _execution_case(tmp_path)
    state_directory = (tmp_path / "state").resolve()
    selected_descriptor = descriptor
    lock_directory = (tmp_path / "locks").resolve()
    if violation == "state":
        state_directory = staged / ".controller-state"
    elif violation == "descriptor":
        selected_descriptor = tmp_path / "outside-product.toml"
        selected_descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    elif violation == "relative_lock":
        lock_directory = Path("relative-locks")
    else:
        lock_directory = staged / ".controller-locks"
    store = ControllerStateStore(
        state_directory, product=PRODUCT, target_ref=TARGET_REF
    )

    with pytest.raises(SpecError):
        execute_authorized(
            envelope=envelope,
            descriptor_path=selected_descriptor,
            actual_controller=_controller(),
            actual_authorizer=_authorizer(),
            authorization_evidence=_authorization_evidence_for(envelope),
            revision_oracle=object(),
            observer=RecordingObserver(
                current, events=[], lock_directory=(tmp_path / "locks").resolve()
            ),
            state_store=store,
            staged_application_root=staged,
            lock_directory=lock_directory,
        )


# ── fixed roots and legacy path retirement ──────────────────────────────────


def test_execute_authorized_cli_has_fixed_controller_roots() -> None:
    assert CONTROLLER_STATE_ROOT.is_absolute()
    assert CONTROLLER_LOCK_ROOT.is_absolute()
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "--execution-envelope",
            "/authority/execution.json",
            "--staged-application-root",
            "/staged/application",
            "execute-authorized",
            "--authorizer-repo",
            "/authority/repository",
            "--application-history-repo",
            "/authority/application-history",
            "--git-bin",
            "/usr/bin/git",
            "--docker-bin",
            "/usr/bin/docker",
        ]
    )
    assert parsed.handler is cmd_execute_authorized
    assert not hasattr(parsed, "state_dir")
    assert not hasattr(parsed, "lock_dir")
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "execute-authorized",
                "--authorizer-repo",
                "/authority/repository",
                "--application-history-repo",
                "/authority/application-history",
                "--git-bin",
                "/usr/bin/git",
                "--docker-bin",
                "/usr/bin/docker",
                "--state-dir",
                "/attacker-controlled/state",
            ]
        )


def test_cli_wires_the_authorized_path_to_the_fixed_roots_by_ast() -> None:
    tree = ast.parse(inspect.getsource(cmd_execute_authorized))
    state_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ControllerStateStore"
    ]
    execute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_authorized"
    ]
    assert len(state_calls) == len(execute_calls) == 1
    assert isinstance(state_calls[0].args[0], ast.Name)
    assert state_calls[0].args[0].id == "CONTROLLER_STATE_ROOT"
    lock_keyword = next(
        item for item in execute_calls[0].keywords if item.arg == "lock_directory"
    )
    assert isinstance(lock_keyword.value, ast.Name)
    assert lock_keyword.value.id == "CONTROLLER_LOCK_ROOT"


def test_legacy_deploy_and_rollback_execute_paths_refuse(tmp_path: Path) -> None:
    descriptor = tmp_path / "product.toml"
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    parser = build_parser()
    deploy = parser.parse_args(["-f", str(descriptor), "deploy", "--execute"])
    rollback = parser.parse_args(
        [
            "-f",
            str(descriptor),
            "rollback",
            "--previous-image",
            f"ghcr.io/example/app@{IMAGE_A}",
            "--execute",
        ]
    )

    with pytest.raises(SpecError, match="direct deploy --execute is disabled"):
        cmd_deploy(deploy)
    with pytest.raises(SpecError, match="direct rollback --execute is disabled"):
        cmd_rollback(rollback)


def test_legacy_dry_run_guidance_names_only_the_authorized_launcher(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    descriptor = tmp_path / "product.toml"
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    parser = build_parser()

    deploy = parser.parse_args(["-f", str(descriptor), "deploy"])
    assert cmd_deploy(deploy) == 0
    deploy_output = capsys.readouterr().out
    assert "DeploymentExecutionEnvelope.v1" in deploy_output
    assert "Re-run with --execute" not in deploy_output

    rollback = parser.parse_args(
        ["-f", str(descriptor), "rollback", "--previous-image", IMAGE_A]
    )
    assert cmd_rollback(rollback) == 0
    rollback_output = capsys.readouterr().out
    assert "exact typed override" in rollback_output
    assert "Re-run with --execute" not in rollback_output


def test_legacy_deploy_dry_run_is_a_nonmutating_positive_control(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "product.toml"
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    args = build_parser().parse_args(["-f", str(descriptor), "deploy"])

    assert cmd_deploy(args) == cli_module.EXIT_OK
