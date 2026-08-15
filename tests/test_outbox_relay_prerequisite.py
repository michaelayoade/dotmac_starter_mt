"""The PostgreSQL proofs for `outbox_relay.v1`.

`dotmac_kernel.messaging` claims, leases, retries and dead-letters through
`public.outbox_events` / `public.platform_outbox_events` and their four
`SECURITY DEFINER` functions. Nothing in a consuming lineage's DDL touches any
of it, so — exactly like the at-most-once ledger one release earlier — an
undeclared consumer migrates cleanly and dies on its first claim. ADR-0030 § 4a
makes that concrete: `dotmac-durable-timers` REUSES this relay instead of
shipping a second claim loop, and it cannot declare a dependency on a facility
with no name.

A name is worth nothing unless the claim behind it is checked against the
database, so these run the real verifier against the migrated catalogue and
then break ONE observable at a time, asserting the SPECIFIC refusal for that
observable. "Something raised" is not evidence: a verifier that fails for the
wrong reason is a verifier that will pass for the wrong reason later.

The relay is unusual among the prerequisites in that half its contract is
PRIVILEGE, not shape. A provider can supply both tables, both function pairs
and every index, grant the dispatcher `SELECT` on the outbox, and have handed a
NOBYPASSRLS role every tenant's events while satisfying every name in the
summary. So the hostile cases below are weighted towards posture — grants,
ownership, `search_path`, RLS — and `test_a_has_table_only_verifier_would_miss
_almost_all_of_it` measures exactly how little a name-only check would catch.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Sequence

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

PREREQUISITE = "outbox_relay.v1"

TENANT_CLAIM = "public.claim_outbox_batch(text, integer, integer)"
TENANT_SETTLE = (
    "public.settle_outbox_event(uuid, text, text, timestamptz, integer, text)"
)
PLATFORM_CLAIM = "public.claim_platform_outbox_batch(text, integer, integer)"
PLATFORM_SETTLE = (
    "public.settle_platform_outbox_event"
    "(uuid, text, text, timestamptz, integer, text)"
)


def _admin_url() -> str:
    """The migrating role's URL. Every break below is owner-or-superuser DDL."""
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these proofs need Postgres")
    return url


@contextlib.contextmanager
def _bound_prerequisites() -> Iterator[None]:
    """Install this assembly's bindings, and put back whatever was there.

    Bindings are process state. A test that installs and walks away makes the
    NEXT test's result depend on file order, which is the failure mode a green
    suite hides best.
    """
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


@contextlib.contextmanager
def _broken(admin_url: str, statements: Sequence[str]) -> Iterator[Connection]:
    """Apply one break, hand back the connection, roll it back.

    The break happens in an open transaction on the SAME connection the verifier
    reads, so the damage is visible to the check and invisible to everything
    else — no second migrated database per hostile case, and no repair step that
    could itself be wrong. Role DDL (`DROP ROLE`, `ALTER ROLE`) is transactional
    in PostgreSQL too, which is why the dispatcher-posture cases can live here
    beside the schema ones.

    `statements` is a sequence rather than a string because two breaks are
    genuinely two statements: a role holding grants cannot be dropped until
    `DROP OWNED BY` releases them.
    """
    engine = create_engine(admin_url)
    conn = engine.connect()
    transaction = conn.begin()
    try:
        for statement in statements:
            conn.execute(text(statement))
        yield conn
    finally:
        transaction.rollback()
        conn.close()
        engine.dispose()


# ── Positive: the migrated database satisfies the whole contract ────────────


def test_the_relay_prerequisite_is_satisfied_by_the_migrated_database() -> None:
    """Non-vacuity. A contract nothing can satisfy refuses every install, and
    the kernel's own `0008`/`0011`/`0012` are what a reviewer will compare a
    proposed foreign provider against."""
    from dotmac_kernel.migrations.verify import require_prerequisites

    engine = create_engine(_admin_url())
    with _bound_prerequisites(), engine.connect() as conn:
        require_prerequisites(conn, (PREREQUISITE,))
    engine.dispose()


