"""Durable polling attempt/failure/backoff evidence, and its boundaries.

Logic and structure only — SQLite, no RLS, single-threaded. The claims that need
a real database (row-locked attempt counting under concurrency, crash/retry
across a rolled-back transaction, starvation across a keyset walk, backoff
observed through PostgreSQL's own timestamps) are proved in
`tests/test_integration_isolation.py`, which composes the `ig` lineage against
PostgreSQL. Asserting them here would be asserting them against a backend that
renders no `FOR UPDATE` and runs one connection — a green test about nothing.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from dotmac_integration import (
    DEFAULT_POLICY,
    DEFAULT_POLL_PAGE_SIZE,
    MAX_POLL_PAGE_SIZE,
    POLL_FAILURE_CODES,
    POLL_PAGE_SIZE_VAR,
    CapabilityBinding,
    CheckpointConflict,
    ConnectorConfigRevision,
    ConnectorInstallation,
    ConnectorRegistry,
    CursorInvalid,
    ExecutionPolicy,
    InboxReceipt,
    PollConnectorRaised,
    PollContractError,
    PollFailurePageKey,
    PollHandlerUnavailable,
    PollingAttemptFailure,
    PollingCheckpoint,
    PollingJobUnknown,
    PollPageKey,
    PollSecretsUnavailable,
    PollUnavailable,
    classify_poll_failure,
    due_polling_jobs,
    is_retry_eligible,
    poll_backoff_seconds,
    poll_failure_history,
    poll_once,
    prune_poll_failure_history,
    record_poll_failure,
    record_poll_success,
    resolve_poll_page_size,
    retry_delay_seconds,
    retry_state,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, FakePlugin, fake_plugin
from dotmac_integration.manifest import module
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO_ROOT
    / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
    / "ig_0014_polling_evidence.py"
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


@pytest.fixture()
def engine() -> Engine:
    value = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        InboxReceipt,
        PollingCheckpoint,
        PollingAttemptFailure,
    ):
        cast(Any, model.__table__).create(value)
    return value


def _seed(
    engine: Engine,
    *,
    jobs: tuple[str, ...] = ("live-tail",),
    events: tuple[Any, ...] = (),
    next_cursor: str | None = "page-2",
    poll_raises: BaseException | None = None,
    binding_state: str = "enabled",
    installation_state: str = "enabled",
) -> tuple[ConnectorRegistry, FakePlugin, tuple[uuid.UUID, ...]]:
    """One installation, one binding, and one checkpoint per `jobs` entry."""
    plugin = fake_plugin(
        inbound=events, next_cursor=next_cursor, poll_raises=poll_raises
    )
    registry = ConnectorRegistry((plugin,))
    with Session(engine) as db:
        installation = ConnectorInstallation(
            id=uuid.uuid4(),
            connector_key=plugin.manifest.connector_key,
            connector_version=plugin.manifest.version,
            spi_range=str(plugin.manifest.spi_range),
            manifest_digest=plugin.manifest.digest,
            name="poll-source",
            state=installation_state,
        )
        db.add(installation)
        db.flush()
        revision = ConnectorConfigRevision(
            id=uuid.uuid4(),
            installation_id=installation.id,
            revision=1,
            schema_version="1",
            config_digest="c" * 64,
            config_json={"variant": "primary"},
            secret_refs={"token": "bao://integrator/poll/token"},
        )
        db.add(revision)
        db.flush()
        installation.current_config_revision_id = revision.id
        binding = CapabilityBinding(
            id=uuid.uuid4(),
            installation_id=installation.id,
            capability_id=FAKE_CAPABILITY,
            state=binding_state,
        )
        db.add(binding)
        db.flush()
        ids: list[uuid.UUID] = []
        for index, job_key in enumerate(jobs):
            checkpoint = PollingCheckpoint(
                id=uuid.uuid4(),
                capability_binding_id=binding.id,
                job_key=job_key,
                version=1,
                cursor_json={"cursor": "page-1"},
                # Distinct floors, ascending with the job order, so ordering
                # assertions below are about the query rather than about which
                # row a database happened to write first.
                next_attempt_at=NOW - timedelta(minutes=100 - index),
            )
            db.add(checkpoint)
            ids.append(checkpoint.id)
        db.commit()
        return registry, plugin, tuple(ids)


def _unit_of_work(engine: Engine) -> Any:
    @contextmanager
    def _open() -> Iterator[Session]:
        with Session(engine) as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    return _open


# ── The closed failure vocabulary ───────────────────────────────────────────


def test_every_poll_exception_type_maps_to_its_own_code() -> None:
    """Most-specific-first, so three remedies do not collapse into one code."""
    assert classify_poll_failure(CursorInvalid("x")) == "cursor_invalid"
    assert classify_poll_failure(PollHandlerUnavailable("x")) == "handler_unavailable"
    assert classify_poll_failure(PollUnavailable("x")) == "checkpoint_unavailable"
    assert classify_poll_failure(PollSecretsUnavailable("x")) == "secrets_unavailable"
    assert classify_poll_failure(PollContractError("x")) == "contract_violated"
    assert classify_poll_failure(PollConnectorRaised("TimeoutError")) == (
        "connector_raised"
    )
    assert classify_poll_failure(CheckpointConflict("x")) == "checkpoint_conflict"


def test_the_subclasses_are_not_swallowed_by_their_base() -> None:
    """The sensitivity half: a broad-first chain would make these all equal."""
    assert issubclass(CursorInvalid, PollUnavailable)
    assert issubclass(PollHandlerUnavailable, PollUnavailable)
    codes = {
        classify_poll_failure(CursorInvalid("x")),
        classify_poll_failure(PollHandlerUnavailable("x")),
        classify_poll_failure(PollUnavailable("x")),
    }
    assert len(codes) == 3


def test_an_unrecognised_error_is_recorded_rather_than_given_a_new_code() -> None:
    assert classify_poll_failure(ValueError("anything")) == "settlement_failed"
    assert classify_poll_failure(RuntimeError("anything")) == "settlement_failed"


def test_every_declared_code_is_reachable_from_the_classifier() -> None:
    """No dead vocabulary: a code nothing can produce reads as a working state."""
    produced = {
        classify_poll_failure(error)
        for error in (
            PollUnavailable("x"),
            CursorInvalid("x"),
            PollHandlerUnavailable("x"),
            PollSecretsUnavailable("x"),
            PollContractError("x"),
            PollConnectorRaised("TimeoutError"),
            CheckpointConflict("x"),
            ValueError("x"),
        )
    }
    assert produced == set(POLL_FAILURE_CODES)


def test_the_vocabulary_matches_its_check_constraint_in_both_places() -> None:
    """The Python tuple, the ORM constraint and the migration must agree.

    Three copies is one too many to trust by eye, and the one that matters most
    is the migration: a code the database refuses is an exception raised at the
    moment the engine is trying to record an exception.
    """
    constraint = next(
        text
        for text in (
            str(item.sqltext)
            for item in cast(Any, PollingAttemptFailure.__table__).constraints
            if getattr(item, "name", None) == "ck_polling_attempt_failures_code"
        )
    )
    migration = MIGRATION.read_text(encoding="utf-8")
    for code in POLL_FAILURE_CODES:
        assert f"'{code}'" in constraint, f"{code} missing from the ORM constraint"
        assert f"'{code}'" in migration, f"{code} missing from the migration"
    quoted = set(re.findall(r"'([a-z_]+)'", constraint))
    assert quoted == set(POLL_FAILURE_CODES)


# ── No channel for provider or connector text ───────────────────────────────


def test_the_evidence_table_has_no_free_text_column() -> None:
    """The guarantee is structural: there is nowhere for a message to go.

    Every other table in this module that persists failure detail needs a
    source scan to keep exception text out of it. This one cannot be broken by
    a future handler because it has no column that would accept the text.
    """
    columns = {
        column.name: column for column in cast(Any, PollingAttemptFailure.__table__).c
    }
    assert set(columns) == {
        "id",
        "checkpoint_id",
        "attempt_number",
        "checkpoint_version",
        "failure_code",
        "connector_exception",
        "retry_in_seconds",
        "next_attempt_at",
        "observed_at",
    }
    for name in ("error_detail", "message", "payload_json", "headers_json", "reason"):
        assert name not in columns
    # The one string column that is not the closed vocabulary is bounded to an
    # identifier's length, not to a message's.
    assert columns["connector_exception"].type.length == 120


def test_a_connector_exception_is_stored_as_a_bare_type_name(engine: Engine) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        recorded = record_poll_failure(
            db,
            checkpoint_id=checkpoint_id,
            error=PollConnectorRaised("TimeoutError"),
            now=NOW,
        )
        db.commit()
    assert recorded.connector_exception == "TimeoutError"


def test_a_connector_that_smuggles_a_message_stores_no_part_of_it(
    engine: Engine,
) -> None:
    """The raise site sanitizes; the write site re-checks. Both, deliberately."""
    _, _, (checkpoint_id,) = _seed(engine)
    smuggled = PollConnectorRaised("Timeout: token=held-material")
    with Session(engine) as db:
        recorded = record_poll_failure(
            db, checkpoint_id=checkpoint_id, error=smuggled, now=NOW
        )
        db.commit()
    assert recorded.connector_exception == "Exception"
    assert "held-material" not in str(recorded)


def test_an_engine_failure_names_no_connector_exception(engine: Engine) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        recorded = record_poll_failure(
            db,
            checkpoint_id=checkpoint_id,
            error=PollSecretsUnavailable("unavailable"),
            now=NOW,
        )
        db.commit()
    assert recorded.connector_exception is None


# ── Backoff has one owner ───────────────────────────────────────────────────


def test_poll_backoff_is_the_engine_curve_and_not_a_second_copy() -> None:
    for attempt in range(0, 24):
        assert poll_backoff_seconds(attempt) == retry_delay_seconds(attempt, None)


def test_poll_backoff_saturates_at_the_policy_ceiling() -> None:
    policy = ExecutionPolicy(base_delay_seconds=60, max_backoff_seconds=600)
    assert poll_backoff_seconds(1, policy=policy) == 60
    assert poll_backoff_seconds(2, policy=policy) == 120
    assert poll_backoff_seconds(99, policy=policy) == 600


def test_a_negative_attempt_count_is_refused_rather_than_clamped() -> None:
    with pytest.raises(ValueError, match="negative"):
        poll_backoff_seconds(-1)


# ── Recording a failure ─────────────────────────────────────────────────────


def test_a_failure_appends_evidence_and_advances_only_the_floor(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        recorded = record_poll_failure(
            db,
            checkpoint_id=checkpoint_id,
            error=PollConnectorRaised("TimeoutError"),
            now=NOW,
        )
        db.commit()

    assert recorded.attempt_number == 1
    assert recorded.failure_code == "connector_raised"
    assert recorded.retry_in_seconds == poll_backoff_seconds(1)
    assert recorded.next_attempt_at == NOW + timedelta(
        seconds=recorded.retry_in_seconds
    )

    with Session(engine) as db:
        state = retry_state(db, checkpoint_id=checkpoint_id)
        checkpoint = db.get(PollingCheckpoint, checkpoint_id)
        assert checkpoint is not None
    assert state.attempt_count == 1
    assert state.last_failure_code == "connector_raised"
    assert state.last_failure_at == NOW
    assert state.failing is True
    # The cursor did not move, so nothing about the cursor may claim it did.
    assert state.version == 1
    assert checkpoint.cursor_json == {"cursor": "page-1"}


def test_failure_bookkeeping_never_touches_the_optimistic_version(
    engine: Engine,
) -> None:
    """Bumping it would make an in-flight settle lose a race that never ran."""
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        for _ in range(4):
            record_poll_failure(
                db,
                checkpoint_id=checkpoint_id,
                error=PollContractError("bad shape"),
                now=NOW,
            )
        db.commit()
    with Session(engine) as db:
        assert retry_state(db, checkpoint_id=checkpoint_id).version == 1


def test_consecutive_failures_number_and_back_off_further_each_time(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    delays: list[int] = []
    with Session(engine) as db:
        for index in range(4):
            recorded = record_poll_failure(
                db,
                checkpoint_id=checkpoint_id,
                error=PollContractError("bad shape"),
                now=NOW + timedelta(seconds=index),
            )
            assert recorded.attempt_number == index + 1
            delays.append(recorded.retry_in_seconds)
        db.commit()
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_a_poll_job_is_never_dead_lettered_however_often_it_fails(
    engine: Engine,
) -> None:
    """A poll that stops fails silently, so the curve saturates and it stays."""
    policy = ExecutionPolicy(max_attempts=2, base_delay_seconds=1)
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        for _ in range(policy.max_attempts + 5):
            record_poll_failure(
                db,
                checkpoint_id=checkpoint_id,
                error=PollContractError("bad shape"),
                now=NOW,
                policy=policy,
            )
        db.commit()

    with Session(engine) as db:
        # Eligible again once the floor passes — there is no state that could
        # hide it from a selection.
        jobs = due_polling_jobs(
            db, now=NOW + timedelta(seconds=policy.max_backoff_seconds + 1)
        )
    assert [job.checkpoint_id for job in jobs] == [checkpoint_id]


def test_recording_against_an_unknown_checkpoint_is_a_lookup_error(
    engine: Engine,
) -> None:
    with Session(engine) as db, pytest.raises(PollingJobUnknown):
        record_poll_failure(
            db, checkpoint_id=uuid.uuid4(), error=PollContractError("x"), now=NOW
        )
    with Session(engine) as db, pytest.raises(PollingJobUnknown):
        retry_state(db, checkpoint_id=uuid.uuid4())


# ── Recording a success ─────────────────────────────────────────────────────


def test_success_clears_the_run_of_failures_but_not_their_history(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        record_poll_failure(
            db, checkpoint_id=checkpoint_id, error=PollContractError("x"), now=NOW
        )
        record_poll_failure(
            db,
            checkpoint_id=checkpoint_id,
            error=PollContractError("x"),
            now=NOW + timedelta(seconds=1),
        )
        state = record_poll_success(
            db, checkpoint_id=checkpoint_id, now=NOW + timedelta(minutes=5)
        )
        db.commit()

    assert state.attempt_count == 0
    assert state.last_failure_code is None
    assert state.last_failure_at is None
    assert state.last_success_at == NOW + timedelta(minutes=5)
    # The floor is the success itself: eligible now, ordered behind everything
    # polled longer ago. A schedule would have put it in the future.
    assert state.next_attempt_at == NOW + timedelta(minutes=5)
    assert state.failing is False

    with Session(engine) as db:
        history = poll_failure_history(db, checkpoint_id=checkpoint_id)
    assert [row.attempt_number for row in history] == [2, 1]


def test_the_failure_code_and_time_are_always_written_and_cleared_together(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        record_poll_failure(
            db, checkpoint_id=checkpoint_id, error=PollContractError("x"), now=NOW
        )
        db.commit()
    with Session(engine) as db:
        after_failure = retry_state(db, checkpoint_id=checkpoint_id)
        record_poll_success(db, checkpoint_id=checkpoint_id, now=NOW)
        db.commit()
        after_success = retry_state(db, checkpoint_id=checkpoint_id)

    for state in (after_failure, after_success):
        assert (state.last_failure_code is None) == (state.last_failure_at is None)


# ── Retry eligibility ───────────────────────────────────────────────────────


def test_eligibility_is_a_floor_not_an_interval(engine: Engine) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        record_poll_failure(
            db, checkpoint_id=checkpoint_id, error=PollContractError("x"), now=NOW
        )
        db.commit()
    with Session(engine) as db:
        state = retry_state(db, checkpoint_id=checkpoint_id)

    delay = timedelta(seconds=poll_backoff_seconds(1))
    assert is_retry_eligible(state, now=NOW) is False
    assert is_retry_eligible(state, now=NOW + delay - timedelta(seconds=1)) is False
    assert is_retry_eligible(state, now=NOW + delay) is True
    assert is_retry_eligible(state, now=NOW + delay + timedelta(days=365)) is True


# ── Bounded keyset selection ────────────────────────────────────────────────


def test_selection_returns_due_jobs_oldest_floor_first(engine: Engine) -> None:
    _, _, ids = _seed(engine, jobs=("a", "b", "c"))
    with Session(engine) as db:
        jobs = due_polling_jobs(db, now=NOW)
    assert [job.checkpoint_id for job in jobs] == list(ids)
    assert {job.job_key for job in jobs} == {"a", "b", "c"}
    assert all(job.capability_id for job in jobs)
    assert all(job.connector_key for job in jobs)


def test_a_job_under_backoff_is_not_selected_until_its_floor_passes(
    engine: Engine,
) -> None:
    _, _, (first, second) = _seed(engine, jobs=("a", "b"))
    with Session(engine) as db:
        record_poll_failure(
            db, checkpoint_id=first, error=PollContractError("x"), now=NOW
        )
        db.commit()
    with Session(engine) as db:
        assert [job.checkpoint_id for job in due_polling_jobs(db, now=NOW)] == [second]
        later = NOW + timedelta(seconds=poll_backoff_seconds(1))
        assert {job.checkpoint_id for job in due_polling_jobs(db, now=later)} == {
            first,
            second,
        }


def test_a_disabled_binding_or_installation_is_never_selected(
    engine: Engine,
) -> None:
    """Disabling is the ONE way to stop a poll job, and it stops it here."""
    for kwargs in ({"binding_state": "disabled"}, {"installation_state": "disabled"}):
        value = create_engine(
            "sqlite:///:memory:",
            execution_options={"schema_translate_map": {"mod_intg": None}},
        )
        for model in (
            ConnectorInstallation,
            ConnectorConfigRevision,
            CapabilityBinding,
            InboxReceipt,
            PollingCheckpoint,
            PollingAttemptFailure,
        ):
            cast(Any, model.__table__).create(value)
        _seed(value, **cast(Any, kwargs))
        with Session(value) as db:
            assert due_polling_jobs(db, now=NOW) == ()


def test_a_selection_claims_nothing(engine: Engine) -> None:
    """Two workers see the same page; the cursor's version decides the winner."""
    _, _, ids = _seed(engine, jobs=("a", "b"))
    with Session(engine) as db:
        first = due_polling_jobs(db, now=NOW)
        second = due_polling_jobs(db, now=NOW)
        states = [retry_state(db, checkpoint_id=job_id) for job_id in ids]
    assert [job.checkpoint_id for job in first] == [job.checkpoint_id for job in second]
    assert all(state.attempt_count == 0 for state in states)
    assert all(state.last_attempt_at is None for state in states)


