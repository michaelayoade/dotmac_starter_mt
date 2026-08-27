"""The failure-injection matrix, as ordinary tests.

The programme requires twenty failure cases to be proven — a wrong image
digest, missing migration credentials, a corrupt backup, a candidate that never
becomes ready, a maintenance-required migration attempted through the online
path, and the rest. Every one of them is here, and every one runs in
milliseconds with no host, no daemon and no database.

That is the whole reason `engine/run.py` puts every effect behind one
`Effects` Protocol. `dotmac_sub`'s 880-line shell engine cannot do this: its
gates are `if`-statements interleaved with `docker` invocations, so the only
way to observe a gate firing is to arrange the failure on a real machine. Two
of its gates were in fact discovered to be wrong only in production — the
180-second health budget that caused a false rollback, and the profile filter
that stopped Beat being created but not from continuing to run.

A test that never sees a gate fire cannot tell a working gate from a deleted
one. These do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from dotmac_deployment_foundation.engine.plan import (
    Strategy,
    build_plan,
    steps_for_rollback,
)
from dotmac_deployment_foundation.engine.run import (
    BackupResult,
    CommandResult,
    Executor,
    RoleObservation,
)
from dotmac_deployment_foundation.errors import (
    PreconditionFailed,
    SpecError,
    StepFailed,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

GOOD_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64
REVISION = "c" * 40
MANIFEST_DIGEST = "sha256:" + "d" * 64
# A realistic write-time checksum, so the canary that proves the verifier
# receives the REAL one is asserting against something that looks like one.
_CHECKSUM = "e" * 64

DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "example"
environment = "test"

[assembly]
manifest_path = "deploy/product-manifest.json"
manifest_digest = "sha256:{"d" * 64}"

[image]
reference = "ghcr.io/example/app@{GOOD_DIGEST}"
source_revision = "{REVISION}"

[runtime_materials]
names = ["DATABASE_URL", "REDIS_URL"]

[[roles]]
code = "app"
command = ["uvicorn", "app.main:app"]
replicas = 1
materials = ["DATABASE_URL", "REDIS_URL"]
[roles.resources]
cpus = "1.0"
memory = "1g"
[roles.health.live]
path = "/health/live"
port = 8000
[roles.health.ready]
path = "/health/ready"
port = 8000

[[roles]]
code = "worker"
command = ["celery", "-A", "app", "worker"]
replicas = 1
depends_on = ["app"]
materials = ["DATABASE_URL", "REDIS_URL"]
[roles.resources]
cpus = "1.0"
memory = "1g"
[roles.health.live]
path = "/health/live"
port = 8001
[roles.worker]
kind = "celery"
ping_command = ["celery", "-A", "app", "inspect", "ping"]
heartbeat_max_age_seconds = 120

[[roles]]
code = "beat"
command = ["celery", "-A", "app", "beat"]
replicas = 1
materials = ["DATABASE_URL", "REDIS_URL"]
[roles.resources]
cpus = "0.5"
memory = "512m"
[roles.health.live]
path = "/health/live"
port = 8002
[roles.scheduler]
last_tick_max_age_seconds = 300
tick_command = ["sh", "-c", "stat -c %Y /tmp/beat"]

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["a003", "k012"]
compatibility = "online"
preflight_command = ["python", "-m", "verify_role"]

[backup]
[[backup.datasets]]
code = "primary"
kind = "postgres"
material = "BACKUP_DATABASE_URL"
retention_days = 14

[ingress]
host = "example.dotmac.io"
[[ingress.routes]]
path = "/"
role = "app"
port = 8000

[rollout]
stability_window_seconds = 60
"""


def load(**overrides: str) -> ProductDeploymentSpec:
    """The good descriptor, with one substring swapped.

    Substring substitution rather than a builder because the point of most of
    these tests is that the DESCRIPTOR is fine and the WORLD is wrong; keeping
    one canonical descriptor makes the exceptions visible.
    """
    text = DESCRIPTOR
    for old, new in overrides.items():
        text = text.replace(old.replace("__", " "), new)
    return ProductDeploymentSpec.loads(text, source="<test>")


