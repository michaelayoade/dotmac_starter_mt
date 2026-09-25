"""The admission-provider SEAM itself: sequencing, freshness, and shape.

`test_deployment_foundation_host_source_gate.py` proves the DEFAULT
(`RefusingHostSourceAdmissionProvider`) — no provider supplied, unconditional
refusal, in every environment. This file exercises the other half with a
synthetic ACCEPTING provider (`tests/unit/host_source_stance.py`'s
`AcceptingHostSourceAdmissionProvider`) to drive both `Executor` and
`RecoveryExecutor` past the gate for sequencing tests. The provider is not
trusted provenance. It is consulted FRESH on every mutating entry point rather
than cached, and an explicitly-supplied refusing provider
still refuses exactly like the default. The synthetic accepting provider only
bypasses provenance to exercise sequencing after the gate; it is not a trusted
positive path. It also proves the constructor and
evidence-shape properties every consumer of the seam depends on:
`admission_provider` is keyword-only on both classes with no receipt/envelope/
subject/metadata/trust-policy/verifier/root/expected-host/preverified-result
parameter anywhere near it, `HostSourceAdmissionProvider` is satisfied by both
shipped implementations under `isinstance`, the two new public names are on
the package surface, and the admission TRACE never reaches either executor's
persisted evidence document.
"""

from __future__ import annotations

import inspect

import pytest
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import (
    ABSENT,
    DISAGREES,
    NO_RECEIPT,
    WRONG_KIND,
)
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionProvider,
    HostSourceAdmissionTrace,
    RefusingHostSourceAdmissionProvider,
)
from dotmac_deployment_foundation.recovery_execution import RecoveryExecutor

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.host_source_stance import (
    AcceptingHostSourceAdmissionProvider,
    accepting_admission_provider,
    valid_host_source_kwargs,
)
from tests.unit.test_deployment_foundation_execution_binding import (
    _fixture,
    _grant,
    _plan_and_digest,
)
from tests.unit.test_deployment_foundation_failure_injection import (
    AcceptingVerifier,
    evidence_policy,
)
from tests.unit.test_deployment_foundation_recovery_bundle import (
    _evidence,
    _manifest,
    _spec,
)
from tests.unit.test_deployment_foundation_recovery_execution import (
    IMAGE as RECOVERY_IMAGE,
)
from tests.unit.test_deployment_foundation_recovery_execution import (
    RecordingRecoveryEffects,
)

HOST_SOURCE_CODES = {ABSENT, WRONG_KIND, DISAGREES, NO_RECEIPT}


# ── helpers ──────────────────────────────────────────────────────────────


def _deploy_executor(*, admission_provider):  # type: ignore[no-untyped-def]
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan=execution_plan),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        admission_provider=admission_provider,
    )
    return spec, plan, effects, executor


def _recovery_executor(*, admission_provider):  # type: ignore[no-untyped-def]
    effects = RecordingRecoveryEffects()
    executor = RecoveryExecutor(
        _spec(),
        _manifest(),
        effects,
        source_evidence=_evidence(),
        product_image=RECOVERY_IMAGE,
        admission_provider=admission_provider,
    )
    return effects, executor


# ── sequencing path: a synthetic provider exercises both executors ─────────


def test_a_valid_accepting_provider_admits_the_deployment_executor_past_the_gate() -> (
    None
):
    """The synthetic provider bypasses provenance to exercise `Executor.run`.
    The run either succeeds or fails on some LATER gate, but never on the
    host-source refusal that fires unconditionally against the default."""
    spec, plan, _effects, executor = _deploy_executor(
        admission_provider=accepting_admission_provider()
    )
    outcome = executor.run(plan, lock=held_lock(spec.product))
    assert outcome.succeeded, outcome.failure
    assert outcome.failed_step is None
    assert executor._host_source is not None
    assert executor._host_source_admission_trace is not None


