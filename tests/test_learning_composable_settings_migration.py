"""Hosted lr_0008/lr_0009 goldens: defaults backfill rows without drift.

Seeds below each migration with raw SQL (the ORM already carries the new
columns), upgrades, and proves existing rows materialize the historical
defaults; the lr_0009 half also pins the is_final heuristic backfill.
"""

import json
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import (
    KERNEL_VERSIONS,
    LEARNING_VERSIONS,
    REPO_ROOT,
    _course,
    _person,
    _refused,
    _scope,
    _tenant,
)

learning_database = learning_catalog.learning_database


def _config():
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    return cfg


def _legacy_states(prereq_pairs, completed, sequence):
    """Pre-lr_0008 semantics: track 'previous' then implicit-ALL completion.

    Returns ``{course_id: (locked, locked_until_course_id)}``; a track
    blocker keeps precedence over an explicit prerequisite blocker, as the
    parent overlay kept the first (track) reason.
    """
    states = {}
    for index, course_id in enumerate(sequence):
        previous = sequence[index - 1] if index > 0 else None
        blocker = (
            previous if previous is not None and previous not in completed else None
        )
        states[course_id] = (blocker is not None, blocker)
    for course_id, requires_id in prereq_pairs:
        if requires_id not in completed and not states[course_id][0]:
            states[course_id] = (True, requires_id)
    return states


def _serialized(states_by_course):
    """Plan golden serialization: sorted JSON of course_id+locked+locked_until."""
    rows = [
        {
            "course_id": str(course_id),
            "locked": locked,
            "locked_until": str(locked_until) if locked_until is not None else None,
        }
        for course_id, (locked, locked_until) in states_by_course.items()
    ]
    rows.sort(key=lambda row: row["course_id"])
    return json.dumps(rows, sort_keys=True)