class FakeEffects:
    """A world that is correct until a test makes one thing wrong.

    Every knob defaults to the healthy value. A test sets exactly one and
    asserts the specific refusal, so a test that passes for an unrelated reason
    is visible: it would have to be failing on a different step than the one it
    names.
    """

    def __init__(self, **overrides: object) -> None:
        self.present = True
        self.labels: dict[str, str] = {"org.opencontainers.image.revision": REVISION}
        self.evidence: dict[str, str] = {"ci": "passed", "run": "1"}
        self.manifest = MANIFEST_DIGEST
        self.dirty = False
        self.overrides_found: list[str] = []
        self.materials = ["DATABASE_URL", "REDIS_URL", "MIGRATION_DATABASE_URL"]
        self.command_results: dict[str, CommandResult] = {}
        self.default_command_result = CommandResult(0)
        self.backup_ok = True
        self.backup_verifies = True
        self.heads = ["a003", "k012"]
        self.candidate_never_ready = False
        self.candidate_digest = GOOD_DIGEST
        self.roles: dict[str, RoleObservation] = {
            code: RoleObservation(code, True, GOOD_DIGEST, 0)
            for code in ("app", "worker", "beat")
        }
        self.worker_ok = True
        self.tick_age: int | None = 30
        self.evidence_written: list[Mapping[str, object]] = []
        self.switched_to: list[str] = []
        self.annotations: list[dict[str, str]] = []
        self.pruned = 0
        self._ready_calls = 0
        for key, value in overrides.items():
            setattr(self, key, value)

    # ── gates ──
    def image_present(self, reference: str) -> bool:
        return self.present

    def image_labels(self, reference: str) -> Mapping[str, str]:
        return self.labels

    def release_evidence(self, revision: str) -> Mapping[str, str]:
        return self.evidence

    def manifest_digest(self, manifest_path: str) -> str:
        return self.manifest

    def observe_roles(self) -> Sequence[RoleObservation]:
        return list(self.roles.values())

    def working_tree_dirty(self) -> bool:
        return self.dirty

    def untracked_compose_overrides(self) -> Sequence[str]:
        return self.overrides_found

    def resolved_materials(self) -> Sequence[str]:
        return self.materials

    # ── mutation ──
    def run_command(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        materials: Sequence[str] = (),
    ) -> CommandResult:
        return self.command_results.get(command[0], self.default_command_result)

    def backup(self, dataset_code: str, *, timeout_seconds: int) -> BackupResult:
        if not self.backup_ok:
            raise StepFailed("backup", "pg_dump exited 1: connection refused")
        return BackupResult(
            dataset_code,
            f"/backups/{dataset_code}.sql.gz",
            1024,
            _CHECKSUM,
            "sha256",
        )

    def verify_backup(self, result: BackupResult) -> bool:
        return self.backup_verifies

    def migration_heads(self) -> Sequence[str]:
        return self.heads

    def stop_roles(self, roles: Sequence[str], *, timeout_seconds: int) -> None:
        for code in roles:
            self.roles[code] = RoleObservation(code, False, GOOD_DIGEST, 0)

    def start_candidate(self, role: str, *, timeout_seconds: int) -> str:
        return self.candidate_digest

    def candidate_ready(self, role: str) -> bool:
        self._ready_calls += 1
        return not self.candidate_never_ready

    def switch(self, *, timeout_seconds: int, image: str) -> None:
        digest = image.rsplit("@", 1)[1] if "@" in image else image
        self.switched_to.append(digest)
        for code in self.roles:
            self.roles[code] = RoleObservation(code, True, digest, 0)

    def worker_responds(self, role: str) -> bool:
        return self.worker_ok

    def scheduler_last_tick_age_seconds(self, role: str) -> int | None:
        return self.tick_age

    def write_evidence(self, evidence: Mapping[str, object]) -> str:
        self.evidence_written.append(evidence)
        return "/var/lib/dotmac/deploy-evidence.json"

    def prune_images(self, *, retain: int) -> None:
        self.pruned = retain

    def emit_annotation(self, annotation: Mapping[str, str]) -> None:
        self.annotations.append(dict(annotation))


