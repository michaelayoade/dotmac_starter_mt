"""A database change ends by promoting the descriptor it actually produced."""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation import (
    DatabaseCheckpointV1,
    DatabaseDurability,
    DatabaseTransitionAuthorizationV1,
    DatabaseTransitionReceiptV1,
    DatabaseTransitionV1,
    DescriptorPromotionEvidenceV1,
    PreconditionFailed,
    PromotionPendingV1,
    SpecError,
    authorize_database_transition,
    observe_database_postcondition,
    promote_database_descriptor,
    recover_database_promotion,
    require_database_precondition,
)

FROM = "sha256:" + "1" * 64
TO = "sha256:" + "2" * 64
MID = "sha256:" + "3" * 64
PLAN = "sha256:" + "4" * 64
TARGET = "platform-cp-production"


def _transition(**overrides: object) -> DatabaseTransitionV1:
    fields: dict[str, object] = {
        "transition_id": "bootstrap-2026-08-31",
        "target": TARGET,
        "plan_digest": PLAN,
        "from_descriptor_digest": FROM,
        "to_descriptor_digest": TO,
        "durability": DatabaseDurability.ONE_TRANSACTION,
    }
    fields.update(overrides)
    return DatabaseTransitionV1(**fields)  # type: ignore[arg-type]


def _authorization(**overrides: object) -> DatabaseTransitionAuthorizationV1:
    fields: dict[str, object] = {
        "target": TARGET,
        "plan_digest": PLAN,
        "to_descriptor_digest": TO,
        "decision_ref": "wave-7a/decision/42",
        "authorized_at": "2026-08-31T09:00:00Z",
        "control_version": "0.3.0a2",
    }
    fields.update(overrides)
    return DatabaseTransitionAuthorizationV1(**fields)  # type: ignore[arg-type]


def _pending() -> PromotionPendingV1:
    grant = authorize_database_transition(
        transition=_transition(), authorization=_authorization()
    )
    precondition = require_database_precondition(grant, observed_descriptor_digest=FROM)
    return observe_database_postcondition(
        precondition,
        observed_descriptor_digest=TO,
        observed_at="2026-08-31T09:05:00Z",
    )


def _evidence(**overrides: object) -> DescriptorPromotionEvidenceV1:
    fields: dict[str, object] = {
        "transition_id": "bootstrap-2026-08-31",
        "target": TARGET,
        "expected_descriptor_digest": FROM,
        "promoted_descriptor_digest": TO,
        "observed_before_digest": FROM,
        "observed_after_digest": TO,
        "event_ref": "descriptor-promotions/991",
        "promoted_at": "2026-08-31T09:06:00Z",
    }
    fields.update(overrides)
    return DescriptorPromotionEvidenceV1(**fields)  # type: ignore[arg-type]


class _Promoter:
    def __init__(self, evidence: DescriptorPromotionEvidenceV1) -> None:
        self.evidence = evidence
        self.calls: list[tuple[str, str, str, str]] = []

    def compare_and_swap(
        self,
        *,
        transition_id: str,
        target: str,
        expected_descriptor_digest: str,
        promoted_descriptor_digest: str,
    ) -> DescriptorPromotionEvidenceV1:
        self.calls.append(
            (
                transition_id,
                target,
                expected_descriptor_digest,
                promoted_descriptor_digest,
            )
        )
        return self.evidence


def test_authorization_binds_result_plan_and_target() -> None:
    transition = _transition(from_descriptor_digest="A" * 64)
    grant = authorize_database_transition(
        transition=transition,
        authorization=_authorization(
            to_descriptor_digest="2" * 64, plan_digest="4" * 64
        ),
    )

    assert grant.transition.from_descriptor_digest == FROM.replace("1", "a")
    assert grant.transition.to_descriptor_digest == TO


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target", "another-target", "target"),
        ("plan_digest", "5" * 64, "plan"),
        ("to_descriptor_digest", "6" * 64, "result descriptor"),
    ],
)
def test_authorization_refuses_a_different_transition_binding(
    field: str, value: str, message: str
) -> None:
    with pytest.raises(PreconditionFailed, match=message):
        authorize_database_transition(
            transition=_transition(),
            authorization=_authorization(**{field: value}),
        )


def test_from_descriptor_is_the_live_compare_and_swap_precondition() -> None:
    grant = authorize_database_transition(
        transition=_transition(), authorization=_authorization()
    )

    assert require_database_precondition(grant, FROM).observed_descriptor_digest == FROM
    with pytest.raises(PreconditionFailed, match="starting descriptor"):
        require_database_precondition(grant, MID)


