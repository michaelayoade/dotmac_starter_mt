"""Lane 3, the TARGET half: the session that runs on the host being mutated.

E7 / T1 (Gate 2, 0.4.0a2; Michael's Q2 ruling: target-side execution). The
controller-side runner, `exposure_rehearsal_runner.run`, did everything in one
process on the runner VM and reached the target over SSH. Foundation's contracts
were written for the other placement (E3 audit, section 2): the lock, the
installed-artifact self-check and the effects are all host-local. This module is
the half that runs there, launched by the launcher over the controller's one
SSH seam, in the candidate's isolated venv (`-E -P`).

## What this half does

1. takes the start parameters from the launcher over the framed side channel
   (`lane3_session_channel`), never from the environment or the command line;
2. runs the read-only prestate and the V3 plan, then the placement self-check,
   then the authorization (the Control dispatch is consumed there, so a process
   on the wrong host never burns one);
3. reads the launcher's read-only COPY of the lease and re-checks it, takes the
   product lock in the target's `/var/lock`, and drives the one Executor;
4. measures items 1-10 locally, then PAUSES with the stack up (T2) so the
   launcher can measure items 11-16 from vantages only it holds;
5. hands the serialized exposure calls to the launcher, which encrypts them
   with the probe bytes only it holds (T3), and assembles the receipt in THIS
   process, because `build_receipt_v2` needs the live `ExecutionGrant` and
   `DeploymentOutcome`, neither of which can cross a process boundary;
6. reports a terminal outcome. It never writes a release: the release principal
   is the GitHub workload, which only the launcher can prove (T4).

## What it deliberately keeps

`require_build_effects=True`. The CP bindings supply the thin target-side
factory, so no control is relaxed. The exposure effects are Foundation's own
`ComposeHostExposureEffects` over a local runner.

## Why it imports from the controller-side runner

The refusal classes, `classify_refusal`, `Results`, `TerminalContext`,
`CapturingRunner` and `judge_proxy_recreation` have exactly one definition, and
the terminal vocabulary is closed and Foundation-owned. A second copy would be a
second answer to "which refusal is this". The controller-side `run` stays as it
is until E11 proves this path.

## Output discipline

Nothing here writes to stdout. `Channel.from_stdio` moves the protocol onto
private descriptors and points stdout at stderr before anything else runs.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any, Final

# See `exposure_rehearsal_runner`: with `-E -P` the script's directory is not
# on `sys.path`, so the sibling modules are resolved from this file's own.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_receipt_v2 as receipt_v2
from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.engine.lock import deployment_lock
from dotmac_deployment_foundation.engine.run import bind_authorized_effects
from dotmac_deployment_foundation.errors import (
    DeploymentFoundationError,
    SpecError,
)
from dotmac_deployment_foundation.exposure import (
    refuse_non_recreating_apply,
    verify_exposure,
)
from dotmac_deployment_foundation.lease import load_lease
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.providers.exposure_host import (
    ComposeHostExposureEffects,
)
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RequirementResult,
    RequirementStatus,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from lane3_authorization import (
    AuthorizationUnverifiable,
    establish_authorization,
    prepare_authorization,
)
from lane3_exposure_evidence import (
    ExposureEvidenceRefused,
    require_exposure_join,
    serialize_exposure_calls,
)
from lane3_exposure_recording import RecordingExposureEffects
from lane3_session_channel import Channel, ChannelRefused
from lane3_target_runner import (
    PlacementRefused,
    local_runner,
    placement_self_check,
    read_machine_id,
)

EXIT_OK = controller_half.EXIT_OK
EXIT_REFUSED = controller_half.EXIT_REFUSED
EXIT_INDETERMINATE = controller_half.EXIT_INDETERMINATE

PASSED = RequirementStatus.EXECUTED_PASSED
FAILED = RequirementStatus.EXECUTED_FAILED
BLOCKED = RequirementStatus.BLOCKED

#: Items 11-16: the rows only the launcher can measure. A closed set, so the
#: launcher can neither omit one nor supply a row for an item this half owns.
EXTERNAL_CODES: Final = frozenset(
    {
        "external_positive_v6",
        "external_negative_v6",
        "external_v4",
        "closed_port_behaviour",
        "private_from_source",
        "privileged_vantage_refused",
    }
)

#: The product lock lives where every other deployer on this host looks for it.
LOCK_ROOT: Final = pathlib.PurePosixPath("/var/lock")

START_TIMEOUT_SECONDS: Final = 60.0
MAX_CIPHERTEXT_BYTES: Final = 48 * 1024 * 1024

_ROW_KEYS: Final = frozenset({"code", "status", "detail", "evidence"})
_RUN_KEYS: Final = frozenset({"repository_id", "run_id", "run_attempt"})
_MAX_DETAIL: Final = 4000
_MAX_EVIDENCE_ITEMS: Final = 16
_MAX_EVIDENCE_TEXT: Final = 500
_MAX_TOKEN: Final = 256

_Unfit = controller_half.PreconditionUnfit


@dataclasses.dataclass(frozen=True, slots=True)
class Start:
    """The launcher's start parameters, parsed. Every field is required."""

    foundation_revision: str
    candidate_source_revision: str
    foundation_artifact: str
    authorization_run: str
    controller_identity: str
    host_id: str
    candidate_version: str
    descriptor: pathlib.Path
    authorization_document: pathlib.Path
    deploy_dir: pathlib.Path
    lock_dir: pathlib.Path
    lease_dir: pathlib.Path
    timeout: int
    probe_vantage_ref: str
    execution_run: ExecutionRunBindingV1
    expected_machine_id: str
    expected_host_incarnation: str
    window_seconds: int
    evidence_seconds: int


