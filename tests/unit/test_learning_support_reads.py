"""Source support thresholds, strict window bounds and authority scope."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning.support_contracts import SearchAuthority
from dotmac_learning.support_reads import (
    _dashboard_state,
    _risk_for_window,
    _search_authority,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def test_dashboard_missing_completion_zero_denominator_and_exact_stale_boundary():
    a, b = uuid4(), uuid4()
    assert _dashboard_state((a, b), {a: 0.8}, None, NOW, 0.5, 14) == (0.4, True)
    assert _dashboard_state((a, b), {a: 1}, None, NOW, 0.5, 14) == (0.5, False)
    assert _dashboard_state((), {}, None, NOW, 0.5, 14) == (0, True)
    assert _dashboard_state((a,), {}, NOW - timedelta(days=14), NOW, 0.5, 14) == (
        0,
        False,
    )
    assert _dashboard_state(
        (a,), {}, NOW - timedelta(days=14, seconds=1), NOW, 0.5, 14
    ) == (0, True)


def test_risk_window_completed_overdue_and_strict_behind_edges():
    before, after = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert _risk_for_window(before, NOW, 0, False, NOW, 0.2) is None
    assert _risk_for_window(NOW, after, 0, False, NOW, 0.2) is None
    assert _risk_for_window(before, after, 0.3, False, NOW, 0.2) is None
    assert _risk_for_window(before, after, 0.299, False, NOW, 0.2) == ("behind", 0.5)
    assert _risk_for_window(None, before, 1, False, NOW, 0.2) == ("overdue", 1)
    assert _risk_for_window(None, before, 0, True, NOW, 0.2) is None
    assert _risk_for_window(None, None, 0, False, NOW, 0.2) is None
    assert _risk_for_window(after, before, 0, False, NOW, 0.2) == ("overdue", 1)


def test_search_authority_binds_both_tenant_and_person():
    tenant, person = uuid4(), uuid4()
    authority = SearchAuthority(tenant, person, True)
    _search_authority(tenant, person, authority)
    with pytest.raises(ValueError):
        _search_authority(uuid4(), person, authority)
    with pytest.raises(ValueError):
        _search_authority(tenant, uuid4(), authority)
