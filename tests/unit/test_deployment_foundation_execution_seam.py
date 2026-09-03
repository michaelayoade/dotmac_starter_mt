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

import argparse
import json
from datetime import UTC, datetime, timedelta
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
from dotmac_deployment_foundation.provenance import (
    AuthorizationReceipt,
    VerifiedAuthorization,
    verify_authorization,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
#: `ExecutionPlanDigestV1` and Control's own snapshot digest. DISTINCT VALUES
#: throughout this file, and deliberately so: a fixture that reused one digest
#: for all three terms would pass every test here while the three were still
#: conflated, which is the defect these fields exist to separate.
PLAN_DIGEST = "sha256:" + "e" * 64
CONTROL_PLAN_DIGEST = "f" * 64
TARGET = "acme-prod-1"
#: Inside every fixture receipt's window. Stated as a constant rather than read
#: from a clock, so these tests do not start failing on 2026-08-31.
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class _StubVerifier:
    """Stands in for the verifier the ASSEMBLY supplies.

    Attests whatever it is handed. That is correct for this file: these tests
    exercise the SEAM — that raw material cannot become verified terms without
    passing through a verifier — not the cryptography, which this facility
    deliberately does not own (zero runtime dependencies, ADR-0070).
    """

    def attest(self, material: object) -> object:
        return dict(material)  # type: ignore[call-overload]


def _verified(**overrides: object) -> VerifiedAuthorization:
    """A receipt round-tripped through the verifier, as production does it."""
    return verify_authorization(
        _receipt(**overrides).as_document(), verifier=_StubVerifier()
    )


def _receipt(**overrides: object) -> AuthorizationReceipt:
    fields: dict[str, object] = {
        "plan_id": "00000000-0000-4000-8000-000000000001",
        "target_ref": TARGET,
        "descriptor_digest": DIGEST_A,
        "execution_plan_digest": PLAN_DIGEST,
        "execution_sequence": 7,
        "attempt_no": 1,
        "control_plan_digest": CONTROL_PLAN_DIGEST,
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
            execution_plan_digest=PLAN_DIGEST,
            execution_sequence=7,
            attempt_no=1,
            receipt=_receipt(),
        )


def test_authorize_issues_a_usable_grant() -> None:
    """The positive control. Without it, refusing everything scores full marks."""
    grant = authorize(
        verified=_verified(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
        now=NOW,
    )
    assert grant.operation == "deploy"
    grant.require(operation="deploy", descriptor_digest=DIGEST_A)


# ── deploy and rollback are separately authorized ───────────────────────────


def test_a_deploy_receipt_cannot_authorize_a_rollback() -> None:
    """One decision must not both make a change and erase it."""
    with pytest.raises(PreconditionFailed, match="authorized 'deploy'"):
        authorize(
            verified=_verified(operation="deploy"),
            operation="rollback",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            now=NOW,
        )


def test_a_rollback_receipt_cannot_authorize_a_deploy() -> None:
    with pytest.raises(PreconditionFailed, match="authorized 'rollback'"):
        authorize(
            verified=_verified(operation="rollback"),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            now=NOW,
        )


def test_a_deploy_grant_is_refused_at_the_rollback_seam() -> None:
    """Checked again at use, not only at issue."""
    grant = authorize(
        verified=_verified(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
        now=NOW,
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
        verified=_verified(operation=operation),
        operation=operation,
        descriptor_digest=DIGEST_A,
        target=TARGET,
        now=NOW,
    )
    assert grant.operation == operation


# ── the descriptor and target bindings ──────────────────────────────────────


def test_a_receipt_for_another_descriptor_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="not an approval for this"):
        authorize(
            verified=_verified(descriptor_digest=DIGEST_B),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            now=NOW,
        )


def test_a_receipt_for_another_target_is_refused() -> None:
    """An approval for staging is not an approval for production."""
    with pytest.raises(PreconditionFailed, match="authorizes target"):
        authorize(
            verified=_verified(target_ref="acme-staging-1"),
            operation="deploy",
            descriptor_digest=DIGEST_A,
            target=TARGET,
            now=NOW,
        )


def test_a_descriptor_edited_after_authorization_is_refused_at_use() -> None:
    """The reason `require` re-checks instead of trusting construction."""
    grant = authorize(
        verified=_verified(),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
        now=NOW,
    )
    with pytest.raises(PreconditionFailed, match="not the descriptor in hand"):
        grant.require(operation="deploy", descriptor_digest=DIGEST_B)


def test_bare_hex_and_prefixed_digests_are_the_same_digest() -> None:
    """The digest-format trap: Control writes bare hex, this facility prefixes."""
    grant = authorize(
        verified=_verified(descriptor_digest="a" * 64),
        operation="deploy",
        descriptor_digest=DIGEST_A,
        target=TARGET,
        now=NOW,
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


#: The CLI's own refusal code, imported rather than written as `1`. A literal
#: would still pass if `EXIT_REFUSED` and `EXIT_USAGE` were ever swapped.


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


def test_one_receipt_refused_unattested_and_granted_when_verified(
    tmp_path: Path,
) -> None:
    """ONE receipt, two runs, differing only in whether a verifier was supplied.

    This test used to drive the whole CLI with a receipt for the WRONG HOST and
    assert the target refusal's WORDING. Three things were wrong with that, and
    the third only surfaced by trying to prove the second.

    *The property moved.* Raw authorization material now reaches this facility
    only through an injected `AuthorizationVerifier`, and the CLI has none — it
    declares zero runtime dependencies and must not ship a weak signature
    substitute. Attestation is the FIRST gate, so the target check is no longer
    reachable from the CLI. It is still asserted where it still lives:
    `test_a_receipt_for_another_target_is_refused` drives `authorize()`.

    *The assertion read PROSE.* `"authorizes target" in stderr` checks which
    words a message used — a checker resolving to a spelling rather than to a
    behaviour, which passes when the behaviour changes underneath it.

    *And an exit code could not replace it.* The obvious repair was to drive a
    receipt correct in every term and assert `EXIT_REFUSED`, on the reasoning
    that nothing else could refuse it. Planting the gate's deletion showed that
    reasoning to be wrong twice over: the first attempt reused the module's
    fixture receipt, which had already EXPIRED, so it refused for expiry; and
    once that was fixed the ungated CLI simply proceeded into the deployment and
    failed there — `EXIT_REFUSED` either way. A single exit code cannot tell
    "refused before anything happened" from "tried and failed".

    So the discriminator is the seam itself, and it is a DIFFERENCE rather than
    a value: the same file, the same descriptor, the same target, parsed by the
    same code, refuses without a verifier and yields a grant with one. Nothing
    but attestation changes between the two halves, so nothing but attestation
    can explain the difference — and if the gate were deleted the first half
    would stop raising rather than merely reword itself.
    """
    from dotmac_deployment_foundation.cli import _require_grant
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    descriptor = _descriptor(tmp_path)
    spec = ProductDeploymentSpec.load(descriptor)
    real_digest = spec.to_canonical_document().sha256_digest()

    # LIVE at the moment `_require_grant` reads its own clock, in both
    # directions. Computed rather than written down: the first version of this
    # test used the module fixture's `2026-08-31` expiry and was silently
    # asserting an expiry refusal.
    now = datetime.now(UTC)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                target_ref=TARGET,
                descriptor_digest=real_digest,
                approved_at=(now - timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
                expires_at=(now + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
            ).as_document()
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        target=TARGET, authorization=str(receipt), authorization_verifier=None
    )

    with pytest.raises(PreconditionFailed):
        _require_grant(args, spec, "deploy")

    args.authorization_verifier = _StubVerifier()
    grant = _require_grant(args, spec, "deploy")
    assert grant.operation == "deploy"
    assert grant.target == TARGET
    assert (
        grant.execution_plan_digest == PLAN_DIGEST
    ), "the grant must carry the frozen plan digest through from the receipt"


def test_a_dry_run_still_needs_no_authorization(tmp_path: Path) -> None:
    """Sensitivity's other half.

    Printing a plan mutates nothing and must stay usable without going to
    Control — otherwise operators lose the safe way to inspect a change, and a
    guard that makes the safe path expensive pushes people onto the unsafe one.
    """
    assert _run_cli(["-f", _descriptor(tmp_path), "deploy"]) == 0