def _token(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TOKEN
        or any(ord(c) < 0x21 or ord(c) > 0x7E for c in value)
    ):
        raise _Unfit(f"start field {key} is not usable")
    return value


def _bounded_int(data: dict[str, Any], key: str, *, low: int, high: int) -> int:
    value = data[key]
    if type(value) is not int or not low <= value <= high:
        raise _Unfit(f"start field {key} is out of range")
    return value


def _absolute(data: dict[str, Any], key: str) -> pathlib.Path:
    text = _token(data, key)
    path = pathlib.PurePosixPath(text)
    if not path.is_absolute() or ".." in path.parts:
        raise _Unfit(f"start field {key} is not a normalized absolute path")
    return pathlib.Path(text)


def parse_start(data: dict[str, Any]) -> Start:
    """The closed `start` frame as typed values, or a `PreconditionUnfit`."""
    run = data["execution_run"]
    if not isinstance(run, dict) or set(run) != _RUN_KEYS:
        raise _Unfit("start field execution_run is not closed")
    with controller_half.refusal_of(_Unfit):
        binding = ExecutionRunBindingV1(
            repository_id=run["repository_id"],
            run_id=run["run_id"],
            run_attempt=run["run_attempt"],
        )
    lock_dir = _absolute(data, "lock_dir")
    if LOCK_ROOT not in pathlib.PurePosixPath(lock_dir).parents:
        raise _Unfit("the product lock must live under the target's /var/lock")
    return Start(
        foundation_revision=_token(data, "foundation_revision"),
        candidate_source_revision=_token(data, "candidate_source_revision"),
        foundation_artifact=_token(data, "foundation_artifact"),
        authorization_run=_token(data, "authorization_run"),
        controller_identity=_token(data, "controller_identity"),
        host_id=_token(data, "host_id"),
        candidate_version=_token(data, "candidate_version"),
        descriptor=_absolute(data, "descriptor"),
        authorization_document=_absolute(data, "authorization_document"),
        deploy_dir=_absolute(data, "deploy_dir"),
        lock_dir=lock_dir,
        lease_dir=_absolute(data, "lease_dir"),
        timeout=_bounded_int(data, "timeout", low=1, high=3600),
        probe_vantage_ref=_token(data, "probe_vantage_ref"),
        execution_run=binding,
        expected_machine_id=_token(data, "expected_machine_id"),
        expected_host_incarnation=_token(data, "expected_host_incarnation"),
        window_seconds=_bounded_int(data, "window_seconds", low=1, high=1800),
        evidence_seconds=_bounded_int(data, "evidence_seconds", low=1, high=600),
    )


