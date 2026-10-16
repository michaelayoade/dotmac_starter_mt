"""Independent executable operands must match frozen authority before consumption."""

import dataclasses

import pytest
from dotmac_deployment_foundation.engine.plan import (
    Step,
    StepKind,
    Strategy,
    build_plan,
)
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import PreconditionFailed

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.foundation_v3_support import grant_for_plan
from tests.unit.host_source_stance import valid_host_source_kwargs
from tests.unit.test_deployment_foundation_execution_binding import (
    RecordingEffects,
    _plan_and_digest,
)
from tests.unit.test_deployment_foundation_failure_injection import (
    GOOD_DIGEST,
    OLD_DIGEST,
    AcceptingVerifier,
    evidence_policy,
    load,
)


def prepared():
    spec = load()
    work = build_plan(spec)
    effects = RecordingEffects(spec=spec)
    frozen, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, frozen)
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=frozen,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    )
    return spec, work, effects, frozen, grant, executor


@pytest.mark.parametrize(
    "field",
    [
        "product",
        "image",
        "image_digest",
        "source_revision",
        "manifest_digest",
        "strategy",
        "remove",
        "add",
        "reorder",
        "argv",
        "timeout",
        "retry",
        "target",
        "kind",
    ],
)
def test_every_operative_work_mutation_refuses_before_consumption(field):
    spec, work, effects, _, grant, executor = prepared()
    if field in (
        "product",
        "image",
        "image_digest",
        "source_revision",
        "manifest_digest",
    ):
        value = (
            "other"
            if field == "product"
            else f"ghcr.io/other/app@{GOOD_DIGEST}"
            if field == "image"
            else OLD_DIGEST
            if field in ("image_digest", "manifest_digest")
            else "e" * 40
        )
        work = dataclasses.replace(work, **{field: value})
    elif field == "strategy":
        work = dataclasses.replace(work, strategy=Strategy.MAINTENANCE)
    else:
        steps = list(work.steps)
        if field == "remove":
            steps.pop()
        elif field == "add":
            steps.append(steps[-1])
        elif field == "reorder":
            steps[0], steps[1] = steps[1], steps[0]
        else:
            index = next(
                i for i, step in enumerate(steps) if step.kind is StepKind.MIGRATE
            )
            changes = {
                "argv": {"command": tuple(reversed(steps[index].command))},
                "timeout": {"timeout_seconds": steps[index].timeout_seconds + 1},
                "retry": {"retries": steps[index].retries + 1},
                "target": {"target": "other"},
                "kind": {"kind": StepKind.SWITCH},
            }
            steps[index] = dataclasses.replace(steps[index], **changes[field])
        work = dataclasses.replace(work, steps=tuple(steps))
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


def test_newly_frozen_supported_work_variant_executes():
    spec, work, effects, _, _, _ = prepared()
    steps = tuple(
        dataclasses.replace(s, timeout_seconds=s.timeout_seconds + 1)
        if s.kind is StepKind.MIGRATE
        else s
        for s in work.steps
    )
    work = dataclasses.replace(work, steps=steps)
    frozen, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, frozen)
    outcome = Executor(
        spec,
        effects,
        grant,
        execution_plan=frozen,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    ).run(work, lock=held_lock(spec.product))
    assert outcome.succeeded
    assert grant.v3_provider.consumed
    assert len(effects.switched_to) == 1


def test_consumption_cannot_mutate_the_executed_or_recorded_operands():
    spec, work, effects, frozen, grant, executor = prepared()
    original = grant.v3_provider.consume_dispatch
    approved_command = work.step(StepKind.MIGRATE).command
    approved_digest = frozen.digest()

    def consume(*, request):
        object.__setattr__(work.step(StepKind.MIGRATE), "command", ("unapproved",))
        object.__setattr__(work, "image", f"ghcr.io/other/app@{OLD_DIGEST}")
        object.__setattr__(spec.migration, "owner_material", "WRONG_OWNER")
        object.__setattr__(frozen.base, "steps", ())
        return original(request=request)

    grant.v3_provider.consume_dispatch = consume
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.succeeded
    assert outcome.plan is not work
    assert outcome.plan.image.endswith(GOOD_DIGEST)
    assert outcome.plan.step(StepKind.MIGRATE).command == approved_command
    assert outcome.execution_plan_digest == approved_digest
    assert all(value != "unapproved" for _, value in effects.mutations)


