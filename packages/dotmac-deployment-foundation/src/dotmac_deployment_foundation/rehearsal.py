"""``RehearsalReceipt.v1`` — Lane 3's evidence, and the gate that reads it.

The exposure rehearsal used to be a prose table in
`docs/inventories/deployment-exposure-rehearsal.md`, hand-maintained. On
2026-08-29 its header said *"14 of 16 items CLOSED"* while the table three lines
below recorded four items **partial** and one **n/a**. Fourteen was reached by
counting those as closed, and nothing could catch it, because the summary and
the evidence were written by the same hand into the same file.

So the status document is now GENERATED from this receipt
(:func:`render_status_document`), and publication is gated on the receipt
(:func:`verify_publication`) rather than on the document. A hand-edited table
cannot make a release pass, and a receipt cannot contradict the count derived
from it.

## The status vocabulary is the whole design

Six statuses, and **exactly one of them satisfies publication**:

======================  ======================================================
`executed_passed`       the controller ran it and it passed — the ONLY pass
`executed_failed`       the controller ran it and it failed
`not_executed`          nothing ran it
`hand_measured`         a human measured it; supporting context, never a pass
`blocked`               a prerequisite is missing
`vacuous`               it ran but the fixture could not exercise it
======================  ======================================================

`hand_measured` and `vacuous` exist as their own statuses precisely because the
2026-08-29 count folded them into "closed". A hand-driven step proves the
OPERATOR can do it, not that the CODE can; a check whose fixture derives nothing
passes without observing anything. Naming them separately makes both visible in
the generated table instead of arithmetically invisible.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Final

from .digest import Digest, require_same_digest
from .errors import SpecError
from .version import VERSION

__all__ = [
    "REHEARSAL_RECEIPT_SCHEMA",
    "REQUIRED_ITEMS",
    "LaneThreeItem",
    "RehearsalReceiptV1",
    "RequirementResult",
    "RequirementStatus",
    "build_receipt",
    "render_pending_document",
    "render_status_document",
    "verify_publication",
]

REHEARSAL_RECEIPT_SCHEMA: Final = "RehearsalReceipt.v1"

#: Lane 3 and only Lane 3. Lane 2 proves a real engine, database, ingress
#: handoff and restore loop; it says nothing about address-family exposure,
#: which is what this release is named after. A Lane 2 receipt offered here is
#: refused rather than credited — see `verify_publication`.
LANE: Final = 3


class RequirementStatus(str, Enum):
    """What actually happened to one gate item."""

    EXECUTED_PASSED = "executed_passed"
    EXECUTED_FAILED = "executed_failed"
    NOT_EXECUTED = "not_executed"
    HAND_MEASURED = "hand_measured"
    BLOCKED = "blocked"
    VACUOUS = "vacuous"

    @property
    def satisfies_publication(self) -> bool:
        return self is RequirementStatus.EXECUTED_PASSED


@dataclasses.dataclass(frozen=True, slots=True)
class LaneThreeItem:
    """One of the sixteen, as a declared member rather than a row in a table."""

    number: int
    code: str
    title: str
    #: Which host produces the evidence. Recorded so the generated document can
    #: group by it without a second, drifting list.
    evidence_from: str


#: The sixteen. Declared here ONCE; the generated document, the gate and the
#: runner all read this tuple, so an item cannot exist in one and not another.
REQUIRED_ITEMS: Final[tuple[LaneThreeItem, ...]] = (
    LaneThreeItem(
        1,
        "apply_under_lock",
        "Apply under the product deployment lock",
        "target",
    ),
    LaneThreeItem(
        2,
        "pre_change_snapshot",
        "Pre-change snapshot (HostObservation)",
        "target",
    ),
    LaneThreeItem(
        3,
        "non_recreating_refused",
        "A non-recreating apply is REFUSED",
        "target",
    ),
    LaneThreeItem(
        4,
        "socket_reobservation",
        "Socket re-observation, per family",
        "target",
    ),
    LaneThreeItem(
        5,
        "proxy_reobservation",
        "docker-proxy PID is NEW, host-ip correct",
        "target",
    ),
    LaneThreeItem(
        6,
        "firewall_reobservation",
        "Firewall rules land in the right chain, terminal DROP present",
        "target",
    ),
    LaneThreeItem(
        7,
        "inert_v6_chain",
        "The inert v6 DOCKER-USER chain, captured with a zero counter",
        "target",
    ),
    LaneThreeItem(
        8,
        "provoked_rollback",
        "Rollback, provoked rather than simulated",
        "target",
    ),
    LaneThreeItem(
        9,
        "digest_equality",
        "Descriptor == authorized plan == execution report",
        "target",
    ),
    LaneThreeItem(
        10,
        "none_emits_no_socket",
        'exposure = "none" emits no socket at all',
        "target",
    ),
    LaneThreeItem(
        11,
        "closed_port_behaviour",
        "The target's closed-port behaviour, recorded",
        "workstation",
    ),
    LaneThreeItem(
        12,
        "privileged_vantage_refused",
        "The privileged-vantage refusal fires on a real probe",
        "workstation",
    ),
    LaneThreeItem(
        13,
        "external_negative_v6",
        "IPv6 external negative against a RUNNING service",
        "probe",
    ),
    LaneThreeItem(
        14,
        "external_positive_v6",
        "IPv6 external positive control to THIS target",
        "probe",
    ),
    LaneThreeItem(
        15,
        "external_v4",
        "IPv4 external negative plus its positive control",
        "probe",
    ),
    LaneThreeItem(
        16,
        "private_from_source",
        "A private exposure reached from inside its source set",
        "probe",
    ),
)

_BY_CODE: Final[dict[str, LaneThreeItem]] = {item.code: item for item in REQUIRED_ITEMS}


@dataclasses.dataclass(frozen=True, slots=True)
class RequirementResult:
    """One item's outcome, with the evidence that produced it."""

    code: str
    status: RequirementStatus
    detail: str
    #: Free-form pointers to the bytes behind the claim — a log path, a command,
    #: an artifact name. Never a secret value.
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in _BY_CODE:
            raise SpecError(
                f"{self.code!r} is not one of the sixteen Lane 3 items "
                f"({sorted(_BY_CODE)}). A receipt cannot invent a requirement, "
                "because a gate that accepts unknown item codes can be "
                "satisfied by renaming a failure"
            )
        if not self.detail.strip():
            raise SpecError(
                f"item {self.code!r} carries no detail. A bare status is not "
                "evidence — the detail is what a reader checks the status against"
            )

    @property
    def item(self) -> LaneThreeItem:
        return _BY_CODE[self.code]

    def as_document(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status.value,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RehearsalReceiptV1:
    """The canonical, digest-bearing record of one Lane 3 execution."""

    content: dict[str, Any]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return str(Digest.of(self.canonical_bytes()))

    @property
    def lane(self) -> int:
        return int(self.content["lane"])

    @property
    def foundation_revision(self) -> str:
        return str(self.content["foundation_revision"])

    @property
    def authorization_run_id(self) -> str:
        return str(self.content["authorization_run_id"])

    @property
    def results(self) -> tuple[RequirementResult, ...]:
        return tuple(
            RequirementResult(
                code=str(row["code"]),
                status=RequirementStatus(str(row["status"])),
                detail=str(row["detail"]),
                evidence=tuple(str(item) for item in row.get("evidence", ())),
            )
            for row in self.content["results"]
        )

    def result_for(self, code: str) -> RequirementResult:
        for result in self.results:
            if result.code == code:
                return result
        raise SpecError(f"the receipt carries no result for item {code!r}")

    @classmethod
    def from_json(cls, payload: str | bytes) -> RehearsalReceiptV1:
        try:
            content = json.loads(payload)
        except ValueError as exc:
            raise SpecError(f"the receipt is not valid JSON: {exc}") from exc
        if not isinstance(content, dict):
            raise SpecError("a receipt is a JSON object")
        schema = content.get("schema")
        if schema != REHEARSAL_RECEIPT_SCHEMA:
            raise SpecError(
                f"expected {REHEARSAL_RECEIPT_SCHEMA}, got {schema!r}. A reader "
                "of v1 refuses a document it does not understand rather than "
                "interpreting unknown fields"
            )
        return cls(content=content)


def build_receipt(
    *,
    foundation_revision: str,
    foundation_artifact_digest: str,
    authorization_run_id: str,
    authorization_document_digest: str,
    descriptor_digest: str,
    execution_report_digest: str,
    fixture_digest: str,
    controller_identity: str,
    target: str,
    lease_id: str,
    probe_identity: str,
    started_at: str,
    finished_at: str,
    results: Sequence[RequirementResult],
) -> RehearsalReceiptV1:
    """Assemble a receipt, refusing anything that could not be checked later.

    The three-term digest equality (gate item 9) is enforced HERE, at
    construction, rather than being one more thing the runner is trusted to have
    done. A receipt that cannot be built is better than one that records a
    mismatch it did not notice.
    """
    for name, value in (
        ("foundation_revision", foundation_revision),
        ("authorization_run_id", authorization_run_id),
        ("controller_identity", controller_identity),
        ("target", target),
        ("lease_id", lease_id),
        ("probe_identity", probe_identity),
        ("started_at", started_at),
        ("finished_at", finished_at),
    ):
        if not str(value).strip():
            raise SpecError(
                f"{name} is empty. Every field on a receipt exists so a reader "
                "can go and check it; an empty one is an unverifiable claim"
            )
    revision = str(foundation_revision).strip().lower()
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise SpecError(
            f"foundation_revision {foundation_revision!r} is not a full commit. "
            "A rehearsal is evidence about one exact revision or about nothing"
        )

    # Gate item 9, all THREE terms. Two matching terms cannot pass: the
    # authorized plan is the middle term, and without it the check degenerates
    # into "the report agrees with the descriptor it was generated from".
    agreed = require_same_digest(
        {
            "canonical_descriptor": descriptor_digest,
            "authorized_plan": authorization_document_digest,
            "controller_execution_report": execution_report_digest,
        },
        what="gate item 9 (digest equality)",
    )

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.code in seen:
            raise SpecError(
                f"item {result.code!r} appears twice in the receipt. Two rows "
                "for one item is how a failure hides behind a pass"
            )
        seen.add(result.code)
        rows.append(result.as_document())
    missing = sorted(set(_BY_CODE) - seen)
    if missing:
        raise SpecError(
            f"the receipt omits {missing}. Every one of the sixteen must carry "
            "an explicit status — an absent item is not an implicit pass, and "
            "silence is exactly how the previous count went wrong"
        )

    content: dict[str, Any] = {
        "schema": REHEARSAL_RECEIPT_SCHEMA,
        "lane": LANE,
        "foundation_version": VERSION,
        "foundation_revision": revision,
        "foundation_artifact_digest": str(
            Digest.parse(foundation_artifact_digest, where="foundation_artifact_digest")
        ),
        "authorization_run_id": str(authorization_run_id).strip(),
        "authorization_document_digest": str(agreed),
        "descriptor_digest": str(agreed),
        "execution_report_digest": str(agreed),
        "fixture_digest": str(Digest.parse(fixture_digest, where="fixture_digest")),
        "controller_identity": str(controller_identity).strip(),
        "target": str(target).strip(),
        "lease_id": str(lease_id).strip(),
        "probe_identity": str(probe_identity).strip(),
        "started_at": str(started_at).strip(),
        "finished_at": str(finished_at).strip(),
        "results": sorted(rows, key=lambda row: _BY_CODE[str(row["code"])].number),
    }
    return RehearsalReceiptV1(content=content)


def verify_publication(receipt: RehearsalReceiptV1, *, revision: str) -> None:
    """Refuse publication unless EVERY item is `executed_passed` at `revision`.

    Three refusals, in the order a reader would ask them.

    **Lane.** A Lane 2 receipt is refused outright rather than counted. Lane 2
    is a real and valuable proof of a different thing; substituting it here
    would be the "green preflight reads as attested" failure with two lanes
    instead of two gates.

    **Revision.** Evidence from another commit is evidence about another commit.

    **Every item, executed and passed.** No `partial`, no `not_applicable`, no
    `hand_measured`, no `vacuous`, no missing row. The statuses are enumerated
    in the refusal so the operator sees which ones and why, rather than a count.
    """
    if receipt.lane != LANE:
        raise SpecError(
            f"this is a Lane {receipt.lane} receipt and publication requires "
            f"Lane {LANE}. Lane 2 proves a real engine, database and restore "
            "loop; it does not watch an IPv6 socket refuse the internet, which "
            "is the property this release is named after"
        )
    wanted = str(revision).strip().lower()
    if receipt.foundation_revision != wanted:
        raise SpecError(
            f"the receipt is for {receipt.foundation_revision} and the release "
            f"is {wanted}. A rehearsal that passed on another commit says "
            "nothing about this one"
        )
    unsatisfied = [
        result
        for result in receipt.results
        if not result.status.satisfies_publication
    ]
    if unsatisfied:
        detail = "; ".join(
            f"{result.item.number} {result.code}={result.status.value}"
            for result in sorted(unsatisfied, key=lambda r: r.item.number)
        )
        raise SpecError(
            f"{len(unsatisfied)} of {len(REQUIRED_ITEMS)} Lane 3 items are not "
            f"`executed_passed`: {detail}. Only a controller-driven pass "
            "satisfies publication — a hand measurement proves the operator can "
            "do it, and a vacuous check observed nothing"
        )


def _table(rows: Mapping[str, tuple[RequirementStatus, str]]) -> list[str]:
    """The sixteen rows plus their tally, derived from ONE mapping.

    Shared by the receipt renderer and the pending renderer so the two can
    never disagree about what the items are or how they are counted — which is
    precisely the failure the generated document exists to prevent.
    """
    lines = [
        "| # | Item | Evidence from | Status | Detail |",
        "|---|---|---|---|---|",
    ]
    tally: dict[str, int] = {}
    for item in REQUIRED_ITEMS:
        status, detail = rows[item.code]
        tally[status.value] = tally.get(status.value, 0) + 1
        mark = "**PASS**" if status.satisfies_publication else status.value
        lines.append(
            f"| {item.number} | {item.title} | {item.evidence_from} | "
            f"{mark} | {detail} |"
        )
    lines.extend(["", "## Tally", ""])
    for status in RequirementStatus:
        lines.append(f"- `{status.value}`: {tally.get(status.value, 0)}")
    passed = tally.get(RequirementStatus.EXECUTED_PASSED.value, 0)
    total = len(REQUIRED_ITEMS)
    lines.extend(
        [
            "",
            f"**Publication requires {total} × `executed_passed`.** "
            + (
                "This receipt satisfies it."
                if passed == total
                else f"This does not: {total - passed} item(s) short."
            ),
            "",
        ]
    )
    return lines


def render_pending_document(
    rows: Mapping[str, tuple[RequirementStatus, str]], *, reason: str
) -> str:
    """The status table for a state where NO Lane 3 receipt exists yet.

    A receipt cannot be constructed before an authorization exists — gate item
    9 binds three terms and the middle one is the authorized plan, so
    `build_receipt` refuses. That refusal is correct and it leaves a gap: the
    repository still owes a truthful status document in the meantime.

    This renders one, through the same item list and the same tally as the
    receipt renderer, so the pre-execution document cannot drift from the
    post-execution one or contradict its own rows.
    """
    missing = sorted(set(_BY_CODE) - set(rows))
    if missing:
        raise SpecError(
            f"the pending document omits {missing}. Every one of the sixteen "
            "carries an explicit status — an absent item is not an implicit pass"
        )
    passed = sum(
        1 for status, _ in rows.values() if status.satisfies_publication
    )
    lines = [
        "<!-- GENERATED by dotmac_deployment_foundation.rehearsal."
        "render_pending_document — do not hand-edit. -->",
        "",
        f"# Exposure rehearsal (Lane {LANE}) — {passed} of {len(REQUIRED_ITEMS)} "
        "executed and passed",
        "",
        f"**No Lane {LANE} receipt exists.** {reason}",
        "",
        "Historical hand measurements are retained below as supporting context. "
        "They are recorded as `hand_measured`, which **cannot satisfy "
        "publication**: a hand-driven step proves the operator can do it, not "
        "that the controller can. `vacuous` means the check ran against a "
        "fixture that could not exercise it.",
        "",
    ]
    lines.extend(_table(rows))
    return "\n".join(lines)


def render_status_document(receipt: RehearsalReceiptV1) -> str:
    """The status table, DERIVED from the receipt.

    This function exists because the previous document was hand-maintained and
    its header contradicted its own table. A generated summary cannot: the
    counts below are computed from the same rows they summarise.
    """
    rows = {
        result.code: (result.status, result.detail) for result in receipt.results
    }
    passed = sum(
        1 for status, _ in rows.values() if status.satisfies_publication
    )
    lines: list[str] = [
        "<!-- GENERATED by dotmac_deployment_foundation.rehearsal."
        "render_status_document — do not hand-edit. -->",
        "",
        f"# Exposure rehearsal (Lane {receipt.lane}) — {passed} of "
        f"{len(REQUIRED_ITEMS)} executed and passed",
        "",
        f"- **Foundation revision:** `{receipt.foundation_revision}`",
        f"- **Authorization run:** `{receipt.authorization_run_id}`",
        "- **Bound digest (all three terms):** "
        f"`{receipt.content['descriptor_digest']}`",
        f"- **Target:** `{receipt.content['target']}` "
        f"under lease `{receipt.content['lease_id']}`",
        f"- **Controller identity:** `{receipt.content['controller_identity']}`",
        f"- **External probe:** `{receipt.content['probe_identity']}`",
        f"- **Window:** {receipt.content['started_at']} → "
        f"{receipt.content['finished_at']}",
        f"- **Receipt digest:** `{receipt.sha256_digest()}`",
        "",
        "Only `executed_passed` satisfies publication. Every other status is "
        "reported as itself rather than folded into a total — the 2026-08-29 "
        'count reached "14 of 16" by counting `partial` and `n/a` as closed.',
        "",
    ]
    lines.extend(_table(rows))
    return "\n".join(lines)
