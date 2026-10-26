"""Hosted-only public boundary/negative provenance canaries.

Each process uses the real public runtime module and public strict binding API;
no registry patches, fake authorization flags, mocked SQL, or positive owner
authorization are used. Protected authorization positives belong to the later
identity-provisioning owner. These tests are authored here, not locally executed.
"""

import multiprocessing
import traceback

import pytest


class _Denied(RuntimeError):
    pass


class _Cancelled(BaseException):
    pass


def _watch_close(db, events):
    original_close = db.close

    def close():
        original_close()
        events.append("close")

    db.close = close


def _exercise(name, result):
    # A fresh interpreter owns process binding; the pytest assembly may already
    # have claimed its reference runtime. Never reset/patch that global slot.
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        _protected_platform_operation,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://", hide_parameters=True)
    runtime = DatabaseRuntime(engine=engine)
    calls = []

    def deny(connection):
        calls.append(connection)
        raise _Denied("actual owner authorization is deliberately unavailable")

    def guard(db):
        with _protected_platform_operation(db, deny):
            pytest.fail("a guard-negative case reached the operation body")

    try:
        if name.startswith("ordinary"):
            events = []
            with runtime.platform_session() as db:
                assert not db.in_transaction()  # producer remains lazy
                _watch_close(db, events)
                event.listen(
                    db, "after_commit", lambda session: events.append("commit")
                )
                event.listen(
                    db, "after_rollback", lambda session: events.append("rollback")
                )
                event.listen(
                    db, "after_transaction_end", lambda *args: events.append("end")
                )
                if name == "ordinary_nested":
                    with db.begin_nested():
                        assert db.scalar(text("SELECT 1")) == 1
                else:
                    assert db.scalar(text("SELECT 1")) == 1
            assert events.count("commit") == (2 if name == "ordinary_nested" else 1)
            assert "rollback" not in events
            assert events[-1] == "close"
            assert not db.in_transaction()
        elif name == "manual":
            with Session(engine) as db, pytest.raises(RuntimeBindingError):
                guard(db)
        elif name == "factory":
            with (
                runtime.platform_session_factory() as db,
                pytest.raises(RuntimeBindingError),
            ):
                guard(db)
        elif name == "request":
            producer = runtime.platform_request_session()
            db = next(producer)
            with pytest.raises(RuntimeBindingError):
                guard(db)
            producer.close()
            assert not db.in_transaction()
        else:
            if name == "optional":
                bind_database_runtime(runtime, required=False)
            elif name == "foreign_runtime":
                bind_database_runtime(DatabaseRuntime(engine=engine), required=True)
            elif name != "unbound":
                bind_database_runtime(runtime, required=True)
            expected = {
                "unbound": "strictly bound",
                "optional": "strictly bound",
                "foreign_runtime": "strictly bound",
                "wrong_dialect": "require PostgreSQL",
                "foreign_engine": "engine or session factory changed",
                "factory_change": "engine or session factory changed",
                "replaced_root": "scope has ended or failed",
                "nested": "cannot enter a nested transaction",
            }[name]
            with pytest.raises(RuntimeBindingError), runtime.platform_session() as db:
                if name == "foreign_engine":
                    foreign = create_engine("sqlite://", hide_parameters=True)
                    db.bind = foreign
                elif name == "factory_change":
                    runtime.platform_session_factory.configure(autoflush=True)
                elif name == "replaced_root":
                    db.execute(text("SELECT 1"))
                    db.commit()
                    db.execute(text("SELECT 1"))
                elif name == "nested":
                    db.begin_nested()
                with pytest.raises(RuntimeBindingError, match=expected):
                    guard(db)
                # Catching a guard failure cannot revive final root commit.
                with pytest.raises(RuntimeBindingError):
                    db.commit()
            assert not db.in_transaction()
        assert calls == []
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


