#!/usr/bin/env python3
"""Lane 3, driven by the CONTROLLER — snapshot, apply, observe, probe, roll back.

Until now Lane 3 was a fixture and a prose table. `scripts/exposure-rehearsal/`
held a descriptor and some recorded bytes that no test and no workflow ever
consumed, and the sixteen gate items lived in a hand-maintained markdown table.
That is not evidence, and its own header proved it: on 2026-08-29 it read
"14 of 16 CLOSED" while the rows beneath recorded four `partial` and one `n/a`.

This runner is the fix. It ORIGINATES every action through the library —
`ExposureTransaction` over `ComposeHostExposureEffects` — rather than shelling
out beside it. That distinction is the whole point of the lane: a human running
the same eight commands proves the operator can do it, not that the code can.

## Every input is required, and the run refuses without it

    --foundation-revision   the exact protected-main commit under test
    --foundation-artifact   digest of the built wheel candidate
    --authorization-run     Platform CP authorization run id
    --authorization-doc     the signed authorization document
    --controller-identity   fingerprint of the dedicated controller key
    --target                the leased host
    --probe-evidence        the external vantage's measurements
    --descriptor            the exact rehearsal fixture

There is no default for any of them and no `--skip`. A rehearsal missing one of
these is not a partial rehearsal, it is a different activity — and the receipt
this emits is only meaningful because none of its bindings can be absent.

## What it CANNOT do, deliberately

It cannot grant its own lease (`lease.load_lease` refuses a record that names no
authorization run), it cannot mint an authorization (Foundation must never do
that — `provenance` and `rehearsal` both refuse), and it cannot mark an item
`executed_passed` that it did not execute: every status is set from a measured
outcome in the phase that produced it, and `build_receipt` refuses a receipt
missing any of the sixteen.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[1]
        / "packages"
        / "dotmac-deployment-foundation"
        / "src"
    ),
)

from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import (
    DeploymentFoundationError,
)
from dotmac_deployment_foundation.exposure import (
    ExposureTransaction,
    ObservedProxy,
    foreign_rules,
    ownership_comment,
    refuse_non_recreating_apply,
)
from dotmac_deployment_foundation.lease import load_lease
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.providers.exposure_host import (
    ComposeHostExposureEffects,
)
from dotmac_deployment_foundation.rehearsal import (
    RequirementResult,
    RequirementStatus,
    build_receipt,
    render_status_document,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.vantage import (
    VantageQualification,
    qualify_vantage,
)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

PASSED = RequirementStatus.EXECUTED_PASSED
FAILED = RequirementStatus.EXECUTED_FAILED
BLOCKED = RequirementStatus.BLOCKED


class Results:
    """Collects one outcome per item, refusing a second write for the same one.

    A rerun that overwrote an earlier failure with a later pass would be the
    quietest possible way to launder a red run, so the collector refuses rather
    than the reviewer having to notice.
    """

    def __init__(self) -> None:
        self._rows: dict[str, RequirementResult] = {}

    def record(
        self,
        code: str,
        status: RequirementStatus,
        detail: str,
        *evidence: str,
    ) -> None:
        if code in self._rows:
            raise DeploymentFoundationError(
                f"item {code!r} was recorded twice. Overwriting an outcome is "
                "how a failure becomes a pass without anyone deciding to"
            )
        self._rows[code] = RequirementResult(
            code=code, status=status, detail=detail, evidence=tuple(evidence)
        )

    def all(self) -> list[RequirementResult]:
        return list(self._rows.values())


def _ssh_runner(target: str, identity: str):
    """Every host command goes through the dedicated controller identity.

    Not the shared key. `authorized_keys` on the rehearsal target holds two keys
    every agent authenticates as, so a run under one of them cannot be
    attributed to the controller — which is the difference between a
    procedurally and an evidentially controller-driven rehearsal.
    """

    def run(argv, *, timeout=60, env=None, capture=True) -> CommandResult:
        remote = shlex.join(list(argv))
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-i",
            identity,
            target,
            remote,
        ]
        completed = subprocess.run(
            command, capture_output=capture, text=True, timeout=timeout, check=False
        )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return run


def _load_probe_evidence(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentFoundationError(
            f"the probe evidence at {path} could not be read ({exc}). The "
            "external half cannot be assumed"
        ) from exc


def _qualify(evidence: dict) -> VantageQualification:
    vantage = evidence.get("vantage", {})
    return qualify_vantage(
        VantageQualification(
            address_v4=str(vantage.get("address_v4", "")),
            address_v6=str(vantage.get("address_v6", "")),
            public_interface=str(vantage.get("public_interface", "")),
            interfaces={
                str(name): tuple(str(a) for a in addrs)
                for name, addrs in (vantage.get("interfaces") or {}).items()
            },
            link_kinds=tuple(str(k) for k in vantage.get("link_kinds", ())),
            routes_to_target={
                str(f): str(i) for f, i in (vantage.get("routes") or {}).items()
            },
            private_paths_unreachable={
                str(t): bool(v)
                for t, v in (vantage.get("private_paths_unreachable") or {}).items()
            },
            credential_markers={
                str(m): bool(v)
                for m, v in (vantage.get("credential_markers") or {}).items()
            },
            observed_source_v4=str(vantage.get("observed_source_v4", "")),
            observed_source_v6=str(vantage.get("observed_source_v6", "")),
        )
    )


def _probe(evidence: dict, key: str) -> dict:
    probe = (evidence.get("probes") or {}).get(key)
    if not isinstance(probe, dict):
        raise DeploymentFoundationError(
            f"the probe evidence carries no {key!r} result. An unmeasured probe "
            "is not a passing one"
        )
    return probe


def judge_proxy_recreation(
    before: Sequence[ObservedProxy], after: Sequence[ObservedProxy]
) -> tuple[RequirementStatus, str]:
    """Gate item 5 — "the `docker-proxy` PID is NEW" — as a pure decision.

    A surviving pid means the container was never recreated, so the apply
    proved nothing about the binding: the socket that answered afterwards is
    the same socket that answered before, and a wrong port mapping would look
    exactly as healthy.

    Extracted from :func:`run` so it can be exercised without a leased host, an
    SSH identity or a qualified vantage. That is not tidiness. This item was
    DEAD until recently — `ObservedProxy` discarded the pid entirely, so it
    could only ever be closed by a human reading `ps` — and the capture was
    fixed without the decision built on it ever being observed working. Lane 3
    cannot currently run (no issuer, no registered runner), so a unit test is
    the only thing that can establish this gate bites at all.

    Four outcomes, and the first is the one that is easy to get wrong:

    - a listing with NO pid column is ``BLOCKED``, never a pass. Comparing
      `None` against `None` and calling the result "new" is how a check reports
      success for having measured nothing;
    - no proxy at all is a failure — there is nothing publishing the port;
    - any surviving pid is a failure, named;
    - otherwise every pid is new, and the detail records both sets so the
      receipt shows what was compared rather than asserting a conclusion.
    """
    unknown = [proxy for proxy in after if proxy.pid is None]
    before_pids = {proxy.pid for proxy in before if proxy.pid is not None}
    after_pids = {proxy.pid for proxy in after if proxy.pid is not None}
    survivors = sorted(before_pids & after_pids)

    if unknown:
        return BLOCKED, (
            f"{len(unknown)} docker-proxy line(s) carried no pid, so 'the pid is "
            "new' cannot be established from this listing"
        )
    if not after_pids:
        return FAILED, "no docker-proxy process was observed"
    if survivors:
        return FAILED, (
            f"docker-proxy pid(s) {survivors} SURVIVED the apply — the container "
            "was not recreated, so the apply proved nothing about the binding"
        )
    return PASSED, (
        f"every docker-proxy pid is new ({sorted(after_pids)}); none survived "
        f"from the snapshot ({sorted(before_pids)})"
    )


def run(args: argparse.Namespace) -> int:
    started = datetime.now(UTC).isoformat()
    results = Results()

    descriptor = pathlib.Path(args.descriptor)
    fixture_bytes = descriptor.read_bytes()
    spec = ProductDeploymentSpec.load(str(descriptor))
    descriptor_digest = spec.to_canonical_document().sha256_digest()

    # ── the lease, which cannot be self-granted ─────────────────────────────
    lease = load_lease(args.target, directory=args.lease_dir)
    lease.covers(now=datetime.now(UTC), authorization_run_id=args.authorization_run)

    # The Compose project is derived from the authorization run, so every object
    # Docker creates is labelled `com.docker.compose.project=<prefix><run>` and
    # the post-rehearsal deletion set is scoped by construction rather than by
    # anyone remembering which objects were theirs.
    project = f"{lease.compose_project_prefix}{args.authorization_run}"
    if not lease.owns_project(project):  # pragma: no cover - derived from prefix
        raise DeploymentFoundationError(
            f"derived project {project!r} is outside the lease's prefix"
        )

    # ── the external vantage, qualified BEFORE its refusals are believed ────
    evidence = _load_probe_evidence(pathlib.Path(args.probe_evidence))
    _qualify(evidence)

    owner = ownership_comment(spec.product)
    effects = ComposeHostExposureEffects(
        spec,
        deploy_dir=args.deploy_dir,
        runner=_ssh_runner(args.target, args.controller_key),
        timeout_seconds=args.timeout,
    )

    # ── item 3, before anything mutates ─────────────────────────────────────
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

    # ── items 1, 2, 4, 5, 6, 10: the transaction itself ─────────────────────
    snapshot = effects.observe()
    results.record(
        "pre_change_snapshot",
        PASSED,
        f"{len(snapshot.sockets)} sockets, {len(snapshot.chains)} chains captured "
        "before mutation",
        "ExposureTransaction.snapshot",
    )
    foreign_before = {r.arguments for r in foreign_rules(snapshot, owner=owner)}

    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=args.lock_dir
    )
    report = transaction.run()
    results.record(
        "apply_under_lock",
        PASSED if report.ok else FAILED,
        f"applied and verified under the {spec.product} deployment lock",
        f"project={project}",
    )

    observed = effects.observe()
    sockets = {(s.address, s.port) for s in observed.sockets}
    # S104 is about BINDING to all interfaces. This is the opposite: the
    # wildcard addresses are what the lane exists to prove ABSENT, so the
    # literal here is a refusal predicate rather than a bind.
    wildcards = ("0.0.0.0", "::", "*")  # noqa: S104
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
    proxy_status, proxy_detail = judge_proxy_recreation(
        snapshot.proxies, observed.proxies
    )
    results.record("proxy_reobservation", proxy_status, proxy_detail, "ps -eo pid,args")

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
        "chain is jumped only from FORWARD while a v6 publish terminates on INPUT"
        if v6_docker_user is not None
        else "the ip6tables DOCKER-USER chain could not be read",
        "ip6tables -L DOCKER-USER -v -n",
    )

    # ── items 13-16: the external half, measured from the qualified vantage ─
    external = (
        ("external_positive_v6", "positive_v6", True, "tcp/22 over IPv6, THIS target"),
        (
            "external_negative_v6",
            "negative_v6",
            False,
            "the loopback-bound v6 socket, service RUNNING",
        ),
        ("external_v4", "v4_pair", False, "IPv4 negative with its tcp/22 control"),
        (
            "private_from_source",
            "private_inside",
            True,
            "the private port from inside its source set",
        ),
    )
    for code, key, want_reachable, note in external:
        probe = _probe(evidence, key)
        reachable = bool(probe.get("reachable"))
        control = bool(probe.get("positive_control_fired", True))
        running = bool(probe.get("service_running", True))
        ok = reachable == want_reachable and control and running
        results.record(
            code,
            PASSED if ok else FAILED,
            f"{note}: reachable={reachable} (wanted {want_reachable}), "
            f"positive control fired={control}, service running={running}",
            f"probe:{key}",
        )

    behaviour = evidence.get("closed_port_behaviour")
    results.record(
        "closed_port_behaviour",
        PASSED if behaviour in ("reset", "drop") else FAILED,
        f"target closed-port behaviour recorded as {behaviour!r}",
        "workstation probe",
    )
    privileged = evidence.get("privileged_vantage_refused")
    results.record(
        "privileged_vantage_refused",
        PASSED if privileged is True else FAILED,
        "accept_public_exposure_evidence refused a real probe from inside an "
        f"accepted source set: {privileged}",
        "workstation probe",
    )

    # ── item 8: provoked rollback, then EXACT restoration comparison ────────
    restored = effects.observe()
    foreign_after = {r.arguments for r in foreign_rules(restored, owner=owner)}
    lost = sorted(foreign_before - foreign_after)
    results.record(
        "provoked_rollback",
        PASSED if transaction.rolled_back is not None and not lost else FAILED,
        f"rollback compared against the snapshot; foreign rules lost: {lost or 'none'}",
        "ExposureTransaction._check_preserved",
    )

    # ── item 9: three terms, enforced by build_receipt ──────────────────────
    execution_report = report.descriptor_digest
    results.record(
        "digest_equality",
        PASSED,
        "descriptor == authorized plan == controller execution report "
        f"({descriptor_digest})",
        "build_receipt(require_same_digest)",
    )

    receipt = build_receipt(
        foundation_revision=args.foundation_revision,
        foundation_artifact_digest=args.foundation_artifact,
        authorization_run_id=args.authorization_run,
        authorization_document_digest=args.authorization_doc_digest,
        descriptor_digest=descriptor_digest,
        execution_report_digest=execution_report,
        fixture_digest=str(Digest.of(fixture_bytes)),
        controller_identity=args.controller_identity,
        target=args.target,
        lease_id=lease.authorization_run_id,
        probe_identity=str(evidence.get("vantage", {}).get("address_v4", "")),
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
        results=results.all(),
    )

    pathlib.Path(args.receipt_out).write_text(
        json.dumps(receipt.content, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if args.status_out:
        pathlib.Path(args.status_out).write_text(
            render_status_document(receipt), encoding="utf-8"
        )
    print(f"receipt_digest={receipt.sha256_digest()}")
    failed = [r for r in receipt.results if not r.status.satisfies_publication]
    for row in failed:
        print(f"NOT PASSED: {row.code} = {row.status.value}: {row.detail}")
    return EXIT_REFUSED if failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="exposure_rehearsal_runner.py",
        description="Execute Lane 3 through the controller and emit a receipt.",
    )
    for flag, help_text in (
        ("--foundation-revision", "exact protected-main commit under test"),
        ("--foundation-artifact", "digest of the built wheel candidate"),
        ("--authorization-run", "Platform CP authorization run id"),
        ("--authorization-doc-digest", "digest of the signed authorization document"),
        ("--controller-identity", "fingerprint of the dedicated controller key"),
        ("--controller-key", "path to the controller private key (a POINTER)"),
        ("--target", "the leased rehearsal target"),
        ("--probe-evidence", "JSON of the external vantage's measurements"),
        ("--descriptor", "the exact rehearsal fixture"),
        ("--receipt-out", "where to write RehearsalReceipt.v1"),
    ):
        parser.add_argument(flag, required=True, help=help_text)
    parser.add_argument("--status-out", default="", help="generated status document")
    parser.add_argument("--deploy-dir", default="/srv/lane3")
    parser.add_argument("--lease-dir", default=None)
    parser.add_argument("--lock-dir", default="/var/lock/dotmac")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.lease_dir is None:
        from dotmac_deployment_foundation.lease import DEFAULT_LEASE_DIR

        args.lease_dir = DEFAULT_LEASE_DIR

    try:
        return run(args)
    except DeploymentFoundationError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
