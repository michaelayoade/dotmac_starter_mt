"""Genuine runtime binding/provenance negatives; hosted only, NOT RUN.

Actual Academy positive/extension/cohort/precommit cases live with the real CLI
consumer. These tests never install callback-True or synthetic table authority.
"""

import json
import multiprocessing
import os
import traceback
from queue import Empty

import pytest


def _case(name, result):
    from dotmac_kernel.identity_provisioning import (
        BootstrapTenantV1,
        IdentityProvisioningAssemblyV1,
        bootstrap_tenant,
        install_identity_provisioning,
    )
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    class UnavailableProduct:
        def open(self, connection, command):
            raise AssertionError("invalid provenance reached product authorization")

    engine = create_engine("sqlite://", hide_parameters=True)
    runtime = DatabaseRuntime(engine=engine)
    assembly = IdentityProvisioningAssemblyV1(runtime, "canary", UnavailableProduct())
    try:
        if name == "unbound":
            with pytest.raises(RuntimeBindingError):
                install_identity_provisioning(assembly)
        elif name == "optional":
            bind_database_runtime(runtime)
            with pytest.raises(RuntimeBindingError):
                install_identity_provisioning(assembly)
        else:
            bind_database_runtime(runtime, required=True)
            install_identity_provisioning(assembly)
            install_identity_provisioning(assembly)
            if name == "replacement":
                with pytest.raises(RuntimeBindingError):
                    install_identity_provisioning(
                        IdentityProvisioningAssemblyV1(
                            runtime, "canary", assembly.product
                        )
                    )
            elif name == "manual":
                with Session(engine) as db, pytest.raises(RuntimeBindingError):
                    bootstrap_tenant(
                        db,
                        BootstrapTenantV1("test", "Test", "owner@example.test"),
                        password="call-local",
                    )
            elif name == "factory":
                with (
                    runtime.platform_session_factory() as db,
                    pytest.raises(RuntimeBindingError),
                ):
                    bootstrap_tenant(
                        db,
                        BootstrapTenantV1("test", "Test", "owner@example.test"),
                        password="call-local",
                    )
            elif name == "foreign":
                with (
                    pytest.raises(RuntimeBindingError),
                    DatabaseRuntime(engine=engine).platform_session() as db,
                ):
                    with pytest.raises(RuntimeBindingError):
                        bootstrap_tenant(
                            db,
                            BootstrapTenantV1("test", "Test", "owner@example.test"),
                            password="call-local",
                        )
            elif name == "request":
                producer = runtime.platform_request_session()
                db = next(producer)
                try:
                    with pytest.raises(RuntimeBindingError):
                        bootstrap_tenant(
                            db,
                            BootstrapTenantV1("test", "Test", "owner@example.test"),
                            password="call-local",
                        )
                finally:
                    producer.close()
            else:
                with (
                    pytest.raises(RuntimeBindingError),
                    runtime.platform_session() as db,
                ):
                    with pytest.raises(RuntimeBindingError):
                        bootstrap_tenant(
                            db,
                            BootstrapTenantV1("test", "Test", "owner@example.test"),
                            password="call-local",
                        )
        result.put(None)
    except BaseException:
        result.put(traceback.format_exc())
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "name",
    [
        "unbound",
        "optional",
        "replacement",
        "manual",
        "factory",
        "foreign",
        "request",
        "dialect",
    ],
)
def test_owner_refuses_invalid_binding_and_actual_provenance(name):
    assert os.environ.get("CI") == "true", "hosted only"
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(target=_case, args=(name, result))
    process.start()
    process.join(45)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("bootstrap provenance canary timed out")
    assert process.exitcode == 0
    assert result.get(timeout=5) is None


class _Cancelled(BaseException):
    pass


def _safe_exception_stage(error):
    """Only bounded class/frame facts, including suppressed exception context."""
    pending = [(error, "root", None)]
    visited = set()
    exceptions = []
    while pending and len(exceptions) < 12:
        current, edge, parent = pending.pop(0)
        if id(current) in visited:
            continue
        visited.add(id(current))
        frames = []
        trace = current.__traceback__
        while trace is not None and len(frames) < 8:
            code = trace.tb_frame.f_code
            frames.append(
                {
                    "file": os.path.basename(code.co_filename)[:120],
                    "function": code.co_name[:120],
                    "line": trace.tb_lineno,
                }
            )
            trace = trace.tb_next
        index = len(exceptions)
        exceptions.append(
            {
                "class": type(current).__name__[:120],
                "edge": edge,
                "parent": parent,
                "frames": frames,
                "frames_truncated": trace is not None,
            }
        )
        for relation, linked in (
            ("cause", current.__cause__),
            ("context", current.__context__),
        ):
            if linked is not None and id(linked) not in visited:
                pending.append((linked, relation, index))
    return {"exceptions": exceptions, "truncated": bool(pending)}


