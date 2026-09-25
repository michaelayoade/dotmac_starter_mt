"""Lane 3 cannot turn a V1 receipt or workflow text into execution authority."""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.execution_bindings import (
    ENTRY_POINT_GROUP,
    ExecutionBindings,
)
from dotmac_deployment_foundation.host_source import InstalledArtifact

from tests.unit.foundation_v3_support import WHEEL, provider_for
from tests.unit.test_deployment_foundation_execution_seam import _subject

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lane3_authorization as authorization  # noqa: E402

RUNNER = ROOT / "scripts" / "exposure_rehearsal_runner.py"
WORKFLOW = ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"
FIXTURE = ROOT / "scripts" / "exposure-rehearsal" / "product.toml"


class _Entry:
    def __init__(self, bindings: ExecutionBindings) -> None:
        self.name = "lane3-test-bindings"
        self.dist = type("_Dist", (), {"name": "lane3-test-bindings"})()
        self._bindings = bindings

    def load(self):  # type: ignore[no-untyped-def]
        return lambda: self._bindings


def _pair(tmp_path: pathlib.Path, content: object) -> pathlib.Path:
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _installed_v3_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    from dotmac_deployment_foundation import authorization_v3

    monkeypatch.setattr(
        authorization_v3,
        "read_installed_artifact",
        lambda: InstalledArtifact(
            distribution="dotmac-deployment-foundation",
            version="test",
            artifact_digest=Digest.parse(WHEEL),
            installed_content_digest=Digest.parse("sha256:" + "9" * 64),
            read_from="synthetic PEP 610",
        ),
    )


def _v3_pair(tmp_path: pathlib.Path) -> pathlib.Path:
    return _pair(
        tmp_path,
        {
            "authorization_material": {"kind": "authorization"},
            "dispatch_material": {"kind": "dispatch"},
        },
    )


def test_no_installed_v3_provider_is_unanswerable() -> None:
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        authorization.establish_authorization(
            descriptor_digest="sha256:" + "a" * 64,
            target="lane3-target",
            authorization_document=None,
            entries=[],
        )
    assert raised.value.standing is authorization.Standing.UNANSWERABLE
    assert raised.value.exit_status == 2


def test_single_v1_document_refused_with_provider(tmp_path: pathlib.Path) -> None:
    spec, plan = _subject(tmp_path)
    bindings = ExecutionBindings(
        provider="lane3-test-bindings",
        authorization_v3_provider=provider_for(spec, plan),
    )
    path = _pair(tmp_path, {"operation": "deploy", "target_ref": plan.target})
    with pytest.raises(
        authorization.AuthorizationUnverifiable, match="single V1 receipt"
    ) as raised:
        authorization.establish_authorization(
            descriptor_digest=plan.descriptor_digest,
            target=plan.target,
            authorization_document=path,
            execution_plan=plan,
            entries=[_Entry(bindings)],
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


def test_pair_without_trusted_v3_plan_is_non_admitting(tmp_path: pathlib.Path) -> None:
    spec, plan = _subject(tmp_path)
    bindings = ExecutionBindings(
        provider="lane3-test-bindings",
        authorization_v3_provider=provider_for(spec, plan),
    )
    path = _pair(
        tmp_path,
        {
            "authorization_material": {"kind": "authorization"},
            "dispatch_material": {"kind": "dispatch"},
        },
    )
    with pytest.raises(
        authorization.AuthorizationUnverifiable,
        match="no trusted FoundationExecutionPlanV3",
    ) as raised:
        authorization.establish_authorization(
            descriptor_digest=plan.descriptor_digest,
            target=plan.target,
            authorization_document=path,
            entries=[_Entry(bindings)],
        )
    assert raised.value.standing is authorization.Standing.UNANSWERABLE


def test_typed_v3_pair_admits_only_from_fixed_synthetic_composition(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed_v3_wheel(monkeypatch)
    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    bindings = ExecutionBindings(
        provider="lane3-test-bindings", authorization_v3_provider=provider
    )
    grant = authorization.establish_authorization(
        descriptor_digest=plan.descriptor_digest,
        target=plan.target,
        authorization_document=_v3_pair(tmp_path),
        execution_plan=plan,
        entries=[_Entry(bindings)],
    )
    assert grant.operation == "deploy"
    assert grant.execution_plan_digest == plan.digest()

    provider.receipt = dataclasses.replace(provider.receipt, target_ref="other-target")
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        authorization.establish_authorization(
            descriptor_digest=plan.descriptor_digest,
            target=plan.target,
            authorization_document=_v3_pair(tmp_path),
            execution_plan=plan,
            entries=[_Entry(bindings)],
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


def test_preconditions_are_declared_and_refusal_codes_exist() -> None:
    codes = [item.code for item in authorization.PRECONDITIONS]
    assert codes and len(codes) == len(set(codes))
    for item in authorization.PRECONDITIONS:
        assert item.statement and item.owner and item.evidence
    tree = ast.parse(pathlib.Path(authorization.__file__).read_text(encoding="utf-8"))
    cited = {
        element.value
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "unmet"
        and isinstance(node.value, ast.Tuple)
        for element in node.value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }
    assert cited and cited <= set(codes)
    assert authorization.ENTRY_POINT_GROUP == ENTRY_POINT_GROUP


def test_three_standings_keep_refusal_distinct_from_unanswerable() -> None:
    assert authorization.Standing.ATTESTED.exit_status == 0
    assert authorization.Standing.UNATTESTABLE.exit_status == 1
    assert authorization.Standing.UNANSWERABLE.exit_status == 2


def test_workflow_authorization_gate_precedes_probe_and_runner() -> None:
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    bodies = [
        str(step.get("run", "")) for step in workflow["jobs"]["rehearse"]["steps"]
    ]
    installed = next(
        i for i, body in enumerate(bodies) if "pip install --no-deps" in body
    )
    gate = next(
        i for i, body in enumerate(bodies) if "scripts/lane3_authorization.py" in body
    )
    probe = next(
        i for i, body in enumerate(bodies) if "collect_probe_evidence.sh" in body
    )
    runner = next(
        i
        for i, body in enumerate(bodies)
        if "scripts/exposure_rehearsal_runner.py" in body
    )
    assert installed < gate < probe < runner


def test_runner_and_workflow_do_not_accept_single_v1_authority() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "establish_authorization(\n" in runner
    assert runner.index("establish_authorization(\n") < runner.index(
        "load_lease(args.target"
    )
    assert "--authorization-doc-digest" not in runner
    assert "--authorization-document" in runner
    assert "--authorization-document" not in workflow
    assert "authorization_doc_digest" not in workflow
    assert "authorization_document_digest=grant.receipt.descriptor_digest" in runner


def test_runner_has_no_v1_authorize_call() -> None:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "authorize" not in called
    assert "verify_authorization" not in called


def test_lane3_preflight_is_unanswerable_without_composition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = authorization.main(
        ["--descriptor", str(FIXTURE), "--target", "lane3-target"]
    )
    assert status == 2
    assert "unanswerable" in capsys.readouterr().err
