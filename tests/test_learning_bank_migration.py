"""Hosted lr_0010 goldens: published-v1 backfill, pins and downgrade gate.

Seeds at lr_0009 with raw SQL, upgrades to head, and proves the backfill
materializes the version layer without inventing historical pins.
"""

import json
from uuid import uuid4

import pytest
from dotmac_learning import canonical_content_hash
from sqlalchemy import create_engine, text

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import (
    KERNEL_VERSIONS,
    LEARNING_VERSIONS,
    REPO_ROOT,
    _course,
    _person,
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


QUESTIONS = (
    ("q1", "First?", ["aaaa", "bbbb"], ["aaaa"], "recall"),
    ("q2", "Second?", ["aaaa", "bbbb"], ["bbbb"], "application"),
)


def _seed_at_lr0009(app, tenant, person):
    ids = {}
    with app.begin() as conn:
        _scope(conn, tenant)
        course = _course(conn, tenant)
        other_course = _course(conn, tenant)
        bank, activity = uuid4(), uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_learning.question_banks"
                " (id, tenant_id, course_id, chapter_number, kind, version)"
                " VALUES (:id, :t, :course, 1, 'chapter', 7)"
            ),
            {"id": bank, "t": tenant, "course": course},
        )
        for ext_id, stem, options, correct, rubric in QUESTIONS:
            conn.execute(
                text(
                    "INSERT INTO mod_learning.questions"
                    " (id, tenant_id, bank_id, ext_id, stem, type, options,"
                    " correct, rubric_category, explanation, weight)"
                    " VALUES (:id, :t, :bank, :ext, :stem, 'single',"
                    " CAST(:options AS jsonb), CAST(:correct AS jsonb),"
                    " :rubric, '', 1)"
                ),
                {
                    "id": uuid4(),
                    "t": tenant,
                    "bank": bank,
                    "ext": ext_id,
                    "stem": stem,
                    "options": json.dumps(options),
                    "correct": json.dumps(correct),
                    "rubric": rubric,
                },
            )
        conn.execute(
            text(
                "INSERT INTO mod_learning.activities"
                " (id, tenant_id, course_id, chapter_number, type, bank_id,"
                " title, pass_threshold, grading, weight)"
                " VALUES (:id, :t, :course, 1, 'mcq_test', :bank, 'T',"
                " 0.5, 'auto', 1.0)"
            ),
            {"id": activity, "t": tenant, "course": course, "bank": bank},
        )
        open_attempt, closed_attempt = uuid4(), uuid4()
        statements = (
            (
                open_attempt,
                "INSERT INTO mod_learning.activity_attempts"
                " (id, tenant_id, activity_id, person_id,"
                " question_ext_ids, started_at, submitted_at)"
                " VALUES (:id, :t, :activity, :person,"
                " CAST(:ext AS jsonb), now(), NULL)",
            ),
            (
                closed_attempt,
                "INSERT INTO mod_learning.activity_attempts"
                " (id, tenant_id, activity_id, person_id,"
                " question_ext_ids, started_at, submitted_at)"
                " VALUES (:id, :t, :activity, :person,"
                " CAST(:ext AS jsonb), now(), now())",
            ),
        )
        for attempt, statement in statements:
            conn.execute(
                text(statement),
                {
                    "id": attempt,
                    "t": tenant,
                    "activity": activity,
                    "person": person,
                    "ext": json.dumps(["q1", "q2"]),
                },
            )
        conn.execute(
            text(
                "INSERT INTO mod_learning.course_prerequisites"
                " (id, tenant_id, course_id, requires_course_id)"
                " VALUES (:id, :t, :course, :requires)"
            ),
            {"id": uuid4(), "t": tenant, "course": other_course, "requires": course},
        )
        ids = {
            "bank": bank,
            "activity": activity,
            "open": open_attempt,
            "closed": closed_attempt,
        }
    return ids


