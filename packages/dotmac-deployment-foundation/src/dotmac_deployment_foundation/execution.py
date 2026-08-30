"""Immutable execution authority for one deployment attempt.

``ProductDeploymentSpec.v1`` belongs to the product being deployed.  It
therefore cannot also name the controller that is allowed to judge that
product: an old application tree could name an old controller and downgrade
its own guard.  This module keeps those identities in a separate
``DeploymentExecutionEnvelope.v1`` supplied by the authorizing control path.

There are two deliberately separate layers here:

* identity and policy are pure data, so every refusal is executable without a
  host; and
* :class:`GitRevisionOracle` observes ancestry from an explicitly named,
  complete authorizing checkout with an absolute Git executable.  It never
  infers order from versions, timestamps, or lexical SHA ordering.

The independent launcher is the root of trust for controller bytes.  It hashes
the wheel before installation and invokes the installed package with isolated
Python.  The controller then binds that receipt, the authorizer, the candidate,
the observed current release, and the independently recomputed relation before
any deployment mutation is admissible.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import stat
import subprocess  # nosec B404 -- argv lists, shell=False; see below
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .authenticity import (
    ApplicationHistorySnapshotV1,
    DeploymentAuthorizationEvidenceV1,
)
from .errors import SpecError, UnknownFieldError, UnknownSchemaError

EXECUTION_SCHEMA: Final = "DeploymentExecutionEnvelope.v1"
CONTROLLER_DISTRIBUTION: Final = "dotmac-deployment-foundation"
LAUNCH_CONTEXT_SCHEMA: Final = "DeploymentControllerLaunchContext.v1"
CONTROLLER_PROVENANCE_ENVIRONMENT: Final = frozenset(
    {
        "DOTMAC_CONTROLLER_ARTIFACT_SHA256",
        "DOTMAC_CONTROLLER_LAUNCHER_SHA256",
        "DOTMAC_CONTROLLER_SOURCE_REVISION",
        "DOTMAC_CONTROLLER_RELEASE_RUN_ID",
        "DOTMAC_CONTROLLER_TAG",
    }
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_PRODUCT = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def strict_json_loads(text: str) -> object:
    """Parse JSON while refusing duplicate keys at every object depth."""

    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SpecError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_from_pairs)


def _canonical_digest(kind: str, document: dict[str, object]) -> str:
    payload = json.dumps(
        {"kind": kind, **document}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _require_text(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SpecError(f"{name} is not a valid {name.replace('_', ' ')}")
    return value


def _require_nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{name} must be a non-empty string")
    return value.strip()


def _require_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpecError(f"{name} must be a positive integer")
    return value


def _object(value: object, *, name: str, required: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SpecError(f"{name} must be an object")
    document = dict(value)
    keys = set(document)
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unknown {extra}")
        raise UnknownFieldError(f"{name}: {', '.join(details)}")
    return document


@dataclass(frozen=True, slots=True)
class ApplicationReleaseIdentityV1:
    """The exact application bytes and composition one release represents."""

    image_digest: str
    source_revision: str
    configuration_digest: str
    manifest_digest: str

    def __post_init__(self) -> None:
        _require_text(self.image_digest, "image_digest", _DIGEST)
        _require_text(self.source_revision, "source_revision", _REVISION)
        _require_text(self.configuration_digest, "configuration_digest", _DIGEST)
        _require_text(self.manifest_digest, "manifest_digest", _DIGEST)

    @property
    def identity_digest(self) -> str:
        return _canonical_digest("ApplicationReleaseIdentityV1", self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "image_digest": self.image_digest,
            "source_revision": self.source_revision,
            "configuration_digest": self.configuration_digest,
            "manifest_digest": self.manifest_digest,
        }

    @classmethod
    def from_document(cls, value: object) -> ApplicationReleaseIdentityV1:
        document = _object(
            value,
            name="application release identity",
            required=frozenset(
                {
                    "image_digest",
                    "source_revision",
                    "configuration_digest",
                    "manifest_digest",
                }
            ),
        )
        return cls(
            image_digest=_require_text(
                document["image_digest"], "image_digest", _DIGEST
            ),
            source_revision=_require_text(
                document["source_revision"], "source_revision", _REVISION
            ),
            configuration_digest=_require_text(
                document["configuration_digest"], "configuration_digest", _DIGEST
            ),
            manifest_digest=_require_text(
                document["manifest_digest"], "manifest_digest", _DIGEST
            ),
        )


@dataclass(frozen=True, slots=True)
class ControllerProvenanceV1:
    """The independently released controller artifact required for execution."""

    distribution: str
    exact_version: str
    artifact_sha256: str
    launcher_sha256: str
    source_revision: str
    release_run_id: int
    tag: str

    def __post_init__(self) -> None:
        if self.distribution != CONTROLLER_DISTRIBUTION:
            raise SpecError(
                f"controller distribution must be {CONTROLLER_DISTRIBUTION!r}"
            )
        _require_text(self.exact_version, "exact_version", _VERSION)
        _require_text(self.artifact_sha256, "artifact_sha256", _DIGEST)
        _require_text(self.launcher_sha256, "launcher_sha256", _DIGEST)
        _require_text(self.source_revision, "source_revision", _REVISION)
        _require_positive_int(self.release_run_id, "release_run_id")
        expected_tag = f"{CONTROLLER_DISTRIBUTION}-v{self.exact_version}"
        if self.tag != expected_tag:
            raise SpecError(
                f"controller tag must be {expected_tag!r}, got {self.tag!r}"
            )

    @property
    def identity_digest(self) -> str:
        return _canonical_digest("ControllerProvenanceV1", self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "exact_version": self.exact_version,
            "artifact_sha256": self.artifact_sha256,
            "launcher_sha256": self.launcher_sha256,
            "source_revision": self.source_revision,
            "release_run_id": self.release_run_id,
            "tag": self.tag,
        }

    @classmethod
    def from_document(cls, value: object) -> ControllerProvenanceV1:
        document = _object(
            value,
            name="controller provenance",
            required=frozenset(
                {
                    "distribution",
                    "exact_version",
                    "artifact_sha256",
                    "launcher_sha256",
                    "source_revision",
                    "release_run_id",
                    "tag",
                }
            ),
        )
        return cls(
            distribution=_require_nonempty(document["distribution"], "distribution"),
            exact_version=_require_text(
                document["exact_version"], "exact_version", _VERSION
            ),
            artifact_sha256=_require_text(
                document["artifact_sha256"], "artifact_sha256", _DIGEST
            ),
            launcher_sha256=_require_text(
                document["launcher_sha256"], "launcher_sha256", _DIGEST
            ),
            source_revision=_require_text(
                document["source_revision"], "source_revision", _REVISION
            ),
            release_run_id=_require_positive_int(
                document["release_run_id"], "release_run_id"
            ),
            tag=_require_nonempty(document["tag"], "tag"),
        )


@dataclass(frozen=True, slots=True)
class AuthorizerProvenanceV1:
    """The immutable workflow code and run that authorized this execution."""

    repository: str
    workflow_path: str
    workflow_revision: str
    run_id: int

    def __post_init__(self) -> None:
        _require_text(self.repository, "repository", _REPOSITORY)
        _require_text(self.workflow_path, "workflow_path", _WORKFLOW)
        _require_text(self.workflow_revision, "workflow_revision", _REVISION)
        _require_positive_int(self.run_id, "run_id")

    @property
    def identity_digest(self) -> str:
        return _canonical_digest("AuthorizerProvenanceV1", self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "workflow_path": self.workflow_path,
            "workflow_revision": self.workflow_revision,
            "run_id": self.run_id,
        }

    @classmethod
    def from_document(cls, value: object) -> AuthorizerProvenanceV1:
        document = _object(
            value,
            name="authorizer provenance",
            required=frozenset(
                {"repository", "workflow_path", "workflow_revision", "run_id"}
            ),
        )
        return cls(
            repository=_require_text(document["repository"], "repository", _REPOSITORY),
            workflow_path=_require_text(
                document["workflow_path"], "workflow_path", _WORKFLOW
            ),
            workflow_revision=_require_text(
                document["workflow_revision"], "workflow_revision", _REVISION
            ),
            run_id=_require_positive_int(document["run_id"], "run_id"),
        )


class RevisionRelation(str, Enum):
    FIRST_INSTALL = "first_install"
    SAME = "same"
    FORWARD = "forward"
    ROLLBACK = "rollback"
    DIVERGED = "diverged"
    UNPROVABLE = "unprovable"


@dataclass(frozen=True, slots=True)
class RevisionEvidenceV1:
    """An ancestry verdict tied to the exact history checkout that proved it."""

    relation: RevisionRelation
    from_revision: str | None
    to_revision: str
    history_snapshot_digest: str

    def __post_init__(self) -> None:
        if self.from_revision is not None:
            _require_text(self.from_revision, "from_revision", _REVISION)
        _require_text(self.to_revision, "to_revision", _REVISION)
        _require_text(self.history_snapshot_digest, "history_snapshot_digest", _DIGEST)
        if (
            self.relation is RevisionRelation.FIRST_INSTALL
            and self.from_revision is not None
        ):
            raise SpecError("first_install relation requires from_revision=null")
        if (
            self.relation is not RevisionRelation.FIRST_INSTALL
            and self.relation is not RevisionRelation.UNPROVABLE
            and self.from_revision is None
        ):
            raise SpecError(f"{self.relation.value} relation requires from_revision")
        if (
            self.relation is RevisionRelation.SAME
            and self.from_revision != self.to_revision
        ):
            raise SpecError("same relation requires identical from/to revisions")

    @property
    def identity_digest(self) -> str:
        return _canonical_digest("RevisionEvidenceV1", self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "relation": self.relation.value,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "history_snapshot_digest": self.history_snapshot_digest,
        }

    @classmethod
    def from_document(cls, value: object) -> RevisionEvidenceV1:
        document = _object(
            value,
            name="revision evidence",
            required=frozenset(
                {
                    "relation",
                    "from_revision",
                    "to_revision",
                    "history_snapshot_digest",
                }
            ),
        )
        try:
            relation = RevisionRelation(document["relation"])
        except (TypeError, ValueError) as exc:
            raise SpecError(
                f"unknown revision relation {document['relation']!r}"
            ) from exc
        raw_from = document["from_revision"]
        if raw_from is not None:
            raw_from = _require_text(raw_from, "from_revision", _REVISION)
        return cls(
            relation=relation,
            from_revision=raw_from,
            to_revision=_require_text(
                document["to_revision"], "to_revision", _REVISION
            ),
            history_snapshot_digest=_require_text(
                document["history_snapshot_digest"],
                "history_snapshot_digest",
                _DIGEST,
            ),
        )


_OVERRIDABLE = frozenset(
    {
        RevisionRelation.ROLLBACK,
        RevisionRelation.DIVERGED,
        RevisionRelation.UNPROVABLE,
    }
)


@dataclass(frozen=True, slots=True)
class TransitionOverrideV1:
    """One exact, typed exception; never a reusable generic ``--force``."""

    kind: RevisionRelation
    decision_ref: str
    execution_identity_digest: str
    plan_digest: str
    from_identity_digest: str
    to_identity_digest: str
    controller_identity_digest: str
    authorizer_identity_digest: str
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in _OVERRIDABLE:
            raise SpecError(
                "transition override kind must be rollback, diverged, or unprovable"
            )
        _require_nonempty(self.decision_ref, "decision_ref")
        _require_nonempty(self.reason, "reason")
        for name in (
            "execution_identity_digest",
            "plan_digest",
            "from_identity_digest",
            "to_identity_digest",
            "controller_identity_digest",
            "authorizer_identity_digest",
        ):
            _require_text(getattr(self, name), name, _DIGEST)

    def to_document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "decision_ref": self.decision_ref,
            "execution_identity_digest": self.execution_identity_digest,
            "plan_digest": self.plan_digest,
            "from_identity_digest": self.from_identity_digest,
            "to_identity_digest": self.to_identity_digest,
            "controller_identity_digest": self.controller_identity_digest,
            "authorizer_identity_digest": self.authorizer_identity_digest,
            "reason": self.reason,
        }

    @classmethod
    def from_document(cls, value: object) -> TransitionOverrideV1:
        required = frozenset(
            {
                "kind",
                "decision_ref",
                "execution_identity_digest",
                "plan_digest",
                "from_identity_digest",
                "to_identity_digest",
                "controller_identity_digest",
                "authorizer_identity_digest",
                "reason",
            }
        )
        document = _object(value, name="transition override", required=required)
        try:
            kind = RevisionRelation(document["kind"])
        except (TypeError, ValueError) as exc:
            raise SpecError(f"unknown override kind {document['kind']!r}") from exc
        return cls(
            kind=kind,
            decision_ref=_require_nonempty(document["decision_ref"], "decision_ref"),
            execution_identity_digest=_require_text(
                document["execution_identity_digest"],
                "execution_identity_digest",
                _DIGEST,
            ),
            plan_digest=_require_text(document["plan_digest"], "plan_digest", _DIGEST),
            from_identity_digest=_require_text(
                document["from_identity_digest"], "from_identity_digest", _DIGEST
            ),
            to_identity_digest=_require_text(
                document["to_identity_digest"], "to_identity_digest", _DIGEST
            ),
            controller_identity_digest=_require_text(
                document["controller_identity_digest"],
                "controller_identity_digest",
                _DIGEST,
            ),
            authorizer_identity_digest=_require_text(
                document["authorizer_identity_digest"],
                "authorizer_identity_digest",
                _DIGEST,
            ),
            reason=_require_nonempty(document["reason"], "reason"),
        )


@dataclass(frozen=True, slots=True)
class DeploymentExecutionEnvelopeV1:
    """The immutable authority and observed transition for one execution."""

    execution_id: str
    product: str
    target_ref: str
    plan_digest: str
    required_controller: ControllerProvenanceV1
    authorizer: AuthorizerProvenanceV1
    candidate: ApplicationReleaseIdentityV1
    expected_current: ApplicationReleaseIdentityV1 | None
    relation_evidence: RevisionEvidenceV1
    override: TransitionOverrideV1 | None

    def __post_init__(self) -> None:
        _require_text(self.execution_id, "execution_id", _REFERENCE)
        _require_text(self.product, "product", _PRODUCT)
        _require_text(self.target_ref, "target_ref", _REFERENCE)
        _require_text(self.plan_digest, "plan_digest", _DIGEST)

    @classmethod
    def loads(
        cls, text: str, *, source: str = "<string>"
    ) -> DeploymentExecutionEnvelopeV1:
        try:
            raw = strict_json_loads(text)
        except json.JSONDecodeError as exc:
            raise SpecError(f"not valid JSON: {exc}", where=source) from exc
        required = frozenset(
            {
                "schema",
                "execution_id",
                "product",
                "target_ref",
                "plan_digest",
                "required_controller",
                "authorizer",
                "candidate",
                "expected_current",
                "relation_evidence",
                "override",
            }
        )
        try:
            document = _object(raw, name="execution envelope", required=required)
            if document["schema"] != EXECUTION_SCHEMA:
                raise UnknownSchemaError(
                    f"schema is {document['schema']!r}, this facility reads "
                    f"{EXECUTION_SCHEMA!r}"
                )
            raw_current = document["expected_current"]
            expected_current = (
                None
                if raw_current is None
                else ApplicationReleaseIdentityV1.from_document(raw_current)
            )
            raw_override = document["override"]
            override = (
                None
                if raw_override is None
                else TransitionOverrideV1.from_document(raw_override)
            )
            return cls(
                execution_id=_require_text(
                    document["execution_id"], "execution_id", _REFERENCE
                ),
                product=_require_text(document["product"], "product", _PRODUCT),
                target_ref=_require_text(
                    document["target_ref"], "target_ref", _REFERENCE
                ),
                plan_digest=_require_text(
                    document["plan_digest"], "plan_digest", _DIGEST
                ),
                required_controller=ControllerProvenanceV1.from_document(
                    document["required_controller"]
                ),
                authorizer=AuthorizerProvenanceV1.from_document(document["authorizer"]),
                candidate=ApplicationReleaseIdentityV1.from_document(
                    document["candidate"]
                ),
                expected_current=expected_current,
                relation_evidence=RevisionEvidenceV1.from_document(
                    document["relation_evidence"]
                ),
                override=override,
            )
        except SpecError as exc:
            if exc.where:
                raise
            raise type(exc)(str(exc), where=source) from exc

    @classmethod
    def load(cls, path: str | Path) -> DeploymentExecutionEnvelopeV1:
        path = Path(path)
        return cls.loads(path.read_text(encoding="utf-8"), source=str(path))

    def to_document(self) -> dict[str, object]:
        return {
            "schema": EXECUTION_SCHEMA,
            "execution_id": self.execution_id,
            "product": self.product,
            "target_ref": self.target_ref,
            "plan_digest": self.plan_digest,
            "required_controller": self.required_controller.to_document(),
            "authorizer": self.authorizer.to_document(),
            "candidate": self.candidate.to_document(),
            "expected_current": (
                None
                if self.expected_current is None
                else self.expected_current.to_document()
            ),
            "relation_evidence": self.relation_evidence.to_document(),
            "override": None if self.override is None else self.override.to_document(),
        }

    @property
    def execution_identity_digest(self) -> str:
        """Digest the exact target authorization, excluding its override.

        Excluding the override avoids a circular digest while still binding an
        override to the product, target and execution for which it was issued.
        """

        return _canonical_digest(
            "DeploymentExecutionAuthorizationV1",
            {
                "execution_id": self.execution_id,
                "product": self.product,
                "target_ref": self.target_ref,
                "plan_digest": self.plan_digest,
                "required_controller": self.required_controller.to_document(),
                "authorizer": self.authorizer.to_document(),
                "candidate": self.candidate.to_document(),
                "expected_current": (
                    None
                    if self.expected_current is None
                    else self.expected_current.to_document()
                ),
                "relation_evidence": self.relation_evidence.to_document(),
            },
        )

    @property
    def envelope_digest(self) -> str:
        """Digest the complete immutable envelope, including any override."""

        return _canonical_digest(
            "DeploymentExecutionEnvelopeV1",
            self.to_document(),
        )

    def dumps(self) -> str:
        return json.dumps(self.to_document(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    allowed: bool
    relation: RevisionRelation
    reason_code: str
    overridden: bool = False
    blockers: tuple[str, ...] = ()


def _override_blockers(
    envelope: DeploymentExecutionEnvelopeV1,
    *,
    actual_current: ApplicationReleaseIdentityV1,
    actual_relation: RevisionEvidenceV1,
) -> tuple[str, ...]:
    override = envelope.override
    if override is None:
        return ("override_missing",)
    checks = {
        "override_kind": override.kind == actual_relation.relation,
        "override_execution": (
            override.execution_identity_digest == envelope.execution_identity_digest
        ),
        "override_plan": override.plan_digest == envelope.plan_digest,
        "override_from": (
            override.from_identity_digest == actual_current.identity_digest
        ),
        "override_to": (
            override.to_identity_digest == envelope.candidate.identity_digest
        ),
        "override_controller": (
            override.controller_identity_digest
            == envelope.required_controller.identity_digest
        ),
        "override_authorizer": (
            override.authorizer_identity_digest == envelope.authorizer.identity_digest
        ),
    }
    return tuple(name for name, matched in checks.items() if not matched)


def decide_transition(
    envelope: DeploymentExecutionEnvelopeV1,
    *,
    actual_controller: ControllerProvenanceV1,
    actual_authorizer: AuthorizerProvenanceV1,
    actual_candidate: ApplicationReleaseIdentityV1,
    actual_current: ApplicationReleaseIdentityV1 | None,
    actual_relation: RevisionEvidenceV1,
    actual_plan_digest: str,
) -> TransitionDecision:
    """Bind every observed fact, then apply the source-order policy.

    Every mismatch is reported together.  This is operationally useful and
    also prevents an ``or`` accidentally becoming an ``and``: one wrong
    coordinate must be sufficient to refuse.
    """

    _require_text(actual_plan_digest, "actual_plan_digest", _DIGEST)
    blockers: list[str] = []
    if actual_controller != envelope.required_controller:
        blockers.append("controller_identity")
    if actual_authorizer != envelope.authorizer:
        blockers.append("authorizer_identity")
    if actual_candidate != envelope.candidate:
        blockers.append("candidate_identity")
    if actual_plan_digest != envelope.plan_digest:
        blockers.append("plan_digest")
    if actual_current != envelope.expected_current:
        blockers.append("current_identity")
    if actual_relation != envelope.relation_evidence:
        blockers.append("relation_evidence")
    if actual_relation.to_revision != actual_candidate.source_revision:
        blockers.append("relation_to_revision")
    if actual_current is None:
        if actual_relation.from_revision is not None:
            blockers.append("relation_from_revision")
    elif actual_relation.from_revision != actual_current.source_revision:
        blockers.append("relation_from_revision")
    if blockers:
        return TransitionDecision(
            False,
            actual_relation.relation,
            "binding_mismatch",
            blockers=tuple(blockers),
        )

    relation = actual_relation.relation
    if relation is RevisionRelation.FIRST_INSTALL:
        if actual_current is not None:
            return TransitionDecision(
                False,
                relation,
                "binding_mismatch",
                blockers=("first_install_has_current",),
            )
        if envelope.override is not None:
            return TransitionDecision(
                False,
                relation,
                "binding_mismatch",
                blockers=("unexpected_override",),
            )
        return TransitionDecision(True, relation, "first_install")

    if actual_current is None:
        return TransitionDecision(
            False,
            relation,
            "binding_mismatch",
            blockers=("current_identity_missing",),
        )

    if relation is RevisionRelation.SAME:
        if actual_current != actual_candidate:
            return TransitionDecision(
                False,
                relation,
                "rebuild_conflict",
                blockers=("same_revision_different_release_identity",),
            )
        if envelope.override is not None:
            return TransitionDecision(
                False,
                relation,
                "binding_mismatch",
                blockers=("unexpected_override",),
            )
        return TransitionDecision(True, relation, "exact_replay")

    if relation is RevisionRelation.FORWARD:
        if envelope.override is not None:
            return TransitionDecision(
                False,
                relation,
                "binding_mismatch",
                blockers=("unexpected_override",),
            )
        return TransitionDecision(True, relation, "forward")

    reason = {
        RevisionRelation.ROLLBACK: "rollback_refused",
        RevisionRelation.DIVERGED: "diverged_refused",
        RevisionRelation.UNPROVABLE: "unprovable_refused",
    }[relation]
    override_blockers = _override_blockers(
        envelope, actual_current=actual_current, actual_relation=actual_relation
    )
    if override_blockers:
        return TransitionDecision(
            False,
            relation,
            reason if envelope.override is None else "binding_mismatch",
            blockers=override_blockers,
        )
    return TransitionDecision(True, relation, "override", overridden=True)


def _controller_from_environment(
    *, staged_application_root: Path
) -> ControllerProvenanceV1:
    """Measure the isolated installed controller and bind its launcher receipt.

    The wheel SHA and release coordinates come from the independent launcher,
    which verifies them before this package is imported.  This function adds
    the second half: the imported module must be the files belonging to the
    installed distribution and must not live below the staged application.
    """

    try:
        distribution = importlib.metadata.distribution(CONTROLLER_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise SpecError(
            f"installed distribution {CONTROLLER_DISTRIBUTION!r} is absent"
        ) from exc
    package_root = Path(
        str(distribution.locate_file("dotmac_deployment_foundation"))
    ).resolve()
    module_root = Path(__file__).resolve().parent
    if module_root != package_root:
        raise SpecError(
            f"controller imported from {module_root}, but installed distribution "
            f"owns {package_root}. Refusing a shadowed controller"
        )
    staged_root = staged_application_root.resolve()
    if module_root.is_relative_to(staged_root):
        raise SpecError(
            f"controller import root {module_root} is inside staged application "
            f"{staged_root}"
        )

    def required(name: str) -> str:
        value = os.environ.get(name, "")
        if not value:
            raise SpecError(f"independent launcher did not supply {name}")
        return value

    release_run_id = _require_positive_int(
        _parse_environment_int(
            required("DOTMAC_CONTROLLER_RELEASE_RUN_ID"),
            "DOTMAC_CONTROLLER_RELEASE_RUN_ID",
        ),
        "release_run_id",
    )
    return ControllerProvenanceV1(
        distribution=CONTROLLER_DISTRIBUTION,
        exact_version=distribution.version,
        artifact_sha256=required("DOTMAC_CONTROLLER_ARTIFACT_SHA256"),
        launcher_sha256=required("DOTMAC_CONTROLLER_LAUNCHER_SHA256"),
        source_revision=required("DOTMAC_CONTROLLER_SOURCE_REVISION"),
        release_run_id=release_run_id,
        tag=required("DOTMAC_CONTROLLER_TAG"),
    )


def provenance_from_launch_context(
    file_descriptor: int, *, staged_application_root: Path
) -> tuple[
    ControllerProvenanceV1,
    AuthorizerProvenanceV1,
    DeploymentAuthorizationEvidenceV1,
]:
    """Read the exact launcher-verified provenance from one inherited FD."""

    if file_descriptor < 0:
        raise SpecError("launch context descriptor must be non-negative")
    try:
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SpecError("launch context descriptor must name a regular file")
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o400:
            raise SpecError(
                "launch context file must be root-owned with exact mode 0400"
            )
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(item) for item in chunks) > 1024 * 1024:
                raise SpecError("launch context exceeds one MiB")
        text = b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"launch context could not be read: {exc}") from exc
    document = _object(
        strict_json_loads(text),
        name="controller launch context",
        required=frozenset(
            {"schema", "controller", "authorizer", "authorization_evidence"}
        ),
    )
    if document["schema"] != LAUNCH_CONTEXT_SCHEMA:
        raise UnknownSchemaError(
            f"launch context schema is {document['schema']!r}, expected "
            f"{LAUNCH_CONTEXT_SCHEMA!r}"
        )
    expected_controller = ControllerProvenanceV1.from_document(document["controller"])
    authorizer = AuthorizerProvenanceV1.from_document(document["authorizer"])
    authorization_evidence = DeploymentAuthorizationEvidenceV1.from_document(
        document["authorization_evidence"]
    )
    signed_run = authorization_evidence.workflow_run
    signed_authorizer = AuthorizerProvenanceV1(
        repository=signed_run.repository,
        workflow_path=signed_run.workflow_path,
        workflow_revision=signed_run.workflow_revision,
        run_id=signed_run.run_id,
    )
    if authorizer != signed_authorizer:
        raise SpecError(
            "launch-context authorizer disagrees with signed workflow-run evidence"
        )
    measured_controller = _controller_from_environment(
        staged_application_root=staged_application_root
    )
    if measured_controller != expected_controller:
        raise SpecError(
            "installed controller does not match the sealed launcher context"
        )
    return measured_controller, authorizer, authorization_evidence


def scrub_controller_provenance_environment() -> None:
    """Remove launcher-only evidence before any product command can inherit it."""

    for name in CONTROLLER_PROVENANCE_ENVIRONMENT:
        os.environ.pop(name, None)


def _parse_environment_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise SpecError(f"independent launcher supplied a non-integer {name}") from exc


class GitRevisionOracle:
    """Classify two commits using one explicit, immutable history checkout."""

    def __init__(
        self,
        repository: Path,
        git_binary: Path,
        *,
        snapshot: ApplicationHistorySnapshotV1,
    ) -> None:
        if not git_binary.is_absolute():
            raise SpecError("git_binary must be an absolute path")
        repository = repository.resolve()
        git_binary = git_binary.resolve()
        self._repository = repository
        self._git_binary = git_binary
        self._snapshot = snapshot

    @property
    def git_binary(self) -> Path:
        return self._git_binary

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_REPLACE_REF_BASE": "refs/dotmac-disabled-replace/",
            "GIT_GRAFT_FILE": os.devnull,
        }
        try:
            return subprocess.run(  # noqa: S603  # nosec B603 -- verified absolute Git argv, shell=False
                [
                    str(self._git_binary),
                    "-C",
                    str(self._repository),
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return subprocess.CompletedProcess([], 128, "", "oracle unavailable")

    def _commit_exists(self, revision: str) -> bool:
        return self._run("cat-file", "-e", f"{revision}^{{commit}}").returncode == 0

    def evidence(
        self,
        *,
        from_revision: str | None,
        to_revision: str,
        history_snapshot_digest: str,
    ) -> RevisionEvidenceV1:
        if from_revision is not None:
            _require_text(from_revision, "from_revision", _REVISION)
        _require_text(to_revision, "to_revision", _REVISION)
        _require_text(history_snapshot_digest, "history_snapshot_digest", _DIGEST)
        if history_snapshot_digest != self._snapshot.snapshot_digest:
            raise SpecError(
                "application-history checkout was bound to a different signed snapshot"
            )
        if (
            from_revision != self._snapshot.from_revision
            or to_revision != self._snapshot.to_revision
        ):
            raise SpecError(
                "application-history snapshot does not name the authorized transition"
            )
        object_format = self._run("rev-parse", "--show-object-format")
        shallow = self._run("rev-parse", "--is-shallow-repository")
        git_directory_result = self._run("rev-parse", "--git-dir")
        replace_refs = self._run("for-each-ref", "--format=%(refname)", "refs/replace")
        git_directory = Path(git_directory_result.stdout.strip())
        if not git_directory.is_absolute():
            git_directory = self._repository / git_directory
        borrowed_objects = (git_directory / "objects" / "info" / "alternates").exists()
        legacy_grafts = (git_directory / "info" / "grafts").exists()
        if (
            object_format.returncode != 0
            or object_format.stdout.strip() != self._snapshot.object_format
            or shallow.returncode != 0
            or shallow.stdout.strip() != "false"
            or git_directory_result.returncode != 0
            or replace_refs.returncode != 0
            or bool(replace_refs.stdout.strip())
            or borrowed_objects
            or legacy_grafts
        ):
            return RevisionEvidenceV1(
                RevisionRelation.UNPROVABLE,
                from_revision,
                to_revision,
                history_snapshot_digest,
            )
        if not self._commit_exists(to_revision):
            return RevisionEvidenceV1(
                RevisionRelation.UNPROVABLE,
                from_revision,
                to_revision,
                history_snapshot_digest,
            )
        if from_revision is None:
            return RevisionEvidenceV1(
                RevisionRelation.FIRST_INSTALL,
                None,
                to_revision,
                history_snapshot_digest,
            )
        if not self._commit_exists(from_revision):
            return RevisionEvidenceV1(
                RevisionRelation.UNPROVABLE,
                from_revision,
                to_revision,
                history_snapshot_digest,
            )
        if from_revision == to_revision:
            return RevisionEvidenceV1(
                RevisionRelation.SAME,
                from_revision,
                to_revision,
                history_snapshot_digest,
            )

        forward = self._run(
            "merge-base", "--is-ancestor", from_revision, to_revision
        ).returncode
        if forward == 0:
            relation = RevisionRelation.FORWARD
        elif forward != 1:
            relation = RevisionRelation.UNPROVABLE
        else:
            reverse = self._run(
                "merge-base", "--is-ancestor", to_revision, from_revision
            ).returncode
            if reverse == 0:
                relation = RevisionRelation.ROLLBACK
            elif reverse == 1:
                relation = RevisionRelation.DIVERGED
            else:
                relation = RevisionRelation.UNPROVABLE
        return RevisionEvidenceV1(
            relation,
            from_revision,
            to_revision,
            history_snapshot_digest,
        )


__all__ = [
    "EXECUTION_SCHEMA",
    "LAUNCH_CONTEXT_SCHEMA",
    "ApplicationReleaseIdentityV1",
    "AuthorizerProvenanceV1",
    "ControllerProvenanceV1",
    "DeploymentExecutionEnvelopeV1",
    "GitRevisionOracle",
    "RevisionEvidenceV1",
    "RevisionRelation",
    "TransitionDecision",
    "TransitionOverrideV1",
    "decide_transition",
    "provenance_from_launch_context",
    "strict_json_loads",
]
