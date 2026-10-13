"""Closed candidate-material receipts and version-gated deployment probes."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from dotmac_deployment_foundation.engine.plan import StepKind, build_plan
from dotmac_deployment_foundation.engine.run import (
    CommandResult,
    Executor,
    RoleObservation,
)
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.material_readiness import (
    MaterialProbeObservation,
    MaterialProbePhase,
    MaterialProbeStatus,
    parse_material_probe_result,
)
from dotmac_deployment_foundation.providers.compose_host import (
    ComposeHostEffects,
    RetainedComposeAsset,
    _bounded_material_probe,
)
from dotmac_deployment_foundation.render.compose import (
    render_compose,
    render_compose_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.test_deployment_foundation_compose_topology import (
    DESCRIPTOR,
    _staged_host,
)


def probe_declaration(role: str, phase: str) -> str:
    argv = ["/opt/venv/bin/python", "-m", "product.material_probe"]
    return "\n".join(
        (
            "",
            "[[material_probes]]",
            f'role = "{role}"',
            f'phase = "{phase}"',
            "argv = " + json.dumps(argv),
            "timeout_seconds = 30",
            "",
        )
    )


def descriptor() -> str:
    text = DESCRIPTOR.replace("ProductDeploymentSpec.v3", "ProductDeploymentSpec.v4")
    for role in ("app", "relay"):
        for phase in ("pre_mutation", "post_migration"):
            text += probe_declaration(role, phase)
    return text


def receipt(status: str = "ready") -> str:
    return json.dumps(
        {
            "schema": "CandidateMaterialProbeResult.v1",
            "status": status,
            "checks": [{"name": "boot.jwt", "status": status}],
        }
    )


def test_material_probe_capability_has_consistent_public_exports() -> None:
    import dotmac_deployment_foundation as foundation
    import dotmac_deployment_foundation.engine as engine
    from dotmac_deployment_foundation.engine.run import CandidateMaterialProbeEffects

    assert foundation.CandidateMaterialProbeEffects is CandidateMaterialProbeEffects
    assert engine.CandidateMaterialProbeEffects is CandidateMaterialProbeEffects
    assert "CandidateMaterialProbeEffects" in foundation.__all__
    assert "CandidateMaterialProbeEffects" in engine.__all__


def test_closed_receipt_positive_control() -> None:
    assert parse_material_probe_result(receipt()).status is MaterialProbeStatus.READY


@pytest.mark.parametrize(
    "wire",
    [
        receipt() + receipt(),
        receipt().replace(
            '"ready", "checks"', '"ready", "secret": "planted", "checks"'
        ),
        receipt().replace('"boot.jwt"', '"/secret/path"'),
        receipt().replace('"checks":', '"status":"ready","checks":'),
        json.dumps(
            {
                "schema": "CandidateMaterialProbeResult.v1",
                "status": "ready",
                "checks": [],
            }
        ),
        receipt().replace('"status": "ready"}', '"status": "refused"}'),
        " " * 16385,
    ],
)
def test_closed_receipt_refuses_corruption_without_echo(wire: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_material_probe_result(wire)
    assert "planted" not in str(error.value)
    assert "/secret/path" not in str(error.value)


def test_v4_covers_each_role_and_orders_post_gate() -> None:
    spec = ProductDeploymentSpec.loads(descriptor())
    assert len(spec.material_probes) == 4
    plan = build_plan(spec)
    kinds = [step.kind for step in plan.steps]
    assert kinds.index(StepKind.VERIFY_HEADS) < kinds.index(
        StepKind.VERIFY_POST_MIGRATION_MATERIALS
    )
    assert kinds.index(StepKind.VERIFY_POST_MIGRATION_MATERIALS) < kinds.index(
        StepKind.SWITCH
    )


def test_v4_missing_active_role_coverage_refuses() -> None:
    spec = ProductDeploymentSpec.loads(descriptor())
    with pytest.raises(SpecError):
        replace(spec, material_probes=spec.material_probes[:-1])


def test_old_schemas_refuse_new_declarations() -> None:
    with pytest.raises(SpecError):
        ProductDeploymentSpec.loads(
            descriptor().replace("ProductDeploymentSpec.v4", "ProductDeploymentSpec.v3")
        )


def test_phase_is_closed() -> None:
    with pytest.raises(ValueError):
        MaterialProbePhase("later")


@pytest.mark.parametrize(
    "exit_code,output", [(0, "x" * 20000), (0, "secret diagnostic"), (1, receipt())]
)
def test_transport_refuses_overflow_malformed_and_failed_child(
    exit_code: int, output: str
) -> None:
    result = _bounded_material_probe(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write(sys.argv[1]);sys.exit(int(sys.argv[2]))",
            output,
            str(exit_code),
        ],
        timeout_seconds=5,
        env=dict(os.environ),
        max_stdout_bytes=16384,
    )
    assert result.status is MaterialProbeStatus.UNAVAILABLE
    assert "secret diagnostic" not in repr(result)


def test_transport_accepts_valid_child_and_discards_stderr() -> None:
    result = _bounded_material_probe(
        [
            sys.executable,
            "-c",
            "import sys;sys.stderr.write('secret diagnostic');print(sys.argv[1])",
            receipt(),
        ],
        timeout_seconds=5,
        env=dict(os.environ),
        max_stdout_bytes=16384,
    )
    assert result.status is MaterialProbeStatus.READY
    assert "secret diagnostic" not in repr(result)


def test_transport_timeout_reaps_child(tmp_path: Path) -> None:
    import signal

    pid_file = tmp_path / "probe.pid"
    result = _bounded_material_probe(
        [
            sys.executable,
            "-c",
            "import os,pathlib,sys,time;"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",
            str(pid_file),
        ],
        timeout_seconds=1,
        env=dict(os.environ),
        max_stdout_bytes=16384,
    )
    assert result.status is MaterialProbeStatus.UNAVAILABLE
    assert pid_file.exists(), "child must have actually started"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), signal.SIGCONT)


def test_governance_enrolls_closed_immutable_probe_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    profile = json.loads((root / ".dotmac/standards-profile.json").read_text())
    surface = next(
        row
        for row in profile["typed_contract_surfaces"]
        if row["surface_id"] == "candidate-material-probe-contract"
    )
    assert surface["require_public_annotations"]
    assert surface["forbid_any"]
    assert surface["require_immutable_records"]
    authority = next(
        row
        for row in profile["authorities"]
        if row["authority_id"] == "candidate-material-readiness"
    )
    assert authority["decision_interface"].endswith(
        "Executor._verify_candidate_materials"
    )


def test_environment_file_empty_value_wins_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR)
    monkeypatch.setenv("DATABASE_URL", "planted-parent-value")
    (tmp_path / ".env").write_text("DATABASE_URL=\n")
    effects = ComposeHostEffects(spec, tmp_path)
    assert "DATABASE_URL" not in effects.resolved_materials()
    assert effects._materials_env(("DATABASE_URL",))["DATABASE_URL"] == ""


def test_running_zero_replica_role_requires_coverage_before_any_probe() -> None:
    spec = ProductDeploymentSpec.loads(descriptor())
    spec = replace(
        spec,
        roles=tuple(
            replace(role, replicas=0) if role.code == "relay" else role
            for role in spec.roles
        ),
        material_probes=tuple(
            probe for probe in spec.material_probes if probe.role == "app"
        ),
    )
    calls = []

    class Effects:
        def observe_roles(self):
            return [RoleObservation("relay", True, "sha256:" + "a" * 64, 0)]

        def probe_candidate_materials(self, probe, *, image):
            calls.append(probe)
            return MaterialProbeObservation(
                image, probe.role, probe.phase, parse_material_probe_result(receipt())
            )

    executor = object.__new__(Executor)
    executor._spec = spec
    executor._effects = Effects()
    with pytest.raises(PreconditionFailed, match="coverage"):
        executor._verify_candidate_materials(
            MaterialProbePhase.PRE_MUTATION, build_plan(spec)
        )
    assert calls == []


def test_role_observation_exception_is_sanitized() -> None:
    spec = ProductDeploymentSpec.loads(descriptor())

    class Effects:
        def observe_roles(self):
            raise RuntimeError("planted-secret-diagnostic")

        def probe_candidate_materials(self, probe, *, image):
            raise AssertionError("must not run")

    executor = object.__new__(Executor)
    executor._spec = spec
    executor._effects = Effects()
    with pytest.raises(PreconditionFailed) as error:
        executor._verify_candidate_materials(
            MaterialProbePhase.PRE_MUTATION, build_plan(spec)
        )
    assert "planted-secret-diagnostic" not in str(error.value)
    assert "planted-secret-diagnostic" not in repr(error.value)


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "cleanup",
        "image_id",
        "image_env",
        "//app",
        "//opt",
        "/app",
        "parent_pointer",
    ],
)
def test_provider_uses_role_exact_candidate_and_cleans_only_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    spec = ProductDeploymentSpec.loads(descriptor())
    asset = tmp_path / "retained.yml"
    asset.write_text(render_compose(spec))
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if any(".Config.Env" in item for item in argv):
            return CommandResult(
                0, "PYTHONPATH\n" if defect == "image_env" else "PATH\n"
            )
        if argv[1:3] == ["rm", "-f"] and defect == "cleanup":
            return CommandResult(1, "planted-secret-diagnostic")
        if "{{.Image}}" in argv and defect == "image_id":
            return CommandResult(0, "sha256:" + "f" * 64)
        if "{{.Config.WorkingDir}}" in argv:
            return CommandResult(0, "/app")
        return CommandResult(0, "sha256:" + "e" * 64)

    def transport(argv, **kwargs):
        calls.append(argv)
        assert kwargs["max_stdout_bytes"] == 16384
        assert kwargs["env"]["SIGNING_KEY_TARGET"] == "/run/key"
        assert "--no-deps" in argv
        assert argv[argv.index("--entrypoint") + 1] == "/opt/venv/bin/python"
        return parse_material_probe_result(receipt())

    effects = ComposeHostEffects(
        spec,
        tmp_path,
        runner=runner,
        material_probe_transport=transport,
        retained_compose_assets={
            spec.image: RetainedComposeAsset(asset, render_compose_digest(spec))
        },
    )
    _staged_host(tmp_path)
    if defect in ("//app", "//opt", "/app"):
        with (tmp_path / ".env").open("a") as env_file:
            env_file.write(f"SIGNING_KEY_TARGET={defect}\n")
    if defect == "parent_pointer":
        monkeypatch.setenv("SIGNING_KEY_TARGET", "/app")
    observation = effects.probe_candidate_materials(
        spec.material_probes[0], image=spec.image
    )
    expected_ready = defect in ("none", "parent_pointer")
    assert (observation.result.status is MaterialProbeStatus.READY) == expected_ready
    assert "planted-secret-diagnostic" not in repr(observation)
    assert observation.image == spec.image
    cleanup = calls[-1]
    assert cleanup[1:3] == ["rm", "-f"]
    assert cleanup[-1].startswith("foundation-material-probe-")
    if not expected_ready:
        return
    image_id_reads = [argv for argv in calls if "{{.Id}}" in argv]
    container_id_reads = [argv for argv in calls if "{{.Image}}" in argv]
    assert image_id_reads[0][1:3] == ["image", "inspect"]
    assert image_id_reads[0][-1] == spec.image
    assert container_id_reads[0][-1] == cleanup[-1]


def test_post_phase_refusal_is_a_mutation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dotmac_deployment_foundation.errors import StepFailed

    executor = object.__new__(Executor)

    def refuse(*args):
        raise PreconditionFailed("planted")

    monkeypatch.setattr(executor, "_verify_candidate_materials", refuse)
    spec = ProductDeploymentSpec.loads(descriptor())
    plan = build_plan(spec)
    step = next(
        step
        for step in plan.steps
        if step.kind is StepKind.VERIFY_POST_MIGRATION_MATERIALS
    )
    with pytest.raises(StepFailed, match="post-migration"):
        executor._do_verify_post_migration_materials(step, plan, None)


@pytest.mark.parametrize(
    "schema",
    [
        "ProductDeploymentSpec.v1",
        "ProductDeploymentSpec.v2",
        "ProductDeploymentSpec.v3",
    ],
)
def test_v4_only_field_is_absent_from_older_normalization(schema: str) -> None:
    from dotmac_deployment_foundation.document import _normalize

    # Normalize the same object at each older schema discriminator: adding the
    # new dataclass field must not change any previously canonicalized payload.
    spec = ProductDeploymentSpec.loads(descriptor())
    assert "material_probes" not in _normalize(spec, descriptor_schema=schema)
    assert "material_probes" in _normalize(
        spec, descriptor_schema="ProductDeploymentSpec.v4"
    )


@pytest.mark.parametrize("failure_phase", ["none", "pre_mutation", "post_migration"])
def test_full_executor_material_gates_order_bootstrap_and_handoff(
    failure_phase: str,
) -> None:
    from dotmac_deployment_foundation.deployment_evidence import RunStanding
    from dotmac_deployment_foundation.execution_plan_v2 import render_execution_plan_v2

    from tests.unit.deployment_lock_harness import held_lock
    from tests.unit.foundation_v3_support import v3_plan
    from tests.unit.host_source_stance import valid_host_source_kwargs
    from tests.unit.test_deployment_foundation_bootstrap_invocation import _bootstrap
    from tests.unit.test_deployment_foundation_execution_binding import (
        AcceptingVerifier,
        RecordingEffects,
        _grant,
        _plan_and_digest,
        evidence_policy,
    )
    from tests.unit.test_deployment_foundation_failure_injection import (
        DESCRIPTOR as BASE,
    )
    from tests.unit.test_deployment_foundation_failure_injection import (
        FakeClock,
    )

    text = BASE.replace("ProductDeploymentSpec.v1", "ProductDeploymentSpec.v4")
    text += (
        "\n[compose_topology]\nroles_depend_on_migrate = false\n"
        '[[compose_topology.networks]]\ncode = "back"\n'
    )
    for role in ("app", "worker", "beat", "migrate"):
        text += (
            f'\n[[compose_topology.placements]]\ncode = "{role}"\nnetworks = ["back"]\n'
        )
        if role == "migrate":
            text += 'profiles = ["ops"]\n'
    for role in ("app", "worker", "beat"):
        for phase in MaterialProbePhase:
            text += probe_declaration(role, phase.value)
    spec = ProductDeploymentSpec.loads(text)
    plan = build_plan(spec)
    events = []

    class ProbeEffects(RecordingEffects):
        def probe_candidate_materials(self, probe, *, image):
            events.append(probe.phase.value)
            result = parse_material_probe_result(
                receipt("refused" if probe.phase.value == failure_phase else "ready")
            )
            return MaterialProbeObservation(image, probe.role, probe.phase, result)

        def bootstrap_principal_credential(self, bootstrap):
            events.append("bootstrap")
            return super().bootstrap_principal_credential(bootstrap)

    effects = ProbeEffects()
    baseline, _digest = _plan_and_digest(spec, plan, effects=effects)
    execution_plan = v3_plan(
        render_execution_plan_v2(baseline.base, principal_bootstraps=(_bootstrap(),))
    )
    clock = FakeClock()
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan=execution_plan),
        execution_plan=execution_plan,
        sleep=clock.sleep,
        clock=clock.read,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    )
    outcome = executor.run(plan, lock=held_lock(spec.product))
    assert "pre_mutation" in events, "fixture must reach the actual readiness gate"
    if failure_phase == "pre_mutation":
        assert effects.bootstrapped == []
        assert effects.mutations == []
        assert not outcome.mutated
        assert outcome.standing is RunStanding.REFUSED
        assert outcome.failed_step is StepKind.VERIFY_MATERIALS
    else:
        assert (
            events.index("pre_mutation")
            < events.index("bootstrap")
            < events.index("post_migration")
        )
        assert effects.bootstrapped
        if failure_phase == "post_migration":
            assert outcome.standing is RunStanding.FAILED
            assert outcome.mutated
            assert outcome.failed_step is StepKind.VERIFY_POST_MIGRATION_MATERIALS
            assert effects.switched_to == []
            assert effects.candidate_started_with == []
        else:
            assert outcome.succeeded, outcome.failure