def test_an_unbounded_page_is_refused(engine: Engine) -> None:
    with Session(engine) as db:
        for limit in (0, -1, MAX_POLL_PAGE_SIZE + 1):
            with pytest.raises(ValueError, match="bounded page"):
                due_polling_jobs(db, now=NOW, limit=limit)
            with pytest.raises(ValueError, match="bounded page"):
                poll_failure_history(db, checkpoint_id=uuid.uuid4(), limit=limit)


def test_a_keyset_walk_visits_every_job_exactly_once(engine: Engine) -> None:
    jobs = tuple(f"job-{index:02d}" for index in range(9))
    _, _, ids = _seed(engine, jobs=jobs)
    seen: list[uuid.UUID] = []
    after: PollPageKey | None = None
    with Session(engine) as db:
        while True:
            page = due_polling_jobs(db, now=NOW, limit=2, after=after)
            if not page:
                break
            seen.extend(job.checkpoint_id for job in page)
            after = page[-1].page_key
    assert seen == list(ids)
    assert len(set(seen)) == len(ids)


def test_a_job_repolled_mid_walk_drops_out_rather_than_being_offered_twice(
    engine: Engine,
) -> None:
    """The `now` captured for the walk is what makes this deterministic."""
    jobs = tuple(f"job-{index:02d}" for index in range(6))
    _, _, ids = _seed(engine, jobs=jobs)
    seen: list[uuid.UUID] = []
    after: PollPageKey | None = None
    with Session(engine) as db:
        while True:
            page = due_polling_jobs(db, now=NOW, limit=2, after=after)
            if not page:
                break
            for job in page:
                seen.append(job.checkpoint_id)
                # Simulate the worker actually polling it: the floor moves to
                # the success moment, which is after the walk's `now`.
                record_poll_success(
                    db, checkpoint_id=job.checkpoint_id, now=NOW + timedelta(minutes=1)
                )
            after = page[-1].page_key
    assert seen == list(ids)


