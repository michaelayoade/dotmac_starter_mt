"""The restore EXECUTOR: it performs the ten steps, and obeys the adjudicator.

`recovery.py` could describe a recovery in exact detail and could not perform
one — `RESTORE_PROCEDURE`'s ten steps are `RestoreStepSpec` TEXT, and the
deployment `Effects` protocol has no restore method among its twenty-four. That
is the same shape as `ExecutionPlanDigestV1` before a5: built, tested, and
unreachable from anything that touches a host.

## `RecoveryExecutor.run` refuses UNCONDITIONALLY — the full sequence is UNMONITORED

`_verify_host_source` always calls `require_host_source(receipt=None)`, which
always refuses — a typed refusal with zero effects, in every environment.
`RecoveryExecutor.run(...)` therefore cannot reach `_do_fresh_target` or any
step after it, anywhere, until trusted provenance (separate, future work) lands.

An earlier repair (`061cf4bd`) drove the ten steps through `_drive_steps`, a
test helper that replicated `run()`'s dispatch loop — `restore_plan`, the
procedure-drift check, the `for`/`_dispatch` loop, the `StepFailed`/
`PreconditionFailed` handling — MINUS the `_verify_host_source()` call.
Michael's ruling on review: **`_drive_steps` was an unguarded second recovery
executor**, and it had already drifted from the real one on its first day —
its docstring called it a "byte-for-byte replica" while it silently omitted
the procedure-drift refusal (`run()`'s own `if tuple(...) != tuple(...):
raise PreconditionFailed` check has no equivalent in `_drive_steps`). A
replica that drifts on day one is the strongest argument against keeping one
at all, not a reason to fix the drift and keep going.

`_drive_steps` and everything it enabled — the ten-step happy path, the
whole-sequence catalog-mismatch stop, the whole-sequence unready-image
failure — are DELETED, not repaired. **The full recovery sequence (all ten
steps executing in the real declared order, via the real `run()`/`_dispatch`)
is UNMONITORED by this test suite** until trusted provenance makes the real
`RecoveryExecutor.run` reachable.

What remains, per Michael's instruction to retain "direct pure-function and
individual-handler tests" — those exercise REAL subjects with no
reimplemented sequencing:

* each `_do_*` step handler, called directly with a manually-prepared
  `RecoveryOutcome` (never through `_dispatch`, never through a loop this
  file owns) — `test_do_fresh_target_...`, `test_do_adjudicate_...`,
  `test_do_prove_catalog_...`, `test_do_start_product_image_...` below;
* `tests/architecture/
  test_deployment_foundation_recovery_execution_host_source_coverage.py`
  for STRUCTURAL proof, over the real `run()` method's own source, that
  `_verify_host_source` precedes `restore_plan` and the dispatch loop —
  not a re-driven copy of any of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, StepFailed
from dotmac_deployment_foundation.host_source import (
    ABSENT,
    DISAGREES,
    NO_RECEIPT,
    WRONG_KIND,
)
from dotmac_deployment_foundation.recovery import (
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

HOST_SOURCE_CODES = {ABSENT, WRONG_KIND, DISAGREES, NO_RECEIPT}

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

TARGET = RestoreTarget(identifier="recovery-1", major_version=16)


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


# ── individual handlers, called directly — real subjects, no reimplemented
# sequencing. Each test prepares the ONE piece of `RecoveryOutcome` state the
# handler under test needs and calls that handler alone; none of them drive
# `_dispatch`, a loop, or `restore_plan`. ──────────────────────────────────


def test_do_fresh_target_creates_from_the_manifests_postgres_major() -> None:
    """`_do_fresh_target` is step 1 and needs no prior state."""
    effects = RecordingRecoveryEffects()
    executor = _executor(effects)
    outcome = RecoveryOutcome()

    executor._do_fresh_target({}, outcome)

    assert outcome.target is not None
    assert effects.created == [_manifest().postgres_major], (
        "the major version must come from the BUNDLE/manifest — a restore "
        "across majors is a migration wearing a recovery's clothes"
    )


def test_do_adjudicate_destroys_a_partial_restore_before_anything_inspects_it() -> None:
    """THE refusal this module exists for, with the measured failure as input.

    A non-zero restore that left 45 tables, 23 policies and 16 RLS-enabled
    tables is not "failed, therefore nothing happened" — it is a database
    that will pass a table count, a policy listing and an RLS check. The
    verdict is `adjudicate_restore`'s; performing it is `_do_adjudicate`'s.
    """
    effects = RecordingRecoveryEffects(attempt=VENDOR_CP_PARTIAL)
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET, attempt=VENDOR_CP_PARTIAL)

    with pytest.raises(StepFailed) as failed:
        executor._do_adjudicate({}, outcome)

    assert "destroyed" in str(failed.value)
    assert outcome.adjudication is not None
    assert outcome.adjudication.disposition is Disposition.DESTROY
    assert outcome.destroyed is True
    assert effects.destroyed == ["recovery-1"]
    assert effects.calls == ["destroy_target"], (
        "nothing else was called: the target is destroyed and NOTHING "
        "inspects it afterward, in the same call"
    )


def test_do_adjudicate_does_not_destroy_a_clean_exit() -> None:
    """The positive control. Without it, the refusal above is equally
    consistent with a handler that destroys every target it judges."""
    effects = RecordingRecoveryEffects()
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET, attempt=CLEAN)

    executor._do_adjudicate({}, outcome)

    assert outcome.destroyed is False
    assert effects.destroyed == []
    assert outcome.adjudication is not None
    assert outcome.adjudication.disposition is Disposition.PROCEED


def test_do_prove_catalog_stops_on_a_catalog_that_differs_from_the_bundle() -> None:
    """`verify_recovery` returns findings rather than raising, so an operator
    sees them all at once. `_do_prove_catalog` must still STOP: a recovery
    reported as proved with findings attached is one nobody reads the
    findings of."""
    lost_roles = CatalogEvidence(
        **{
            **{f: getattr(_evidence(), f) for f in _evidence().__dataclass_fields__},
            "roles": (),
        }
    )
    effects = RecordingRecoveryEffects(restored=lost_roles)
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET)

    with pytest.raises(StepFailed):
        executor._do_prove_catalog({}, outcome)

    assert outcome.findings, "the findings must be carried, not just the failure"


def test_do_prove_catalog_stays_silent_on_an_agreeing_catalog() -> None:
    """NEAR-MISS, MUST BE SILENT. The positive control for the stop above."""
    effects = RecordingRecoveryEffects()
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET)

    executor._do_prove_catalog({}, outcome)

    assert outcome.findings == ()


def test_do_start_product_image_fails_when_the_image_never_becomes_ready() -> None:
    """A database that restores and cannot run the application is a copy,
    not a recovery."""
    effects = RecordingRecoveryEffects(image_ready=False)
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET)

    with pytest.raises(StepFailed) as failed:
        executor._do_start_product_image({}, outcome)

    assert "did not become ready" in str(failed.value)


def test_do_start_product_image_succeeds_when_the_image_becomes_ready() -> None:
    """NEAR-MISS, MUST BE SILENT."""
    effects = RecordingRecoveryEffects(image_ready=True)
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET)

    executor._do_start_product_image({}, outcome)  # must not raise


# ── the host-source gate: SAFETY-ONLY, unconditional, no admit path ─────────


def test_a_bare_recovery_executor_refuses_by_default() -> None:
    """`RecoveryExecutor.run` calls `_verify_host_source` before its first
    effect, which always calls `require_host_source(receipt=None)` — there is
    no parameter through which a caller could supply anything else, so this
    always refuses, in every environment."""
    effects = RecordingRecoveryEffects()
    executor = _executor(effects)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code in HOST_SOURCE_CODES, (
        f"refused with {refusal.value.code!r}, not one of the host-source "
        f"codes {HOST_SOURCE_CODES}"
    )
    assert (
        effects.calls == []
    ), f"the gate did not fire before the first effect: {effects.calls}"


def test_recovery_executor_init_accepts_no_host_source_parameter() -> None:
    """THE NEGATIVE CONTROL the independent review demanded, this class's
    half. There is no `host_source_metadata`/`host_source_receipts_dir`/
    `host_source_receipt` parameter left on `RecoveryExecutor.__init__` at
    all — supplying any of them, even ones a caller believes agree with each
    other, is not silently ignored, it is a `TypeError` at construction."""
    with pytest.raises(TypeError):
        RecoveryExecutor(  # type: ignore[call-arg]
            _spec(),
            _manifest(),
            RecordingRecoveryEffects(),
            source_evidence=_evidence(),
            product_image=IMAGE,
            host_source_metadata=object(),
        )


def test_the_verification_call_happens_exactly_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """NON-VACUITY, on the refusal path — the only path that exists. A
    `require_host_source` that is imported but never CALLED would let the
    refusal test above fail for an unrelated reason and let this one pass by
    accident."""
    from unittest.mock import MagicMock

    import dotmac_deployment_foundation.recovery_execution as recovery_execution_module

    real = recovery_execution_module.require_host_source
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(recovery_execution_module, "require_host_source", spy)

    effects = RecordingRecoveryEffects()
    with pytest.raises(PreconditionFailed):
        _executor(effects).run(bundle={})

    assert spy.call_count == 1, (
        f"require_host_source was called {spy.call_count} time(s), not " "exactly once"
    )
    assert effects.calls == []


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


# ── the evidence shape ────────────────────────────────────────────────────


def test_the_outcome_records_what_happened_including_a_destruction() -> None:
    """Built from a manually-driven `_do_adjudicate` call (see above), not a
    full run — `.as_evidence()` is a pure function of `RecoveryOutcome`'s own
    fields and is exercised as such."""
    effects = RecordingRecoveryEffects(attempt=VENDOR_CP_PARTIAL)
    executor = _executor(effects)
    outcome = RecoveryOutcome(target=TARGET, attempt=VENDOR_CP_PARTIAL)
    with pytest.raises(StepFailed):
        executor._do_adjudicate({}, outcome)

    evidence = outcome.as_evidence()
    assert evidence["schema"] == "RecoveryExecution.v1"
    assert evidence["disposition"] == "destroy"
    assert evidence["destroyed"] is True
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
