"""Real providers join their own descriptor to authority before any effect."""

import dataclasses
import tomllib

import pytest
from dotmac_deployment_foundation.engine.plan import Step, StepKind, build_plan
from dotmac_deployment_foundation.engine.run import CommandResult, Executor
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
    render_execution_plan_v2,
)
from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects
from dotmac_deployment_foundation.providers.exposure_host import (
    ComposeHostExposureEffects,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.foundation_v3_support import grant_for_plan, v3_plan
from tests.unit.host_source_stance import valid_host_source_kwargs
from tests.unit.test_deployment_foundation_compose_host import DESCRIPTOR, FakePopen
from tests.unit.test_deployment_foundation_external_recovery import (
    NOW,
    _Accepts,
    _document,
    _envelope,
    _spec,
)


def parsed(**changes):
    document = tomllib.loads(DESCRIPTOR)
    document.update(changes)
    return ProductDeploymentSpec.from_document(document)


class Commands:
    def __init__(self):
        self.calls = []
        self.dumps = []

    def __call__(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        return CommandResult(0, stdout="[]")

    def popen(self, argv, env):
        self.dumps.append(tuple(argv))
        return FakePopen(b"COPY public.example FROM stdin;\n1\n\\.\n")


def concrete(spec, root, commands):
    return ComposeHostEffects(
        spec,
        root,
        runner=commands,
        popen_factory=commands.popen,
        backup_dir=root / "backups",
    )


def authorized(spec, effects, steps=(), *, exposure=None, **kwargs):
    work = dataclasses.replace(build_plan(spec), steps=tuple(steps))
    base = render_execution_plan(
        spec,
        work,
        target="provider-binding-target",
        operation="deploy",
        descriptor_digest=spec.to_canonical_document().sha256_digest(),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    acts = (
        ()
        if exposure is None
        else (ExposureReconciliationV1(family="ipv4", chain="DOCKER-USER"),)
    )
    frozen = v3_plan(render_execution_plan_v2(base, exposure_reconciliations=acts))
    grant = grant_for_plan(spec, frozen)
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=frozen,
        exposure_effects=exposure,
        **valid_host_source_kwargs(),
        **kwargs,
    )
    return work, grant, executor


def unchanged(root, commands, grant):
    assert not grant.v3_provider.consumed
    assert commands.calls == []
    assert commands.dumps == []
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("provider", ["compose", "exposure"])
@pytest.mark.parametrize("difference", ["product", "port", "role", "material"])
def test_independently_parsed_provider_a_cannot_execute_under_b(
    tmp_path, provider, difference
):
    a = parsed()
    document = tomllib.loads(DESCRIPTOR)
    if difference == "product":
        document["product"] = "other-product"
    elif difference == "port":
        document["ingress"]["routes"][0]["port"] = 8010
    elif difference == "role":
        document["roles"][0]["command"] = ["other-server"]
    else:
        document["backup"]["datasets"][0]["material"] = "OTHER_BACKUP_URL"
    b = ProductDeploymentSpec.from_document(document)
    commands = Commands()
    effects = concrete(a if provider == "compose" else b, tmp_path, commands)
    exposure = None
    if provider == "exposure":
        exposure = ComposeHostExposureEffects(a, deploy_dir=tmp_path, runner=commands)
    work, grant, executor = authorized(b, effects, exposure=exposure)
    with pytest.raises(PreconditionFailed, match="different descriptor"):
        executor.run(work, lock=held_lock(b.product))
    unchanged(tmp_path, commands, grant)
    assert (
        effects.execution_descriptor
        == (a if provider == "compose" else b).to_canonical_document().canonical_bytes()
    )
    if exposure is not None:
        assert exposure.comment.endswith(a.product)


@pytest.mark.parametrize("value", [None, True, "sha256:" + "a" * 64, bytearray(b"{}")])
def test_missing_or_digest_boolean_mutable_provider_contract_refuses(tmp_path, value):
    spec = parsed()
    commands = Commands()
    actual = concrete(spec, tmp_path, commands)

    class WrongContract:
        execution_descriptor = value

        def __getattr__(self, name):
            return getattr(actual, name)

    work, grant, executor = authorized(spec, WrongContract())
    with pytest.raises(PreconditionFailed, match="immutable execution_descriptor"):
        executor.run(work, lock=held_lock(spec.product))
    unchanged(tmp_path, commands, grant)


def test_matching_real_provider_captures_verifies_and_records_under_its_own_identity(
    tmp_path,
):
    caller = parsed()
    commands = Commands()
    effects = concrete(caller, tmp_path, commands)
    # Independently parsed equality is accepted; object identity is irrelevant.
    approved = parsed()
    steps = (
        Step(StepKind.BACKUP, "capture", target="primary", timeout_seconds=30),
        Step(StepKind.VERIFY_BACKUP, "verify", target="primary"),
    )
    work, grant, executor = authorized(approved, effects, steps)
    original = grant.v3_provider.consume_dispatch

    def consume(*, request):
        # Original caller aliases may change after admission; provider caches,
        # capture material and execution descriptor must keep their own snapshot.
        object.__setattr__(caller, "product", "unapproved-product")
        object.__setattr__(caller.backup_datasets[0], "material", "UNAPPROVED_URL")
        return original(request=request)

    grant.v3_provider.consume_dispatch = consume
    outcome = executor.run(work, lock=held_lock(approved.product))
    assert outcome.succeeded
    assert grant.v3_provider.consumed
    assert commands.dumps
    assert "example" in commands.dumps[0]
    assert (tmp_path / "backups" / "example").is_dir()
    assert not (tmp_path / "backups" / "unapproved-product").exists()
    assert (
        effects.execution_descriptor
        == approved.to_canonical_document().canonical_bytes()
    )
    assert (tmp_path / "deploy-annotations.jsonl").is_file()
    assert outcome.evidence_path


def test_matching_real_exposure_binding_crosses_admission_and_keeps_dynamic_failure(
    tmp_path,
):
    spec = parsed()
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)
    caller_exposure = parsed()
    exposure = ComposeHostExposureEffects(
        caller_exposure, deploy_dir=tmp_path, runner=commands
    )
    work, grant, executor = authorized(spec, effects, exposure=exposure)
    original = grant.v3_provider.consume_dispatch

    def consume(*, request):
        object.__setattr__(caller_exposure, "product", "unapproved-product")
        return original(request=request)

    grant.v3_provider.consume_dispatch = consume
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert grant.v3_provider.consumed
    assert not outcome.succeeded  # Empty host observations cannot prove exposure.
    assert commands.calls
    assert exposure.execution_descriptor == effects.execution_descriptor
    assert exposure.comment.endswith(spec.product)
    assert all("unapproved-product" not in " ".join(call) for call in commands.calls)
    assert (tmp_path / "deploy-annotations.jsonl").is_file()


@pytest.mark.parametrize(
    "steps",
    [
        (Step(StepKind.BACKUP, "unknown", target="absent"),),
        (Step(StepKind.VERIFY_BACKUP, "unknown", target="absent"),),
        (Step(StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT, "unknown", target="absent"),),
        (
            Step(
                StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT,
                "wrong mode",
                target="primary",
            ),
        ),
        (Step(StepKind.VERIFY_BACKUP, "before capture", target="primary"),),
        (
            Step(StepKind.VERIFY_BACKUP, "before capture", target="primary"),
            Step(StepKind.BACKUP, "too late", target="primary"),
        ),
    ],
)
def test_newly_frozen_invalid_dataset_work_refuses_before_consumption(tmp_path, steps):
    spec = parsed()
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)
    work, grant, executor = authorized(spec, effects, steps)
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    unchanged(tmp_path, commands, grant)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "object_store"},
        {"kind": "volume"},
        {"encryption": "age"},
        {"encryption": "gpg"},
        {"offsite": "remote-vault"},
    ],
)
def test_native_capture_unsupported_requirements_refuse_without_host_or_material_access(
    tmp_path, change, monkeypatch
):
    document = tomllib.loads(DESCRIPTOR)
    document["backup"]["datasets"][0].update(change)
    spec = ProductDeploymentSpec.from_document(document)
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)

    def no_material_access(*args):
        raise AssertionError("preflight must not resolve materials")

    monkeypatch.setattr(effects, "_materials_env", no_material_access)
    steps = (Step(StepKind.BACKUP, "unsupported capture", target="primary"),)
    work, grant, executor = authorized(spec, effects, steps)
    with pytest.raises(PreconditionFailed):
        executor.run(work, lock=held_lock(spec.product))
    unchanged(tmp_path, commands, grant)


