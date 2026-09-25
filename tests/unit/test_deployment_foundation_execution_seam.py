"""V1 parses for history, but only a trusted Control V2 pair can issue a grant."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_deployment_foundation.authorization import (
    OPERATIONS,
    ExecutionGrant,
    authorize,
)
from dotmac_deployment_foundation.authorization_v3 import authorize_v3
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.errors import (
    PreconditionFailed,
    SpecError,
    UnknownFieldError,
)
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.provenance import AuthorizationReceipt
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_v3_support import grant_for_plan, provider_for, v3_plan

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
TARGET = "acme-prod-1"


def _receipt(**overrides: object) -> AuthorizationReceipt:
    """A valid historical document, deliberately incapable of admission."""
    fields: dict[str, object] = {
        "plan_id": "00000000-0000-4000-8000-000000000001",
        "target_ref": TARGET,
        "descriptor_digest": DIGEST_A,
        "execution_plan_digest": "sha256:" + "e" * 64,
        "execution_sequence": 7,
        "attempt_no": 1,
        "control_plan_digest": "f" * 64,
        "policy_code": "deployment.production",
        "policy_version": 1,
        "decision_ref": "approvals:decision:1",
        "approved_at": "2026-08-30T00:00:00Z",
        "expires_at": "2026-08-31T00:00:00Z",
        "control_version": "0.1.0a4",
        "operation": "deploy",
    }
    fields.update(overrides)
    return AuthorizationReceipt(**fields)  # type: ignore[arg-type]


def _descriptor(tmp_path: Path) -> str:
    body = Path("scripts/exposure-rehearsal/product.toml").read_text(encoding="utf-8")
    path = tmp_path / "product.toml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _subject(tmp_path: Path, *, operation: str = "deploy"):
    spec = ProductDeploymentSpec.load(_descriptor(tmp_path))
    base = render_execution_plan(
        spec,
        build_plan(spec),
        target=TARGET,
        operation=operation,
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    return spec, v3_plan(base)


def test_a_hand_built_grant_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="only be produced by authorize"):
        ExecutionGrant(
            object(),  # type: ignore[arg-type]
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            execution_plan_digest="sha256:" + "e" * 64,
            execution_sequence=7,
            attempt_no=1,
            receipt=_receipt(),  # type: ignore[arg-type]
            v3_provider=object(),  # type: ignore[arg-type]
            authorization_material_json=b'{"kind":"authorization"}',
            dispatch_material_json=b'{"kind":"dispatch"}',
        )


def test_historical_authorize_always_refuses() -> None:
    with pytest.raises(PreconditionFailed, match="V1 authorization is historical"):
        authorize(
            verified=object(),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            now=datetime.now(UTC),
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_declared_operation_has_a_v3_positive_control(
    tmp_path: Path, operation: str
) -> None:
    spec, plan = _subject(tmp_path, operation=operation)
    grant = grant_for_plan(spec, plan)
    assert grant.operation == operation
    grant.require(operation=operation, descriptor_digest=plan.descriptor_digest)


@pytest.mark.parametrize(
    "granted,requested", [(a, b) for a in OPERATIONS for b in OPERATIONS if a != b]
)
def test_no_operation_authorizes_another(
    tmp_path: Path, granted: str, requested: str
) -> None:
    spec, plan = _subject(tmp_path, operation=granted)
    grant = grant_for_plan(spec, plan)
    with pytest.raises(PreconditionFailed, match="authorizes"):
        grant.require(operation=requested, descriptor_digest=plan.descriptor_digest)


def test_descriptor_drift_is_refused_at_use(tmp_path: Path) -> None:
    spec, plan = _subject(tmp_path)
    grant = grant_for_plan(spec, plan)
    with pytest.raises(PreconditionFailed, match="not the descriptor in hand"):
        grant.require(operation="deploy", descriptor_digest=DIGEST_B)


def test_wrong_target_is_refused_at_v3_issue(tmp_path: Path) -> None:
    from dotmac_deployment_foundation.execution_bindings import ExecutionBindings

    spec, plan = _subject(tmp_path)
    provider = provider_for(spec, plan)
    bindings = ExecutionBindings(
        provider="synthetic-test-host", authorization_v3_provider=provider
    )
    with pytest.raises(PreconditionFailed, match="target in hand"):
        authorize_v3(
            bindings=bindings,
            authorization_material={"kind": "authorization"},
            dispatch_material={"kind": "dispatch"},
            plan=plan,
            operation="deploy",
            descriptor_digest=plan.descriptor_digest,
            target="acme-staging-1",
        )


def test_v1_receipt_document_remains_strictly_parseable() -> None:
    document = _receipt().as_document()
    assert AuthorizationReceipt.from_document(document).operation == "deploy"
    with pytest.raises(SpecError, match="missing required field"):
        AuthorizationReceipt.from_document(
            {k: v for k, v in document.items() if k != "operation"}
        )
    with pytest.raises(UnknownFieldError, match="unknown field"):
        AuthorizationReceipt.from_document({**document, "escalation": "ignore"})


def test_execute_without_authorization_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dotmac_deployment_foundation.cli import main

    code = main(
        ["-f", _descriptor(tmp_path), "deploy", "--target", TARGET, "--execute"]
    )
    assert code != 0
    assert "--authorization" in capsys.readouterr().err


def test_execute_without_target_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dotmac_deployment_foundation.cli import main

    code = main(["-f", _descriptor(tmp_path), "deploy", "--execute"])
    assert code != 0
    assert "--target" in capsys.readouterr().err


def test_cli_refuses_a_single_v1_receipt_even_with_a_request_selected_verifier(
    tmp_path: Path,
) -> None:
    from dotmac_deployment_foundation.cli import _require_grant

    spec, plan = _subject(tmp_path)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt().as_document()), encoding="utf-8")
    args = argparse.Namespace(
        target=TARGET, authorization=str(receipt), authorization_verifier=object()
    )
    with pytest.raises(PreconditionFailed, match="V1 receipt is non-authorizing"):
        _require_grant(args, spec, "deploy", execution_plan=plan)


def test_dry_run_needs_no_authorization(tmp_path: Path) -> None:
    from dotmac_deployment_foundation.cli import main

    assert main(["-f", _descriptor(tmp_path), "deploy"]) == 0
