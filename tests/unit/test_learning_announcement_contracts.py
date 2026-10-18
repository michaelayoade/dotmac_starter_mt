"""Source payload fidelity and the mandatory product fanout seam."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from inspect import Parameter, signature
from uuid import uuid4

import pytest
from dotmac_learning.announcement_contracts import (
    AnnouncementFact,
    CreateAnnouncementCommand,
)
from pydantic import ValidationError


def test_command_preserves_raw_source_content_and_separates_actor_context():
    command = CreateAnnouncementCommand(
        tenant_id=uuid4(),
        author_person_id=uuid4(),
        title="  raw title  ",
        body_md="\n# source\n",
        body_html="<h1>product rendering</h1>\n",
    )
    assert command.title == "  raw title  "
    assert command.body_md == "\n# source\n"
    assert command.body_html == "<h1>product rendering</h1>\n"
    assert command.cohort_id is None
    with pytest.raises(ValidationError):
        CreateAnnouncementCommand(
            **command.model_dump(), actor_type="system", actor_id="client"
        )
    with pytest.raises(ValidationError):
        command.title = "changed"


def test_detached_fact_has_all_nine_source_fields_and_is_immutable():
    assert [field.name for field in fields(AnnouncementFact)] == [
        "id",
        "tenant_id",
        "cohort_id",
        "author_person_id",
        "title",
        "body_md",
        "body_html",
        "created_at",
        "updated_at",
    ]
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fact = AnnouncementFact(
        uuid4(), uuid4(), None, uuid4(), "title", "md", "html", now, now
    )
    with pytest.raises(FrozenInstanceError):
        fact.title = "changed"


def test_fanout_and_audit_context_are_required_explicit_wiring():
    from dotmac_learning.announcements import create

    parameters = signature(create).parameters
    assert parameters["fanout"].default is Parameter.empty
    assert parameters["audit_context"].default is Parameter.empty