def test_a_permanently_failing_first_job_cannot_starve_the_ones_behind_it(
    engine: Engine,
) -> None:
    """The oldest checkpoint failing forever must not monopolise the front."""
    jobs = ("stuck", "b", "c", "d")
    _, _, (stuck, *others) = _seed(engine, jobs=jobs)
    with Session(engine) as db:
        # Ten consecutive failures on the OLDEST row.
        for index in range(10):
            record_poll_failure(
                db,
                checkpoint_id=stuck,
                error=PollContractError("always"),
                now=NOW + timedelta(seconds=index),
            )
        db.commit()
    with Session(engine) as db:
        page = due_polling_jobs(db, now=NOW, limit=2)
    assert stuck not in [job.checkpoint_id for job in page]
    assert [job.checkpoint_id for job in page] == others[:2]


def test_failure_history_pages_newest_first_and_keysets_backwards(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        for index in range(7):
            record_poll_failure(
                db,
                checkpoint_id=checkpoint_id,
                error=PollContractError("x"),
                now=NOW + timedelta(seconds=index),
            )
        db.commit()

    numbers: list[int] = []
    after = None
    with Session(engine) as db:
        while True:
            page = poll_failure_history(
                db, checkpoint_id=checkpoint_id, limit=3, after=after
            )
            if not page:
                break
            numbers.extend(row.attempt_number for row in page)
            after = PollFailurePageKey(
                observed_at=page[-1].observed_at, failure_id=page[-1].id
            )
    assert numbers == [7, 6, 5, 4, 3, 2, 1]


def test_failure_retention_requires_product_cutoff_and_prunes_oldest_page(
    engine: Engine,
) -> None:
    _, _, (checkpoint_id,) = _seed(engine)
    with Session(engine) as db:
        for index in range(3):
            record_poll_failure(
                db,
                checkpoint_id=checkpoint_id,
                error=PollContractError("x"),
                now=NOW + timedelta(days=index),
            )
        db.commit()

    with Session(engine) as db:
        deleted = prune_poll_failure_history(
            db,
            older_than=NOW + timedelta(days=2),
            limit=1,
        )
        db.commit()
    assert deleted == 1

    with Session(engine) as db:
        history = poll_failure_history(db, checkpoint_id=checkpoint_id)
    assert [row.attempt_number for row in history] == [3, 2]

    with Session(engine) as db, pytest.raises(ValueError, match="timezone-aware"):
        prune_poll_failure_history(
            db,
            older_than=datetime(2026, 8, 30),
        )


def test_the_page_size_knob_reads_configuration_and_refuses_nonsense() -> None:
    assert resolve_poll_page_size({}) == DEFAULT_POLL_PAGE_SIZE
    assert resolve_poll_page_size({POLL_PAGE_SIZE_VAR: "25"}) == 25
    with pytest.raises(ValueError, match="whole number"):
        resolve_poll_page_size({POLL_PAGE_SIZE_VAR: "many"})
    with pytest.raises(ValueError, match="bounded page"):
        resolve_poll_page_size({POLL_PAGE_SIZE_VAR: str(MAX_POLL_PAGE_SIZE + 1)})


# ── The engine records its own failures ─────────────────────────────────────


def test_poll_once_records_a_connector_failure_and_still_raises(
    engine: Engine,
) -> None:
    registry, _, (checkpoint_id,) = _seed(
        engine, poll_raises=RuntimeError("held-material provider-body")
    )

    with pytest.raises(PollConnectorRaised):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held-material"},
            unit_of_work=_unit_of_work(engine),
        )

    with Session(engine) as db:
        history = poll_failure_history(db, checkpoint_id=checkpoint_id)
        state = retry_state(db, checkpoint_id=checkpoint_id)
    assert [row.failure_code for row in history] == ["connector_raised"]
    assert history[0].connector_exception == "RuntimeError"
    assert state.attempt_count == 1
    assert state.next_attempt_at > state.last_failure_at  # type: ignore[operator]


