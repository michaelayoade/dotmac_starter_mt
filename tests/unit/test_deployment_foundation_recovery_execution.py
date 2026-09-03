"""The restore EXECUTOR: it performs the ten steps, and obeys the adjudicator.

`recovery.py` could describe a recovery in exact detail and could not perform
one — `RESTORE_PROCEDURE`'s ten steps are `RestoreStepSpec` TEXT, and the
deployment `Effects` protocol has no restore method among its twenty-four. That
is the same shape as `ExecutionPlanDigestV1` before a5: built, tested, and
unreachable from anything that touches a host.

## What these tests hold

The executor DECIDES nothing the data contract already decides. So the
assertions are about obedience and ordering, not about judgement:

- a DESTROY verdict from `adjudicate_restore` is PERFORMED, before anything
  inspects the target, and the run stops there;
- a catalog that differs from the bundle stops the recovery rather than
  reporting a difference and continuing;
- an image that never becomes ready is a failed recovery — a database that
  restores and cannot run the application is a copy;
- and the happy path walks all ten steps in the contract's order.

## Deliberately NOT tested here: reachability

This slice ships the executor and nothing that can call it. There is no CLI
subcommand and no `recover` member in `authorization.OPERATIONS`, so a test
asserting "the CLI can recover" would be asserting a thing that must not exist
yet. The vocabulary widening and the authorization binding are the next slice,
in that order, because a tuple widened before its executor exists authorizes an
operation nothing can perform.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.recovery import (
    RESTORE_PROCEDURE,
    CatalogEvidence,
    Disposition,
    RestoreAttempt,
)
from dotmac_deployment_foundation.recovery_execution import (
    RecoveryEffects,
    RecoveryExecutor,
    RecoveryOutcome,
    RestoreTarget,
)

from tests.unit.test_deployment_foundation_recovery_bundle import (
    _evidence,
    _manifest,
    _spec,
)

CLEAN = RestoreAttempt(exit_status=0, tables_present=45, duration_seconds=12)

#: The measured Vendor CP failure, verbatim: exit 1 with 114 missing-role
#: errors, leaving 45 tables, 23 policies and 16 RLS-enabled tables behind. A
#: wrapper returning on the status alone would report a clean failure and leave
#: that database sitting there looking recovered.
VENDOR_CP_PARTIAL = RestoreAttempt(
    exit_status=1,
    tables_present=45,
    policies_present=23,
    rls_tables_present=16,
    missing_role_errors=114,
    stderr_excerpt="role does not exist",
)

IMAGE = "ghcr.io/example/app@sha256:" + "a" * 64


class RecordingRecoveryEffects:
    """Every host effect recorded, every observation answerable.

    Defaults to the healthy world so a test that expects a step to be reached
    can reach it; each test makes exactly one thing wrong.
    """

    def __init__(
        self,
        *,
        attempt: RestoreAttempt = CLEAN,
        restored: CatalogEvidence | None = None,
        image_ready: bool = True,
    ) -> None:
        self.calls: list[str] = []
        self.attempt = attempt
        self.restored = restored if restored is not None else _evidence()
        self.image_ready = image_ready
        self.destroyed: list[str] = []
        self.created: list[int] = []

    def create_fresh_target(self, *, major_version: int) -> RestoreTarget:
        self.calls.append("create_fresh_target")
        self.created.append(major_version)
        return RestoreTarget(identifier="recovery-1", major_version=major_version)

    def restore_roles(
        self, target: RestoreTarget, *, bundle: Mapping[str, Any]
    ) -> RestoreAttempt:
        self.calls.append("restore_roles")
        return CLEAN

    def restore_objects(
        self, target: RestoreTarget, *, bundle: Mapping[str, Any]
    ) -> RestoreAttempt:
        self.calls.append("restore_objects")
        return self.attempt

    def destroy_target(self, target: RestoreTarget) -> None:
        self.calls.append("destroy_target")
        self.destroyed.append(target.identifier)

    def install_login_material(self, target: RestoreTarget) -> None:
        self.calls.append("install_login_material")

    def observe_catalog(self, target: RestoreTarget) -> CatalogEvidence:
        self.calls.append("observe_catalog")
        return self.restored

    def observe_plane_isolation(self, target: RestoreTarget) -> Sequence[Any]:
        self.calls.append("observe_plane_isolation")
        return ()

    def start_product_image(self, target: RestoreTarget, *, image: str) -> bool:
        self.calls.append("start_product_image")
        return self.image_ready


def _executor(effects: RecordingRecoveryEffects) -> RecoveryExecutor:
    return RecoveryExecutor(
        _spec(),
        _manifest(),
        effects,
        source_evidence=_evidence(),
        product_image=IMAGE,
    )


# ── the seam is a protocol a product can satisfy ────────────────────────────


def test_the_recording_effects_satisfies_the_protocol() -> None:
    """If the fake drifts from the seam, every test below is exercising a shape
    no provider has to implement."""
    assert isinstance(RecordingRecoveryEffects(), RecoveryEffects)


# ── the happy path: all ten steps, in the contract's order ──────────────────


def test_a_clean_restore_walks_every_step_of_the_declared_procedure() -> None:
    effects = RecordingRecoveryEffects()
    outcome = _executor(effects).run(bundle={})
    assert outcome.failure == "", outcome.failure
    assert outcome.proved is True
    assert outcome.destroyed is False
    assert outcome.steps_completed == tuple(
        step.step.value for step in RESTORE_PROCEDURE
    ), "the executor walked a different procedure than the contract declares"
    assert effects.created == [_manifest().postgres_major], (
        "the major version must come from the BUNDLE — a restore across majors "
        "is a migration wearing a recovery's clothes"
    )


def test_the_procedure_is_read_from_the_contract_not_copied() -> None:
    """Ten steps today. If the contract grows an eleventh, the executor walks it
    or this fails — which is the point of driving `restore_plan`'s output rather
    than a list in the executor."""
    effects = RecordingRecoveryEffects()
    outcome = _executor(effects).run(bundle={})
    assert len(outcome.steps_completed) == len(RESTORE_PROCEDURE) == 10


# ── the adjudicator's DESTROY is PERFORMED, not reported ────────────────────


def test_a_partial_restore_is_destroyed_before_anything_inspects_it() -> None:
    """THE refusal this module exists for, with the measured failure as input.

    A non-zero restore that left 45 tables, 23 policies and 16 RLS-enabled
    tables is not "failed, therefore nothing happened" — it is a database that
    will pass a table count, a policy listing and an RLS check. The verdict is
    `adjudicate_restore`'s; performing it is the executor's.
    """
    effects = RecordingRecoveryEffects(attempt=VENDOR_CP_PARTIAL)
    outcome = _executor(effects).run(bundle={})

    assert outcome.adjudication is not None
    assert outcome.adjudication.disposition is Disposition.DESTROY
    assert outcome.destroyed is True
    assert effects.destroyed == ["recovery-1"]
    assert outcome.proved is False
    assert "destroyed" in outcome.failure

    # And nothing looked at it afterwards.
    after_destroy = effects.calls[effects.calls.index("destroy_target") + 1 :]
    assert after_destroy == [], (
        f"the target was inspected after being destroyed: {after_destroy}. A "
        "partial target is destroyed BEFORE anything reads it, or the reader "
        "finds policies present and concludes the isolation survived"
    )
    assert "observe_catalog" not in effects.calls
    assert "start_product_image" not in effects.calls


def test_a_clean_exit_is_not_destroyed() -> None:
    """The positive control. Without it, the refusal above is equally
    consistent with an executor that destroys every target it makes."""
    effects = RecordingRecoveryEffects()
    outcome = _executor(effects).run(bundle={})
    assert outcome.destroyed is False
    assert effects.destroyed == []
    assert outcome.adjudication is not None
    assert outcome.adjudication.disposition is Disposition.PROCEED


# ── the proofs stop the recovery rather than annotating it ──────────────────


def test_a_catalog_that_differs_from_the_bundle_stops_the_recovery() -> None:
    """`verify_recovery` returns findings rather than raising, so an operator
    sees them all at once. The executor must still STOP: a recovery reported as
    proved with findings attached is one nobody reads the findings of."""
    lost_roles = CatalogEvidence(
        **{
            **{f: getattr(_evidence(), f) for f in _evidence().__dataclass_fields__},
            "roles": (),
        }
    )
    effects = RecordingRecoveryEffects(restored=lost_roles)
    outcome = _executor(effects).run(bundle={})
    assert outcome.proved is False
    assert outcome.findings, "the findings must be carried, not just the failure"
    assert "prove_catalog" not in outcome.steps_completed
    assert "start_product_image" not in effects.calls


def test_an_image_that_never_becomes_ready_fails_the_recovery() -> None:
    """A database that restores and cannot run the application is a copy, not a
    recovery."""
    effects = RecordingRecoveryEffects(image_ready=False)
    outcome = _executor(effects).run(bundle={})
    assert outcome.proved is False
    assert "did not become ready" in outcome.failure
    assert "emit_receipt" not in outcome.steps_completed


# ── construction-time refusals ──────────────────────────────────────────────


def test_a_recovery_with_no_product_image_is_refused_at_construction() -> None:
    """Step 9 cannot be performed without one, and an executor that discovers
    that at step 9 has already created a cluster and restored into it."""
    with pytest.raises(PreconditionFailed, match="no product image"):
        RecoveryExecutor(
            _spec(),
            _manifest(),
            RecordingRecoveryEffects(),
            source_evidence=_evidence(),
            product_image="  ",
        )


def test_a_target_with_no_identifier_is_refused() -> None:
    """The DESTROY verdict must always be actionable; a handle that cannot name
    what to destroy makes the one unconditional refusal unperformable."""
    with pytest.raises(PreconditionFailed, match="cannot be destroyed"):
        RestoreTarget(identifier="", major_version=16)


# ── the evidence shape ──────────────────────────────────────────────────────


def test_the_outcome_records_what_happened_including_a_destruction() -> None:
    effects = RecordingRecoveryEffects(attempt=VENDOR_CP_PARTIAL)
    evidence = _executor(effects).run(bundle={}).as_evidence()
    assert evidence["schema"] == "RecoveryExecution.v1"
    assert evidence["disposition"] == "destroy"
    assert evidence["destroyed"] is True
    assert evidence["proved"] is False
    assert evidence["exit_status"] == 1
    assert evidence["adjudication_reasons"], "a DESTROY must say why"
    assert evidence["target"] == "recovery-1"


def test_an_unstarted_outcome_carries_no_invented_facts() -> None:
    outcome = RecoveryOutcome()
    evidence = outcome.as_evidence()
    assert evidence["target"] == ""
    assert evidence["exit_status"] is None
    assert evidence["disposition"] == ""
    assert evidence["proved"] is False


# ── Shape B: reachable, and the premise for needing no ExecutionGrant ────────


def test_every_recovery_effect_acts_on_a_target_this_executor_created() -> None:
    """THE ENFORCEABLE PREMISE, checked as a property rather than asserted.

    `deploy` and `rollback` need an `ExecutionGrant` because `Executor` mutates
    the product host. This executor needs none, and the reason has to be
    checkable or it is an exemption wearing a premise's clothes: every method on
    `RecoveryEffects` either CREATES a target or takes one as a parameter, so
    there is no method by which it can name, reach or mutate a running
    deployment.

    Signature inspection, not a name list. Adding `restart_product_role(self,
    role: str)` to the protocol fails HERE — which is the point, because that
    method would silently give a grant-free executor a way onto the product
    host, and nothing else in this package would notice.
    """
    import inspect

    from dotmac_deployment_foundation.recovery_execution import RecoveryEffects

    methods = {
        name: getattr(RecoveryEffects, name)
        for name in vars(RecoveryEffects)
        if not name.startswith("_") and callable(getattr(RecoveryEffects, name))
    }
    assert methods, "the protocol exposes no methods; this check would be vacuous"

    offenders = []
    for name, method in sorted(methods.items()):
        params = inspect.signature(method).parameters
        if name == "create_fresh_target":
            continue
        annotations = {str(p.annotation) for p in params.values()}
        if not any("RestoreTarget" in item for item in annotations):
            offenders.append(name)
    assert not offenders, (
        f"RecoveryEffects methods {offenders} do not take a RestoreTarget. "
        "Every effect must act on a target this executor created, or the "
        "premise for running without an ExecutionGrant no longer holds and "
        "this executor needs the authorization chain a7 builds"
    )


def test_the_executor_is_on_the_packages_public_surface() -> None:
    """It was importable only as a private submodule, which is not reachability.
    An embedder could not reach it through `__all__`, and the CLI did not call
    it — so every seam was real in principle and unreachable in practice."""
    import dotmac_deployment_foundation as facility

    for name in (
        "RecoveryExecutor",
        "RecoveryEffects",
        "RecoverySession",
        "RecoveryOutcome",
        "RestoreTarget",
    ):
        assert name in facility.__all__, name
        assert hasattr(facility, name), name


def test_recover_is_not_an_authorizable_operation() -> None:
    """Withdrawn in a6. The executor performs an isolated REHEARSAL; `recover`
    names recovering a failed production system, which it cannot do. See
    `authorization.OPERATIONS` for the reasoning and the a7 successor."""
    from dotmac_deployment_foundation.authorization import OPERATIONS

    assert "recover" not in OPERATIONS
    assert set(OPERATIONS) == {"deploy", "rollback"}


# ── the session, refused on typed CODES ─────────────────────────────────────


def _session(**overrides):
    from dotmac_deployment_foundation.recovery import CatalogEvidence
    from dotmac_deployment_foundation.recovery_execution import RecoverySession

    fields = {
        "effects": RecordingRecoveryEffects(),
        "source_evidence": CatalogEvidence(),
        "product_image": "ghcr.io/x@sha256:" + "a" * 64,
        "bundle": {},
    }
    fields.update(overrides)
    return RecoverySession(**fields)


def test_a_valid_recovery_session_constructs() -> None:
    """POSITIVE CONTROL. A refusal suite whose subject can never be built
    proves nothing about the refusals."""
    assert _session().product_image


def test_a_session_whose_effects_are_a_look_alike_is_refused_by_code() -> None:
    from dotmac_deployment_foundation.errors import PreconditionFailed
    from dotmac_deployment_foundation.recovery_execution import (
        SESSION_EFFECTS_INVALID,
    )

    class LookAlike:
        pass

    with pytest.raises(PreconditionFailed) as caught:
        _session(effects=LookAlike())
    assert caught.value.code == SESSION_EFFECTS_INVALID


def test_a_session_without_real_source_evidence_is_refused_by_code() -> None:
    """No default and no empty-CatalogEvidence fallback anywhere upstream: an
    empty source catalogue compares clean against an empty restored one, so a
    defaulted session reports a created database as a proved recovery."""
    from dotmac_deployment_foundation.errors import PreconditionFailed
    from dotmac_deployment_foundation.recovery_execution import (
        SESSION_EVIDENCE_INVALID,
    )

    with pytest.raises(PreconditionFailed) as caught:
        _session(source_evidence={"roles": []})
    assert caught.value.code == SESSION_EVIDENCE_INVALID


def test_a_session_with_no_product_image_is_refused_by_code() -> None:
    from dotmac_deployment_foundation.errors import PreconditionFailed
    from dotmac_deployment_foundation.recovery_execution import (
        SESSION_IMAGE_MISSING,
    )

    with pytest.raises(PreconditionFailed) as caught:
        _session(product_image="   ")
    assert caught.value.code == SESSION_IMAGE_MISSING
