"""Job runner entrypoint: a second process from the same image.

    python -m dotmac_durable_timers.run --assembly package.module:attr

``attr`` is a ``ProductAssemblySpec`` or a zero-argument callable returning
one.  The runner reads that assembly's ``ModuleRegistry.jobs()``, so it runs
exactly the jobs the web process declares, from the same pins.

Two database credentials, each read from a FILE (a tmpfs secret mount).  The
path is an argument or a ``*_FILE`` variable; the value is never an argument,
never in the environment and never logged.  A file must be a regular file (not a
symlink), mode 0400, owned by the running user or root:

* ``--dispatcher-dsn-file`` / ``RELAY_DISPATCHER_DATABASE_URL_FILE`` - the
  least-privilege ``outbox_dispatcher`` role: claim and settle outbox rows only,
  never a tenant read;
* ``--database-dsn-file`` (alias ``--app-dsn-file``) / ``DATABASE_URL_FILE`` -
  the RLS-enforced ``app_user`` role, scoped per event to that event's tenant
  for every handler and every tenant read.

Health: a heartbeat file (``--heartbeat-file``, default under /tmp) is refreshed
by a background thread and stamped after each reconcile pass; the deploy probe
runs ``python -m dotmac_durable_timers.probe ping`` (process alive) and
``... probe tick`` (scheduler still reconciling).

Refuses to start (exit 2, nothing claimed) when: the assembly cannot be
loaded; ``durable_timers`` is absent, disabled or lacks the tenant plane; a
declared job uses the platform plane; any OTHER enabled module declares outbox
event types (the kernel claim is not type-scoped, so this runner would
dead-letter their events; lifted when the kernel offers a filtered claim); the
assembly binds no health sink under ``durable_timers.health_sink``; no source
application is named; either DSN is missing; or the database read-back of the
two roles fails (same role, a superuser or BYPASSRLS tenant role, or a
dispatcher that can read tenant data).

The runner claims ONE event at a time, so SIGTERM and SIGINT stop after the
event in hand: it finishes and commits, nothing more is claimed, engines are
disposed, exit 0.  Anything claimed but unfinished at a hard kill is reclaimed
after the lease goes stale.

This file is the composition root: it is the only place that builds engines and
reads the ambient clock.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Final

from dotmac_durable_timers.dispatch import JobDispatchTransport
from dotmac_durable_timers.dsnfile import DsnFileError, resolve_dsn
from dotmac_durable_timers.health import HEALTH_SINK_PROVIDER_KEY
from dotmac_durable_timers.heartbeat import DEFAULT_PATH, Heartbeat
from dotmac_durable_timers.jobs import JobCatalogue, JobPlaneUnsupported
from dotmac_durable_timers.rolecheck import RoleSplitError, verify_role_split
from dotmac_durable_timers.runner import JobRunner

logger = logging.getLogger("dotmac_durable_timers.run")

MODULE_CODE: Final[str] = "durable_timers"
EXIT_REFUSED: Final[int] = 2


class RunnerConfigError(RuntimeError):
    """The runner refused to start; the message names what is missing."""


def _clock() -> datetime:
    return datetime.now(UTC)


def load_assembly(target: str) -> object:
    module_name, sep, attr = target.partition(":")
    if not sep or not module_name or not attr:
        raise RunnerConfigError("--assembly must be 'package.module:attr'")
    try:
        value = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise RunnerConfigError(f"cannot load assembly {target!r}: {exc}") from exc
    return value() if callable(value) else value


def foreign_outbox_event_types(
    manifests: Iterable[object],
) -> dict[str, tuple[str, ...]]:
    """Outbox event types declared by modules other than durable_timers.

    The kernel's outbox claim is not type-scoped: this runner would claim and
    dead-letter any other module's events (and another relay would claim ours).
    Until a type-filtered claim exists the runner refuses such an assembly.
    """
    foreign: dict[str, tuple[str, ...]] = {}
    for manifest in manifests:
        code = str(getattr(manifest, "code", ""))
        declared = tuple(getattr(manifest, "outbox_event_types", ()))
        if code != MODULE_CODE and declared:
            foreign[code] = declared
    return foreign


def prepare(
    spec: object, *, source_application: str | None
) -> tuple[JobCatalogue, object]:
    """Validate the assembly and install the process vocabularies.

    Returns the job catalogue and the bound health sink.  Pure of any database.
    """
    from dotmac_kernel.assembly import ProductAssemblySpec
    from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
    from dotmac_kernel.modules import ModuleRegistry
    from dotmac_kernel.outbox_event_types import (
        OutboxEventTypeRegistry,
        install_outbox_event_types,
    )
    from dotmac_kernel.planes import ModulePlane
    from dotmac_kernel.source_applications import install_host_application

    if not isinstance(spec, ProductAssemblySpec):
        raise RunnerConfigError("the assembly must be a ProductAssemblySpec")
    registry = ModuleRegistry(spec.modules)
    disabled = set(spec.disabled_modules)
    if MODULE_CODE not in registry.enabled_codes(disabled):
        raise RunnerConfigError(f"module {MODULE_CODE!r} is absent or disabled")
    selected = {
        ModulePlane(p)
        for selection in spec.module_planes
        if selection.module == MODULE_CODE
        for p in selection.planes
    }
    if ModulePlane.TENANT not in selected:
        raise RunnerConfigError(
            f"module {MODULE_CODE!r} must select the tenant plane in the assembly"
        )
    sink = spec.providers.get(HEALTH_SINK_PROVIDER_KEY)
    if sink is None or not hasattr(sink, "job_dead_lettered"):
        raise RunnerConfigError(
            f"no health sink bound: set providers[{HEALTH_SINK_PROVIDER_KEY!r}] "
            "(LoggingHealthSink is an explicit log-only choice)"
        )
    if not source_application:
        raise RunnerConfigError(
            "name this application with --source-application or "
            "DOTMAC_SOURCE_APPLICATION (audit attribution)"
        )
    foreign = foreign_outbox_event_types(registry.enabled_order(disabled))
    if foreign:
        listed = "; ".join(
            f"{code}: {', '.join(types)}" for code, types in foreign.items()
        )
        raise RunnerConfigError(
            "other enabled modules declare outbox event types and the outbox "
            f"claim is not type-scoped yet ({listed}); the job runner would "
            "dead-letter their events"
        )
    manifests = list(registry.startup_order())
    install_audit_actions(AuditActionRegistry.from_manifests(manifests))
    install_outbox_event_types(OutboxEventTypeRegistry.from_manifests(manifests))
    install_host_application(source_application)
    try:
        catalogue = JobCatalogue.from_registry(registry, disabled)
    except JobPlaneUnsupported as exc:
        raise RunnerConfigError(str(exc)) from exc
    return catalogue, sink


def _dsn(path_arg: str | None, env_name: str) -> str:
    try:
        return resolve_dsn(path_arg, env_name)
    except DsnFileError as exc:
        raise RunnerConfigError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dotmac_durable_timers.run")
    parser.add_argument("--assembly", required=True, help="package.module:attr")
    parser.add_argument(
        "--dispatcher-dsn-file",
        help="path of the file holding the outbox_dispatcher DSN "
        "(else the RELAY_DISPATCHER_DATABASE_URL_FILE variable)",
    )
    parser.add_argument(
        "--database-dsn-file",
        "--app-dsn-file",
        help="path of the file holding the app_user DSN "
        "(else the DATABASE_URL_FILE variable)",
    )
    parser.add_argument("--heartbeat-file", default=DEFAULT_PATH)
    parser.add_argument(
        "--batch-size",
        type=int,
        choices=(1,),
        default=1,
        help="reserved: the runner claims one event at a time",
    )
    parser.add_argument(
        "--source-application", default=os.environ.get("DOTMAC_SOURCE_APPLICATION")
    )
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--reconcile-interval", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        catalogue, sink = prepare(
            load_assembly(args.assembly), source_application=args.source_application
        )
        dispatcher_dsn = _dsn(
            args.dispatcher_dsn_file, "RELAY_DISPATCHER_DATABASE_URL_FILE"
        )
        tenant_dsn = _dsn(args.database_dsn_file, "DATABASE_URL_FILE")
        if dispatcher_dsn == tenant_dsn:
            raise RunnerConfigError(
                "the dispatcher and tenant DSNs are identical; they must be two "
                "different database roles"
            )
    except RunnerConfigError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    from dotmac_kernel.messaging.relay import RelayPolicy
    from dotmac_kernel.session_runtime import DatabaseRuntime

    tenant_runtime = DatabaseRuntime.from_urls(
        database_url=tenant_dsn, hide_parameters=True
    )
    dispatcher_runtime = DatabaseRuntime.from_urls(
        database_url=dispatcher_dsn,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
    )
    try:
        verify_role_split(
            tenant_factory=tenant_runtime.session_factory,
            dispatcher_factory=dispatcher_runtime.session_factory,
        )
    except Exception as exc:
        for runtime in (tenant_runtime, dispatcher_runtime):
            runtime.engine.dispose()
            runtime.platform_engine.dispose()
        reason = str(exc) if isinstance(exc, RoleSplitError) else type(exc).__name__
        print(
            f"refusing to start: database role check failed: {reason}", file=sys.stderr
        )
        return EXIT_REFUSED
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    transport = JobDispatchTransport(
        catalogue=catalogue,
        health=sink,  # type: ignore[arg-type]
        clock=_clock,
    )
    runner = JobRunner(
        catalogue=catalogue,
        transport=transport,
        dispatcher_session_factory=dispatcher_runtime.session_factory,
        tenant_runtime=tenant_runtime,
        worker_id=args.worker_id,
        clock=_clock,
        policy=RelayPolicy(batch_size=1),
        reconcile_interval_seconds=args.reconcile_interval,
        poll_interval_seconds=args.poll_interval,
        heartbeat=Heartbeat(
            args.heartbeat_file, worker_id=args.worker_id, clock=_clock
        ),
    )
    try:
        runner.run_forever(stop)
    finally:
        tenant_runtime.engine.dispose()
        tenant_runtime.platform_engine.dispose()
        dispatcher_runtime.engine.dispose()
        dispatcher_runtime.platform_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
