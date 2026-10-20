"""Pure requirements engine: no database, no wiring (slice 1)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import UUID, uuid4

import pytest
from dotmac_learning.requirements import (
    BUILTIN_CONDITIONS,
    CERTIFICATE_FINAL_MIN_PCT,
    MAX_DEPTH,
    MAX_NODES,
    All,
    Any,
    Cond,
    ConditionRegistry,
    ConditionSpec,
    DuplicateConditionKindError,
    ExpressionError,
    LeafResult,
    LearnerFacts,
    Not,
    Route,
    Target,
    UnknownConditionKindError,
    combine_additive,
    content_hash,
    default_registry,
    dependency_edges,
    evaluate,
    find_cycles,
    parse,
    to_data,
    to_json,
    validate,
    walk,
)
from pydantic import BaseModel, ConfigDict

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TENANT, PERSON = uuid4(), uuid4()
REG = default_registry()


def facts(**kw) -> LearnerFacts:
    return LearnerFacts(tenant_id=TENANT, person_id=PERSON, now=NOW, **kw)


def course_target(course: UUID | None = None) -> Target:
    return Target("course_access", course or uuid4())


def attempt_target() -> Target:
    return Target("activity_attempt", uuid4())


def done(course: UUID) -> Cond:
    return Cond("course_completed", {"course_id": str(course)})


def run(expr, f=None, target=None):
    return evaluate(expr, f or facts(), REG, target or course_target())


# -- expression -----------------------------------------------------------


def test_parse_round_trip_and_hash_stable_across_key_order():
    c = uuid4()
    doc = {
        "all": [
            {"cond": "course_completed", "params": {"course_id": str(c)}},
            {"not": {"any": []}},
        ]
    }
    expr = parse(doc)
    assert expr == All((done(c), Not(Any(()))))
    assert parse(to_json(expr)) == expr
    assert to_data(expr) == doc
    a = Cond("minimum_score", {"subject": "activity", "ref_id": "x", "min_pct": 50})
    b = Cond("minimum_score", {"min_pct": 50, "ref_id": "x", "subject": "activity"})
    assert to_json(a) == to_json(b) and content_hash(a) == content_hash(b)
    assert " " not in to_json(a)
    assert len(content_hash(a)) == 64
    # Child order is significant.
    assert content_hash(All((done(c), Any(())))) != content_hash(
        All((Any(()), done(c)))
    )


@pytest.mark.parametrize(
    "bad",
    [
        "{not json",
        "[]",
        "null",
        {},
        {"all": [], "any": []},
        {"all": {}},
        {"not": [{"all": []}]},
        {"all": [], "extra": 1},
        {"cond": "x"},
        {"cond": "x", "params": []},
        {"cond": "", "params": {}},
        {"cond": "x", "params": {}, "extra": 1},
        {"cond": "x", "params": {"v": object()}},
        {"cond": "x", "params": {"v": float("nan")}},
        {"bogus": []},
    ],
)
def test_malformed_documents_rejected(bad):
    with pytest.raises(ExpressionError):
        parse(bad)


def test_parse_hard_bounds():
    deep: dict = {"all": []}
    for _ in range(80):
        deep = {"not": deep}
    with pytest.raises(ExpressionError, match="deep"):
        parse(deep)
    with pytest.raises(ExpressionError, match="many nodes"):
        parse({"all": [{"all": []}] * 10_001})


def test_walk_paths_preorder():
    c1, c2 = uuid4(), uuid4()
    expr = All((Any((done(c1), Not(done(c2)))), All(())))
    assert [p for p, _ in walk(expr)] == [
        "",
        "all/0",
        "all/0/any/0",
        "all/0/any/1",
        "all/0/any/1/not/0",
        "all/1",
    ]


# -- primitives: pass and fail -------------------------------------------


def test_course_completed():
    c = uuid4()
    assert run(done(c), facts(completed_course_ids=frozenset({c}))).passed
    res = run(done(c))
    assert not res.passed and res.reasons[0].code == "course_not_completed"


def test_certificate_held():
    c = uuid4()
    cond = Cond("certificate_held", {"course_id": str(c)})
    assert run(cond, facts(certificate_course_ids=frozenset({c}))).passed
    assert run(cond).reasons[0].code == "certificate_missing"


@pytest.mark.parametrize(
    ("subject", "field"),
    [
        ("activity", "best_fraction_by_activity"),
        ("course_final", "course_final_fraction"),
        ("course_grade", "course_grade_fraction"),
    ],
)
def test_minimum_score(subject, field):
    ref = uuid4()
    cond = Cond(
        "minimum_score", {"subject": subject, "ref_id": str(ref), "min_pct": 70}
    )
    assert run(cond, facts(**{field: {ref: 0.7}})).passed  # boundary is inclusive
    low = run(cond, facts(**{field: {ref: 0.69}}))
    assert not low.passed and low.reasons[0].code == "score_below_minimum"
    missing = run(cond)
    assert not missing.passed and missing.reasons[0].code == "fact_missing"


def test_attempts_at_least():
    act = uuid4()
    cond = Cond("attempts_at_least", {"activity_id": str(act), "n": 2})
    target = attempt_target()
    assert run(cond, facts(attempts_by_activity={act: 2}), target).passed
    assert run(cond, facts(attempts_by_activity={act: 1}), target).reasons[0].code == (
        "attempts_below_minimum"
    )
    assert run(cond, None, target).reasons[0].code == "fact_missing"
    # Not allowed on a course_access target.
    refused = run(cond, facts(attempts_by_activity={act: 5}))
    assert refused.reasons[0].code == "kind_not_allowed_for_target"


def test_enrolled_before():
    cohort = uuid4()
    cond = Cond("enrolled_before", {"at": "2026-06-01T00:00:00+00:00"})
    early = facts(enrolled_at_by_cohort={cohort: datetime(2026, 5, 31, tzinfo=UTC)})
    assert run(cond, early).passed
    late = facts(enrolled_at_by_cohort={cohort: datetime(2026, 6, 1, tzinfo=UTC)})
    assert run(cond, late).reasons[0].code == "enrolled_after_cutoff"
    assert run(cond).reasons[0].code == "fact_missing"


def test_manual_approval_binds_to_target_and_node_path():
    target = course_target()
    cond = Cond("manual_approval", {"approver_permission": "learning.approve"})
    expr = All((All(()), cond))  # the approval node lives at "all/1"
    ok = facts(approvals=frozenset({("course_access", target.id, "all/1")}))
    assert run(expr, ok, target).passed
    wrong_path = facts(approvals=frozenset({("course_access", target.id, "all/0")}))
    assert run(expr, wrong_path, target).reasons[0].code == "approval_missing"
    other = facts(approvals=frozenset({("course_access", uuid4(), "all/1")}))
    assert not run(expr, other, target).passed


def test_track_position():
    track, prev = uuid4(), uuid4()
    target = course_target()
    cond = Cond("track_position", {"track_id": str(track)})

    def with_routes(*routes, completed=()):
        return facts(
            routes_by_course={target.id: tuple(routes)},
            completed_course_ids=frozenset(completed),
        )

    assert run(cond, with_routes(Route(track, None)), target).passed
    assert run(cond, with_routes(Route(track, prev), completed=[prev]), target).passed
    res = run(cond, with_routes(Route(track, prev)), target)
    assert res.reasons[0].code == "track_predecessor_incomplete"
    assert res.reasons[0].params["predecessor_course_id"] == str(prev)
    assert run(cond, with_routes(Route(uuid4(), None)), target).reasons[0].code == (
        "fact_missing"
    )
    assert run(cond, None, target).reasons[0].code == "fact_missing"
    assert (
        run(cond, with_routes(Route(track, None)), attempt_target()).reasons[0].code
        == "kind_not_allowed_for_target"
    )


def test_fact_missing_and_bad_params_fail_closed():
    ref = uuid4()
    cond = Cond(
        "minimum_score", {"subject": "activity", "ref_id": str(ref), "min_pct": 1}
    )
    assert not run(cond).passed
    assert run(Cond("course_completed", {"course_id": "nope"})).reasons[0].code == (
        "invalid_params"
    )
    assert (
        run(Cond("course_completed", {"course_id": str(ref), "x": 1})).reasons[0].code
        == "invalid_params"
    )
    bad_pct = Cond(
        "minimum_score", {"subject": "activity", "ref_id": str(ref), "min_pct": 0}
    )
    assert run(bad_pct).reasons[0].code == "invalid_params"
    with pytest.raises(UnknownConditionKindError):
        run(Cond("no_such_kind", {}))


# -- composition ----------------------------------------------------------


@pytest.fixture
def leaves():
    t, f = uuid4(), uuid4()
    return done(t), done(f), facts(completed_course_ids=frozenset({t}))


def test_truth_tables(leaves):
    t, f, fx = leaves
    for combo in product([t, f], repeat=2):
        vals = [c is t for c in combo]
        assert run(All(combo), fx).passed == all(vals)
        assert run(Any(combo), fx).passed == any(vals)
    assert run(Not(t), fx).passed is False
    assert run(Not(f), fx).passed is True
    assert run(All(()), fx).passed is True
    empty_any = run(Any(()), fx)
    assert not empty_any.passed and empty_any.reasons[0].code == "empty_any"


def test_reasons_and_evidence_follow_declared_order_without_short_circuit():
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    fx = facts(completed_course_ids=frozenset({b, d}))
    res = run(All((done(a), done(b), done(c), done(d))), fx)
    assert not res.passed
    assert [r.params["course_id"] for r in res.reasons] == [str(a), str(c)]
    assert [r.path for r in res.reasons] == ["all/0", "all/2"]
    assert [(e.kind, e.id) for e in res.evidence] == [
        ("course_completion", str(b)),
        ("course_completion", str(d)),
    ]
    any_res = run(Any((done(a), done(c))), fx)
    assert [r.params["course_id"] for r in any_res.reasons] == [str(a), str(c)]
    assert [n.path for n in res.root.children] == ["all/0", "all/1", "all/2", "all/3"]
    # Evaluation is deterministic.
    assert run(All((done(a), done(b))), fx) == run(All((done(a), done(b))), fx)


def test_not_prefixes_reason_codes(leaves):
    t, f, fx = leaves
    failed = run(Not(t), fx)
    assert [r.code for r in failed.reasons] == ["not.course_completed"]
    nested = run(Not(Not(f)), fx)
    assert not nested.passed
    assert [r.code for r in nested.reasons] == ["not.not.course_not_completed"]
    assert run(Not(f), fx).root.satisfied[0].code == "not.course_not_completed"


# -- registry -------------------------------------------------------------


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _spec(kind: str) -> ConditionSpec:
    return ConditionSpec(
        kind=kind,
        params_model=_P,
        targets=frozenset({"course_access"}),
        references=lambda p: (),
        monotone=True,
        evaluate=lambda p, ctx: LeafResult(True, kind, {}),
    )


def test_registry_duplicate_and_unknown_kinds():
    assert {s.kind for s in BUILTIN_CONDITIONS} == {
        "course_completed",
        "certificate_held",
        "minimum_score",
        "attempts_at_least",
        "enrolled_before",
        "manual_approval",
        "track_position",
    }
    with pytest.raises(DuplicateConditionKindError):
        ConditionRegistry.from_specs([_spec("x"), _spec("x")])
    with pytest.raises(DuplicateConditionKindError):
        ConditionRegistry.from_specs([*BUILTIN_CONDITIONS, BUILTIN_CONDITIONS[0]])
    with pytest.raises(UnknownConditionKindError):
        REG.require("nope")


def test_product_kind_is_registered_by_the_product():
    level = ConditionSpec(
        kind="academy.entrance_level_at_least",
        params_model=_P,
        targets=frozenset({"course_access"}),
        references=lambda p: (),
        monotone=True,
        evaluate=lambda p, ctx: LeafResult(
            ctx.facts.product_facts.get("entrance_level") == 3,
            "academy.entrance_level_at_least",
            {},
        ),
    )
    registry = ConditionRegistry.from_specs([*BUILTIN_CONDITIONS, level])
    expr = Cond("academy.entrance_level_at_least", {})
    assert evaluate(
        expr, facts(product_facts={"entrance_level": 3}), registry, course_target()
    ).passed
    assert not evaluate(expr, facts(), registry, course_target()).passed


# -- validation -----------------------------------------------------------


class Catalog:
    def __init__(self, courses=(), activities=None, gradable=()):
        self.courses = set(courses)
        self.activities = activities or {}
        self.gradable = set(gradable)

    def course_exists(self, course_id):
        return course_id in self.courses

    def activity_exists(self, activity_id):
        return activity_id in self.activities

    def activity_max_attempts(self, activity_id):
        return self.activities[activity_id]

    def activity_gradable(self, activity_id):
        return activity_id in self.gradable


def codes(issues, severity=None):
    return sorted(i.code for i in issues if severity in (None, i.severity))


def check(expr, catalog=None, target=None, **kw):
    return validate(
        expr, REG, target or course_target(), catalog or Catalog(), now=NOW, **kw
    )


def test_validation_clean_expression_has_no_issues():
    a, b = uuid4(), uuid4()
    assert check(All((done(a),)), Catalog({a, b}), course_target(b)) == []


def test_unknown_kind_params_and_target():
    act = uuid4()
    assert codes(check(Cond("nope", {}))) == ["unknown_kind"]
    assert codes(check(Cond("course_completed", {"course_id": "x"}))) == [
        "params_invalid"
    ]
    cond = Cond("attempts_at_least", {"activity_id": str(act), "n": 1})
    assert "kind_not_allowed_for_target" in codes(
        check(cond, Catalog(activities={act: 3}))
    )


def test_missing_and_foreign_references():
    a, act = uuid4(), uuid4()
    issues = check(
        All(
            (
                done(a),
                Cond(
                    "minimum_score",
                    {"subject": "activity", "ref_id": str(act), "min_pct": 50},
                ),
            )
        )
    )
    assert codes(issues) == ["missing_reference", "missing_reference"]
    assert [i.path for i in issues] == ["all/0", "all/1"]


def test_impossible_thresholds():
    act, ungraded = uuid4(), uuid4()
    catalog = Catalog(activities={act: 3, ungraded: None}, gradable={act})
    target = attempt_target()
    over = Cond("attempts_at_least", {"activity_id": str(act), "n": 4})
    ok = Cond("attempts_at_least", {"activity_id": str(act), "n": 3})
    unlimited = Cond("attempts_at_least", {"activity_id": str(ungraded), "n": 99})
    assert codes(check(over, catalog, target)) == ["impossible_threshold"]
    assert check(ok, catalog, target) == []
    assert check(unlimited, catalog, target) == []
    score = Cond(
        "minimum_score", {"subject": "activity", "ref_id": str(ungraded), "min_pct": 10}
    )
    assert codes(check(score, catalog, target)) == ["impossible_threshold"]


def test_self_reference_and_cycles_across_three_courses_and_a_track_edge():
    a, b, c = uuid4(), uuid4(), uuid4()
    catalog = Catalog({a, b, c})
    own = check(done(a), catalog, course_target(a))
    assert "self_reference" in codes(own) and "cycle" not in codes(own)
    # a -> b (this expr); b -> c (another set); c -> a (a track-order edge).
    cyc = check(done(b), catalog, course_target(a), extra_edges={b: {c}, c: {a}})
    assert codes(cyc) == ["cycle"] and str(c) in cyc[0].message
    assert check(done(b), catalog, course_target(a), extra_edges={b: {c}}) == []
    # A cycle not involving the target is not this version's fault.
    assert check(done(b), catalog, course_target(a), extra_edges={b: {c}, c: {b}}) == []


def test_find_cycles_and_dependency_edges():
    a, b, c, d = sorted(uuid4() for _ in range(4))
    assert find_cycles({a: {b}, b: {c}, c: {d}}) == []
    assert find_cycles({a: {a}}) == [[a]]
    assert find_cycles({a: {b}, b: {c}, c: {a}, d: {a}}) == [[a, b, c]]
    assert find_cycles({d: {c}, c: {b}, b: {a}, a: {d}}) == [[a, d, c, b]]
    expr = All(
        (
            done(b),
            Cond(
                "minimum_score",
                {"subject": "course_final", "ref_id": str(c), "min_pct": 5},
            ),
            Cond("attempts_at_least", {"activity_id": str(d), "n": 1}),
        )
    )
    edges = list(dependency_edges(Target("course_access", a), expr, REG))
    assert sorted(edges) == sorted([(a, b), (a, c)])
    assert list(dependency_edges(Target("activity_attempt", a), expr, REG)) == []


def test_depth_and_node_limits():
    expr = done(uuid4())
    for _ in range(MAX_DEPTH - 1):
        expr = All((expr,))
    catalog_courses = {
        c.params["course_id"] for _, c in walk(expr) if isinstance(c, Cond)
    }
    catalog = Catalog({UUID(c) for c in catalog_courses})
    assert "depth_exceeded" not in codes(check(expr, catalog))  # exactly MAX_DEPTH
    assert "depth_exceeded" in codes(check(All((expr,)), catalog))
    wide = All(tuple(All(()) for _ in range(MAX_NODES)))  # root + 64 = 65 nodes
    assert "too_many_nodes" in codes(check(wide))
    assert "too_many_nodes" not in codes(
        check(All(tuple(All(()) for _ in range(MAX_NODES - 1))))
    )


def test_empty_any_and_negation_rules():
    a = uuid4()
    assert "empty_any" in codes(check(Any(())))
    approval = Cond("manual_approval", {"approver_permission": "p"})
    issues = check(Not(approval))
    assert (
        codes(issues, "error") == ["negated_non_monotone"] and issues[0].path == "not/0"
    )
    nested = check(All((Any((Not(All((approval,))),)),)))
    assert [i.path for i in nested if i.code == "negated_non_monotone"] == [
        "all/0/any/0/not/0/all/0"
    ]
    # Monotone conditions may be negated; approval outside a not is fine.
    assert codes(check(Not(done(a)), Catalog({a}))) == []
    assert "negated_non_monotone" not in codes(check(approval))


def test_warnings():
    past = Cond("enrolled_before", {"at": (NOW - timedelta(days=1)).isoformat()})
    future = Cond("enrolled_before", {"at": (NOW + timedelta(days=1)).isoformat()})
    assert codes(check(past), "warning") == ["enrolled_before_in_past"]
    assert check(future) == []
    assert codes(check(All(())), "warning") == ["constant_true"]
    assert codes(check(Not(All(()))), "warning") == ["constant_false"]
    assert codes(check(Any((All(()), done(uuid4())))), "warning") == ["constant_true"]


def test_validate_does_not_mutate_or_depend_on_clock_object():
    expr = All((done(uuid4()),))
    before = to_json(expr)
    check(expr)
    assert to_json(expr) == before
    assert json.loads(before)["all"][0]["cond"] == "course_completed"
    assert replace(facts(), now=NOW).now == NOW


def test_certificate_floor_hook():
    course = uuid4()
    catalog = Catalog({course})

    def final(pct):
        return Cond(
            "minimum_score",
            {"subject": "course_final", "ref_id": str(course), "min_pct": pct},
        )

    assert CERTIFICATE_FINAL_MIN_PCT == 50
    low = check(final(49), catalog, certificate_bearing=True)
    assert codes(low) == ["below_certificate_floor"]
    assert check(final(50), catalog, certificate_bearing=True) == []
    assert check(final(10), catalog) == []  # not certificate-bearing: no floor


def test_narrower_scope_is_additive_only(leaves):
    t, f, fx = leaves
    assert run(combine_additive(t, t), fx).passed
    res = run(combine_additive(t, f), fx)
    assert not res.passed and res.reasons[0].path == "all/1"
    assert not run(combine_additive(f, t), fx).passed