def parse_external_rows(data: dict[str, Any]) -> list[RequirementResult]:
    """The launcher's six rows, closed: exactly items 11-16, each once.

    The launcher measured these from vantages this host never held, so this half
    cannot judge them. It only refuses anything that is not the closed shape: a
    row for an item it owns, a duplicate or a missing one would each let a result
    be chosen rather than measured.
    """
    rows = data["rows"]
    if not isinstance(rows, list) or len(rows) != len(EXTERNAL_CODES):
        raise controller_half.EvidenceIncomplete(
            "the external results are not exactly the six launcher-measured items"
        )
    statuses = {status.value: status for status in RequirementStatus}
    parsed: dict[str, RequirementResult] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise controller_half.EvidenceUnreadable("an external row is not closed")
        code, status = row["code"], row["status"]
        detail, evidence = row["detail"], row["evidence"]
        if (
            not isinstance(code, str)
            or code not in EXTERNAL_CODES
            or code in parsed
            or not isinstance(status, str)
            or status not in statuses
            or not isinstance(detail, str)
            or not detail.strip()
            or len(detail) > _MAX_DETAIL
            or not isinstance(evidence, list)
            or len(evidence) > _MAX_EVIDENCE_ITEMS
            or any(
                not isinstance(item, str) or len(item) > _MAX_EVIDENCE_TEXT
                for item in evidence
            )
        ):
            raise controller_half.EvidenceUnreadable(
                "an external row is outside the closed vocabulary"
            )
        parsed[code] = RequirementResult(
            code=code,
            status=statuses[status],
            detail=detail,
            evidence=tuple(evidence),
        )
    return list(parsed.values())