def test_safe_exception_stage_excludes_material_and_bounds_chains():
    assert os.environ.get("CI") == "true", "hosted only"
    sentinel = "public-synthetic-stage-privacy-sentinel"

    class PoisonedStageError(Exception):
        def __str__(self):
            raise AssertionError("stage helper called exception str")

        def __repr__(self):
            raise AssertionError("stage helper called exception repr")

    def raise_synthetic(depth):
        private_local = sentinel
        if depth:
            raise_synthetic(depth - 1)
        raise PoisonedStageError(private_local)

    try:
        raise_synthetic(12)
    except PoisonedStageError as caught:
        suppressed = LookupError(sentinel)
        caught.__context__ = suppressed
        caught.__suppress_context__ = True
        suppressed.__cause__ = caught  # Cycle must not revisit the root.
        chain = caught
        for _ in range(20):
            linked = PoisonedStageError(sentinel)
            chain.__cause__ = linked
            chain = linked
        stage = _safe_exception_stage(caught)
    encoded = json.dumps(stage)
    assert sentinel not in encoded
    assert len(encoded) < 40000
    assert stage["truncated"] is True
    assert len(stage["exceptions"]) == 12
    assert stage["exceptions"][0]["class"] == "PoisonedStageError"
    assert stage["exceptions"][0]["frames_truncated"] is True
    assert any(
        item["class"] == "LookupError" and item["edge"] == "context"
        for item in stage["exceptions"]
    )
    assert sum(item["edge"] == "root" for item in stage["exceptions"]) == 1
    assert all(len(item["frames"]) <= 8 for item in stage["exceptions"])
    assert stage["exceptions"][0]["frames"][0]["function"] == (
        "test_safe_exception_stage_excludes_material_and_bounds_chains"
    )
    assert all(
        set(frame) == {"file", "function", "line"}
        and "/" not in frame["file"]
        and isinstance(frame["line"], int)
        for item in stage["exceptions"]
        for frame in item["frames"]
    )


def _postgres_denial(mode, url, result):
    from types import SimpleNamespace

    from dotmac_kernel.identity_provisioning import (
        BootstrapPolicyV1,
        BootstrapRoleV1,
        BootstrapTenantV1,
        IdentityProvisioningAssemblyV1,
        bootstrap_tenant,
        install_identity_provisioning,
    )
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine, text

    engine = create_engine(
        url,
        hide_parameters=True,
        echo=False,
    )
    runtime = DatabaseRuntime(engine=engine)
    original = _Cancelled("original cancellation instance")
    policy = BootstrapPolicyV1(
        (("nonexistent-canary-login", "nonexistent-canary-effective"),),
        (BootstrapRoleV1("custom", "Custom"),),
        "custom",
        True,
    )
    product_opened = False
    caught_meta = None

    class UnavailableProduct:
        def open(self, connection, command):
            nonlocal product_opened
            product_opened = True
            assert connection.dialect.name == "postgresql"
            if mode == "cancel":
                raise original
            if mode == "damaged":
                connection.begin_nested()
                raise AssertionError(
                    "custody guard admitted foreign physical savepoint"
                )
            # This intentionally mismatched pair must deny before canonical or
            # extension effects; this incomplete product cannot authorize a body.
            return SimpleNamespace(policy=policy)

    try:
        bind_database_runtime(runtime, required=True)
        install_identity_provisioning(
            IdentityProvisioningAssemblyV1(
                runtime, "kernel-canary", UnavailableProduct()
            )
        )
        with pytest.raises(RuntimeBindingError), runtime.platform_session() as db:
            db.execute(
                text(
                    "CREATE TEMP TABLE bootstrap_prior_work(value integer) ON COMMIT"
                    " DROP"
                )
            )
            db.execute(text("INSERT INTO bootstrap_prior_work VALUES (41)"))
            connection = db.connection()
            root = connection.get_transaction()
            try:
                bootstrap_tenant(
                    db,
                    BootstrapTenantV1(
                        "never-created", "Never created", "owner@example.test"
                    ),
                    password="call-local",
                )
            except BaseException as caught:
                caught_meta = _safe_exception_stage(caught)
                if mode == "cancel":
                    assert caught is original
                elif mode == "damaged":
                    assert isinstance(caught, RuntimeBindingError)
                else:
                    assert isinstance(caught, ValueError)
            else:
                raise AssertionError("owner admitted unavailable product authority")
            assert product_opened is True
            if mode == "damaged":
                assert not root.is_active
                assert not db.in_transaction()
            else:
                assert root.is_active
                assert db.scalar(text("SELECT value FROM bootstrap_prior_work")) == 41
            with pytest.raises(RuntimeBindingError):
                db.commit()
        result.put(None)
    except BaseException as failure:
        stage = {
            "product_opened": product_opened,
            "caught": caught_meta,
            "outer": _safe_exception_stage(failure),
        }
        print("DOTMAC_IDENTITY_TEST_STAGE_V1:" + json.dumps(stage), flush=True)
        result.put(stage)
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["pair", "cancel", "damaged"])
def test_actual_postgresql_owner_denial_cancellation_and_custody(
    mode, identity_reference_url
):
    assert os.environ.get("CI") == "true", "hosted only"
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(
        target=_postgres_denial, args=(mode, identity_reference_url, result)
    )
    process.start()
    try:
        outcome = result.get(timeout=45)
    except Empty:
        pytest.fail("PostgreSQL bootstrap denial canary timed out", pytrace=False)
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail("PostgreSQL bootstrap denial canary timed out")
    assert process.exitcode == 0
    assert outcome is None


