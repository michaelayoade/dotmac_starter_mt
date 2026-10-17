"""Source-only startup observation contract; executed only by hosted CI."""

import copy
import hashlib
import json
import tomllib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_deployment_foundation.deployment_evidence import RunStanding
from dotmac_deployment_foundation.engine.lock import deployment_lock
from dotmac_deployment_foundation.engine.plan import Step, StepKind
from dotmac_deployment_foundation.engine.run import CommandResult, DeploymentOutcome
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.live_instance import (
    LiveInstanceRefusal,
    StartupAssignmentConfirmation,
    StartupObservationContext,
)
from dotmac_deployment_foundation.providers.compose_host import (
    ComposeHostEffects,
    RetainedComposeAsset,
)
from dotmac_deployment_foundation.render.compose import (
    render_compose,
    render_compose_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.foundation_v3_support import NOW
from tests.unit.test_deployment_foundation_material_readiness import descriptor
from tests.unit.test_deployment_foundation_provider_operands import authorized


def document():
    doc = tomllib.loads(descriptor())
    doc["schema"] = "ProductDeploymentSpec.v5"
    declarations = []
    for role in doc["roles"]:
        role["environment"] = {"APP_MODE": "production"}
        placement = next(
            p
            for p in doc["compose_topology"]["placements"]
            if p["code"] == role["code"]
        )
        placement["mounts"] = []
        declarations.append(
            {
                "role": role["code"],
                "environment_names": ["APP_MODE"],
                "material_names": sorted(role["materials"]),
                "mounts": [],
            }
        )
    doc["startup_config"] = {
        "schema": "StartupConfig.v1",
        "complete": True,
        "binding": "process_start",
        "roles": declarations,
    }
    return doc


def test_v5_complete_reference_declaration_is_digest_covered():
    doc = document()
    spec = ProductDeploymentSpec.from_document(doc)
    assert spec.startup_config.complete is True
    changed = copy.deepcopy(doc)
    changed["roles"][0]["environment"]["APP_MODE"] = "staging"
    assert (
        ProductDeploymentSpec.from_document(changed)
        .to_canonical_document()
        .canonical_bytes()
        != spec.to_canonical_document().canonical_bytes()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["startup_config"].update(complete=1),
        lambda d: d["startup_config"].update(extra="ignored"),
        lambda d: d["startup_config"]["roles"][0].update(environment_names=[]),
        lambda d: d["startup_config"]["roles"][0].update(material_names=["APP_MODE"]),
        lambda d: d["roles"][0]["environment"].update(APP_MODE="${MODE}"),
        lambda d: d["roles"][0].update(replicas=2),
        lambda d: d["startup_config"]["roles"].reverse(),
    ],
)
def test_v5_refuses_incomplete_ambiguous_or_interpolated_configuration(mutation):
    doc = document()
    mutation(doc)
    with pytest.raises(SpecError):
        ProductDeploymentSpec.from_document(doc)


@pytest.mark.parametrize("schema", ["v1", "v2", "v3", "v4"])
def test_old_schemas_refuse_startup_section(schema):
    doc = document()
    doc["schema"] = "ProductDeploymentSpec." + schema
    with pytest.raises(SpecError):
        ProductDeploymentSpec.from_document(doc)


def test_public_descriptor_alias_cannot_change_private_provider(tmp_path):
    spec = ProductDeploymentSpec.from_document(document())
    path = tmp_path / "compose.yml"
    path.write_text(render_compose(spec))
    provider = ComposeHostEffects(
        spec,
        tmp_path,
        retained_compose_assets={
            spec.image: RetainedComposeAsset(path, render_compose_digest(spec))
        },
    )
    retained = provider.execution_descriptor
    object.__setattr__(spec, "image", "registry.invalid/forged@sha256:" + "0" * 64)
    assert provider.execution_descriptor == retained


def test_custody_confirmation_is_value_free_and_requires_immutable_identity():
    with pytest.raises(ValueError):
        StartupAssignmentConfirmation(
            container_id="a" * 64,
            started_at="2026-08-30T12:00:00Z",
            installation_ref="",
            config_revision_ref="revision-1",
            credential_ref="credential-1",
            credential_version="1",
            material_names=(),
            custody_mounts=(),
            assignment_ref="assignment-1",
            confirmed_at=datetime.now(UTC),
            assigned_at_process_start=True,
            immutable_since_start=True,
        )


def test_refusal_never_includes_child_output():
    error = LiveInstanceRefusal("mismatch")
    assert str(error) == "startup observation refused: mismatch"
    assert not hasattr(error, "output")