def _decode_b64(value: object, *, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > limit * 2:
        raise controller_half.EvidenceUnreadable("a base64 member is unusable")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise controller_half.EvidenceUnreadable("a base64 member is invalid") from None
    if not raw or len(raw) > limit:
        raise controller_half.EvidenceUnreadable("a base64 member is out of bounds")
    return raw


class Session:
    """One run's state, held so a terminal report can be made from any refusal."""

    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        self.ctx = controller_half.TerminalContext()
        self.started = datetime.now(UTC).isoformat()
        self.machine_id = ""
        self.receipt_digest = ""

    def run(self, start: Start) -> None:
        ctx, channel = self.ctx, self.channel
        results = controller_half.Results()

        with controller_half.refusal_of(_Unfit):
            controller_identity = ControllerSshFingerprintV1.parse(
                start.controller_identity, field="controller_identity"
            )
            controller_half.require_commit(
                start.foundation_revision, field="foundation_revision"
            )
            controller_half.require_commit(
                start.candidate_source_revision, field="candidate_source_revision"
            )
        ctx.controller_identity = controller_identity

        fixture_bytes = start.descriptor.read_bytes()
        spec = ProductDeploymentSpec.loads(
            fixture_bytes.decode("utf-8"), source=str(start.descriptor)
        )
        descriptor_digest = spec.to_canonical_document().sha256_digest()

        # Remote execution needs the trusted factory: never a local-effects
        # fallback, and never a relaxed `require_build_effects`.
        with controller_half.receipt_v2_refusals(refused_as=_Unfit):
            prepared = prepare_authorization(
                authorization_document=start.authorization_document,
                require_build_effects=True,
            )
        bindings = prepared.bindings

        with (
            controller_half.receipt_v2_refusals(refused_as=_Unfit),
            controller_half.refusal_of(_Unfit),
        ):
            base_plan, deployment_plan, deployment_effects = receipt_v2.build_base_plan(
                spec,
                target=start.host_id,
                deploy_dir=start.deploy_dir,
                bindings=bindings,
            )
            execution_plan = receipt_v2.acquire_execution_plan(
                base_plan, bindings=bindings, spec=spec
            )
            # BEFORE the grant: authorization consumes the Control dispatch, and
            # a process on the wrong host must not burn one.
            self.machine_id = self._placement(execution_plan, start)
            grant = establish_authorization(
                descriptor_digest=descriptor_digest,
                target=start.host_id,
                authorization_document=None,
                execution_plan=execution_plan,
                prepared=prepared,
            )
            if execution_plan.controller_ssh_fingerprint != str(controller_identity):
                raise _Unfit("the authorized plan does not name the controller")
            if not execution_plan.exposure_reconciliations:
                raise _Unfit(
                    "Lane 3 exposure rehearsal is blocked: the trusted plan has "
                    "no exposure reconciliations; no deployment or provocation ran"
                )
        ctx.vm_installation_id = self.machine_id

        # The launcher's READ-ONLY copy. The release is the launcher's to write.
        lease = load_lease(start.host_id, directory=str(start.lease_dir))
        lease.covers(
            now=datetime.now(UTC), authorization_run_id=start.authorization_run
        )
        if controller_identity != lease.controller_identity_fingerprint:
            raise _Unfit("the controller identity does not match the held lease")
        ctx.lease = lease

        channel.send(
            "ready",
            {
                "host_id": start.host_id,
                "machine_id": self.machine_id,
                "plan_digest": str(execution_plan.digest()),
            },
        )

        with deployment_lock(
            spec.product,
            label=f"lane3 rehearsal {deployment_plan.image_digest}",
            directory=str(start.lock_dir),
        ) as held:
            held.require_held(product=spec.product)
            controller = controller_half.CapturingRunner(local_runner)
            effects = ComposeHostExposureEffects(
                spec,
                deploy_dir=str(start.deploy_dir),
                runner=controller,
                timeout_seconds=start.timeout,
            )
            executor_exposure = RecordingExposureEffects(effects, ctx.exposure_calls)
            try:
                execution_outcome = self._execute(
                    start,
                    results,
                    spec=spec,
                    descriptor_digest=descriptor_digest,
                    execution_plan=execution_plan,
                    grant=grant,
                    deployment_plan=deployment_plan,
                    deployment_effects=deployment_effects,
                    executor_exposure=executor_exposure,
                    effects=effects,
                    bindings=bindings,
                    held=held,
                    lease=lease,
                    controller_identity=controller_identity,
                )
            finally:
                # Best effort, after the verdict, under the same lock. Item 8 is
                # blocked and seeds nothing, so today these record "not
                # attempted"; they exist so a future seeding act is unwound here.
                controller_half.run_cleanup(ctx, controller, effects, ())

        self._finish(
            start,
            results,
            fixture_bytes=fixture_bytes,
            descriptor_digest=descriptor_digest,
            execution_plan=execution_plan,
            grant=grant,
            execution_outcome=execution_outcome,
            lease_run=lease.authorization_run_id,
            controller_identity=controller_identity,
        )

    def _placement(self, execution_plan: Any, start: Start) -> str:
        try:
            machine_id = read_machine_id()
            placement_self_check(
                plan_host_id=str(execution_plan.host_id),
                plan_host_incarnation=str(execution_plan.host_incarnation),
                expected_host_id=start.host_id,
                expected_host_incarnation=start.expected_host_incarnation,
                expected_machine_id=start.expected_machine_id,
                machine_id=machine_id,
            )
        except PlacementRefused as exc:
            raise _Unfit(f"placement self-check refused: {exc}") from None
        return machine_id

    def _execute(
        self,
        start: Start,
        results: Any,
        *,
        spec: Any,
        descriptor_digest: Any,
        execution_plan: Any,
        grant: Any,
        deployment_plan: Any,
        deployment_effects: Any,
        executor_exposure: Any,
        effects: Any,
        bindings: Any,
        held: Any,
        lease: Any,
        controller_identity: Any,
    ) -> Any:
        ctx = self.ctx

        refused = False
        try:
            refuse_non_recreating_apply(["restart"])
        except DeploymentFoundationError:
            refused = True
        results.record(
            "non_recreating_refused",
            PASSED if refused else FAILED,
            "`docker compose restart` refused by the real controller path"
            if refused
            else "a non-recreating apply was NOT refused",
            "refuse_non_recreating_apply(['restart'])",
        )

        snapshot = effects.observe()
        results.record(
            "pre_change_snapshot",
            PASSED,
            f"{len(snapshot.sockets)} sockets, {len(snapshot.chains)} chains "
            "captured before mutation",
            "effects.observe() before any mutation",
        )

        bind_authorized_effects(deployment_effects, execution_plan)

        # Recheck the held lease and identity immediately before the only
        # mutating authority; the Executor keeps its own prestate drift guard.
        lease.covers(
            now=datetime.now(UTC), authorization_run_id=start.authorization_run
        )
        if (
            execution_plan.host_id != start.host_id
            or execution_plan.controller_ssh_fingerprint != str(controller_identity)
            or controller_identity != lease.controller_identity_fingerprint
        ):
            raise SpecError(
                "the authorized plan does not name the leased host/controller"
            )
        held.require_held(product=spec.product)
        with controller_half.receipt_v2_refusals(refused_as=SpecError):
            ctx.host_mutated = True
            execution_outcome = receipt_v2.execute_authorized_plan(
                execution_plan,
                grant,
                spec=spec,
                deployment_plan=deployment_plan,
                effects=deployment_effects,
                exposure_effects=executor_exposure,
                bindings=bindings,
                lock=held,
            )

        # Items 1 and 8 stay BLOCKED for the reasons recorded in the
        # controller-half runner: the apply and its compensation live in the
        # Executor under a grant, and this lane neither orders effects itself
        # nor seeds a shared host to rehearse something that will not run.
        results.record(
            "apply_under_lock",
            BLOCKED,
            "the exposure apply moved to `Executor._reconcile_exposure`, driven "
            "from `FoundationExecutionPlanV3.exposure_reconciliations`. This lane "
            "has no trusted CP-rendered V3 plan and Control V2 pair, so no "
            "ExecutionGrant names this act. "
            "Foundation cannot mint one and this runner will not order the "
            "effects itself: either would rebuild the second executor boundary 1 "
            "removed",
            f"project={spec.product}",
        )

        observed = effects.observe()
        sockets = {(s.address, s.port) for s in observed.sockets}
        wildcards = ("0.0.0.0", "::", "*")  # noqa: S104 - a refusal predicate
        wildcard = [p for a, p in sockets if a in wildcards]
        results.record(
            "socket_reobservation",
            PASSED if not wildcard else FAILED,
            f"{len(sockets)} sockets re-observed; wildcard binds: {wildcard or 'none'}",
            "ss -tlnp",
        )
        results.record(
            "none_emits_no_socket",
            PASSED if not any(p == 18444 for _a, p in sockets) else FAILED,
            'the exposure = "none" port emits no socket',
            "ss -tlnp",
        )
        proxy_status, proxy_detail = controller_half.judge_proxy_recreation(
            snapshot.proxies, observed.proxies
        )
        results.record(
            "proxy_reobservation", proxy_status, proxy_detail, "ps -eo pid,args"
        )

        planned = build_firewall_plan(spec)
        landed = []
        for rule in planned:
            chain = observed.chain(rule.family, rule.chain)
            landed.append(bool(chain and chain.rules_for(rule.host_port)))
        terminal_drop = any(rule.action == "DROP" and rule.terminal for rule in planned)
        firewall_ok = bool(planned) and all(landed) and terminal_drop
        results.record(
            "firewall_reobservation",
            PASSED if firewall_ok else FAILED,
            f"{len(planned)} derived rules; landed={sum(landed)}/{len(planned)}; "
            f"terminal DROP={terminal_drop}",
            "iptables-save",
            "ip6tables-save",
        )

        v6_docker_user = observed.chain("ipv6", "DOCKER-USER")
        results.record(
            "inert_v6_chain",
            PASSED if v6_docker_user is not None else BLOCKED,
            "ip6tables DOCKER-USER captured; a v6 rule there is inert because the "
            "chain is jumped only from FORWARD while a v6 publish terminates on "
            "INPUT"
            if v6_docker_user is not None
            else "the ip6tables DOCKER-USER chain could not be read",
            "ip6tables -L DOCKER-USER -v -n",
        )
        results.record(
            "provoked_rollback",
            BLOCKED,
            "the compensation moved to `Executor._restore_exposure` and is "
            "reached only through an authorized `FoundationExecutionPlanV3` "
            "carrying an exposure reconciliation and a consumed Control V2 "
            "dispatch. No foreign rule was seeded: a blocked item must not dirty "
            "a shared host to rehearse something that will not run",
            "Executor._reconcile_exposure -> verify_exposure -> _restore_exposure",
        )
        execution_report = verify_exposure(spec, observed).descriptor_digest
        results.record(
            "digest_equality",
            PASSED if execution_report == descriptor_digest else FAILED,
            "descriptor -> authorized V3 plan -> grant -> deployment outcome; the "
            f"observed host verifies to {execution_report}",
            "build_receipt_v2(chain)",
        )

        # ── T2: the mid-run pause. Stack up, lock held, grant live. ─────────
        for row in self._await_external_rows(start):
            results.record(row.code, row.status, row.detail, *row.evidence)
        return execution_outcome

    def _await_external_rows(self, start: Start) -> list[RequirementResult]:
        """Open the window and wait, bounded, for the launcher's six rows.

        A timeout, an `abort` or a malformed frame is a refusal like any other:
        it unwinds through the same `finally` and the same terminal report, so
        the launcher's silence can neither hang this host nor pass as a result.
        """
        try:
            self.channel.send("window_open", {"deadline_seconds": start.window_seconds})
            kind, data = self.channel.receive(
                "external_results", "abort", timeout=start.window_seconds
            )
        except ChannelRefused as exc:
            raise controller_half.ProbeRefused(
                f"the external probe window failed ({exc.code})"
            ) from None
        if kind == "abort":
            raise controller_half.ProbeRefused("the launcher aborted the probe window")
        return parse_external_rows(data)

    def _finish(
        self,
        start: Start,
        results: Any,
        *,
        fixture_bytes: bytes,
        descriptor_digest: Any,
        execution_plan: Any,
        grant: Any,
        execution_outcome: Any,
        lease_run: str,
        controller_identity: Any,
    ) -> None:
        ctx, channel = self.ctx, self.channel
        with controller_half.receipt_v2_refusals(refused_as=SpecError):
            try:
                records = tuple(ctx.exposure_calls)
                require_exposure_join(records, execution_plan.exposure_reconciliations)
                calls = serialize_exposure_calls(records)
            except (ExposureEvidenceRefused, AttributeError):
                raise receipt_v2.ReceiptRefused(
                    "exposure evidence finalization refused"
                ) from None
            # T3: the launcher encrypts with probe bytes it alone holds, so this
            # host never sees them and cannot rewrite them before encryption.
            try:
                channel.send(
                    "evidence_request",
                    {"exposure_calls_b64": base64.b64encode(calls).decode("ascii")},
                )
                kind, data = channel.receive(
                    "evidence_ciphertext", "abort", timeout=start.evidence_seconds
                )
            except ChannelRefused as exc:
                raise receipt_v2.ReceiptRefused(
                    f"the evidence hand-back failed ({exc.code})"
                ) from None
            if kind == "abort":
                raise receipt_v2.ReceiptRefused("the launcher aborted the hand-back")
            ciphertext = _decode_b64(data["ciphertext_b64"], limit=MAX_CIPHERTEXT_BYTES)
            receipt = receipt_v2.assemble_receipt(
                foundation_revision=start.foundation_revision,
                foundation_artifact_digest=start.foundation_artifact,
                descriptor_digest=descriptor_digest,
                execution_plan=execution_plan,
                grant=grant,
                execution_outcome=execution_outcome,
                fixture_digest=str(Digest.of(fixture_bytes)),
                evidence_bundle=ciphertext,
                controller_identity=str(controller_identity),
                lease_id=lease_run,
                probe_vantage_ref=start.probe_vantage_ref,
                execution_run=start.execution_run,
                started_at=self.started,
                finished_at=datetime.now(UTC).isoformat(),
                results=results.all(),
            )
            receipt_bytes = receipt.canonical_bytes()
            self.receipt_digest = receipt.sha256_digest()
        ctx.receipt_digest = self.receipt_digest
        try:
            channel.send(
                "receipt",
                {
                    "receipt_b64": base64.b64encode(receipt_bytes).decode("ascii"),
                    "receipt_digest": self.receipt_digest,
                },
            )
        except ChannelRefused as exc:
            raise receipt_v2.ReceiptRefused(
                f"the receipt hand-back failed ({exc.code})"
            ) from None

    def report(self, *, refusal: Any, exit_status: int) -> None:
        """Always the last frame. Never raises: a report that failed is silence,
        and silence is read as HELD."""
        ctx = self.ctx
        try:
            self.channel.send(
                "terminal",
                {
                    "receipt_digest": self.receipt_digest,
                    "refusal": "" if refusal is None else refusal.value,
                    "host_mutated": ctx.host_mutated,
                    "lease_in_hand": ctx.lease_in_hand,
                    "cleanup": ctx.cleanup_disposition().value,
                    "cleanup_acts": [act.as_document() for act in ctx.acts],
                    "machine_id": ctx.vm_installation_id,
                    "notes": list(ctx.notes),
                    "exit_status": exit_status,
                },
            )
        except ChannelRefused as exc:
            sys.stderr.write(f"terminal report not sent: {exc.code}\n")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        sys.stderr.write("REFUSED: the target session takes no arguments\n")
        return EXIT_INDETERMINATE
    # FIRST, before anything that could print: the protocol leaves stdout.
    channel = Channel.from_stdio()
    session = Session(channel)
    try:
        try:
            _, data = channel.receive("start", timeout=START_TIMEOUT_SECONDS)
        except ChannelRefused as exc:
            sys.stderr.write(f"REFUSED: no usable start frame ({exc.code})\n")
            return EXIT_INDETERMINATE
        try:
            session.run(parse_start(data))
        # No `except Exception`: an unexpected exception names no member of the
        # closed vocabulary, sends no terminal frame, and so leaves the lease HELD.
        except (AuthorizationUnverifiable, DeploymentFoundationError) as exc:
            sys.stderr.write(f"REFUSED: {exc}\n")
            member = controller_half.classify_refusal(
                exc,
                lease_in_hand=session.ctx.lease_in_hand,
                host_mutated=session.ctx.host_mutated,
            )
            status = (
                exc.exit_status
                if isinstance(exc, AuthorizationUnverifiable)
                else EXIT_REFUSED
            )
            session.report(refusal=member, exit_status=status)
            return status
        session.report(refusal=None, exit_status=EXIT_OK)
        return EXIT_OK
    finally:
        channel.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