class FakeClock:
    """Time the test controls, because the alternative is a test that lies.

    The executor's readiness gate loops until a DEADLINE. With a real clock and
    a no-op sleep it spins as fast as the CPU allows, so "the candidate is never
    ready" quietly becomes "the candidate is ready on the ten-thousandth probe"
    and the test passes for the wrong reason — which is exactly what happened
    the first time this suite was run.

    Sleeping advances the clock. That also makes the stability window
    instantaneous: a test that genuinely waits 120 seconds is a test nobody
    runs.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.001)


def run(spec: ProductDeploymentSpec, effects: FakeEffects, **plan_kwargs: object):  # type: ignore[no-untyped-def]
    plan = build_plan(spec, **plan_kwargs)  # type: ignore[arg-type]
    clock = FakeClock()
    executor = Executor(spec, effects, sleep=clock.sleep, clock=clock.read)
    return plan, executor.run(plan)


# ── the happy path, which every failure test needs to exist ─────────────────


def test_a_correct_deployment_succeeds_end_to_end() -> None:
    """The negative control for the whole file.

    Twenty tests below assert that a deployment FAILS. Without this one, every
    single one of them could be passing because the executor refuses
    everything — which is the most common way a suite of refusal tests stops
    meaning anything.
    """
    spec = load()
    effects = FakeEffects()
    _, outcome = run(spec, effects)
    assert outcome.succeeded, outcome.failure
    assert outcome.failed_step is None
    assert effects.evidence_written, "evidence must be written even on the happy path"
    assert effects.pruned == 2


# ── gates: nothing is mutated ───────────────────────────────────────────────


def test_a_missing_image_digest_refuses_before_anything_is_mutated() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(present=False))
    assert not outcome.succeeded
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_image"
    )
    assert not outcome.mutated, "a gate failure must leave the world untouched"


def test_an_image_built_from_the_wrong_revision_is_refused() -> None:
    spec = load()
    _, outcome = run(
        spec, FakeEffects(labels={"org.opencontainers.image.revision": "f" * 40})
    )
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "verify_revision"
    )
    assert not outcome.mutated


def test_an_image_with_no_revision_label_is_refused() -> None:
    """An unlabelled image is refused, not accepted-with-a-warning.

    Without the label nothing connects the running bytes to a reviewable
    commit, so every later integrity claim about this deployment is unfounded.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(labels={}))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "verify_revision"
    )


def test_a_revision_with_no_release_evidence_is_refused() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(evidence={}))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "verify_release_evidence"
    )
    assert not outcome.mutated


def test_a_dirty_checkout_is_refused() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(dirty=True))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "refuse_dirty_state"
    )