# Synthetic custody is explicit test composition, never a production default.
class FixtureCustody:
    def __init__(self, *, on_confirm=None, change=None):
        self.requests = []
        self.on_confirm = on_confirm
        self.change = change or {}

    def confirm_startup_assignment(self, request):
        self.requests.append(request)
        if self.on_confirm:
            self.on_confirm()
        result = StartupAssignmentConfirmation(
            container_id=request.container_id,
            started_at=request.started_at,
            installation_ref="installation-1",
            config_revision_ref="revision-1",
            credential_ref="credential-1",
            credential_version="version-1",
            material_names=request.material_names,
            custody_mounts=request.custody_mounts,
            assignment_ref="assignment-1",
            confirmed_at=NOW,
            assigned_at_process_start=True,
            immutable_since_start=True,
        )
        return replace(result, **self.change)


class NarrowDocker:
    def __init__(self, spec, root):
        self.spec = spec
        self.calls = []
        self.id = "a" * 64
        self.image_id = "sha256:" + "b" * 64
        self.aliases = [spec.image]
        self.environment = None
        self.structure = {
            "id": self.id,
            "running": True,
            "started": "2026-08-30T12:00:00Z",
            "restarts": 0,
            "image": self.image_id,
            "pid": 123,
            "mounts": [
                {
                    "type": "bind",
                    "source": str(root / "settings.txt"),
                    "target": "/etc/product/settings.txt",
                    "rw": False,
                }
            ],
        }

    def __call__(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if argv[1] == "ps":
            return CommandResult(0, self.id)
        if argv[1:3] == ["image", "inspect"]:
            return CommandResult(
                0, json.dumps({"id": self.image_id, "refs": self.aliases})
            )
        assert argv[1] == "inspect" and argv[-1] == self.id
        template = argv[argv.index("--format") + 1]
        if ".Config.Env" in template:
            output = self.environment
            if output is None:
                output = "E0:true\n" + "".join(
                    f"M{i}:present\n"
                    for i, _ in enumerate(sorted(self.spec.role("app").materials))
                )
            return CommandResult(0, output)
        assert ".State.Health" not in template
        assert set(json.loads(json.dumps(self.structure))) == {
            "id",
            "running",
            "started",
            "restarts",
            "image",
            "pid",
            "mounts",
        }
        return CommandResult(0, json.dumps(self.structure))


def provider_fixture(tmp_path):
    doc = document()
    doc["roles"] = doc["roles"][:1]
    doc["startup_config"]["roles"] = doc["startup_config"]["roles"][:1]
    doc["material_probes"] = [
        row for row in doc["material_probes"] if row["role"] == "app"
    ]
    doc["compose_topology"]["placements"] = [
        row for row in doc["compose_topology"]["placements"] if row["code"] != "relay"
    ]
    content = b"approved nonsecret configuration\n"
    (tmp_path / "settings.txt").write_bytes(content)
    process_target = (
        tmp_path / "proc" / "123" / "root" / "etc" / "product" / "settings.txt"
    )
    process_target.parent.mkdir(parents=True)
    process_target.hardlink_to(tmp_path / "settings.txt")
    placement = next(
        row for row in doc["compose_topology"]["placements"] if row["code"] == "app"
    )
    placement["mounts"] = [
        {
            "kind": "repository",
            "source": "settings.txt",
            "source_digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "target": "/etc/product/settings.txt",
            "read_only": True,
            "justification": "Install reviewed immutable nonsecret configuration.",
            "approved_by": "fixture-owner",
        }
    ]
    doc["startup_config"]["roles"][0]["mounts"] = [
        {"mount_index": 0, "classification": "nonsecret_file"}
    ]
    spec = ProductDeploymentSpec.from_document(doc)
    retained = tmp_path / "approved-compose.yml"
    retained.write_text(render_compose(spec))
    docker = NarrowDocker(spec, tmp_path)
    provider = ComposeHostEffects(
        spec,
        tmp_path,
        runner=docker,
        clock=lambda: NOW.timestamp(),
        startup_proc_root=tmp_path / "proc",
        retained_compose_assets={
            spec.image: RetainedComposeAsset(retained, render_compose_digest(spec))
        },
    )
    _, grant, _ = authorized(spec, provider)
    context = StartupObservationContext(
        grant.v3_provider.context,
        spec.to_canonical_document().sha256_digest(),
        grant.execution_plan_digest,
        "control-dispatch:" + grant.receipt.dispatch_id,
        NOW,
    )
    return spec, provider, docker, context


def test_real_provider_pins_full_instance_and_compares_only_approved_configuration(
    tmp_path,
):
    spec, provider, docker, context = provider_fixture(tmp_path)
    custody = FixtureCustody()
    result = provider.observe_live_instance(
        role="app", custody=custody, context=context
    )
    assert result.container_id == docker.id
    assert result.image_id == docker.image_id
    assert result.approved_image_ref == spec.image
    assert result.nonsecret_file_digests == (
        (0, spec.compose_topology.placement("app").mounts[0].source_digest),
    )
    assert len(custody.requests) == 1
    assert sum(call[1] == "ps" for call in docker.calls) == 1
    assert all(call[-1] == docker.id for call in docker.calls if call[1] == "inspect")
    assert "SECRET_SENTINEL" not in repr(result)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda docker: docker.structure.update(restarts=1),
        lambda docker: docker.structure.update(started="2026-08-30T12:01:00Z"),
        lambda docker: docker.structure.update(running=False),
        lambda docker: docker.structure.update(image="sha256:" + "c" * 64),
        lambda docker: docker.structure["mounts"][0].update(rw=True),
    ],
)
def test_instance_or_mount_changes_during_custody_refuse(tmp_path, mutation):
    _, provider, docker, context = provider_fixture(tmp_path)
    with pytest.raises(LiveInstanceRefusal):
        provider.observe_live_instance(
            role="app",
            custody=FixtureCustody(on_confirm=lambda: mutation(docker)),
            context=context,
        )


