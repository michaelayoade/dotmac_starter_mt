"""Pure contract checks for held-partition admission and the seal hold list."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import opening_hold_contracts as hold
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_lane import (
    OpeningLaneAssemblyAuthorityV1,
    import_opening_lane_partition,
)
from dotmac_billing.opening_window_contracts import SealOpeningWindowV1
from dotmac_kernel.cache import PlatformScope

from tests.unit import test_billing_opening_receivable_contracts as evidence

DIGEST = "sha256:" + "c" * 64


def _command(**override):
    imported = replace(evidence._candidate(), scope=PlatformScope())
    imported = replace(
        imported, manifest=replace(imported.manifest, scope=PlatformScope())
    )
    values = {
        "scope": PlatformScope(),
        "window_id": uuid4(),
        "import_command": imported,
        "disposition": hold.OpeningHoldDispositionV1("disposition:1", DIGEST),
        "maker_ref": "maker",
        "approval_request_id": uuid4(),
    }
    values.update(override)
    return hold.AdmitHeldOpeningAccountV2(**values)


def _approval(command, **override):
    values = {
        "request_id": command.approval_request_id,
        "subject_type": hold.HOLD_APPROVAL_SUBJECT_TYPE,
        "subject_id": hold.opening_hold_subject_id(command),
        "content_digest": hold.opening_hold_content_digest(command),
        "requester_ref": "maker",
        "approver_refs": ("checker",),
    }
    values.update(override)
    return hold.HeldOpeningHoldApprovalV1(**values)


def test_standing_distinct_checker_approval_is_accepted():
    command = _command()
    approval = _approval(command)
    assert hold.require_hold_maker_checker(command, approval) is approval


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"approver_refs": ("maker",)}, "opening_hold_maker_checker"),
        ({"approver_refs": ("checker", "maker")}, "opening_hold_maker_checker"),
        ({"approver_refs": ()}, "opening_hold_maker_checker"),
        ({"approver_refs": ("c", "c")}, "opening_hold_maker_checker"),
        ({"requester_ref": "other"}, "opening_hold_maker_checker"),
        ({"content_digest": "sha256:" + "0" * 64}, "opening_hold_approval_binding"),
        ({"subject_id": "x"}, "opening_hold_approval_binding"),
        ({"subject_type": "x"}, "opening_hold_approval_binding"),
        ({"request_id": uuid4()}, "opening_hold_approval_binding"),
    ],
)
def test_refusals(override, code):
    command = _command()
    with pytest.raises(BillingRuleViolation) as refused:
        hold.require_hold_maker_checker(command, _approval(command, **override))
    assert refused.value.code == f"billing.{code}"


def test_missing_approval_is_refused():
    with pytest.raises(BillingRuleViolation) as refused:
        hold.require_hold_maker_checker(_command(), None)
    assert refused.value.code == "billing.opening_hold_approval_missing"


def test_content_digest_binds_partition_command_disposition_and_maker():
    command = _command()
    base = hold.opening_hold_content_digest(command)
    for changed in (
        replace(command, maker_ref="someone"),
        replace(command, window_id=uuid4()),
        replace(
            command,
            disposition=hold.OpeningHoldDispositionV1("disposition:2", DIGEST),
        ),
        replace(
            command,
            import_command=replace(command.import_command, idempotency_key="other"),
        ),
    ):
        assert hold.opening_hold_content_digest(changed) != base
    assert (
        hold.opening_hold_content_digest(replace(command, approval_request_id=uuid4()))
        == base
    )


@pytest.mark.parametrize(
    "digest", ["", "abc", "sha256:" + "A" * 64, "sha256:" + "a" * 63]
)
def test_disposition_digest_shape_is_closed(digest):
    with pytest.raises(OpeningEvidenceError):
        hold.OpeningHoldDispositionV1("disposition:1", digest)


def test_hold_list_must_be_unique_and_canonically_ordered():
    ids = sorted((uuid4(), uuid4()), key=str)
    base = {
        "scope": PlatformScope(),
        "window_id": uuid4(),
        "epoch": uuid4(),
        "manifest_fingerprint": "f",
        "admissions": (),
        "coupled_watermark": "w",
        "command_key": "k",
        "authority_ref": "a",
        "occurred_at": evidence.NOW,
    }
    assert SealOpeningWindowV1(**base).held_account_ids == ()
    assert SealOpeningWindowV1(
        **base, held_account_ids=tuple(ids)
    ).held_account_ids == tuple(ids)
    for bad in ((ids[1], ids[0]), (ids[0], ids[0])):
        with pytest.raises(OpeningEvidenceError):
            SealOpeningWindowV1(**base, held_account_ids=bad)


def test_lane_port_refuses_an_untrusted_authority_object():
    assert not isinstance(object(), OpeningLaneAssemblyAuthorityV1)
    with pytest.raises(BillingRuleViolation) as refused:
        import_opening_lane_partition(
            None,
            scope=PlatformScope(),
            command=_command().import_command,
            authority=object(),
        )
    assert refused.value.code == "billing.opening_verifier_unavailable"
    assert isinstance(UUID(int=1), UUID)
