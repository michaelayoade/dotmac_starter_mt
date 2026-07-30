"""Conformance tests for the `ProvisioningProvider` contract (kernel-boundary
Task 3, ruling C6).

These prove the *contract* — a minimal in-test fake satisfies the Protocol, the
result dataclasses are frozen and carry the documented fields, the error
hierarchy classifies retryable vs terminal correctly, and the
idempotency / partial-result / cancellation semantics hold on the fake. This is
NOT the full `FakeProvisioningProvider` test-kit (that is Task 5's
`dotmac_kernel.testing`); the fake here is the smallest thing that exercises the
contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Mapping

import pytest
from dotmac_kernel.providers.provisioning import (
    ApplyResult,
    ObserveResult,
    PlanResult,
    ProvisioningApplyError,
    ProvisioningCancelled,
    ProvisioningError,
    ProvisioningPlanError,
    ProvisioningProvider,
    ProvisioningRequest,
    ProvisioningRetryableError,
    ProvisioningStatus,
    ProvisioningStep,
    ProvisioningTerminalError,
    StepStatus,
)

# ── A minimal in-test fake (NOT the Task-5 kit) ─────────────────────────────


def _plan_hash(spec: Mapping[str, object]) -> str:
    canonical = repr(sorted((k, repr(v)) for k, v in spec.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class _FakeProvisioner:
    """Structural implementation of `ProvisioningProvider`.

    ``spec`` knobs (all product-neutral, opaque to the kernel):
    - ``steps``: list of step ids to converge.
    - ``converge``: ``"all"`` (default) succeeds every step; an int N succeeds
      the first N and leaves the rest PENDING → a PARTIAL result.
    - ``bad``: truthy → ``plan`` raises ``ProvisioningPlanError``.
    """

    def __init__(self) -> None:
        self._ops: dict[str, ApplyResult] = {}
        self._cancelled: set[str] = set()

    def _op_id(self, request: ProvisioningRequest, plan_hash: str) -> str:
        return request.operation_id or f"{request.intent_id}:{plan_hash}"

    def plan(self, request: ProvisioningRequest) -> PlanResult:
        if request.spec.get("bad"):
            raise ProvisioningPlanError("contradictory spec")
        steps = tuple(
            ProvisioningStep(step_id=str(s), status=StepStatus.PENDING)
            for s in request.spec.get("steps", [])
        )
        return PlanResult(
            intent_id=request.intent_id,
            plan_hash=_plan_hash(request.spec),
            steps=steps,
        )

    def apply(self, request: ProvisioningRequest) -> ApplyResult:
        plan = self.plan(request)
        op_id = self._op_id(request, plan.plan_hash)

        prior = self._ops.get(op_id)
        # Terminal idempotency: re-apply of a settled op is a no-op.
        if prior is not None and prior.is_terminal:
            return prior

        if op_id in self._cancelled:
            raise ProvisioningCancelled(op_id)

        converge = request.spec.get("converge", "all")
        limit = len(plan.steps) if converge == "all" else int(converge)
        settled = []
        for i, step in enumerate(plan.steps):
            if prior is not None:
                # Resume: keep already-settled steps, work the outstanding ones.
                already = next(
                    (s for s in prior.steps if s.step_id == step.step_id), step
                )
                if already.is_settled:
                    settled.append(already)
                    continue
            new_status = StepStatus.SUCCEEDED if i < limit else StepStatus.PENDING
            settled.append(dataclasses.replace(step, status=new_status))

        done = all(s.status is StepStatus.SUCCEEDED for s in settled)
        status = ProvisioningStatus.SUCCEEDED if done else ProvisioningStatus.PARTIAL
        result = ApplyResult(
            intent_id=request.intent_id,
            operation_id=op_id,
            plan_hash=plan.plan_hash,
            status=status,
            steps=tuple(settled),
        )
        self._ops[op_id] = result
        return result

    def observe(self, operation_id: str) -> ObserveResult:
        prior = self._ops.get(operation_id)
        if prior is None:
            raise ProvisioningError(f"unknown operation {operation_id}")
        return ObserveResult(
            intent_id=prior.intent_id,
            operation_id=prior.operation_id,
            status=prior.status,
            steps=prior.steps,
            plan_hash=prior.plan_hash,
        )

    def cancel(self, operation_id: str) -> ObserveResult:
        self._cancelled.add(operation_id)
        prior = self._ops.get(operation_id)
        if prior is not None and prior.is_terminal:
            return self.observe(operation_id)
        cancelled_steps = tuple(
            s if s.is_settled else dataclasses.replace(s, status=StepStatus.CANCELLED)
            for s in (prior.steps if prior is not None else ())
        )
        result = ObserveResult(
            intent_id=prior.intent_id if prior else "",
            operation_id=operation_id,
            status=ProvisioningStatus.CANCELLED,
            steps=cancelled_steps,
            plan_hash=prior.plan_hash if prior else None,
        )
        if prior is not None:
            self._ops[operation_id] = dataclasses.replace(
                prior, status=ProvisioningStatus.CANCELLED, steps=cancelled_steps
            )
        return result


# ── Protocol / type conformance ─────────────────────────────────────────────


def test_fake_satisfies_runtime_checkable_protocol() -> None:
    provider = _FakeProvisioner()
    assert isinstance(provider, ProvisioningProvider)


def test_missing_method_fails_the_protocol_check() -> None:
    class _Incomplete:
        def plan(self, request: ProvisioningRequest) -> PlanResult:  # pragma: no cover
            ...

        # no apply/observe/cancel

    assert not isinstance(_Incomplete(), ProvisioningProvider)


def test_static_type_binding_accepts_the_fake() -> None:
    # A `ProvisioningProvider`-typed slot binds the fake — the structural
    # (mypy-checked) conformance the seam exists for.
    provider: ProvisioningProvider = _FakeProvisioner()
    result = provider.plan(ProvisioningRequest(intent_id="i", spec={"steps": ["a"]}))
    assert isinstance(result, PlanResult)


# ── Result dataclasses are frozen and carry the documented fields ───────────


@pytest.mark.parametrize(
    "instance",
    [
        PlanResult(intent_id="i", plan_hash="h"),
        ApplyResult(
            intent_id="i",
            operation_id="o",
            plan_hash="h",
            status=ProvisioningStatus.SUCCEEDED,
        ),
        ObserveResult(
            intent_id="i", operation_id="o", status=ProvisioningStatus.PENDING
        ),
        ProvisioningStep(step_id="s"),
    ],
)
def test_result_types_are_frozen(instance: object) -> None:
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "mutated")


def test_result_types_carry_idempotency_and_resumption_fields() -> None:
    steps = (ProvisioningStep(step_id="a", status=StepStatus.SUCCEEDED),)
    apply = ApplyResult(
        intent_id="intent-1",
        operation_id="op-1",
        plan_hash="deadbeef",
        status=ProvisioningStatus.SUCCEEDED,
        steps=steps,
    )
    # operation_id + plan_hash are the idempotency/resume references.
    assert apply.operation_id == "op-1"
    assert apply.plan_hash == "deadbeef"
    assert apply.steps == steps
    assert apply.succeeded and apply.is_terminal and not apply.is_partial


def test_partial_result_breakdown_and_outstanding_steps() -> None:
    steps = (
        ProvisioningStep(step_id="a", status=StepStatus.SUCCEEDED),
        ProvisioningStep(step_id="b", status=StepStatus.PENDING),
        ProvisioningStep(step_id="c", status=StepStatus.FAILED),
    )
    apply = ApplyResult(
        intent_id="i",
        operation_id="o",
        plan_hash="h",
        status=ProvisioningStatus.PARTIAL,
        steps=steps,
    )
    assert apply.is_partial and not apply.is_terminal
    # only the settled (SUCCEEDED/SKIPPED/CANCELLED) step drops out.
    assert {s.step_id for s in apply.outstanding_steps} == {"b", "c"}


def test_plan_result_noop_when_no_steps() -> None:
    assert PlanResult(intent_id="i", plan_hash="h").is_noop
    assert not PlanResult(
        intent_id="i", plan_hash="h", steps=(ProvisioningStep(step_id="a"),)
    ).is_noop


# ── Error hierarchy is well-formed (retryable vs terminal) ──────────────────


def test_error_hierarchy_subclassing() -> None:
    for cls in (
        ProvisioningRetryableError,
        ProvisioningTerminalError,
        ProvisioningCancelled,
    ):
        assert issubclass(cls, ProvisioningError)
    for cls in (ProvisioningPlanError, ProvisioningApplyError):
        assert issubclass(cls, ProvisioningTerminalError)


def test_retryable_vs_terminal_classification() -> None:
    assert ProvisioningRetryableError().retryable is True
    for cls in (
        ProvisioningTerminalError,
        ProvisioningPlanError,
        ProvisioningApplyError,
        ProvisioningCancelled,
    ):
        assert cls().retryable is False
    # base default fails closed (unknown → terminal).
    assert ProvisioningError().retryable is False


def test_isinstance_and_retryable_attr_agree() -> None:
    retryable = ProvisioningRetryableError("throttled")
    terminal = ProvisioningApplyError("bad state")
    assert retryable.retryable is isinstance(retryable, ProvisioningRetryableError)
    assert terminal.retryable is isinstance(terminal, ProvisioningRetryableError)


# ── Semantics exercised on the fake ─────────────────────────────────────────


def test_apply_converges_and_is_idempotent_when_terminal() -> None:
    provider = _FakeProvisioner()
    request = ProvisioningRequest(
        intent_id="tenant-x", spec={"steps": ["dns", "tls"]}, operation_id="op-1"
    )
    first = provider.apply(request)
    assert first.succeeded and first.is_terminal
    # Re-apply of a terminal op is a no-op returning the SAME result.
    second = provider.apply(request)
    assert second == first


def test_derived_operation_id_is_stable() -> None:
    provider = _FakeProvisioner()
    request = ProvisioningRequest(intent_id="tenant-y", spec={"steps": ["a"]})
    first = provider.apply(request)
    # No operation_id supplied → derived stably; re-apply keys to same op (no-op).
    second = provider.apply(request)
    assert first.operation_id == second.operation_id
    assert second == first


def test_partial_apply_then_resume_reconciles_outstanding() -> None:
    provider = _FakeProvisioner()
    request = ProvisioningRequest(
        intent_id="tenant-z",
        spec={"steps": ["a", "b", "c"], "converge": 1},
        operation_id="op-partial",
    )
    partial = provider.apply(request)
    assert partial.is_partial
    assert {s.step_id for s in partial.outstanding_steps} == {"b", "c"}

    observed = provider.observe("op-partial")
    assert observed.status is ProvisioningStatus.PARTIAL

    # Resume: same operation_id, now allowed to converge everything.
    resumed = provider.apply(
        ProvisioningRequest(
            intent_id="tenant-z",
            spec={"steps": ["a", "b", "c"], "converge": "all"},
            operation_id="op-partial",
        )
    )
    assert resumed.succeeded and not resumed.outstanding_steps
    # the originally-settled step is preserved, not redone.
    assert any(s.step_id == "a" for s in resumed.steps)


def test_cooperative_cancel_settles_a_partial_op_to_cancelled() -> None:
    provider = _FakeProvisioner()
    request = ProvisioningRequest(
        intent_id="tenant-c",
        spec={"steps": ["a", "b"], "converge": 1},
        operation_id="op-cancel",
    )
    provider.apply(request)  # leaves a PARTIAL op
    snapshot = provider.cancel("op-cancel")
    assert snapshot.status is ProvisioningStatus.CANCELLED
    # CANCELLED is terminal: observe confirms it and re-apply is an idempotent
    # no-op returning the cancelled result (not a re-run).
    assert provider.observe("op-cancel").status is ProvisioningStatus.CANCELLED
    reapplied = provider.apply(request)
    assert reapplied.status is ProvisioningStatus.CANCELLED
    assert reapplied.is_terminal


def test_apply_of_a_precancelled_operation_raises_cancelled() -> None:
    # cancel() before any apply arms the cooperative-cancel signal; the
    # in-flight apply then observes it and raises ProvisioningCancelled.
    provider = _FakeProvisioner()
    provider.cancel("op-precancel")
    request = ProvisioningRequest(
        intent_id="tenant-c",
        spec={"steps": ["a"]},
        operation_id="op-precancel",
    )
    with pytest.raises(ProvisioningCancelled):
        provider.apply(request)


def test_plan_raises_terminal_error_on_bad_spec() -> None:
    provider = _FakeProvisioner()
    with pytest.raises(ProvisioningPlanError) as excinfo:
        provider.plan(ProvisioningRequest(intent_id="i", spec={"bad": True}))
    assert excinfo.value.retryable is False


def test_observe_unknown_operation_raises() -> None:
    provider = _FakeProvisioner()
    with pytest.raises(ProvisioningError):
        provider.observe("nope")