@pytest.mark.parametrize(
    "output",
    [
        "E0:false\nM0:present\nM1:present",
        "E0:true\nE0:true\nM0:present\nM1:present",
        "E0:true\nM0:present",
        "E0:true\nM0:present\nM1:present\nU",
    ],
)
def test_environment_mismatch_missing_duplicate_and_unexpected_are_value_free(
    tmp_path, output
):
    _, provider, docker, context = provider_fixture(tmp_path)
    docker.environment = output
    with pytest.raises(LiveInstanceRefusal) as error:
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(), context=context
        )
    assert output not in str(error.value)


@pytest.mark.parametrize(
    "difference", ["source", "target", "bytes", "symlink", "replacement", "mutation"]
)
def test_actual_nonsecret_files_are_measured_and_replacement_refuses(
    tmp_path, difference
):
    _, provider, docker, context = provider_fixture(tmp_path)
    action = None
    if difference in ("source", "target"):
        docker.structure["mounts"][0][difference] = "/other/file"
    elif difference == "bytes":
        (tmp_path / "settings.txt").write_bytes(b"SECRET_SENTINEL")
    elif difference == "symlink":
        (tmp_path / "settings.txt").unlink()
        (tmp_path / "settings.txt").symlink_to(tmp_path / "approved-compose.yml")
    elif difference == "replacement":

        def action():
            (tmp_path / "settings.txt").rename(tmp_path / "old-settings.txt")
            (tmp_path / "settings.txt").write_bytes(
                (tmp_path / "old-settings.txt").read_bytes()
            )
    else:

        def action():
            (tmp_path / "settings.txt").write_bytes(b"SECRET_SENTINEL")

    with pytest.raises(LiveInstanceRefusal) as error:
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(on_confirm=action), context=context
        )
    assert "SECRET_SENTINEL" not in str(error.value)
    assert hashlib.sha256(b"SECRET_SENTINEL").hexdigest() not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"container_id": "b" * 64},
        {"started_at": "2026-08-30T12:01:00Z"},
        {"material_names": ()},
        {"immutable_since_start": False},
        {"installation_ref": ""},
        {"config_revision_ref": ""},
        {"credential_version": ""},
    ],
)
def test_missing_or_wrong_custody_instance_and_roster_refuse(tmp_path, change):
    _, provider, _, context = provider_fixture(tmp_path)
    with pytest.raises(LiveInstanceRefusal):
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(change=change), context=context
        )


def test_custody_exception_output_never_enters_failure(tmp_path):
    _, provider, _, context = provider_fixture(tmp_path)

    class BrokenCustody:
        def confirm_startup_assignment(self, request):
            raise RuntimeError("SECRET_SENTINEL")

    with pytest.raises(LiveInstanceRefusal) as error:
        provider.observe_live_instance(
            role="app", custody=BrokenCustody(), context=context
        )
    assert "SECRET_SENTINEL" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_exact_image_alias_is_selected_independently_of_order(tmp_path):
    spec, provider, docker, context = provider_fixture(tmp_path)
    docker.aliases = ["another.invalid/product@sha256:" + "c" * 64, spec.image]
    assert (
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(), context=context
        ).approved_image_ref
        == spec.image
    )


@pytest.mark.parametrize(
    "aliases", [[], ["other.invalid/image@sha256:" + "c" * 64], None]
)
def test_missing_or_wrong_image_reference_refuses(tmp_path, aliases):
    _, provider, docker, context = provider_fixture(tmp_path)
    docker.aliases = aliases
    with pytest.raises(LiveInstanceRefusal):
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(), context=context
        )


class ExecutionEffects:
    """Real observer with scripted mutation seams; no accepting observer stub."""

    def __init__(self, provider, events):
        self.provider = provider
        self.events = events
        self.saved_evidence = None
        self.postflight_fails = False

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def observe_roles(self):
        return ()

    def switch(self, **kwargs):
        self.events.append("switch")

    def run_command(self, command, **kwargs):
        self.events.append("postflight")
        return CommandResult(1 if self.postflight_fails else 0)

    def observe_live_instance(self, **kwargs):
        self.events.append("observe:" + kwargs["role"])
        return self.provider.observe_live_instance(**kwargs)

    def emit_annotation(self, annotation):
        self.events.append(annotation["event"])

    def write_evidence(self, evidence):
        self.saved_evidence = evidence
        return "fixture-evidence.json"


