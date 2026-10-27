"""Configured read inputs; hosted execution only, no installed database claim."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning import (
    agenda,
    announcements,
    instructor_reads,
    scheduling,
    support_reads,
    support_report_reads,
    todo,
)
from dotmac_learning.assessment_reads import FeedbackRevealTerms
from dotmac_learning.todo_contracts import TodoTerms

FEEDBACK = FeedbackRevealTerms(
    {"practice": "immediate", "graded": "on_pass_or_exhausted", "exam": "never"}
)

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def test_terms_require_all_fields_and_are_frozen():
    with pytest.raises(TypeError):
        TodoTerms()
    terms = TodoTerms(3, 2, 11, 2, 5)
    with pytest.raises(FrozenInstanceError):
        terms.graded_limit = 99
    assert (
        terms.graded_lookback_days,
        terms.graded_limit,
        terms.window_horizon_days,
        terms.near_deadline_days,
        terms.far_deadline_days,
    ) == (3, 2, 11, 2, 5)


@pytest.mark.parametrize(
    "function,values",
    [
        (scheduling.join_is_open, {"session": object(), "now": NOW}),
        (
            todo.timeline_for_person,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "person_id": uuid4(),
                "local_zone": UTC,
            },
        ),
        (
            agenda.upcoming_for_person,
            {"db": object(), "tenant_id": uuid4(), "person_id": uuid4()},
        ),
        (
            announcements.for_person,
            {"db": object(), "tenant_id": uuid4(), "person_id": uuid4()},
        ),
        (announcements.list_for_tenant, {"db": object(), "tenant_id": uuid4()}),
        (
            instructor_reads.cohort_workspace,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "cohort_id": uuid4(),
                "accounts": object(),
            },
        ),
        (
            support_reads.cohort_overview,
            {"db": object(), "tenant_id": uuid4(), "cohort_id": uuid4()},
        ),
        (
            support_reads.at_risk_for_person,
            {"db": object(), "tenant_id": uuid4(), "person_id": uuid4()},
        ),
        (
            support_reads.search,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "person_id": uuid4(),
                "q": "",
                "authority": object(),
            },
        ),
        (
            support_report_reads.top_movers,
            {"db": object(), "tenant_id": uuid4(), "since": NOW},
        ),
        (support_report_reads.attention_list, {"db": object(), "tenant_id": uuid4()}),
    ],
)
def test_missing_policy_refuses_before_database_or_mutation(function, values):
    with pytest.raises(TypeError):
        function(**values)


@pytest.mark.parametrize("minutes,expected", [(4, True), (4.0001, False)])
def test_compound_timeline_forwards_join_and_configured_deadline_buckets(
    monkeypatch, minutes, expected
):
    terms = TodoTerms(3, 2, 11, 2, 5)
    session = SimpleNamespace(
        id=uuid4(),
        starts_at=NOW + timedelta(minutes=minutes),
        ends_at=None,
        status="scheduled",
        join_url="https://example.test",
    )
    deadlines = tuple(
        SimpleNamespace(due_at=NOW + timedelta(days=days)) for days in [2, 3, 5, 6]
    )
    db, tenant, person = object(), uuid4(), uuid4()
    monkeypatch.setattr(todo, "deadline_rows", lambda *args, **kwargs: deadlines)
    monkeypatch.setattr(
        todo,
        "list_for_person",
        lambda *args, **kwargs: SimpleNamespace(
            upcoming=(SimpleNamespace(session=session),)
        ),
    )
    calls = []

    def rows(actual_db, **values):
        assert actual_db is db
        assert values in (
            {
                "tenant_id": tenant,
                "person_id": person,
                "now": NOW,
                "terms": terms,
            },
            {
                "tenant_id": tenant,
                "person_id": person,
                "now": NOW,
                "terms": terms,
                "feedback_terms": FEEDBACK,
            },
        )
        calls.append(values)
        return ()

    monkeypatch.setattr(todo, "_graded_rows", rows)
    monkeypatch.setattr(todo, "_course_windows", rows)
    result = todo.timeline_for_person(
        db,
        tenant_id=tenant,
        person_id=person,
        local_zone=UTC,
        now=NOW,
        terms=terms,
        feedback_terms=FEEDBACK,
        join_lead_minutes=4,
    )
    assert result.next_7 == deadlines[:1]
    assert result.next_30 == deadlines[1:3]
    assert (session.id in result.joinable) is expected
    assert len(calls) == 2


@pytest.mark.parametrize("minutes,expected", [(4, True), (4.0001, False)])
def test_compound_agenda_forwards_join_and_explicit_cap(monkeypatch, minutes, expected):
    session = SimpleNamespace(
        id=uuid4(),
        starts_at=NOW + timedelta(minutes=minutes),
        ends_at=None,
        status="scheduled",
        title="Class",
    )
    statements = []

    class Result:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Database:
        def scalars(self, statement):
            statements.append(statement)
            return Result([uuid4()] if len(statements) == 1 else [session])

    monkeypatch.setattr(agenda, "accessible_course_ids", lambda *args, **kwargs: set())
    monkeypatch.setattr(
        scheduling,
        "_fact",
        lambda row: SimpleNamespace(**vars(row), join_url="https://example.test"),
    )
    result = agenda.upcoming_for_person(
        Database(),
        tenant_id=uuid4(),
        person_id=uuid4(),
        now=NOW,
        limit=1,
        join_lead_minutes=4,
    )
    assert len(result) == 1
    assert result[0].joinable is expected


def test_instructor_nondefault_threshold_and_stale_boundary():
    course = uuid4()
    assert instructor_reads._progress(
        [course], {course: 0.6}, True, NOW - timedelta(days=3), NOW, 0.7, 2
    ) == (0.6, True)
    assert instructor_reads._progress(
        [course], {course: 0.6}, True, NOW - timedelta(days=2), NOW, 0.7, 2
    ) == (0.6, False)


def test_graded_lookback_and_cap_are_actual_query_inputs():
    statements = []
    terms = TodoTerms(3, 2, 11, 2, 5)

    class Database:
        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    assert (
        todo._graded_rows(
            Database(),
            tenant_id=uuid4(),
            person_id=uuid4(),
            now=NOW,
            terms=terms,
            feedback_terms=FEEDBACK,
        )
        == ()
    )
    (statement,) = statements
    assert statement._limit_clause.value == 2
    assert statement._where_criteria[-1].right.value == NOW - timedelta(days=3)


def test_configured_course_window_includes_exact_horizon_and_keeps_order():
    terms = TodoTerms(3, 2, 3, 2, 5)
    at_horizon = NOW + timedelta(days=3)
    rows = [
        (
            SimpleNamespace(
                starts_at=at_horizon, ends_at=at_horizon + timedelta(microseconds=1)
            ),
            "Last",
        ),
        (SimpleNamespace(starts_at=NOW, ends_at=NOW + timedelta(days=1)), "First"),
    ]

    class Database:
        def execute(self, statement):
            return SimpleNamespace(all=lambda: rows)

    result = todo._course_windows(
        Database(), tenant_id=uuid4(), person_id=uuid4(), now=NOW, terms=terms
    )
    assert [(r.kind, r.course_title, r.at) for r in result] == [
        ("closes", "First", NOW + timedelta(days=1)),
        ("opens", "Last", at_horizon),
    ]


@pytest.mark.parametrize(
    "function,values,required",
    [
        (
            todo.timeline_for_person,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "person_id": uuid4(),
                "local_zone": UTC,
                "terms": TodoTerms(3, 2, 11, 2, 5),
                "feedback_terms": FEEDBACK,
                "join_lead_minutes": 4,
            },
            ("terms", "feedback_terms", "join_lead_minutes"),
        ),
        (
            agenda.upcoming_for_person,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "person_id": uuid4(),
                "limit": 1,
                "join_lead_minutes": 4,
            },
            ("limit", "join_lead_minutes"),
        ),
        (
            instructor_reads.cohort_workspace,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "cohort_id": uuid4(),
                "accounts": object(),
                "at_risk_pct": 0.7,
                "stale_days": 2,
                "limit": 1,
            },
            ("at_risk_pct", "stale_days", "limit"),
        ),
        (
            support_reads.cohort_overview,
            {
                "db": object(),
                "tenant_id": uuid4(),
                "cohort_id": uuid4(),
                "at_risk_pct": 0.7,
                "stale_days": 2,
            },
            ("at_risk_pct", "stale_days"),
        ),
    ],
)
def test_each_compound_policy_input_is_individually_required(
    function, values, required
):
    for name in required:
        omitted = {key: value for key, value in values.items() if key != name}
        with pytest.raises(TypeError):
            function(**omitted)
