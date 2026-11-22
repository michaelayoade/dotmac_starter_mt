"""One launch cycle: fetch, lease, transfer, render, deliver, run, classify, report.

The agent decides nothing. Every check below is a refusal of something Control
or the bindings already bound; any refusal stops the cycle before Foundation is
started and is reported as a refused observation.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .client import Reply, TargetClient
from .commands import Commands
from .config import AgentConfig, refuse_db_environment
from .delivery import Delivered, deliver_secret_ids
from .dispatch import read_identity
from .files import (
    ensure_private_dir,
    ensure_traversable_dir,
    require_private_dir,
    write_private,
)
from .lease import EXPIRED_UNRELEASED, Lease, LeaseClient
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
from .openbao import OpenBaoSession
from .openbao_config import check_authored_configs
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
from .runner import Runner, RunResult, RunSpec
from .systemd_creds import read_credential
from .transfer import Transfer
from .transport import Transport


@dataclass(frozen=True)
class CycleResult:
    # idle | completed | refused | failed | integrity_violation | reconcile_reported
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
        self.outbox = Outbox(config.state_dir)

    # -- housekeeping ------------------------------------------------------
    def _prepare_dirs(self) -> None:
        ensure_traversable_dir(self.config.run_dir)
        ensure_private_dir(self.config.deploy_run_dir)
        ensure_private_dir(self.config.inbox_dir)

    def _clean_stale(self) -> None:
        """A file left by a crashed run is removed before anything else (s2.5.5)."""
        remove_runtime_files(self.config.deploy_run_dir, (PARAMS_FILE,))
        remove_legacy_copy(self.config.deploy_run_dir)

    def flush_outbox(self) -> None:
        for path in self.outbox.pending():
            try:
                document = json.loads(self.outbox.read(path))
                self.client.post_observation(document)
            except (Refusal, ValueError):
                return  # keep it; the next cycle retries
            self.outbox.acknowledge(path)

    def _report(self, observation: dict[str, object]) -> None:
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

    # -- the cycle ---------------------------------------------------------
    def run_once(self) -> CycleResult:
        refuse_db_environment(self.environ)
        self._prepare_dirs()
        self._clean_stale()
        self._recover_interrupted()
        self.flush_outbox()
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
            self._report(self._observation(dispatch_id, "refused", refusal.code, {}))
            return CycleResult("refused", refusal.code, dispatch_id)
        try:
            standing = self.lease.standing()
        except Refusal as refusal:
            self._report(
                self._observation(
                    materials.dispatch_id, "refused", refusal.code, _detail(refusal)
                )
            )
            return CycleResult("refused", refusal.code, materials.dispatch_id)
        if standing == EXPIRED_UNRELEASED:
            return self._reconcile(materials.dispatch_id, standing)
        return self._execute(materials, reply)

    def _reconcile(self, dispatch_id: str, standing: str) -> CycleResult:
        """Read-only: report what is running and stop. Control issues the marker."""
        try:
            observation = reconcile_observation(
                self.config,
                self.read_only,
                dispatch_id=dispatch_id,
                standing=standing,
                clock=self.clock,
            )
        except Refusal as refusal:
            self._report(self._observation(dispatch_id, "refused", refusal.code, {}))
            return CycleResult("refused", refusal.code, dispatch_id)
        self._report(observation)
        return CycleResult("reconcile_reported", None, dispatch_id)

    def _observation(
        self,
        dispatch_id: str,
        outcome: str,
        reason: str | None,
        details: dict[str, object],
    ) -> dict[str, object]:
        return build_observation(
            kind="deploy",
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
        try:
            refuse_preexisting_overrides(self.config.deploy_dir)
            planned = canonical_render(materials.params, materials.app_image)
            if render_digest(planned) != materials.params_env_digest:
                raise Refusal("params_digest_mismatch")
            require_private_dir(deploy_run_dir)

            # Order (DESIGN-rev3 2.6.2): acquire, transfer, render, deliver, run.
            lease = self.lease.acquire(
                dispatch_id=materials.dispatch_id,
                ttl_seconds=materials.lease_ttl_seconds,
            )
            details["lease"] = {
                "lease_id": lease.lease_id,
                "ttl_seconds": lease.ttl_seconds,
            }
            transferred = self.transfer.transfer_all(
                materials.images, ttl_seconds=lease.ttl_seconds
            )
            details["transfer"] = [t.as_details() for t in transferred]
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

            delivered = self._deliver(materials, details)
            details["delivered_roles"] = [
                {
                    "role": d.role,
                    "single_use": d.single_use,
                    "restarted": d.restarted,
                    "old_accessors_destroyed": d.old_accessors_destroyed,
                }
                for d in delivered
            ]

            if not self.lease.covers(lease, dispatch_id=materials.dispatch_id):
                raise Refusal("lease_unavailable")
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
                    credentials_directory=self.environ.get("CREDENTIALS_DIRECTORY"),
                    legacy_env=legacy_file,
                    lease_id=lease.lease_id,
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
            if lease is not None:
                details["lease_released"] = self._release(lease, host_mutated=ran)
            if ran:
                clear_inflight(self.config.state_dir)
        self._report(self._observation(materials.dispatch_id, outcome, reason, details))
        return CycleResult(outcome, reason, materials.dispatch_id)

    def _release(self, lease: Lease, *, host_mutated: bool) -> bool:
        """Best effort: a lease that is not released simply expires."""
        try:
            self.lease.release(lease, host_mutated=host_mutated)
        except Refusal:
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
        if self.bao_transport is None:
            raise Refusal("secret_delivery_failed")
        # The authored configs are root-installed; the agent checks and reports them.
        if not materials.secrets_namespace or not materials.secrets_environment:
            raise Refusal("materials_invalid", "bad_secrets_name")
        details["openbao_config"] = check_authored_configs(
            self.config,
            materials.secrets_namespace,
            materials.roles,
            required_uid=self.root_uid,
        )
        role_id = _credential_text(
            self.environ, self.config.delivery_role_id_credential
        )
        secret_id = _credential_text(
            self.environ, self.config.delivery_secret_id_credential
        )
        session = OpenBaoSession(self.bao_transport, materials.secrets_environment)
        try:
            session.login(role_id, secret_id)
            return deliver_secret_ids(
                session,
                self.config,
                materials.roles,
                self.commands,
                secrets_namespace=materials.secrets_namespace,
                secrets_environment=materials.secrets_environment,
                dispatch_id=materials.dispatch_id,
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