def executor_fixture(tmp_path, *, custody=True):
    spec, provider, docker, _ = provider_fixture(tmp_path)
    events = []
    effects = ExecutionEffects(provider, events)
    steps = (
        Step(StepKind.SWITCH, "switch", timeout_seconds=30),
        Step(StepKind.STABILISE, "stability", timeout_seconds=30),
        Step(
            StepKind.PRODUCT_POSTFLIGHT,
            "postflight",
            target="fixture",
            command=("fixture-postflight",),
            timeout_seconds=30,
        ),
    )
    work, grant, executor = authorized(
        spec,
        effects,
        steps,
        startup_assignment_custody=FixtureCustody() if custody else None,
        sleep=lambda seconds: events.append("stability"),
    )
    original_consume = grant.v3_provider.consume_dispatch

    def consume(*, request):
        original_consume(request=request)
        events.append("consume")

    grant.v3_provider.consume_dispatch = consume
    original_exposure = executor._reconcile_exposure

    def exposure(outcome):
        original_exposure(outcome)
        events.append("exposure")

    executor._reconcile_exposure = exposure
    return spec, work, grant, executor, effects, docker, events


def test_real_executor_consumes_once_and_observes_after_stability_postflight_exposure(
    tmp_path,
):
    spec, work, grant, executor, effects, _, events = executor_fixture(tmp_path)
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.succeeded
    assert outcome.mutated
    assert events.count("consume") == 1
    assert [
        event
        for event in events
        if event
        in {"consume", "switch", "stability", "postflight", "exposure", "observe:app"}
    ] == ["consume", "switch", "stability", "postflight", "exposure", "observe:app"]
    assert len(outcome.live_instance_observations) == 1
    assert grant.v3_provider.consumed
    assert executor._startup_continuation is None and executor._startup_token is None
    assert "live_instance_observations" not in effects.saved_evidence
    assert "startup_config" not in json.dumps(effects.saved_evidence)


def test_missing_custody_refuses_before_consumption_and_effects(tmp_path):
    spec, work, grant, executor, _, _, events = executor_fixture(
        tmp_path, custody=False
    )
    with pytest.raises(LiveInstanceRefusal):
        executor.run(work, lock=held_lock(spec.product))
    assert not grant.v3_provider.consumed and events == []
    assert executor._startup_token is None and executor._startup_continuation is None


def test_consumption_failure_cannot_observe_or_retain_witness(tmp_path):
    spec, work, grant, executor, _, _, events = executor_fixture(tmp_path)
    grant.v3_provider.commit_fails = True
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert events == [] and executor._startup_continuation is None
    assert executor._startup_token is None


def test_postflight_failure_skips_observation_and_preserves_mutated_failure(tmp_path):
    spec, work, _, executor, effects, _, events = executor_fixture(tmp_path)
    effects.postflight_fails = True
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.mutated and not outcome.succeeded
    assert not outcome.live_instance_observations
    assert "exposure" not in events and "observe:app" not in events
    assert executor._startup_token is None and executor._startup_continuation is None


@pytest.mark.parametrize("change", ["restart", "host", "attempt", "clock", "custody"])
def test_failed_startup_observation_preserves_mutation_and_emits_no_partial_evidence(
    tmp_path, change
):
    spec, work, grant, executor, effects, docker, events = executor_fixture(tmp_path)

    def change_at_custody():
        if change == "restart":
            docker.structure["restarts"] += 1
        elif change == "host":
            grant.v3_provider.context = replace(
                grant.v3_provider.context, host_incarnation="sha256:" + "c" * 64
            )
        elif change == "attempt":
            grant.v3_provider.context = replace(grant.v3_provider.context, attempt_no=2)
        elif change == "clock":
            grant.v3_provider.clock = NOW - timedelta(seconds=1)
        else:
            raise RuntimeError("SECRET_SENTINEL")

    executor._startup_custody = FixtureCustody(on_confirm=change_at_custody)
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.mutated and not outcome.succeeded
    assert outcome.standing is RunStanding.FAILED
    assert outcome.failed_step is StepKind.VERIFY_ROLES
    assert outcome.records[-1].target == "app"
    assert outcome.live_instance_observations == ()
    assert "SECRET_SENTINEL" not in repr(outcome)
    assert "SECRET_SENTINEL" not in repr(effects.saved_evidence)
    assert events.count("consume") == 1
    assert executor._startup_token is None and executor._startup_continuation is None
    with pytest.raises(LiveInstanceRefusal):
        executor._require_startup_continuation()


