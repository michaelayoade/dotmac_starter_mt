"""Lane 3, the LAUNCHER half of the probe rendezvous: items 11-16 (E7 / T2).

The target session pauses with its stack up and the product lock held, and sends
`window_open`. This module is what the launcher runs inside that window. It
measures the six items only a controller-held vantage can measure, with the
launcher's own keys, and returns them as closed rows for the `external_results`
frame. Nothing here touches the target's Executor or its lock.

## Why the launcher measures and the target only carries

The external probes catch a false "the firewall landed" claim by its
consequence (E3 audit S3). A target that judged its own external rows could
report the pass it wanted. So the rows are measured here, from vantages and with
keys the target never holds, and the target only validates their closed shape.

## What is reused

The judgements are the controller-side runner's, unchanged, so one definition
answers "what does reachable-from-outside mean": the three external rows, the
closed-port behaviour, item 16 (`private_from_source`) and item 12
(`privileged_vantage_refused`). They are lifted out of
`exposure_rehearsal_runner.run` as they were, not reinterpreted. The measuring
calls are injected through :class:`Measurers` so a test can supply fakes; the
defaults are the runner's own collectors.

A measurement that cannot run raises (`ProbeRefused`, `EvidenceIncomplete`,
`EvidenceUnreadable`). The caller then sends `abort`, and the target unwinds
through its own terminal report. An unmeasured probe is never a passing one.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import pathlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_inside_vantage as inside_vantage
import lane3_negative_proof as negative_proof
from dotmac_deployment_foundation.rehearsal import RequirementStatus

PASSED = RequirementStatus.EXECUTED_PASSED
FAILED = RequirementStatus.EXECUTED_FAILED

MAX_DETAIL: Final = 4000
MAX_EVIDENCE_ITEMS: Final = 16
MAX_EVIDENCE_TEXT: Final = 500

__all__ = [
    "ExternalMeasurement",
    "Measurers",
    "ProbeInputs",
    "default_measurers",
    "measure_external_rows",
    "measure_post_compensation",
]


@dataclasses.dataclass(frozen=True, slots=True)
class ProbeInputs:
    """Everything the six measurements need. Every field is required."""

    probe_host: str
    target_address: str
    inside_vantage: str
    far_end: str
    observer_user: str
    observer_key: str
    inside_jump_key: str
    timeout: int
    product: str
    private_port: int
    accepted_source_set: str
    target_v6: str

    def namespace(self) -> argparse.Namespace:
        """The attribute set the runner's collectors read, and no more."""
        return argparse.Namespace(
            probe_host=self.probe_host,
            target_address=self.target_address,
            inside_vantage=self.inside_vantage,
            far_end=self.far_end,
            observer_user=self.observer_user,
            observer_key=self.observer_key,
            timeout=self.timeout,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Measurers:
    """The three measuring acts, injected so a test needs no vantage."""

    probe_phase: Callable[[argparse.Namespace], dict[str, Any]]
    inside_probe: Callable[[ProbeInputs], dict[str, str]]
    far_end: Callable[[argparse.Namespace, str], dict[str, str]]


def _inside_probe(inputs: ProbeInputs) -> dict[str, str]:
    # The whole region is `evidence_unreadable`, for the reason recorded where
    # this was written: the collector raises one bare error from two sites.
    with controller_half.refusal_of(controller_half.EvidenceUnreadable):
        return inside_vantage.collect(
            str(controller_half.inside_probe_harness()),
            jump=inputs.inside_vantage,
            target_v4=inputs.target_address,
            target_v6=inputs.target_v6,
            port=inputs.private_port,
            timeout=inputs.timeout,
        )


def _far_end(args: argparse.Namespace, jump_key: str) -> dict[str, str]:
    return controller_half._observed_from(args, jump_key=jump_key)


def default_measurers() -> Measurers:
    return Measurers(
        probe_phase=controller_half._collect_probe_phase,
        inside_probe=_inside_probe,
        far_end=_far_end,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalMeasurement:
    """The six rows, and the raw window-phase document they were judged from.

    `document` is the qualify-phase document with the probes measured NOW merged
    in, plus the inside-vantage probe and the far-end observation. It is what the
    launcher encrypts: the offline verifier must be able to re-derive rows 11-16
    from bytes the target never held, so the window-phase measurements have to be
    in the bundle and not only in the rows.
    """

    rows: list[dict]
    document: dict[str, Any]

    def document_bytes(self) -> bytes:
        """The document as bytes, in insertion order, never NaN or infinity."""
        return (
            json.dumps(
                self.document,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )


def _row(code: str, status: RequirementStatus, detail: str, *evidence: str) -> dict:
    return {
        "code": code,
        "status": status.value,
        "detail": detail[:MAX_DETAIL],
        "evidence": [item[:MAX_EVIDENCE_TEXT] for item in evidence][
            :MAX_EVIDENCE_ITEMS
        ],
    }


def _external_row(
    evidence: dict[str, Any], code: str, key: str, want_reachable: bool, note: str
) -> dict:
    """One external judgement. The single definition, shared by the window rows
    (items 11-13) and the post-compensation probe (S8b)."""
    probe = controller_half._probe(evidence, key)
    reachable = bool(probe.get("reachable"))
    # ABSENCE FAILS here too: defaulting the control to "fired" would read an
    # unmeasured control as a satisfied one.
    if "positive_control_fired" not in probe:
        raise controller_half.EvidenceIncomplete(
            f"the {key!r} probe carries no `positive_control_fired`. An "
            "unmeasured control must never read as a fired one"
        )
    control = bool(probe["positive_control_fired"])
    # ABSENCE FAILS: a negative probe against a port where nothing listens
    # measures an absent service, not an enforced exposure.
    if "service_running" not in probe:
        raise controller_half.EvidenceIncomplete(
            f"the {key!r} probe carries no `service_running`. An unmeasured "
            "negative must never read as a pass"
        )
    running = bool(probe["service_running"])
    ok = reachable == want_reachable and control and running
    return _row(
        code,
        PASSED if ok else FAILED,
        f"{note}: reachable={reachable} (wanted {want_reachable}), "
        f"positive control fired={control}, service running={running}",
        f"probe:{key}",
    )


def measure_external_rows(
    inputs: ProbeInputs,
    qualified_evidence: dict[str, Any],
    *,
    measurers: Measurers | None = None,
) -> ExternalMeasurement:
    """The six closed rows for items 11-16, measured now, with the stack up.

    `qualified_evidence` is the qualify-phase document, already qualified before
    the target was touched. It is copied, never mutated; the returned
    `document` is that copy with the window-phase measurements merged in.
    """
    measure = measurers if measurers is not None else default_measurers()
    args = inputs.namespace()
    evidence = copy.deepcopy(qualified_evidence)

    probe_phase = measure.probe_phase(args)
    evidence["probes"] = probe_phase.get("probes", {})
    if "closed_port_behaviour" in probe_phase:
        evidence["closed_port_behaviour"] = probe_phase["closed_port_behaviour"]

    rows: list[dict] = []
    external = (
        ("external_positive_v6", "positive_v6", True, "tcp/22 over IPv6, THIS target"),
        (
            "external_negative_v6",
            "negative_v6",
            False,
            "the loopback-bound v6 socket, service RUNNING",
        ),
        ("external_v4", "v4_pair", False, "IPv4 negative with its tcp/22 control"),
    )
    for code, key, want_reachable, note in external:
        rows.append(_external_row(evidence, code, key, want_reachable, note))

    behaviour = evidence.get("closed_port_behaviour")
    rows.append(
        _row(
            "closed_port_behaviour",
            PASSED if behaviour in ("reset", "drop") else FAILED,
            f"target closed-port behaviour recorded as {behaviour!r}",
            "workstation probe",
        )
    )

    inside_probe = measure.inside_probe(inputs)
    inside_observed = measure.far_end(args, inputs.inside_jump_key)
    evidence["inside_probe"] = dict(inside_probe)
    evidence["inside_observed"] = dict(inside_observed)
    seen = inside_vantage.vantages(inside_probe, inside_observed)
    control_ok = inside_vantage.control_is_meaningful(inside_probe)

    both_reached = all(
        vantage.outcome is inside_vantage.InsideOutcome.REACHED for vantage in seen
    )
    observed_all = all(vantage.observed_source for vantage in seen)
    per_family = "; ".join(
        f"{vantage.family}: outcome={vantage.outcome}, observed as "
        f"{vantage.observed_source or 'NOTHING'} ({vantage.cidr})"
        for vantage in seen
    )
    control_note = (
        "jump-scope control prohibited as expected"
        if control_ok
        else "jump-scope control DID NOT FIRE; a permitopen that refused "
        "everything would look identical to one correctly scoped"
    )
    rows.append(
        _row(
            "private_from_source",
            PASSED if both_reached and observed_all and control_ok else FAILED,
            f"the private port from inside its source set, per family. "
            f"{per_family}. {control_note}",
            *(f"inside:{vantage.family}:{vantage.cidr}" for vantage in seen),
            "requalified-every-run",
        )
    )

    endpoint = f"{inputs.product}:{inputs.private_port}"
    verdicts = [
        inside_vantage.refusal_fired_for_the_right_reason(
            vantage,
            endpoint_token=endpoint,
            accepted_source_sets=(inputs.accepted_source_set,),
        )
        for vantage in seen
    ]
    refused_ok = all(ok for ok, _ in verdicts) and control_ok
    rows.append(
        _row(
            "privileged_vantage_refused",
            PASSED if refused_ok else FAILED,
            "accept_public_exposure_evidence refused a real probe from inside an "
            "accepted source set, on the privileged-vantage branch rather than "
            "the membership one: "
            + "; ".join(
                f"{v.family}={detail}"
                for v, (_, detail) in zip(seen, verdicts, strict=False)
            ),
            f"accepted_source_set:{inputs.accepted_source_set}",
        )
    )
    return ExternalMeasurement(rows=rows, document=evidence)


def measure_post_compensation(
    inputs: ProbeInputs,
    qualified_evidence: dict[str, Any],
    *,
    dispatch_id: str,
    host_id: str,
    measurers: Measurers | None = None,
    clock: Callable[[], datetime] | None = None,
) -> bytes:
    """The negative target's post-compensation probe (S8b), as closed bytes.

    Taken in the bounded window the negative session opens AFTER its compensation
    and BEFORE its own cleanup, with the stack up. The two existing judgements are
    reused unchanged: `external_negative_v6` and `external_v4`, both wanted
    UNREACHABLE, each needing its own tcp/22 positive control to fire and the
    service to be running. `positive_v6` is a different row and is not used.
    An expectation is never relaxed here: a legitimately reachable compensated
    host is a fixture fact that needs a ruling.
    """
    measure = measurers if measurers is not None else default_measurers()
    evidence = copy.deepcopy(qualified_evidence)
    probe_phase = measure.probe_phase(inputs.namespace())
    evidence["probes"] = probe_phase.get("probes", {})
    rows = [
        _external_row(
            evidence,
            "external_negative_v6",
            "negative_v6",
            False,
            "the loopback-bound v6 socket, service RUNNING, after compensation",
        ),
        _external_row(
            evidence,
            "external_v4",
            "v4_pair",
            False,
            "IPv4 negative with its tcp/22 control, after compensation",
        ),
    ]
    moment = (clock or (lambda: datetime.now(UTC)))()
    return negative_proof.build_probe(
        dispatch_id=dispatch_id, host_id=host_id, measured_at=moment, rows=rows
    )
