"""Trusted account relation is scoped, frozen and nonexecuting."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning.account_contracts import ActiveAccountSelection
from sqlalchemy import select


def test_explicit_account_selection_is_scoped_frozen_and_keeps_relation_identity():
    tenant, available = uuid4(), uuid4()
    relation = select(Party.id).where(Party.tenant_id == tenant, Party.id == available)
    fact = ActiveAccountSelection(tenant, relation)
    assert fact.tenant_id == tenant
    assert fact.active_person_ids is relation
    assert tuple(relation.selected_columns) == (Party.__table__.c.id,)
    assert relation.selected_columns[0].nullable is False
    with pytest.raises(FrozenInstanceError):
        fact.tenant_id = uuid4()
    assert not hasattr(fact, "actor_is_admin")
    assert not hasattr(fact, "party_is_active")
    assert not hasattr(fact, "session")
