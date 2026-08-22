"""The `ProvisioningProvider` seam — a product-neutral provisioning contract.

This is the ONE provider interface pulled into the kernel alpha ahead of the
general workstream-5 provider fill (ruling C6, kernel-boundary Task 3): the
vendor control plane's first executable slice needs a *shared* provisioning
contract so it never defines a local replacement. Everything here is
**product-neutral** — there are no cloud, deployment, or fleet specifics. A
concrete provider (a cloud runner, a device orchestrator, a fake) lives OUTSIDE
the kernel and implements this Protocol; the kernel owns only the shape of the
conversation.

The seam is a CONTRACT, not a runner (contracts-not-implementations, matching
`dotmac_kernel.middleware.rate_limit.RateLimitStore`): no executable state
machine ships here. The semantics live entirely in the Protocol's method
docstrings, the frozen result dataclasses, and the stable error hierarchy.

Lifecycle (each step product-neutral, keyed only on opaque identifiers):

1. ``plan(request) -> PlanResult`` — read-only. Diff an opaque desired-state
   ``spec`` against reality and return the ordered steps needed to converge,
   plus a **stable ``plan_hash``** (same spec ⇒ same hash). No side effects.
2. ``apply(request) -> ApplyResult`` — execute the plan. **Idempotent by
   ``operation_id``**: re-applying the same ``operation_id`` is a no-op that
   returns the prior result. May return a **PARTIAL** result (some steps done,
   others outstanding) — the ``operation_id`` is the resume token.
3. ``observe(operation_id) -> ObserveResult`` — read-only status of an
   operation; reconciles / confirms a partial apply without mutating.
4. ``cancel(operation_id) -> ObserveResult`` — **cooperative** cancellation:
   signal the operation to stop and return a snapshot. The operation settles to
   ``CANCELLED`` (confirm via ``observe``); an in-flight ``apply`` may raise
   ``ProvisioningCancelled``.

Semantics, encoded in the types:

- **Cancellation** — cooperative only. ``cancel()`` requests a stop; it never
  force-kills. The terminal outcome is ``ProvisioningStatus.CANCELLED`` (a
  result) and/or a raised ``ProvisioningCancelled`` (an error) from an
  in-flight ``apply``.
- **Retry vs terminal** — every error is classified by the hierarchy below and
  by its ``retryable`` class attribute. ``ProvisioningRetryableError`` (and
  subclasses) mean the SAME ``operation_id`` may be retried/resumed safely;
  ``ProvisioningTerminalError`` (incl. ``ProvisioningPlanError`` /
  ``ProvisioningApplyError``) means retrying the same operation will not
  succeed — the caller must change the spec or escalate.
- **Idempotency** — ``operation_id`` is the idempotency key. Re-``apply`` of a
  *terminal* ``operation_id`` (succeeded/failed/cancelled) is a no-op that
  returns the stored ``ApplyResult`` unchanged. When a caller omits the id, a
  provider MUST derive a stable one from ``(intent_id, plan_hash)`` so a retried
  apply keys to the same operation.
- **Partial results & resumption** — ``apply`` may return
  ``ProvisioningStatus.PARTIAL`` with a per-step breakdown. The operation is not
  terminal, so re-``apply`` of the same ``operation_id`` *resumes* it —
  converging the ``outstanding_steps`` rather than redoing settled ones — while
  ``observe`` reports progress without mutating. ``outstanding_steps`` names
  exactly what remains. (Terminal idempotency + partial resumption do not
  conflict: the terminal states are frozen, the partial state is resumable.)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

# ── Status vocabularies ─────────────────────────────────────────────────────


class ProvisioningStatus(str, Enum):
    """Status of a whole provisioning operation.

    ``PARTIAL`` is the load-bearing state: some steps converged, others remain,
    and the operation is *resumable* by re-applying the same ``operation_id``.
    ``SUCCEEDED``/``FAILED``/``CANCELLED`` are the three terminal states.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Status of a single planned step / resource within an operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = frozenset(
    {
        ProvisioningStatus.SUCCEEDED,
        ProvisioningStatus.FAILED,
        ProvisioningStatus.CANCELLED,
    }
)
_SETTLED_STEP_STATUSES = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


