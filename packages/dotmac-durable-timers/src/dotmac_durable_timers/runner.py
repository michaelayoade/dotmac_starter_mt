"""The job runner loop: reconcile generations, then drain due events.

Two database identities, never mixed:

* the DISPATCHER session (``outbox_dispatcher``) only claims and settles
  outbox rows through the relay's SECURITY DEFINER functions.  It never reads
  tenant data;
* the TENANT runtime (``app_user``) does everything tenant-scoped: listing
  tenants (the non-RLS tenancy table), arming generations, and running the
  handler inside the event's tenant RLS session.

This module receives its sessions and runtime; it builds no engine.  The
entrypoint (``dotmac_durable_timers.run``) builds them.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from dotmac_kernel.messaging.relay import RelayPolicy
from dotmac_kernel.messaging.worker import SessionFactory, run_once
from dotmac_kernel.models import Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_durable_timers.dispatch import JobDispatchTransport
from dotmac_durable_timers.heartbeat import Heartbeat
from dotmac_durable_timers.jobs import JobCatalogue, reconcile_tenant

logger = logging.getLogger("dotmac_durable_timers.runner")


class TenantRuntime(Protocol):
    """The slice of ``DatabaseRuntime`` the runner uses."""

    @property
    def session_factory(self) -> SessionFactory: ...

    def resolver_session(self) -> AbstractContextManager[Session]: ...

    def tenant_session(self, tenant_id: object) -> AbstractContextManager[Session]: ...


class JobRunner:
    def __init__(
        self,
        *,
        catalogue: JobCatalogue,
        transport: JobDispatchTransport,
        dispatcher_session_factory: SessionFactory,
        tenant_runtime: TenantRuntime,
        worker_id: str,
        clock: Callable[[], datetime],
        policy: RelayPolicy = RelayPolicy(),
        reconcile_interval_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._catalogue = catalogue
        self._transport = transport
        self._dispatcher_sessions = dispatcher_session_factory
        self._runtime = tenant_runtime
        self._worker_id = worker_id
        self._clock = clock
        self._policy = policy
        self._reconcile_interval = reconcile_interval_seconds
        self._poll_interval = poll_interval_seconds
        self._monotonic = monotonic
        self._heartbeat = heartbeat

    def reconcile(self) -> int:
        """Arm every unpaused declared job in every active tenant. A tenant that
        fails is logged and skipped; it never blocks the others."""
        with self._runtime.resolver_session() as db:
            tenant_ids = list(
                db.scalars(
                    select(Tenant.id).where(
                        Tenant.is_active.is_(True),
                        Tenant.deleted_at.is_(None),
                        Tenant.suspended_at.is_(None),
                    )
                )
            )
        armed = 0
        for tenant_id in tenant_ids:
            try:
                with self._runtime.tenant_session(tenant_id) as db:
                    armed += reconcile_tenant(
                        db,
                        tenant_id=tenant_id,
                        catalogue=self._catalogue,
                        now=self._clock(),
                    )
            except Exception:
                logger.exception("job reconcile failed for tenant %s", tenant_id)
        return armed

    def drain_once(self) -> int:
        """Claim and deliver one batch. Returns the number claimed."""
        dispatcher_db: Session = self._dispatcher_sessions()
        try:
            return run_once(
                dispatcher_db=dispatcher_db,
                tenant_session_factory=self._runtime.session_factory,
                transport=self._transport,
                worker_id=self._worker_id,
                policy=self._policy,
            )
        finally:
            dispatcher_db.close()

    def run_forever(self, stop: threading.Event) -> None:
        """Loop until ``stop`` is set; an in-flight event finishes first."""
        logger.info("job runner %s starting", self._worker_id)
        if self._heartbeat is not None:
            self._heartbeat.start(stop)
        next_reconcile = self._monotonic()
        while not stop.is_set():
            if self._monotonic() >= next_reconcile:
                try:
                    self.reconcile()
                    if self._heartbeat is not None:
                        self._heartbeat.tick()
                except Exception:
                    logger.exception("job reconcile pass failed")
                next_reconcile = self._monotonic() + self._reconcile_interval
            try:
                claimed = self.drain_once()
            except Exception:
                logger.exception("job drain pass failed")
                claimed = 0
            if claimed == 0:
                stop.wait(self._poll_interval)  # interruptible idle sleep
        if self._heartbeat is not None:
            self._heartbeat.remove()
        logger.info("job runner %s stopped", self._worker_id)


__all__ = ["JobRunner", "TenantRuntime"]
