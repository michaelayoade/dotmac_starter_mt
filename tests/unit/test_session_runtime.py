"""`DatabaseRuntime` — the configurable parts, without a Postgres.

Scope note (repo testing model): this file covers what is decidable without a
database — the declaration grammar, the composed scope statement, and the
listener lifecycle. Whether the scope actually ISOLATES rows is a tenancy
question and lives in the Postgres canaries (`tests/test_tenant_session_scope
.py`), because RLS is the thing being tested there and SQLite has none.

That split matters here more than usual: every failure mode this runtime exists
to prevent is SILENT under RLS. A unit test that "passed" against SQLite would
be asserting that no error was raised, which is precisely what a leaked scope
also looks like.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.session_runtime import (
    CANONICAL_TENANT_SETTING,
    DatabaseRuntime,
)
from sqlalchemy import create_engine


@pytest.fixture
def sqlite_runtime() -> DatabaseRuntime:
    """A runtime over caller-supplied engines — the `__init__` path a product
    uses when it has already built its own (pool sizing, credentials, a
    dialect the kernel never named)."""
    engine = create_engine("sqlite://")
    return DatabaseRuntime(engine=engine)


# ── the declaration grammar ─────────────────────────────────────────────────


def test_the_canonical_setting_is_always_primed_and_always_first(
    sqlite_runtime: DatabaseRuntime,
) -> None:
    assert sqlite_runtime.tenant_settings == (CANONICAL_TENANT_SETTING,)
    assert sqlite_runtime.legacy_tenant_settings == ()


def test_legacy_settings_are_primed_alongside_the_canonical_one() -> None:
    runtime = DatabaseRuntime(
        engine=create_engine("sqlite://"),
        legacy_tenant_settings=("app.current_organization_id",),
    )
    # Order is part of the contract: the canonical name is never displaced by
    # a legacy one, so a product cannot accidentally reorder itself into
    # priming only its own setting.
    assert runtime.tenant_settings == (
        CANONICAL_TENANT_SETTING,
        "app.current_organization_id",
    )
    assert runtime.legacy_tenant_settings == ("app.current_organization_id",)


def test_declaring_the_canonical_setting_as_legacy_is_refused() -> None:
    """It is primed unconditionally, so listing it would write it twice — and,
    worse, would read as though priming it were optional."""
    with pytest.raises(ValueError, match="already"):
        DatabaseRuntime(
            engine=create_engine("sqlite://"),
            legacy_tenant_settings=(CANONICAL_TENANT_SETTING,),
        )


@pytest.mark.parametrize(
    "name",
    [
        "nodot",
        "app.Current_Tenant",  # set names are lowercased by Postgres
        "app.tenant; DROP TABLE parties--",
        'app."quoted"',
        "app.",
        ".current_tenant",
        "app.current tenant",
    ],
)
def test_a_setting_name_outside_the_plain_grammar_is_refused(name: str) -> None:
    """Names are INTERPOLATED into SQL, because `set_config` cannot bind its
    first argument. That makes the grammar a boundary, not a style rule, so it
    is checked at construction rather than at first use — a deployment learns
    it declared something unusable at startup, not on the first scoped query."""
    with pytest.raises(ValueError, match="valid Postgres setting name"):
        DatabaseRuntime(
            engine=create_engine("sqlite://"), legacy_tenant_settings=(name,)
        )


def test_a_duplicated_legacy_setting_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        DatabaseRuntime(
            engine=create_engine("sqlite://"),
            legacy_tenant_settings=("app.org", "app.org"),
        )


# ── the composed statement ──────────────────────────────────────────────────


def test_every_declared_setting_is_primed_in_one_statement() -> None:
    """One statement, not one per name.

    A failure between two separate statements would leave the canonical
    setting armed and a legacy one stale — which does not look like an error,
    it looks like a working scope over the wrong rows.
    """
    runtime = DatabaseRuntime(
        engine=create_engine("sqlite://"),
        legacy_tenant_settings=("app.current_organization_id",),
    )
    sql = str(runtime._scope_sql)
    assert sql.count("set_config") == 2
    assert sql.startswith("SELECT ")
    assert ";" not in sql
    for name in runtime.tenant_settings:
        assert f"set_config('{name}', :tenant_id, :is_local)" in sql


def test_the_platform_engine_defaults_to_the_tenant_engine() -> None:
    """A deployment that has not split the credential still gets a working
    platform boundary, rather than a second engine it did not ask for."""
    engine = create_engine("sqlite://")
    runtime = DatabaseRuntime(engine=engine)
    assert runtime.platform_engine is engine
    assert runtime.session_factory is not runtime.platform_session_factory


def test_a_separate_platform_engine_is_kept_separate() -> None:
    engine = create_engine("sqlite://")
    platform = create_engine("sqlite://")
    runtime = DatabaseRuntime(engine=engine, platform_engine=platform)
    assert runtime.engine is engine
    assert runtime.platform_engine is platform


# ── the listener lifecycle ──────────────────────────────────────────────────


def test_tenant_scope_installs_and_removes_its_re_arm_listener(
    sqlite_runtime: DatabaseRuntime,
) -> None:
    """The listener is what makes a scope survive a commit; removing it is what
    stops the scope outliving the block.

    A listener left behind would keep re-arming a session the caller has since
    handed elsewhere — the scope equivalent of a leaked connection setting, and
    invisible until two tenants share one long-lived session.
    """
    db = sqlite_runtime.session_factory()
    try:
        before = len(db.dispatch.after_begin)
        with sqlite_runtime.tenant_scope(db, "11111111-1111-1111-1111-111111111111"):
            assert len(db.dispatch.after_begin) == before + 1
        assert len(db.dispatch.after_begin) == before
    finally:
        db.close()


def test_the_listener_is_removed_even_when_the_block_raises(
    sqlite_runtime: DatabaseRuntime,
) -> None:
    db = sqlite_runtime.session_factory()
    try:
        before = len(db.dispatch.after_begin)
        tenant = "22222222-2222-2222-2222-222222222222"
        with pytest.raises(RuntimeError):
            with sqlite_runtime.tenant_scope(db, tenant):
                raise RuntimeError("boom")
        assert len(db.dispatch.after_begin) == before
    finally:
        db.close()


def test_a_fresh_session_is_not_primed_before_it_has_a_transaction(
    sqlite_runtime: DatabaseRuntime,
) -> None:
    """`tenant_scope` primes an ALREADY-OPEN transaction immediately and leaves
    the rest to the listener.

    Both halves are needed and neither is redundant: the listener cannot reach
    a transaction that began before it was installed (which is
    `tenant_session_by_slug`'s case, where resolving the tenant is what opened
    it), and the immediate prime cannot reach transactions that do not exist
    yet. Asserting the fresh-session case pins that the immediate prime is
    guarded rather than unconditional — an unconditional one would open a
    transaction here purely as a side effect of scoping.
    """
    db = sqlite_runtime.session_factory()
    try:
        assert not db.in_transaction()
        with sqlite_runtime.tenant_scope(db, "33333333-3333-3333-3333-333333333333"):
            # Still no transaction: scoping did not start one, so nothing was
            # sent to the database and there is nothing to roll back.
            assert not db.in_transaction()
    finally:
        db.close()