def test_an_untracked_compose_override_is_refused() -> None:
    """The exact shape that twice took a live staging host down.

    A host-only override is an undocumented manual step, and the first re-render
    reverts it — which is how the service went down the previous two times.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(overrides_found=["docker-compose.override.yml"]))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "refuse_dirty_state"
    )
    assert "override" in outcome.failure


def test_a_source_bind_mount_into_a_container_is_refused() -> None:
    spec = load()
    effects = FakeEffects()
    effects.roles["app"] = RoleObservation(
        "app", True, GOOD_DIGEST, 0, source_mounted=True
    )
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "refuse_dirty_state"
    )
    assert "bind-mount" in outcome.failure


def test_missing_migration_credentials_are_refused_before_ddl() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(materials=["DATABASE_URL", "REDIS_URL"]))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "verify_materials"
    )
    assert "MIGRATION_DATABASE_URL" in outcome.failure
    assert not outcome.mutated


def test_owner_credentials_present_in_a_runtime_role_are_refused_at_parse_time() -> (
    None
):
    """This one never reaches the executor, and that is the stronger outcome.

    A runtime role holding the migration owner credential can create, alter and
    drop any table for the life of the deployment. Catching it in the loader
    means the descriptor cannot even be committed in that state — the review
    fails, not the deployment.
    """
    with pytest.raises(SpecError) as caught:
        ProductDeploymentSpec.loads(
            DESCRIPTOR.replace(
                'materials = ["DATABASE_URL", "REDIS_URL"]',
                'materials = ["DATABASE_URL", "MIGRATION_DATABASE_URL"]',
                1,
            ),
            source="<test>",
        )
    assert "MIGRATION_DATABASE_URL" in str(caught.value)


def test_a_failing_product_preflight_hook_refuses_before_mutation() -> None:
    text = DESCRIPTOR.replace(
        "[migration]",
        (
            '[[hooks.preflight]]\ncode = "licence"\n'
            'command = ["check-licence"]\n\n[migration]'
        ),
        1,
    )
    spec = ProductDeploymentSpec.loads(text, source="<test>")
    effects = FakeEffects(
        command_results={"check-licence": CommandResult(3, stderr="expired")}
    )
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "product_preflight"
    )
    assert not outcome.mutated


# ── mutation: backup and migration ──────────────────────────────────────────


def test_a_failed_backup_stops_the_deployment_before_any_ddl() -> None:
    spec = load()
    plan, outcome = run(spec, FakeEffects(backup_ok=False))
    assert outcome.failed_step is not None and outcome.failed_step.value == "backup"
    ran = [record.kind.value for record in outcome.records]
    assert "migrate" not in ran, "DDL must never run after a failed backup"


def test_a_corrupt_backup_fails_verification_and_stops_the_deployment() -> None:
    """The defect three products share, made into a refusal.

    `pg_dump | gzip` reports gzip's exit status, so a dump that dies halfway
    produces a valid, non-empty, entirely truncated archive that every source
    implementation's size check accepts.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(backup_verifies=False))
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_backup"
    )
    ran = [record.kind.value for record in outcome.records]
    assert "migrate" not in ran


def test_a_failing_migration_role_preflight_stops_before_the_migration() -> None:
    spec = load()
    effects = FakeEffects(
        command_results={"python": CommandResult(1, stderr="app_admin cannot CREATE")}
    )
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "migration_preflight"
    )


def test_a_migration_failure_that_is_not_lock_contention_is_not_retried() -> None:
    """A blanket retry runs a genuinely failing migration four times.

    Half-applied and re-driven is how a schema nobody can describe comes about,
    so the retry predicate is a closed list of lock markers.
    """
    spec = load()
    effects = FakeEffects(
        command_results={"alembic": CommandResult(1, stderr="relation already exists")}
    )
    _, outcome = run(spec, effects)
    assert outcome.failed_step is not None and outcome.failed_step.value == "migrate"
    assert "not lock contention" in outcome.failure


def test_migration_lock_contention_is_retried_and_then_gives_up() -> None:
    spec = load()
    effects = FakeEffects(
        command_results={
            "alembic": CommandResult(
                1, stderr="canceling statement due to lock timeout"
            )
        }
    )
    _, outcome = run(spec, effects)
    assert outcome.failed_step is not None and outcome.failed_step.value == "migrate"
    assert "persisted across" in outcome.failure


