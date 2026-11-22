"""One launch cycle: fetch, verify, render, deliver, run Foundation, classify, report.

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
from .config import AgentConfig, refuse_db_environment
from .delivery import Delivered, deliver_secret_ids
from .files import ensure_private_dir, require_private_dir
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
from .openbao import OpenBaoClient
from .openbao_agent import install_agent_config
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
from .refusal import Refusal
from .runner import Runner, RunResult, RunSpec


@dataclass(frozen=True)
class CycleResult:
    outcome: str  # idle | completed | refused | failed | integrity_violation
    reason: str | None = None
    dispatch_id: str | None = None


class Launcher:
    def __init__(
        self,
        config: AgentConfig,
        client: TargetClient,
        runner: Runner,
        openbao: OpenBaoClient | None,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.client = client
        self.runner = runner
        self.openbao = openbao
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        self.clock = clock
        self.outbox = Outbox(config.state_dir)

    # -- housekeeping ------------------------------------------------------
    def _prepare_dirs(self) -> None:
        ensure_private_dir(self.config.run_dir)
        ensure_private_dir(self.config.deploy_run_dir)
        ensure_private_dir(self.config.openbao_agent_config_dir)

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
            materials = parse_materials(reply.document, self.config)
        except Refusal as refusal:
            dispatch_id = safe_id(reply.document.get("dispatch_id"))
            self._report(self._observation(dispatch_id, "refused", refusal.code, {}))
            return CycleResult("refused", refusal.code, dispatch_id)
        return self._execute(materials, reply)

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
        try:
            refuse_preexisting_overrides(self.config.deploy_dir)
            planned = canonical_render(materials.params, materials.app_image)
            if render_digest(planned) != materials.params_env_digest:
                raise Refusal("params_digest_mismatch")
            require_private_dir(deploy_run_dir)
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
                    binding_names={n.name for r in materials.bindings for n in r.names},
                )
                details["legacy"] = {
                    "version": materials.legacy.version,
                    "sha256": materials.legacy.sha256,
                    "names": list(materials.legacy.names),
                }

            delivered = self._deliver(materials)
            details["delivered_roles"] = [
                {"role": d.role, "single_use": d.single_use} for d in delivered
            ]

            mark_inflight(self.config.state_dir, materials.dispatch_id)
            ran = True
            result = self.runner.run(
                RunSpec(
                    dispatch_id=materials.dispatch_id,
                    target_ref=materials.target_ref,
                    product=self.config.product,
                    params_env=params_file,
                    legacy_env=legacy_file,
                )
            )
            details["runner"] = {
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
            post = classify_post_run(
                params_path(deploy_run_dir),
                planned_render=planned,
                previous_app_image=materials.previous_app_image,
            )
            details["params_env_post_run"] = post.value
            outcome, reason = self._classify(result, post)
        except Refusal as refusal:
            outcome = "failed" if ran else "refused"
            reason = refusal.code
        finally:
            remove_runtime_files(deploy_run_dir, (PARAMS_FILE,))
            remove_legacy_copy(deploy_run_dir)
            if ran:
                clear_inflight(self.config.state_dir)
        self._report(self._observation(materials.dispatch_id, outcome, reason, details))
        return CycleResult(outcome, reason, materials.dispatch_id)

    @staticmethod
    def _classify(result: RunResult, post: PostRun) -> tuple[str, str | None]:
        if post is PostRun.INTEGRITY_VIOLATION:
            return "integrity_violation", "integrity_violation"
        if result.timed_out:
            return "failed", "runner_timeout"
        if result.exit_code != 0:
            return "failed", None
        return "completed", None

    def _deliver(self, materials: Materials) -> tuple[Delivered, ...]:
        if not materials.bindings:
            return ()
        if self.openbao is None:
            raise Refusal("secret_delivery_failed")
        for role in materials.bindings:
            install_agent_config(self.config, role)
        return deliver_secret_ids(self.openbao, materials.bindings, self.config.run_dir)