def test_one_transaction_operation_refuses_checkpoint_declarations() -> None:
    with pytest.raises(SpecError, match="one transaction"):
        _transition(
            checkpoints=(DatabaseCheckpointV1("after-v017", TO),),
        )


def test_partially_committing_operation_refuses_a_lone_final_candidate() -> None:
    with pytest.raises(SpecError, match="intermediate durable state"):
        _transition(
            durability=DatabaseDurability.DECLARED_CHECKPOINTS,
            checkpoints=(DatabaseCheckpointV1("finished", TO),),
        )


def test_partially_committing_operation_declares_every_ordered_checkpoint() -> None:
    transition = _transition(
        durability=DatabaseDurability.DECLARED_CHECKPOINTS,
        checkpoints=(
            DatabaseCheckpointV1("after-v016", MID),
            DatabaseCheckpointV1("after-v017", TO),
        ),
    )

    assert tuple(item.descriptor_digest for item in transition.checkpoints) == (
        MID,
        TO,
    )


def test_checkpoint_sequence_must_end_at_the_result_descriptor() -> None:
    with pytest.raises(SpecError, match="last checkpoint"):
        _transition(
            durability=DatabaseDurability.DECLARED_CHECKPOINTS,
            checkpoints=(
                DatabaseCheckpointV1("after-v016", TO),
                DatabaseCheckpointV1("after-v017", MID),
            ),
        )


def test_database_commit_creates_an_explicit_promotion_pending_record() -> None:
    pending = _pending()

    assert pending.state == "promotion_pending"
    assert pending.transition.from_descriptor_digest == FROM
    assert pending.transition.to_descriptor_digest == TO
    assert pending.postcondition.observed_descriptor_digest == TO
    assert pending.authorization.to_descriptor_digest == TO


def test_wrong_postcondition_never_becomes_promotion_pending() -> None:
    grant = authorize_database_transition(
        transition=_transition(), authorization=_authorization()
    )
    precondition = require_database_precondition(grant, FROM)
    with pytest.raises(PreconditionFailed, match="postcondition"):
        observe_database_postcondition(
            precondition,
            observed_descriptor_digest=MID,
            observed_at="2026-08-31T09:05:00Z",
        )


def test_promotion_is_a_compare_and_swap_from_start_to_result() -> None:
    promoter = _Promoter(_evidence())

    receipt = promote_database_descriptor(_pending(), promoter)

    assert promoter.calls == [
        ("bootstrap-2026-08-31", TARGET, FROM, TO),
    ]
    assert receipt.state == "promoted"
    assert receipt.from_descriptor_digest == FROM
    assert receipt.to_descriptor_digest == TO
    assert receipt.postcondition.observed_descriptor_digest == TO
    assert receipt.promotion.event_ref == "descriptor-promotions/991"


def test_promotion_evidence_must_prove_the_exact_compare_and_swap() -> None:
    promoter = _Promoter(_evidence(observed_before_digest=MID))

    with pytest.raises(PreconditionFailed, match="observed before"):
        promote_database_descriptor(_pending(), promoter)


def test_recovery_re_drives_the_same_idempotent_promotion() -> None:
    promoter = _Promoter(_evidence())

    receipt = recover_database_promotion(_pending(), promoter)

    assert receipt.state == "promoted"
    assert promoter.calls[0][0] == "bootstrap-2026-08-31"


def test_pending_and_terminal_receipts_round_trip_as_strict_documents() -> None:
    pending = _pending()
    receipt = promote_database_descriptor(pending, _Promoter(_evidence()))

    assert PromotionPendingV1.from_document(pending.as_document()) == pending
    assert DatabaseTransitionReceiptV1.from_document(receipt.as_document()) == receipt
    assert receipt.sha256_digest().startswith("sha256:")


def test_terminal_receipt_document_binds_every_transition_fact() -> None:
    document = promote_database_descriptor(
        _pending(), _Promoter(_evidence())
    ).as_document()

    assert document["from_descriptor_digest"] == FROM
    assert document["to_descriptor_digest"] == TO
    assert document["plan_digest"] == PLAN
    assert document["target"] == TARGET
    assert document["postcondition"]["observed_descriptor_digest"] == TO
    assert document["promotion"]["observed_before_digest"] == FROM
    assert document["promotion"]["observed_after_digest"] == TO


def test_unknown_receipt_fields_are_refused() -> None:
    document = promote_database_descriptor(
        _pending(), _Promoter(_evidence())
    ).as_document()
    document["accepted_by_hand"] = True

    with pytest.raises(SpecError, match="unknown field"):
        DatabaseTransitionReceiptV1.from_document(document)