@pytest.mark.parametrize(
    "mode", ["local-capture", "missing-receipt", "no-verifier", "bad-receipt", "valid"]
)
def test_external_dataset_uses_existing_receipt_owner_before_consumption(
    tmp_path, mode
):
    spec = _spec(external=True)
    dataset = spec.backup_datasets[0]
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)
    kind = (
        StepKind.BACKUP
        if mode == "local-capture"
        else StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT
    )
    steps = (Step(kind, "external recovery", target=dataset.code),)
    document = _document(spec, dataset)
    if mode == "bad-receipt":
        document["descriptor_digest"] = "sha256:" + "f" * 64
    receipts = {} if mode == "missing-receipt" else {dataset.code: _envelope(document)}
    work, grant, executor = authorized(
        spec,
        effects,
        steps,
        recovery_receipts=receipts,
        recovery_verifier=None if mode == "no-verifier" else _Accepts(),
        now_epoch=NOW,
    )
    if mode == "valid":
        outcome = executor.run(work, lock=held_lock(spec.product))
        assert outcome.succeeded
        assert grant.v3_provider.consumed
        assert commands.dumps == []
        assert any("recovery receipt" in note for note in outcome.notes)
    else:
        with pytest.raises(PreconditionFailed):
            executor.run(work, lock=held_lock(spec.product))
        unchanged(tmp_path, commands, grant)


