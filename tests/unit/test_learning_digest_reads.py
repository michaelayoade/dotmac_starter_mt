"""Digest facts are immutable observations without transaction capabilities."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning.digest_contracts import LearnerWindowActivity, StaffLearningWindow


def test_digest_window_facts_are_frozen_and_contain_only_detached_counts_ids():
    person = uuid4()
    staff = StaffLearningWindow(frozenset({person}), frozenset({person}), 3)
    learner = LearnerWindowActivity(1, 2, 4)
    assert staff.active_ids == staff.ever_active_ids == frozenset({person})
    assert staff.completion_count == 3
    assert (learner.chapters, learner.submissions, learner.passed) == (1, 2, 4)
    for fact in (staff, learner):
        assert not hasattr(fact, "_sa_instance_state")
        for name in ("execute", "commit", "rollback", "connection"):
            assert not hasattr(fact, name)
    with pytest.raises(FrozenInstanceError):
        learner.passed = 0
    with pytest.raises(FrozenInstanceError):
        staff.completion_count = 0