@pytest.mark.parametrize(
    "mutation",
    [
        "list",
        "bool",
        "zero",
        "negative-timeout",
        "negative-retry",
        "subclass",
        "unknown",
        "authority-list",
        "unsupported",
    ],
)
def test_malformed_shape_or_signed_unsupported_act_cannot_consume(mutation):
    spec, work, effects, frozen, grant, executor = prepared()
    if mutation == "list":
        object.__setattr__(work.steps[0], "command", [])
    elif mutation == "bool":
        object.__setattr__(work.steps[0], "timeout_seconds", True)
    elif mutation == "zero":
        object.__setattr__(work.steps[0], "retries", 0)
    elif mutation == "negative-timeout":
        object.__setattr__(work.steps[0], "timeout_seconds", -1)
    elif mutation == "negative-retry":
        object.__setattr__(work.steps[0], "retries", -1)
    elif mutation == "subclass":

        class EvilStep(Step):
            @property
            def mutates(self):
                return False

        object.__setattr__(work, "steps", (EvilStep(StepKind.SWITCH, "override"),))
    elif mutation == "unknown":
        object.__setattr__(work.steps[0], "kind", "switch")
    elif mutation == "authority-list":
        object.__setattr__(frozen.base, "steps", list(frozen.steps))
    else:
        work = dataclasses.replace(
            work, steps=(Step(StepKind.BOOTSTRAP_PRINCIPALS, "not a dispatch step"),)
        )
        frozen, _ = _plan_and_digest(spec, work, effects=effects)
        grant = grant_for_plan(spec, frozen)
        executor = Executor(
            spec, effects, grant, execution_plan=frozen, **valid_host_source_kwargs()
        )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


@pytest.mark.parametrize(
    "family",
    [
        "resource",
        "health",
        "owner_material",
        "stability",
        "retention",
        "hidden",
        "cache",
    ],
)
def test_actual_spec_mutations_or_missing_parser_input_never_consume(family):
    spec, work, effects, _, grant, executor = prepared()
    if family == "resource":
        object.__setattr__(spec.roles[0].resources, "pids", -1)
    elif family == "health":
        object.__setattr__(spec.roles[0].ready, "timeout_seconds", -1)
    elif family == "owner_material":
        object.__setattr__(spec.migration, "owner_material", "DIFFERENT_OWNER")
    elif family == "stability":
        object.__setattr__(spec, "stability_window_seconds", 999)
    elif family == "retention":
        object.__setattr__(spec, "rollback_images_retained", 7)
    elif family == "hidden":
        object.__setattr__(spec, "material_probes", ("omitted-by-v1-digest",))
    else:
        object.__setattr__(spec, "_execution_parse_input", None)
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


def test_work_description_and_notes_are_diagnostics_and_may_be_unicode():
    spec, work, effects, frozen, grant, executor = prepared()
    work = dataclasses.replace(
        work,
        notes=("Opérateur",),
        steps=tuple(
            dataclasses.replace(s, description="Déploiement") for s in work.steps
        ),
    )
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.succeeded
    assert outcome.execution_plan_digest == frozen.digest()
    assert outcome.plan.notes == ("Opérateur",)
    assert all(s.description == "Déploiement" for s in outcome.plan.steps)
    assert grant.v3_provider.consumed


@pytest.mark.parametrize(
    "previous,permitted",
    [
        ("", False),
        (f"ghcr.io/example/app@{OLD_DIGEST}", True),
        ("unfrozen-target", True),
    ],
)
def test_all_historical_rollback_operands_refuse_without_consumption(
    previous, permitted
):
    spec, work, effects, _, _, _ = prepared()
    frozen, _ = _plan_and_digest(spec, work, operation="rollback", effects=effects)
    grant = grant_for_plan(spec, frozen)
    work = dataclasses.replace(
        work, previous_image=previous, rollback_permitted=permitted
    )
    executor = Executor(
        spec, effects, grant, execution_plan=frozen, **valid_host_source_kwargs()
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as caught:
        executor.rollback(work, lock=held_lock(spec.product))
    assert caught.value.code == "execution_plan.rollback_target_unbound"
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


def test_unclassified_new_record_field_refuses_instead_of_being_silently_dropped(
    monkeypatch,
):
    from dotmac_deployment_foundation.engine.run import _EXECUTION_RECORD_FIELDS

    spec, work, effects, _, grant, executor = prepared()
    # Plant a stale field inventory: exactly the shape a new owner field would
    # expose. The complete dataclass-field equality must bite before copying.
    monkeypatch.setitem(_EXECUTION_RECORD_FIELDS, Step, "kind description command")
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


def test_absent_selected_capability_refuses_before_consumption():
    spec, work, effects, _, grant, executor = prepared()
    effects.run_migration_command = None
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before


@pytest.mark.parametrize("kind", [StepKind.START_CANDIDATE, StepKind.GATE_CANDIDATE])
def test_newly_frozen_empty_candidate_target_refuses_before_consumption(kind):
    spec, work, effects, _, _, _ = prepared()
    work = dataclasses.replace(
        work, steps=(Step(kind, "approved but invalid empty target", target=""),)
    )
    frozen, _ = _plan_and_digest(spec, work, effects=effects)
    grant = grant_for_plan(spec, frozen)
    executor = Executor(
        spec, effects, grant, execution_plan=frozen, **valid_host_source_kwargs()
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed
    assert effects.snapshot() == before