# ── One specific refusal per observable ─────────────────────────────────────

#: `(id, statements, expected refusal fragment)`. One break each, and the
#: fragment names the OBSERVABLE rather than merely the table, so a check that
#: starts failing earlier for an unrelated reason fails this file instead of
#: quietly passing.
BREAKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # ── The tables exist ────────────────────────────────────────────────────
    (
        "tenant-relay-table-absent",
        ("ALTER TABLE public.outbox_events RENAME TO outbox_events_gone",),
        r"public\.outbox_events does not exist",
    ),
    (
        "platform-relay-table-absent",
        (
            "ALTER TABLE public.platform_outbox_events "
            "RENAME TO platform_outbox_events_gone",
        ),
        r"public\.platform_outbox_events does not exist",
    ),
    # ── The lease / retry / dead-letter columns ─────────────────────────────
    (
        "tenant-lease-holder-absent",
        ("ALTER TABLE public.outbox_events DROP COLUMN leased_by",),
        r"public\.outbox_events columns differ .*'leased_by'",
    ),
    (
        "platform-lease-clock-absent",
        ("ALTER TABLE public.platform_outbox_events DROP COLUMN leased_at",),
        r"public\.platform_outbox_events columns differ .*'leased_at'",
    ),
    (
        "tenant-retry-counter-absent",
        ("ALTER TABLE public.outbox_events DROP COLUMN attempts",),
        r"public\.outbox_events columns differ .*'attempts'",
    ),
    (
        "tenant-dead-letter-state-absent",
        ("ALTER TABLE public.outbox_events DROP COLUMN status",),
        r"public\.outbox_events columns differ .*'status'",
    ),
    (
        "platform-retry-clock-undefaulted",
        (
            "ALTER TABLE public.platform_outbox_events "
            "ALTER COLUMN available_at DROP DEFAULT",
        ),
        r"public\.platform_outbox_events\.available_at has no server default",
    ),
    # ── The indexes the claim predicate depends on ──────────────────────────
    (
        "tenant-claim-index-absent",
        ("DROP INDEX public.ix_outbox_events_status_available_at",),
        r"public\.outbox_events has no index on \('status', 'available_at'\)",
    ),
    (
        "platform-reclaim-index-absent",
        ("DROP INDEX public.ix_platform_outbox_events_status_leased_at",),
        r"public\.platform_outbox_events has no index on " r"\('status', 'leased_at'\)",
    ),
    # ── Plane posture ───────────────────────────────────────────────────────
    (
        "tenant-relay-unforced",
        ("ALTER TABLE public.outbox_events NO FORCE ROW LEVEL SECURITY",),
        r"public\.outbox_events must have FORCEd row-level security",
    ),
    (
        "tenant-relay-policy-dropped",
        ("DROP POLICY outbox_events_tenant_isolation " "ON public.outbox_events",),
        r"public\.outbox_events has FORCEd row-level security and no policy",
    ),
    (
        "tenant-relay-policy-always-passes",
        (
            "ALTER POLICY outbox_events_tenant_isolation "
            "ON public.outbox_events USING (true)",
        ),
        r"do not restrict rows by app_current_tenant_id\(\)",
    ),
    (
        "platform-relay-policied",
        ("ALTER TABLE public.platform_outbox_events ENABLE ROW LEVEL SECURITY",),
        r"public\.platform_outbox_events is the platform plane and must carry "
        r"no row-level security",
    ),
    (
        "platform-relay-readable-by-tenant-role",
        ("GRANT SELECT ON public.platform_outbox_events TO app_user",),
        r"public\.platform_outbox_events is reachable by 'app_user'",
    ),
    # ── The claim/settle functions ──────────────────────────────────────────
    (
        "platform-claim-function-absent",
        (f"DROP FUNCTION {PLATFORM_CLAIM}",),
        r"public\.claim_platform_outbox_batch\(text, integer, integer\) does "
        r"not exist",
    ),
    (
        "tenant-settle-function-security-invoker",
        (f"ALTER FUNCTION {TENANT_SETTLE} SECURITY INVOKER",),
        r"public\.settle_outbox_event\(.*\) is not SECURITY DEFINER",
    ),
    (
        "tenant-claim-function-search-path-reset",
        (f"ALTER FUNCTION {TENANT_CLAIM} RESET search_path",),
        r"public\.claim_outbox_batch\(.*\) is SECURITY DEFINER without an empty "
        r"search_path",
    ),
    (
        "platform-settle-function-search-path-mutable",
        (f"ALTER FUNCTION {PLATFORM_SETTLE} SET search_path TO public",),
        r"public\.settle_platform_outbox_event\(.*\) is SECURITY DEFINER "
        r"without an empty search_path",
    ),
    (
        "tenant-claim-function-reowned",
        (f"ALTER FUNCTION {TENANT_CLAIM} OWNER TO CURRENT_USER",),
        r"is SECURITY DEFINER owned by .*, expected 'app_admin'",
    ),
    # ── Dispatcher privileges ───────────────────────────────────────────────
    (
        "tenant-dispatcher-role-absent",
        (
            "DROP OWNED BY outbox_dispatcher",
            "DROP ROLE outbox_dispatcher",
        ),
        r"database role 'outbox_dispatcher' does not exist",
    ),
    (
        "tenant-dispatcher-bypasses-rls",
        ("ALTER ROLE outbox_dispatcher BYPASSRLS",),
        r"role 'outbox_dispatcher' has rolbypassrls=True",
    ),
    (
        "tenant-dispatcher-holds-table-privilege",
        ("GRANT SELECT ON public.outbox_events TO outbox_dispatcher",),
        r"role 'outbox_dispatcher' holds table or column privilege on "
        r"public\.outbox_events",
    ),
    (
        "tenant-dispatcher-holds-column-privilege",
        ("GRANT SELECT (payload) ON public.outbox_events " "TO outbox_dispatcher",),
        r"role 'outbox_dispatcher' holds table or column privilege on "
        r"public\.outbox_events",
    ),
    (
        "platform-dispatcher-execute-revoked",
        (
            f"REVOKE EXECUTE ON FUNCTION {PLATFORM_SETTLE} "
            "FROM platform_outbox_dispatcher",
        ),
        r"role 'platform_outbox_dispatcher' cannot EXECUTE "
        r"public\.settle_platform_outbox_event",
    ),
    (
        "tenant-execute-granted-to-public",
        (f"GRANT EXECUTE ON FUNCTION {TENANT_CLAIM} TO PUBLIC",),
        r"EXECUTE on public\.claim_outbox_batch\(.*\) is granted to PUBLIC",
    ),
)

