"""Source instructor progress/risk arithmetic and trusted observation bounds."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning.account_contracts import ActiveAccountPopulation
from dotmac_learning.instructor_contracts import (
    InstructorAuthority,
)
from dotmac_learning.instructor_reads import (
    _progress,
    _require_accounts,
    _require_authority,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def test_progress_missing_completions_count_zero_and_exact_risk_boundary():
    a, b = uuid4(), uuid4()
    assert _progress((a, b), {a: 0.8}, True, None, NOW) == (0.4, True)
    assert _progress((a, b), {a: 1}, True, None, NOW) == (0.5, False)
    assert _progress((a, b), {}, True, NOW - timedelta(days=14), NOW) == (0, False)
    assert _progress((a, b), {}, True, NOW - timedelta(days=14, seconds=1), NOW) == (
        0,
        True,
    )
    assert _progress((), {}, False, None, NOW) == (0, False)


def test_account_and_admin_observations_are_frozen_and_scope_bound():
    tenant, person = uuid4(), uuid4()
    accounts = ActiveAccountPopulation(tenant, frozenset({person}))
    authority = InstructorAuthority(tenant, person, True)
    _require_accounts(tenant, accounts)
    _require_authority(tenant, person, authority)
    with pytest.raises(ValueError):
        _require_accounts(uuid4(), accounts)
    with pytest.raises(ValueError):
        _require_authority(tenant, uuid4(), authority)
    with pytest.raises(FrozenInstanceError):
        authority.actor_is_admin = False
