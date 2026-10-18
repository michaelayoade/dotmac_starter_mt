"""Pure scheduling boundary canaries, authored before the service port."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning.delivery_contracts import (
    ClassSessionFact,
    CreateSessionCommand,
    SchedulingBadRequest,
    UpdateSessionCommand,
)
from dotmac_learning.scheduling import _clean_join_url, join_is_open
from pydantic import ValidationError

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def fact(**values):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "cohort_id": uuid4(),
        "offering_id": None,
        "session_type": "live_class",
        "title": "Class",
        "starts_at": NOW,
        "ends_at": None,
        "instructor_person_id": None,
        "location": None,
        "join_url": "https://example.test/join",
        "notes": None,
        "status": "scheduled",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return ClassSessionFact(**(fields | values))


@pytest.mark.parametrize(
    "minutes,expected", [(60, True), (60.0001, False), (0, True), (-0.0001, False)]
)
def test_join_exact_lead_and_point_session_end(minutes, expected):
    assert (
        join_is_open(
            fact(starts_at=NOW + timedelta(minutes=minutes)), now=NOW, lead_minutes=60
        )
        is expected
    )


def test_join_in_progress_end_status_link_and_custom_lead():
    session = fact(starts_at=NOW - timedelta(hours=2), ends_at=NOW)
    assert join_is_open(session, now=NOW, lead_minutes=60)
    assert not join_is_open(
        session, now=NOW + timedelta(microseconds=1), lead_minutes=60
    )
    assert not join_is_open(
        replace(session, status="completed"), now=NOW, lead_minutes=60
    )
    assert not join_is_open(replace(session, join_url=None), now=NOW, lead_minutes=60)
    assert not join_is_open(
        fact(starts_at=NOW + timedelta(minutes=5)), now=NOW, lead_minutes=4
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (" \t", None),
        (" HTTPS://Example.test/path ", "HTTPS://Example.test/path"),
        ("http://", "http://"),
    ],
)
def test_source_url_cleaning_without_extra_host_validation(value, expected):
    assert _clean_join_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/plain,a",
        "//example.test",
        "ftp://example.test",
    ],
)
def test_unsafe_join_scheme_source_error(value):
    with pytest.raises(
        SchedulingBadRequest, match="Join URL must start with http:// or https://."
    ):
        _clean_join_url(value)


def test_update_payload_preserves_omission_explicit_null_and_free_vocabulary():
    scope = {"tenant_id": uuid4(), "session_id": uuid4()}
    omitted = UpdateSessionCommand(**scope)
    clear = UpdateSessionCommand(**scope, ends_at=None, title=None, join_url="")
    assert omitted.model_fields_set == {"tenant_id", "session_id"}
    assert clear.model_fields_set == {
        "tenant_id",
        "session_id",
        "ends_at",
        "title",
        "join_url",
    }
    assert UpdateSessionCommand(**scope, status="unknown").status == "unknown"
    with pytest.raises(ValidationError):
        UpdateSessionCommand(**scope, actor_id="forged")
    with pytest.raises(ValidationError):
        clear.title = "changed"
    with pytest.raises(FrozenInstanceError):
        fact().title = "changed"
    with pytest.raises(ValidationError):
        CreateSessionCommand(
            session_type="live_class",
            tenant_id=uuid4(),
            cohort_id=uuid4(),
            title="Class",
            starts_at=NOW,
            authorize=True,
        )