def _exception_case(result):
    from dotmac_kernel.session_runtime import DatabaseRuntime
    from sqlalchemy import create_engine, event, text

    engine = create_engine("sqlite://", hide_parameters=True)
    original = _Denied("original instance")
    events = []
    try:
        try:
            with DatabaseRuntime(engine=engine).platform_session() as db:
                event.listen(
                    db, "after_rollback", lambda session: events.append("rollback")
                )
                _watch_close(db, events)
                db.execute(text("SELECT 1"))
                raise original
        except _Denied as caught:
            assert caught is original
        else:
            raise AssertionError("boundary swallowed original exception")
        assert events == ["rollback", "close"]
        assert not db.in_transaction()
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


def _postgres_case(cancelled, url, result):
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        _protected_platform_operation,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine, text

    engine = create_engine(
        url,
        hide_parameters=True,
    )
    runtime = DatabaseRuntime(engine=engine)
    bind_database_runtime(runtime, required=True)
    original = (_Cancelled if cancelled else _Denied)(
        "owner denies; provenance is not authorization"
    )
    try:
        with pytest.raises(RuntimeBindingError), runtime.platform_session() as db:
            # Real already-flushed caller SQL survives protected entry refusal
            # until the runtime rolls the poisoned root back at scope exit.
            db.execute(
                text(
                    "CREATE TEMP TABLE platform_boundary_probe (value integer) ON CO"
                    "MMIT DROP"
                )
            )
            db.execute(text("INSERT INTO platform_boundary_probe VALUES (41)"))

            def deny(connection):
                assert connection is db.connection()
                assert connection.scalar(text("SELECT pg_backend_pid()")) > 0
                raise original

            try:
                with _protected_platform_operation(db, deny):
                    raise AssertionError("owner denial reached protected body")
            except BaseException as caught:
                assert caught is original
            assert db.scalar(text("SELECT value FROM platform_boundary_probe")) == 41
            with pytest.raises(RuntimeBindingError):
                db.commit()
            db.rollback()
            # Root completion permanently retires authority in this same scope.
            with pytest.raises(RuntimeBindingError):
                with _protected_platform_operation(db, deny):
                    raise AssertionError("rollback revived authority")
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


def _core_savepoint_case(action, url, result):
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        _protected_platform_operation,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine

    engine = create_engine(
        url,
        hide_parameters=True,
    )
    runtime = DatabaseRuntime(engine=engine)
    bind_database_runtime(runtime, required=True)
    calls = []

    def unavailable_owner(connection):
        calls.append(connection)
        raise _Denied("owner authorization is unavailable")

    try:
        with (
            pytest.raises(RuntimeBindingError, match="poisoned"),
            runtime.platform_session() as db,
        ):
            connection = db.connection()
            foreign = connection.begin_nested()
            assert foreign.is_active
            with pytest.raises(
                RuntimeBindingError, match="preexisting physical savepoint"
            ):
                with _protected_platform_operation(db, unavailable_owner):
                    raise AssertionError("preexisting Core SAVEPOINT was admitted")
            assert calls == []  # Refusal precedes any authorization callback.
            assert connection.get_nested_transaction() is foreign
            assert foreign.is_active
            with pytest.raises(RuntimeBindingError, match="only the operation"):
                if action == "release":
                    foreign.commit()
                elif action == "rollback":
                    foreign.rollback()
                else:
                    connection.begin_nested()
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


def _core_creation_case(url, result):
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        _protected_platform_operation,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine, text

    engine = create_engine(
        url,
        hide_parameters=True,
    )
    runtime = DatabaseRuntime(engine=engine)
    bind_database_runtime(runtime, required=True)
    refusals = []
    try:
        with (
            pytest.raises(RuntimeBindingError, match="poisoned"),
            runtime.platform_session() as db,
        ):
            connection = db.connection()
            root = connection.get_transaction()
            connection.execute(text("SELECT 1"))

            def denied_core_creation(actual):
                assert actual is connection
                try:
                    actual.begin_nested()
                except RuntimeBindingError as refusal:
                    refusals.append(refusal)
                    raise
                raise AssertionError("validator created an unauthorized Core SAVEPOINT")

            try:
                with _protected_platform_operation(db, denied_core_creation):
                    raise AssertionError(
                        "Core custody violation reached operation body"
                    )
            except RuntimeBindingError as caught:
                assert caught is refusals[0]
            else:
                raise AssertionError("custody violation was swallowed")
            # Damaged custody retires and rolls back the whole actual root;
            # operation-only rollback/preservation is deliberately not claimed.
            assert not root.is_active
            assert connection.get_transaction() is None
            assert not db.in_transaction()
            with pytest.raises(RuntimeBindingError, match="scope has ended or failed"):
                with _protected_platform_operation(db, denied_core_creation):
                    raise AssertionError("damaged custody minted a new root")
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