# ── Value types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProvisioningStep:
    """One unit of the plan (a resource, a phase). Product-neutral.

    ``step_id`` is stable within a plan so a resumed apply can address the same
    step. ``detail`` is a human-readable, product-neutral note (never a cloud
    resource ARN or a fleet host — an opaque string the provider owns).
    """

    step_id: str
    status: StepStatus = StepStatus.PENDING
    detail: str = ""

    @property
    def is_settled(self) -> bool:
        """True when the step needs no further work (succeeded/skipped/
        cancelled). A ``FAILED``/``PENDING``/``IN_PROGRESS`` step is outstanding.
        """
        return self.status in _SETTLED_STEP_STATUSES


@dataclass(frozen=True)
class ProvisioningRequest:
    """The opaque, product-neutral input to ``plan``/``apply``.

    - ``intent_id`` — the caller's stable identity for *what* is being
      provisioned (an opaque token; not a cloud/fleet identifier).
    - ``spec`` — the opaque desired-state description. The kernel never
      interprets it; a concrete provider does. Same ``spec`` ⇒ same
      ``plan_hash``.
    - ``operation_id`` — the idempotency / resume key for ``apply`` (see module
      docstring). ``plan`` ignores it. When ``None``, a provider derives a
      stable id from ``(intent_id, plan_hash)``.
    """

    intent_id: str
    spec: Mapping[str, object] = field(default_factory=dict)
    operation_id: str | None = None