def test_cli_factory_mismatch_refuses_before_observation_or_consumption(
    tmp_path, monkeypatch
):
    import argparse

    from dotmac_deployment_foundation import cli
    from dotmac_deployment_foundation.execution_bindings import ExecutionBindings

    a = parsed()
    b = parsed(product="other-product")
    commands = Commands()
    effects = concrete(a, tmp_path, commands)
    # Real provider and real discovery factory boundary; no mocked Executor.
    bindings = ExecutionBindings(
        provider="independent-host", build_effects=lambda *_: effects
    )
    monkeypatch.setattr(cli, "_load", lambda _: b)
    monkeypatch.setattr(cli, "_load_bindings", lambda _: bindings)

    def no_grant(*args, **kwargs):
        raise AssertionError("mismatched provider must refuse before authority use")

    monkeypatch.setattr(cli, "_require_grant", no_grant)
    args = argparse.Namespace(
        descriptor="unused.toml",
        previous_image="",
        skip_backup=False,
        skip_backup_reason="",
        execute=True,
        target="provider-binding-target",
        authorization="unused-authorization.json",
        provider="independent-host",
        deploy_dir=str(tmp_path),
    )
    with pytest.raises(PreconditionFailed, match="different descriptor"):
        cli.cmd_deploy(args)
    assert commands.calls == []
    assert commands.dumps == []
    assert list(tmp_path.iterdir()) == []


def test_supported_backup_preparation_is_pure(tmp_path, monkeypatch):
    spec = parsed()
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)

    def no_material_access(*args):
        raise AssertionError("pure preparation must not read or resolve material")

    monkeypatch.setattr(effects, "_env_file_values", no_material_access)
    monkeypatch.setattr(effects, "_materials_env", no_material_access)
    effects.preflight_backup("primary")
    assert commands.calls == []
    assert commands.dumps == []
    assert list(tmp_path.iterdir()) == []


def test_external_receipt_is_rechecked_at_execution_after_preflight(tmp_path):
    spec = _spec(external=True)
    dataset = spec.backup_datasets[0]
    commands = Commands()
    effects = concrete(spec, tmp_path, commands)
    document = _document(spec, dataset)
    steps = (
        Step(
            StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT, "external", target=dataset.code
        ),
    )
    work, grant, executor = authorized(
        spec,
        effects,
        steps,
        recovery_receipts={dataset.code: _envelope(document)},
        recovery_verifier=_Accepts(),
        now_epoch=NOW,
    )
    original = grant.v3_provider.consume_dispatch

    def consume(*, request):
        document["descriptor_digest"] = "sha256:" + "f" * 64
        return original(request=request)

    grant.v3_provider.consume_dispatch = consume
    outcome = executor.run(work, lock=held_lock(spec.product))
    assert grant.v3_provider.consumed
    assert not outcome.succeeded
    assert outcome.failed_step is StepKind.VERIFY_EXTERNAL_RECOVERY_RECEIPT
    assert not outcome.mutated
    assert commands.dumps == []
    assert (tmp_path / "deploy-annotations.jsonl").is_file()