def test_forged_outcome_and_another_executor_cannot_create_consumed_continuation(
    tmp_path,
):
    spec, work, _, executor, _, _, events = executor_fixture(tmp_path)
    forged = DeploymentOutcome(
        plan=work, succeeded=True, control_consumption_ref="control-dispatch:forged"
    )
    with pytest.raises(LiveInstanceRefusal):
        executor._collect_startup_observations(forged)
    assert not events and forged.live_instance_observations == ()


def test_nonconsuming_path_and_rollback_cannot_mint_startup_witness(tmp_path):
    spec, work, grant, executor, _, _, events = executor_fixture(tmp_path)
    executor._verify_host_source()
    executor._snapshot_spec()
    executor._snapshot_and_require_deploy_operands(work)
    _, ref = executor._require_execution_plan("deploy", consume=False)
    assert ref == "" and not grant.v3_provider.consumed
    assert executor._startup_continuation is None
    with pytest.raises(PreconditionFailed):
        executor.rollback(work, lock=held_lock(spec.product))
    assert not events and executor._startup_continuation is None


def test_base_exception_before_inner_try_clears_active_run_token(tmp_path):
    spec, work, _, executor, _, _, _ = executor_fixture(tmp_path)

    def interrupt():
        raise KeyboardInterrupt()

    executor._verify_host_source = interrupt
    with pytest.raises(KeyboardInterrupt):
        executor.run(work, lock=held_lock(spec.product))
    assert executor._startup_token is None
    assert executor._startup_lock is None
    assert executor._startup_continuation is None


def test_reentrant_run_from_custody_is_refused_and_witness_cannot_replay(tmp_path):
    spec, work, _, executor, _, _, events = executor_fixture(tmp_path)
    executor._startup_custody = FixtureCustody(
        on_confirm=lambda: executor.run(work, lock=held_lock(spec.product))
    )
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.mutated and not outcome.succeeded
    assert outcome.live_instance_observations == ()
    assert events.count("consume") == 1
    assert executor._startup_token is None and executor._startup_continuation is None
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    assert events.count("consume") == 1


def test_lock_loss_during_observation_fails_under_original_hold(tmp_path):
    spec, work, _, executor, _, _, _ = executor_fixture(tmp_path)
    with deployment_lock(spec.product, directory=tmp_path / "locks") as lock:
        executor._startup_custody = FixtureCustody(
            on_confirm=lambda: setattr(lock, "live", False)
        )
        outcome = executor.run(work, lock=lock)
    assert not outcome.succeeded and outcome.mutated
    assert outcome.live_instance_observations == ()
    assert outcome.standing is RunStanding.FAILED


def test_expired_receipt_during_custody_fails_after_effects(tmp_path):
    spec, work, grant, executor, _, _, _ = executor_fixture(tmp_path)
    executor._startup_custody = FixtureCustody(
        on_confirm=lambda: setattr(grant.v3_provider, "clock", NOW + timedelta(days=1))
    )
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert not outcome.succeeded and outcome.mutated
    assert outcome.live_instance_observations == ()


def test_final_recheck_keeps_original_full_id_and_detects_same_byte_replacement(
    tmp_path,
):
    _, provider, docker, context = provider_fixture(tmp_path)
    custody = FixtureCustody()
    measurement = provider.observe_live_instance(
        role="app", custody=custody, context=context
    )
    original = tmp_path / "settings.txt"
    content = original.read_bytes()
    original.unlink()
    original.write_bytes(content)
    with pytest.raises(LiveInstanceRefusal):
        provider.recheck_live_instance(measurement, custody=custody)
    assert sum(call[1] == "ps" for call in docker.calls) == 1


def test_old_evidence_projection_is_unchanged_by_additive_startup_result(tmp_path):
    spec, provider, _, context = provider_fixture(tmp_path)
    measurement = provider.observe_live_instance(
        role="app", custody=FixtureCustody(), context=context
    )
    work, _, _ = authorized(spec, provider)
    outcome = DeploymentOutcome(plan=work)
    before = json.dumps(outcome.as_evidence(), sort_keys=True, separators=(",", ":"))
    outcome.live_instance_observations = (measurement,)
    assert (
        json.dumps(outcome.as_evidence(), sort_keys=True, separators=(",", ":"))
        == before
    )


def test_v5_named_mount_and_overlapping_targets_refuse(tmp_path):
    doc = document()
    placement = next(
        row for row in doc["compose_topology"]["placements"] if row["code"] == "app"
    )
    placement["mounts"] = [
        {"kind": "named", "source": "data", "target": "/data", "read_only": True}
    ]
    doc["startup_config"]["roles"][0]["mounts"] = [
        {"mount_index": 0, "classification": "nonsecret_file"}
    ]
    with pytest.raises(SpecError):
        ProductDeploymentSpec.from_document(doc)
    repository = {
        "kind": "repository",
        "source": "settings.txt",
        "source_digest": "sha256:" + "e" * 64,
        "target": "/etc/product",
        "read_only": True,
        "justification": "Approved repository configuration fixture.",
        "approved_by": "fixture-owner",
    }
    placement["mounts"] = [
        repository,
        {**repository, "target": "/etc/product/settings"},
    ]
    doc["startup_config"]["roles"][0]["mounts"] = [
        {"mount_index": index, "classification": "nonsecret_file"} for index in range(2)
    ]
    with pytest.raises(SpecError):
        ProductDeploymentSpec.from_document(doc)


