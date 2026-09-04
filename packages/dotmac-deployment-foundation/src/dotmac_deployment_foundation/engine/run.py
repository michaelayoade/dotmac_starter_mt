"""The executor: turn a :class:`~.plan.DeploymentPlan` into effects.

The whole design rests on one seam. Every effect this package can have on the
world goes through :class:`Effects` — a Protocol with fourteen methods and no
implementation here. That is what makes the required failure-injection matrix
— a wrong digest, missing migration credentials, a failed backup, a corrupt
backup, a candidate that never becomes ready, a primary that fails after
handoff, an unhealthy worker, a stale scheduler, an invalid ingress config, an
unavailable collector, a source bind mount, a maintenance-required migration
attempted online — an ordinary unit test rather than a disposable-VM exercise.

`dotmac_sub`'s 880-line script is the extraction source and cannot do this. Its
gates are `if`-statements interleaved with `docker` invocations, so the only
way to observe a gate firing is to arrange the failure on a real host. Two of
its gates were in fact found to be wrong only in production
(`seabone-staging-dotmac-sub-deploy-landmines`): the 180-second health budget
that caused a false rollback, and the profile filter that prevented Beat from
being CREATED but not from CONTINUING TO RUN.

## What the executor is responsible for, and what it is not

It owns **order, refusal and evidence**. It does not own *how* to talk to
Docker, Postgres or Nginx — that is the `Effects` implementation, which lives
in a provider and can be swapped for a Kubernetes one without touching the
sequence.

## The rule about failure

Every step failure raises. Nothing is caught and continued, with exactly one
exception, which is stated rather than silent: `PRUNE_IMAGES` is housekeeping,
and failing an otherwise-healthy deployment because a retention sweep hit a
permission error would be worse than the debt it leaves. The original had the
same fail-open behaviour at `deploy.sh:831-836`; the difference is that here it
becomes a recorded note rather than an undocumented `|| true`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..authorization import ExecutionGrant
from ..backup import BackupRecord
from ..canonical_plan import EXECUTION_PLAN_WRONG_TYPE
from ..errors import DeploymentError, PreconditionFailed, StepFailed
from ..evidence import (
    SignatureVerifier,
    SignedEvidenceEnvelope,
    TrustPolicy,
    accept_release_evidence,
)
from ..execution_plan import (
    FoundationExecutionPlanV1,
    HostPrestateV1,
    require_execution_plan_digest,
)
from ..external_recovery import (
    accept_external_recovery_receipt,
    backup_record_from_receipt,
    require_restore_proof,
)
from ..spec import ProductDeploymentSpec
from ..telemetry import Annotation
from .plan import (
    DeploymentPlan,
    Step,
    StepKind,
    steps_for_rollback,
)
from .plan import (
    _ingress_roles as _plan_ingress_roles,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class RoleObservation:
    """What a role actually looks like right now.

    ``restarts`` is separate from ``running`` because a container that is
    running having restarted four times is a failed deployment that looks
    healthy at the instant you check it — Sub verifies `RestartCount == 0` for
    exactly this reason (`deploy.sh:232-272`).
    """

    code: str
    running: bool
    image_digest: str
    restarts: int
    source_mounted: bool = False


@dataclass(frozen=True, slots=True)
class BackupResult:
    dataset: str
    path: str
    size_bytes: int
    checksum: str
    checksum_algorithm: str


@runtime_checkable
class Effects(Protocol):
    """Everything the executor can do to the world.

    Every method either returns a fact or raises. None of them decide anything:
    the decision to fail is the executor's, from the fact returned, so the
    policy lives in one readable place instead of being distributed across
    fourteen implementations that might each interpret a failure differently.
    """

    # ── gates ──
    def image_present(self, reference: str) -> bool: ...

    def image_labels(self, reference: str) -> Mapping[str, str]: ...

    def release_evidence(self, revision: str) -> SignedEvidenceEnvelope | None:
        """The signed envelope for `revision`, parsed and TYPED — or None.

        Typed at the SEAM, deliberately. This was ``Mapping[str, str]``, and
        that type did not merely permit the corruption that made a4
        inadmissible — it required it: the envelope's `document` is a nested
        object, so every conforming provider had to stringify it, and the
        signature was then judged over a Python repr. `SignedEvidenceEnvelope`
        refuses a stringified document at construction, so a conforming
        implementation CANNOT corrupt it. None means "no evidence for this
        revision" and is refused downstream; an empty mapping used to mean
        that, and an empty mapping is also what a half-broken reader returns.
        """
        ...

    def manifest_digest(self, manifest_path: str) -> str: ...

    def observe_roles(self) -> Sequence[RoleObservation]: ...

    def working_tree_dirty(self) -> bool: ...

    def untracked_compose_overrides(self) -> Sequence[str]: ...

    def resolved_materials(self) -> Sequence[str]: ...

    # ── mutation ──
    def run_command(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        materials: Sequence[str] = (),
    ) -> CommandResult: ...

    def backup(self, dataset_code: str, *, timeout_seconds: int) -> BackupResult: ...

    def verify_backup(self, result: BackupResult) -> bool: ...

    # ── candidate-image injected work (item 7 of the a5 audit) ──
    #
    # Every method below takes the image the ENGINE says this deployment is
    # about, because each one used to run against whatever happened to be
    # lying around instead: `migration_preflight` and `migrate` were bare
    # host-shell commands with no image concept at all — `alembic` from the
    # host PATH migrating a schema the CANDIDATE image's code owns — and
    # `verify_heads`/`start_candidate` used the on-disk compose file, which
    # still pins the PREVIOUS image until `switch` re-renders it at step
    # sixteen. A migration run by the old code against the new release's
    # schema expectations is the exact drift `require_prerequisites` exists
    # to catch one layer down, reintroduced by the executor itself.
    def run_migration_command(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        materials: Sequence[str] = (),
        image: str,
    ) -> CommandResult: ...

    def migration_heads(self, *, image: str) -> Sequence[str]: ...

    def stop_roles(self, roles: Sequence[str], *, timeout_seconds: int) -> None: ...

    def start_candidate(
        self, role: str, *, timeout_seconds: int, image: str
    ) -> str: ...

    def role_ready(self, role: str) -> bool: ...

    def candidate_ready(self, role: str) -> bool: ...

    def switch(self, *, timeout_seconds: int, image: str) -> None: ...

    def worker_responds(self, role: str) -> bool: ...

    def scheduler_last_tick_age_seconds(self, role: str) -> int | None: ...

    def write_evidence(self, evidence: Mapping[str, object]) -> str: ...

    def read_evidence(self, path: str) -> Mapping[str, object]: ...

    def prune_images(self, *, retain: int) -> None: ...

    def emit_annotation(self, annotation: Mapping[str, str]) -> None: ...


@dataclass(slots=True)
class StepRecord:
    kind: StepKind
    target: str
    ok: bool
    detail: str
    duration_seconds: float


@dataclass(slots=True)
class DeploymentOutcome:
    """What happened, in enough detail to be the evidence artefact."""

    plan: DeploymentPlan
    records: list[StepRecord] = field(default_factory=list)
    succeeded: bool = False
    failed_step: StepKind | None = None
    failure: str = ""
    mutated: bool = False
    notes: list[str] = field(default_factory=list)
    evidence_path: str = ""
    #: `ExecutionPlanDigestV1` and the operation, carried onto the report so the
    #: thing that ran is recognisable as the thing Control froze. Empty when the
    #: run was not bound to an authorized execution plan -- and empty is REPORTED
    #: rather than defaulted to something plausible, because a report that
    #: invents a digest is worse evidence than one that says it has none.
    execution_plan_digest: str = ""
    #: The other two terms, persisted under their OWN names. The outcome used to
    #: carry the execution-plan digest alone (always empty, see `Executor`), no
    #: descriptor digest at all, and no name for Control's plan digest — so the
    #: evidence could not distinguish three measurements that must never be
    #: conflated. `provenance.py` explains why they are separate.
    descriptor_digest: str = ""
    control_plan_digest: str = ""
    #: Control's replay coordinate, echoed onto the report so Control can place
    #: this execution against its target's high-water mark. Zero means "not
    #: carried", which `authorize()` makes unreachable on a real run.
    execution_sequence: int = 0
    attempt_no: int = 0
    operation: str = ""

    def as_evidence(self) -> dict[str, object]:
        return {
            "product": self.plan.product,
            "image": self.plan.image,
            "image_digest": self.plan.image_digest,
            "source_revision": self.plan.source_revision,
            "manifest_digest": self.plan.manifest_digest,
            "execution_plan_digest": self.execution_plan_digest,
            "descriptor_digest": self.descriptor_digest,
            "control_plan_digest": self.control_plan_digest,
            "execution_sequence": int(self.execution_sequence),
            "attempt_no": int(self.attempt_no),
            "operation": self.operation,
            "strategy": self.plan.strategy.value,
            "succeeded": self.succeeded,
            "mutated": self.mutated,
            "failed_step": self.failed_step.value if self.failed_step else None,
            "failure": self.failure,
            "rollback_permitted": self.plan.rollback_permitted,
            "rollback_reason": self.plan.rollback_reason,
            "steps": [
                {
                    "kind": record.kind.value,
                    "target": record.target,
                    "ok": record.ok,
                    "detail": record.detail,
                    "duration_seconds": round(record.duration_seconds, 3),
                }
                for record in self.records
            ],
            "notes": list(self.notes),
        }


class Executor:
    """Runs a plan against an :class:`Effects` implementation.

    ``sleep`` is injected so the stability window is instantaneous in a test.
    A test that genuinely waits 120 seconds is a test nobody runs.
    """

    def __init__(
        self,
        spec: ProductDeploymentSpec,
        effects: Effects,
        grant: ExecutionGrant,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deployment_id: str = "",
        evidence_policy: TrustPolicy | None = None,
        evidence_verifier: SignatureVerifier | None = None,
        recovery_receipts: Mapping[str, object] | None = None,
        recovery_verifier: SignatureVerifier | None = None,
        recovery_records: Mapping[str, Sequence[BackupRecord]] | None = None,
        now_epoch: int = 0,
        execution_plan: FoundationExecutionPlanV1,
    ) -> None:
        """`grant` is positional and required — that is the whole point.

        An `Executor` is the only thing in this facility that mutates a host,
        so it is where authorization has to be unavoidable. Making the grant a
        required POSITIONAL parameter means a caller cannot forget it, cannot
        default it, and cannot be given one by a helper that quietly passes
        `None`. A caller with no grant has nothing to construct this with, and
        `ExecutionGrant` cannot be built outside `authorize()` — so "execute on
        a flag" stops being expressible rather than merely being discouraged.
        """
        self._spec = spec
        self._effects = effects
        self._grant = grant
        # Both default to None and BOTH refuse at the point of use rather than
        # at construction. A missing policy must not be discovered only on the
        # host that had one — but it also must not stop a `plan` or a dry run,
        # neither of which verifies anything.
        self._evidence_policy = evidence_policy
        self._evidence_verifier = evidence_verifier
        # HANDED OVER, never discovered. There is no directory scan and no
        # `Effects.find_receipt()`: the caller passes the exact envelope for
        # each dataset, exactly as `--authorization` passes an
        # `AuthorizationReceipt`. Ambient discovery is how a stale receipt from
        # a previous quarter comes to satisfy today's gate, and a facility that
        # goes looking cannot tell "no proof exists" from "no proof was offered".
        self._recovery_receipts: Mapping[str, object] = recovery_receipts or {}
        self._recovery_verifier = recovery_verifier
        # Records the deployment already holds for this dataset. The accepted
        # receipt is APPENDED to them rather than replacing them, so a fresh
        # receipt cannot make an otherwise-overdue history look current by being
        # the only thing in the list.
        self._recovery_records: Mapping[str, Sequence[BackupRecord]] = (
            recovery_records or {}
        )
        self._now_epoch = now_epoch
        # THE MIDDLE TERM, and it is now unavoidable in both halves.
        #
        # `execution_plan` used to default to None and the authorized digest to
        # "", with `_require_execution_plan` PERMITTING "absent on both sides"
        # and returning "". `cmd_deploy` passed neither and there was no flag to
        # supply one, so every real deployment took that branch, mutated a host,
        # and wrote `deploy-evidence.json` with an empty digest. The entire
        # `ExecutionPlanDigestV1` mechanism was built, tested, and unreachable
        # from the only path that touches a host.
        #
        # The plan is now a REQUIRED keyword argument with no default, and the
        # authorized digest is not a parameter at all — it is read from the
        # grant, which carries it from the receipt. So there is no way to
        # construct an executor bound to a plan nobody froze, and no way to
        # supply an authorized digest that did not come through attestation.
        # TYPE-CHECKED AT CONSTRUCTION, not at first attribute access.
        # `RecoveryExecutionPlanV1` exists and carries no `operation` field, so
        # a swapped plan would previously have travelled all the way to
        # `_require_execution_plan` and died on `AttributeError: 'Recovery...'
        # object has no attribute 'operation'` — after the executor had been
        # built and while an operator reads a traceback rather than a refusal.
        # The two plan kinds are not interchangeable at any acceptance point,
        # and this is one of the three.
        #
        # `None` is deliberately NOT refused here, and the exclusion is the
        # opposite of a loophole. Absent-plan is an EXISTING, separately proven
        # refusal that `_require_execution_plan` makes with its own precise
        # sentence — "this executor has no execution plan, so nothing can be
        # recomputed and nothing was frozen" — and
        # `test_absent_execution_plan_produces_zero_effects` is the regression
        # test for the branch that used to let exactly that state mutate a host.
        # Swallowing `None` here would answer that case with "you passed a
        # NoneType, not a FoundationExecutionPlanV1", which is true, useless,
        # and replaces a diagnosis with a type name. Two different faults, two
        # different sentences, and the wrong-type one must not eat the other.
        if execution_plan is not None and not isinstance(
            execution_plan, FoundationExecutionPlanV1
        ):
            raise PreconditionFailed(
                f"Executor was given a {type(execution_plan).__name__} as its "
                f"execution plan, not a {FoundationExecutionPlanV1.__name__}. "
                "This executor mutates a product host under a deployment "
                "authorization, and a plan of another kind describes another "
                "act that nothing this class holds authorizes",
                code=EXECUTION_PLAN_WRONG_TYPE,
            )
        self._execution_plan = execution_plan
        self._sleep = sleep
        self._clock = clock
        self._rolling_back = False
        # Identifies this RUN, not this release: two deployments of one digest
        # to one host are two deployments, and an operator asking whether the
        # second one fixed it needs them apart.
        self._deployment_id = deployment_id or "unset"
        self._notes_sink: list[str] = []
        # The real BackupResult per dataset, kept as a value rather than
        # reconstructed from a formatted note. See `_do_verify_backup`.
        self._backups: dict[str, BackupResult] = {}

    # ── entry point ─────────────────────────────────────────────────────────

    def _descriptor_digest(self) -> str:
        """This run's descriptor digest, in the canonical spelling.

        Derived from the spec rather than taken from the caller: a digest the
        caller supplies alongside the descriptor it is asking us to run proves
        only that the caller can compute a digest.
        """
        return self._spec.to_canonical_document().sha256_digest()

    def run(self, plan: DeploymentPlan) -> DeploymentOutcome:
        # Re-checked here, not merely at construction: the grant names a
        # descriptor, and this asserts the plan in hand is that descriptor's.
        # A grant built early and used late is exactly where "nothing changed
        # in between" stops being safe to assume.
        self._grant.require(
            operation="deploy", descriptor_digest=self._descriptor_digest()
        )
        digest = self._require_execution_plan("deploy")
        outcome = DeploymentOutcome(
            plan=plan,
            notes=list(plan.notes),
            execution_plan_digest=digest,
            # Each from the side that OWNS it: the descriptor digest re-derived
            # from the spec in hand, Control's plan digest copied verbatim off
            # the receipt and never recomputed here.
            descriptor_digest=self._descriptor_digest(),
            control_plan_digest=str(self._grant.receipt.control_plan_digest),
            execution_sequence=int(self._grant.execution_sequence),
            attempt_no=int(self._grant.attempt_no),
            operation="deploy",
        )
        # The first question about any graph that turned bad at 14:32 is what
        # changed at 14:32. No product in the fleet emits this today, and the
        # annotation is worth almost nothing after the fact — it has to be sent
        # BEFORE the work, or a deployment that dies mid-migration is the one
        # case with no marker at all.
        self._annotate("deployment.start", plan)
        try:
            self._run_steps(plan.steps, plan, outcome)
            outcome.succeeded = True
        except DeploymentError:
            # The failure is RETURNED on the outcome, not raised. A caller needs
            # the whole record — which steps ran, what mutated, where the gate
            # boundary was — and an exception carries only the last line of it.
            # `_run_steps` re-raises so that `rollback()` gets the same shape.
            pass
        finally:
            self._annotate(
                "deployment.success" if outcome.succeeded else "deployment.failure",
                plan,
                detail=outcome.failure,
            )
            outcome.notes.extend(self._notes_sink)
            self._notes_sink.clear()
            # Evidence is written on EVERY path, including the failing ones.
            # An earlier version made `record_evidence` an ordinary step near the
            # end of the plan, so a deployment that died at `migrate` — the case
            # where an operator most needs to know what ran — left no record at
            # all. Evidence that exists only on success is evidence that is
            # absent exactly when it is wanted.
            self._persist_evidence(outcome)
        return outcome

    def _require_execution_plan(self, operation: str) -> str:
        """Step 4: RECOMPUTE the plan digest before executing. Returns it.

        Nothing here reconstructs Control's document and nothing normalizes it.
        Control froze a digest the Foundation produced; this re-derives that
        same digest from the plan actually in hand, so a mismatch means the plan
        CHANGED rather than that two canonicalizers disagreed. That distinction
        is the whole repair: while Control hashed the spec wrapped in six
        sibling keys and the Foundation hashed the descriptor alone, a mismatch
        was unavoidable and told nobody anything.

        ABSENT IS NO LONGER PERMITTED. This used to return "" when neither a
        plan nor an authorized digest was present, on the reasoning that a run
        never bound to one must not have a digest invented for its report. The
        reasoning was sound and the consequence was not: the only production
        caller supplied neither, so "unbound" was not an unusual case being
        reported honestly, it was every deployment this facility has ever run.
        A branch that is only ever taken by the path it was meant to describe as
        exceptional is not a fallback, it is the behaviour.
        """
        if self._execution_plan is None:
            raise PreconditionFailed(
                "this executor has no execution plan, so nothing can be "
                "recomputed and nothing was frozen. An unbound executor is "
                "refused BEFORE any effect: the plan is what says what will be "
                "DONE to this target under this operation, and a descriptor "
                "approval alone does not say that"
            )
        authorized = self._grant.execution_plan_digest
        if not authorized:
            raise PreconditionFailed(
                "the grant carries no authorized execution plan digest, so "
                "nothing froze this plan. Rendering a plan is not the same as "
                "having it authorized, and running the unfrozen one is the gap "
                "this contract closes"
            )
        # THE HOST IS RE-OBSERVED, not remembered. The plan's prestate is what
        # the host looked like when the plan was rendered and authorized; this
        # is what it looks like NOW, at the moment before mutation. Between the
        # two, another deployment, a manual compose invocation or a rollback
        # can have moved the base state — and an authorized change applied to
        # an unreviewed starting point is not the change that was reviewed.
        # A read, deliberately BEFORE any effect, so the refusal leaves the
        # host byte-identical.
        observed = HostPrestateV1.from_observations(self._effects.observe_roles())
        if observed != self._execution_plan.host_prestate:
            authorized_state = self._execution_plan.host_prestate.as_document()
            raise PreconditionFailed(
                "the host is not the host that was authorized: the plan was "
                f"rendered against prestate {authorized_state} "
                f"and the target now observes {observed.as_document()}. "
                "Something deployed, rolled back or hand-mutated this target "
                "after authorization. Re-render the plan against the current "
                "state and have it re-authorized; executing anyway would apply "
                "a reviewed change to an unreviewed starting point"
            )
        if self._execution_plan.operation != operation:
            raise PreconditionFailed(
                f"the execution plan authorizes {self._execution_plan.operation!r} "
                f"and this is a {operation!r}. Deploy and rollback are frozen "
                "separately, for the same reason they are authorized separately: "
                "one decision must not both make a change and erase it"
            )
        return require_execution_plan_digest(
            self._execution_plan, authorized=authorized
        )

    def _run_steps(
        self,
        steps: Sequence[Step],
        plan: DeploymentPlan,
        outcome: DeploymentOutcome,
    ) -> None:
        for step in steps:
            started = self._clock()
            if step.mutates:
                # Claimed BEFORE the call, not after it. A step that fails
                # PARTWAY — a migration that applied three revisions of five, a
                # switch that recreated half the roles — has mutated the world,
                # and setting the flag only on success reported `mutated=False`
                # for precisely the failures where the answer decides whether an
                # operator may re-run the identical command.
                outcome.mutated = True
            try:
                detail = self._dispatch(step, plan, outcome)
            except DeploymentError as exc:
                outcome.records.append(
                    StepRecord(
                        step.kind, step.target, False, str(exc), self._clock() - started
                    )
                )
                outcome.failed_step = step.kind
                outcome.failure = str(exc)
                outcome.succeeded = False
                raise
            outcome.records.append(
                StepRecord(
                    step.kind, step.target, True, detail, self._clock() - started
                )
            )

    def _annotate(self, event: str, plan: DeploymentPlan, *, detail: str = "") -> None:
        """Send one deployment annotation, and never let it fail a deployment.

        A telemetry sink being unreachable is not a reason to refuse to deploy —
        ADR-0003 says failure or disabling of telemetry never blocks the
        product's request path, and the same reasoning applies to the deployment
        path. The failure is recorded as a note so it is visible without being
        fatal.
        """
        annotation = Annotation(
            event=event,
            product=self._spec.product,
            environment=self._spec.environment or "unknown",
            deployment_id=self._deployment_id,
            image_digest=plan.image_digest,
            git_sha=plan.source_revision,
            strategy=plan.strategy.value,
            detail=detail,
        )
        try:
            self._effects.emit_annotation(annotation.as_mapping())
        except Exception as exc:
            self._notes_sink.append(f"annotation {event!r} could not be sent: {exc}")

    def _persist_evidence(self, outcome: DeploymentOutcome) -> None:
        """Write the evidence artefact, and never let that failure mask another.

        A write failure here is recorded as a note rather than raised: losing the
        record of a deployment is bad, and replacing the reason it failed with
        "could not write the evidence file" is worse.
        """
        try:
            outcome.evidence_path = self._effects.write_evidence(outcome.as_evidence())
        except Exception as exc:
            outcome.notes.append(f"evidence could not be written: {exc}")

    def rollback(self, plan: DeploymentPlan) -> DeploymentOutcome:
        """Execute the rollback, or refuse it.

        `steps_for_rollback` returns the steps and nothing ran them, so
        `dotmac-deploy rollback` could only ever PRINT a plan. Here the steps
        actually execute, and `_rolling_back` makes `switch` target the previous
        digest rather than the deploying one — which the shared handler would
        otherwise have done, restoring the image that had just failed.
        """
        self._grant.require(
            operation="rollback", descriptor_digest=self._descriptor_digest()
        )
        outcome = DeploymentOutcome(
            plan=plan,
            notes=list(plan.notes),
            execution_plan_digest=self._require_execution_plan("rollback"),
            descriptor_digest=self._descriptor_digest(),
            control_plan_digest=str(self._grant.receipt.control_plan_digest),
            execution_sequence=int(self._grant.execution_sequence),
            attempt_no=int(self._grant.attempt_no),
            operation="rollback",
        )
        steps = steps_for_rollback(plan)
        if not steps:
            outcome.failure = plan.rollback_reason
            outcome.notes.append(f"ROLLBACK REFUSED — {plan.rollback_reason}")
            self._persist_evidence(outcome)
            raise PreconditionFailed(f"rollback refused: {plan.rollback_reason}")
        self._rolling_back = True
        self._annotate("deployment.rollback", plan, detail=plan.rollback_reason)
        try:
            self._run_steps(steps, plan, outcome)
            outcome.succeeded = True
        except DeploymentError:
            pass
        finally:
            self._rolling_back = False
            outcome.notes.extend(self._notes_sink)
            self._notes_sink.clear()
            self._persist_evidence(outcome)
        return outcome

    # ── dispatch ────────────────────────────────────────────────────────────

    def _dispatch(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        handler = getattr(self, f"_do_{step.kind.value}", None)
        if handler is None:
            # Not a silent skip. A step in the plan with no handler is a bug in
            # this file, and a deployment that quietly omits a gate is the worst
            # possible way to find out.
            raise StepFailed(step.kind.value, "no handler is implemented for this step")
        result: str = handler(step, plan, outcome)
        return result

    # ── gates: nothing below mutates ────────────────────────────────────────

    def _do_acquire_lock(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        # The lock is held by the CALLER around the whole run (`lock.py`), not
        # here: a lock taken inside the executor would be released the moment
        # this method returns, which is the beginning of the deployment rather
        # than the end of it.
        return "held by the caller for the duration of the run"

    def _do_verify_image(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        if not self._effects.image_present(plan.image):
            raise PreconditionFailed(
                f"the exact image {plan.image} is not present. Nothing is built "
                "here: build once, promote the digest. A missing digest means "
                "the release was never published or the reference is wrong"
            )
        return f"digest {plan.image_digest} present"

    def _do_verify_revision(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        labels = self._effects.image_labels(plan.image)
        recorded = labels.get("org.opencontainers.image.revision", "")
        if not recorded:
            raise PreconditionFailed(
                f"{plan.image} carries no org.opencontainers.image.revision "
                "label, so nothing connects the running bytes to a reviewable "
                "commit"
            )
        if recorded != plan.source_revision:
            raise PreconditionFailed(
                f"{plan.image} was built from {recorded}, but the descriptor "
                f"declares {plan.source_revision}. One of the two is stale, and "
                "guessing which would deploy an unreviewed tree"
            )
        return f"built from {recorded}"

    def _do_verify_manifest(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        observed = self._effects.manifest_digest(step.target)
        if not observed:
            raise PreconditionFailed(
                f"the product manifest {step.target!r} could not be read, so "
                "nothing establishes which module set this deployment runs. The "
                "image digest cannot see it: the same image composes whatever "
                "manifest it is given"
            )
        if observed != plan.manifest_digest:
            raise PreconditionFailed(
                f"the product manifest hashes to {observed}, the descriptor "
                f"declares {plan.manifest_digest}. Either the manifest was "
                "regenerated without updating the descriptor, or this "
                "deployment is about to run an approved image against a module "
                "set nobody approved"
            )
        return f"manifest {observed}"

    def _do_verify_release_evidence(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        """Evidence must be signed, current, and from a protected ref HERE.

        The previous version of this step checked that the evidence mapping was
        non-empty. That made "CI is the acceptance owner" satisfiable by any
        writable file: nothing established who wrote it, whether the run it
        describes happened, or whether it happened in this repository.
        """
        raw = self._effects.release_evidence(plan.source_revision)
        if raw is None:
            raise PreconditionFailed(
                f"no release evidence exists for revision {plan.source_revision}. "
                "CI is the acceptance owner; a deployment of a revision CI never "
                "accepted is a deployment of an unreviewed tree"
            )
        if not isinstance(raw, SignedEvidenceEnvelope):
            raise PreconditionFailed(
                "the Effects implementation returned release evidence as "
                f"{type(raw).__name__}, not as a SignedEvidenceEnvelope. The "
                "typed envelope is the contract that keeps the signed document "
                "un-restated between the file and the verifier; a loose "
                "mapping here is exactly the seam that stringified a4's "
                "envelopes"
            )
        if self._evidence_policy is None:
            raise PreconditionFailed(
                "release evidence was found but no TrustPolicy is configured, "
                "so there is nothing to judge it against. Refusing rather than "
                "falling back to 'a file exists': that fallback is what let an "
                "unsigned, unattributed file stand for CI acceptance"
            )
        evidence = accept_release_evidence(
            raw,
            revision=plan.source_revision,
            policy=self._evidence_policy,
            verifier=self._evidence_verifier,
        )
        return (
            f"run {evidence.run_id} of {evidence.workflow} on {evidence.ref} "
            f"({evidence.repository}), signed and not from a fork"
        )

    def _do_refuse_dirty_state(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        problems: list[str] = []
        if self._effects.working_tree_dirty():
            problems.append(
                "the deployment checkout has uncommitted changes, so what runs "
                "is not what any commit describes"
            )
        overrides = list(self._effects.untracked_compose_overrides())
        if overrides:
            problems.append(
                f"untracked compose override(s) {overrides} carry configuration "
                "nothing tracks. A host-only override is an undocumented manual "
                "step, and the first re-render reverts it"
            )
        mounted = [
            observation.code
            for observation in self._effects.observe_roles()
            if observation.source_mounted
        ]
        if mounted:
            problems.append(
                f"role(s) {mounted} bind-mount source into the container, so the "
                "running behaviour is the host checkout rather than the image "
                "digest that was reviewed"
            )
        if problems:
            raise PreconditionFailed("; ".join(problems))
        return "clean checkout, no untracked override, no source mount"

    def _do_verify_materials(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        resolved = set(self._effects.resolved_materials())
        required = set(self._spec.runtime_materials) | {
            self._spec.migration.owner_material
        }
        missing = sorted(required - resolved)
        if missing:
            raise PreconditionFailed(
                f"material(s) {missing} do not resolve. A container started with "
                "an empty credential fails later, further from the cause, and "
                "sometimes only under load"
            )
        # The second half is a static property of the descriptor and `spec.py`
        # already refuses it at parse time. It is re-checked here because the
        # descriptor on the host may not be the descriptor that was reviewed,
        # and this is the last moment before DDL at which that is cheap to see.
        for role in self._spec.roles:
            if self._spec.migration.owner_material in role.materials:
                raise PreconditionFailed(
                    f"role {role.code!r} holds the migration owner material "
                    f"{self._spec.migration.owner_material!r}"
                )
        return (
            f"{len(required)} material(s) resolve; "
            "owner material absent from every role"
        )

    def _do_verify_external_recovery_receipt(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        """Somebody else restored it. Prove it, bind it, and enforce the window.

        Three things had to be true before this step could exist and each was
        missing. The descriptor could not name the privilege verifications, so a
        receipt could only ever claim `schema` and `row_counts`. Nothing turned
        an external proof into a `BackupRecord`, so `restore_proved_at_epoch` was
        written by nothing in the package. And `backup.assess()` -- which
        computes the `restore_proof_max_age_days` window correctly -- had zero
        callers, so the window was inert. This is the caller.
        """
        dataset = next(
            (item for item in self._spec.backup_datasets if item.code == step.target),
            None,
        )
        if dataset is None or dataset.external_executor is None:
            raise StepFailed(
                step.kind.value,
                f"the plan asks for an external recovery receipt for "
                f"{step.target!r} and the descriptor declares no external "
                "executor for it. A plan and a descriptor that disagree about "
                "who performs recovery must not be resolved by guessing",
            )
        envelope = self._recovery_receipts.get(step.target)
        if envelope is None:
            raise PreconditionFailed(
                f"no recovery receipt was supplied for {step.target!r}. It is "
                "passed in, never discovered: this facility does not go looking, "
                "because a search cannot tell 'no proof exists' from 'no proof "
                "was offered' and will happily find last quarter's"
            )
        receipt = accept_external_recovery_receipt(
            envelope,
            identity=dataset.identity(self._spec.product),
            descriptor_digest=str(self._spec.to_canonical_document().sha256_digest()),
            executor=dataset.external_executor,
            required_verifications=dataset.verify,
            verifier=self._recovery_verifier,
        )
        records = [
            *self._recovery_records.get(step.target, ()),
            backup_record_from_receipt(
                receipt,
                path=f"external:{receipt.executor.identifier}",
                size_bytes=max(1, receipt.restore_duration_seconds),
            ),
        ]
        detail = require_restore_proof(
            self._spec,
            step.target,
            records,
            now_epoch=self._now_epoch or receipt.proved_at_epoch,
        )
        outcome.notes.append(
            f"recovery receipt {receipt.sha256_digest()} for {step.target}: "
            f"{detail}"
        )
        return (
            f"{receipt.executor.kind}:{receipt.executor.identifier}"
            f"@{receipt.executor.version} proved {list(receipt.verifications)} "
            f"on snapshot {receipt.snapshot_checksum_algorithm}="
            f"{receipt.snapshot_checksum}; {detail}"
        )

    def _do_product_preflight(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        return self._run_hook(step, "preflight")

    # ── mutation ────────────────────────────────────────────────────────────

    def _do_backup(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        result = self._effects.backup(step.target, timeout_seconds=step.timeout_seconds)
        if not result.checksum:
            raise StepFailed(
                step.kind.value,
                f"backup {step.target!r} returned no checksum. The checksum is "
                "recorded at WRITE time on purpose: computed later it proves only "
                "that the file has not changed since the verifier read it",
            )
        if result.size_bytes <= 0:
            raise StepFailed(
                step.kind.value,
                f"backup {step.target!r} reported {result.size_bytes} bytes",
            )
        self._backups[step.target] = result
        outcome.notes.append(
            f"backup {step.target}: {result.path} "
            f"({result.size_bytes} bytes, "
            f"{result.checksum_algorithm}={result.checksum})"
        )
        return f"{result.path} ({result.size_bytes} bytes)"

    def _do_verify_backup(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        result = self._backups.get(step.target)
        if result is None:
            raise StepFailed(
                step.kind.value,
                f"no backup was recorded for {step.target!r}, so there is nothing "
                "to verify. Verification of a backup that did not run must fail, "
                "not pass vacuously",
            )
        # The REAL result, carried as a value. An earlier version re-parsed it
        # out of a formatted note and rebuilt it with `size_bytes=0` and an
        # empty checksum — so the verifier was handed the two fields it exists
        # to check, blanked, and could only ever confirm the path.
        if not self._effects.verify_backup(result):
            raise StepFailed(
                step.kind.value,
                f"backup {step.target!r} did not verify. A clean exit is "
                "COMPLETION, not integrity: it cannot see corruption during the "
                "write, during transfer, or on disk since. Verification is a "
                "full decompression plus the checksum recorded at write time",
            )
        return f"{step.target} verified"

    def _do_migration_preflight(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        if not step.command:
            return "no preflight command is declared"
        # IN THE CANDIDATE IMAGE. This was `run_command` — the bare host
        # shell — so the preflight proved the HOST's alembic and the host's
        # code could talk to the database, about a release whose code it was
        # not running. The image is the plan's: the one the grant authorized.
        result = self._effects.run_migration_command(
            step.command,
            timeout_seconds=step.timeout_seconds,
            materials=[self._spec.migration.owner_material],
            image=plan.image,
        )
        if not result.ok:
            raise StepFailed(
                step.kind.value,
                "the migration identity, role posture or database ownership "
                f"contract is unsatisfied (exit {result.exit_code}): "
                f"{result.stderr.strip() or result.stdout.strip()}",
                exit_code=result.exit_code,
            )
        return "migration role verified"

    def _do_stop_for_maintenance(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        roles = list(self._spec.role_codes)
        self._effects.stop_roles(roles, timeout_seconds=step.timeout_seconds)
        return f"stopped {len(roles)} role(s) before DDL"

    def _do_migrate(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        last = ""
        for attempt in range(1, step.retries + 1):
            # IN THE CANDIDATE IMAGE — the migration is the candidate's code
            # changing the schema to what the candidate expects. Run by the
            # previous release's code (or the host's PATH), it is the drift
            # `require_prerequisites` exists to catch, reintroduced here.
            result = self._effects.run_migration_command(
                step.command,
                timeout_seconds=step.timeout_seconds,
                materials=[self._spec.migration.owner_material],
                image=plan.image,
            )
            if result.ok:
                return f"migrated on attempt {attempt}"
            last = (result.stderr or result.stdout).strip()
            if not _is_lock_contention(last):
                # Narrow on purpose, ported from `deploy.sh:378-400`. A blanket
                # retry runs a genuinely failing migration `retries` times, and
                # a migration that fails halfway and is retried is the shape
                # that leaves a schema nobody can describe.
                raise StepFailed(
                    step.kind.value,
                    f"migration failed and the failure is not lock contention, "
                    f"so retrying would re-run a failing migration: {last}",
                    exit_code=result.exit_code,
                )
            if attempt < step.retries:
                self._sleep(min(10 * attempt, 60))
        raise StepFailed(
            step.kind.value,
            f"migration lock contention persisted across {step.retries} attempts: "
            f"{last}",
        )

    def _do_verify_heads(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        # The heads READ also runs in the candidate image: the previous
        # image's alembic may not even know the new lineage's branch labels.
        observed = set(self._effects.migration_heads(image=plan.image))
        expected = set(self._spec.migration.expected_heads)
        if observed != expected:
            raise StepFailed(
                step.kind.value,
                f"migration heads are {sorted(observed)}, the descriptor expects "
                f"{sorted(expected)}. `upgrade heads` is plural because a "
                "composition has several lineages, and a lineage that silently "
                "did not advance is invisible to an exit code of 0",
            )
        return f"heads {sorted(observed)}"

    def _do_start_candidate(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        # The image is INJECTED into the start, not hoped for: before this,
        # the candidate started from the on-disk compose file — which still
        # pins the PREVIOUS image until `switch` re-renders it — and only a
        # post-hoc digest comparison stood between that and gating traffic
        # onto the old release. The comparison stays, as a canary.
        digest = self._effects.start_candidate(
            step.target, timeout_seconds=step.timeout_seconds, image=plan.image
        )
        if digest and digest != plan.image_digest:
            raise StepFailed(
                step.kind.value,
                f"the {step.target!r} candidate started on digest {digest}, not "
                f"the deploying digest {plan.image_digest}",
            )
        return f"{step.target} candidate started"

    def _do_gate_candidate(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        role = self._spec.role(step.target)
        probe = role.ready
        if probe is None:
            raise StepFailed(
                step.kind.value,
                f"role {step.target!r} has no readiness probe, so the only "
                "available gate is a timer",
            )
        deadline = self._clock() + step.timeout_seconds
        attempts = 0
        while self._clock() < deadline:
            attempts += 1
            if self._effects.candidate_ready(step.target):
                return f"{step.target} ready after {attempts} probe(s)"
            self._sleep(probe.interval_seconds)
        raise StepFailed(
            step.kind.value,
            f"the {step.target!r} candidate never became ready within "
            f"{step.timeout_seconds}s across {attempts} probes. It is NOT handed "
            "traffic. Note that a budget too small for a host that starts every "
            "role at once produces this same message, and has caused a false "
            "rollback before — check the budget before assuming the image",
        )

    def _do_switch(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        target = plan.previous_image if self._rolling_back else plan.image
        self._effects.switch(timeout_seconds=step.timeout_seconds, image=target)
        if self._rolling_back:
            return f"every role restored to the previous digest {target}"
        return "traffic switched and every role recreated on the deploying digest"

    def _do_verify_roles(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        expected = (
            _digest_of(plan.previous_image) if self._rolling_back else plan.image_digest
        )
        if not step.target:
            # A whole-deployment verification, which is the shape the rollback
            # plan uses: there is no per-role step list to walk because every
            # role moved together.
            checked = [role.code for role in self._spec.roles if role.replicas > 0]
            for code in checked:
                self._verify_one_role(
                    code, expected, plan, step_timeout=step.timeout_seconds
                )
            return f"{len(checked)} role(s) verified on {expected}"
        self._verify_one_role(
            step.target, expected, plan, step_timeout=step.timeout_seconds
        )
        return f"{step.target} verified on {expected}"

    def _verify_one_role(
        self, code: str, expected: str, plan: DeploymentPlan, *, step_timeout: int = 60
    ) -> None:
        step_kind = StepKind.VERIFY_ROLES.value
        role = self._spec.role(code)
        if role.replicas == 0:
            return
        observations = {item.code: item for item in self._effects.observe_roles()}
        observation = observations.get(code)
        if observation is None or not observation.running:
            raise StepFailed(step_kind, f"role {code!r} is not running")
        if observation.image_digest != expected:
            raise StepFailed(
                step_kind,
                f"role {code!r} runs digest {observation.image_digest}, not "
                f"{expected}. One deployment, one digest, every role — a "
                "surviving container from a previous release is how a stale "
                "scheduler kept firing failing tasks for hours with every "
                "container-level check green",
            )
        if observation.restarts:
            raise StepFailed(
                step_kind,
                f"role {code!r} has restarted {observation.restarts} time(s). "
                "A running container that has restarted is a crash loop observed "
                "between crashes",
            )
        if observation.source_mounted:
            raise StepFailed(
                step_kind, f"role {code!r} bind-mounts source into the container"
            )
        if role.ready is not None and code in set(_plan_ingress_roles(self._spec)):
            # REAL readiness, every strategy. `GATE_CANDIDATE` polls the
            # candidate's probe before traffic — but only the warm-candidate
            # strategy ever creates that step, so a maintenance or recreate
            # deployment used to declare victory on docker-inspect facts plus
            # a sleep: running, right digest, zero restarts — and an app that
            # binds its port then hangs on a dead dependency passes all three.
            # The role's own declared probe is polled here, bounded by the
            # step's budget, for every strategy including warm-candidate
            # (whose candidate is gone by now; the REAL role must be ready
            # too).
            deadline = self._clock() + step_timeout
            attempts = 0
            while True:
                attempts += 1
                if self._effects.role_ready(code):
                    break
                if self._clock() >= deadline:
                    raise StepFailed(
                        step_kind,
                        f"role {code!r} is running the right digest and never "
                        f"answered its readiness probe within {step_timeout}s "
                        f"across {attempts} attempt(s). Running is not ready: "
                        "a process that bound its port and hangs on a dead "
                        "dependency looks exactly like this",
                    )
                self._sleep(role.ready.interval_seconds)
        if role.worker is not None and not self._effects.worker_responds(code):
            raise StepFailed(
                step_kind,
                f"worker role {code!r} does not answer its ping. A worker "
                "whose process is up and whose queue is not being drained is "
                "indistinguishable from a healthy one at the container level",
            )
        if role.scheduler_tick_max_age_seconds is not None:
            age = self._effects.scheduler_last_tick_age_seconds(code)
            if age is None:
                raise StepFailed(
                    step_kind, f"scheduler role {code!r} reports no tick at all"
                )
            if age > role.scheduler_tick_max_age_seconds:
                raise StepFailed(
                    step_kind,
                    f"scheduler role {code!r} last ticked {age}s ago, "
                    f"budget {role.scheduler_tick_max_age_seconds}s",
                )

    def _do_stabilise(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        window = self._spec.stability_window_seconds
        self._sleep(window)
        for observation in self._effects.observe_roles():
            if observation.code not in self._spec.role_codes:
                continue
            if self._spec.role(observation.code).replicas == 0:
                continue
            if not observation.running or observation.restarts:
                raise StepFailed(
                    step.kind.value,
                    f"role {observation.code!r} did not survive the {window}s "
                    f"stability window (running={observation.running}, "
                    f"restarts={observation.restarts})",
                )
        return f"stable for {window}s"

    def _do_product_postflight(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        return self._run_hook(step, "postflight")

    def _do_record_evidence(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        # An interim write, so the record exists before the last two steps run.
        # It is NOT the only write: `run()` persists in a `finally`, so a
        # deployment that dies at `migrate` still leaves evidence. This step
        # remains in the plan because a reader of the plan should see that
        # evidence is written, and because the interim copy captures the state
        # before pruning touches anything.
        #
        # MANDATORY here, best-effort in the `finally` — deliberately split.
        # On the SUCCESS path, evidence that failed to persist or does not
        # read back must fail the deployment before success is declared: an
        # unrecorded deployment is indistinguishable from an unauthorized one
        # a week later. On the FAILURE path (`_persist_evidence` from
        # `finally`), a write failure stays a note, because replacing the real
        # failure with "could not write the evidence file" is worse — that
        # rule has its own test and it is not weakened here.
        self._persist_evidence(outcome)
        if not outcome.evidence_path:
            raise StepFailed(
                step.kind.value,
                "the evidence artefact could not be written, and this is the "
                "success path: a deployment whose only account of itself "
                "failed to persist must not be declared successful. "
                f"{'; '.join(outcome.notes[-1:]) or ''}",
            )
        document = json.loads(
            json.dumps(outcome.as_evidence(), sort_keys=True, default=str)
        )
        read_back = json.loads(
            json.dumps(
                dict(self._effects.read_evidence(outcome.evidence_path)),
                sort_keys=True,
                default=str,
            )
        )
        if read_back != document:
            raise StepFailed(
                step.kind.value,
                f"the evidence record at {outcome.evidence_path} does not read "
                "back as the outcome this run produced. A record that cannot "
                "be read back identical is a hope, not evidence",
            )
        return outcome.evidence_path

    def _do_prune_images(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        try:
            self._effects.prune_images(retain=self._spec.rollback_images_retained)
        except DeploymentError as exc:
            # The one deliberate fail-open, stated rather than silent. Retention
            # is housekeeping; failing a verified-healthy deployment over a disk
            # permission is worse than the debt it leaves behind.
            outcome.notes.append(f"image retention failed and was not fatal: {exc}")
            return "retention failed (non-fatal, recorded)"
        return f"retained {self._spec.rollback_images_retained} rollback image(s)"

    def _do_release_lock(
        self, step: Step, plan: DeploymentPlan, outcome: DeploymentOutcome
    ) -> str:
        return "released by the caller when the run exits"

    # ── shared ──────────────────────────────────────────────────────────────

    def _run_hook(self, step: Step, phase: str) -> str:
        result = self._effects.run_command(
            step.command,
            timeout_seconds=step.timeout_seconds,
            materials=self._spec.runtime_materials,
        )
        if not result.ok:
            detail = (result.stderr or result.stdout).strip()
            message = f"product {phase} hook {step.target!r} failed: {detail}"
            if phase == "preflight":
                raise PreconditionFailed(message)
            raise StepFailed(step.kind.value, message, exit_code=result.exit_code)
        return f"{step.target} ok"


_LOCK_MARKERS: tuple[str, ...] = (
    "lock timeout",
    "canceling statement due to lock",
    "could not obtain lock",
    "deadlock detected",
)


def _is_lock_contention(message: str) -> bool:
    """Whether a migration failure is worth retrying.

    Deliberately a closed list of markers rather than "does the word lock
    appear": a migration whose own SQL creates a table called `locks` would
    otherwise be retried four times on every real failure.
    """
    lowered = message.lower()
    return any(marker in lowered for marker in _LOCK_MARKERS)


def _digest_of(reference: str) -> str:
    """The `sha256:…` part of an image reference, or the reference itself.

    A recorded previous image may be stored either as a full reference or as a
    bare digest depending on what the host reported, and a rollback that
    compared the two forms directly would fail on a correct restoration.
    """
    return reference.rsplit("@", 1)[1] if "@" in reference else reference
