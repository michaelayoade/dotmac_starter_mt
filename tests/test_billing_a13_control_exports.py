"""Billing a13 control-position exports (R1): unit proofs, no database.

Billing's public surface is ``dotmac_billing.__all__`` (it has no
``public_exports.json``).
"""

import dotmac_billing as billing
import pytest

CONTROL_NAMES = (
    "get_receivable_control_position_v1",
    "ReceivableControlPositionV1",
    "ReceivableControlCurrencyV1",
    "RECEIVABLE_CONTROL_POSITION_CONTRACT",
)


@pytest.mark.parametrize("name", CONTROL_NAMES)
def test_control_names_are_in_the_top_level_all(name):
    assert name in billing.__all__
    assert getattr(billing, name) is not None


def test_exports_are_the_owner_objects_not_wrappers():
    from dotmac_billing import control_position

    assert (
        billing.get_receivable_control_position_v1
        is control_position.get_receivable_control_position_v1
    )
    assert (
        billing.ReceivableControlPositionV1
        is control_position.ReceivableControlPositionV1
    )


def test_all_has_no_duplicates_and_resolves():
    assert len(billing.__all__) == len(set(billing.__all__))
    for name in billing.__all__:
        assert hasattr(billing, name), name


def test_the_contract_constant_is_pinned():
    assert billing.RECEIVABLE_CONTROL_POSITION_CONTRACT == (
        "billing.receivable.control_position.v1"
    )