def test_lr_0008_upgrade_backfills_defaults_and_preserves_access(learning_database):
    from dotmac_learning.entitlements import course_access_states

    from alembic import command

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = _config()
    tenant = uuid4()
    try:
        command.downgrade(cfg, "lr_0007_enrollment_state_policy")
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            a, b, c = (_course(conn, tenant) for _ in range(3))
            cohort, track = uuid4(), uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_learning.cohorts (id, tenant_id, name, discipline)"
                    " VALUES (:id, :t, 'Cohort', 'engineering')"
                ),
                {"id": cohort, "t": tenant},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.tracks (id, tenant_id, slug, name)"
                    " VALUES (:id, :t, 'seq', 'Seq')"
                ),
                {"id": track, "t": tenant},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.cohort_tracks"
                    " (id, tenant_id, cohort_id, track_id)"
                    " VALUES (:id, :t, :cohort, :track)"
                ),
                {"id": uuid4(), "t": tenant, "cohort": cohort, "track": track},
            )
            for index, course_id in enumerate((a, b, c), start=1):
                conn.execute(
                    text(
                        "INSERT INTO mod_learning.track_courses"
                        " (id, tenant_id, track_id, course_id, order_index)"
                        " VALUES (:id, :t, :track, :course, :idx)"
                    ),
                    {
                        "id": uuid4(),
                        "t": tenant,
                        "track": track,
                        "course": course_id,
                        "idx": index,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_learning.course_offerings"
                        " (id, tenant_id, cohort_id, course_id)"
                        " VALUES (:id, :t, :cohort, :course)"
                    ),
                    {"id": uuid4(), "t": tenant, "cohort": cohort, "course": course_id},
                )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.enrollments"
                    " (id, tenant_id, cohort_id, person_id, track_id,"
                    " role_in_cohort, status)"
                    " VALUES (:id, :t, :cohort, :person, :track, 'student', 'active')"
                ),
                {
                    "id": uuid4(),
                    "t": tenant,
                    "cohort": cohort,
                    "person": person,
                    "track": track,
                },
            )
            # c explicitly requires a; person completed b only.
            conn.execute(
                text(
                    "INSERT INTO mod_learning.course_prerequisites"
                    " (id, tenant_id, course_id, requires_course_id)"
                    " VALUES (:id, :t, :course, :requires)"
                ),
                {"id": uuid4(), "t": tenant, "course": c, "requires": a},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.course_completions"
                    " (id, tenant_id, person_id, course_id, status, pct)"
                    " VALUES (:id, :t, :person, :course, 'completed', 1.0)"
                ),
                {"id": uuid4(), "t": tenant, "person": person, "course": b},
            )
        expected = _legacy_states([(c, a)], {b}, [a, b, c])
        assert expected == {a: (False, None), b: (True, a), c: (True, a)}

        command.upgrade(cfg, "learning@head")
        with admin.connect() as conn:
            heads = set(conn.scalars(text("SELECT version_num FROM alembic_version")))
            assert "lr_0008_prerequisite_conditions" in heads
            for table, column in (
                ("courses", "prerequisite_composition"),
                ("tracks", "gating_mode"),
                ("course_prerequisites", "condition_kind"),
                ("course_prerequisites", "params"),
                ("course_prerequisites", "group_key"),
            ):
                columns = {
                    col["name"]
                    for col in inspect(conn).get_columns(table, schema="mod_learning")
                }
                assert column in columns
        with app.begin() as conn:
            _scope(conn, tenant)
            assert conn.execute(
                text(
                    "SELECT condition_kind, params::text, group_key"
                    " FROM mod_learning.course_prerequisites"
                )
            ).one() == ("course_completed", "{}", "g0")
            assert set(
                conn.scalars(
                    text("SELECT prerequisite_composition FROM mod_learning.courses")
                )
            ) == {"all"}
            assert set(
                conn.scalars(text("SELECT gating_mode FROM mod_learning.tracks"))
            ) == {"previous"}
            # Check constraints refuse values outside the closed catalogs.
            _refused(
                conn,
                "UPDATE mod_learning.courses SET prerequisite_composition='some'",
                {},
                "23514",
            )
            _refused(
                conn,
                "UPDATE mod_learning.tracks SET gating_mode='sometimes'",
                {},
                "23514",
            )
            _refused(
                conn,
                "UPDATE mod_learning.course_prerequisites"
                " SET condition_kind='waiver'",
                {},
                "23514",
            )
        with Session(bind=app) as db, db.begin():
            _scope(db.connection(), tenant)
            states = course_access_states(db, tenant_id=tenant, person_id=person)
            actual = {
                course_id: (
                    states[course_id].locked,
                    states[course_id].locked_until_course_id,
                )
                for course_id in expected
            }
            assert _serialized(actual) == _serialized(expected)
            # Reason is asserted on single-unmet seeded courses only (b is
            # blocked solely by track order, c solely by the explicit
            # prerequisite); a is unlocked. Seeded titles are all "Course".
            assert states[a].locked_reason is None
            assert states[b].locked_reason == (
                'Complete "Course" to unlock this course.'
            )
            assert states[c].locked_reason == (
                'Complete "Course" to unlock this course.'
            )

        # Downgrade smoke: non-default rows are deleted, the historical
        # unique key returns, and a re-upgrade restores the defaults.
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                text(
                    "UPDATE mod_learning.course_prerequisites"
                    " SET condition_kind='certificate_held'"
                )
            )
        command.downgrade(cfg, "lr_0007_enrollment_state_policy")
        with app.begin() as conn:
            _scope(conn, tenant)
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM mod_learning.course_prerequisites")
                )
                == 0
            )
        command.upgrade(cfg, "learning@head")
    finally:
        app.dispose()
        admin.dispose()


