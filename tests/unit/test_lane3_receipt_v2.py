"""D4: ``scripts/lane3_receipt_v2.py``, the producer of ``RehearsalReceipt.v2``.

Nothing here contacts a host, OpenBao or GitHub. The plan, grant and outcome
come from the same fixture the Foundation v2 tests use; the run coordinates are
a synthetic Actions environment; the evidence bundle is a synthetic age header.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
from typing import Any

import pytest
from dotmac_deployment_foundation.rehearsal import (
    REQUIRED_ITEMS,
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
    RequirementResult,
    RequirementStatus,
    verify_publication,
)

from tests.unit.foundation_v3_support import CONTROLLER, WHEEL
from tests.unit.lane3_evidence_support import REFERENCE, write_evidence, write_topology
from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    REVISION,
    Subject,
    subject,  # noqa: F401 - pytest fixture, used by name
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_execution  # noqa: E402
import lane3_receipt_v2 as producer  # noqa: E402

REPO_ID = 1406738001
OWNER_ID = 335992433
WORKFLOW = ".github/workflows/lane3-exposure-rehearsal.yml"
EXECUTION_REPO = "dotmac-tech/lane3-exposure-execution"
LAUNCHER = "e" * 40

DOCUMENT: dict[str, Any] = {
    "schema": "Lane3ExecutionTopology.v1",
    "starter_repository": "michaelayoade/dotmac_starter_mt",
    "execution_repository": EXECUTION_REPO,
    "execution_repository_id": REPO_ID,
    "execution_owner_id": OWNER_ID,
    "workflow_path": WORKFLOW,
    "environment": {
        "name": "lane3-rehearsal-protected",
        "id": 23551908838,
        "reviewer": "michaelayoade",
    },
    "runner_group": {"name": "lane3-exposure-protected", "id": 4},
    "admitted_launcher_revisions": [LAUNCHER],
    "receipt_artifact": "lane3-rehearsal-receipt",
    "admission_evidence": REFERENCE,
}
_FIXTURE_TMP = tempfile.TemporaryDirectory(prefix="lane3-evidence-")  # removed at exit
_FIXTURE_ROOT = pathlib.Path(_FIXTURE_TMP.name)
write_evidence(_FIXTURE_ROOT)
TOPOLOGY = lane3_execution.parse_topology(DOCUMENT, repo_root=_FIXTURE_ROOT)

ENVIRON: dict[str, str] = {
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "GITHUB_REPOSITORY_OWNER_ID": str(OWNER_ID),
    "GITHUB_RUN_ID": "4242",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": LAUNCHER,
    "GITHUB_WORKFLOW_REF": f"{EXECUTION_REPO}/{WORKFLOW}@refs/heads/main",
}
RUN = ExecutionRunBindingV1(repository_id=REPO_ID, run_id=4242, run_attempt=1)
API_RUN: dict[str, Any] = {
    "id": 4242,
    "run_attempt": 1,
    "repository": {"id": REPO_ID, "owner": {"id": OWNER_ID}},
    "path": WORKFLOW,
    "head_branch": "main",
    "event": "workflow_dispatch",
    "head_sha": LAUNCHER,
}
BUNDLE = b"age-encryption.org/v1\n-> X25519 synthetic\n--- mac\n\x00ciphertext"


def _results() -> list[RequirementResult]:
    return [
        RequirementResult(
            code=item.code,
            status=RequirementStatus.EXECUTED_PASSED,
            detail=f"{item.title} — measured",
            evidence=(f"bundle:{item.code}",),
        )
        for item in REQUIRED_ITEMS
    ]


def _assemble(subj: Subject, **overrides: Any) -> RehearsalReceiptV2:
    fields: dict[str, Any] = {
        "foundation_revision": REVISION,
        "foundation_artifact_digest": WHEEL,
        "descriptor_digest": subj.descriptor_digest,
        "execution_plan": subj.plan,
        "grant": subj.grant,
        "execution_outcome": subj.outcome,
        "fixture_digest": "sha256:" + "d" * 64,
        "evidence_bundle": BUNDLE,
        "controller_identity": CONTROLLER,
        "lease_id": "lease-1",
        "probe_vantage_ref": "inside-vantage@7",
        "execution_run": RUN,
        "started_at": "2026-10-06T10:00:00+00:00",
        "finished_at": "2026-10-06T10:40:00+00:00",
        "results": _results(),
    }
    fields.update(overrides)
    return producer.assemble_receipt(**fields)


# ── the run binding ─────────────────────────────────────────────────────────


def test_the_run_binding_comes_from_the_pinned_runtime() -> None:
    assert producer.execution_run_from_environment(ENVIRON, topology=TOPOLOGY) == RUN


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_REPOSITORY_ID", "1397614141"),
        ("GITHUB_REPOSITORY_OWNER_ID", "1"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("GITHUB_REF", "refs/heads/feature"),
        (
            "GITHUB_WORKFLOW_REF",
            f"{EXECUTION_REPO}/.github/workflows/other.yml@refs/heads/main",
        ),
        ("GITHUB_WORKFLOW_REF", f"{EXECUTION_REPO}/{WORKFLOW}@refs/heads/feature"),
        ("GITHUB_RUN_ID", ""),
        ("GITHUB_RUN_ATTEMPT", "0"),
    ],
)
def test_a_run_outside_the_pinned_surface_is_refused(name: str, value: str) -> None:
    environ = {**ENVIRON, name: value}
    with pytest.raises(producer.ReceiptRefused):
        producer.execution_run_from_environment(environ, topology=TOPOLOGY)


def test_a_missing_runtime_coordinate_is_refused() -> None:
    environ = dict(ENVIRON)
    del environ["GITHUB_RUN_ID"]
    with pytest.raises(producer.ReceiptRefused, match="GITHUB_RUN_ID"):
        producer.execution_run_from_environment(environ, topology=TOPOLOGY)


# ── the evidence bundle ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [b"age-encryption.org/v1\nbody", b"-----BEGIN AGE ENCRYPTED FILE-----\nbody"],
)
def test_both_age_encodings_are_accepted(payload: bytes) -> None:
    assert producer.evidence_bundle_digest(payload).startswith("sha256:")


@pytest.mark.parametrize("payload", [b'{"probes": {}}', b"", b"PK\x03\x04zip"])
def test_a_plaintext_bundle_is_refused(payload: bytes) -> None:
    with pytest.raises(producer.ReceiptRefused, match="not age-encrypted"):
        producer.evidence_bundle_digest(payload)


# ── assembly ────────────────────────────────────────────────────────────────


def test_an_assembled_receipt_is_what_the_oracle_reads(
    subject: Subject,  # noqa: F811
) -> None:
    receipt = _assemble(subject)
    verify_publication(receipt, revision=REVISION)
    again = RehearsalReceiptV2.from_json(receipt.canonical_bytes())
    assert again.content == receipt.content
    assert again.execution_run == RUN
    assert receipt.content["evidence_bundle_digest"] == producer.evidence_bundle_digest(
        BUNDLE
    )


def test_a_chain_break_refuses_as_this_lanes_refusal(
    subject: Subject,  # noqa: F811
) -> None:
    with pytest.raises(producer.ReceiptRefused):
        _assemble(subject, foundation_artifact_digest="sha256:" + "0" * 64)


def test_a_plaintext_bundle_refuses_assembly(
    subject: Subject,  # noqa: F811
) -> None:
    with pytest.raises(producer.ReceiptRefused, match="not age-encrypted"):
        _assemble(subject, evidence_bundle=b'{"vantage": {}}')


def test_a_vantage_address_refuses_assembly(
    subject: Subject,  # noqa: F811
) -> None:
    with pytest.raises(producer.ReceiptRefused):
        _assemble(subject, probe_vantage_ref="vantage.example.net@1")


def test_the_receipt_is_written_create_only(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
) -> None:
    receipt = _assemble(subject)
    out = tmp_path / "receipt.json"
    digest = producer.write_receipt(receipt, out)
    assert digest == receipt.sha256_digest()
    assert RehearsalReceiptV2.from_json(out.read_bytes()).content == receipt.content
    with pytest.raises(FileExistsError):
        producer.write_receipt(receipt, out)


# ── the integration points refuse, naming what is missing ───────────────────


def test_authority_is_unavailable_and_says_why() -> None:
    with pytest.raises(producer.AuthorityUnavailable) as caught:
        producer.acquire_authority()
    missing = caught.value.missing
    assert any(m.startswith("trusted_cp_v3_plan_provider") for m in missing)
    assert any(m.startswith("control_v2_pair_verifier") for m in missing)


def test_execution_is_unavailable_until_q1_is_answered(
    subject: Subject,  # noqa: F811
) -> None:
    with pytest.raises(producer.AuthorityUnavailable, match="Q1"):
        producer.execute_authorized_plan(subject.plan, subject.grant)


def test_plan_acquisition_refuses_without_any_bindings() -> None:
    with pytest.raises(producer.AuthorityUnavailable) as caught:
        producer.acquire_execution_plan(object(), entries=[])
    assert all(m.startswith("trusted_cp") for m in caught.value.missing)


def test_authority_bindings_refuse_without_any_bindings() -> None:
    with pytest.raises(producer.AuthorityUnavailable) as caught:
        producer.acquire_authority_bindings(entries=[])
    assert all(m.startswith("trusted_cp") for m in caught.value.missing)


def test_plan_acquisition_delegates_to_the_mirror_with_discovered_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lane3_plan_v3

    sentinel = object()
    bindings = type("B", (), {"authorization_v3_provider": object()})()
    seen: dict[str, object] = {}

    def _render(base_plan: object, *, bindings: object, spec: object) -> object:
        seen.update(base_plan=base_plan, bindings=bindings, spec=spec)
        return sentinel

    monkeypatch.setattr(lane3_plan_v3, "render_local_execution_plan_v3", _render)
    base = object()
    assert producer.acquire_execution_plan(base, bindings=bindings) is sentinel
    assert seen == {"base_plan": base, "bindings": bindings, "spec": None}

    spec = object()
    assert (
        producer.acquire_execution_plan(base, bindings=bindings, spec=spec) is sentinel
    )
    assert seen == {"base_plan": base, "bindings": bindings, "spec": spec}


def test_authority_acquisition_passes_the_same_spec_to_plan_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lane3_authorization

    plan, grant = object(), object()
    base, bindings, entries, spec = object(), object(), object(), object()
    document = object()
    seen: dict[str, object] = {}

    def acquire(got_base: object, **kwargs: object) -> object:
        seen.update(acquire_base=got_base, acquire_kwargs=kwargs)
        return plan

    def establish(**kwargs: object) -> object:
        seen.update(establish_kwargs=kwargs)
        return grant

    monkeypatch.setattr(producer, "acquire_execution_plan", acquire)
    monkeypatch.setattr(lane3_authorization, "establish_authorization", establish)

    assert producer.acquire_authority(
        descriptor_digest="sha256:descriptor",
        target="lane3-target",
        authorization_document=document,  # type: ignore[arg-type]
        base_plan=base,
        bindings=bindings,
        entries=entries,
        spec=spec,
    ) == (plan, grant)
    assert seen == {
        "acquire_base": base,
        "acquire_kwargs": {
            "bindings": bindings,
            "entries": entries,
            "spec": spec,
        },
        "establish_kwargs": {
            "descriptor_digest": "sha256:descriptor",
            "target": "lane3-target",
            "authorization_document": document,
            "execution_plan": plan,
            "entries": entries,
        },
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"spec": object()},
        {"spec": object(), "target": "lane3-target"},
        {"target": "lane3-target", "deploy_dir": "unused"},
    ],
)
def test_base_plan_refuses_without_spec_target_or_deploy_dir(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(producer.AuthorityUnavailable) as caught:
        producer.build_base_plan(**kwargs)
    assert all(m.startswith("trusted_cp") for m in caught.value.missing)


class _BasePlanSpec:
    def to_canonical_document(self) -> Any:
        return type(
            "Canonical",
            (),
            {"sha256_digest": lambda _self: "sha256:descriptor"},
        )()


class _BasePlanEffects:
    def __init__(self, observations: object) -> None:
        self.observations = observations
        self.observe_calls = 0

    def observe_roles(self) -> object:
        self.observe_calls += 1
        return self.observations


def _patch_base_plan_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import dotmac_deployment_foundation.application_profile as profile_module
    import dotmac_deployment_foundation.engine.plan as plan_module
    import dotmac_deployment_foundation.execution_plan as execution_plan_module

    seen: dict[str, Any] = {
        "deployment_plan": object(),
        "base_plan": object(),
        "observations": object(),
        "prestate": object(),
    }

    class _Prestate:
        @classmethod
        def from_observations(cls, observations: object) -> object:
            seen["prestate_observations"] = observations
            return seen["prestate"]

    def _build_plan(spec: object, **kwargs: Any) -> object:
        seen.update(build_spec=spec, build_kwargs=kwargs)
        return seen["deployment_plan"]

    def _render(spec: object, plan: object, **kwargs: Any) -> object:
        seen.update(render_spec=spec, render_plan=plan, render_kwargs=kwargs)
        return seen["base_plan"]

    found = type("Profile", (), {"as_document": lambda _self: {"schema": "x"}})()
    monkeypatch.setattr(plan_module, "build_plan", _build_plan)
    monkeypatch.setattr(execution_plan_module, "render_execution_plan", _render)
    monkeypatch.setattr(execution_plan_module, "HostPrestateV1", _Prestate)
    monkeypatch.setattr(profile_module, "discover_profile", lambda: found)
    monkeypatch.setattr(
        profile_module,
        "profile_digest",
        lambda document: seen.update(profile_document=document) or "sha256:profile",
    )
    return seen


def test_base_plan_uses_supplied_effects_for_prestate_and_returns_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    seen = _patch_base_plan_modules(monkeypatch)
    effects = _BasePlanEffects(seen["observations"])
    spec = _BasePlanSpec()

    built = producer.build_base_plan(
        spec,
        target="lane3-target",
        deploy_dir=tmp_path,
        effects=effects,
        previous_image="sha256:previous",
        skip_backup=True,
        skip_backup_reason="rehearsal fixture is rebuildable",
    )

    assert built.base_plan is seen["base_plan"]
    assert built.deployment_plan is seen["deployment_plan"]
    assert built.effects is effects
    assert built == (seen["base_plan"], seen["deployment_plan"], effects)
    assert effects.observe_calls == 1
    assert seen["build_spec"] is spec
    assert seen["build_kwargs"] == {
        "previous_image": "sha256:previous",
        "skip_backup": True,
        "skip_backup_reason": "rehearsal fixture is rebuildable",
    }
    assert seen["render_spec"] is spec
    assert seen["render_plan"] is seen["deployment_plan"]
    assert seen["render_kwargs"] == {
        "target": "lane3-target",
        "operation": "deploy",
        "descriptor_digest": "sha256:descriptor",
        "prestate": seen["prestate"],
        "application_profile_digest": "sha256:profile",
    }
    assert seen["prestate_observations"] is seen["observations"]
    assert seen["profile_document"] == {"schema": "x"}


def test_base_plan_prefers_the_bindings_effects_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    seen = _patch_base_plan_modules(monkeypatch)
    effects = _BasePlanEffects(seen["observations"])
    factory_seen: dict[str, Any] = {}

    def _factory(spec: object, deploy_dir: pathlib.Path) -> _BasePlanEffects:
        factory_seen.update(spec=spec, deploy_dir=deploy_dir)
        return effects

    bindings = type("B", (), {"build_effects": staticmethod(_factory)})()
    spec = _BasePlanSpec()

    _base_plan, _deployment_plan, used_effects = producer.build_base_plan(
        spec,
        target="lane3-target",
        deploy_dir=str(tmp_path),
        bindings=bindings,
    )

    assert _deployment_plan is seen["deployment_plan"]
    assert used_effects is effects
    assert factory_seen == {"spec": spec, "deploy_dir": tmp_path}


def test_base_plan_defaults_to_pinned_compose_host_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    import dotmac_deployment_foundation.providers.compose_host as compose_module
    import dotmac_deployment_foundation.toolchain as toolchain_module

    seen = _patch_base_plan_modules(monkeypatch)
    constructed: dict[str, Any] = {}

    class _ComposeEffects(_BasePlanEffects):
        def __init__(self, spec: object, deploy_dir: pathlib.Path, **kwargs: Any):
            super().__init__(seen["observations"])
            constructed.update(spec=spec, deploy_dir=deploy_dir, kwargs=kwargs)

    monkeypatch.setattr(compose_module, "ComposeHostEffects", _ComposeEffects)
    monkeypatch.setattr(
        toolchain_module,
        "DEFAULT_TOOLS",
        {"docker": "/tools/docker", "git": "/tools/git"},
    )
    monkeypatch.setattr(
        toolchain_module,
        "resolve_tool",
        lambda configured, *, what: f"resolved:{what}:{configured}",
    )
    spec = _BasePlanSpec()

    _base_plan, _deployment_plan, used_effects = producer.build_base_plan(
        spec,
        target="lane3-target",
        deploy_dir=str(tmp_path),
    )

    assert _deployment_plan is seen["deployment_plan"]
    assert isinstance(used_effects, _ComposeEffects)
    assert constructed == {
        "spec": spec,
        "deploy_dir": tmp_path,
        "kwargs": {
            "docker_bin": "resolved:docker_bin:/tools/docker",
            "git_bin": "resolved:git_bin:/tools/git",
        },
    }


def test_base_plan_refuses_when_role_observation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    _patch_base_plan_modules(monkeypatch)

    class _FailingEffects:
        def observe_roles(self) -> object:
            raise RuntimeError("incumbent roles cannot be observed")

    with pytest.raises(
        producer.ReceiptRefused, match="observed host prestate"
    ) as caught:
        producer.build_base_plan(
            _BasePlanSpec(),
            target="lane3-target",
            deploy_dir=tmp_path,
            effects=_FailingEffects(),
        )
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_base_plan_refuses_when_rendering_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    import dotmac_deployment_foundation.execution_plan as execution_plan_module

    seen = _patch_base_plan_modules(monkeypatch)

    def _refuse_render(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("base plan cannot be rendered")

    monkeypatch.setattr(execution_plan_module, "render_execution_plan", _refuse_render)
    with pytest.raises(
        producer.ReceiptRefused, match="observed host prestate"
    ) as caught:
        producer.build_base_plan(
            _BasePlanSpec(),
            target="lane3-target",
            deploy_dir=tmp_path,
            effects=_BasePlanEffects(seen["observations"]),
        )
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_execute_wires_executor_with_discovered_admission_and_lock(
    monkeypatch: pytest.MonkeyPatch,
    subject: Subject,  # noqa: F811
) -> None:
    import dotmac_deployment_foundation.engine.lock as lock_module
    import dotmac_deployment_foundation.engine.run as run_module

    constructed: dict[str, object] = {}
    held = object()
    outcome = object()

    class _Lock:
        def __enter__(self) -> object:
            return held

        def __exit__(self, *exc: object) -> None:
            return None

    class _Executor:
        def __init__(self, spec: object, effects: object, grant: object, **kw: object):
            constructed.update(spec=spec, effects=effects, grant=grant, **kw)

        def run(self, plan: object, *, lock: object) -> object:
            constructed.update(ran_plan=plan, ran_lock=lock)
            return outcome

    monkeypatch.setattr(run_module, "Executor", _Executor)
    monkeypatch.setattr(lock_module, "deployment_lock", lambda *a, **k: _Lock())
    admission = object()
    bindings = type(
        "B",
        (),
        {
            "deployment_admission_provider": admission,
            "evidence_policy": None,
            "evidence_verifier": None,
            "recovery_verifier": None,
        },
    )()
    spec = type("S", (), {"product": "lane3_exposure"})()
    deployment_plan = type("P", (), {"image_digest": "sha256:x"})()
    result = producer.execute_authorized_plan(
        subject.plan,
        subject.grant,
        spec=spec,
        deployment_plan=deployment_plan,
        effects=object(),
        bindings=bindings,
    )
    assert result is outcome
    assert constructed["admission_provider"] is admission
    assert constructed["execution_plan"] is subject.plan
    assert constructed["ran_plan"] is deployment_plan
    assert constructed["ran_lock"] is held


def test_execute_falls_back_to_the_lane3_refusing_provider(
    monkeypatch: pytest.MonkeyPatch,
    subject: Subject,  # noqa: F811
) -> None:
    import dotmac_deployment_foundation.engine.lock as lock_module
    import dotmac_deployment_foundation.engine.run as run_module
    from lane3_host_source_admission import Lane3HostSourceAdmissionProvider

    constructed: dict[str, object] = {}

    class _Lock:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *exc: object) -> None:
            return None

    class _Executor:
        def __init__(self, *a: object, **kw: object):
            constructed.update(kw)

        def run(self, plan: object, *, lock: object) -> object:
            return object()

    monkeypatch.setattr(run_module, "Executor", _Executor)
    monkeypatch.setattr(lock_module, "deployment_lock", lambda *a, **k: _Lock())
    producer.execute_authorized_plan(
        subject.plan,
        subject.grant,
        spec=type("S", (), {"product": "lane3_exposure"})(),
        deployment_plan=type("P", (), {"image_digest": "sha256:x"})(),
        effects=object(),
        bindings=None,
    )
    assert isinstance(
        constructed["admission_provider"], Lane3HostSourceAdmissionProvider
    )


# ── the CLI's three exits ───────────────────────────────────────────────────


def _topology_file(tmp_path: pathlib.Path, document: dict[str, Any]) -> str:
    return write_topology(tmp_path, document)


def _api_file(tmp_path: pathlib.Path, document: Any = None) -> str:
    path = tmp_path / "api-run.json"
    path.write_text(json.dumps(API_RUN if document is None else document), "utf-8")
    return str(path)


def _argv(tmp_path: pathlib.Path, *, topology: Any = DOCUMENT, api: Any = None):
    return [
        "--topology",
        _topology_file(tmp_path, topology),
        "--api-run",
        _api_file(tmp_path, api),
        "--receipt-out",
        str(tmp_path / "receipt.json"),
    ]


def test_the_checked_in_topology_is_indeterminate_today(tmp_path: pathlib.Path) -> None:
    # The real file: admission_evidence is null until § 7's proofs exist.
    out = tmp_path / "receipt.json"
    argv = ["--api-run", _api_file(tmp_path), "--receipt-out", str(out)]
    assert producer.main(argv, environ=ENVIRON) == 2
    assert not out.exists()


def test_an_admitted_topology_is_still_indeterminate_without_a_provider(
    tmp_path: pathlib.Path,
) -> None:
    assert producer.main(_argv(tmp_path), environ=ENVIRON) == 2
    assert not (tmp_path / "receipt.json").exists()


def test_a_foreign_run_is_refused_before_authority_is_asked(
    tmp_path: pathlib.Path,
) -> None:
    environ = {**ENVIRON, "GITHUB_REPOSITORY_ID": "1397614141"}
    assert producer.main(_argv(tmp_path), environ=environ) == 1
    assert not (tmp_path / "receipt.json").exists()


def test_an_api_run_that_disagrees_refuses_the_cli(tmp_path: pathlib.Path) -> None:
    api = {**API_RUN, "run_attempt": 2}
    assert producer.main(_argv(tmp_path, api=api), environ=ENVIRON) == 1


def test_an_unreadable_api_run_refuses_the_cli(tmp_path: pathlib.Path) -> None:
    argv = _argv(tmp_path)
    pathlib.Path(argv[3]).write_text("not json", encoding="utf-8")
    assert producer.main(argv, environ=ENVIRON) == 1


def test_an_unadmitted_topology_never_reaches_the_run_check(
    tmp_path: pathlib.Path,
) -> None:
    document = copy.deepcopy(DOCUMENT)
    document["admission_evidence"] = None
    assert producer.main(_argv(tmp_path, topology=document), environ={}) == 2


# ── the API's run must agree with the runtime's ─────────────────────────────


def _agree(api: dict[str, Any], environ: dict[str, str] = ENVIRON) -> None:
    producer.require_api_agreement(api, environ=environ, binding=RUN, topology=TOPOLOGY)


def test_an_agreeing_api_run_is_accepted() -> None:
    _agree(API_RUN)


def _with(path: str, value: Any) -> dict[str, Any]:
    api = copy.deepcopy(API_RUN)
    node = api
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    if value is _DROP:
        del node[parts[-1]]
    else:
        node[parts[-1]] = value
    return api


_DROP = object()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("id", 4243),
        ("run_attempt", 2),
        ("repository.id", 1397614141),
        ("repository.owner.id", 1),
        ("path", ".github/workflows/other.yml"),
        ("head_branch", "feature"),
        ("event", "pull_request"),
        ("head_sha", "f" * 40),
        ("id", _DROP),
        ("head_sha", _DROP),
        ("run_attempt", True),
    ],
)
def test_any_api_disagreement_is_refused(path: str, value: Any) -> None:
    with pytest.raises(producer.ReceiptRefused):
        _agree(_with(path, value))


def test_an_unadmitted_launcher_is_refused_even_when_both_witnesses_agree() -> None:
    other = "f" * 40
    with pytest.raises(producer.ReceiptRefused, match="admitted launcher"):
        _agree({**API_RUN, "head_sha": other}, {**ENVIRON, "GITHUB_SHA": other})


# ── the Starter-owned host-source admission provider ────────────────────────


def test_the_admission_provider_satisfies_the_seam_and_refuses_like_the_reference():
    from dotmac_deployment_foundation.errors import PreconditionFailed
    from dotmac_deployment_foundation.host_source_admission import (
        HostSourceAdmissionProvider,
        RefusingHostSourceAdmissionProvider,
    )
    from lane3_host_source_admission import Lane3HostSourceAdmissionProvider

    ours = Lane3HostSourceAdmissionProvider()
    assert isinstance(ours, HostSourceAdmissionProvider)
    with pytest.raises(PreconditionFailed) as mine:
        ours.admit_host_source()
    with pytest.raises(PreconditionFailed) as reference:
        RefusingHostSourceAdmissionProvider().admit_host_source()
    assert mine.value.code == reference.value.code
    assert mine.value.code


def test_supplied_deployment_plan_is_not_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_deployment_foundation.engine.plan as plan_module
    import dotmac_deployment_foundation.execution_plan as render_module

    spec = object()
    deployment_plan = object()
    observed = object()
    effects = type("Effects", (), {"observe_roles": lambda self: observed})()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        plan_module, "build_plan", lambda *a, **k: pytest.fail("rebuilt")
    )
    monkeypatch.setattr(
        render_module.HostPrestateV1, "from_observations", lambda value: value
    )
    monkeypatch.setattr(producer, "_application_profile_digest", lambda: "")
    spec = type(
        "Spec",
        (),
        {
            "to_canonical_document": lambda self: type(
                "Doc", (), {"sha256_digest": lambda self: "digest"}
            )()
        },
    )()

    def render(got_spec: object, got_plan: object, **kwargs: object) -> object:
        captured.update(spec=got_spec, deployment_plan=got_plan, **kwargs)
        return observed

    monkeypatch.setattr(render_module, "render_execution_plan", render)
    base, used_plan, used = producer.build_base_plan(
        spec,
        target="host",
        deploy_dir="/deploy",
        effects=effects,
        deployment_plan=deployment_plan,
    )
    assert base is observed and used is effects
    assert used_plan is deployment_plan
    assert captured["spec"] is spec
    assert captured["deployment_plan"] is deployment_plan
    assert captured["prestate"] is observed


def test_executor_reuses_live_caller_lock_without_reacquiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_deployment_foundation.engine.lock as lock_module
    import dotmac_deployment_foundation.engine.run as run_module

    spec = type("Spec", (), {"product": "lane3_exposure"})()
    events: list[object] = []

    class Held:
        def require_held(self, *, product: str) -> None:
            events.append(product)

    held = Held()
    outcome = object()

    class Executor:
        def __init__(self, *args: object, **kwargs: object):
            pass

        def run(self, deployment_plan: object, *, lock: object) -> object:
            events.append(lock)
            return outcome

    monkeypatch.setattr(
        lock_module, "deployment_lock", lambda *a, **k: pytest.fail("reacquired")
    )
    monkeypatch.setattr(run_module, "Executor", Executor)
    assert (
        producer.execute_authorized_plan(
            object(),
            object(),
            spec=spec,
            deployment_plan=object(),
            effects=object(),
            lock=held,
        )
        is outcome
    )
    assert events == ["lane3_exposure", held]


def test_dead_caller_lock_refuses_before_executor_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_deployment_foundation.engine.run as run_module
    from dotmac_deployment_foundation.errors import PreconditionFailed

    class Dead:
        def require_held(self, *, product: str) -> None:
            raise PreconditionFailed("dead")

    monkeypatch.setattr(
        run_module, "Executor", lambda *a, **k: pytest.fail("constructed")
    )
    with pytest.raises(PreconditionFailed, match="dead"):
        producer.execute_authorized_plan(
            object(),
            object(),
            spec=type("Spec", (), {"product": "lane3"})(),
            deployment_plan=object(),
            effects=object(),
            lock=Dead(),
        )


def test_public_executor_helper_preserves_prestate_drift_refusal() -> None:
    from types import SimpleNamespace

    from dotmac_deployment_foundation.engine.run import RoleObservation
    from dotmac_deployment_foundation.errors import PreconditionFailed

    from tests.unit import test_deployment_foundation_execution_binding as fixture

    spec, deployment_plan, effects = fixture._fixture()
    plan, _ = fixture._plan_and_digest(spec, deployment_plan, effects=effects)
    grant = fixture._grant(spec, execution_plan=plan)
    bindings = SimpleNamespace(
        deployment_admission_provider=fixture.valid_host_source_kwargs()[
            "admission_provider"
        ],
        evidence_policy=fixture.evidence_policy(),
        evidence_verifier=fixture.AcceptingVerifier(),
    )
    for code, observation in list(effects.roles.items()):
        effects.roles[code] = RoleObservation(
            code, observation.running, "sha256:" + "7" * 64, observation.restarts
        )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="not the host that was authorized"):
        producer.execute_authorized_plan(
            plan,
            grant,
            spec=spec,
            deployment_plan=deployment_plan,
            effects=effects,
            bindings=bindings,
            lock=fixture.held_lock(spec.product),
        )
    fixture._assert_untouched(effects, before)
