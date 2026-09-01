"""The deployment state machine, as DATA.

`dotmac_sub`'s `scripts/deploy.sh` is the proven production implementation of
this sequence and is the extraction source (rule 24). What is ported is its
ORDER and its refusals; what is deliberately not ported is the shell, because a
900-line script cannot be tested without a host, so the only way to know whether
a gate fires is to break production and watch.

So the plan is built as a pure function of the descriptor and the observed
world, and it produces an ordered tuple of typed steps. Nothing here runs
anything. That means every failure-injection case the programme requires — a
wrong digest, a failed backup, a candidate that never becomes ready, a
maintenance-required release attempted through the online path — is an
ordinary unit test over a data structure, and the executor
(`engine/run.py`) stays small enough to read.

## The order, and why each step is where it is

Steps 1-7 are GATES: they mutate nothing, so a failure means the operator fixes
the stated cause and re-runs the identical command with no cleanup. Everything
from step 8 can change the world.

 1. `acquire_lock`        — exclusive, or two deployments interleave migrations.
 2. `verify_image`        — the exact digest exists and is what was approved.
 3. `verify_revision`     — the image's source revision equals the descriptor's.
 4. `verify_manifest`     — the composed manifest hashes to the declared digest.
                            An earlier plan checked the image and the revision
                            and never this, so a deployment could run the
                            approved image against a module set nobody approved
                            and only `drift` would notice — afterwards.
 5. `verify_release_evidence` — required CI/release evidence for that revision.
 5. `refuse_dirty_state`  — no dirty checkout, no source bind mount, no untracked
                            override carrying load-bearing configuration.
 6. `verify_materials`    — every declared material resolves, and the owner
                            material is absent from every runtime role.
 7. `product_preflight`   — the product's own hooks, bounded.
 8. `backup`              — before ANY DDL. Never after.
 9. `verify_backup`       — completion, size and checksum. An unverified backup
                            is a belief.
10. `migration_preflight` — the migration role can actually do DDL, and the
                            catalog is in the state the migration expects.
11. `stop_for_maintenance` — ONLY when the release declares
                            `maintenance_required`. Ingress, app, workers and
                            scheduler stop before DDL.
12. `migrate`             — the migration lock, bounded retries, `upgrade heads`.
13. `verify_heads`        — the declared heads are the real heads afterwards.
14. `start_candidate`     — a warm candidate, ONLY on the online path.
15. `gate_candidate`      — readiness, not a sleep.
16. `switch`              — atomically hand traffic over / recreate all roles.
17. `verify_roles`        — app, every worker, the scheduler.
18. `stabilise`           — a bounded observation window.
19. `product_postflight`  — the product's own hooks, bounded.
20. `record_evidence`     — what ran, against which digest, with what observed.
21. `prune_images`        — keep the rollback images, drop the rest.
22. `release_lock`

## Two refusals that are the point of the whole thing

**A `maintenance_required` release may not take the online path.** The online
path leaves the previous image running while the new schema is applied. If the
new schema is not readable by the previous image, that is a running process
issuing queries against a database it does not understand — a data-loss shape,
not a degraded one. `build_plan` refuses rather than warns.

**Rollback is permitted only when the release's own compatibility declaration
permits it.** Reusing the previous image after an incompatible migration is the
same defect arriving through the recovery path, which is where it is least
expected and most damaging. The plan carries `rollback_permitted` and the
executor honours it; a migration is NEVER automatically downgraded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ..errors import SpecError
from ..spec import ProductDeploymentSpec


class Phase(str, Enum):
    """Which half of the deployment a step is in.

    The distinction an operator needs at 3am: has anything changed yet?
    """

    GATE = "gate"
    """Mutates nothing. A failure here is safely re-runnable."""

    MUTATE = "mutate"
    """May change the world. A failure here needs a decision."""


class StepKind(str, Enum):
    ACQUIRE_LOCK = "acquire_lock"
    VERIFY_IMAGE = "verify_image"
    VERIFY_REVISION = "verify_revision"
    VERIFY_MANIFEST = "verify_manifest"
    VERIFY_RELEASE_EVIDENCE = "verify_release_evidence"
    REFUSE_DIRTY_STATE = "refuse_dirty_state"
    VERIFY_MATERIALS = "verify_materials"
    VERIFY_EXTERNAL_RECOVERY_RECEIPT = "verify_external_recovery_receipt"
    PRODUCT_PREFLIGHT = "product_preflight"
    BACKUP = "backup"
    VERIFY_BACKUP = "verify_backup"
    MIGRATION_PREFLIGHT = "migration_preflight"
    STOP_FOR_MAINTENANCE = "stop_for_maintenance"
    MIGRATE = "migrate"
    VERIFY_HEADS = "verify_heads"
    START_CANDIDATE = "start_candidate"
    GATE_CANDIDATE = "gate_candidate"
    SWITCH = "switch"
    VERIFY_ROLES = "verify_roles"
    STABILISE = "stabilise"
    PRODUCT_POSTFLIGHT = "product_postflight"
    RECORD_EVIDENCE = "record_evidence"
    PRUNE_IMAGES = "prune_images"
    RELEASE_LOCK = "release_lock"


_PHASE_OF: Final[Mapping[StepKind, Phase]] = {
    StepKind.ACQUIRE_LOCK: Phase.GATE,
    StepKind.VERIFY_IMAGE: Phase.GATE,
    StepKind.VERIFY_REVISION: Phase.GATE,
    StepKind.VERIFY_MANIFEST: Phase.GATE,
    StepKind.VERIFY_RELEASE_EVIDENCE: Phase.GATE,
    StepKind.REFUSE_DIRTY_STATE: Phase.GATE,
    StepKind.VERIFY_MATERIALS: Phase.GATE,
    # A GATE, and the placement is the point. Accepting somebody else's
    # signed proof mutates nothing, and it must refuse BEFORE any DDL --
    # discovering that recovery was never demonstrated after the migration
    # has run is discovering it at the one moment it cannot help.
    StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT: Phase.GATE,
    StepKind.PRODUCT_PREFLIGHT: Phase.GATE,
    StepKind.BACKUP: Phase.MUTATE,
    StepKind.VERIFY_BACKUP: Phase.MUTATE,
    StepKind.MIGRATION_PREFLIGHT: Phase.MUTATE,
    StepKind.STOP_FOR_MAINTENANCE: Phase.MUTATE,
    StepKind.MIGRATE: Phase.MUTATE,
    StepKind.VERIFY_HEADS: Phase.MUTATE,
    StepKind.START_CANDIDATE: Phase.MUTATE,
    StepKind.GATE_CANDIDATE: Phase.MUTATE,
    StepKind.SWITCH: Phase.MUTATE,
    StepKind.VERIFY_ROLES: Phase.MUTATE,
    StepKind.STABILISE: Phase.MUTATE,
    StepKind.PRODUCT_POSTFLIGHT: Phase.MUTATE,
    StepKind.RECORD_EVIDENCE: Phase.MUTATE,
    StepKind.PRUNE_IMAGES: Phase.MUTATE,
    StepKind.RELEASE_LOCK: Phase.MUTATE,
}


class Strategy(str, Enum):
    """How the release reaches the runtime roles."""

    WARM_CANDIDATE = "warm_candidate"
    """Start a candidate beside the primary, gate it on readiness, then switch.

    Available only when the migration declares `online` compatibility, because
    the previous image keeps serving while the new schema is applied.
    """

    RECREATE = "recreate"
    """Migrate online, then recreate every role. No candidate.

    The correct strategy when the migration is online-compatible but nothing
    serves ingress — a worker-only deployment, or the Starter's single role
    with an external proxy. A warm candidate for a role with no traffic to hand
    over is not a safer rollout: two workers consuming one queue is a
    double-processing bug wearing a rollout's clothes.

    Named apart from WARM_CANDIDATE rather than folded into it, because a plan
    that SAYS warm-candidate and contains no candidate step is a plan a reader
    would have to count steps to disbelieve.
    """

    MAINTENANCE = "maintenance"
    """Stop everything, migrate, start everything on the new digest.

    The only correct strategy for `maintenance_required`, and the reason it is
    a separate strategy rather than a flag: the step LIST differs, so a reader
    of the plan sees the outage rather than inferring it.
    """


@dataclass(frozen=True, slots=True)
class Step:
    """One step. Deliberately data — `command` is what to run, not how."""

    kind: StepKind
    description: str
    command: tuple[str, ...] = ()
    timeout_seconds: int = 300
    retries: int = 1
    target: str = ""
    """The role, dataset or hook this step acts on. Empty when whole-deployment."""

    @property
    def phase(self) -> Phase:
        return _PHASE_OF[self.kind]

    @property
    def mutates(self) -> bool:
        return self.phase is Phase.MUTATE


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    """The complete ordered plan for one release onto one target."""

    product: str
    image: str
    image_digest: str
    source_revision: str
    manifest_digest: str
    strategy: Strategy
    steps: tuple[Step, ...]
    rollback_permitted: bool
    rollback_reason: str
    previous_image: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def step(self, kind: StepKind) -> Step | None:
        for candidate in self.steps:
            if candidate.kind is kind:
                return candidate
        return None

    def has(self, kind: StepKind) -> bool:
        return self.step(kind) is not None

    @property
    def gate_steps(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if not step.mutates)

    @property
    def mutating_steps(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.mutates)

    @property
    def first_mutating_index(self) -> int:
        """Where the safely-re-runnable half ends.

        An operator reading a failure needs exactly one fact first: was the
        failed step before or after this index?
        """
        for index, step in enumerate(self.steps):
            if step.mutates:
                return index
        return len(self.steps)


# ── construction ────────────────────────────────────────────────────────────


def build_plan(
    spec: ProductDeploymentSpec,
    *,
    previous_image: str = "",
    skip_backup: bool = False,
    skip_backup_reason: str = "",
) -> DeploymentPlan:
    """The plan for deploying ``spec``.

    ``previous_image`` is the digest currently running, when one is known. It is
    what makes the rollback decision expressible: with no previous image there
    is nothing to roll back TO, which is a different situation from a rollback
    that is forbidden, and conflating them is how a first deployment comes to
    report a rollback capability it does not have.

    ``skip_backup`` exists because Sub's production script has the same escape
    hatch and removing it would push operators outside the tool. It is typed and
    it demands a reason: an untyped `--force` is how the backup step becomes
    optional in practice.
    """
    if skip_backup and not skip_backup_reason.strip():
        raise SpecError(
            "skip_backup requires a stated reason. A backup skipped for an "
            "unstated reason is indistinguishable, three months later, from a "
            "backup step that silently stopped working"
        )

    online = spec.migration.is_online
    warmable = _ingress_roles(spec)
    if not online:
        strategy = Strategy.MAINTENANCE
    elif warmable:
        strategy = Strategy.WARM_CANDIDATE
    else:
        strategy = Strategy.RECREATE
    steps: list[Step] = []
    notes: list[str] = []
    if strategy is Strategy.RECREATE:
        notes.append(
            "No role serves ingress, so there is no traffic to hand over and no "
            "warm candidate is started. Roles are recreated after the migration."
        )

    # ── gates ───────────────────────────────────────────────────────────────
    steps.append(
        Step(
            StepKind.ACQUIRE_LOCK,
            "Take the exclusive deployment lock",
            timeout_seconds=60,
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_IMAGE,
            f"Verify the exact image digest {spec.image_digest} is present and "
            "matches the approved reference",
            target=spec.image,
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_REVISION,
            f"Verify the image was built from source revision "
            f"{spec.source_revision}",
            target=spec.source_revision,
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_MANIFEST,
            f"Verify the composed product manifest hashes to "
            f"{spec.manifest_digest}",
            target=spec.manifest_path,
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_RELEASE_EVIDENCE,
            "Verify the required CI/release evidence exists for that exact "
            "revision before anything is mutated",
            target=spec.source_revision,
        )
    )
    steps.append(
        Step(
            StepKind.REFUSE_DIRTY_STATE,
            "Refuse a dirty checkout, a source bind mount, or an untracked "
            "override carrying load-bearing configuration",
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_MATERIALS,
            "Verify every declared material resolves and the migration owner "
            "material is absent from every runtime role",
        )
    )
    for dataset in spec.backup_datasets:
        if not dataset.externally_executed:
            continue
        executor = dataset.external_executor
        assert executor is not None  # nosec B101 -- narrowed by the guard above
        steps.append(
            Step(
                StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT,
                f"Verify a signed RecoveryReceipt.v1 for {dataset.code!r} from "
                f"{executor.kind}:{executor.identifier}@{executor.version}, "
                f"bound to this descriptor, proving an isolated restore inside "
                f"{dataset.restore_proof_max_age_days} days with "
                f"{list(dataset.verify)}",
                target=dataset.code,
                timeout_seconds=60,
            )
        )

    for hook in spec.preflight_hooks:
        steps.append(
            Step(
                StepKind.PRODUCT_PREFLIGHT,
                f"Product preflight hook {hook.code!r}",
                command=hook.command,
                timeout_seconds=hook.timeout_seconds,
                target=hook.code,
            )
        )

    # ── mutation ────────────────────────────────────────────────────────────
    if skip_backup:
        notes.append(f"BACKUP SKIPPED — {skip_backup_reason}")
    elif not spec.backup_datasets:
        # Not a silent pass. A product that declares no dataset has declared
        # that nothing needs backing up, and that claim belongs in the plan
        # where a reviewer sees it, not in the absence of a step.
        notes.append(
            "No backup dataset is declared, so no backup runs. If this product "
            "has durable state, the descriptor is wrong."
        )
    else:
        for dataset in spec.backup_datasets:
            if dataset.externally_executed:
                # NO backup step, deliberately. Another party executes recovery
                # for this dataset, so a `backup` step here would attribute to
                # the consuming product an act it does not perform -- and a plan
                # that says a product backed something up is exactly the
                # artefact that read as green while nothing had ever been
                # restored. The gate above is what this deployment can honestly
                # do: demand a current signed proof from the party that can.
                notes.append(
                    f"Dataset {dataset.code!r} is executed by "
                    f"{dataset.external_executor.kind}:"  # type: ignore[union-attr]
                    f"{dataset.external_executor.identifier}, so this "  # type: ignore[union-attr]
                    "deployment runs no backup for it and instead requires a "
                    "signed RecoveryReceipt.v1 before any DDL."
                )
                continue
            steps.append(
                Step(
                    StepKind.BACKUP,
                    f"Back up {dataset.code!r} ({dataset.kind}) before any DDL",
                    timeout_seconds=3600,
                    target=dataset.code,
                )
            )
            steps.append(
                Step(
                    StepKind.VERIFY_BACKUP,
                    f"Verify {dataset.code!r} completed, is non-empty, and its "
                    f"{dataset.checksum} checksum matches",
                    timeout_seconds=900,
                    target=dataset.code,
                )
            )

    steps.append(
        Step(
            StepKind.MIGRATION_PREFLIGHT,
            "Verify the migration role can perform DDL and the catalog is in "
            "the state the migration expects",
            command=spec.migration.preflight_command,
            timeout_seconds=300,
        )
    )

    if strategy is Strategy.MAINTENANCE:
        steps.append(
            Step(
                StepKind.STOP_FOR_MAINTENANCE,
                "This release declares maintenance_required: stop ingress, the "
                "application, every worker and the scheduler BEFORE DDL",
                timeout_seconds=max(role.stop_grace_seconds for role in spec.roles)
                + 60,
            )
        )

    steps.append(
        Step(
            StepKind.MIGRATE,
            "Take the migration lock and run `upgrade heads` as the owner role",
            command=spec.migration.command,
            timeout_seconds=spec.migration.lock_timeout_seconds,
            retries=spec.migration.lock_retries,
        )
    )
    steps.append(
        Step(
            StepKind.VERIFY_HEADS,
            f"Verify the real heads are exactly {list(spec.migration.expected_heads)}",
            timeout_seconds=120,
        )
    )

    if strategy is Strategy.WARM_CANDIDATE:
        for role in warmable:
            steps.append(
                Step(
                    StepKind.START_CANDIDATE,
                    f"Start a warm candidate for role {role!r} on the new digest, "
                    "beside the running primary",
                    timeout_seconds=600,
                    target=role,
                )
            )
            steps.append(
                Step(
                    StepKind.GATE_CANDIDATE,
                    f"Gate the {role!r} candidate on its READINESS probe — not on "
                    "a timer, and not on liveness, which cannot fail",
                    timeout_seconds=600,
                    target=role,
                )
            )

    steps.append(
        Step(
            StepKind.SWITCH,
            "Atomically switch traffic to the candidate and recreate every "
            "remaining role on the same digest"
            if strategy is Strategy.WARM_CANDIDATE
            else "Recreate every role on the new digest"
            if strategy is Strategy.RECREATE
            else "Start every role on the new digest",
            timeout_seconds=900,
        )
    )
    for role in spec.startup_order:
        steps.append(
            Step(
                StepKind.VERIFY_ROLES,
                f"Verify role {role!r} is running the exact digest, has not "
                "restarted, and answers its own health contract",
                timeout_seconds=300,
                target=role,
            )
        )
    steps.append(
        Step(
            StepKind.STABILISE,
            f"Observe for {spec.stability_window_seconds}s before declaring "
            "success — a container that crashes 40 seconds in is a failed "
            "deployment, not a successful one followed by an incident",
            timeout_seconds=spec.stability_window_seconds + 60,
        )
    )
    for hook in spec.postflight_hooks:
        steps.append(
            Step(
                StepKind.PRODUCT_POSTFLIGHT,
                f"Product postflight hook {hook.code!r}",
                command=hook.command,
                timeout_seconds=hook.timeout_seconds,
                target=hook.code,
            )
        )
    steps.append(
        Step(
            StepKind.RECORD_EVIDENCE,
            "Write the deployment evidence: what ran, against which digest, "
            "with which observed digests and configuration digest",
            timeout_seconds=120,
        )
    )
    steps.append(
        Step(
            StepKind.PRUNE_IMAGES,
            f"Retain the {spec.rollback_images_retained} most recent rollback "
            "images and prune the rest",
            timeout_seconds=300,
        )
    )
    steps.append(
        Step(StepKind.RELEASE_LOCK, "Release the deployment lock", timeout_seconds=60)
    )

    rollback_permitted, rollback_reason = _rollback_decision(spec, previous_image)
    return DeploymentPlan(
        product=spec.product,
        image=spec.image,
        image_digest=spec.image_digest,
        source_revision=spec.source_revision,
        manifest_digest=spec.manifest_digest,
        strategy=strategy,
        steps=tuple(steps),
        rollback_permitted=rollback_permitted,
        rollback_reason=rollback_reason,
        previous_image=previous_image,
        notes=tuple(notes),
    )


def _ingress_roles(spec: ProductDeploymentSpec) -> tuple[str, ...]:
    """Roles that serve ingress, in declaration order, deduplicated.

    Only these get a warm candidate. A worker has no traffic to hand over, so a
    candidate for one would be two workers consuming the same queue — which is
    not a safer rollout, it is a double-processing bug.
    """
    if spec.ingress is None:
        return ()
    seen: list[str] = []
    for route in spec.ingress.routes:
        if route.role not in seen:
            seen.append(route.role)
    return tuple(seen)


def _rollback_decision(
    spec: ProductDeploymentSpec, previous_image: str
) -> tuple[bool, str]:
    """Whether restoring ``previous_image`` is permitted, and why.

    Three distinct answers, and collapsing any two of them is a real defect:

    - No previous image — there is nothing to roll back TO. Reporting this as
      "rollback forbidden" would make a first deployment look like a risky one.
    - Migration declared `maintenance_required` — the previous image cannot
      read the new schema. Refused.
    - Migration declared `online` — the previous image was, by that very
      declaration, able to run against this schema. Permitted.

    In no case is a migration downgraded. `alembic downgrade` against a
    production database is a destructive operation whose correctness depends on
    every migration author having written a correct and tested `downgrade()`,
    and that is not a property any deployment tool may assume.
    """
    if not previous_image:
        return (
            False,
            "no previous image is recorded, so there is nothing to roll back to",
        )
    if not spec.migration.is_online:
        return False, (
            "the release declares migration compatibility 'maintenance_required', "
            "so the previous image cannot read the schema now in place. Recovery "
            "is a restore from the pre-migration backup, not an image swap"
        )
    return True, (
        "the release declares migration compatibility 'online', so the previous "
        "image remains able to run against the schema in place"
    )


def format_plan(plan: DeploymentPlan) -> str:
    """A human-readable plan.

    This is what `dotmac-deploy deploy --dry-run` prints and what a reviewer
    reads on a pull request. The gate/mutate boundary is drawn explicitly
    because it is the one thing a reader needs before authorising a run.
    """
    lines: list[str] = [
        f"product          {plan.product}",
        f"image            {plan.image}",
        f"source revision  {plan.source_revision}",
        f"manifest digest  {plan.manifest_digest}",
        f"strategy         {plan.strategy.value}",
        f"previous image   {plan.previous_image or '(none recorded)'}",
        f"rollback         {'permitted' if plan.rollback_permitted else 'REFUSED'} — "
        f"{plan.rollback_reason}",
        "",
    ]
    for note in plan.notes:
        lines.append(f"NOTE  {note}")
    if plan.notes:
        lines.append("")

    drawn = False
    for index, step in enumerate(plan.steps, start=1):
        if step.mutates and not drawn:
            lines.append(
                "  ── everything above mutates nothing; a failure above here is "
                "safely re-runnable ──"
            )
            drawn = True
        target = f" [{step.target}]" if step.target else ""
        lines.append(f"{index:>3}. {step.kind.value}{target}")
        lines.append(f"     {step.description}")
        if step.command:
            lines.append(f"     $ {' '.join(step.command)}")
    return "\n".join(lines) + "\n"


def steps_for_rollback(plan: DeploymentPlan) -> Sequence[Step]:
    """The steps a rollback runs, or an empty sequence when it is refused.

    Refusal is not an error here — `rollback_reason` already says why, and a
    caller that wants to fail loudly can. Returning an empty plan keeps
    "refused" and "nothing to do" from needing different call sites.
    """
    if not plan.rollback_permitted:
        return ()
    return (
        Step(
            StepKind.ACQUIRE_LOCK,
            "Take the exclusive deployment lock",
            timeout_seconds=60,
        ),
        Step(
            StepKind.SWITCH,
            f"Restore every role to the previous digest {plan.previous_image}",
            timeout_seconds=900,
            target=plan.previous_image,
        ),
        Step(
            StepKind.VERIFY_ROLES,
            "Verify every role is running the restored digest",
            timeout_seconds=300,
        ),
        Step(
            StepKind.RECORD_EVIDENCE,
            "Record the rollback, its cause, and the restored digest",
            timeout_seconds=120,
        ),
        Step(StepKind.RELEASE_LOCK, "Release the deployment lock", timeout_seconds=60),
    )
