"""Sensitivity proofs for the active V3 grant and its pre-effect recheck."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_deployment_foundation.authorization_v3 import authorize_v3
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_bindings import ExecutionBindings
from dotmac_deployment_foundation.host_source import InstalledArtifact

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.foundation_v3_support import grant_for_plan, provider_for
from tests.unit.host_source_stance import (
    accepting_admission_provider,
    valid_host_source_kwargs,
)
from tests.unit.test_deployment_foundation_execution_binding import _plan_and_digest
from tests.unit.test_deployment_foundation_execution_seam import _subject
from tests.unit.test_deployment_foundation_failure_injection import (
    OLD_DIGEST,
    FakeEffects,
    load,
)


def _issue(spec, plan, *, provider=None, target=None):  # type: ignore[no-untyped-def]
    provider = provider or provider_for(spec, plan)
    return authorize_v3(
        bindings=ExecutionBindings(
            provider="synthetic-test-host", authorization_v3_provider=provider
        ),
        authorization_material={"kind": "authorization"},
        dispatch_material={"kind": "dispatch"},
        plan=plan,
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        operation=plan.operation,
        target=target or plan.target,
    )


@pytest.mark.parametrize(
    "field",
    [
        "target_id",
        "controller_ssh_fingerprint",
        "host_id",
        "host_incarnation",
        "host_enrolment_ref",
    ],
)
def test_independent_subject_drift_refuses_issue(tmp_path: Path, field: str) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    changed = (
        "00000000-0000-4000-8000-000000000002"
        if field == "host_enrolment_ref"
        else getattr(provider.context, field) + "-drift"
    )
    provider.context = dataclasses.replace(provider.context, **{field: changed})
    with pytest.raises(PreconditionFailed, match=field):
        _issue(spec, plan, provider=provider)


def test_wrong_recorded_wheel_refuses_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dotmac_deployment_foundation import authorization_v3

    spec, plan = _subject(tmp_path)
    monkeypatch.setattr(
        authorization_v3,
        "read_installed_artifact",
        lambda: InstalledArtifact(
            distribution="dotmac-deployment-foundation",
            version="test",
            artifact_digest=Digest.parse("sha256:" + "c" * 64),
            installed_content_digest=Digest.parse("sha256:" + "9" * 64),
            read_from="synthetic PEP610",
        ),
    )
    with pytest.raises(PreconditionFailed, match="wheel"):
        _issue(spec, plan)


def test_matching_digest_without_fixed_provider_is_not_authority(
    tmp_path: Path,
) -> None:
    spec, plan = _subject(tmp_path)
    with pytest.raises(PreconditionFailed, match="startup-fixed"):
        authorize_v3(
            bindings=None,
            authorization_material={"kind": "authorization"},
            dispatch_material={"kind": "dispatch"},
            plan=plan,
            descriptor_digest=plan.descriptor_digest,
            operation=plan.operation,
            target=plan.target,
        )


def test_grant_freezes_exact_control_pair_before_caller_mutation(
    tmp_path: Path,
) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    authorization_material = {"kind": "authorization"}
    dispatch_material = {"kind": "dispatch"}
    grant = authorize_v3(
        bindings=ExecutionBindings(
            provider="synthetic-test-host", authorization_v3_provider=provider
        ),
        authorization_material=authorization_material,
        dispatch_material=dispatch_material,
        plan=plan,
        descriptor_digest=plan.descriptor_digest,
        operation=plan.operation,
        target=plan.target,
    )
    authorization_material["kind"] = "replacement"
    dispatch_material["kind"] = "replacement"
    assert grant.authorization_material_json == b'{"kind":"authorization"}'
    assert grant.dispatch_material_json == b'{"kind":"dispatch"}'


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("execution_plan_digest", "sha256:" + "f" * 64),
        ("approval_decision_ref", "another-decision"),
        ("target_ref", "another-target"),
    ],
)
def test_v2_pair_terms_must_match_independent_inputs(
    tmp_path: Path, field: str, wrong: object
) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan, receipt_overrides={field: wrong})
    with pytest.raises(PreconditionFailed, match=field):
        _issue(spec, plan, provider=provider)


def test_v2_dispatch_coordinate_must_match_observation(tmp_path: Path) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    provider.receipt = dataclasses.replace(provider.receipt, execution_sequence=8)
    with pytest.raises(PreconditionFailed, match="execution_sequence"):
        _issue(spec, plan, provider=provider)


def test_signed_pair_mismatch_is_refused_by_fixed_attester(tmp_path: Path) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    with pytest.raises(ValueError, match="pair disagrees"):
        authorize_v3(
            bindings=ExecutionBindings(
                provider="synthetic-test-host", authorization_v3_provider=provider
            ),
            authorization_material={"kind": "authorization"},
            dispatch_material={"kind": "unrelated-dispatch"},
            plan=plan,
            descriptor_digest=plan.descriptor_digest,
            operation=plan.operation,
            target=plan.target,
        )


def test_fixed_attester_refuses_revoked_standing(tmp_path: Path) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)

    def revoked(*, authorization_material, dispatch_material):  # type: ignore[no-untyped-def]
        raise PreconditionFailed("Control standing revoked")

    provider.attest_pair = revoked  # type: ignore[method-assign]
    with pytest.raises(PreconditionFailed, match="standing revoked"):
        _issue(spec, plan, provider=provider)


def test_expired_v2_pair_refuses_issue(tmp_path: Path) -> None:
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan, now=datetime(2026, 9, 1, tzinfo=UTC))
    with pytest.raises(PreconditionFailed):
        _issue(spec, plan, provider=provider)


def test_recheck_refuses_host_reenrolment_before_effects() -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(spec)
    plan, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, plan)
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        **valid_host_source_kwargs(),
    )
    before = effects.snapshot()
    grant.v3_provider.context = dataclasses.replace(
        grant.v3_provider.context,
        host_enrolment_ref="00000000-0000-4000-8000-000000000002",
    )
    with pytest.raises(PreconditionFailed, match="host_enrolment_ref"):
        executor.run(work, lock=held_lock(spec.product))
    assert effects.snapshot() == before
    assert grant.v3_provider.consumed is False


def test_recheck_refuses_stale_pair_before_effects() -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(spec)
    plan, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, plan)
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        **valid_host_source_kwargs(),
    )
    before = effects.snapshot()
    grant.v3_provider.clock = datetime(2026, 9, 1, tzinfo=UTC)
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert effects.snapshot() == before
    assert grant.v3_provider.consumed is False


@pytest.mark.parametrize("operation", ["deploy", "rollback"])
def test_f2_host_b_cannot_execute_v3_target_a(operation: str) -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(
        spec,
        previous_image=(
            f"ghcr.io/example/app@{OLD_DIGEST}" if operation == "rollback" else ""
        ),
    )
    plan, _ = _plan_and_digest(spec, work, operation=operation, effects=effects)
    grant = grant_for_plan(spec, plan)
    admission = accepting_admission_provider()
    admission.trace = dataclasses.replace(admission.trace, host_identity="fleet-host-b")
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        admission_provider=admission,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="F2 admitted host_id"):
        if operation == "deploy":
            executor.run(work, lock=held_lock(spec.product))
        else:
            executor.rollback(work, lock=held_lock(spec.product))
    assert admission.calls == 1
    assert effects.snapshot() == before
    assert grant.v3_provider.consumed is False


@pytest.mark.parametrize(
    "field,wrong,binding",
    [
        ("installed_signer_fingerprint", "sha256:" + "c" * 64, "host_incarnation"),
        (
            "installed_trust_root_version",
            "00000000-0000-4000-8000-000000000002",
            "host_enrolment_ref",
        ),
    ],
)
def test_f2_incarnation_and_root_must_match_v3_before_effects(
    field: str, wrong: str, binding: str
) -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(spec)
    plan, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, plan)
    admission = accepting_admission_provider()
    admission.trace = dataclasses.replace(admission.trace, **{field: wrong})
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        admission_provider=admission,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match=f"F2 admitted {binding}"):
        executor.run(work, lock=held_lock(spec.product))
    assert effects.snapshot() == before
    assert grant.v3_provider.consumed is False


def test_v3_digest_moves_for_each_authority_coordinate(tmp_path: Path) -> None:
    _, plan = _subject(tmp_path)
    for field in (
        "candidate_wheel_digest",
        "target_id",
        "controller_ssh_fingerprint",
        "host_id",
        "host_incarnation",
        "host_enrolment_ref",
    ):
        replacement = (
            ("sha256:" + "e" * 64)
            if field == "candidate_wheel_digest"
            else "00000000-0000-4000-8000-000000000002"
            if field == "host_enrolment_ref"
            else getattr(plan, field) + "-drift"
        )
        assert (
            dataclasses.replace(plan, **{field: replacement}).digest() != plan.digest()
        )


@pytest.mark.parametrize("operation", ["deploy", "rollback"])
@pytest.mark.parametrize(
    "failure",
    [
        "replay",
        "revoked_after_grant",
        "consumption_failure",
        "request_mismatch",
        "commit_failure",
    ],
)
def test_unconsumed_or_changed_control_authority_has_zero_effects(
    operation: str, failure: str
) -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(
        spec,
        previous_image=(
            f"ghcr.io/example/app@{OLD_DIGEST}" if operation == "rollback" else ""
        ),
    )
    plan, _ = _plan_and_digest(spec, work, operation=operation, effects=effects)
    grant = grant_for_plan(spec, plan)
    provider = grant.v3_provider
    if failure == "replay":
        provider.consumed = True
    elif failure == "revoked_after_grant":
        provider.approval_current = False
    elif failure == "commit_failure":
        provider.commit_fails = True
    elif failure == "consumption_failure":

        def unavailable(*, request):  # type: ignore[no-untyped-def]
            raise PreconditionFailed("Control consumption failed")

        provider.consume_dispatch = unavailable  # type: ignore[method-assign]
    else:
        original = provider.consume_dispatch

        def wrong_request(*, request):  # type: ignore[no-untyped-def]
            return original(
                request=dataclasses.replace(
                    request,
                    control_consumption_ref="control-dispatch:wrong-dispatch",
                )
            )

        provider.consume_dispatch = wrong_request  # type: ignore[method-assign]
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        **valid_host_source_kwargs(),
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        if operation == "deploy":
            executor.run(work, lock=held_lock(spec.product))
        else:
            executor.rollback(work, lock=held_lock(spec.product))
    assert effects.snapshot() == before
    assert provider.consumed is (failure == "replay")
    assert provider.last_consumption_ref == ""


@pytest.mark.parametrize("operation", ["deploy", "rollback"])
def test_successful_consume_has_deterministic_recovery_ref_and_effect_boundary(
    operation: str,
) -> None:
    spec = load()
    effects = FakeEffects()
    work = build_plan(
        spec,
        previous_image=(
            f"ghcr.io/example/app@{OLD_DIGEST}" if operation == "rollback" else ""
        ),
    )
    plan, _ = _plan_and_digest(spec, work, operation=operation, effects=effects)
    grant = grant_for_plan(spec, plan)
    provider = grant.v3_provider
    events: list[str] = []
    original_consume = provider.consume_dispatch
    original_annotate = effects.emit_annotation

    def consume(*, request):  # type: ignore[no-untyped-def]
        events.append("consume")
        return original_consume(request=request)

    def annotate(annotation):  # type: ignore[no-untyped-def]
        events.append("effect")
        return original_annotate(annotation)

    provider.consume_dispatch = consume  # type: ignore[method-assign]
    effects.emit_annotation = annotate  # type: ignore[method-assign]
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=plan,
        sleep=lambda _: None,
        **valid_host_source_kwargs(),
    )
    outcome = (
        executor.run(work, lock=held_lock(spec.product))
        if operation == "deploy"
        else executor.rollback(work, lock=held_lock(spec.product))
    )
    expected = f"control-dispatch:{grant.receipt.dispatch_id}"
    assert provider.consumed is True
    assert provider.last_consumption_ref == expected
    assert events[:2] == ["consume", "effect"]
    assert outcome.control_consumption_ref == expected
    evidence = outcome.as_evidence()
    assert evidence["schema"] == "DeploymentEvidence.v2"
    assert evidence["control_consumption_ref"] == expected
