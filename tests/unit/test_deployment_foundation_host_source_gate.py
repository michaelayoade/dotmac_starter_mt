"""The executor calls `require_host_source` ITSELF, and can never be admitted.

## The ruling this file proves (Michael, verbatim), UPDATED after `541cee5d`

An independent review at exact head `541cee5d` found the prior shape of this
file — and the `Executor`/`RecoveryExecutor` constructors it tested —
respelled the bypass rather than closing it: `host_source_receipt` (a plain
frozen `CandidateReceipt`, constructible by anyone) paired with
`host_source_metadata` (an `InstalledMetadata` whose `read_text(...)` returns
a caller-chosen string) let a caller state the SAME digest on both sides of
`require_host_source`'s one comparison — byte-for-byte the admission
`host_source_installed=` originally produced, with a `json.dumps` in between.
`valid_host_source_kwargs()` (`tests/unit/host_source_stance.py`) WAS that
exploit, shipped as a fixture every "admitted executor" test in this file
used.

Michael's ruling, reshaping this into a SAFETY-ONLY file:

* `Executor.__init__`/`RecoveryExecutor.__init__` accept **no host-source
  parameter of any kind** — no receipt, no metadata, no directory. There is
  nothing left to construct an "admitted" executor with, and this file no
  longer tries to.
* `_verify_host_source` always calls `require_host_source(receipt=None)`,
  which always refuses — a typed refusal (`ABSENT`/`WRONG_KIND`/
  `NO_RECEIPT`) with zero effects, in EVERY environment.
* Verification ordering (after the lock, before the grant, before every
  step) is PRESERVED and re-proved below — on the refusal path, since that
  is now the only path that exists.
* A negative control proves that even a caller holding the exact exploit
  shape (a `CandidateReceipt` and an `InstalledMetadata` that mutually
  agree) cannot get it admitted: there is no parameter to hand either one
  to, and the executor that IS constructible (knowing nothing of either)
  still refuses.

## What this file no longer proves, and why — coverage lost, named plainly

There is **no admit control** in this file any more. The prior "composition
admit proof" (`test_valid_host_source_and_valid_authorization_proceeds_past_
the_gate`) drove `Executor.run` past the gate using the exploit shape above
and observed the `deployment.start` annotation. That composition — "the gate
actually admits something, rather than refusing unconditionally" — is
UNREACHABLE through `Executor`/`RecoveryExecutor` today, by design, and
proving it would require re-adding a caller-suppliable admission path. The
only place that composition can still be proved honestly is
`test_deployment_foundation_host_source.py::
test_a_genuine_artifact_matching_its_receipt_is_accepted`, which calls
`require_host_source` directly rather than through an executor — that
function's own admit control is untouched by this reshape and remains the
proof that `require_host_source` itself is not "refuses everything".

Everything downstream of the gate that other test files used to reach by
building an "admitted" `Executor` (the authorization cross-matrix, failure
injection, principal bootstrap, deployment evidence, lock capability,
external recovery) is likewise unreachable through this seam now — see
`tests/unit/host_source_stance.py` for the full account and
`tests/unit/test_deployment_foundation_execution_binding.py` and its siblings
for where those tests now skip, naming this file as the reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import dotmac_deployment_foundation.engine.run as run_module
import pytest
from dotmac_deployment_foundation.authorization import ExecutionGrant
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import (
    ABSENT,
    DISAGREES,
    NO_RECEIPT,
    WRONG_KIND,
    require_host_source,
)

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.test_deployment_foundation_execution_binding import (
    _assert_untouched,
    _fixture,
    _grant,
    _plan_and_digest,
)
from tests.unit.test_deployment_foundation_host_source import FakeInstall, _receipt

HOST_SOURCE_CODES = {ABSENT, WRONG_KIND, DISAGREES, NO_RECEIPT}


# ── 1. constructing an Executor directly cannot bypass the gate ────────────


def test_a_bare_executor_refuses_by_default() -> None:
    """A caller that builds an `Executor` and supplies nothing about host
    source gets the REAL default reader — no fake, no override, because none
    exists — and it refuses, because nothing in this sandbox (or, per
    `pyproject.toml`'s `develop = true`, in this repo's own CI unit-test job)
    is a non-editable install with a receipt behind it.

    The refusal's CODE is asserted to be one of `host_source`'s own four,
    which is what proves this specific gate fired rather than some other
    refusal happening to come first.
    """
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES, (
        f"refused with {refusal.value.code!r}, not one of the host-source "
        f"codes {HOST_SOURCE_CODES}. That would mean some OTHER gate fired "
        "first, which is precisely what this test exists to rule out"
    )
    _assert_untouched(effects, before)


def test_a_bare_executor_refuses_on_rollback_too() -> None:
    """The same hole, on the sharper path. Boundary 3 names rollback
    explicitly; a gate proven only on `run` would leave this one open."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(
        spec, plan, operation="rollback", effects=effects
    )
    executor = Executor(
        spec,
        effects,
        _grant(spec, operation="rollback", execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.rollback(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES
    _assert_untouched(effects, before)


# ── 2. the negative control the independent review demanded ────────────────


def test_mutually_agreeing_receipt_and_metadata_cannot_admit_the_executor() -> None:
    """THE NEGATIVE CONTROL. A caller holding the EXACT exploit shape — a
    `CandidateReceipt` and an `InstalledMetadata` that mutually agree, byte
    for byte, the shape `valid_host_source_kwargs()` used to build — has no
    constructor parameter left to hand either one to.

    First proved at the PURE-FUNCTION level: this really is an admitting
    pair, so the control below is not vacuous — it is not that the pair
    fails to agree, it is that `Executor` has nowhere to put it.
    """
    receipt = _receipt()
    metadata = FakeInstall()
    admitted = require_host_source(receipt=receipt, metadata=metadata)
    assert admitted is not None, (
        "the pair used below is not actually an agreeing one; the control "
        "would be vacuous"
    )

    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    grant = _grant(spec, execution_plan_digest=digest)

    with pytest.raises(TypeError):
        Executor(  # type: ignore[call-arg]
            spec,
            effects,
            grant,
            execution_plan=execution_plan,
            sleep=lambda _: None,
            host_source_receipt=receipt,
            host_source_metadata=metadata,
        )

    # The executor that IS constructible — built with no knowledge of either
    # value, because there is no parameter through which to supply them —
    # still refuses when run.
    executor = Executor(
        spec, effects, grant, execution_plan=execution_plan, sleep=lambda _: None
    )
    assert not hasattr(executor, "_host_source_receipt")
    assert not hasattr(executor, "_host_source_metadata")

    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES
    _assert_untouched(effects, before)


# ── 3. ordering, preserved on the refusal path ──────────────────────────────


def test_the_gate_fires_before_grant_revalidation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`_verify_host_source` must run, and refuse, before `self._grant.require`
    is ever called — proved by spying on `ExecutionGrant.require` (patched on
    the CLASS: the instance is a `frozen=True, slots=True` dataclass) and
    showing it is never reached, even though the grant itself is perfectly
    valid here."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )

    calls: list[str] = []
    real_require = ExecutionGrant.require

    def require_spy(self, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("grant")
        return real_require(self, **kwargs)

    monkeypatch.setattr(ExecutionGrant, "require", require_spy)

    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES
    assert calls == [], (
        f"ExecutionGrant.require was called ({calls}); the host-source gate "
        "must refuse before grant revalidation is ever reached"
    )


def test_the_verification_call_happens_exactly_once_on_the_refusal_path(
    monkeypatch,
) -> None:
    """NON-VACUITY. A `require_host_source` that is imported but never
    CALLED would let every refusal test in this file fail for an unrelated
    reason and let this one pass by accident. Proved on the refusal path,
    since that is the only path `Executor` has today."""
    real = run_module.require_host_source
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(run_module, "require_host_source", spy)

    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )

    with pytest.raises(PreconditionFailed):
        executor.run(plan, lock=held_lock(spec.product))

    assert spy.call_count == 1, (
        f"require_host_source was called {spy.call_count} time(s), not " "exactly once"
    )


def test_rollback_also_reaches_the_verification_call_on_the_refusal_path(
    monkeypatch,
) -> None:
    """The refusal-path admit control's rollback half."""
    real = run_module.require_host_source
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(run_module, "require_host_source", spy)

    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(
        spec, plan, operation="rollback", effects=effects
    )
    executor = Executor(
        spec,
        effects,
        _grant(spec, operation="rollback", execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )

    with pytest.raises(PreconditionFailed):
        executor.rollback(plan, lock=held_lock(spec.product))

    assert spy.call_count == 1