def test_the_recorded_failure_holds_no_part_of_the_connector_message(
    engine: Engine,
) -> None:
    registry, _, (checkpoint_id,) = _seed(
        engine, poll_raises=RuntimeError("held-material provider-body")
    )
    with pytest.raises(PollConnectorRaised):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held-material"},
            unit_of_work=_unit_of_work(engine),
        )
    columns = [column.name for column in cast(Any, PollingAttemptFailure.__table__).c]
    with Session(engine) as db:
        rows = db.scalars(select(PollingAttemptFailure)).all()
    assert rows, "the failure was not recorded at all"
    rendered = " ".join(str(getattr(row, name)) for row in rows for name in columns)
    assert "held-material" not in rendered
    assert "provider-body" not in rendered


def test_poll_once_records_an_unresolvable_configuration_as_a_poll_failure(
    engine: Engine,
) -> None:
    registry, _, (checkpoint_id,) = _seed(engine)

    def _refuse(refs: Any) -> Any:
        raise RuntimeError("bao unreachable")

    with pytest.raises(PollSecretsUnavailable):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=_refuse,
            unit_of_work=_unit_of_work(engine),
        )
    with Session(engine) as db:
        history = poll_failure_history(db, checkpoint_id=checkpoint_id)
    assert [row.failure_code for row in history] == ["secrets_unavailable"]


