"""S10 entry criteria, SQLite lane (K-8): the unknown-outcome type, the relay
dead-letter predicates and `settle_dispatch`. The PG half is
`tests/test_delivery_relay_entry_pg.py`."""

from __future__ import annotations

import pytest
from dotmac_kernel.delivery import (
    DeliveryDeferred,
    DeliveryError,
    DeliveryOutcomeUnknown,
    DeliveryTerminal,
)
from dotmac_kernel.delivery_dispatch import create_dispatch, settle_dispatch
from dotmac_kernel.delivery_models import (
    DISPATCH_DEAD,
    DISPATCH_PENDING,
    DISPATCH_SENT,
)
from dotmac_kernel.messaging.relay import (
    RelayPolicy,
    deferral_dead_letters,
    failure_dead_letters,
)
from dotmac_kernel.models import Tenant


def test_unknown_outcome_carries_its_reason_and_is_not_a_hold_or_input_error() -> None:
    exc = DeliveryOutcomeUnknown("reply lost after DATA (TimeoutError)")
    assert exc.reason == "reply lost after DATA (TimeoutError)"
    # Not a hold and not a malformed-input error: neither mapping may catch it.
    assert not isinstance(exc, DeliveryDeferred | DeliveryError)


def test_unknown_and_terminal_are_distinct_from_holds_and_errors() -> None:
    for kind in (DeliveryOutcomeUnknown, DeliveryTerminal):
        assert not issubclass(kind, DeliveryDeferred | DeliveryError | ValueError)
        assert kind("why").reason == "why"


def test_dead_letter_predicates_match_the_relay_policy() -> None:
    policy = RelayPolicy(max_attempts=3, max_deferrals=2)
    assert [failure_dead_letters(a, policy) for a in (0, 1, 2)] == [False, False, True]
    assert [deferral_dead_letters(d, policy) for d in (0, 1, 2)] == [
        False,
        False,
        True,
    ]


def _dispatch(db):
    tenant = Tenant(slug="alpha", name="Alpha")
    db.add(tenant)
    db.flush()
    res = create_dispatch(
        db,
        tenant.id,
        idempotency_key="k1",
        on_conflict="raise_if_different",
        channel="email",
        category="transactional",
        address="jane@example.com",
        template_provenance="template:welcome@r1",
        subject="Hi",
        text="body",
    )
    return tenant, res.dispatch_id


def test_settle_dispatch_moves_pending_once_and_never_overwrites(db) -> None:
    tenant, did = _dispatch(db)
    assert settle_dispatch(db, tenant.id, did, DISPATCH_DEAD) is True
    assert _row(db, did).status == DISPATCH_DEAD
    # Already terminal: a later outcome does not overwrite it.
    assert settle_dispatch(db, tenant.id, did, DISPATCH_SENT) is False
    assert _row(db, did).status == DISPATCH_DEAD


def test_settle_dispatch_refuses_a_non_terminal_target_and_unknown_ids(
    db,
) -> None:
    from uuid import uuid4

    tenant, did = _dispatch(db)
    with pytest.raises(ValueError):
        settle_dispatch(db, tenant.id, did, DISPATCH_PENDING)
    assert settle_dispatch(db, tenant.id, uuid4(), DISPATCH_DEAD) is False
    assert _row(db, did).status == DISPATCH_PENDING


def _row(db, did):
    from dotmac_kernel.delivery_models import CommunicationDispatch

    return db.get(CommunicationDispatch, did)