def _cleanup_case(mode, capture_failure, url, result):
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        _PlatformScope,
        _protected_platform_operation,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine

    engine = create_engine(
        url,
        hide_parameters=True,
    )
    runtime = DatabaseRuntime(engine=engine)
    bind_database_runtime(runtime, required=True)
    original = _Cancelled("original protected cancellation instance")
    events = []
    original_dispose = _PlatformScope.dispose

    def dispose(scope):
        try:
            original_dispose(scope)
        finally:
            events.append("dispose")
        if mode in {"dispose", "both"}:
            raise _Denied("injected disposal failure")

    # Fault injection affects cleanup only; it cannot issue or authorize a scope.
    _PlatformScope.dispose = dispose
    try:
        try:
            with runtime.platform_session() as db:
                original_close = db.close

                def close():
                    try:
                        original_close()
                    finally:
                        events.append("close")
                    if mode in {"close", "both"}:
                        raise _Denied("injected close failure")

                db.close = close
                if capture_failure:

                    def fail_capture(*args, **kwargs):
                        raise original

                    db.connection = fail_capture

                def unavailable_owner(connection):
                    assert connection is db.connection()
                    raise original

                with _protected_platform_operation(db, unavailable_owner):
                    raise AssertionError("denied owner reached protected operation")
        except _Cancelled as caught:
            assert caught is original
        else:
            raise AssertionError("cleanup swallowed the original cancellation")
        assert events == ["close", "dispose"]
        with pytest.raises(RuntimeBindingError, match="not issued"):
            with _protected_platform_operation(db, unavailable_owner):
                raise AssertionError("scope registry survived cleanup")
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        _PlatformScope.dispose = original_dispose
        engine.dispose()


def _run(target, *args):
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(target=target, args=(*args, result))
    process.start()
    process.join(45)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("isolated public-runtime canary timed out")
    assert process.exitcode == 0
    failure = result.get(timeout=5)
    assert failure is None, failure


@pytest.mark.parametrize(
    "name",
    [
        "ordinary",
        "ordinary_nested",
        "manual",
        "factory",
        "request",
        "unbound",
        "optional",
        "foreign_runtime",
        "wrong_dialect",
        "foreign_engine",
        "factory_change",
        "replaced_root",
        "nested",
    ],
)
def test_public_platform_boundary_and_guard_negatives(name):
    _run(_exercise, name)


def test_public_platform_boundary_preserves_original_failure():
    _run(_exception_case)


@pytest.mark.parametrize("cancelled", [False, True])
def test_postgresql_denial_preserves_flushed_work_and_poison_is_sticky(
    cancelled, identity_reference_url
):
    _run(_postgres_case, cancelled, identity_reference_url)


@pytest.mark.parametrize("action", ["release", "rollback", "create"])
def test_preexisting_core_savepoint_refuses_before_owner_validation(
    action, identity_reference_url
):
    _run(_core_savepoint_case, action, identity_reference_url)


@pytest.mark.parametrize("mode", ["close", "dispose", "both"])
@pytest.mark.parametrize("capture_failure", [False, True])
def test_protected_cancellation_identity_survives_cleanup_failures(
    mode, capture_failure, identity_reference_url
):
    _run(_cleanup_case, mode, capture_failure, identity_reference_url)


def test_unauthorized_core_creation_retires_actual_root_without_authorizing_owner(
    identity_reference_url,
):
    _run(_core_creation_case, identity_reference_url)
