"""`--execute` alone must always refuse, and the refusal must be observable.

Before this seam existed, the whole distance between "print a plan" and "mutate
production" was one boolean the caller supplied to itself. That is an
*advisory* authorization: the tool asks whether you meant it, and the answer is
whatever you typed.

## What is actually being asserted

Not "a check exists next to the flag" — that is convention, and it holds only
until someone adds a second entry point or calls `Executor` from a script. The
assertion is that the seam is **closed by construction**: `Executor` cannot be
built without an `ExecutionGrant`, and `ExecutionGrant` cannot be built outside
`authorize()`. A caller who skips authorization has nothing to pass.

## Observing the refusal, not the absence of an effect

Every negative test below asserts an explicit refusal — an exception of a named
type, or `EXIT_REFUSED` with a message. None asserts merely that the fake host
recorded no calls.

That distinction is the whole value of the file. `assert effects.calls == []`
passes just as happily against an executor that silently did nothing, a
misspelled step name, or a fake that never wired up — so it cannot tell
"refused" from "broken", and a seam that is broken today looks exactly like a
seam that is working. A refusal has to be *seen* to count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dotmac_deployment_foundation.authorization import (
    OPERATIONS,
    ExecutionGrant,
    authorize,
)
from dotmac_deployment_foundation.errors import (
    PreconditionFailed,
    SpecError,
    UnknownFieldError,
)
from dotmac_deployment_foundation.provenance import AuthorizationReceipt

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
TARGET = "acme-prod-1"


def _receipt(**overrides: object) -> AuthorizationReceipt:
    fields: dict[str, object] = {
        "plan_id": "00000000-0000-4000-8000-000000000001",
        "target_ref": TARGET,
        "descriptor_digest": DIGEST_A,
        "policy_code": "deployment.production",
        "policy_version": 1,
        "decision_ref": "approvals:decision:1",
        "approved_at": "2026-08-30T00:00:00Z",
        "control_version": "0.1.0a4",
        "operation": "deploy",
    }
    fields.update(overrides)
    return AuthorizationReceipt(**fields)  # type: ignore[arg-type]


# ── the grant cannot be fabricated ──────────────────────────────────────────


def test_a_hand_built_grant_is_refused() -> None:
    """The construction-level guarantee, stated as a test.

    If this ever passes silently, "insufficient by construction" has quietly
    become "insufficient by convention" and every other test here is checking
    a door that is no longer attached to a wall.
    """
    with pytest.raises(PreconditionFailed, match="only be produced by authorize"):
        ExecutionGrant(
            object(),  # type: ignore[arg-type]
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            receipt=_receipt(),
        )


def test_authorize_issues_a_usable_grant() -> None:
    """The positive control. Without it, refusing everything scores full marks."""
    grant = authorize(
        receipt=_receipt(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
    )
    assert grant.operation == "deploy"
    grant.require(operation="deploy", descriptor_digest=DIGEST_A)


# ── deploy and rollback are separately authorized ───────────────────────────


def test_a_deploy_receipt_cannot_authorize_a_rollback() -> None:
    """One decision must not both make a change and erase it."""
    with pytest.raises(PreconditionFailed, match="authorized 'deploy'"):
        authorize(
            receipt=_receipt(operation="deploy"),
            operation="rollback",
            descriptor_digest=DIGEST_A,
            target=TARGET,
        )


def test_a_rollback_receipt_cannot_authorize_a_deploy() -> None:
    with pytest.raises(PreconditionFailed, match="authorized 'rollback'"):
        authorize(
            receipt=_receipt(operation="rollback"),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
        )


def test_a_deploy_grant_is_refused_at_the_rollback_seam() -> None:
    """Checked again at use, not only at issue."""
    grant = authorize(
        receipt=_receipt(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
    )
    with pytest.raises(PreconditionFailed, match="authorizes 'deploy', not 'rollback'"):
        grant.require(operation="rollback", descriptor_digest=DIGEST_A)


def test_a_receipt_must_name_an_operation() -> None:
    with pytest.raises(SpecError, match="operation must be"):
        _receipt(operation="")


@pytest.mark.parametrize("operation", OPERATIONS)
def test_both_declared_operations_are_authorizable(operation: str) -> None:
    """Neither operation is accidentally unreachable."""
    grant = authorize(
        receipt=_receipt(operation=operation),
        operation=operation,
        descriptor_digest=DIGEST_A,
        target=TARGET,
    )
    assert grant.operation == operation


# ── the descriptor and target bindings ──────────────────────────────────────


def test_a_receipt_for_another_descriptor_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="not an approval for this"):
        authorize(
            receipt=_receipt(descriptor_digest=DIGEST_B),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
        )


def test_a_receipt_for_another_target_is_refused() -> None:
    """An approval for staging is not an approval for production."""
    with pytest.raises(PreconditionFailed, match="authorizes target"):
        authorize(
            receipt=_receipt(target_ref="acme-staging-1"),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
        )


def test_a_descriptor_edited_after_authorization_is_refused_at_use() -> None:
    """The reason `require` re-checks instead of trusting construction."""
    grant = authorize(
        receipt=_receipt(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
    )
    with pytest.raises(PreconditionFailed, match="not the descriptor in hand"):
        grant.require(operation="deploy", descriptor_digest=DIGEST_B)


def test_bare_hex_and_prefixed_digests_are_the_same_digest() -> None:
    """The digest-format trap: Control writes bare hex, this facility prefixes."""
    grant = authorize(
        receipt=_receipt(descriptor_digest="a" * 64),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
    )
    grant.require(operation="deploy", descriptor_digest="a" * 64)


# ── the receipt document is read strictly ───────────────────────────────────


def _document(**overrides: object) -> dict[str, object]:
    document = dict(_receipt().as_document())
    document.update(overrides)
    return document


def test_a_receipt_document_round_trips() -> None:
    assert AuthorizationReceipt.from_document(_document()).operation == "deploy"


def test_a_receipt_document_missing_the_operation_is_refused() -> None:
    document = _document()
    del document["operation"]
    with pytest.raises(SpecError, match="missing required field"):
        AuthorizationReceipt.from_document(document)


def test_a_receipt_document_with_an_unknown_field_is_refused() -> None:
    """A field we cannot evaluate may carry a condition on the approval."""
    with pytest.raises(UnknownFieldError, match="unknown field"):
        AuthorizationReceipt.from_document(_document(escalation="ignore-drift"))


# ── the CLI seam: --execute alone refuses ───────────────────────────────────


def _descriptor(tmp_path: Path) -> str:
    source = Path("scripts/exposure-rehearsal/product.toml")
    body = source.read_text(encoding="utf-8")
    path = tmp_path / "product.toml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _run_cli(argv: list[str]) -> int:
    from dotmac_deployment_foundation.cli import main

    return main(argv)


def test_execute_without_authorization_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE property. Observed as an explicit refusal, not as an idle host."""
    code = _run_cli(
        [
            "-f",
            _descriptor(tmp_path),
            "deploy",
            "--target",
            TARGET,
            "--execute",
        ]
    )
    assert code != 0
    assert "--authorization" in capsys.readouterr().err