def test_lr_0009_upgrade_backfills_defaults_and_finals(learning_database):
    from alembic import command

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = _config()
    tenant = uuid4()
    matrix = [(None, 0.7, True), (None, 0.0, False), (1, 0.7, False), (2, 0.0, False)]
    try:
        command.downgrade(cfg, "lr_0008_prerequisite_conditions")
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        activity_ids = []
        with app.begin() as conn:
            _scope(conn, tenant)
            course_id = _course(conn, tenant)
            cohort, offering = uuid4(), uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_learning.cohorts (id, tenant_id, name, discipline)"
                    " VALUES (:id, :t, 'Cohort', 'engineering')"
                ),
                {"id": cohort, "t": tenant},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.course_offerings"
                    " (id, tenant_id, cohort_id, course_id)"
                    " VALUES (:id, :t, :cohort, :course)"
                ),
                {"id": offering, "t": tenant, "cohort": cohort, "course": course_id},
            )
            for chapter, threshold, _want in matrix:
                act_id = uuid4()
                activity_ids.append(act_id)
                conn.execute(
                    text(
                        "INSERT INTO mod_learning.activities"
                        " (id, tenant_id, course_id, chapter_number, type, title,"
                        " pass_threshold, grading, weight)"
                        " VALUES (:id, :t, :course, :chapter, 'mcq_test', 'A',"
                        " :threshold, 'auto', 1.0)"
                    ),
                    {
                        "id": act_id,
                        "t": tenant,
                        "course": course_id,
                        "chapter": chapter,
                        "threshold": threshold,
                    },
                )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.certificates"
                    " (id, tenant_id, person_id, course_id, serial, issued_at)"
                    " VALUES (:id, :t, :person, :course, 'CERT-SEED', now())"
                ),
                {"id": uuid4(), "t": tenant, "person": person, "course": course_id},
            )

        command.upgrade(cfg, "learning@head")
        with admin.connect() as conn:
            heads = set(conn.scalars(text("SELECT version_num FROM alembic_version")))
            assert heads == {"lr_0010_question_bank_versions"}
        with app.begin() as conn:
            _scope(conn, tenant)
            assert conn.execute(
                text(
                    "SELECT completion_fraction, certificate_auto_issue,"
                    " certificate_validity_days FROM mod_learning.courses"
                )
            ).one() == (1.0, True, None)
            assert conn.execute(
                text(
                    "SELECT scoring_policy, min_attempt_interval_seconds"
                    " FROM mod_learning.activities"
                    " WHERE id = :id"
                ),
                {"id": activity_ids[0]},
            ).one() == ("best", 0)
            finals = dict(
                conn.execute(
                    text(
                        "SELECT id, is_final FROM mod_learning.activities"
                        " WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                ).all()
            )
            for act_id, (_chapter, _threshold, want) in zip(
                activity_ids, matrix, strict=True
            ):
                assert finals[act_id] is want
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM mod_learning.activities"
                        " WHERE is_final <>"
                        " (chapter_number IS NULL AND pass_threshold > 0)"
                    )
                )
                == 0
            )
            assert set(
                conn.scalars(text("SELECT expires_at FROM mod_learning.certificates"))
            ) == {None}
            assert set(
                conn.scalars(
                    text(
                        "SELECT pacing_default_open"
                        " FROM mod_learning.course_offerings"
                    )
                )
            ) == {True}
            assert set(
                conn.scalars(
                    text("SELECT auto_accept_below_action FROM mod_learning.cohorts")
                )
            ) == {"waitlisted"}
            for statement in (
                "UPDATE mod_learning.courses SET completion_fraction = 0",
                "UPDATE mod_learning.courses SET certificate_validity_days = 0",
                "UPDATE mod_learning.activities SET scoring_policy = 'median'",
                "UPDATE mod_learning.activities SET min_attempt_interval_seconds = -1",
                "UPDATE mod_learning.activities"
                " SET min_attempt_interval_seconds = 2592001",
                "UPDATE mod_learning.cohorts"
                " SET auto_accept_below_action = 'rejected'",
            ):
                _refused(conn, statement, {}, "23514")

        command.downgrade(cfg, "lr_0008_prerequisite_conditions")
        with admin.connect() as conn:
            # All nine lr_0009 columns and their named checks are gone,
            # while prior-lineage columns survive the downgrade (A-P3-6).
            added = {
                "courses": {
                    "completion_fraction",
                    "certificate_auto_issue",
                    "certificate_validity_days",
                },
                "activities": {
                    "scoring_policy",
                    "is_final",
                    "min_attempt_interval_seconds",
                },
                "certificates": {"expires_at"},
                "course_offerings": {"pacing_default_open"},
                "cohorts": {"auto_accept_below_action"},
            }
            inspector = inspect(conn)
            for table, dropped in added.items():
                remaining = {
                    col["name"]
                    for col in inspector.get_columns(table, schema="mod_learning")
                }
                assert dropped.isdisjoint(remaining), (table, dropped & remaining)
                checks = {
                    chk["name"]
                    for chk in inspector.get_check_constraints(
                        table, schema="mod_learning"
                    )
                }
                named = {
                    "courses": {
                        "ck_courses_completion_fraction",
                        "ck_courses_certificate_validity_days",
                    },
                    "activities": {
                        "ck_activities_scoring_policy",
                        "ck_activities_min_attempt_interval",
                    },
                    "cohorts": {"ck_cohorts_auto_accept_below_action"},
                }.get(table, set())
                assert named.isdisjoint(checks), (table, named & checks)
            retained = {
                col["name"]
                for col in inspector.get_columns("courses", schema="mod_learning")
            }
            assert "prerequisite_composition" in retained
        command.upgrade(cfg, "learning@head")
    finally:
        app.dispose()
        admin.dispose()