def test_a_missing_migration_head_is_caught_even_though_the_command_exited_zero() -> (
    None
):
    """`upgrade heads` is plural, and a lineage that did not advance exits 0.

    An exit code cannot see this. Only comparing the real heads against the
    declared ones can.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(heads=["a003"]))
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_heads"
    )
    assert "k012" in outcome.failure


# ── mutation: candidate, switch, roles ──────────────────────────────────────


def test_a_candidate_that_never_becomes_ready_is_not_handed_traffic() -> None:
    spec = load()
    effects = FakeEffects(candidate_never_ready=True)
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "gate_candidate"
    )
    ran = [record.kind.value for record in outcome.records]
    assert (
        "switch" not in ran
    ), "traffic must not switch to a candidate that is not ready"


def test_a_candidate_started_on_the_wrong_digest_is_refused() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(candidate_digest=OLD_DIGEST))
    assert (
        outcome.failed_step is not None
        and outcome.failed_step.value == "start_candidate"
    )


def test_a_role_left_on_a_previous_digest_after_the_switch_is_caught() -> None:
    """One deployment, one digest, every role.

    A container surviving from a previous release is how a stale scheduler kept
    firing failing tasks for hours with every container-level check green.
    """
    spec = load()
    effects = FakeEffects()
    original_switch = effects.switch

    def switch_leaving_beat_behind(*, timeout_seconds: int, image: str) -> None:
        original_switch(timeout_seconds=timeout_seconds, image=image)
        effects.roles["beat"] = RoleObservation("beat", True, OLD_DIGEST, 0)

    effects.switch = switch_leaving_beat_behind  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_roles"
    )
    assert OLD_DIGEST in outcome.failure


def test_a_restarting_role_is_a_failed_deployment_not_a_healthy_one() -> None:
    spec = load()
    effects = FakeEffects()
    original_switch = effects.switch

    def switch_with_crashloop(*, timeout_seconds: int, image: str) -> None:
        original_switch(timeout_seconds=timeout_seconds, image=image)
        effects.roles["app"] = RoleObservation("app", True, GOOD_DIGEST, 4)

    effects.switch = switch_with_crashloop  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_roles"
    )
    assert "restarted" in outcome.failure


def test_an_unhealthy_worker_fails_the_deployment_even_though_its_container_is_up() -> (
    None
):
    """A running worker draining nothing looks healthy at the container level."""
    spec = load()
    _, outcome = run(spec, FakeEffects(worker_ok=False))
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_roles"
    )
    assert "ping" in outcome.failure


def test_a_stale_scheduler_fails_the_deployment() -> None:
    spec = load()
    _, outcome = run(spec, FakeEffects(tick_age=9_999))
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_roles"
    )
    assert "ticked" in outcome.failure


def test_a_scheduler_that_has_never_ticked_is_distinguished_from_a_late_one() -> None:
    """`None` is not `a very large number`.

    A scheduler reporting no tick at all has a different cause from one running
    late, and reporting them identically sends the responder to the wrong
    place.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(tick_age=None))
    assert (
        outcome.failed_step is not None and outcome.failed_step.value == "verify_roles"
    )
    assert "no tick at all" in outcome.failure


# ── the two compatibility refusals ──────────────────────────────────────────


def test_a_maintenance_required_release_cannot_take_the_online_path() -> None:
    """The refusal ADR-0070 is mostly about.

    The online path leaves the previous image serving while the new schema is
    applied. If the previous image cannot read that schema, that is a running
    process querying a database it does not understand.
    """
    spec = ProductDeploymentSpec.loads(
        DESCRIPTOR.replace(
            'compatibility = "online"', 'compatibility = "maintenance_required"'
        ),
        source="<test>",
    )
    plan = build_plan(spec, previous_image=OLD_DIGEST)
    assert plan.strategy is Strategy.MAINTENANCE
    kinds = [step.kind.value for step in plan.steps]
    assert "start_candidate" not in kinds
    assert "gate_candidate" not in kinds
    assert "stop_for_maintenance" in kinds
    assert kinds.index("stop_for_maintenance") < kinds.index(
        "migrate"
    ), "everything must stop BEFORE the DDL, not after it"


def test_the_previous_image_is_not_reusable_after_an_incompatible_migration() -> None:
    spec = ProductDeploymentSpec.loads(
        DESCRIPTOR.replace(
            'compatibility = "online"', 'compatibility = "maintenance_required"'
        ),
        source="<test>",
    )
    plan = build_plan(spec, previous_image=OLD_DIGEST)
    assert not plan.rollback_permitted
    assert "maintenance_required" in plan.rollback_reason
    assert steps_for_rollback(plan) == ()


def test_rollback_is_permitted_after_an_online_migration() -> None:
    """The positive control for the rollback rule.

    Without it, `rollback_permitted` could be hardcoded `False` and the refusal
    test above would still pass.
    """
    spec = load()
    plan = build_plan(spec, previous_image=OLD_DIGEST)
    assert plan.rollback_permitted
    assert steps_for_rollback(plan)


