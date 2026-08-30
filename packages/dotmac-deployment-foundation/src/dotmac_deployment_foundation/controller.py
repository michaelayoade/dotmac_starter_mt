"""Independent host-side execution controller.

The application release supplies declarative topology and bounded commands
that become canonical typed-plan steps. It cannot supply a deployment action
or the code that decides whether a plan may run. This module is imported only
from the exact wheel verified by ``scripts/run_deployment_controller.py``.

The controller owns one uninterrupted critical section:

1. acquire the product deployment lock;
2. observe the current Docker release and the controller-owned state record;
3. recompute Git ancestry from the exact authorizer checkout;
4. bind controller, authorizer, candidate, current, plan and relation to the
   execution envelope;
5. run the Foundation-owned typed plan while the same lock remains held; and
6. atomically record the new current release only after a successful effect.

There is no generic force switch.  A pre-controller installation is
``unprovable`` and therefore needs the same exact typed override as any other
unprovable transition.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404 -- argv lists, shell=False; see below
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .authenticity import DeploymentAuthorizationEvidenceV1
from .engine.lock import deployment_lock
from .engine.plan import DeploymentPlan, build_plan
from .engine.run import DeploymentOutcome, Effects, Executor
from .errors import PreconditionFailed, SpecError, StepFailed
from .execution import (
    ApplicationReleaseIdentityV1,
    AuthorizerProvenanceV1,
    ControllerProvenanceV1,
    DeploymentExecutionEnvelopeV1,
    GitRevisionOracle,
    RevisionEvidenceV1,
    RevisionRelation,
    TransitionDecision,
    decide_transition,
    strict_json_loads,
)
from .spec import ProductDeploymentSpec

STATE_SCHEMA: Final = "DeploymentControllerState.v1"
SOURCE_REVISION_LABEL: Final = "org.opencontainers.image.revision"
IMAGE_MANIFEST_DIGEST_LABEL: Final = "org.dotmac.product.manifest.digest"
SERVICE_KIND_LABEL: Final = "io.dotmac.deployment.service.kind"
PRODUCT_LABEL: Final = "io.dotmac.deployment.product"
IDENTITY_SCHEMA_LABEL: Final = "io.dotmac.deployment.identity.schema"
ROLE_LABEL: Final = "io.dotmac.deployment.role"
CONFIGURATION_DIGEST_LABEL: Final = "io.dotmac.deployment.configuration.digest"
MANIFEST_DIGEST_LABEL: Final = "io.dotmac.deployment.manifest.digest"
ROSTER_LABEL: Final = "io.dotmac.deployment.release.roster"
COMPOSE_SERVICE_LABEL: Final = "com.docker.compose.service"
COMPOSE_CONFIG_HASH_LABEL: Final = "com.docker.compose.config-hash"
COMPOSE_ONEOFF_LABEL: Final = "com.docker.compose.oneoff"
CONTROLLER_STATE_ROOT: Final = Path("/var/lib/dotmac-deployment-controller")
CONTROLLER_LOCK_ROOT: Final = Path("/var/lock")
_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
EffectsFactory = Callable[[ProductDeploymentSpec, Path, Path], Effects]


def _default_runner(
    argv: Sequence[str], timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603 -- argv list, shell=False
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
    )


def digest_file(path: Path) -> str:
    """Hash exact deployment input bytes without parsing or normalising them."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SpecError(f"cannot hash deployment input {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def deployment_plan_document(plan: DeploymentPlan) -> dict[str, object]:
    """Canonical, complete execution-plan document frozen by an authorizer."""

    return {
        "schema": "DeploymentPlan.v1",
        "product": plan.product,
        "image": plan.image,
        "image_digest": plan.image_digest,
        "source_revision": plan.source_revision,
        "manifest_digest": plan.manifest_digest,
        "strategy": plan.strategy.value,
        "rollback_permitted": plan.rollback_permitted,
        "rollback_reason": plan.rollback_reason,
        "previous_image": plan.previous_image,
        "notes": list(plan.notes),
        "steps": [
            {
                "kind": step.kind.value,
                "description": step.description,
                "command": list(step.command),
                "timeout_seconds": step.timeout_seconds,
                "retries": step.retries,
                "target": step.target,
                "phase": step.phase.value,
            }
            for step in plan.steps
        ],
    }


def deployment_plan_digest(plan: DeploymentPlan) -> str:
    payload = json.dumps(
        deployment_plan_document(plan), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ControllerStateV1:
    """The last release this independent controller completed successfully."""

    product: str
    target_ref: str
    current: ApplicationReleaseIdentityV1
    execution_id: str
    execution_envelope_digest: str
    controller: ControllerProvenanceV1
    authorizer: AuthorizerProvenanceV1
    plan_digest: str
    relation_evidence: RevisionEvidenceV1
    decision_reason_code: str
    overridden: bool
    override_decision_ref: str | None
    runtime_configuration_digest: str
    authorization_evidence_digest: str
    controller_release_evidence_digest: str
    application_history_snapshot_digest: str

    def __post_init__(self) -> None:
        if not self.product or not self.target_ref or not self.execution_id:
            raise SpecError(
                "controller state product, target_ref and execution_id are required"
            )
        if not _DIGEST.fullmatch(self.execution_envelope_digest):
            raise SpecError(
                "controller state execution_envelope_digest must be a SHA-256 digest"
            )
        if not _DIGEST.fullmatch(self.plan_digest):
            raise SpecError("controller state plan_digest must be a SHA-256 digest")
        if not _DIGEST.fullmatch(self.runtime_configuration_digest):
            raise SpecError(
                "controller state runtime_configuration_digest must be a SHA-256 digest"
            )
        for field in (
            "authorization_evidence_digest",
            "controller_release_evidence_digest",
            "application_history_snapshot_digest",
        ):
            if not _DIGEST.fullmatch(getattr(self, field)):
                raise SpecError(f"controller state {field} must be a SHA-256 digest")
        if not self.decision_reason_code:
            raise SpecError("controller state decision_reason_code is required")
        if self.overridden != (self.override_decision_ref is not None):
            raise SpecError(
                "controller state override decision reference disagrees with overridden"
            )

    def to_document(self) -> dict[str, object]:
        return {
            "schema": STATE_SCHEMA,
            "product": self.product,
            "target_ref": self.target_ref,
            "current": self.current.to_document(),
            "execution_id": self.execution_id,
            "execution_envelope_digest": self.execution_envelope_digest,
            "controller": self.controller.to_document(),
            "authorizer": self.authorizer.to_document(),
            "plan_digest": self.plan_digest,
            "relation_evidence": self.relation_evidence.to_document(),
            "decision_reason_code": self.decision_reason_code,
            "overridden": self.overridden,
            "override_decision_ref": self.override_decision_ref,
            "runtime_configuration_digest": self.runtime_configuration_digest,
            "authorization_evidence_digest": self.authorization_evidence_digest,
            "controller_release_evidence_digest": (
                self.controller_release_evidence_digest
            ),
            "application_history_snapshot_digest": (
                self.application_history_snapshot_digest
            ),
        }

    @classmethod
    def from_document(cls, value: object) -> ControllerStateV1:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise SpecError("controller state must be an object")
        required = {
            "schema",
            "product",
            "target_ref",
            "current",
            "execution_id",
            "execution_envelope_digest",
            "controller",
            "authorizer",
            "plan_digest",
            "relation_evidence",
            "decision_reason_code",
            "overridden",
            "override_decision_ref",
            "runtime_configuration_digest",
            "authorization_evidence_digest",
            "controller_release_evidence_digest",
            "application_history_snapshot_digest",
        }
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required)
        if missing or unknown:
            raise SpecError(
                f"controller state fields differ: missing={missing}, unknown={unknown}"
            )
        if value["schema"] != STATE_SCHEMA:
            raise SpecError(
                f"controller state schema is {value['schema']!r}, expected "
                f"{STATE_SCHEMA!r}"
            )
        product = value["product"]
        target_ref = value["target_ref"]
        execution_id = value["execution_id"]
        execution_envelope_digest = value["execution_envelope_digest"]
        decision_reason_code = value["decision_reason_code"]
        overridden = value["overridden"]
        override_decision_ref = value["override_decision_ref"]
        if not isinstance(product, str) or not product:
            raise SpecError("controller state product must be a non-empty string")
        if not isinstance(target_ref, str) or not target_ref:
            raise SpecError("controller state target_ref must be a non-empty string")
        if not isinstance(execution_id, str) or not execution_id:
            raise SpecError("controller state execution_id must be a non-empty string")
        if not isinstance(execution_envelope_digest, str) or not _DIGEST.fullmatch(
            execution_envelope_digest
        ):
            raise SpecError(
                "controller state execution_envelope_digest must be a SHA-256 digest"
            )
        if not isinstance(decision_reason_code, str) or not decision_reason_code:
            raise SpecError(
                "controller state decision_reason_code must be a non-empty string"
            )
        if not isinstance(overridden, bool):
            raise SpecError("controller state overridden must be a boolean")
        if override_decision_ref is not None and (
            not isinstance(override_decision_ref, str) or not override_decision_ref
        ):
            raise SpecError(
                "controller state override_decision_ref must be null or non-empty"
            )
        if overridden != (override_decision_ref is not None):
            raise SpecError(
                "controller state override decision reference disagrees with overridden"
            )
        return cls(
            product=product,
            target_ref=target_ref,
            current=ApplicationReleaseIdentityV1.from_document(value["current"]),
            execution_id=execution_id,
            execution_envelope_digest=execution_envelope_digest,
            controller=ControllerProvenanceV1.from_document(value["controller"]),
            authorizer=AuthorizerProvenanceV1.from_document(value["authorizer"]),
            plan_digest=str(value["plan_digest"]),
            relation_evidence=RevisionEvidenceV1.from_document(
                value["relation_evidence"]
            ),
            decision_reason_code=decision_reason_code,
            overridden=overridden,
            override_decision_ref=override_decision_ref,
            runtime_configuration_digest=str(value["runtime_configuration_digest"]),
            authorization_evidence_digest=str(value["authorization_evidence_digest"]),
            controller_release_evidence_digest=str(
                value["controller_release_evidence_digest"]
            ),
            application_history_snapshot_digest=str(
                value["application_history_snapshot_digest"]
            ),
        )


class ControllerStateStore:
    """Atomic, controller-owned current-release evidence for one target."""

    def __init__(self, directory: Path, *, product: str, target_ref: str) -> None:
        if not directory.is_absolute():
            raise SpecError("controller state directory must be absolute")
        if directory.is_symlink():
            raise SpecError("controller state directory must not be a symlink")
        self.directory = directory.resolve()
        self.product = product
        self.target_ref = target_ref
        target_digest = hashlib.sha256(target_ref.encode("utf-8")).hexdigest()[:16]
        self.path = self.directory / f"{product}-{target_digest}.json"

    def load(self) -> ControllerStateV1 | None:
        if not self.path.exists():
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags)
        except (OSError, json.JSONDecodeError) as exc:
            raise PreconditionFailed(
                f"controller state {self.path} is unreadable: {exc}"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise PreconditionFailed(
                    f"controller state {self.path} must be a regular file"
                )
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise PreconditionFailed(
                    f"controller state {self.path} must be owned by the "
                    "controller user and mode 0600"
                )
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                document = strict_json_loads(handle.read())
        except (OSError, json.JSONDecodeError) as exc:
            raise PreconditionFailed(
                f"controller state {self.path} is unreadable: {exc}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        state = ControllerStateV1.from_document(document)
        if state.product != self.product or state.target_ref != self.target_ref:
            raise PreconditionFailed(
                "controller state product or target does not match its canonical path"
            )
        return state

    def ensure_directory(self) -> None:
        """Create the state directory durably, or verify the existing owner."""

        created = False
        try:
            os.mkdir(self.directory, mode=0o700)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise PreconditionFailed(
                f"controller state directory cannot be created: {exc}"
            ) from exc
        directory_stat = self.directory.stat()
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or directory_stat.st_mode & 0o022
        ):
            raise PreconditionFailed(
                "controller state directory must be owned by the controller user "
                "and not group/world writable"
            )
        if created:
            parent_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                parent_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                parent_flags |= os.O_NOFOLLOW
            parent_fd = os.open(self.directory.parent, parent_flags)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)

    def write(self, state: ControllerStateV1) -> Path:
        if state.product != self.product or state.target_ref != self.target_ref:
            raise SpecError(
                "refusing to write controller state under a different product or target"
            )
        self.ensure_directory()
        fd, temporary = tempfile.mkstemp(
            dir=str(self.directory), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.to_document(), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            directory_fd = os.open(self.directory, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return self.path


@dataclass(frozen=True, slots=True)
class CurrentReleaseObservation:
    identity: ApplicationReleaseIdentityV1 | None
    relation_unprovable: bool = False
    runtime_configuration_digest: str | None = None


@dataclass(frozen=True, slots=True)
class _ContainerObservation:
    container_id: str
    kind: str | None
    service: str
    oneoff: bool
    identity: ApplicationReleaseIdentityV1 | None
    role: str | None
    roster: tuple[tuple[str, int], ...]
    compose_config_hash: str | None


class DockerCurrentReleaseObserver:
    """Observe current bytes without importing or running the staged product."""

    def __init__(
        self,
        *,
        docker_binary: Path,
        product: str,
        state_store: ControllerStateStore,
        runner: Runner = _default_runner,
        timeout_seconds: int = 30,
    ) -> None:
        if not docker_binary.is_absolute():
            raise SpecError("docker_binary must be an absolute path")
        self._docker = docker_binary.resolve()
        self._product = product
        self._state_store = state_store
        self._runner = runner
        self._timeout = timeout_seconds

    @property
    def docker_binary(self) -> Path:
        return self._docker

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                [str(self._docker), *arguments],
                self._timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreconditionFailed(f"Docker observation failed: {exc}") from exc

    def _container_ids(self) -> tuple[str, ...]:
        result = self._run(
            "ps",
            "--filter",
            f"label=com.docker.compose.project={self._product}",
            "--format",
            "{{.ID}}",
        )
        if result.returncode != 0:
            raise PreconditionFailed(
                "Docker could not enumerate the current product containers: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        return tuple(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )

    def _image_identity(
        self,
        *,
        container_id: str,
        container: dict[str, object],
        require_manifest: bool = True,
    ) -> tuple[str, str, str]:
        config = container.get("Config")
        if not isinstance(config, dict):
            raise PreconditionFailed(
                f"current container {container_id} has no Docker Config object"
            )
        configured_image = str(config.get("Image", ""))
        image_id = str(container.get("Image", ""))
        if not configured_image or not image_id:
            raise PreconditionFailed(
                f"current container {container_id} has no attributable image reference"
            )
        if "@sha256:" in configured_image:
            configured_digest = configured_image.rsplit("@", 1)[1]
        elif _DIGEST.fullmatch(configured_image):
            configured_digest = configured_image
        else:
            raise PreconditionFailed(
                f"current release container {container_id} was not created from an "
                "exact digest reference"
            )
        image = self._run("image", "inspect", image_id)
        if image.returncode != 0:
            raise PreconditionFailed(
                f"Docker could not resolve the image for current container "
                f"{container_id}: {(image.stderr or image.stdout).strip()}"
            )
        try:
            image_document = json.loads(image.stdout)[0]
            repo_digests = {
                str(reference)
                for reference in (image_document.get("RepoDigests", []) or [])
            }
            image_labels = image_document.get("Config", {}).get("Labels", {}) or {}
        except (json.JSONDecodeError, IndexError, TypeError, AttributeError) as exc:
            raise PreconditionFailed(
                f"Docker returned malformed image data for {container_id}"
            ) from exc
        available_digests = {
            reference.rsplit("@", 1)[1]
            for reference in repo_digests
            if "@sha256:" in reference
        }
        exact_reference_missing = (
            "@sha256:" in configured_image and configured_image not in repo_digests
        )
        if configured_digest not in available_digests or exact_reference_missing:
            raise PreconditionFailed(
                f"current release container {container_id} names image "
                f"{configured_image!r}, absent from its inspected image "
                "repository digests"
            )
        revision = str(image_labels.get(SOURCE_REVISION_LABEL, ""))
        manifest_digest = str(image_labels.get(IMAGE_MANIFEST_DIGEST_LABEL, ""))
        if not revision:
            raise PreconditionFailed(
                f"current image for {container_id} has no {SOURCE_REVISION_LABEL} label"
            )
        if require_manifest and not manifest_digest:
            raise PreconditionFailed(
                f"current image for {container_id} has no "
                f"{IMAGE_MANIFEST_DIGEST_LABEL} label"
            )
        return revision, configured_digest, manifest_digest

    def _runtime_container(self, container_id: str) -> _ContainerObservation:
        result = self._run("inspect", container_id)
        if result.returncode != 0:
            raise PreconditionFailed(
                f"Docker could not inspect current container {container_id}: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        try:
            document = json.loads(result.stdout)
            container = document[0]
            labels = container.get("Config", {}).get("Labels", {}) or {}
        except (json.JSONDecodeError, IndexError, TypeError, AttributeError) as exc:
            raise PreconditionFailed(
                f"Docker returned malformed inspection data for {container_id}"
            ) from exc
        kind_value = labels.get(SERVICE_KIND_LABEL)
        kind = None if kind_value is None else str(kind_value)
        service = str(labels.get(COMPOSE_SERVICE_LABEL, ""))
        oneoff = str(labels.get(COMPOSE_ONEOFF_LABEL, "")).lower() == "true"
        if kind is None:
            return _ContainerObservation(
                container_id,
                None,
                service,
                oneoff,
                None,
                None,
                (),
                None,
            )
        if str(labels.get(PRODUCT_LABEL, "")) != self._product:
            raise PreconditionFailed(
                f"container {container_id} deployment product label does not match "
                f"{self._product!r}"
            )
        if kind not in {"release", "auxiliary", "migration"}:
            raise PreconditionFailed(
                f"container {container_id} has unknown deployment service kind {kind!r}"
            )
        if kind != "release":
            return _ContainerObservation(
                container_id,
                kind,
                service,
                oneoff,
                None,
                None,
                (),
                None,
            )
        if labels.get(IDENTITY_SCHEMA_LABEL) != "ApplicationReleaseIdentityV1":
            raise PreconditionFailed(
                f"release container {container_id} has an unknown identity schema"
            )
        role = str(labels.get(ROLE_LABEL, ""))
        if not role or service != role:
            raise PreconditionFailed(
                f"release container {container_id} role label disagrees with its "
                "Compose service"
            )
        try:
            roster_document = strict_json_loads(str(labels.get(ROSTER_LABEL, "")))
            if not isinstance(roster_document, dict) or not all(
                isinstance(key, str)
                and key
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count > 0
                for key, count in roster_document.items()
            ):
                raise ValueError(
                    "roster must map role names to positive replica counts"
                )
            roster = tuple(sorted(roster_document.items()))
        except (json.JSONDecodeError, SpecError, ValueError) as exc:
            raise PreconditionFailed(
                f"release container {container_id} has an invalid release roster"
            ) from exc
        revision, image_digest, image_manifest_digest = self._image_identity(
            container_id=container_id,
            container=container,
        )
        configuration_digest = str(labels.get(CONFIGURATION_DIGEST_LABEL, ""))
        manifest_digest = str(labels.get(MANIFEST_DIGEST_LABEL, ""))
        if manifest_digest != image_manifest_digest:
            raise PreconditionFailed(
                f"release container {container_id} manifest label disagrees with its "
                "inspected image"
            )
        identity = ApplicationReleaseIdentityV1(
            image_digest=image_digest,
            source_revision=revision,
            configuration_digest=configuration_digest,
            manifest_digest=manifest_digest,
        )
        compose_config_hash = str(labels.get(COMPOSE_CONFIG_HASH_LABEL, "")) or None
        if compose_config_hash is None:
            raise PreconditionFailed(
                f"release container {container_id} has no Compose configuration hash"
            )
        return _ContainerObservation(
            container_id,
            kind,
            service,
            oneoff,
            identity,
            role,
            roster,
            compose_config_hash,
        )

    @staticmethod
    def _runtime_configuration_digest(
        release: Sequence[_ContainerObservation],
    ) -> str:
        document = {
            "schema": "ComposeReleaseRuntimeV1",
            "services": [
                {
                    "role": item.role,
                    "config_hash": item.compose_config_hash,
                }
                for item in sorted(
                    release,
                    key=lambda value: (str(value.role), value.container_id),
                )
            ],
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    def expected_compose_hashes(
        self,
        *,
        rendered_compose: str,
        project_directory: Path,
        env_file: Path,
        roles: Sequence[str],
    ) -> dict[str, str]:
        """Ask Compose for hashes of a private canonical render.

        Container labels alone are self-consistent evidence: a hand-edited
        project can preserve an approved digest label. Compose's own canonical
        service hash binds the running container to the exact Foundation render.
        """

        if not roles:
            raise PreconditionFailed("a release must declare at least one role")
        self._state_store.ensure_directory()
        descriptor, temporary = tempfile.mkstemp(
            dir=str(self._state_store.directory),
            prefix=".expected-compose.",
            suffix=".yml",
        )
        path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered_compose)
                handle.flush()
                os.fsync(handle.fileno())
            result = self._run(
                "compose",
                "--project-name",
                self._product,
                "--project-directory",
                str(project_directory),
                "--env-file",
                str(env_file),
                "-f",
                str(path),
                "config",
                "--hash",
                *roles,
            )
        finally:
            path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise PreconditionFailed(
                "Docker Compose could not hash the canonical release configuration: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        hashes: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[0] in hashes or not parts[1]:
                raise PreconditionFailed(
                    "Docker Compose returned malformed or duplicate service hashes"
                )
            hashes[parts[0]] = parts[1]
        if set(hashes) != set(roles):
            raise PreconditionFailed(
                "Docker Compose did not hash the exact declared release-role roster"
            )
        return hashes

    def _classified_runtime(
        self,
        container_ids: Sequence[str] | None = None,
        *,
        expected_roster: dict[str, int] | None = None,
        expected_compose_hashes: dict[str, str] | None = None,
    ) -> CurrentReleaseObservation | None:
        selected = self._container_ids() if container_ids is None else container_ids
        observations = [self._runtime_container(item) for item in selected]
        if not observations:
            return CurrentReleaseObservation(None)
        classified = [item for item in observations if item.kind is not None]
        if not classified:
            return None
        if len(classified) != len(observations):
            raise PreconditionFailed(
                "the Compose project mixes classified and unclassified containers"
            )
        oneoffs = [item.container_id for item in observations if item.oneoff]
        if oneoffs:
            raise PreconditionFailed(
                "the Compose project is not at steady state; one-off containers "
                "remain: " + ", ".join(sorted(oneoffs))
            )
        migrations = [
            item.container_id for item in observations if item.kind == "migration"
        ]
        if migrations:
            raise PreconditionFailed(
                "a migration service is still running during steady-state observation"
            )
        release = [item for item in observations if item.kind == "release"]
        if not release:
            raise PreconditionFailed(
                "the Compose project has no release-bearing services"
            )
        identities = {item.identity for item in release}
        rosters = {item.roster for item in release}
        if len(identities) != 1 or None in identities:
            raise PreconditionFailed(
                "release-bearing services disagree on the four release identity "
                "coordinates"
            )
        if len(rosters) != 1:
            raise PreconditionFailed(
                "release-bearing services disagree on their role roster"
            )
        roster = dict(next(iter(rosters)))
        observed_counts: dict[str, int] = {}
        for item in release:
            assert item.role is not None
            observed_counts[item.role] = observed_counts.get(item.role, 0) + 1
        if observed_counts != roster:
            raise PreconditionFailed(
                f"release-bearing service quorum is {observed_counts}, expected "
                f"{roster}"
            )
        if expected_roster is not None and roster != expected_roster:
            raise PreconditionFailed(
                f"release-bearing service roster is {roster}, canonical descriptor "
                f"requires {expected_roster}"
            )
        if expected_compose_hashes is not None:
            observed_hashes: dict[str, set[str]] = {}
            for item in release:
                assert item.role is not None
                assert item.compose_config_hash is not None
                observed_hashes.setdefault(item.role, set()).add(
                    item.compose_config_hash
                )
            if any(len(values) != 1 for values in observed_hashes.values()):
                raise PreconditionFailed(
                    "replicas of one release role disagree on Compose configuration"
                )
            flattened = {
                role: next(iter(values)) for role, values in observed_hashes.items()
            }
            if flattened != expected_compose_hashes:
                raise PreconditionFailed(
                    "running release Compose hashes differ from the canonical render"
                )
        identity = next(iter(identities))
        assert identity is not None
        return CurrentReleaseObservation(
            identity,
            runtime_configuration_digest=self._runtime_configuration_digest(release),
        )

    def _legacy_matches(
        self, container_id: str, expected: ApplicationReleaseIdentityV1
    ) -> bool:
        result = self._run("inspect", container_id)
        try:
            container = json.loads(result.stdout)[0]
            revision, image_digest, _ = self._image_identity(
                container_id=container_id,
                container=container,
                require_manifest=False,
            )
        except (json.JSONDecodeError, IndexError, TypeError, AttributeError):
            return False
        except PreconditionFailed:
            return False
        return (
            revision == expected.source_revision
            and image_digest == expected.image_digest
        )

    def observe(
        self, *, expected_current: ApplicationReleaseIdentityV1 | None
    ) -> CurrentReleaseObservation:
        state = self._state_store.load()
        if state is not None and state.product != self._product:
            raise PreconditionFailed(
                f"controller state names product {state.product!r}, expected "
                f"{self._product!r}"
            )
        container_ids = self._container_ids()
        if not container_ids:
            if state is not None or expected_current is not None:
                raise PreconditionFailed(
                    "no running product containers exist, but the execution or "
                    "controller state claims a current release"
                )
            return CurrentReleaseObservation(None)

        classified = self._classified_runtime(container_ids)
        if classified is not None:
            if classified.identity is None:
                raise PreconditionFailed("classified runtime has no release identity")
            if state is not None and (
                classified.identity != state.current
                or classified.runtime_configuration_digest
                != state.runtime_configuration_digest
            ):
                raise PreconditionFailed(
                    "running services disagree with controller-owned release identity "
                    "or runtime configuration state"
                )
            if classified.identity != expected_current:
                raise PreconditionFailed(
                    "running services disagree with the execution's expected current "
                    "release identity"
                )
            return classified

        if state is not None:
            raise PreconditionFailed(
                "controller state exists but the running project lacks classified "
                "release identity labels"
            )
        if expected_current is None:
            raise PreconditionFailed(
                "running containers exist but the execution declares first install"
            )
        if not any(
            self._legacy_matches(container_id, expected_current)
            for container_id in container_ids
        ):
            raise PreconditionFailed(
                "running containers disagree with the execution's expected current "
                "source revision or image digest"
            )
        # The pre-controller runtime cannot independently prove the product
        # manifest. Keep the explicitly authorized identity, but force the Git
        # relation to UNPROVABLE so only an exact bootstrap override can pass.
        return CurrentReleaseObservation(expected_current, relation_unprovable=True)

    def observe_completed(
        self,
        *,
        expected: ApplicationReleaseIdentityV1,
        expected_roster: dict[str, int] | None = None,
        expected_compose_hashes: dict[str, str] | None = None,
    ) -> CurrentReleaseObservation:
        """Prove all four live release coordinates after the executor exits.

        Source and manifest are read from the inspected image object; image
        identity is the exact repository digest used to create the container;
        configuration and the independent Compose config hash are read from
        the running release-bearing service quorum. Controller state is
        intentionally ignored until this succeeds.
        """

        observed = self._classified_runtime(
            expected_roster=expected_roster,
            expected_compose_hashes=expected_compose_hashes,
        )
        if observed is None or observed.identity is None:
            raise PreconditionFailed(
                "the deployment reported success but the project has no complete "
                "classified release identity"
            )
        if observed.identity != expected:
            raise PreconditionFailed(
                "the deployment reported success but running services do not match "
                "all four candidate release identity coordinates"
            )
        return observed


@dataclass(frozen=True, slots=True)
class AuthorizedExecutionResult:
    decision: TransitionDecision
    outcome: DeploymentOutcome | None = None
    state_path: Path | None = None


def _candidate_identity(
    spec: ProductDeploymentSpec, *, configuration_digest: str
) -> ApplicationReleaseIdentityV1:
    return ApplicationReleaseIdentityV1(
        image_digest=spec.image_digest,
        source_revision=spec.source_revision,
        configuration_digest=configuration_digest,
        manifest_digest=spec.manifest_digest,
    )


def execute_authorized(
    *,
    envelope: DeploymentExecutionEnvelopeV1,
    descriptor_path: Path,
    actual_controller: ControllerProvenanceV1,
    actual_authorizer: AuthorizerProvenanceV1,
    authorization_evidence: DeploymentAuthorizationEvidenceV1,
    revision_oracle: GitRevisionOracle,
    observer: DockerCurrentReleaseObserver,
    state_store: ControllerStateStore,
    staged_application_root: Path,
    lock_directory: Path,
    effects_factory: EffectsFactory | None = None,
) -> AuthorizedExecutionResult:
    """Authorize and execute one Foundation-owned typed plan under one lock."""

    staged_root = staged_application_root.resolve()
    if not lock_directory.is_absolute():
        raise SpecError("controller lock directory must be absolute")
    lock_root = lock_directory.resolve()
    if lock_root == staged_root or lock_root.is_relative_to(staged_root):
        raise SpecError("controller lock directory must be outside staged application")
    if state_store.directory == staged_root or state_store.directory.is_relative_to(
        staged_root
    ):
        raise SpecError("controller state directory must be outside staged application")
    descriptor_path = descriptor_path.resolve()
    if not descriptor_path.is_relative_to(staged_root):
        raise SpecError("product descriptor must be inside the staged application")
    with deployment_lock(
        envelope.product,
        directory=lock_root,
        label=f"controller {envelope.execution_id}",
    ):
        recorded = state_store.load()
        if recorded is not None and recorded.execution_id == envelope.execution_id:
            replay_mismatches: list[str] = []
            if recorded.execution_envelope_digest != envelope.envelope_digest:
                replay_mismatches.append("execution_envelope_digest")
            if recorded.controller != actual_controller:
                replay_mismatches.append("controller_identity")
            if recorded.authorizer != actual_authorizer:
                replay_mismatches.append("authorizer_identity")
            if recorded.current != envelope.candidate:
                replay_mismatches.append("candidate_identity")
            if recorded.plan_digest != envelope.plan_digest:
                replay_mismatches.append("plan_digest")
            if (
                recorded.authorization_evidence_digest
                != authorization_evidence.evidence_digest
            ):
                replay_mismatches.append("authorization_evidence")
            if (
                recorded.controller_release_evidence_digest
                != authorization_evidence.controller_release_evidence_digest
            ):
                replay_mismatches.append("controller_release_evidence")
            if (
                recorded.application_history_snapshot_digest
                != authorization_evidence.application_history.snapshot_digest
            ):
                replay_mismatches.append("application_history_snapshot")
            if replay_mismatches:
                raise PreconditionFailed(
                    "execution_id was already recorded with different evidence: "
                    + ", ".join(replay_mismatches)
                )
            replay_observation = observer.observe_completed(expected=recorded.current)
            if (
                replay_observation.runtime_configuration_digest
                != recorded.runtime_configuration_digest
            ):
                raise PreconditionFailed(
                    "execution replay runtime configuration disagrees with "
                    "recorded state"
                )
            return AuthorizedExecutionResult(
                decision=TransitionDecision(
                    True,
                    recorded.relation_evidence.relation,
                    "execution_replay",
                    overridden=recorded.overridden,
                ),
                state_path=state_store.path,
            )

        # Read once under the lock. ProductDeploymentSpec.loads consumes these
        # exact bytes; a second pathname read cannot substitute a descriptor.
        try:
            descriptor_bytes = descriptor_path.read_bytes()
            descriptor_text = descriptor_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SpecError(f"cannot read product descriptor: {exc}") from exc
        spec = ProductDeploymentSpec.loads(descriptor_text, source=str(descriptor_path))
        if spec.product != envelope.product:
            raise SpecError(
                f"descriptor product {spec.product!r} does not match execution "
                f"product {envelope.product!r}"
            )
        observation = observer.observe(expected_current=envelope.expected_current)
        current = observation.identity
        plan = build_plan(
            spec,
            previous_image=("" if current is None else current.image_digest),
        )
        plan_digest = deployment_plan_digest(plan)
        configuration_digest = f"sha256:{hashlib.sha256(descriptor_bytes).hexdigest()}"
        candidate = _candidate_identity(spec, configuration_digest=configuration_digest)
        measured_relation = revision_oracle.evidence(
            from_revision=(None if current is None else current.source_revision),
            to_revision=candidate.source_revision,
            history_snapshot_digest=(
                authorization_evidence.application_history.snapshot_digest
            ),
        )
        if observation.relation_unprovable:
            relation = RevisionEvidenceV1(
                relation=RevisionRelation.UNPROVABLE,
                from_revision=(None if current is None else current.source_revision),
                to_revision=candidate.source_revision,
                history_snapshot_digest=(
                    authorization_evidence.application_history.snapshot_digest
                ),
            )
        else:
            relation = measured_relation
        decision = decide_transition(
            envelope,
            actual_controller=actual_controller,
            actual_authorizer=actual_authorizer,
            actual_candidate=candidate,
            actual_current=current,
            actual_relation=relation,
            actual_plan_digest=plan_digest,
        )
        if not decision.allowed:
            return AuthorizedExecutionResult(decision=decision)

        if effects_factory is None:
            from .providers.compose_host import ComposeHostEffects

            effects: Effects = ComposeHostEffects(
                spec,
                staged_root,
                docker_bin=str(observer.docker_binary),
                git_bin=str(revision_oracle.git_binary),
                candidate_identity=candidate,
                previous_identity=current,
                evidence_path=state_store.directory
                / f"{state_store.path.stem}-deployment-evidence.json",
            )
        else:
            effects = effects_factory(spec, staged_root, state_store.directory)
        outcome = Executor(spec, effects).run(plan)
        if not outcome.succeeded:
            failed = outcome.failed_step.value if outcome.failed_step else "unknown"
            raise StepFailed(failed, outcome.failure)
        post_manifest = effects.manifest_digest(spec.manifest_path)
        if post_manifest != candidate.manifest_digest:
            raise PreconditionFailed(
                "product manifest changed between the preflight and completed "
                "runtime observation"
            )
        from .render.compose import render_compose

        expected_roster = {
            role.code: role.replicas for role in spec.roles if role.replicas > 0
        }
        expected_compose_hashes = observer.expected_compose_hashes(
            rendered_compose=render_compose(spec, release_identity=candidate),
            project_directory=staged_root,
            env_file=staged_root / ".env",
            roles=tuple(sorted(expected_roster)),
        )
        completed = observer.observe_completed(
            expected=candidate,
            expected_roster=expected_roster,
            expected_compose_hashes=expected_compose_hashes,
        )
        if completed.runtime_configuration_digest is None:
            raise PreconditionFailed(
                "completed deployment has no observable runtime configuration digest"
            )

        state_path = state_store.write(
            ControllerStateV1(
                product=envelope.product,
                target_ref=envelope.target_ref,
                current=candidate,
                execution_id=envelope.execution_id,
                execution_envelope_digest=envelope.envelope_digest,
                controller=actual_controller,
                authorizer=actual_authorizer,
                plan_digest=plan_digest,
                relation_evidence=relation,
                decision_reason_code=decision.reason_code,
                overridden=decision.overridden,
                override_decision_ref=(
                    envelope.override.decision_ref
                    if decision.overridden and envelope.override is not None
                    else None
                ),
                runtime_configuration_digest=completed.runtime_configuration_digest,
                authorization_evidence_digest=(authorization_evidence.evidence_digest),
                controller_release_evidence_digest=(
                    authorization_evidence.controller_release_evidence_digest
                ),
                application_history_snapshot_digest=(
                    authorization_evidence.application_history.snapshot_digest
                ),
            )
        )
        return AuthorizedExecutionResult(
            decision=decision,
            outcome=outcome,
            state_path=state_path,
        )


__all__ = [
    "CONTROLLER_LOCK_ROOT",
    "CONTROLLER_STATE_ROOT",
    "STATE_SCHEMA",
    "AuthorizedExecutionResult",
    "ControllerStateStore",
    "ControllerStateV1",
    "CurrentReleaseObservation",
    "DockerCurrentReleaseObserver",
    "deployment_plan_digest",
    "deployment_plan_document",
    "digest_file",
    "execute_authorized",
]