def test_valid_host_source_kwargs_itself_admits_the_deployment_executor() -> None:
    """The shared fixture every reactivated test in the suite now uses,
    exercised directly rather than through another file's helper."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan=execution_plan),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    )
    outcome = executor.run(plan, lock=held_lock(spec.product))
    assert outcome.succeeded, outcome.failure


def test_a_valid_accepting_provider_admits_the_recovery_executor_past_the_gate() -> (
    None
):
    """A synthetic provider bypasses provenance so `RecoveryExecutor.run`
    reaches `_do_fresh_target` (step 1) and beyond — proved by observing the
    first effect fire, not merely by the absence of a host-source refusal."""
    effects, executor = _recovery_executor(
        admission_provider=accepting_admission_provider()
    )
    outcome = executor.run(bundle={})
    assert executor._host_source is not None
    assert executor._host_source_admission_trace is not None
    assert "create_fresh_target" in effects.calls, (
        "the provider admitted the executor but the real first effect was "
        f"never reached: {effects.calls}"
    )
    assert outcome.proved is True, outcome.failure


# ── freshness: the provider is consulted again on every mutating entry ─────


def test_the_deployment_executor_consults_the_provider_fresh_on_every_run() -> None:
    """ONE executor instance, a COUNTING provider: `.calls` must advance on
    every call to `.run()`, proving nothing is cached across invocations on
    the same instance. `_verify_host_source` runs before grant/plan checks,
    so the provider is consulted even when a later check refuses the second
    call for an unrelated reason (the plan/grant were authorized for a single
    execution)."""
    provider = accepting_admission_provider()
    spec, plan, _effects, executor = _deploy_executor(admission_provider=provider)

    executor.run(plan, lock=held_lock(spec.product))
    assert provider.calls == 1

    # A second call on the SAME executor instance. Whether it succeeds or
    # fails on some LATER gate (the plan/grant may not tolerate a repeat
    # execution) is irrelevant to this proof: `_verify_host_source` is the
    # second statement in `run()`, ahead of the grant/plan checks, so the
    # provider is consulted regardless of what happens afterward. The
    # exception itself is not the subject of this test -- only whether the
    # provider was consulted again -- so it is deliberately swallowed rather
    # than asserted on.
    try:
        executor.run(plan, lock=held_lock(spec.product))
    except Exception:  # noqa: S110 -- deliberately not this test's subject
        pass
    assert provider.calls == 2, (
        f"the provider was consulted {provider.calls} time(s) across two "
        "calls to run() on the SAME executor instance; it must be called "
        "again on every mutating entry point, never cached"
    )


def test_the_recovery_executor_consults_the_provider_fresh_on_every_run() -> None:
    """Same freshness proof, `RecoveryExecutor.run`'s half. `_verify_host_source`
    is the first statement in `run()`, ahead of `restore_plan` and the dispatch
    loop, so it fires on every call regardless of what the rest of the run does."""
    provider = accepting_admission_provider()
    _effects, executor = _recovery_executor(admission_provider=provider)

    executor.run(bundle={})
    assert provider.calls == 1

    # See the deployment-executor half of this proof above: the second
    # call's outcome is not this test's subject, only whether the provider
    # was consulted again.
    try:
        executor.run(bundle={})
    except Exception:  # noqa: S110 -- deliberately not this test's subject
        pass
    assert provider.calls == 2, (
        f"the provider was consulted {provider.calls} time(s) across two "
        "calls to run() on the SAME executor instance"
    )


# ── an explicitly-supplied refusing provider propagates unchanged ──────────


def test_an_explicit_refusing_provider_refuses_exactly_like_the_default() -> None:
    """`RefusingHostSourceAdmissionProvider` is the DEFAULT, but nothing about
    its refusal is special-cased to "no provider was supplied" — handing one
    over explicitly produces the identical typed refusal."""
    spec, plan, effects, executor = _deploy_executor(
        admission_provider=RefusingHostSourceAdmissionProvider()
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES
    assert effects.snapshot() == before, "a refusal must leave the world untouched"


def test_an_explicit_refusing_provider_refuses_the_recovery_executor_too() -> None:
    """`RecoveryExecutor._verify_host_source` runs before `RecoveryOutcome`
    even exists (see `run()`'s own body), so a refusal PROPAGATES — it is
    never caught into a returned outcome. Same shape as
    `test_a_bare_recovery_executor_refuses_by_default`
    (`test_deployment_foundation_recovery_execution.py`), proved here with an
    EXPLICITLY supplied refusing provider rather than the default."""
    effects, executor = _recovery_executor(
        admission_provider=RefusingHostSourceAdmissionProvider()
    )
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code in HOST_SOURCE_CODES
    assert (
        effects.calls == []
    ), f"the gate did not fire before the first effect: {effects.calls}"


# ── the Protocol is satisfied by both shipped implementations ──────────────


def test_the_refusing_provider_satisfies_the_runtime_checkable_protocol() -> None:
    assert isinstance(
        RefusingHostSourceAdmissionProvider(), HostSourceAdmissionProvider
    )


def test_the_accepting_test_provider_satisfies_the_runtime_checkable_protocol() -> None:
    assert isinstance(
        AcceptingHostSourceAdmissionProvider(), HostSourceAdmissionProvider
    )
    assert isinstance(accepting_admission_provider(), HostSourceAdmissionProvider)


# ── the two new public names are on the package surface ────────────────────


def test_the_two_new_names_are_exported_from_the_package_surface() -> None:
    import dotmac_deployment_foundation as facility

    for name in ("HostSourceAdmissionProvider", "RefusingHostSourceAdmissionProvider"):
        assert name in facility.__all__, name
        assert hasattr(facility, name), name


# ── constructor introspection: admission_provider is keyword-only, and no
# receipt/envelope/subject/metadata/trust-policy/verifier/root/expected-host/
# preverified-result parameter exists anywhere on either constructor ────────

_FORBIDDEN_NEAR_MISS_PARAMS = frozenset(
    {
        "receipt",
        "envelope",
        "subject",
        "metadata",
        "trust_policy",
        "verifier",
        "root",
        "expected_host",
        "expected_host_identity",
        "preverified_result",
        "host_source_receipt",
        "host_source_metadata",
        "host_source_installed",
        "host_source_probe",
        "host_source_receipts_dir",
    }
)


def test_admission_provider_is_keyword_only_on_the_deployment_executor() -> None:
    signature = inspect.signature(Executor.__init__)
    parameter = signature.parameters["admission_provider"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    names = set(signature.parameters) - {"self"}
    present = names & _FORBIDDEN_NEAR_MISS_PARAMS
    assert present == set(), (
        f"Executor.__init__ carries {sorted(present)} alongside "
        "admission_provider — a parsing/attestation ingredient reintroduced "
        "next to the provider seam is the same bypass shape closed before"
    )


def test_admission_provider_is_keyword_only_on_the_recovery_executor() -> None:
    signature = inspect.signature(RecoveryExecutor.__init__)
    parameter = signature.parameters["admission_provider"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    names = set(signature.parameters) - {"self"}
    present = names & _FORBIDDEN_NEAR_MISS_PARAMS
    assert present == set(), (
        f"RecoveryExecutor.__init__ carries {sorted(present)} alongside "
        "admission_provider"
    )


# ── the trace never reaches either executor's persisted evidence document ──


def test_the_admission_trace_is_absent_from_the_deployment_evidence_document() -> None:
    spec, plan, _effects, executor = _deploy_executor(
        admission_provider=accepting_admission_provider()
    )
    outcome = executor.run(plan, lock=held_lock(spec.product))
    assert outcome.succeeded, outcome.failure
    assert executor._host_source_admission_trace is not None

    document = outcome.as_evidence()
    trace_field_names = set(HostSourceAdmissionTrace.__dataclass_fields__)
    leaked = trace_field_names & set(document)
    assert leaked == set(), (
        f"the admission trace's own field names leaked into the deployment "
        f"evidence document: {leaked}. Nothing in this package's evidence "
        "mapping consumes the trace yet, and the document must not carry it "
        "by accident"
    )


def test_the_admission_trace_is_absent_from_the_recovery_evidence_document() -> None:
    _effects, executor = _recovery_executor(
        admission_provider=accepting_admission_provider()
    )
    outcome = executor.run(bundle={})
    assert executor._host_source_admission_trace is not None

    document = outcome.as_evidence()
    trace_field_names = set(HostSourceAdmissionTrace.__dataclass_fields__)
    leaked = trace_field_names & set(document)
    assert leaked == set(), (
        f"the admission trace's own field names leaked into the recovery "
        f"evidence document: {leaked}"
    )