def test_execute_without_a_target_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without an independently stated target the receipt check is vacuous."""
    code = _run_cli(["-f", _descriptor(tmp_path), "deploy", "--execute"])
    assert code != 0
    assert "--target" in capsys.readouterr().err


def test_execute_with_an_unreadable_receipt_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run_cli(
        [
            "-f",
            _descriptor(tmp_path),
            "deploy",
            "--target",
            TARGET,
            "--authorization",
            str(tmp_path / "nope.json"),
            "--execute",
        ]
    )
    assert code != 0
    assert "authorization receipt" in capsys.readouterr().err


def test_execute_with_a_receipt_for_another_target_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: a real receipt file, for the wrong host.

    The receipt carries the descriptor's REAL digest, so the descriptor check
    passes and the target check is the one that fires. With a placeholder
    digest this test would refuse for the wrong reason and would still pass
    if the target binding were deleted entirely.
    """
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    descriptor = _descriptor(tmp_path)
    real_digest = (
        ProductDeploymentSpec.load(descriptor).to_canonical_document().sha256_digest()
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                target_ref="somewhere-else", descriptor_digest=real_digest
            ).as_document()
        ),
        encoding="utf-8",
    )
    code = _run_cli(
        [
            "-f",
            descriptor,
            "deploy",
            "--target",
            TARGET,
            "--authorization",
            str(receipt),
            "--execute",
        ]
    )
    assert code != 0
    assert "authorizes target" in capsys.readouterr().err


def test_a_dry_run_still_needs_no_authorization(tmp_path: Path) -> None:
    """Sensitivity's other half.

    Printing a plan mutates nothing and must stay usable without going to
    Control — otherwise operators lose the safe way to inspect a change, and a
    guard that makes the safe path expensive pushes people onto the unsafe one.
    """
    assert _run_cli(["-f", _descriptor(tmp_path), "deploy"]) == 0