def test_a_first_deployment_reports_nothing_to_roll_back_to_not_a_refusal() -> None:
    """Three answers, not two.

    "Nothing to roll back to" and "rollback forbidden" have different causes
    and different remedies; reporting the first as the second makes a first
    deployment look like a dangerous one.
    """
    spec = load()
    plan = build_plan(spec, previous_image="")
    assert not plan.rollback_permitted
    assert "nothing to roll back to" in plan.rollback_reason
    assert "maintenance" not in plan.rollback_reason


# ── the backup escape hatch is typed ────────────────────────────────────────


def test_skipping_the_backup_requires_a_stated_reason() -> None:
    """An untyped `--force` is how a backup step becomes optional in practice."""
    spec = load()
    with pytest.raises(SpecError):
        build_plan(spec, skip_backup=True, skip_backup_reason="   ")


def test_a_skipped_backup_is_recorded_in_the_plan_notes() -> None:
    spec = load()
    plan = build_plan(
        spec, skip_backup=True, skip_backup_reason="hotfix, no DDL in this release"
    )
    assert any("BACKUP SKIPPED" in note for note in plan.notes)
    assert "backup" not in [step.kind.value for step in plan.steps]


def test_a_product_declaring_no_backup_dataset_says_so_in_the_plan() -> None:
    """Absence is stated, not silent.

    A plan with no backup step and no explanation reads as though backups are
    handled elsewhere. The note is what makes the claim reviewable.
    """
    head, _, tail = DESCRIPTOR.partition("[backup]")
    _, _, after = tail.partition("retention_days = 14")
    spec = ProductDeploymentSpec.loads(head + after, source="<test>")
    plan = build_plan(spec)
    assert any("no backup dataset" in note.lower() for note in plan.notes)


# ── evidence ────────────────────────────────────────────────────────────────


def test_evidence_is_written_before_success_is_declared() -> None:
    """Evidence written only on success is absent exactly when it is wanted."""
    spec = load()
    effects = FakeEffects()
    _, outcome = run(spec, effects)
    assert effects.evidence_written
    recorded = effects.evidence_written[-1]
    assert recorded["image_digest"] == GOOD_DIGEST
    assert recorded["source_revision"] == REVISION
    assert recorded["strategy"] == "warm_candidate"


def test_image_retention_failure_does_not_fail_a_verified_deployment() -> None:
    """The one deliberate fail-open, and it is recorded rather than silent."""
    spec = load()
    effects = FakeEffects()

    def failing_prune(*, retain: int) -> None:
        raise StepFailed("prune_images", "permission denied on /var/lib/docker")

    effects.prune_images = failing_prune  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert outcome.succeeded
    assert any("retention failed" in note for note in outcome.notes)


def test_the_gate_boundary_is_where_the_plan_says_it_is() -> None:
    """`first_mutating_index` is what an operator reads first after a failure.

    If it drifted from the actual step list, the answer to "has anything
    changed?" would be wrong in the one moment it matters most.
    """
    spec = load()
    plan = build_plan(spec)
    boundary = plan.first_mutating_index
    assert all(not step.mutates for step in plan.steps[:boundary])
    assert all(step.mutates for step in plan.steps[boundary:])


# ── canaries for the four repaired semantics ────────────────────────────────
#
# Each of these passed before the repair by asserting nothing about the property
# it now covers. They are written as canaries rather than as ordinary tests
# because the defects were invisible from the outside: the executor returned a
# well-formed outcome in every one of these cases, and the outcome was wrong.


def test_a_migration_that_fails_partway_reports_the_world_as_MUTATED() -> None:
    """The flag decides whether the identical command may be re-run.

    An earlier version set `mutated` only AFTER a step returned, so a migration
    that applied three revisions of five and then failed reported
    `mutated=False` — for precisely the failure where the answer matters most.
    """
    spec = load()
    effects = FakeEffects(
        command_results={"alembic": CommandResult(1, stderr="relation already exists")}
    )
    _, outcome = run(spec, effects)
    assert outcome.failed_step is not None
    assert outcome.failed_step.value == "migrate"
    assert outcome.mutated, "a failed migration has changed the database"


