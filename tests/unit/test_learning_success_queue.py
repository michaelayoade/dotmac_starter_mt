"""Success source contract canary, authored before implementation; hosted only."""

from dataclasses import fields

from dotmac_learning.success_contracts import QueueFact, SuccessTerms
from dotmac_learning.success_queue import SEGMENT_KEYS


def test_source_queue_shape_and_explicit_unclamped_terms():
    assert len(fields(QueueFact)) == 16
    assert SuccessTerms(-1, -2, -3).inactive_days == -1
    assert SEGMENT_KEYS == (
        "inactive",
        "overdue_work",
        "below_passing",
        "failed_final",
        "almost_complete",
    )
