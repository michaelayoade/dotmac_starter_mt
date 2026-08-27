"""The rehearsal must wait for readiness that can REGRESS, not just arrive.

Postgres' official entrypoint, on a fresh volume, starts a TEMPORARY server to
run initdb and any `/docker-entrypoint-initdb.d` scripts, then shuts it down
and starts the real one. A probe that fires during the temporary phase
succeeds, and the socket disappears underneath whatever runs next.

That is not a hypothesis. Rehearsal run 33088709577 passed `pg_isready` and the
very next `psql` failed with:

    connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed:
    No such file or directory

Two changes close it, and the ORDER of importance is the opposite of the order
they were written in:

1. **Loopback TCP, not the Unix socket.** The official image starts the
   temporary server with `listen_addresses=''`, so it accepts NO TCP at all.
   A loopback TCP probe therefore CANNOT be satisfied by it — the probe
   succeeds only once the real server is listening. That is what removes the
   race. A socket probe cannot distinguish the two servers and can be
   satisfied by the temporary one for longer than any streak is willing to
   wait.
2. **A consecutive-success streak**, as defence in depth behind it, for the
   restarts TCP alone does not describe: a crashed backend, a container the
   daemon restarts.

`wait_for` accepts the FIRST success, which is correct for something that only
ever becomes ready — a listening port, a pushed image — and wrong for anything
whose readiness can go backwards.

These are static checks on a shell script the unit suite cannot execute. They
are cheap and they encode a lesson that cost a 20-minute rehearsal run.
"""

from __future__ import annotations

import pathlib
import re

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "deployment_rehearsal.sh"
)


def _body() -> str:
    return SCRIPT.read_text()


def _code_only() -> str:
    """The script with comment lines stripped.

    Every guard in this repository that read a file as raw text has, at least
    once, matched its own documentation. The comments here NAME `pg_isready`
    and `wait_for` while explaining why they are not used for the database, so
    a substring search over the whole file would fail on the explanation.
    """
    return "\n".join(
        line for line in _body().splitlines() if not line.lstrip().startswith("#")
    )


def test_the_stability_aware_wait_exists() -> None:
    assert (
        "wait_for_stable()" in _code_only()
    ), "the rehearsal needs a wait that requires CONSECUTIVE successes"


def test_database_readiness_uses_the_stable_wait() -> None:
    """Both database waits, not just the first.

    The post-deploy re-check races the same way — the app's own migration
    container can restart the connection pool underneath it.
    """
    code = _code_only()
    stable = len(re.findall(r"wait_for_stable\s+", code))
    assert stable >= 2, (
        f"expected both database readiness waits to use wait_for_stable, "
        f"found {stable} call(s)"
    )


def test_no_database_wait_still_uses_the_first_success_wait() -> None:
    """`wait_for` is fine for a port or a registry; not for a database."""
    code = _code_only()
    pattern = r"wait_for\s+\"?\$\{?[A-Z_]*\}?\"?[^\n]*\n(?:[^\n]*\n)?"
    for match in re.finditer(pattern, code):
        window = match.group(0)
        if "wait_for_stable" in window:
            continue
        assert "pg_isready" not in window and "psql" not in window, (
            "a database readiness probe is using `wait_for`, which accepts the "
            f"first success and races Postgres' init restart:\n{window}"
        )


def test_the_probe_runs_real_sql_rather_than_pg_isready() -> None:
    """`pg_isready` answers "is a server accepting connections".

    The entrypoint's temporary init server answers yes to that too. What the
    next line actually needs is to RUN SQL as the superuser, so that is what
    the wait should prove.
    """
    code = _code_only()
    stable_calls = re.findall(r"wait_for_stable[^\n]*\n(?:\s+[^\n]*\n)+", code)
    assert stable_calls, "no wait_for_stable call found to inspect"
    db_calls = [call for call in stable_calls if "psql" in call or "pg_isready" in call]
    assert db_calls, "no database readiness call found"
    for call in db_calls:
        assert "psql" in call and "SELECT 1" in call, (
            "the database readiness probe must run real SQL, not `pg_isready`:\n" + call
        )


def test_the_stable_probe_count_is_configurable_with_a_default() -> None:
    """Everything by config (AGENTS.md): a tuned wait must be tunable."""
    assert re.search(r':\s*"\$\{WAIT_DB_STABLE_PROBES:=\d+\}"', _body()), (
        "the consecutive-probe count must be an overridable knob with a "
        "documented default"
    )


def test_the_guard_reads_code_not_comments() -> None:
    """Sensitivity proof for `_code_only`.

    The comments deliberately mention `pg_isready` and `wait_for`. If the
    stripper ever stopped working, the tests above would start reading the
    prose that explains them — and pass or fail for the wrong reason.
    """
    assert "pg_isready" in _body(), "the comments should still explain the trap"
    stripped = _code_only()
    assert "Postgres" not in stripped, (
        "_code_only is not removing comment lines, so every check above is "
        "reading documentation rather than code"
    )


# ── loopback TCP is what actually closes the window ─────────────────────────


def _stable_calls() -> list[str]:
    calls = re.findall(r"wait_for_stable[^\n]*\n(?:\s+[^\n]*\n)+", _code_only())
    assert calls, "no wait_for_stable call found to inspect"
    return calls


def _db_calls() -> list[str]:
    calls = [c for c in _stable_calls() if "psql" in c]
    assert len(calls) == 4, (
        f"expected 4 database readiness probes (primary, primary re-check, and "
        f"the restore target at both call sites), found {len(calls)}"
    )
    return calls


def test_every_database_probe_connects_over_loopback_tcp() -> None:
    """The socket cannot tell the two servers apart; TCP can.

    Postgres' entrypoint runs its temporary init server with
    `listen_addresses=''`. It serves the Unix socket and NO TCP, so a socket
    probe can be satisfied by a server that is about to be shut down, while a
    loopback TCP probe cannot be satisfied until the real server listens.

    All four sites, because the one that bit was not the first one written.
    """
    for call in _db_calls():
        assert "-h 127.0.0.1" in call, (
            "a database readiness probe is using the Unix socket, which the "
            "temporary init server also serves:\n" + call
        )
        assert '-p "${DB_PORT}"' in call, (
            "the probe must name the in-container port explicitly:\n" + call
        )


def test_the_probe_count_floor_is_enforced_in_the_script() -> None:
    """A knob that can be tuned to 1 is `wait_for` again, wearing a new name.

    The floor is CHECKED, not documented — `AGENTS.md` rule 25: a guard whose
    premise is not enforceable is an unmonitored region.
    """
    body = _body()
    assert "WAIT_DB_STABLE_PROBES_FLOOR=3" in body, (
        "the consecutive-probe floor must be a named constant, not a literal "
        "buried in a comparison"
    )
    refusal = (
        r'if \[ "\$\{WAIT_DB_STABLE_PROBES\}" -lt '
        r'"\$\{WAIT_DB_STABLE_PROBES_FLOOR\}" \]'
    )
    assert re.search(
        refusal, body
    ), "the script must REFUSE a probe count below the floor, not warn about it"
    assert re.search(r"''\|\*\[!0-9\]\*\)", body), (
        "the script must reject a non-numeric WAIT_DB_STABLE_PROBES; an "
        "unquoted comparison against a word is a shell error, not a refusal"
    )
