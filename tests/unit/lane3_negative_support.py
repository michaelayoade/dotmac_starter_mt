"""Synthetic fixtures for the S8b negative-target proof (hosted tests only).

Documentation names and ports only: no estate address, host or key.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime
from typing import Any

from dotmac_deployment_foundation.exposure import (
    HostObservation,
    ObservedChain,
    ObservedRule,
    ownership_comment,
)
from dotmac_deployment_foundation.policy import build_firewall_plan

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_negative_proof as nproof  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402
from lane3_exposure_recording import ExposureCallRecord  # noqa: E402
from lane3_provocation import FOREIGN_OWNER, FOREIGN_PORTS  # noqa: E402

PRIVATE_PORT = 19001


def load_spec():
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    return ProductDeploymentSpec.loads(
        (REPO / "scripts/exposure-rehearsal/product.toml").read_text("utf-8"),
        source="<negative-fixture>",
    )


def make_plan(
    spec: Any,
    *,
    target_id: str,
    host_id: str,
    incarnation: str,
    enrolment: str,
    wheel: str | None = None,
):
    """A V3 plan for `spec` with the descriptor's exposure reconciliations."""
    from dotmac_deployment_foundation.engine.plan import build_plan
    from dotmac_deployment_foundation.execution_plan import (
        HostPrestateV1,
        render_execution_plan,
    )
    from dotmac_deployment_foundation.execution_plan_v2 import (
        render_execution_plan_v2,
    )
    from dotmac_deployment_foundation.execution_plan_v3 import (
        render_execution_plan_v3,
    )
    from lane3_plan_v3 import exposure_reconciliations_for_spec

    from tests.unit.foundation_v3_support import CONTROLLER, WHEEL

    base = render_execution_plan(
        spec,
        build_plan(spec),
        target="rehearsal-target",
        operation="deploy",
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    return render_execution_plan_v3(
        render_execution_plan_v2(
            base, exposure_reconciliations=exposure_reconciliations_for_spec(spec)
        ),
        candidate_wheel_digest=wheel or WHEEL,
        target_id=target_id,
        controller_ssh_fingerprint=CONTROLLER,
        host_id=host_id,
        host_incarnation=incarnation,
        host_enrolment_ref=enrolment,
    )


def _foreign(family: str, chain: str, port: int) -> ObservedRule:
    return ObservedRule(
        family=family,
        chain=chain,
        arguments=f"-p tcp -m tcp --dport {port} -m comment "
        f"--comment {FOREIGN_OWNER} -j ACCEPT",
    )


def _inert() -> ObservedRule:
    return ObservedRule(
        family="ipv6",
        chain="DOCKER-USER",
        arguments=f"-p tcp -m tcp --dport {PRIVATE_PORT} -j ACCEPT",
    )


def baseline_observation(*, inert: bool = True, foreign: bool = True):
    """The negative baseline: an inert IPv6 DOCKER-USER rule on the private port
    and a foreign rule in each family's shared filter chain."""
    return HostObservation(
        chains=(
            ObservedChain(
                family="ipv4",
                name="DOCKER-USER",
                policy="-",
                rules=(_foreign("ipv4", "DOCKER-USER", FOREIGN_PORTS["ipv4"]),)
                if foreign
                else (),
            ),
            ObservedChain(
                family="ipv6",
                name="INPUT",
                policy="ACCEPT",
                rules=(_foreign("ipv6", "INPUT", FOREIGN_PORTS["ipv6"]),)
                if foreign
                else (),
            ),
            ObservedChain(
                family="ipv6",
                name="DOCKER-USER",
                policy="-",
                rules=(_inert(),) if inert else (),
            ),
        ),
        closed_port_behaviour="rst",
    )


def applied_observation(spec: Any, *, inert: bool = True, drop_foreign: bool = False):
    """After the apply: the product's own rules added, the baseline kept."""
    base = baseline_observation(inert=inert, foreign=not drop_foreign)
    owner = ownership_comment(spec.product)
    ours = [
        ObservedRule(
            family=rule.family,
            chain=rule.chain,
            arguments=f"-p tcp --ctorigdstport {rule.host_port} -m comment "
            f"--comment {owner} -j {rule.action}",
        )
        for rule in build_firewall_plan(spec)
    ]
    chains = []
    for chain in base.chains:
        extra = tuple(
            r for r in ours if (r.family, r.chain) == (chain.family, chain.name)
        )
        chains.append(
            ObservedChain(chain.family, chain.name, chain.policy, chain.rules + extra)
        )
    return HostObservation(chains=tuple(chains), closed_port_behaviour="rst")


def negative_records(
    spec: Any,
    plan: Any,
    *,
    inert: bool = True,
    restore_argument: tuple[ObservedChain, ...] | None = None,
    final: HostObservation | None = None,
    first: HostObservation | None = None,
    failed_index: int | None = None,
) -> list[ExposureCallRecord]:
    """The Executor's boundary crossings for a genuinely failed, compensated apply."""
    snapshot = first if first is not None else baseline_observation()
    records = [ExposureCallRecord(0, "observe", observation=snapshot)]
    for item in plan.exposure_reconciliations:
        records.append(
            ExposureCallRecord(
                len(records),
                "replace_rules",
                family=item.family,
                chain=item.chain,
                rules=tuple(
                    rule
                    for rule in build_firewall_plan(spec)
                    if rule.family == item.family
                ),
            )
        )
    records.append(
        ExposureCallRecord(
            len(records), "observe", observation=applied_observation(spec, inert=inert)
        )
    )
    records.append(
        ExposureCallRecord(
            len(records),
            "restore_chains",
            chains=restore_argument
            if restore_argument is not None
            else snapshot.chains,
        )
    )
    records.append(
        ExposureCallRecord(
            len(records),
            "observe",
            observation=final if final is not None else baseline_observation(),
        )
    )
    if failed_index is not None:
        record = records[failed_index]
        records[failed_index] = ExposureCallRecord(
            record.sequence,
            record.method,
            family=record.family,
            chain=record.chain,
            rules=record.rules,
            chains=record.chains,
            observation=record.observation,
            exception_type="builtins.RuntimeError",
        )
    return records


def probe_bytes(
    dispatch_id: str,
    host_id: str,
    measured_at: datetime,
    *,
    statuses: tuple[str, str] = ("executed_passed", "executed_passed"),
    codes: tuple[str, str] = nproof.PROBE_ROW_CODES,
) -> bytes:
    rows = [
        {
            "code": code,
            "status": status,
            "detail": f"{code} measured after compensation",
            "evidence": [f"probe:{code}"],
        }
        for code, status in zip(codes, statuses, strict=True)
    ]
    return nproof.build_probe(
        dispatch_id=dispatch_id, host_id=host_id, measured_at=measured_at, rows=rows
    )


def proof_bytes(
    *,
    positive_dispatch_id: str = "11111111-1111-4111-8111-111111111111",
    dispatch_id: str = "22222222-2222-4222-8222-222222222222",
    target_id: str = "target-n",
    host_id: str = "host-n",
    execution_sequence: int = 1,
    attempt_no: int = 1,
    lease_authorization_run: str = "lease-n",
    terminal_refusal: str = nproof.NEGATIVE_TERMINAL_REFUSAL,
    host_mutated: bool = True,
    exit_status: int = nproof.NEGATIVE_EXIT_STATUS,
    exposure_calls: bytes | None = None,
    probe: bytes | None = None,
    measured_at: datetime | None = None,
) -> bytes:
    from datetime import UTC

    return nproof.build_proof(
        positive_dispatch_id=positive_dispatch_id,
        dispatch_id=dispatch_id,
        target_id=target_id,
        host_id=host_id,
        execution_sequence=execution_sequence,
        attempt_no=attempt_no,
        lease_authorization_run=lease_authorization_run,
        terminal_refusal=terminal_refusal,
        host_mutated=host_mutated,
        exit_status=exit_status,
        exposure_calls=exposure_calls
        if exposure_calls is not None
        else serialize_exposure_calls(()),
        probe_bytes=probe
        if probe is not None
        else probe_bytes(
            dispatch_id,
            host_id,
            measured_at or datetime(2026, 11, 19, 10, 7, 0, tzinfo=UTC),
        ),
    )
