"""Source characterization; runtime qualification belongs to hosted CI."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from dotmac_learning.reading import chapter_subtopics, mark_complete, slugify_heading
from dotmac_learning.reading_contracts import Heading, SubtopicReadCommand
from dotmac_learning.result_contracts import TransactionChanged
from pydantic import ValidationError


def test_heading_slug_policy_preserves_unicode_and_source_suffix_overflow():
    assert slugify_heading("  Hello,   WORLD!! ") == "hello-world"
    assert slugify_heading("Énergie & 安全") == "énergie-安全"
    assert slugify_heading("!!!") == "section"
    topics = chapter_subtopics(
        (
            Heading(level="h2", title="Why this matters"),
            Heading(level="h3", title="Catch-up sidebar"),
            Heading(level="h2", title="Alpha"),
            Heading(level="h3", title="Alpha"),
            Heading(level="h2", title="x" * 70),
            Heading(level="h2", title="x" * 70),
        )
    )
    assert [t.slug for t in topics] == ["alpha", "alpha-2", "x" * 64, "x" * 64 + "-2"]
    # Preserve the source's suffix-after-cap behavior: the persistence cap is
    # separately applied by mark_complete, even to an already generated slug.
    assert len(topics[-1].slug) == 66


def test_structured_heading_boundary_rejects_unrecognized_levels():
    with pytest.raises(ValidationError):
        Heading(level="h1", title="Not a source subtopic")


def test_blank_subtopic_is_noop_without_sql_but_requires_caller_root():
    db = MagicMock()
    command = SubtopicReadCommand(
        tenant_id=uuid4(), person_id=uuid4(), chapter_id=uuid4(), subtopic_slug=" \n "
    )
    assert mark_complete(db, command) is False
    db.execute.assert_not_called()
    db.flush.assert_not_called()
    db.get_transaction.return_value = None
    with pytest.raises(TransactionChanged, match="caller root"):
        mark_complete(db, command)
    db.begin.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
