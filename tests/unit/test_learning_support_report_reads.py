"""Support report policy canaries, authored before implementation; hosted only."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning import support_report_reads as reads
from dotmac_learning.support_report_contracts import EngagementSnapshot


def test_engagement_fact_is_frozen():
    fact = EngagementSnapshot(3, 2, 1, 1)
    with pytest.raises(FrozenInstanceError):
        fact.enrollees = 4


def test_mover_limit_precedes_missing_person_hydration_without_refill():
    missing = uuid4()
    statements = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Database:
        def execute(self, statement):
            statements.append(statement)
            if len(statements) == 1:
                return Result(
                    [SimpleNamespace(person_id=missing, events=10, submissions=2)]
                )
            return Result([])

    assert (
        reads.top_movers(
            Database(), tenant_id=uuid4(), since=datetime.now(UTC), limit=1
        )
        == ()
    )
    assert statements[0]._limit_clause.value == 1
    assert len(statements) == 4