@dataclass(frozen=True)
class PlanResult:
    """The outcome of ``plan`` — the ordered steps to converge, and the stable
    ``plan_hash`` that keys this desired state. ``steps`` are all ``PENDING`` at
    plan time; an empty ``steps`` means already-converged (a no-op apply)."""

    intent_id: str
    plan_hash: str
    steps: tuple[ProvisioningStep, ...] = ()

    @property
    def is_noop(self) -> bool:
        """True when nothing needs to change — applying is a no-op."""
        return not self.steps


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of ``apply``.

    Carries everything needed for idempotency and resumption: the
    ``operation_id`` (idempotency + resume key) and the ``plan_hash`` this apply
    executed, plus the per-step breakdown. A ``PARTIAL`` status with outstanding
    steps is an explicit, first-class result — not an error.
    """

    intent_id: str
    operation_id: str
    plan_hash: str
    status: ProvisioningStatus
    steps: tuple[ProvisioningStep, ...] = ()

    @property
    def is_terminal(self) -> bool:
        """True when the operation reached a terminal state (succeeded, failed,
        or cancelled) — no further apply/observe will change it."""
        return self.status in _TERMINAL_STATUSES

    @property
    def is_partial(self) -> bool:
        """True when the apply converged only some steps and is resumable by
        re-applying the same ``operation_id``."""
        return self.status is ProvisioningStatus.PARTIAL

    @property
    def succeeded(self) -> bool:
        return self.status is ProvisioningStatus.SUCCEEDED

    @property
    def outstanding_steps(self) -> tuple[ProvisioningStep, ...]:
        """The steps still needing work (not succeeded/skipped/cancelled) — what
        a resume must reconcile."""
        return tuple(step for step in self.steps if not step.is_settled)


@dataclass(frozen=True)
class ObserveResult:
    """The outcome of ``observe`` (and ``cancel``) — a read-only snapshot of an
    operation's current status and per-step breakdown. ``plan_hash`` is present
    when the provider can still associate the operation with its plan."""

    intent_id: str
    operation_id: str
    status: ProvisioningStatus
    steps: tuple[ProvisioningStep, ...] = ()
    plan_hash: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def outstanding_steps(self) -> tuple[ProvisioningStep, ...]:
        return tuple(step for step in self.steps if not step.is_settled)


# ── Error hierarchy (STABLE API — documented, not ad-hoc) ───────────────────


class ProvisioningError(Exception):
    """Base for every provisioning-provider error.

    ``retryable`` is the stable, machine-readable classification: ``False`` here
    (unknown/uncategorised errors are treated as terminal — fail closed). A
    caller may branch on ``err.retryable`` or on ``isinstance(err,
    ProvisioningRetryableError)``; both agree because the retryable subtrees set
    the attribute to match their type.
    """

    retryable: bool = False


class ProvisioningRetryableError(ProvisioningError):
    """A transient failure. Retrying / resuming the SAME ``operation_id`` is
    safe and may succeed (rate limit, transient dependency, timeout)."""

    retryable = True


class ProvisioningTerminalError(ProvisioningError):
    """A non-recoverable failure. Retrying the same operation will NOT succeed —
    the caller must change the spec/intent or escalate."""

    retryable = False


class ProvisioningPlanError(ProvisioningTerminalError):
    """``plan`` could not produce a valid plan — an invalid or contradictory
    ``spec``. Terminal: the same spec will fail identically."""


class ProvisioningApplyError(ProvisioningTerminalError):
    """``apply`` failed terminally partway through. The operation is left in a
    partial state that ``observe(operation_id)`` reveals; the failure itself is
    not resumable without operator intervention."""


class ProvisioningCancelled(ProvisioningError):
    """Raised by an in-flight ``apply`` that observed a cooperative
    ``cancel(operation_id)`` and stopped. It is an OUTCOME, not a failure to
    retry: ``retryable`` is ``False`` and the operation settles to
    ``ProvisioningStatus.CANCELLED``."""

    retryable = False


# ── The provider Protocol ───────────────────────────────────────────────────


@runtime_checkable
class ProvisioningProvider(Protocol):
    """The product-neutral provisioning seam.

    A concrete provider (kept OUTSIDE the kernel) implements these four methods.
    Consumers depend on this Protocol, never on a concrete class. See the module
    docstring for the full lifecycle and the cancellation / retry / idempotency
    / partial-result semantics; each method's contract is below.
    """

    def plan(self, request: ProvisioningRequest) -> PlanResult:
        """Compute the steps needed to reach ``request.spec``'s desired state.

        Read-only — no side effects. MUST return a stable ``plan_hash`` (same
        ``spec`` ⇒ same hash) so ``apply`` can key idempotency off it. Raises
        ``ProvisioningPlanError`` (terminal) for an invalid/contradictory spec,
        or ``ProvisioningRetryableError`` if the desired state could not be read
        transiently.
        """
        ...

    def apply(self, request: ProvisioningRequest) -> ApplyResult:
        """Execute the plan for ``request`` toward the desired state.

        **Idempotent by ``operation_id``**: a second ``apply`` of a *terminal*
        ``operation_id`` returns the prior ``ApplyResult`` unchanged (no-op).
        When ``request.operation_id`` is ``None`` the provider derives a stable
        id from ``(intent_id, plan_hash)``. May return a ``PARTIAL`` result;
        because that state is non-terminal, re-``apply`` of the same
        ``operation_id`` *resumes* it, converging the ``outstanding_steps``.
        Raises ``ProvisioningRetryableError`` for a transient
        failure (retry the same ``operation_id``), ``ProvisioningApplyError``
        for a terminal one, or ``ProvisioningCancelled`` if a cooperative
        cancel was observed mid-flight.
        """
        ...

    def observe(self, operation_id: str) -> ObserveResult:
        """Return a read-only snapshot of the operation's current status and
        per-step breakdown. Never mutates. This is how a caller reconciles a
        ``PARTIAL`` apply or confirms a terminal (``SUCCEEDED``/``FAILED``/
        ``CANCELLED``) outcome. An unknown ``operation_id`` raises
        ``ProvisioningError``."""
        ...

    def cancel(self, operation_id: str) -> ObserveResult:
        """Cooperatively request cancellation of an operation and return a
        snapshot. Cancellation is a signal, never a force-kill: the operation
        settles to ``ProvisioningStatus.CANCELLED`` (confirm via ``observe``),
        and an in-flight ``apply`` may raise ``ProvisioningCancelled``.
        Cancelling an already-terminal operation is a no-op returning its
        current snapshot."""
        ...


__all__ = [
    # protocol
    "ProvisioningProvider",
    # request / value types
    "ProvisioningRequest",
    "ProvisioningStep",
    # result types
    "PlanResult",
    "ApplyResult",
    "ObserveResult",
    # status vocabularies
    "ProvisioningStatus",
    "StepStatus",
    # error hierarchy
    "ProvisioningError",
    "ProvisioningRetryableError",
    "ProvisioningTerminalError",
    "ProvisioningPlanError",
    "ProvisioningApplyError",
    "ProvisioningCancelled",
]