@pytest.fixture
def bootstrap_catalog_connection(identity_reference_engine):
    """Real app_admin-owned reference catalog; all mutations roll back."""
    with (
        identity_reference_engine.connect() as connection,
        connection.begin() as transaction,
    ):
        try:
            yield connection
        finally:
            transaction.rollback()


def test_catalog_accepts_actual_canonical_policy_dependencies(
    bootstrap_catalog_connection,
):
    from dotmac_kernel.identity_provisioning import _catalog, _lookup_floor
    from sqlalchemy import text

    connection = bootstrap_catalog_connection
    connection.execute(text("SET LOCAL search_path TO pg_catalog, public"))
    _lookup_floor(connection)
    facts = _catalog(connection)
    assert len(facts) == 8
    # Includes the actual party_persons column bindings, without inventing a
    # redundant NORMAL dependency on the whole parties relation.
    assert all(facts[index][-1] for index in range(2, 8))


@pytest.mark.parametrize("shadow", ["helper", "operator"])
def test_catalog_refuses_noncanonical_policy_bindings(
    bootstrap_catalog_connection, shadow
):
    from dotmac_kernel.identity_provisioning import _catalog, _lookup_floor
    from sqlalchemy import text

    connection = bootstrap_catalog_connection
    # Public policy changes serialize behind a relation lock and roll back.
    connection.execute(text("LOCK TABLE public.parties IN ACCESS EXCLUSIVE MODE"))
    connection.execute(text("CREATE SCHEMA bootstrap_binding_shadow"))
    connection.execute(
        text("SET LOCAL search_path TO pg_catalog, bootstrap_binding_shadow, public")
    )
    _lookup_floor(connection)
    policy = connection.scalar(
        text(
            "SELECT polname FROM pg_catalog.pg_policy WHERE polrelid='public.parties"
            "'::pg_catalog.regclass"
        )
    )
    assert policy
    quoted = connection.dialect.identifier_preparer.quote(policy)
    if shadow == "helper":
        connection.execute(
            text(
                "CREATE FUNCTION bootstrap_binding_shadow.app_current_tenant_id() RE"
                "TURNS pg_catalog.uuid "
                "LANGUAGE sql STABLE AS 'SELECT NULL::pg_catalog.uuid'"
            )
        )
        expression = "tenant_id=bootstrap_binding_shadow.app_current_tenant_id()"
    else:
        connection.execute(
            text(
                "CREATE OPERATOR bootstrap_binding_shadow.= "
                "(LEFTARG=pg_catalog.uuid,RIGHTARG=pg_catalog.uuid,FUNCTION=pg_catal"
                "og.uuid_eq)"
            )
        )
        expression = (
            "tenant_id OPERATOR(bootstrap_binding_shadow.=) public.app_curr"
            "ent_tenant_id()"
        )
    connection.execute(
        text(
            f"ALTER POLICY {quoted} ON public.parties USING ({expression}) "
            # Preserve the exact SQL while keeping its literal readable.
            f"WITH CHECK ({expression})"
        )
    )
    dependency_class = "pg_proc" if shadow == "helper" else "pg_operator"
    assert connection.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d "
            "JOIN pg_catalog.pg_policy p ON d.objid=p.oid "
            "WHERE d.classid='pg_catalog.pg_policy'::pg_catalog.regclass "
            "AND p.polrelid='public.parties'::pg_catalog.regclass "
            "AND d.refclassid=pg_catalog.to_regclass(:dependency))"
        ),
        {"dependency": "pg_catalog." + dependency_class},
    )
    with pytest.raises(ValueError, match="offline bootstrap"):
        _catalog(connection)