def test_stale_bind_inode_before_observation_is_refused(tmp_path):
    _, provider, _, context = provider_fixture(tmp_path)
    source = tmp_path / "settings.txt"
    content = source.read_bytes()
    source.unlink()
    source.write_bytes(content)
    custody = FixtureCustody()
    with pytest.raises(LiveInstanceRefusal):
        provider.observe_live_instance(role="app", custody=custody, context=context)
    assert not custody.requests


def test_process_namespace_target_symlink_is_refused(tmp_path):
    _, provider, _, context = provider_fixture(tmp_path)
    target = tmp_path / "proc" / "123" / "root" / "etc" / "product" / "settings.txt"
    target.unlink()
    target.symlink_to(tmp_path / "settings.txt")
    with pytest.raises(LiveInstanceRefusal):
        provider.observe_live_instance(
            role="app", custody=FixtureCustody(), context=context
        )


@pytest.mark.parametrize("operation", ["observe", "recheck"])
@pytest.mark.parametrize(
    "field,value", [("restarts", 1), ("pid", 124), ("running", False)]
)
def test_last_file_read_lifecycle_mutation_refuses(
    tmp_path, monkeypatch, operation, field, value
):
    _, provider, docker, context = provider_fixture(tmp_path)
    custody = FixtureCustody()
    prior = (
        provider.observe_live_instance(role="app", custody=custody, context=context)
        if operation == "recheck"
        else None
    )
    import dotmac_deployment_foundation.providers.compose_host as module

    original_read = module.os.read
    reads = 0

    def read(handle, count):
        nonlocal reads
        result = original_read(handle, count)
        reads += 1
        if reads == 4:
            docker.structure[field] = value
        return result

    monkeypatch.setattr(module.os, "read", read)
    with pytest.raises(LiveInstanceRefusal):
        if prior is None:
            provider.observe_live_instance(role="app", custody=custody, context=context)
        else:
            provider.recheck_live_instance(prior, custody=custody)
    assert reads == 4


@pytest.mark.parametrize(
    "difference",
    [
        "container",
        "pid",
        "image",
        "reference",
        "files",
        "environment",
        "installation",
        "revision",
        "credential",
        "version",
        "roster",
        "context",
        "time",
    ],
)
def test_executor_refuses_individually_valid_changed_recheck_measurement(
    tmp_path, difference
):
    spec, work, _, executor, effects, _, _ = executor_fixture(tmp_path)

    def recheck(measurement, *, custody):
        if difference == "container":
            return replace(
                measurement,
                container_id="c" * 64,
                assignment=replace(measurement.assignment, container_id="c" * 64),
            )
        if difference == "pid":
            return replace(measurement, process_pid=124)
        if difference == "image":
            return replace(measurement, image_id="sha256:" + "c" * 64)
        if difference == "reference":
            return replace(
                measurement, approved_image_ref="other.invalid/image@sha256:" + "c" * 64
            )
        if difference == "files":
            return replace(
                measurement, nonsecret_file_digests=((0, "sha256:" + "c" * 64),)
            )
        if difference == "environment":
            return replace(
                measurement, environment_digests=(("APP_MODE", "sha256:" + "c" * 64),)
            )
        if difference in (
            "installation",
            "revision",
            "credential",
            "version",
            "roster",
        ):
            field = {
                "installation": "installation_ref",
                "revision": "config_revision_ref",
                "credential": "credential_ref",
                "version": "credential_version",
                "roster": "material_names",
            }[difference]
            value = ("OTHER",) if difference == "roster" else "other-immutable-identity"
            return replace(
                measurement,
                assignment=replace(measurement.assignment, **{field: value}),
            )
        if difference == "context":
            return replace(
                measurement,
                context=replace(
                    measurement.context, descriptor_digest="sha256:" + "c" * 64
                ),
            )
        earlier = NOW - timedelta(seconds=1)
        # Valid internal chronology, but backwards against the initial reading.
        return replace(
            measurement,
            context=replace(measurement.context, started_at=earlier),
            observation_started_at=earlier,
            observation_finished_at=earlier,
            assignment=replace(measurement.assignment, confirmed_at=earlier),
        )

    effects.recheck_live_instance = recheck
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert outcome.mutated and not outcome.succeeded
    assert outcome.standing is RunStanding.FAILED
    assert outcome.live_instance_observations == ()


