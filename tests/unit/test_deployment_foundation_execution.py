"""Adversarial canaries for deployment-controller provenance and anti-rollback.

These tests deliberately exercise the typed execution envelope independently of
``ProductDeploymentSpec``.  A product descriptor says what the application
needs; it cannot attest which controller bytes made the deployment decision,
which workflow authorised those bytes, or whether a candidate source revision
is an advance or a rollback.

Every refusal below has an adjacent healthy control.  That is load-bearing: a
test which only asserts that a hostile input is refused would also pass if the
decision engine refused every deployment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.authenticity import ApplicationHistorySnapshotV1
from dotmac_deployment_foundation.errors import (
    SpecError,
    UnknownFieldError,
    UnknownSchemaError,
)
from dotmac_deployment_foundation.execution import (
    ApplicationReleaseIdentityV1,
    AuthorizerProvenanceV1,
    ControllerProvenanceV1,
    DeploymentExecutionEnvelopeV1,
    GitRevisionOracle,
    RevisionEvidenceV1,
    RevisionRelation,
    TransitionDecision,
    TransitionOverrideV1,
    decide_transition,
    scrub_controller_provenance_environment,
)

REV_A = "1" * 40
REV_B = "2" * 40
REV_C = "3" * 40
REV_D = "4" * 40
CONTROLLER_REV = "5" * 40
WORKFLOW_REV = "6" * 40

IMAGE_A = "sha256:" + "a" * 64
IMAGE_B = "sha256:" + "b" * 64
IMAGE_C = "sha256:" + "c" * 64
MANIFEST_A = "sha256:" + "d" * 64
MANIFEST_B = "sha256:" + "e" * 64
MANIFEST_C = "sha256:" + "f" * 64
PLAN_DIGEST = "sha256:" + "7" * 64
CONFIG_A = "sha256:" + "0" * 64
CONFIG_B = PLAN_DIGEST
CONFIG_C = "sha256:" + "8" * 64
OTHER_DIGEST = "sha256:" + "8" * 64
HISTORY_BUNDLE_SHA256 = "sha256:" + "3" * 64


def _application(
    *,
    source_revision: str = REV_B,
    image_digest: str = IMAGE_B,
    configuration_digest: str = CONFIG_B,
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
        artifact_sha256="sha256:" + "9" * 64,
        launcher_sha256="sha256:" + "8" * 64,
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


def _case(
    relation: RevisionRelation,
) -> tuple[DeploymentExecutionEnvelopeV1, ApplicationReleaseIdentityV1 | None]:
    if relation is RevisionRelation.FIRST_INSTALL:
        current = None
        candidate = _application()
    elif relation is RevisionRelation.SAME:
        current = _application()
        candidate = current
    elif relation is RevisionRelation.FORWARD:
        current = _application(
            source_revision=REV_A,
            image_digest=IMAGE_A,
            configuration_digest=CONFIG_A,
            manifest_digest=MANIFEST_A,
        )
        candidate = _application()
    elif relation is RevisionRelation.ROLLBACK:
        current = _application()
        candidate = _application(
            source_revision=REV_A,
            image_digest=IMAGE_A,
            configuration_digest=CONFIG_A,
            manifest_digest=MANIFEST_A,
        )
    elif relation is RevisionRelation.DIVERGED:
        current = _application()
        candidate = _application(
            source_revision=REV_C,
            image_digest=IMAGE_C,
            configuration_digest=CONFIG_C,
            manifest_digest=MANIFEST_C,
        )
    else:
        assert relation is RevisionRelation.UNPROVABLE
        current = _application()
        candidate = _application(
            source_revision=REV_D,
            image_digest=IMAGE_C,
            configuration_digest=CONFIG_C,
            manifest_digest=MANIFEST_C,
        )

    envelope = DeploymentExecutionEnvelopeV1(
        execution_id="deployment-run-60366",
        product="dotmac_starter",
        target_ref="observe:dotmac-starter",
        plan_digest=PLAN_DIGEST,
        required_controller=_controller(),
        authorizer=_authorizer(),
        candidate=candidate,
        expected_current=current,
        relation_evidence=_relation(
            relation,
            current=current,
            candidate=candidate,
        ),
        override=None,
    )
    return envelope, current


def _override(envelope: DeploymentExecutionEnvelopeV1) -> TransitionOverrideV1:
    assert envelope.expected_current is not None
    return TransitionOverrideV1(
        kind=envelope.relation_evidence.relation,
        decision_ref="change-control:CHG-60366",
        execution_identity_digest=envelope.execution_identity_digest,
        plan_digest=envelope.plan_digest,
        from_identity_digest=envelope.expected_current.identity_digest,
        to_identity_digest=envelope.candidate.identity_digest,
        controller_identity_digest=envelope.required_controller.identity_digest,
        authorizer_identity_digest=envelope.authorizer.identity_digest,
        reason="Reviewed emergency transition",
    )


def _decide(
    envelope: DeploymentExecutionEnvelopeV1,
    **overrides: object,
) -> TransitionDecision:
    actual: dict[str, object] = {
        "actual_controller": envelope.required_controller,
        "actual_authorizer": envelope.authorizer,
        "actual_candidate": envelope.candidate,
        "actual_current": envelope.expected_current,
        "actual_relation": envelope.relation_evidence,
        "actual_plan_digest": envelope.plan_digest,
    }
    actual.update(overrides)
    return decide_transition(envelope, **actual)


def _identity_json(identity: ApplicationReleaseIdentityV1) -> dict[str, str]:
    return {
        "image_digest": identity.image_digest,
        "source_revision": identity.source_revision,
        "configuration_digest": identity.configuration_digest,
        "manifest_digest": identity.manifest_digest,
    }


def _controller_json(controller: ControllerProvenanceV1) -> dict[str, object]:
    return {
        "distribution": controller.distribution,
        "exact_version": controller.exact_version,
        "artifact_sha256": controller.artifact_sha256,
        "launcher_sha256": controller.launcher_sha256,
        "source_revision": controller.source_revision,
        "release_run_id": controller.release_run_id,
        "tag": controller.tag,
    }


def _authorizer_json(authorizer: AuthorizerProvenanceV1) -> dict[str, object]:
    return {
        "repository": authorizer.repository,
        "workflow_path": authorizer.workflow_path,
        "workflow_revision": authorizer.workflow_revision,
        "run_id": authorizer.run_id,
    }


def _relation_json(evidence: RevisionEvidenceV1) -> dict[str, str | None]:
    return {
        "relation": evidence.relation.value,
        "from_revision": evidence.from_revision,
        "to_revision": evidence.to_revision,
        "history_snapshot_digest": evidence.history_snapshot_digest,
    }


def _override_json(override: TransitionOverrideV1) -> dict[str, str]:
    return {
        "kind": override.kind.value,
        "decision_ref": override.decision_ref,
        "execution_identity_digest": override.execution_identity_digest,
        "plan_digest": override.plan_digest,
        "from_identity_digest": override.from_identity_digest,
        "to_identity_digest": override.to_identity_digest,
        "controller_identity_digest": override.controller_identity_digest,
        "authorizer_identity_digest": override.authorizer_identity_digest,
        "reason": override.reason,
    }


def _payload(
    relation: RevisionRelation = RevisionRelation.FORWARD,
    *,
    with_override: bool = False,
) -> dict[str, Any]:
    envelope, _ = _case(relation)
    if with_override:
        envelope = replace(envelope, override=_override(envelope))
    return {
        "schema": "DeploymentExecutionEnvelope.v1",
        "execution_id": envelope.execution_id,
        "product": envelope.product,
        "target_ref": envelope.target_ref,
        "plan_digest": envelope.plan_digest,
        "required_controller": _controller_json(envelope.required_controller),
        "authorizer": _authorizer_json(envelope.authorizer),
        "candidate": _identity_json(envelope.candidate),
        "expected_current": (
            None
            if envelope.expected_current is None
            else _identity_json(envelope.expected_current)
        ),
        "relation_evidence": _relation_json(envelope.relation_evidence),
        "override": (
            None if envelope.override is None else _override_json(envelope.override)
        ),
    }


# ── strict, separately versioned execution evidence ────────────────────────


def test_execution_envelope_loads_the_complete_strict_json_contract() -> None:
    payload = _payload()

    loaded = DeploymentExecutionEnvelopeV1.loads(
        json.dumps(payload), source="<execution-canary>"
    )

    expected, _ = _case(RevisionRelation.FORWARD)
    assert loaded == expected
    assert loaded.expected_current is not None


def test_execution_envelope_load_reads_the_same_contract_from_a_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "execution-envelope.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    loaded = DeploymentExecutionEnvelopeV1.load(path)

    expected, _ = _case(RevisionRelation.FORWARD)
    assert loaded == expected


def test_launcher_provenance_environment_is_scrubbed_before_product_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = {
        "DOTMAC_CONTROLLER_ARTIFACT_SHA256",
        "DOTMAC_CONTROLLER_LAUNCHER_SHA256",
        "DOTMAC_CONTROLLER_SOURCE_REVISION",
        "DOTMAC_CONTROLLER_RELEASE_RUN_ID",
        "DOTMAC_CONTROLLER_TAG",
    }
    for name in names:
        monkeypatch.setenv(name, "non-secret provenance")

    scrub_controller_provenance_environment()

    assert names.isdisjoint(os.environ)


def test_explicit_null_current_means_first_install() -> None:
    payload = _payload(RevisionRelation.FIRST_INSTALL)
    assert "expected_current" in payload
    assert payload["expected_current"] is None

    loaded = DeploymentExecutionEnvelopeV1.loads(json.dumps(payload))
    decision = _decide(loaded)

    assert decision == TransitionDecision(
        allowed=True,
        relation=RevisionRelation.FIRST_INSTALL,
        reason_code="first_install",
        overridden=False,
        blockers=(),
    )


def test_missing_current_is_not_silently_reclassified_as_first_install() -> None:
    payload = _payload(RevisionRelation.FIRST_INSTALL)
    del payload["expected_current"]

    with pytest.raises(UnknownFieldError, match="expected_current"):
        DeploymentExecutionEnvelopeV1.loads(json.dumps(payload))


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("required_controller",),
        ("authorizer",),
        ("candidate",),
        ("expected_current",),
        ("relation_evidence",),
    ],
)
def test_unknown_json_field_is_refused_at_every_regular_level(
    path: tuple[str, ...],
) -> None:
    payload = _payload()
    cursor: dict[str, Any] = payload
    for part in path:
        nested = cursor[part]
        assert isinstance(nested, dict)
        cursor = nested
    cursor["ignored_security_control"] = True

    with pytest.raises(UnknownFieldError, match="ignored_security_control"):
        DeploymentExecutionEnvelopeV1.loads(json.dumps(payload))


def test_unknown_json_field_is_refused_inside_an_override() -> None:
    payload = _payload(RevisionRelation.ROLLBACK, with_override=True)
    override = payload["override"]
    assert isinstance(override, dict)
    override["ignore_controller_version"] = True

    with pytest.raises(UnknownFieldError, match="ignore_controller_version"):
        DeploymentExecutionEnvelopeV1.loads(json.dumps(payload))


def test_unknown_execution_schema_fails_closed() -> None:
    payload = _payload()
    payload["schema"] = "DeploymentExecutionEnvelope.v2"

    with pytest.raises(UnknownSchemaError, match=r"DeploymentExecutionEnvelope\.v2"):
        DeploymentExecutionEnvelopeV1.loads(json.dumps(payload))


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        "[]",
        json.dumps({**_payload(), "execution_id": 42}),
    ],
)
def test_malformed_or_wrongly_typed_execution_json_is_refused(text: str) -> None:
    with pytest.raises(SpecError):
        DeploymentExecutionEnvelopeV1.loads(text)


@pytest.mark.parametrize(
    "duplicate",
    [
        '"execution_id":"shadow-run","execution_id":',
        '"image_digest":"sha256:' + "0" * 64 + '","image_digest":',
    ],
)
def test_duplicate_json_field_is_refused_at_root_and_nested_levels(
    duplicate: str,
) -> None:
    text = json.dumps(_payload(), separators=(",", ":"))
    field = duplicate.rsplit('"', 2)[1]
    text = text.replace(f'"{field}":', duplicate, 1)

    with pytest.raises(SpecError, match=rf"duplicate JSON field '{field}'"):
        DeploymentExecutionEnvelopeV1.loads(text)


# ── digest receipts bind every byte-relevant identity field ────────────────


def _expected_identity_digest(kind: str, document: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"kind": kind, **document}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def test_execution_envelope_digest_binds_the_complete_override_receipt() -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)
    approved = replace(envelope, override=_override(envelope))
    assert approved.override is not None
    altered = replace(
        approved,
        override=replace(approved.override, reason="A different reviewed reason"),
    )

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", approved.envelope_digest)
    assert envelope.envelope_digest != approved.envelope_digest
    assert approved.envelope_digest != altered.envelope_digest


def test_identity_digest_is_the_canonical_digest_of_every_declared_field() -> None:
    application = _application()
    controller = _controller()
    authorizer = _authorizer()

    assert application.identity_digest == _expected_identity_digest(
        "ApplicationReleaseIdentityV1", _identity_json(application)
    )
    assert controller.identity_digest == _expected_identity_digest(
        "ControllerProvenanceV1", _controller_json(controller)
    )
    assert authorizer.identity_digest == _expected_identity_digest(
        "AuthorizerProvenanceV1", _authorizer_json(authorizer)
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("image_digest", IMAGE_C),
        ("source_revision", REV_C),
        ("configuration_digest", CONFIG_C),
        ("manifest_digest", MANIFEST_C),
    ],
)
def test_application_identity_digest_is_sensitive_to_every_field(
    field: str, replacement: str
) -> None:
    identity = _application()

    changed = replace(identity, **{field: replacement})

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", identity.identity_digest)
    assert changed.identity_digest != identity.identity_digest


@pytest.mark.parametrize(
    "changes",
    [
        {
            "exact_version": "9.9.9",
            "tag": "dotmac-deployment-foundation-v9.9.9",
        },
        {"artifact_sha256": "sha256:" + "0" * 64},
        {"launcher_sha256": "sha256:" + "1" * 64},
        {"source_revision": REV_D},
        {"release_run_id": 60367},
    ],
)
def test_controller_identity_digest_is_sensitive_to_every_field(
    changes: dict[str, object],
) -> None:
    identity = _controller()

    changed = replace(identity, **changes)

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", identity.identity_digest)
    assert changed.identity_digest != identity.identity_digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repository", "attacker/fork"),
        ("workflow_path", ".github/workflows/unreviewed.yml"),
        ("workflow_revision", REV_D),
        ("run_id", 987654322),
    ],
)
def test_authorizer_identity_digest_is_sensitive_to_every_field(
    field: str, replacement: object
) -> None:
    identity = _authorizer()

    changed = replace(identity, **{field: replacement})

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", identity.identity_digest)
    assert changed.identity_digest != identity.identity_digest


def test_application_history_digest_is_not_the_authorizer_workflow_revision() -> None:
    envelope, _ = _case(RevisionRelation.FORWARD)
    evidence = envelope.relation_evidence
    snapshot = _history_snapshot(
        from_revision=evidence.from_revision,
        to_revision=evidence.to_revision,
    )

    assert evidence.history_snapshot_digest == snapshot.snapshot_digest
    assert evidence.history_snapshot_digest != envelope.authorizer.workflow_revision


# ── allowed transitions and the rebuild collision ─────────────────────────


@pytest.mark.parametrize(
    ("relation", "reason_code"),
    [
        (RevisionRelation.FIRST_INSTALL, "first_install"),
        (RevisionRelation.SAME, "exact_replay"),
        (RevisionRelation.FORWARD, "forward"),
    ],
)
def test_safe_transition_is_allowed_with_no_blockers(
    relation: RevisionRelation, reason_code: str
) -> None:
    envelope, _ = _case(relation)

    decision = _decide(envelope)

    assert decision.allowed is True
    assert decision.relation is relation
    assert decision.reason_code == reason_code
    assert decision.overridden is False
    assert decision.blockers == ()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("image_digest", IMAGE_C),
        ("configuration_digest", CONFIG_C),
        ("manifest_digest", MANIFEST_C),
    ],
)
def test_same_source_with_different_application_bytes_is_a_rebuild_conflict(
    field: str, replacement: str
) -> None:
    exact, current = _case(RevisionRelation.SAME)
    assert current is not None
    assert _decide(exact).reason_code == "exact_replay"  # positive control
    rebuilt = replace(
        exact,
        candidate=replace(exact.candidate, **{field: replacement}),
    )

    decision = _decide(rebuilt)

    assert decision.allowed is False
    assert decision.relation is RevisionRelation.SAME
    assert decision.reason_code == "rebuild_conflict"
    assert decision.overridden is False
    assert decision.blockers


# ── anti-rollback and exact, fully bound overrides ─────────────────────────


@pytest.mark.parametrize(
    ("relation", "refusal"),
    [
        (RevisionRelation.ROLLBACK, "rollback_refused"),
        (RevisionRelation.DIVERGED, "diverged_refused"),
        (RevisionRelation.UNPROVABLE, "unprovable_refused"),
    ],
)
def test_unsafe_transition_refuses_without_override_and_exact_override_allows(
    relation: RevisionRelation, refusal: str
) -> None:
    envelope, _ = _case(relation)

    refused = _decide(envelope)
    overridden_envelope = replace(envelope, override=_override(envelope))
    allowed = _decide(overridden_envelope)

    assert refused.allowed is False
    assert refused.reason_code == refusal
    assert refused.overridden is False
    assert refused.blockers
    assert allowed.allowed is True
    assert allowed.reason_code == "override"
    assert allowed.relation is relation
    assert allowed.overridden is True
    assert allowed.blockers == ()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("kind", RevisionRelation.DIVERGED),
        ("execution_identity_digest", OTHER_DIGEST),
        ("plan_digest", OTHER_DIGEST),
        ("from_identity_digest", OTHER_DIGEST),
        ("to_identity_digest", OTHER_DIGEST),
        ("controller_identity_digest", OTHER_DIGEST),
        ("authorizer_identity_digest", OTHER_DIGEST),
    ],
)
def test_each_override_binding_is_individually_reachable_and_fail_closed(
    field: str, replacement: object
) -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)
    exact = replace(envelope, override=_override(envelope))
    assert _decide(exact).allowed is True  # the blocker is avoidable
    assert exact.override is not None
    hostile_override = replace(exact.override, **{field: replacement})
    hostile = replace(exact, override=hostile_override)

    decision = _decide(hostile)

    assert decision.allowed is False
    assert decision.reason_code == "binding_mismatch"
    assert decision.overridden is False
    assert decision.blockers


def test_override_cannot_be_reused_for_another_execution_identity() -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)
    authorised = replace(envelope, override=_override(envelope))
    assert _decide(authorised).allowed is True
    retargeted = replace(authorised, execution_id="deployment-run-60367")

    decision = _decide(retargeted)

    assert decision.allowed is False
    assert decision.reason_code == "binding_mismatch"
    assert decision.blockers == ("override_execution",)


@pytest.mark.parametrize("kind", list(RevisionRelation)[:3])
def test_override_kind_cannot_name_a_normally_allowed_transition(
    kind: RevisionRelation,
) -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)

    with pytest.raises(SpecError):
        replace(_override(envelope), kind=kind)


@pytest.mark.parametrize("field", ["decision_ref", "reason"])
def test_override_requires_nonempty_decision_evidence(field: str) -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)

    with pytest.raises(SpecError):
        replace(_override(envelope), **{field: " "})


# ── observed world must exactly match every authorised binding ─────────────


@pytest.mark.parametrize(
    ("actual_name", "mutation"),
    [
        ("actual_controller", "controller_version"),
        ("actual_controller", "controller_artifact"),
        ("actual_authorizer", "authorizer_workflow"),
        ("actual_candidate", "candidate_image"),
        ("actual_plan_digest", "plan"),
        ("actual_current", "current_image"),
        ("actual_current", "current_missing"),
        ("actual_relation", "relation_kind"),
        ("actual_relation", "relation_from"),
        ("actual_relation", "relation_to"),
        ("actual_relation", "relation_history_snapshot"),
    ],
)
def test_each_authorised_to_actual_binding_is_individually_enforced(
    actual_name: str, mutation: str
) -> None:
    envelope, _ = _case(RevisionRelation.FORWARD)
    assert _decide(envelope).allowed is True  # positive/sensitivity control
    actual: object
    if mutation == "controller_version":
        actual = replace(
            envelope.required_controller,
            exact_version="9.9.9",
            tag="dotmac-deployment-foundation-v9.9.9",
        )
    elif mutation == "controller_artifact":
        actual = replace(
            envelope.required_controller,
            artifact_sha256="sha256:" + "0" * 64,
        )
    elif mutation == "authorizer_workflow":
        actual = replace(
            envelope.authorizer,
            workflow_path=".github/workflows/unreviewed.yml",
        )
    elif mutation == "candidate_image":
        actual = replace(envelope.candidate, image_digest=IMAGE_C)
    elif mutation == "plan":
        actual = OTHER_DIGEST
    elif mutation == "current_image":
        assert envelope.expected_current is not None
        actual = replace(envelope.expected_current, image_digest=IMAGE_C)
    elif mutation == "current_missing":
        actual = None
    elif mutation == "relation_kind":
        actual = replace(
            envelope.relation_evidence,
            relation=RevisionRelation.UNPROVABLE,
        )
    elif mutation == "relation_from":
        actual = replace(envelope.relation_evidence, from_revision=REV_D)
    elif mutation == "relation_to":
        actual = replace(envelope.relation_evidence, to_revision=REV_D)
    else:
        assert mutation == "relation_history_snapshot"
        actual = replace(
            envelope.relation_evidence,
            history_snapshot_digest=OTHER_DIGEST,
        )

    decision = _decide(envelope, **{actual_name: actual})

    assert decision.allowed is False
    assert decision.reason_code == "binding_mismatch"
    assert decision.overridden is False
    assert decision.blockers


def test_override_does_not_excuse_an_observed_world_binding_mismatch() -> None:
    envelope, _ = _case(RevisionRelation.ROLLBACK)
    envelope = replace(envelope, override=_override(envelope))
    assert _decide(envelope).allowed is True

    decision = _decide(
        envelope,
        actual_controller=replace(
            envelope.required_controller,
            artifact_sha256="sha256:" + "0" * 64,
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == "binding_mismatch"
    assert decision.overridden is False


# ── real Git semantics: 0, 1 and errors are three different answers ────────


def _git_binary() -> Path:
    found = shutil.which("git")
    if found is None:
        pytest.skip("git is required by the deployment revision oracle")
    return Path(found).resolve()


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603 - argv is the fixed test Git binary
        [str(_git_binary()), *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repository: Path, content: str, message: str) -> str:
    (repository / "payload.txt").write_text(content, encoding="utf-8")
    _git(repository, "add", "payload.txt")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _git_dag(repository: Path) -> tuple[str, str, str]:
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.email", "canary@example.invalid")
    _git(repository, "config", "user.name", "Deployment Canary")
    first = _commit(repository, "first\n", "first")
    second = _commit(repository, "second\n", "second")
    _git(repository, "checkout", "-b", "sibling", first)
    sibling = _commit(repository, "sibling\n", "sibling")
    _git(repository, "checkout", "main")
    return first, second, sibling


def _oracle_for_transition(
    repository: Path,
    *,
    from_revision: str | None,
    to_revision: str,
    git_binary: Path | None = None,
) -> tuple[GitRevisionOracle, ApplicationHistorySnapshotV1]:
    snapshot = _history_snapshot(
        from_revision=from_revision,
        to_revision=to_revision,
    )
    return (
        GitRevisionOracle(
            repository=repository.resolve(),
            git_binary=git_binary or _git_binary(),
            snapshot=snapshot,
        ),
        snapshot,
    )


def _git_evidence(
    repository: Path,
    *,
    from_revision: str | None,
    to_revision: str,
    git_binary: Path | None = None,
) -> RevisionEvidenceV1:
    oracle, snapshot = _oracle_for_transition(
        repository,
        from_revision=from_revision,
        to_revision=to_revision,
        git_binary=git_binary,
    )
    return oracle.evidence(
        from_revision=from_revision,
        to_revision=to_revision,
        history_snapshot_digest=snapshot.snapshot_digest,
    )


def test_git_revision_oracle_requires_an_absolute_git_binary(tmp_path: Path) -> None:
    snapshot = _history_snapshot(from_revision=None, to_revision=REV_A)
    with pytest.raises(SpecError, match="absolute"):
        GitRevisionOracle(
            repository=tmp_path.resolve(),
            git_binary=Path("git"),
            snapshot=snapshot,
        )


def test_real_git_oracle_distinguishes_same_forward_rollback_and_first_install(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    first, second, _ = _git_dag(repository)
    same = _git_evidence(
        repository,
        from_revision=second,
        to_revision=second,
    )
    forward = _git_evidence(
        repository,
        from_revision=first,
        to_revision=second,
    )
    rollback = _git_evidence(
        repository,
        from_revision=second,
        to_revision=first,
    )
    first_install = _git_evidence(
        repository,
        from_revision=None,
        to_revision=second,
    )

    assert same.relation is RevisionRelation.SAME
    assert forward.relation is RevisionRelation.FORWARD
    assert rollback.relation is RevisionRelation.ROLLBACK
    assert first_install.relation is RevisionRelation.FIRST_INSTALL


def test_real_git_exit_one_means_diverged_not_oracle_failure(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _, second, sibling = _git_dag(repository)
    _git(repository, "checkout", "sibling")
    evidence = _git_evidence(
        repository,
        from_revision=second,
        to_revision=sibling,
    )

    assert evidence.relation is RevisionRelation.DIVERGED


def test_real_git_error_is_unprovable_not_diverged(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _, second, _ = _git_dag(repository)
    missing = "0" * 40
    missing_object = _git_evidence(
        repository,
        from_revision=second,
        to_revision=missing,
    )
    false_first_install = _git_evidence(
        repository,
        from_revision=None,
        to_revision=missing,
    )

    assert missing_object.relation is RevisionRelation.UNPROVABLE
    assert false_first_install.relation is RevisionRelation.UNPROVABLE


def test_git_oracle_refuses_a_wrong_snapshot_digest_or_transition(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    first, second, _ = _git_dag(repository)
    oracle, snapshot = _oracle_for_transition(
        repository,
        from_revision=first,
        to_revision=second,
    )
    healthy = oracle.evidence(
        from_revision=first,
        to_revision=second,
        history_snapshot_digest=snapshot.snapshot_digest,
    )

    with pytest.raises(SpecError, match="different signed snapshot"):
        oracle.evidence(
            from_revision=first,
            to_revision=second,
            history_snapshot_digest=OTHER_DIGEST,
        )
    with pytest.raises(SpecError, match="authorized transition"):
        oracle.evidence(
            from_revision=second,
            to_revision=second,
            history_snapshot_digest=snapshot.snapshot_digest,
        )

    assert healthy.relation is RevisionRelation.FORWARD


def test_merge_base_exit_greater_than_one_is_unprovable(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    first, second, _ = _git_dag(repository)
    real_git = _git_binary()
    failing_git = tmp_path / "git-with-merge-base-error"
    failing_git.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = "merge-base" ]; then exit 2; fi\n'
        f'exec {shlex.quote(str(real_git))} "$@"\n',
        encoding="utf-8",
    )
    failing_git.chmod(0o755)
    evidence = _git_evidence(
        repository,
        from_revision=first,
        to_revision=second,
        git_binary=failing_git.resolve(),
    )

    assert evidence.relation is RevisionRelation.UNPROVABLE