#: Everything a `has_table`-only verifier — the shape this file exists to argue
#: against — would let through: 23 of the 25 breaks. It reads two names, so it
#: catches the two cases that remove a name and nothing else.
#:
#: Written out as a literal rather than derived from `BREAKS` by subtracting the
#: two it catches. A derived set makes the assertion below a tautology, and a
#: tautology is exactly the class of test that keeps passing after its guard is
#: deleted.
WEAK_VERIFIER_MISSES: frozenset[str] = frozenset(
    {
        "tenant-lease-holder-absent",
        "platform-lease-clock-absent",
        "tenant-retry-counter-absent",
        "tenant-dead-letter-state-absent",
        "platform-retry-clock-undefaulted",
        "tenant-claim-index-absent",
        "platform-reclaim-index-absent",
        "tenant-relay-unforced",
        "tenant-relay-policy-dropped",
        "tenant-relay-policy-always-passes",
        "platform-relay-policied",
        "platform-relay-readable-by-tenant-role",
        "platform-claim-function-absent",
        "tenant-settle-function-security-invoker",
        "tenant-claim-function-search-path-reset",
        "platform-settle-function-search-path-mutable",
        "tenant-claim-function-reowned",
        "tenant-dispatcher-role-absent",
        "tenant-dispatcher-bypasses-rls",
        "tenant-dispatcher-holds-table-privilege",
        "tenant-dispatcher-holds-column-privilege",
        "platform-dispatcher-execute-revoked",
        "tenant-execute-granted-to-public",
    }
)