def test_a_gate_failure_still_reports_the_world_as_UNTOUCHED() -> None:
    """The other half, and the reason the flag is worth anything.

    Without this, `mutated=True` could be hardcoded and the canary above would
    pass while the flag told an operator nothing.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(present=False))
    assert outcome.failed_step is not None
    assert outcome.failed_step.value == "verify_image"
    assert not outcome.mutated


def test_evidence_is_written_even_when_the_run_dies_at_the_migration() -> None:
    """Evidence written only on success is absent exactly when it is wanted.

    `record_evidence` used to be an ordinary step near the END of the plan, so a
    deployment that failed at `migrate` left no record of anything that ran.
    """
    spec = load()
    effects = FakeEffects(
        command_results={"alembic": CommandResult(1, stderr="relation already exists")}
    )
    _, outcome = run(spec, effects)
    assert not outcome.succeeded
    assert effects.evidence_written, "a failed run must still leave a record"
    recorded = effects.evidence_written[-1]
    assert recorded["succeeded"] is False
    assert recorded["failed_step"] == "migrate"
    assert recorded["mutated"] is True
    ran = [entry["kind"] for entry in recorded["steps"]]
    assert "backup" in ran and "verify_backup" in ran


def test_an_evidence_write_failure_does_not_mask_the_real_failure() -> None:
    """Replacing the reason a deployment failed with "could not write the file"
    would lose the only thing the operator needed."""
    spec = load()
    effects = FakeEffects(
        command_results={"alembic": CommandResult(1, stderr="relation already exists")}
    )

    def exploding_write(evidence: Mapping[str, object]) -> str:
        raise OSError("read-only filesystem")

    effects.write_evidence = exploding_write  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert outcome.failed_step is not None and outcome.failed_step.value == "migrate"
    assert any("evidence could not be written" in note for note in outcome.notes)


def test_the_verifier_receives_the_real_checksum_and_size() -> None:
    """The two fields verification exists to check were being blanked.

    An earlier version re-parsed the backup out of a formatted note and rebuilt
    it with `size_bytes=0` and an empty checksum, so `verify_backup` could only
    ever confirm the path — and a verifier that compares a checksum was handed
    an empty one on every call.
    """
    spec = load()
    effects = FakeEffects()
    seen: list[BackupResult] = []

    def capture(result: BackupResult) -> bool:
        seen.append(result)
        return True

    effects.verify_backup = capture  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert outcome.succeeded
    assert len(seen) == 1
    assert seen[0].checksum == _CHECKSUM
    assert seen[0].size_bytes == 1024
    assert seen[0].checksum_algorithm == "sha256"


def test_a_backup_returning_no_checksum_is_refused_at_the_backup_step() -> None:
    """Refused where it happened, not two steps later.

    A provider that cannot produce a write-time checksum has not taken a backup
    this facility can make any claim about, and discovering that during
    verification would mean the migration gate had already been cleared.
    """
    spec = load()
    effects = FakeEffects()

    def checksum_free(dataset_code: str, *, timeout_seconds: int) -> BackupResult:
        return BackupResult(dataset_code, "/backups/x.sql.gz", 1024, "", "sha256")

    effects.backup = checksum_free  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert outcome.failed_step is not None and outcome.failed_step.value == "backup"
    assert "checksum" in outcome.failure


def test_rollback_actually_restores_the_previous_digest() -> None:
    """`steps_for_rollback` returned steps and nothing ran them.

    `dotmac-deploy rollback` could therefore only PRINT a plan. Worse, the
    shared `switch` handler targeted the DEPLOYING image, so an executed
    rollback would have restored the image that had just failed.
    """
    spec = load()
    effects = FakeEffects()
    plan = build_plan(spec, previous_image=f"ghcr.io/example/app@{OLD_DIGEST}")
    executor = Executor(spec, effects, sleep=FakeClock().sleep, clock=FakeClock().read)
    outcome = executor.rollback(plan)
    assert outcome.succeeded, outcome.failure
    assert effects.switched_to == [OLD_DIGEST]
    assert all(item.image_digest == OLD_DIGEST for item in effects.observe_roles())
    assert effects.evidence_written


def test_rollback_is_REFUSED_for_a_maintenance_required_release() -> None:
    """And it raises rather than returning a quiet no-op outcome.

    Restoring an image that cannot read the schema now in place is the same
    data-loss shape as deploying it, arriving through the recovery path.
    """
    spec = ProductDeploymentSpec.loads(
        DESCRIPTOR.replace(
            'compatibility = "online"', 'compatibility = "maintenance_required"'
        ),
        source="<test>",
    )
    effects = FakeEffects()
    plan = build_plan(spec, previous_image=f"ghcr.io/example/app@{OLD_DIGEST}")
    executor = Executor(spec, effects, sleep=lambda _: None)
    with pytest.raises(PreconditionFailed) as caught:
        executor.rollback(plan)
    assert "maintenance_required" in str(caught.value)
    assert effects.switched_to == [], "nothing may be switched by a refused rollback"
    assert effects.evidence_written, "the refusal is itself worth recording"


# ── deployment annotations ──────────────────────────────────────────────────


def test_a_start_annotation_is_sent_BEFORE_the_work() -> None:
    """After the fact it is worth almost nothing.

    A deployment that dies mid-migration is the one case somebody most needs a
    marker for, and it is exactly the case where an annotation sent on
    completion never arrives.
    """
    spec = load()
    effects = FakeEffects(
        command_results={"alembic": CommandResult(1, stderr="relation already exists")}
    )
    _, outcome = run(spec, effects)
    assert not outcome.succeeded
    events = [item["event"] for item in effects.annotations]
    assert events[0] == "deployment.start"
    assert "deployment.failure" in events


def test_a_successful_run_annotates_start_then_success() -> None:
    spec = load()
    effects = FakeEffects()
    _, outcome = run(spec, effects)
    assert outcome.succeeded
    assert [item["event"] for item in effects.annotations] == [
        "deployment.start",
        "deployment.success",
    ]
    start = effects.annotations[0]
    assert start["image_digest"] == GOOD_DIGEST
    assert start["git_sha"] == REVISION
    assert start["strategy"] == "warm_candidate"


def test_a_rollback_annotates_itself() -> None:
    spec = load()
    effects = FakeEffects()
    plan = build_plan(spec, previous_image=f"ghcr.io/example/app@{OLD_DIGEST}")
    clock = FakeClock()
    Executor(spec, effects, sleep=clock.sleep, clock=clock.read).rollback(plan)
    assert "deployment.rollback" in [item["event"] for item in effects.annotations]


def test_an_annotation_sink_failure_never_fails_the_deployment() -> None:
    """ADR-0003: failure or disabling of telemetry never blocks the product's
    request path. The same reasoning applies to the deployment path — a
    deployment refused because a dashboard was unreachable is a worse outcome
    than a deployment nobody annotated."""
    spec = load()
    effects = FakeEffects()

    def exploding(annotation: Mapping[str, str]) -> None:
        raise OSError("sink unreachable")

    effects.emit_annotation = exploding  # type: ignore[method-assign]
    _, outcome = run(spec, effects)
    assert outcome.succeeded
    assert any("could not be sent" in note for note in outcome.notes)


def test_a_manifest_that_hashes_to_something_else_is_refused_BEFORE_any_mutation() -> (
    None
):
    """The image digest cannot see this.

    The same image composes whatever manifest it is given, so a deployment could
    run the approved bytes against a module set nobody approved and only
    `dotmac-deploy drift` would notice — afterwards, on a host already serving
    it. An earlier plan verified the image and the source revision and never
    this.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(manifest="sha256:" + "9" * 64))
    assert outcome.failed_step is not None
    assert outcome.failed_step.value == "verify_manifest"
    assert not outcome.mutated


def test_an_unreadable_manifest_is_a_refusal_and_not_a_match() -> None:
    """ "Nothing" is not "agrees".

    A gate that treats an unreadable manifest as agreement goes green precisely
    when the thing it inspects has disappeared.
    """
    spec = load()
    _, outcome = run(spec, FakeEffects(manifest=""))
    assert outcome.failed_step is not None
    assert outcome.failed_step.value == "verify_manifest"
    assert "could not be read" in outcome.failure