@pytest.mark.parametrize(
    "path", ['"$user", public', "pg_catalog, bootstrap_lookup_shadow, public"]
)
def test_lookup_floor_accepts_supported_paths(bootstrap_catalog_connection, path):
    from dotmac_kernel.identity_provisioning import _lookup_floor
    from sqlalchemy import text

    connection = bootstrap_catalog_connection
    connection.execute(text("CREATE SCHEMA bootstrap_lookup_shadow"))
    connection.execute(text("SET LOCAL search_path TO " + path))
    _lookup_floor(connection)
    assert (
        connection.scalar(text("SELECT pg_catalog.to_regtype('uuid')::pg_catalog.oid"))
        == 2950
    )


@pytest.mark.parametrize("kind", ["schema", "uuid", "text"])
def test_lookup_floor_refuses_shadowing(bootstrap_catalog_connection, kind):
    from dotmac_kernel.identity_provisioning import _lookup_floor
    from sqlalchemy import text

    connection = bootstrap_catalog_connection
    if kind == "schema":
        connection.execute(text("CREATE SCHEMA bootstrap_lookup_shadow"))
        connection.execute(
            text("SET LOCAL search_path TO bootstrap_lookup_shadow, pg_catalog, public")
        )
    else:
        connection.execute(
            text("CREATE TEMP TABLE bootstrap_temp_namespace(value pg_catalog.int4)")
        )
        connection.execute(text(f"CREATE DOMAIN pg_temp.{kind} AS pg_catalog.int4"))
        connection.execute(text("SET LOCAL search_path TO pg_catalog, public"))
    with pytest.raises(ValueError, match="offline bootstrap"):
        _lookup_floor(connection)


@pytest.mark.parametrize("unsafe", ["parameters", "echo", "debug"])
def test_logging_guard_refuses_before_material_sql(
    bootstrap_catalog_connection, unsafe
):
    import logging
    from types import SimpleNamespace

    from dotmac_kernel.identity_provisioning import BootstrapTenantV1, _Bootstrap
    from sqlalchemy import event

    connection = bootstrap_catalog_connection
    engine = connection.engine
    previous = (engine.hide_parameters, engine.echo, engine.logger)
    counters = {"sql": 0, "product": 0}

    def count_sql(conn, cursor, statement, parameters, context, executemany):
        # Count only; never capture SQL parameters, result rows or material.
        counters["sql"] += 1

    class UnavailableProduct:
        def open(self, conn, command):
            counters["product"] += 1
            raise AssertionError("unsafe logging reached product")

    state = _Bootstrap(
        SimpleNamespace(product=UnavailableProduct()),
        BootstrapTenantV1("guard", "Guard", "a@b.test"),
        None,
    )
    event.listen(engine, "before_cursor_execute", count_sql)
    try:
        if unsafe == "parameters":
            engine.hide_parameters = False
        elif unsafe == "echo":
            engine.echo = True
        else:
            # Private logger object only; never mutate process/global logging.
            engine.logger = logging.Logger(
                "bootstrap-private-canary", level=logging.DEBUG
            )
        with pytest.raises(ValueError, match="offline bootstrap"):
            state.revalidate(connection)
        with pytest.raises(ValueError, match="offline bootstrap"):
            state.credential(connection)
        assert counters == {"sql": 0, "product": 0}
    finally:
        engine.hide_parameters, engine.echo, engine.logger = previous
        event.remove(engine, "before_cursor_execute", count_sql)


def test_from_urls_forwards_parameter_hiding_to_both_real_engines(
    identity_reference_url,
):
    from dotmac_kernel.session_runtime import DatabaseRuntime

    assert os.environ.get("CI") == "true", "hosted only"
    url = identity_reference_url
    runtime = DatabaseRuntime.from_urls(
        database_url=url, platform_database_url=url, hide_parameters=True
    )
    try:
        assert runtime.engine.hide_parameters is True
        assert runtime.platform_engine.hide_parameters is True
        assert runtime.engine.echo is False
        assert runtime.platform_engine.echo is False
    finally:
        runtime.engine.dispose()
        runtime.platform_engine.dispose()


def test_lookup_floor_accepts_own_actual_temporary_namespace(
    bootstrap_catalog_connection,
):
    from dotmac_kernel.identity_provisioning import _lookup_floor
    from sqlalchemy import text

    connection = bootstrap_catalog_connection
    connection.execute(
        text("CREATE TEMP TABLE bootstrap_temp_namespace(value pg_catalog.int4)")
    )
    connection.execute(text("SET LOCAL search_path TO pg_catalog, public"))
    assert connection.scalar(text("SELECT pg_catalog.pg_my_temp_schema()"))
    _lookup_floor(connection)
