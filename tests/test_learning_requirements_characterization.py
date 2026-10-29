"""Parity proof: the requirements engine reproduces today's course access.

Slice 1 is not wired. This module drives the REAL
``entitlements.course_access_states`` / ``unmet_prerequisites`` and the engine
(compile -> evaluate -> map back) from the same database rows and requires the
same answer for every course. Any mismatch is a stop-and-report, not something
to adjust the mapping around.

Documented nondeterminism: ``entitlements`` reads explicit prerequisites with
an un-ordered SELECT. When a course has more than one distinct unmet
prerequisite and no route lock, the legacy ``locked_until_course_id`` is
whichever row the database returned first, so those cases assert membership in
the unmet set (and a reason consistent with the legacy id) instead of equality.

Runs on hosted migrated PostgreSQL only (same fixtures as
``test_learning_entitlements``); like the rest of this repo it is executed by
Git-hosted CI.
"""

import random
from datetime import UTC, datetime
from uuid import UUID, uuid4

from dotmac_learning.entitlements import (
    _course_lock_reason,
    _entitled_course_rows,
    course_access_states,
    unmet_prerequisites,
)
from dotmac_learning.models import Course, CourseCompletion
from dotmac_learning.participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Track,
    TrackCourse,
)
from dotmac_learning.requirements import (
    LearnerFacts,
    Route,
    Target,
    default_registry,
    evaluate,
)
from dotmac_learning.requirements.legacy import (
    PREREQUISITE_FALLBACK,
    compile_course_access,
    legacy_unmet_prerequisites,
    to_course_access_state,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests import test_learning_track_commands as track_commands
from tests.test_learning_catalog import _course, _scope
from tests.test_learning_entitlements import _setup

track_case = track_commands.track_case
learning_database = track_commands.learning_database

NOW = datetime(2026, 1, 1, tzinfo=UTC)
REGISTRY = default_registry()


def _load(db, tenant, person):
    """Test-local facts loader over the same row shapes entitlements reads."""
    rows = _entitled_course_rows(db, tenant_id=tenant, person_id=person)
    completed = frozenset(
        db.scalars(
            select(CourseCompletion.course_id)
            .where(CourseCompletion.tenant_id == tenant)
            .where(CourseCompletion.person_id == person)
            .where(CourseCompletion.status == "completed")
        ).all()
    )
    sequences: dict[UUID, list[UUID]] = {}
    legacy: list[UUID] = []
    for course_id, track_id, _order, _title in rows:
        if track_id is None:
            if course_id not in legacy:
                legacy.append(course_id)
            continue
        sequences.setdefault(track_id, [])
        if course_id not in sequences[track_id]:
            sequences[track_id].append(course_id)
    routes: dict[UUID, list[Route]] = {}
    for course_id in legacy:
        routes.setdefault(course_id, []).append(Route(None, None))
    for track_id, sequence in sequences.items():
        for index, course_id in enumerate(sequence):
            previous = sequence[index - 1] if index > 0 else None
            routes.setdefault(course_id, []).append(Route(track_id, previous))
    entitled = [row[0] for row in rows]
    # Same statement as entitlements, so row order matches.
    prereq_rows = db.execute(
        select(CoursePrerequisite.course_id, CoursePrerequisite.requires_course_id)
        .where(CoursePrerequisite.tenant_id == tenant)
        .where(CoursePrerequisite.course_id.in_(entitled))
    ).all()
    prerequisites: dict[UUID, list[UUID]] = {}
    for course_id, requires in prereq_rows:
        prerequisites.setdefault(course_id, []).append(requires)
    titles = dict(
        db.execute(
            select(Course.id, Course.title).where(Course.tenant_id == tenant)
        ).all()
    )
    facts = LearnerFacts(
        tenant_id=tenant,
        person_id=person,
        now=NOW,
        completed_course_ids=completed,
        routes_by_course={cid: tuple(r) for cid, r in routes.items()},
    )
    return facts, routes, prerequisites, titles


def _assert_parity(db, tenant, person, stats=None):
    kwargs = {"tenant_id": tenant, "person_id": person}
    real = course_access_states(db, **kwargs)
    facts, routes, prerequisites, titles = _load(db, tenant, person)
    assert set(real) == set(routes)
    for course_id, legacy_state in real.items():
        course_routes = routes[course_id]
        required = prerequisites.get(course_id, [])
        expr = compile_course_access(course_id, course_routes, required)
        result = evaluate(expr, facts, REGISTRY, Target("course_access", course_id))
        mapped = to_course_access_state(
            course_id, result, course_routes, required, titles
        )
        route_locked = not result.root.children[0].passed
        unmet = {r for r in required if r not in facts.completed_course_ids}
        assert mapped.locked == legacy_state.locked, course_id
        if legacy_state.locked and len(unmet) > 1 and not route_locked:
            assert legacy_state.locked_until_course_id in unmet
            assert legacy_state.locked_reason == _course_lock_reason(
                titles.get(legacy_state.locked_until_course_id),
                PREREQUISITE_FALLBACK,
            )
            assert mapped.locked_until_course_id in unmet
            assert mapped.locked_reason == _course_lock_reason(
                titles.get(mapped.locked_until_course_id), PREREQUISITE_FALLBACK
            )
        else:
            assert mapped.locked_until_course_id == legacy_state.locked_until_course_id
            assert mapped.locked_reason == legacy_state.locked_reason
        if stats is not None:
            stats["courses"] += 1
            route_passes = [n.passed for n in result.root.children[0].children]
            stats["mixed_routes"] += any(route_passes) and not all(route_passes)
            stats["several_unmet"] += len(unmet) > 1
            stats["route_locked"] += route_locked
            stats["prerequisite_locked"] += mapped.locked and not route_locked
            stats["open"] += not mapped.locked
        _assert_unmet(
            db,
            kwargs,
            course_id,
            legacy_state,
            mapped,
            required,
            result,
            route_locked,
            course_routes,
            facts,
        )


def _assert_unmet(
    db,
    kwargs,
    course_id,
    legacy_state,
    mapped,
    required,
    result,
    route_locked,
    course_routes,
    facts,
):
    """``unmet_prerequisites`` == failing course_completed/track_position leaves."""
    real_unmet = unmet_prerequisites(db, course_id=course_id, **kwargs)
    mine = legacy_unmet_prerequisites(mapped, required, facts.completed_course_ids)
    # Failing leaves: prerequisite group, plus the explaining (last) route.
    failing = {
        UUID(node_result_param(n))
        for n in result.root.children[1].children
        if not n.passed
    }
    if route_locked:
        failing.add(course_routes[-1].predecessor_course_id)
    assert set(real_unmet) == set(mine) == failing
    if len({r for r in required if r not in facts.completed_course_ids}) <= 1:
        # Legacy duplicate behaviour (e.g. [a, a]) is preserved exactly.
        assert real_unmet == mine
    if legacy_state.locked:
        assert real_unmet


def node_result_param(node):
    return node.reasons[0].params["course_id"]


# -- deterministic scenarios ----------------------------------------------


def test_parity_cross_tenant_canary(track_case):
    engine, tenant, other, person, cohort, courses, _foreign = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _setup(db, tenant, person, cohort, courses[:2])
        _assert_parity(db, tenant, person)
        _assert_parity(db, tenant, uuid4())
        _scope(db, other)
        _assert_parity(db, other, person)
        assert course_access_states(db, tenant_id=other, person_id=person) == {}


def test_parity_sequence_and_eligibility(track_case):
    engine, tenant, _, person, cohort, (a, b, c, d), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _, enrollment = _setup(db, tenant, person, cohort, (a, b, c))
        db.get(Course, a).status = "draft"
        db.get(Course, b).title = "Eligible previous"
        db.get(Course, c).status = "completed"
        db.add(CourseOffering(tenant_id=tenant, cohort_id=cohort, course_id=d))
        db.flush()
        _assert_parity(db, tenant, person)
        states = course_access_states(db, tenant_id=tenant, person_id=person)
        assert states[c].locked_until_course_id == b
        enrollment.track_id = None
        db.flush()
        _assert_parity(db, tenant, person)
        enrollment.status = "dropped"
        db.flush()
        _assert_parity(db, tenant, person)


def test_parity_any_route_and_duplicate_explicit_prerequisites(track_case):
    engine, tenant, _, person, cohort, (a, b, c, _), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _setup(db, tenant, person, cohort, (a, b))
        db.get(Course, a).title = "First"
        db.get(Course, c).title = "Explicit"
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=a))
        db.flush()
        _assert_parity(db, tenant, person)
        legacy = Cohort(tenant_id=tenant, name="Legacy", discipline="engineering")
        db.add(legacy)
        db.flush()
        db.add_all(
            [
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=legacy.id,
                    person_id=person,
                    role_in_cohort="student",
                    status="active",
                ),
                CourseOffering(tenant_id=tenant, cohort_id=legacy.id, course_id=b),
            ]
        )
        db.flush()
        _assert_parity(db, tenant, person)  # legacy route opens b; prerequisite locks
        db.delete(db.scalar(select(CoursePrerequisite)))
        db.flush()
        _assert_parity(db, tenant, person)
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=a))
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=a,
                status="completed",
                pct=1,
            )
        )
        db.flush()
        _assert_parity(db, tenant, person)
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=c))
        db.flush()
        _assert_parity(db, tenant, person)
        assert (
            course_access_states(db, tenant_id=tenant, person_id=person)[
                b
            ].locked_reason
            == 'Complete "Explicit" to unlock this course.'
        )


