"""Success source contract canary, authored before implementation; hosted only."""

from dataclasses import fields

from dotmac_learning.success_contracts import QueueFact, SuccessTerms
from dotmac_learning.success_queue import SEGMENT_KEYS


def test_source_queue_shape_and_explicit_unclamped_terms():
    assert len(fields(QueueFact)) == 16
    terms = SuccessTerms(
        -1,
        -2,
        -3,
        2,
        3,
        7,
        5,
        2,
        0.20,
        0.5,
        0.8,
        (
            "inactivity",
            "overdue_work",
            "below_passing",
            "failed_final",
            "certificate_blocked",
        ),
        {"failed_final": "high", "certificate_blocked": "medium"},
    )
    assert terms.inactive_days == -1
    assert SEGMENT_KEYS == (
        "inactive",
        "overdue_work",
        "below_passing",
        "failed_final",
        "almost_complete",
    )