def test_custody_mount_is_confirmed_by_reference_and_never_opened(
    tmp_path, monkeypatch
):
    spec, provider, docker, context = provider_fixture(tmp_path)
    # Reparse owner input; do not rebind a provider to a mutated descriptor.
    doc = document()
    doc["roles"] = doc["roles"][:1]
    doc["startup_config"]["roles"] = doc["startup_config"]["roles"][:1]
    doc["material_probes"] = [
        row for row in doc["material_probes"] if row["role"] == "app"
    ]
    doc["compose_topology"]["placements"] = [
        row for row in doc["compose_topology"]["placements"] if row["code"] != "relay"
    ]
    placement = next(
        row for row in doc["compose_topology"]["placements"] if row["code"] == "app"
    )
    placement["mounts"] = [
        {
            "kind": "host_material",
            "source_material": "SIGNING_KEY_SOURCE",
            "target_material": "SIGNING_KEY_TARGET",
            "read_only": True,
            "justification": "Privileged custody owns this immutable startup key file.",
            "approved_by": "fixture-owner",
        }
    ]
    doc["startup_config"]["roles"][0]["mounts"] = [
        {"mount_index": 0, "classification": "custody_file"}
    ]
    spec = ProductDeploymentSpec.from_document(doc)
    retained = tmp_path / "custody-compose.yml"
    retained.write_text(render_compose(spec))
    docker = NarrowDocker(spec, tmp_path)
    docker.structure["mounts"] = [
        {
            "type": "bind",
            "source": "/private/custody/SECRET_SENTINEL",
            "target": "/run/key",
            "rw": False,
        }
    ]
    provider = ComposeHostEffects(
        spec,
        tmp_path,
        runner=docker,
        clock=lambda: NOW.timestamp(),
        startup_proc_root=tmp_path / "proc",
        retained_compose_assets={
            spec.image: RetainedComposeAsset(retained, render_compose_digest(spec))
        },
    )
    _, grant, _ = authorized(spec, provider)
    context = StartupObservationContext(
        grant.v3_provider.context,
        spec.to_canonical_document().sha256_digest(),
        grant.execution_plan_digest,
        "control-dispatch:" + grant.receipt.dispatch_id,
        NOW,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Foundation must not resolve or open custody material")

    monkeypatch.setattr(provider, "_startup_file", forbidden)
    monkeypatch.setattr(provider, "_env_file_values", forbidden)
    custody = FixtureCustody()
    observation = provider.observe_live_instance(
        role="app", custody=custody, context=context
    )
    assert observation.nonsecret_file_digests == ()
    assert len(custody.requests[0].custody_mounts) == 1
    assert custody.requests[0].custody_mounts[0].source_material == "SIGNING_KEY_SOURCE"
    assert "SECRET_SENTINEL" not in repr(observation)


def test_v5_catalog_coordinates_accept_exact_binding_and_refuse_substitution():
    from dotmac_deployment_foundation.database_drift import (
        DatabaseDriftPhase,
        compare_database_contract,
    )
    from dotmac_deployment_foundation.database_structure import (
        DatabaseDescriptorCatalogBindingV1,
        accept_database_structure_comparison,
    )

    from tests.unit import test_deployment_foundation_database_drift as fixture

    spec = ProductDeploymentSpec.from_document(document())
    spec = replace(
        spec,
        product="dotmac_starter_mt",
        database=replace(fixture._contract(), catalogs=(fixture.PRODUCT_CATALOG,)),
        migration=replace(
            spec.migration, expected_heads=("kernel_0016", "deploy_0001")
        ),
    )
    binding = DatabaseDescriptorCatalogBindingV1.from_spec(
        spec, catalogs=spec.database.catalogs
    )
    witness = accept_database_structure_comparison(
        binding,
        fixture.PRODUCT_CATALOG,
        catalog_payload=fixture.PRODUCT_CATALOG_PAYLOAD,
        observation=fixture.STRUCTURE_OBSERVATION,
        observed_at="2026-08-31T05:44:36Z",
        verifier=fixture._RecordingVerifier(
            fixture._verified_comparison(fixture.PRODUCT_CATALOG)
        ),
    )
    assert compare_database_contract(
        spec,
        fixture._observe(),
        phase=DatabaseDriftPhase.POSTFLIGHT,
        catalog_binding=binding,
        structure_witnesses=(witness,),
    ).clean
    changed = replace(fixture.PRODUCT_CATALOG, digest="sha256:" + "e" * 64)
    with pytest.raises(SpecError):
        DatabaseDescriptorCatalogBindingV1.from_spec(spec, catalogs=(changed,))
    forged = replace(binding, catalogs=(changed,))
    with pytest.raises(PreconditionFailed, match="embedded"):
        compare_database_contract(
            spec,
            fixture._observe(),
            phase=DatabaseDriftPhase.POSTFLIGHT,
            catalog_binding=forged,
        )


def test_later_role_failure_publishes_no_partial_observation_tuple(tmp_path):
    spec, _, _, _ = provider_fixture(tmp_path)
    doc = json.loads(spec._execution_parse_input)
    second = copy.deepcopy(doc["roles"][0])
    second["code"] = "relay"
    doc["roles"].append(second)
    second_startup = copy.deepcopy(doc["startup_config"]["roles"][0])
    second_startup.update(role="relay", mounts=[])
    doc["startup_config"]["roles"].append(second_startup)
    doc["compose_topology"]["placements"].append(
        {"code": "relay", "networks": ["back"], "mounts": []}
    )
    for probe in tuple(doc["material_probes"]):
        doc["material_probes"].append({**probe, "role": "relay"})
    spec = ProductDeploymentSpec.from_document(doc)
    retained = tmp_path / "multi-compose.yml"
    retained.write_text(render_compose(spec))

    class MultiDocker(NarrowDocker):
        def __call__(self, argv, **kwargs):
            if argv[1] == "ps" and any(
                arg == "label=com.docker.compose.service=relay" for arg in argv
            ):
                self.id = "d" * 64
                self.structure = {**self.structure, "id": self.id, "mounts": []}
            return super().__call__(argv, **kwargs)

    docker = MultiDocker(spec, tmp_path)
    provider = ComposeHostEffects(
        spec,
        tmp_path,
        runner=docker,
        clock=lambda: NOW.timestamp(),
        startup_proc_root=tmp_path / "proc",
        retained_compose_assets={
            spec.image: RetainedComposeAsset(retained, render_compose_digest(spec))
        },
    )

    class FailSecondCustody(FixtureCustody):
        def confirm_startup_assignment(self, request):
            if request.role == "relay":
                raise RuntimeError("SECRET_SENTINEL")
            return super().confirm_startup_assignment(request)

    custody = FailSecondCustody()
    events = []
    effects = ExecutionEffects(provider, events)
    work, grant, executor = authorized(
        spec,
        effects,
        (Step(StepKind.SWITCH, "switch", timeout_seconds=30),),
        startup_assignment_custody=custody,
    )
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert grant.v3_provider.consumed and outcome.mutated
    assert len(custody.requests) == 1
    assert "observe:app" in events and "observe:relay" in events
    assert not outcome.succeeded and outcome.live_instance_observations == ()
    assert outcome.records[-1].target == "relay"
    assert "SECRET_SENTINEL" not in repr(outcome)


def test_immutable_prechange_v1_v4_canonical_render_plan_and_evidence_goldens():
    """Expected bytes come only from the separately hosted baseline capture."""
    from pathlib import Path

    from dotmac_deployment_foundation.engine.plan import build_plan
    from dotmac_deployment_foundation.execution_plan import (
        HostPrestateV1,
        render_execution_plan,
    )

    root = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "foundation_live_instance"
        / "goldens"
    )
    assert (root / "manifest.json").is_file(), (
        "HOLD: coordinator must capture immutable prechange golden fixtures "
        "on Git-hosted CI"
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["schema"] == "FoundationLegacyGoldenFixtures.v1"
    assert manifest["baseline_commit"] == "1750e2776d5f2374892761763067723ff27595f5"
    assert len(manifest["files"]) == 24
    for artifact in manifest["files"]:
        content = (root / artifact["path"]).read_bytes()
        assert len(content) == artifact["bytes"]
        assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
    for version in ("v1", "v2", "v3", "v4"):
        directory = root / version
        spec = ProductDeploymentSpec.loads((directory / "descriptor.toml").read_text())
        canonical = spec.to_canonical_document()
        assert (
            canonical.canonical_bytes() == (directory / "canonical.json").read_bytes()
        )
        assert render_compose(spec).encode() == (directory / "compose.yml").read_bytes()
        plan = build_plan(spec)
        execution = render_execution_plan(
            spec,
            plan,
            target="legacy-golden-target",
            operation="deploy",
            descriptor_digest=canonical.sha256_digest(),
            prestate=HostPrestateV1.first_deploy(),
            application_profile_digest="",
        )
        assert (
            execution.canonical_bytes()
            == (directory / "execution-plan.json").read_bytes()
        )
        outcome = DeploymentOutcome(
            plan=plan,
            succeeded=True,
            mutated=True,
            descriptor_digest=canonical.sha256_digest(),
            execution_plan_digest=execution.digest(),
            control_plan_digest="c" * 64,
            execution_sequence=1,
            attempt_no=1,
            operation="deploy",
            standing=RunStanding.SUCCEEDED,
        )

        def encode(value):
            return json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()

        assert (
            encode(outcome.as_evidence())
            == (directory / "evidence-v1.json").read_bytes()
        )
        assert (
            encode(
                replace(
                    outcome,
                    control_consumption_ref="control-dispatch:legacy-golden-dispatch",
                ).as_evidence()
            )
            == (directory / "evidence-v2.json").read_bytes()
        )