def test_a_successful_poll_clears_a_previous_run_of_failures(engine: Engine) -> None:
    registry, plugin, (checkpoint_id,) = _seed(
        engine, poll_raises=RuntimeError("transient")
    )
    with pytest.raises(PollConnectorRaised):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held"},
            unit_of_work=_unit_of_work(engine),
        )
    object.__setattr__(plugin, "poll_raises", None)

    poll_once(
        checkpoint_id=checkpoint_id,
        registry=registry,
        resolve_secrets=lambda refs: {"token": "held"},
        unit_of_work=_unit_of_work(engine),
    )

    with Session(engine) as db:
        state = retry_state(db, checkpoint_id=checkpoint_id)
        history = poll_failure_history(db, checkpoint_id=checkpoint_id)
    assert state.attempt_count == 0
    assert state.last_failure_code is None
    assert state.last_success_at is not None
    # The history survives: the reset describes NOW, not what happened.
    assert len(history) == 1


def test_the_evidence_write_is_best_effort_and_never_replaces_the_real_error(
    engine: Engine,
) -> None:
    """A broken recorder must not rewrite the operator's story of the outage."""
    registry, _, (checkpoint_id,) = _seed(engine, poll_raises=RuntimeError("transient"))
    cast(Any, PollingAttemptFailure.__table__).drop(engine)

    with pytest.raises(PollConnectorRaised):
        poll_once(
            checkpoint_id=checkpoint_id,
            registry=registry,
            resolve_secrets=lambda refs: {"token": "held"},
            unit_of_work=_unit_of_work(engine),
        )

    with Session(engine) as db:
        # Degraded SAFELY: no floor was advanced, so the job stays eligible and
        # the next pass tries again. More polling, never a job that stops.
        assert [job.checkpoint_id for job in due_polling_jobs(db, now=NOW)] == [
            checkpoint_id
        ]


# ── The declaration ─────────────────────────────────────────────────────────


def test_the_new_table_is_declared_on_the_platform_plane(engine: Engine) -> None:
    assert "polling_attempt_failures" in module.platform_tables
    assert module.tables == ()
    columns = {column.name for column in cast(Any, PollingAttemptFailure.__table__).c}
    assert "tenant_id" not in columns


def test_the_default_page_size_is_a_bounded_documented_knob() -> None:
    assert 1 <= DEFAULT_POLL_PAGE_SIZE <= MAX_POLL_PAGE_SIZE
    assert DEFAULT_POLICY.max_backoff_seconds >= DEFAULT_POLICY.base_delay_seconds
