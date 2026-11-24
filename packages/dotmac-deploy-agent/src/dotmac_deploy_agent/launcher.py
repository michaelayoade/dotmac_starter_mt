"""One launch cycle: fetch, lease, transfer, render, deliver, run, classify, report.

The agent decides nothing. Every check below is a refusal of something Control
or the bindings already bound; any refusal stops the cycle before Foundation is
started and is reported as a refused observation.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .client import Reply, TargetClient
from .commands import Commands
from .config import AgentConfig, refuse_db_environment
from .delivery import (
    Delivered,
    deliver_secret_ids,
    remove_single_use_files,
    settle_accessors,
)
from .dispatch import read_identity
from .files import (
    ensure_private_dir,
    require_private_dir,
    write_private,
)
from .job_delivery import (
    check_images_present,
    deliver_job_secret_id,
    purge_job_role,
    render_job_credentials,
    require_render_unit,
)
from .jobs import (
    JOB_DISPATCH_LABEL,
    JOB_MARKER_LABEL,
    JOB_STOP_MARGIN,
    JOB_TEARDOWN_RESERVE,
    JOB_TERM_GRACE,
    check_apply_class,
    check_job_inputs,
    foundation_receipt_matches,
    job_env,
)
from .journal import AccessorStore, ExecutedDispatches
from .lease import EXPIRED_UNRELEASED, Lease, LeaseClient, LeaseKeeper
from .legacy import prepare_legacy, remove_legacy_copy
from .materials import Materials, parse_materials
from .observation import (
    Outbox,
    build_observation,
    clear_inflight,
    mark_inflight,
    read_inflight,
    safe_id,
)
from .observation_v2 import kind_for_operation
from .openbao import OpenBaoSession
from .openbao_config import check_authored_configs, job_config_stem
from .params import (
    PARAMS_FILE,
    PostRun,
    canonical_render,
    classify_post_run,
    params_path,
    refuse_preexisting_overrides,
    remove_runtime_files,
    render_digest,
    write_params_env,
)
from .reconcile import reconcile_observation
from .refusal import Refusal
from .report_state import SeenStore
from .runner import Runner, RunResult, RunSpec
from .systemd_creds import read_credential
from .transfer import Transfer
from .transport import Transport
from .wire import utc_stamp

LOG = logging.getLogger("dotmac.deploy_agent")
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")
JOB_PARAMS_FILE = "job-params.env"


@dataclass(frozen=True)
class CycleResult:
    # idle | completed | refused | failed | integrity_violation
    outcome: str
    reason: str | None = None
    dispatch_id: str | None = None


class Launcher:
    def __init__(
        self,
        config: AgentConfig,
        client: TargetClient,
        runner: Runner,
        bao_transport: Transport | None,
        lease: LeaseClient,
        transfer: Transfer,
        read_only: Commands,
        commands: Commands,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        root_uid: int = 0,
        epoch: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.client = client
        self.runner = runner
        self.bao_transport = bao_transport
        self.commands = commands
        self.root_uid = root_uid  # the owner enrolment files must have (0; tests only)
        self.lease = lease
        self.transfer = transfer
        self.read_only = read_only
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        self.clock = clock
        self.epoch = epoch
        self.outbox = Outbox(config.state_dir)
        self.executed = ExecutedDispatches(config.state_dir)
        self.accessors = AccessorStore(config.state_dir)
        #: Job-role accessors (A7): tracked from unwrap, kept apart from the
        #: serving roles' lifecycle so settlement never touches them.
        self.job_accessors = AccessorStore(config.state_dir, "job-accessors")
        self._refusals_reported = SeenStore(config.state_dir, "refusals_reported")
        self._reconcile_posted = SeenStore(config.state_dir, "reconcile_posted")
        self._interrupted: str | None = None

    # -- housekeeping ------------------------------------------------------
    def _prepare_dirs(self) -> None:
        ensure_private_dir(self.config.run_dir)
        ensure_private_dir(self.config.deploy_run_dir)
        ensure_private_dir(self.config.inbox_dir)

    def _clean_stale(self) -> None:
        """A file left by a crashed run is removed before anything else (s2.5.5)."""
        remove_runtime_files(self.config.deploy_run_dir, (PARAMS_FILE,))
        remove_legacy_copy(self.config.deploy_run_dir)
        # A kill mid-copy leaves the loopback registry's storage on tmpfs (R5.6),
        # and a kill mid-run leaves the per-run credentials directory.
        shutil.rmtree(self.config.registry_dir, ignore_errors=True)
        shutil.rmtree(self._run_credentials_dir(), ignore_errors=True)
        self.purge_stale_jobs()

    def purge_stale_jobs(self) -> None:
        """A7, at agent start: a crash mid-job leaves a `secret_id` file and maybe
        a live accessor. Files go first; accessors are destroyed when OpenBao is
        reachable and stay tracked (retried next cycle) when it is not."""
        cleared = self._clear_orphans()
        if cleared is None:
            # The role-job lock is busy: a role job may be running. Nothing is
            # removed and nothing is an anomaly; the preflight refuses the lease.
            LOG.info("role-job lock busy at agent start: orphan check deferred")
        found, remains, foreign, _removed = cleared or (False, False, False, False)
        if found:
            # P-SEC rev7: files and accessors are STILL purged (a surviving
            # container is no reason to leave DSNs on tmpfs). This agent's own orphan
            # was removed by label (r5 ruling b); a foreign or stubborn one keeps the
            # preflight blocking every lease.
            LOG.error(
                "a job container survived an earlier run: remains=%s foreign=%s",
                remains,
                foreign,
            )
        for role in self.job_accessors.roles():
            try:
                self.purge_job_role(role)
            except Refusal as refusal:
                LOG.warning("stale job purge deferred role=%s code=%s", role, refusal)

    def purge_job_role(self, role: str) -> int:
        """A7, after a job (any exit code), at agent start and before every mint:
        remove the agent's own `secret_id`, start the role's purge unit, destroy
        every tracked accessor. Returns how many accessors are still tracked."""
        session: OpenBaoSession | None = None
        try:
            if self.job_accessors.load(role).retire:
                try:
                    session = self._bao_session()
                except Refusal:
                    session = None  # unreachable: files still go, accessors wait
            return purge_job_role(
                self.config, self.commands, self.job_accessors, role, session
            )
        finally:
            if session is not None:
                session.revoke()

    def _run_credentials_dir(self) -> Path:
        return self.config.run_dir / "credentials"

    def flush_outbox(self) -> None:
        for path in self.outbox.pending():
            try:
                document = json.loads(self.outbox.read(path))
                self.client.post_observation(document)
            except Refusal as refusal:
                if refusal.code == "cp_refused":
                    # A signed 4xx will not change on retry: move it aside so one
                    # refused observation cannot block the ones behind it.
                    self.outbox.dead_letter(path)
                    continue
                return  # unreachable or unsigned: keep it, retry next cycle
            except ValueError:
                self.outbox.dead_letter(path)
                continue
            self.outbox.acknowledge(path)

    def _report(self, observation: dict[str, object]) -> None:
        if (
            observation.get("outcome") == "refused"
            and observation.get("kind") != "reconcile"
        ):
            # One refused observation per (dispatch, reason), not one per poll.
            key, reason = (
                str(observation["dispatch_id"]),
                str(observation.get("reason")),
            )
            if self._refusals_reported.seen(key, reason):
                return
            self._refusals_reported.remember(key, reason)
        self.outbox.seal(observation)
        self.flush_outbox()

    def _last_app_image(self) -> str | None:
        """The pin this agent itself last applied: the only second rendering the
        post-run check accepts (Foundation restores it after a failed switch).
        Never CP-supplied: an unsigned previous pin would widen the check."""
        try:
            text = (self.config.state_dir / "last_app_image").read_text()
        except OSError:
            return None
        return text.strip() or None

    def _remember_app_image(self, app_image: str) -> None:
        write_private(
            self.config.state_dir / "last_app_image", app_image.encode(), 0o600
        )

    def _recover_interrupted(self) -> None:
        dispatch_id = read_inflight(self.config.state_dir)
        self._interrupted = dispatch_id
        if dispatch_id is None:
            return
        self._report(
            build_observation(
                kind="deploy",
                dispatch_id=dispatch_id,
                target_ref=self.config.target_ref,
                outcome="failed",
                reason=None,
                version=self.config.version,
                details={"interrupted": True},
                now=self.clock(),
            )
        )
        clear_inflight(self.config.state_dir)

    def _prepare_run_credentials(self) -> Path:
        """Foundation reads only `pop-key` from its credentials directory (to sign
        `consume`). Give it a per-run directory holding just that, not the whole
        credentials directory with secret zero. Same uid, so this narrows reach and
        accident, not a hard boundary; it is removed after the run."""
        directory = self._run_credentials_dir()
        shutil.rmtree(directory, ignore_errors=True)
        ensure_private_dir(directory)
        pop_key = read_credential(
            self.environ, self.config.pop_key_credential, code="config_invalid"
        )
        write_private(directory / "pop-key", pop_key, 0o400)
        return directory

    def _bao_session(self) -> OpenBaoSession:
        """A logged-in delivery session (pinned namespace-env); the caller revokes."""
        if self.bao_transport is None:
            raise Refusal("secret_delivery_failed")
        role_id = _credential_text(
            self.environ, self.config.delivery_role_id_credential
        )
        secret_id = _credential_text(
            self.environ, self.config.delivery_secret_id_credential
        )
        session = OpenBaoSession(self.bao_transport, self.config.secrets_environment)
        session.login(role_id, secret_id)
        return session

    def _settle(self) -> None:
        """Retire superseded accessors on a LATER cycle, once the unit stayed up."""
        try:
            settle_accessors(
                self._bao_session,
                self.config,
                self.commands,
                self.accessors,
                clock=self.epoch,
            )
        except Refusal as refusal:
            LOG.warning("accessor settlement deferred code=%s", refusal)

    # -- the cycle ---------------------------------------------------------
    def run_once(self) -> CycleResult:
        refuse_db_environment(self.environ)
        self._prepare_dirs()
        self._clean_stale()
        self._recover_interrupted()
        self.flush_outbox()
        self._settle()
        reply = self.client.materials()
        if reply is None:
            return CycleResult("idle")
        try:
            materials = parse_materials(
                reply.document,
                self.config,
                identity=read_identity(
                    self.config.dispatch_identity_path, owner_uid=self.root_uid
                ),
                store_now=reply.store_now,
            )
        except Refusal as refusal:
            # Unverified: used for refusal evidence only, never as an authority.
            envelope = reply.document.get("dispatch_envelope")
            statement = (
                envelope.get("statement") if isinstance(envelope, dict) else None
            )
            dispatch_id = safe_id(
                statement.get("dispatch_id") if isinstance(statement, dict) else None
            )
            operation = (
                statement.get("operation") if isinstance(statement, dict) else None
            )
            self._report(
                self._observation(
                    dispatch_id,
                    "refused",
                    refusal.code,
                    {},
                    kind=kind_for_operation(
                        operation if isinstance(operation, str) else None
                    ),
                )
            )
            return CycleResult("refused", refusal.code, dispatch_id)
        try:
            standing = self.lease.standing()
        except Refusal as refusal:
            self._report(
                self._observation(
                    materials.dispatch_id,
                    "refused",
                    refusal.code,
                    _detail(refusal),
                    kind=kind_for_operation(materials.operation),
                )
            )
            return CycleResult("refused", refusal.code, materials.dispatch_id)
        if standing == EXPIRED_UNRELEASED:
            # Post the (de-duplicated) facts, then still try to acquire: CP's
            # `reconcile_marker_required` is what stops the cycle, never the standing.
            stopped = self._reconcile(materials.dispatch_id, standing)
            if stopped is not None:
                return stopped
        cleared = self._clear_orphans()
        if cleared is None:
            # A busy role-job lock means a job is legitimately running (r6 R6-M1):
            # refuse the lease, no page, nothing removed (never an orphan finding).
            busy = Refusal("lease_refused", "role_job_running")
            self._report(
                self._observation(
                    materials.dispatch_id,
                    "refused",
                    busy.code,
                    _detail(busy),
                    kind=kind_for_operation(materials.operation),
                )
            )
            return CycleResult("refused", busy.code, materials.dispatch_id)
        found, remains, foreign, removed = cleared
        if found or foreign:
            # Before ANY lease, job or serving: this agent's own labelled container
            # (stopped ones included) is removed by label and re-proved, another
            # dispatch's is never touched, and the cycle is refused and paged either
            # way (a leftover is an anomaly). The next poll proceeds if none remains.
            self._report(
                self._observation(
                    materials.dispatch_id,
                    "refused",
                    "orphan_job_container",
                    {"page": True, "removed": removed, "foreign": foreign},
                    kind=kind_for_operation(materials.operation),
                )
            )
            return CycleResult("refused", "orphan_job_container", materials.dispatch_id)
        if materials.dispatched_job is not None:
            return self._execute_job(materials)
        return self._execute(materials, reply)

    def _expired_dispatch(self) -> str:
        """The dispatch whose lease expired, from CP's lease row (`dispatch_run` in
        the standing response): never the pending dispatch. A local inflight record,
        when present, must agree."""
        reply = self.client.call("GET", "lease/standing", None)
        run = reply.document.get("dispatch_run")
        if not isinstance(run, str) or safe_id(run) != run:
            raise Refusal("reconcile_unavailable")
        if self._interrupted is not None and self._interrupted != run:
            raise Refusal("reconcile_unavailable")
        return run

    def _reconcile(self, pending_dispatch_id: str, standing: str) -> CycleResult | None:
        """Read-only: report the facts about the EXPIRED dispatch. Control decides the
        marker. Posted once per (expired dispatch, observed digest). Returns a result
        only when the expired dispatch cannot be named (the cycle then stops); None
        means carry on to acquire."""
        _ = standing
        try:
            expired = self._expired_dispatch()
        except Refusal as refusal:
            self._report(
                self._observation(pending_dispatch_id, "refused", refusal.code, {})
            )
            return CycleResult("refused", refusal.code, pending_dispatch_id)
        observation, observed = reconcile_observation(
            self.config, self.read_only, expired_dispatch_id=expired, clock=self.clock
        )
        marker = observed or f"failed:{observation.get('reason')}"
        if not self._reconcile_posted.seen(expired, marker):
            self._report(observation)
            self._reconcile_posted.remember(expired, marker, replace=True)
        return None

    def _observation(
        self,
        dispatch_id: str,
        outcome: str,
        reason: str | None,
        details: dict[str, object],
        kind: str = "deploy",
    ) -> dict[str, object]:
        return build_observation(
            kind=kind,
            dispatch_id=dispatch_id,
            target_ref=self.config.target_ref,
            outcome=outcome,
            reason=reason,
            version=self.config.version,
            details=details,
            now=self.clock(),
        )

    def _execute(self, materials: Materials, reply: Reply) -> CycleResult:
        details: dict[str, object] = {"params_env_digest": materials.params_env_digest}
        ran = False
        outcome, reason = "completed", None
        deploy_run_dir = self.config.deploy_run_dir
        lease: Lease | None = None
        keeper: LeaseKeeper | None = None
        try:
            # Before any lease, transfer or secret: CP can re-serve an old signed
            # dispatch, so one already taken to delivery or run is refused.
            self.executed.refuse_replay(materials.dispatch_id)
            order = (materials.execution_sequence, materials.attempt_no)
            self.executed.refuse_superseded(order)
            refuse_preexisting_overrides(self.config.deploy_dir)
            planned = canonical_render(materials.params, materials.app_image)
            if render_digest(planned) != materials.params_env_digest:
                raise Refusal("params_digest_mismatch")
            require_private_dir(deploy_run_dir)

            # Order (DESIGN-rev3 2.6.2): acquire, transfer, render, deliver, run.
            lease = self.lease.acquire(
                dispatch_id=materials.dispatch_id,
                ttl_seconds=materials.lease_ttl_seconds,
                max_seconds=materials.lease_max_seconds,
            )
            details["lease"] = {
                "lease_id": lease.lease_id,
                "ttl_seconds": lease.ttl_seconds,
                "max_seconds": lease.max_seconds,
            }
            # Renewed every ttl/3 through transfer and the run; see LeaseKeeper.
            keeper = self.lease.keeper(lease, dispatch_id=materials.dispatch_id)
            keeper.start()
            transferred = self.transfer.transfer_all(
                materials.images, ttl_seconds=lease.ttl_seconds
            )
            details["transfer"] = [t.as_details() for t in transferred]
            # Transfer spends lease time. A lease with no room left for the run is
            # refused here, before any file or secret_id is spent on it.
            self.lease.run_budget(
                lease, configured_seconds=self.config.foundation_timeout_seconds
            )
            params_file = write_params_env(deploy_run_dir, planned)

            legacy_file: Path | None = None
            if materials.legacy is not None:
                legacy_file = prepare_legacy(
                    materials.legacy,
                    source=self.config.legacy_source_path,
                    deploy_dir=self.config.deploy_dir,
                    deploy_run_dir=deploy_run_dir,
                    environ=self.environ,
                    cp_now=reply.store_now,
                    param_materials={
                        p.material for p in materials.params if p.material
                    },
                    binding_names=set(materials.active_binding_names),
                )
                details["legacy"] = {
                    "version": materials.legacy.version,
                    "sha256": materials.legacy.sha256,
                    "names": list(materials.legacy.names),
                }

            # The first irreversible step: recorded durably BEFORE it, so a crash
            # makes the dispatch un-repeatable rather than repeatable.
            self.executed.record(materials.dispatch_id, order)
            delivered = self._deliver(materials, details)
            details["delivered_roles"] = [
                {
                    "role": d.role,
                    "single_use": d.single_use,
                    "restarted": d.restarted,
                }
                for d in delivered
            ]

            if not self.lease.covers(lease, dispatch_id=materials.dispatch_id):
                raise Refusal("lease_unavailable")
            # The run must end inside the lease: no renew exists, so it is clipped.
            run_budget = self.lease.run_budget(
                lease, configured_seconds=self.config.foundation_timeout_seconds
            )
            details["run_budget_seconds"] = run_budget
            credentials_dir = self._prepare_run_credentials()
            mark_inflight(self.config.state_dir, materials.dispatch_id)
            ran = True
            result = self.runner.run(
                RunSpec(
                    dispatch_id=materials.dispatch_id,
                    target_ref=materials.target_ref,
                    product=self.config.product,
                    plan_digest=materials.plan_digest,
                    params_env=params_file,
                    agent_config=self.config.config_path,
                    bindings_env=self.config.bindings_env,
                    credentials_directory=str(credentials_dir),
                    legacy_env=legacy_file,
                    lease_id=lease.lease_id,
                    timeout_seconds=run_budget,
                )
            )
            details["runner"] = {
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
            post = classify_post_run(
                params_path(deploy_run_dir),
                planned_render=planned,
                previous_app_image=self._last_app_image(),
            )
            details["params_env_post_run"] = post.value
            outcome, reason = self._classify(result, post)
            if outcome == "completed" and post is PostRun.PLANNED:
                self._remember_app_image(materials.app_image)
        except Refusal as refusal:
            outcome = "failed" if ran else "refused"
            reason = refusal.code
            if refusal.detail is not None:
                details["refusal_detail"] = refusal.detail
        finally:
            remove_runtime_files(deploy_run_dir, (PARAMS_FILE,))
            remove_legacy_copy(deploy_run_dir)
            remove_single_use_files(self.config, materials.roles)
            shutil.rmtree(self._run_credentials_dir(), ignore_errors=True)
            if keeper is not None:
                keeper.stop()  # before release: no renew may race the release
                details["lease"] = {
                    **cast("dict[str, object]", details["lease"]),
                    **keeper.as_details(),
                }
            if lease is not None:
                details["lease_released"] = self._release(
                    lease, host_mutated=ran, details=details
                )
            if ran:
                clear_inflight(self.config.state_dir)
        self._report(self._observation(materials.dispatch_id, outcome, reason, details))
        return CycleResult(outcome, reason, materials.dispatch_id)

    def _lock_role_job(self) -> int:
        """One role job per host: a non-blocking lock held for the whole run. (The
        host lease is the cross-kind fence against a serving deploy.)"""
        descriptor = os.open(
            self.config.state_dir / "role-job.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise Refusal("lease_refused", "role_job_running") from None
        return descriptor

    def _execute_job(self, materials: Materials) -> CycleResult:
        """A signed `run_job` dispatch: the one job `dispatched_job` names, its role
        minted for this run only, purged on every exit path, then the receipt.
        Everything executed is read from `dispatched_job`; everything the plan
        binds was checked at parse (operation, window, role fence, lease fit)."""
        job = materials.dispatched_job
        if job is None:
            raise Refusal("materials_invalid")
        # Foundation's secret files are EXACTLY the job's materials.
        credential_names = job.materials
        details: dict[str, object] = {"params_env_digest": materials.params_env_digest}
        receipt: dict[str, object] = {
            "name": job.name,
            "role": job.role,
            "materials": list(job.materials),
            "window_id": job.window_id,
            "started_at": utc_stamp(self.clock()),
        }
        details["job"] = receipt
        ran, outcome, reason = False, "completed", None
        deploy_run_dir = self.config.deploy_run_dir
        job_params = deploy_run_dir / JOB_PARAMS_FILE
        lease: Lease | None = None
        lock: int | None = None
        try:
            lock = self._lock_role_job()
            self.executed.refuse_replay(materials.dispatch_id)
            order = (materials.execution_sequence, materials.attempt_no)
            self.executed.refuse_superseded(order)
            refuse_preexisting_overrides(self.config.deploy_dir)
            planned = canonical_render(materials.params, materials.app_image)
            if render_digest(planned) != materials.params_env_digest:
                raise Refusal("params_digest_mismatch")
            job_lines = job_env(job)  # signed values vs the signed digest
            definition = next((d for d in materials.jobs if d.name == job.name), None)
            check_apply_class(job, definition)  # RQ-3: re-derived, required
            check_job_inputs(  # RQ-2: input sha256s
                self.config.secrets_root, job, self.config.job_work_root
            )
            config_stem = None
            if job.role is not None:
                # RQ-4: the authored config whose file set equals the materials,
                # or refuse with nothing minted.
                config_stem = job_config_stem(
                    self.config, job.role, job.materials, required_uid=self.root_uid
                )
                # ... and a loaded render unit exists for this material set.
                require_render_unit(
                    self.config, self.commands, job.role, config_stem, job.materials
                )
            require_private_dir(deploy_run_dir)
            if not getattr(self.runner, "job_entry_available", False):
                # Before any lease, mint or file: nothing is spent on a job that
                # Foundation cannot run yet.
                raise Refusal("foundation_job_entry_unavailable")
            # R2-C1: a job runs an image already on the host. No transfer.
            check_images_present(self.config, self.read_only, materials.images)

            lease = self.lease.acquire(
                dispatch_id=materials.dispatch_id,
                ttl_seconds=materials.lease_ttl_seconds,
                serving=False,
            )
            acquired_at = int(self.epoch())
            details["lease"] = {
                "lease_id": lease.lease_id,
                "ttl_seconds": lease.ttl_seconds,
            }
            params_file = write_params_env(deploy_run_dir, planned)
            write_private(job_params, job_lines, 0o400)

            self.executed.record(materials.dispatch_id, order)  # before the mint
            secrets_dir = deploy_run_dir
            secret_files: tuple[str, ...] = ()
            invocation: str | None = None
            if job.role is not None:
                secrets_dir = self.config.role_dir(job.role) / "secrets"
                self.purge_job_role(job.role)  # before every mint
                receipt["accessor_prefix"] = self._mint_job_role(materials, job.role)
                secret_files, invocation = render_job_credentials(
                    self.config,
                    self.commands,
                    job.role,
                    credential_names,
                    config_stem,
                )
                receipt["render_invocation"] = invocation
                receipt["files"] = list(secret_files)
            if not self.lease.covers(lease, dispatch_id=materials.dispatch_id):
                raise Refusal("lease_unavailable")
            credentials_dir = self._prepare_run_credentials()
            mark_inflight(self.config.state_dir, materials.dispatch_id)
            ran = True
            # R6-1: Foundation gets an ABSOLUTE deadline; the agent's own bound is
            # that deadline plus the time Foundation needs to stop and remove the
            # container, so Foundation's `finally` always runs first.
            now = int(self.epoch())
            lease_end = acquired_at + lease.ttl_seconds
            reserve = JOB_STOP_MARGIN + JOB_TERM_GRACE + JOB_TEARDOWN_RESERVE
            # The deadline always binds: never later than the lease allows.
            deadline = min(now + job.timeout_seconds, lease_end - reserve)
            if deadline <= now:
                raise Refusal("lease_unavailable")  # setup used the whole lease
            result = self.runner.run(
                RunSpec(
                    dispatch_id=materials.dispatch_id,
                    target_ref=materials.target_ref,
                    product=self.config.product,
                    plan_digest=materials.plan_digest,
                    params_env=params_file,
                    agent_config=self.config.config_path,
                    bindings_env=self.config.bindings_env,
                    credentials_directory=str(credentials_dir),
                    lease_id=lease.lease_id,
                    job_deadline=deadline,
                    job_bound_seconds=deadline - now + JOB_STOP_MARGIN,
                    job_name=job.name,
                    job_params_env=job_params,
                    job_secrets_dir=secrets_dir,
                    job_argv_digest=job.argv_digest,
                    job_secret_files=secret_files,
                    job_render_invocation=invocation,
                )
            )
            receipt["exit_code"] = result.exit_code
            # Foundation's own receipt (the last JSON line of its stdout), or why not.
            # (details.foundation, not details.job.foundation: nested one level less,
            # so its `inputs` items stay inside Control's depth limit; R5-H2.)
            receipt_ok = False
            if result.receipt is not None:
                details["foundation"] = result.receipt
                receipt_ok = foundation_receipt_matches(
                    result.receipt,
                    dispatch_id=materials.dispatch_id,
                    job=job.name,
                    render_invocation=invocation,
                )
                if not receipt_ok:
                    receipt["foundation_receipt"] = (
                        "mismatch"  # another run's, or a refusal
                    )
            else:
                receipt["foundation_receipt"] = result.receipt_error or "missing"
            details["runner"] = {
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
            if result.timed_out:
                outcome, reason = "failed", "runner_timeout"
            elif result.exit_code != 0:
                outcome = "failed"
            elif not receipt_ok:
                # exit 0 with no readable receipt of THIS run cannot be trusted
                outcome, reason = "failed", "job_receipt_invalid"
        except Refusal as refusal:
            outcome = "failed" if ran else "refused"
            reason = refusal.code
            if refusal.detail is not None:
                details["refusal_detail"] = refusal.detail
        finally:
            # P-SEC rev7: a surviving job container does NOT stop the purge: the
            # files go and the accessor is destroyed regardless. It only HOLDS the
            # lease (never released: it expires unreleased) and pages. The receipt
            # says `orphan_container` and never claims the container was contained.
            orphaned = False
            if ran:
                # Remove THIS dispatch's own container (label-scoped `docker rm -f`;
                # it is recorded in the journal) and prove it again. A container that
                # was there means the run failed and pages; the lease is released only
                # if nothing REMAINS, and held otherwise.
                found, orphaned, _foreign, _removed = self._remove_owned_containers(
                    materials.dispatch_id
                )
                if found:
                    outcome, reason = "failed", "orphan_job_container"
                    receipt["orphan_removed"] = not orphaned
                    details["page"] = True
                    if orphaned:
                        receipt["orphan_container"] = True
            if job.role is not None:
                # Purge first (the agent's secret_id, the purge unit, the accessor),
                # whatever happened above; the receipt is written only after.
                try:
                    receipt["accessors_still_tracked"] = self.purge_job_role(job.role)
                except Refusal as refusal:
                    receipt["purge_failed"] = refusal.code
            remove_runtime_files(deploy_run_dir, (PARAMS_FILE, JOB_PARAMS_FILE))
            shutil.rmtree(self._run_credentials_dir(), ignore_errors=True)
            if lease is not None and not orphaned:
                details["lease_released"] = self._release(lease, host_mutated=ran)
            elif orphaned:
                details["lease_held"] = True  # never released: it expires unreleased
            if ran:
                clear_inflight(self.config.state_dir)
            if lock is not None:
                os.close(lock)
            receipt["finished_at"] = utc_stamp(self.clock())
        # A job receipt is never a deploy observation (it cannot read as convergence).
        self._report(
            self._observation(
                materials.dispatch_id, outcome, reason, details, kind="job"
            )
        )
        return CycleResult(outcome, reason, materials.dispatch_id)

    # -- orphaned job containers (architect r5 ruling, option b) ---------------------
    def _labelled(self, dispatch_id: str | None) -> list[tuple[str, str]] | None:
        """`(container id, dispatch label)` for every container carrying BOTH job
        labels (`docker ps -a` lists the stopped ones too), restricted to one dispatch
        when given. None when the listing cannot be read or any id/label is malformed:
        fail closed, and nothing is then ever removed."""
        argv = [str(self.config.docker_path), "ps", "-a"]
        dispatch_filter = JOB_DISPATCH_LABEL + (
            f"={dispatch_id}" if dispatch_id else ""
        )
        for expression in (f"label={dispatch_filter}", f"label={JOB_MARKER_LABEL}"):
            argv += ["--filter", expression]
        argv += ["--format", '{{.ID}} {{.Label "' + JOB_DISPATCH_LABEL + '"}}']
        try:
            result = self.read_only.run(tuple(argv), timeout=15.0)
        except Refusal:
            return None
        if result.exit_code != 0:
            return None
        found: list[tuple[str, str]] = []
        for line in result.stdout.decode("ascii", errors="replace").splitlines():
            parts = line.split()
            if (
                len(parts) != 2
                or not _CONTAINER_ID.fullmatch(parts[0])
                or safe_id(parts[1]) != parts[1]
            ):
                return None
            found.append((parts[0], parts[1]))
        return found

    def _is_mine(self, dispatch_label: str) -> bool:
        """Only a dispatch this agent recorded itself (executed journal, or the
        inflight record) is ever cleaned up: other products' agents share the daemon."""
        return (
            self.executed.seen(dispatch_label)
            or read_inflight(self.config.state_dir) == dispatch_label
        )

    def _remove_owned_containers(
        self, dispatch_id: str | None = None
    ) -> tuple[bool, bool, bool, bool]:
        """`(found, remains, foreign, removed)`. THE one place that removes a container:
        `docker rm -f <ids>` (an argument list, no shell, no `-v`). The ids come only
        from the label listing AND only for a dispatch this agent recorded itself; any
        other labelled container is left alone and reported as `foreign`. An unreadable
        listing is found and remaining. It then lists again to PROVE what is left."""
        listing = self._labelled(dispatch_id)
        if listing is None:
            return True, True, False, False
        if not listing:
            return False, False, False, False
        mine = [cid for cid, label in listing if self._is_mine(label)]
        if mine:
            try:
                self.commands.run(
                    (str(self.config.docker_path), "rm", "-f", *mine), timeout=60.0
                )
            except Refusal:
                pass  # re-proved below
        again = self._labelled(dispatch_id)
        remains = again is None or any(self._is_mine(label) for _, label in again)
        foreign = any(not self._is_mine(label) for _, label in listing)
        return True, remains, foreign, bool(mine) and not remains

    def _clear_orphans(self) -> tuple[bool, bool, bool, bool] | None:
        """Agent start and before every lease: remove this agent's own orphans, and ONLY
        while holding the role-job lock. Returns None (the distinct BUSY state) when the
        lock is held: a role job may be running, so nothing is removed and nothing is
        reported as an orphan."""
        try:
            descriptor = self._lock_role_job()
        except Refusal:
            return None
        try:
            return self._remove_owned_containers()
        finally:
            os.close(descriptor)

    def _mint_job_role(self, materials: Materials, role: str) -> str:
        # The authored config was checked by `job_config_stem` before anything.
        session = self._bao_session()
        try:
            return deliver_job_secret_id(
                session,
                self.config,
                role,
                self.job_accessors,
                dispatch_id=materials.dispatch_id,
            )
        finally:
            session.revoke()

    def _release(
        self,
        lease: Lease,
        *,
        host_mutated: bool,
        details: dict[str, object] | None = None,
    ) -> bool:
        """Best effort: a lease that is not released simply expires, and Control
        then asks for a reconcile before the next acquire (EXPIRED_UNRELEASED)."""
        try:
            self.lease.release(lease, host_mutated=host_mutated)
        except Refusal as refusal:
            if details is not None and refusal.detail is not None:
                details["lease_release_refusal"] = refusal.detail
            return False
        return True

    @staticmethod
    def _classify(result: RunResult, post: PostRun) -> tuple[str, str | None]:
        if post is PostRun.INTEGRITY_VIOLATION:
            return "integrity_violation", "integrity_violation"
        if result.timed_out:
            return "failed", "runner_timeout"
        if result.exit_code != 0:
            return "failed", None
        return "completed", None

    def _deliver(
        self, materials: Materials, details: dict[str, object]
    ) -> tuple[Delivered, ...]:
        if not materials.roles:
            return ()
        # The authored configs are root-installed; the agent checks and reports them.
        details["openbao_config"] = check_authored_configs(
            self.config,
            self.config.secrets_namespace,
            materials.roles,
            required_uid=self.root_uid,
        )
        session = self._bao_session()
        try:
            return deliver_secret_ids(
                session,
                self.config,
                materials.roles,
                self.commands,
                store=self.accessors,
                dispatch_id=materials.dispatch_id,
                clock=self.epoch,
            )
        finally:
            session.revoke()  # the 5 minute token never outlives the run


def _credential_text(environ: Mapping[str, str], name: str) -> str:
    return (
        read_credential(environ, name, code="secret_delivery_failed")
        .decode("utf-8")
        .strip()
    )


def _detail(refusal: Refusal) -> dict[str, object]:
    return {} if refusal.detail is None else {"refusal_detail": refusal.detail}
