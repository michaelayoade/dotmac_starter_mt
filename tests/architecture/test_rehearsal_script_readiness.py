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
    stable = len(re.findall(r"wait_for_stable_container\s+", code))
    assert stable >= 4, (
        f"expected all four database readiness waits to use "
        f"wait_for_stable_container, found {stable} call(s)"
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


# ── a failed wait must say WHY ──────────────────────────────────────────────


def test_a_failed_container_wait_reports_diagnostics() -> None:
    """A timeout that prints nothing costs a whole rehearsal run to learn one line.

    Run 33095822846 spent twenty minutes to report

        timed out after 20s waiting for: the rehearsal's own collector on :14318

    while `docker logs` had said, the whole time,

        cannot start pipelines: open /sink/otel-sink.json: permission denied

    Every wait that GATES the run — as opposed to an observation window, which
    ends in `|| true` and where a timeout is a legitimate answer — must route
    its failure through `diagnose_container`.
    """
    code = _code_only()
    assert "diagnose_container()" in code, "the diagnostic helper must exist"

    helper = re.search(r"diagnose_container\(\)[^\n]*\n(?:.*?\n)*?^}", code, re.M)
    assert helper, "could not read diagnose_container's body"
    body = helper.group(0)
    assert "logs" in body, "diagnostics must include the container's logs"
    assert "inspect" in body, "diagnostics must include the container's state"

    # NOT by scanning for one syntactic shape. The first version of this test
    # looked for `if ! wait_for ... fi` blocks and therefore only ever saw the
    # two waits written that way — the registry, both databases and all four
    # app waits used other forms and were silently uncovered while this test
    # reported success. A guard that discovers one shape measures the shape,
    # not the property.
    #
    # The property is enforced STRUCTURALLY instead: the primitives are
    # underscore-prefixed and every call site goes through a wrapper that
    # diagnoses, or through `observe_for`, which declares that it does not.
    for wrapper in ("wait_for_container()", "wait_for_stable_container()"):
        body_match = re.search(
            rf"{re.escape(wrapper)}[^\n]*\n(?:.*?\n)*?^}}", code, re.M
        )
        assert body_match, f"{wrapper} must exist"
        assert "diagnose_container" in body_match.group(0), (
            f"{wrapper} must diagnose on failure — it is the only reason it "
            "exists rather than calling the primitive directly"
        )


def test_no_readiness_timeout_is_a_hardcoded_literal() -> None:
    """Everything by config. A tuned timeout nobody can tune is a guess."""
    code = _code_only()
    literals = re.findall(r"wait_for\s+(\d+)\s", code)
    assert not literals, (
        f"hardcoded wait timeout(s) {literals}: every bound must be an "
        "overridable knob with a documented default"
    )


# ── the wrappers are the ONLY way to wait ───────────────────────────────────


def test_every_readiness_wait_goes_through_a_wrapper() -> None:
    """A raw primitive call is how a wait ends up silent.

    `_wait_for` / `_wait_for_stable` are underscore-prefixed so "internal" is
    structural rather than a naming convention nobody enforces. Every call site
    must use `wait_for_container` / `wait_for_stable_container` (which
    diagnose) or `observe_for` (which declares that it deliberately does not).
    """
    code = _code_only()
    outside = re.sub(
        r"^(?:_wait_for|_wait_for_stable|wait_for_container|"
        r"wait_for_stable_container|observe_for)\(\)[^\n]*\n(?:.*?\n)*?^}",
        "",
        code,
        flags=re.M,
    )
    # Ban the PREFIXED names. Banning the bare `wait_for ` bans nothing: the
    # primitives ARE `_wait_for` / `_wait_for_stable`, so a mutation that
    # swapped a wrapper for the raw primitive sailed straight through the first
    # version of this assertion. The sensitivity proof is what said so — the
    # mutation ran, the suite stayed green, and the guard was measuring a name
    # nothing is called.
    raw = re.findall(r"(?<![\w])_wait_for(?:_stable)?\s", outside)
    assert not raw, (
        f"{len(raw)} raw primitive wait call(s) outside the wrappers — those "
        "fail without diagnostics. Use wait_for_container / "
        "wait_for_stable_container, or observe_for for a deliberate window"
    )


def test_observation_windows_are_declared_not_omitted() -> None:
    """`observe_for` exists so "no diagnostics here" is a CHOICE, not a gap.

    Without a distinct name, an observation window and a forgotten diagnostic
    look identical in the source — which is precisely how the uncovered waits
    survived the first version of this file.
    """
    code = _code_only()
    assert "observe_for()" in code, "the observation-window wrapper must exist"
    calls = re.findall(r"(?<![_a-z])observe_for\s+\"", code)
    assert calls, "observe_for must actually be used, or it is dead reassurance"


def test_each_service_has_its_own_timeout_knob() -> None:
    """One shared "infrastructure" timeout cannot be tuned for any of them.

    Raising it for a slow collector pull silently doubles how long a dead
    registry takes to report; lowering it for a fast registry makes the
    collector flaky.
    """
    body = _body()
    for knob in (
        "WAIT_REGISTRY_TIMEOUT_SECONDS",
        "WAIT_COLLECTOR_TIMEOUT_SECONDS",
        "WAIT_PROMETHEUS_TIMEOUT_SECONDS",
        "WAIT_ALERT_FIRE_SECONDS",
        "WAIT_ALERT_RECOVER_SECONDS",
        "WAIT_DB_TIMEOUT_SECONDS",
        "WAIT_HTTP_TIMEOUT_SECONDS",
        "WAIT_RESTART_OBSERVATION_SECONDS",
    ):
        assert re.search(
            rf':\s*"\$\{{{knob}:=\d+\}}"', body
        ), f"{knob} must be an overridable knob with a documented default"


def test_no_wait_loop_hardcodes_its_bound() -> None:
    """The alert fire/recover loops were `while [ waited -lt 60 ]`."""
    code = _code_only()
    literal_loops = re.findall(r'while \[ "\$\{waited\}" -lt (\d+) \]', code)
    assert not literal_loops, f"hardcoded loop bound(s) {literal_loops}: use a knob"


# ── alert recovery is conjunctive and structurally parsed ──────────────────


def _function(name: str) -> str:
    code = _code_only()
    match = re.search(rf"^{re.escape(name)}\(\)[^\n]*\n(?:.*?\n)*?^}}", code, re.M)
    assert match, f"could not read {name}'s body"
    return match.group(0)


def test_alert_state_is_read_from_the_rule_not_an_alert_instance() -> None:
    """`alertname` disappears with the active instance on recovery.

    Exact-main run 33111496459 and an independent Observer reproduction both
    ended with the stable rule named ``RehearsalTargetDown`` inactive and its
    ``alerts`` list empty. The old grep therefore described an impossible
    recovered state.
    """
    code = _code_only()
    assert "prom_rule_is()" in code
    assert 'grep -q \'"alertname"' not in code
    assert "prom_rule_is inactive" in code
    assert "prom_rule_is firing" in code


def test_the_real_wait_path_executes_the_fixture_tested_json_probe() -> None:
    code = _code_only()
    assert (
        '.*"state":"firing"' not in code
    ), "a greedy match can pair one rule's name with another rule's state"
    assert 'PROM_PROBE="${REHEARSAL_DIR}/prometheus_probe.py"' in code
    assert '"${PYTHON_BIN}" "${PROM_PROBE}"' in code


def test_a_vanished_rule_or_target_is_not_treated_as_recovery() -> None:
    """An inactive rule with no scrape target is a monitoring outage.

    ``up == 0`` evaluates to an empty vector when the target disappears. The
    executable parser fixtures prove each named object refuses zero and
    duplicate matches; this composition test pins both halves into recovery.
    """
    recovery = _function("prom_recovery_proved")
    assert "prom_rule_is inactive" in recovery
    assert "prom_target_is up" in recovery


def test_fire_requires_the_same_target_to_be_down() -> None:
    fire = _function("prom_fire_proved")
    assert "prom_rule_is firing" in fire
    assert "prom_target_is down" in fire


def test_restart_restores_application_readiness_before_claiming_recovery() -> None:
    """The tmpfs readiness marker disappears across the injected stop/start."""
    step = _function("run_step_13_alert_fires_and_recovers")
    marker = step.find(": > /tmp/rehearsal-ready")
    ready = step.find("app readiness=200 after restart")
    healthy = step.find("app Docker health healthy after restart")
    recovery = step.find("prom_recovery_proved")
    assert -1 not in (marker, ready, healthy, recovery)
    assert marker < ready < healthy < recovery


def test_alert_transition_failures_emit_rule_target_and_app_diagnostics() -> None:
    helper = _function("diagnose_prometheus_transition")
    for evidence in (
        "rule-summary",
        "target-summary",
        "/health/live",
        "/health/ready",
        "diagnose_container",
    ):
        assert evidence in helper, f"alert diagnostics omit {evidence}"

    step = _function("run_step_13_alert_fires_and_recovers")
    assert step.count("diagnose_prometheus_transition") >= 3, (
        "baseline, fire and recovery failures must print the evidence that "
        "distinguishes a missing rule, missing target and unhealthy app"
    )