def test_lr_0010_backfill_publishes_v1_and_pins_open_sittings(learning_database):
    from alembic import command

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = _config()
    tenant = uuid4()
    try:
        command.downgrade(cfg, "lr_0009_composable_policies")
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        ids = _seed_at_lr0009(app, tenant, person)
        command.upgrade(cfg, "learning@head")
        with admin.connect() as conn:
            heads = set(conn.scalars(text("SELECT version_num FROM alembic_version")))
            assert heads == {"lr_0010_question_bank_versions"}
        with app.begin() as conn:
            _scope(conn, tenant)
            version = conn.execute(
                text(
                    "SELECT id, version_no, state, origin, content_hash"
                    " FROM mod_learning.question_bank_versions"
                    " WHERE bank_id = :bank"
                ),
                {"bank": ids["bank"]},
            ).one()
            assert (version.version_no, version.state, version.origin) == (
                1,
                "published",
                "backfill",
            )
            expected_hash = canonical_content_hash(
                [
                    {
                        "ext_id": ext_id,
                        "stem": stem,
                        "type": "single",
                        "options": options,
                        "correct": correct,
                        "rubric_category": rubric,
                        "category": None,
                        "explanation": "",
                        "weight": 1,
                    }
                    for ext_id, stem, options, correct, rubric in QUESTIONS
                ]
            )
            assert version.content_hash == expected_hash
            assert conn.execute(
                text(
                    "SELECT version, current_version_id"
                    " FROM mod_learning.question_banks WHERE id = :bank"
                ),
                {"bank": ids["bank"]},
            ).one() == (1, version.id)
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM mod_learning.questions"
                        " WHERE bank_version_id IS DISTINCT FROM :vid"
                    ),
                    {"vid": version.id},
                )
                == 0
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT bank_version_id FROM mod_learning.activities"
                        " WHERE id = :id"
                    ),
                    {"id": ids["activity"]},
                )
                == version.id
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT bank_version_id FROM mod_learning.activity_attempts"
                        " WHERE id = :id"
                    ),
                    {"id": ids["open"]},
                )
                == version.id
            )
            # Closed history is not given a fabricated pin.
            assert (
                conn.scalar(
                    text(
                        "SELECT bank_version_id FROM mod_learning.activity_attempts"
                        " WHERE id = :id"
                    ),
                    {"id": ids["closed"]},
                )
                is None
            )
            assert (
                conn.scalar(
                    text("SELECT origin FROM mod_learning.course_prerequisites")
                )
                == "yaml_import"
            )
            # Pre-existing rows backfill as yaml_import, but the standing
            # insert default after the backfill is fail-closed ui.
            standing_default = conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns"
                    " WHERE table_schema = 'mod_learning'"
                    " AND table_name = 'course_prerequisites'"
                    " AND column_name = 'origin'"
                )
            )
            assert standing_default is not None and "'ui'" in standing_default
    finally:
        admin.dispose()
        app.dispose()


def test_lr_0010_refuses_duplicate_bank_natural_keys(learning_database):
    from alembic import command

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = _config()
    tenant = uuid4()
    try:
        command.downgrade(cfg, "lr_0009_composable_policies")
        with admin.begin() as conn:
            _tenant(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            course = _course(conn, tenant)
            for _ in range(2):
                conn.execute(
                    text(
                        "INSERT INTO mod_learning.question_banks"
                        " (id, tenant_id, course_id, chapter_number, kind,"
                        " version) VALUES (:id, :t, :course, NULL, 'final', 1)"
                    ),
                    {"id": uuid4(), "t": tenant, "course": course},
                )
        with pytest.raises(Exception, match="duplicate question-bank"):
            command.upgrade(cfg, "learning@head")
    finally:
        # Restore head for fixture teardown symmetry on a clean tenant.
        with admin.begin() as conn:
            conn.execute(text("DELETE FROM mod_learning.question_banks"))
        command.upgrade(cfg, "learning@head")
        admin.dispose()
        app.dispose()


def test_lr_0010_downgrade_refuses_once_populated(learning_database):
    from alembic import command

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = _config()
    tenant = uuid4()
    try:
        command.downgrade(cfg, "lr_0009_composable_policies")
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        ids = _seed_at_lr0009(app, tenant, person)
        command.upgrade(cfg, "learning@head")
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                text(
                    "INSERT INTO mod_learning.question_bank_versions"
                    " (id, tenant_id, bank_id, state, origin)"
                    " VALUES (:id, :t, :bank, 'draft', 'ui')"
                ),
                {"id": uuid4(), "t": tenant, "bank": ids["bank"]},
            )
        with pytest.raises(Exception, match="rehearsal-only"):
            command.downgrade(cfg, "lr_0009_composable_policies")
    finally:
        admin.dispose()
        app.dispose()
