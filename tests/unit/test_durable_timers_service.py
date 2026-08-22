"""Pure refusal tests for the durable-timer public ports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from dotmac_durable_timers import (
    TimerError,
    TimerIdentity,
    TimerOutput,
    cancel_timer,
    schedule_timer,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.outbox_event_types import (
    OutboxEventTypeRegistry,
    UndeclaredOutboxEventTypeError,
    install_outbox_event_types,
)
from sqlalchemy.orm import Session

NOW = datetime(2030, 1, 1, tzinfo=UTC)
IDENTITY = TimerIdentity("tests.owner", "tests.entity", "one", "tests.due")


def test_undeclared_output_is_refused_before_any_database_access() -> None:
    install_outbox_event_types(OutboxEventTypeRegistry([]))

    with pytest.raises(UndeclaredOutboxEventTypeError, match="not declared"):
        schedule_timer(
            cast(Session, object()),
            scope=TenantScope(uuid4()),
            identity=IDENTITY,
            due_at=NOW,
            output=TimerOutput("tests.typo"),
            recorded_at=NOW,
        )


@pytest.mark.parametrize("observed_generation", [0, -1, True])
def test_cancel_requires_a_strict_positive_observed_generation(
    observed_generation: int,
) -> None:
    with pytest.raises(TimerError, match="positive integer") as captured:
        cancel_timer(
            cast(Session, object()),
            scope=TenantScope(uuid4()),
            identity=IDENTITY,
            observed_generation=observed_generation,
            recorded_at=NOW,
        )
    assert captured.value.code == "durable_timers.invalid_observed_generation"