@pytest.mark.parametrize(
    ("statements", "expected"),
    [pytest.param(s, e, id=i) for i, s, e in BREAKS],
)
def test_the_relay_prerequisite_refuses_a_provider_missing_one_effect(
    statements: tuple[str, ...], expected: str
) -> None:
    """The sensitivity proof for every clause of the summary.

    Each case asserts the refusal text for THAT observable. Half of them break
    nothing about the schema at all — a grant, an owner, a `search_path`, a
    role attribute — because on this prerequisite those are the contract, and a
    provider can get every table and column right while giving the whole outbox
    away.
    """
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        verify_outbox_relay,
    )

    with _bound_prerequisites(), _broken(_admin_url(), statements) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            verify_outbox_relay(conn)


# ── The weak-verifier companion ─────────────────────────────────────────────


def _has_table_only_verifier(bind: Connection) -> None:
    """The verifier this one is NOT: two `has_table` calls and a shrug.

    Written out rather than described, so the claim in the changelog is
    measured against real behaviour instead of asserted in prose.
    """
    import sqlalchemy as sa
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    inspector = sa.inspect(bind)
    for table in ("outbox_events", "platform_outbox_events"):
        if not inspector.has_table(table, schema="public"):
            raise PrerequisiteNotSatisfiedError(f"public.{table} does not exist")


def test_a_has_table_only_verifier_would_miss_almost_all_of_it() -> None:
    """The companion that makes the sensitivity claim true BY CONSTRUCTION.

    Two assertions, and they fail in opposite directions:

    - `escaped_the_real_verifier` must be empty. If someone weakens
      `verify_outbox_relay` — drops the ownership check, stops reading
      `proconfig`, trusts `has_table_privilege` alone — a break stops being
      refused and this fails. That is the property the parametrized cases above
      cannot give on their own: they say each check works, this says no check
      may leave.
    - `escaped_the_weak_verifier` must be exactly `WEAK_VERIFIER_MISSES`. If a
      break stops breaking (a renamed index, a migration that no longer grants
      what it grants), the weak verifier's miss set shifts and this fails
      instead of the count quietly re-balancing.
    """
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        verify_outbox_relay,
    )

    admin_url = _admin_url()
    escaped_the_weak_verifier: set[str] = set()
    escaped_the_real_verifier: set[str] = set()

    with _bound_prerequisites():
        for identifier, statements, _ in BREAKS:
            with _broken(admin_url, statements) as conn:
                try:
                    _has_table_only_verifier(conn)
                except PrerequisiteNotSatisfiedError:
                    pass
                else:
                    escaped_the_weak_verifier.add(identifier)
                try:
                    verify_outbox_relay(conn)
                except PrerequisiteNotSatisfiedError:
                    pass
                else:
                    escaped_the_real_verifier.add(identifier)

    assert escaped_the_real_verifier == set(), (
        f"verify_outbox_relay accepted a database missing "
        f"{sorted(escaped_the_real_verifier)} — every case in BREAKS removes "
        "one clause of the published summary, so accepting one means the "
        "verifier no longer checks what the spec promises"
    )
    assert escaped_the_weak_verifier == WEAK_VERIFIER_MISSES, (
        "the has_table-only miss set moved: expected "
        f"{sorted(WEAK_VERIFIER_MISSES)}, observed "
        f"{sorted(escaped_the_weak_verifier)}"
    )