def test_parity_null_track_is_cohort_wide(track_case):
    engine, tenant, _, person, cohort, courses, _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _, enrollment = _setup(db, tenant, person, cohort, courses[:3])
        enrollment.track_id = None
        db.add(
            CoursePrerequisite(
                tenant_id=tenant, course_id=courses[2], requires_course_id=courses[0]
            )
        )
        db.flush()
        _assert_parity(db, tenant, person)
        states = course_access_states(db, tenant_id=tenant, person_id=person)
        assert not states[courses[0]].locked and not states[courses[1]].locked
        assert states[courses[2]].locked_until_course_id == courses[0]


# -- seeded random scenarios ------------------------------------------------


def _build(db, tenant, person, rng):
    count = rng.randint(1, 6)
    courses = [_course(db, tenant) for _ in range(count)]
    db.flush()
    for index, course_id in enumerate(courses):
        row = db.get(Course, course_id)
        row.title = f"Course {index}"
        if rng.random() < 0.12:
            row.status = "draft"
    tracks = []
    for _ in range(rng.randint(1, 3)):
        track = Track(tenant_id=tenant, slug=uuid4().hex, name="Track")
        db.add(track)
        tracks.append(track)
    db.flush()
    members = {}
    for track in tracks:
        members[track.id] = rng.sample(courses, rng.randint(1, count))
        for position, course_id in enumerate(members[track.id], start=1):
            db.add(
                TrackCourse(
                    tenant_id=tenant,
                    track_id=track.id,
                    course_id=course_id,
                    order_index=position,
                )
            )
    cohorts = []
    for index in range(rng.randint(1, 2)):
        cohort = Cohort(
            tenant_id=tenant, name=f"Cohort {index}", discipline="engineering"
        )
        db.add(cohort)
        cohorts.append(cohort)
    db.flush()
    for cohort in cohorts:
        enabled = rng.sample(tracks, rng.randint(0, len(tracks)))
        for track in enabled:
            db.add(
                CohortTrack(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    track_id=track.id,
                    status="active",
                )
            )
        db.flush()
        chosen = rng.choice([None, *enabled])
        offered = {
            course_id
            for track in enabled
            for course_id in members[track.id]
            if rng.random() < 0.85
        } | {course_id for course_id in courses if rng.random() < 0.2}
        if chosen is None and not offered:
            offered = {rng.choice(courses)}
        for course_id in sorted(offered, key=str):
            db.add(
                CourseOffering(
                    tenant_id=tenant, cohort_id=cohort.id, course_id=course_id
                )
            )
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort.id,
                person_id=person,
                track_id=None if chosen is None else chosen.id,
                role_in_cohort="student",
                status="dropped" if rng.random() < 0.1 else "active",
            )
        )
    order = rng.sample(courses, count)
    pairs = [(order[j], order[i]) for j in range(count) for i in range(j)]
    for course_id, requires in rng.sample(pairs, min(len(pairs), rng.randint(0, 6))):
        db.add(
            CoursePrerequisite(
                tenant_id=tenant, course_id=course_id, requires_course_id=requires
            )
        )
    for course_id in courses:
        roll = rng.random()
        if roll < 0.4:
            db.add(
                CourseCompletion(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course_id,
                    status="completed",
                    pct=1,
                )
            )
        elif roll < 0.55:
            db.add(
                CourseCompletion(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course_id,
                    status="in_progress",
                    pct=0.4,
                )
            )
    db.flush()


def test_parity_200_seeded_random_scenarios(track_case):
    engine, tenant, _, person, _cohort, _courses, _ = track_case
    stats = {
        "courses": 0,
        "route_locked": 0,
        "prerequisite_locked": 0,
        "open": 0,
        "mixed_routes": 0,
        "several_unmet": 0,
    }
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        for seed in range(200):
            savepoint = db.begin_nested()
            try:
                _build(db, tenant, person, random.Random(seed))  # noqa: S311
                _assert_parity(db, tenant, person, stats)
            except AssertionError as exc:
                raise AssertionError(f"parity mismatch at seed {seed}") from exc
            finally:
                savepoint.rollback()
    # The generator must actually exercise every outcome.
    assert stats["courses"] > 200
    assert stats["route_locked"] > 0
    assert stats["prerequisite_locked"] > 0
    assert stats["open"] > 0
    # Courses reached by several routes, one open and one locked.
    assert stats["mixed_routes"] > 0
    assert stats["several_unmet"] > 0
