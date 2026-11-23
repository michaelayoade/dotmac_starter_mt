"""The negative-target compensation proof and the post-compensation probe (S8b).

PRE-E11-DESIGN section 6 row 8 (`provoked_rollback`) asks whether the Executor's
compensation works when a verification genuinely fails. Gate 2 rules that this is
proven on a SEPARATE negative target, by a second genuine Control dispatch, and
that the proof sits inside the evidence the positive receipt binds.

This module owns the two documents that carry it and nothing else:

* `lane3.negative-compensation-proof.v1`: UNSIGNED, closed, canonical. The
  LAUNCHER builds it from bytes only it holds (the negative target's exposure
  records, streamed to it in `compensation_window_open`, and the probe it took in
  that window). It is authenticated two ways: it sits inside the positive
  ciphertext, which the positive receipt binds by `evidence_bundle_digest`, and
  Michael's SSHSIG over the offline result covers the join that consumed it.
* `lane3.post-compensation-probe.v1`: the two external rows the launcher
  measured after the compensation and before the target's cleanup.

## Joined, not carried

Only the two `_b64` fields are content. Every other field is a CLAIM that the
offline verifier re-derives from the negative target's own CP-6 export, its own
host admission, envelopes and canonical plan (`lane3_offline_orchestrator`). The
compensation is re-derived HERE from the recorded observations with Foundation's
own public functions, never from the target's text (`require_compensation`).

Size: the proof member is capped at `MAX_CALLS_BYTES` (16 MiB) as a whole and the
exposure calls ride inside it as base64, so the EFFECTIVE cap on the raw exposure
calls is about 12 MiB (minus the probe); an oversize proof is refused.

Refusals carry a stable code and never any supplied text.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from dotmac_deployment_foundation.exposure import (
    foreign_rules,
    managed_ports,
    ownership_comment,
    require_preserved_foreign_rules,
    verify_exposure,
)
from lane3_bundle_verification import (
    BundleVerificationRefused,
    require_exposure_records,
)
from lane3_exposure_evidence import ExposureEvidenceRefused, parse_exposure_calls
from lane3_exposure_recording import ExposureCallRecord
from lane3_provocation import ProvocationError, private_port

__all__ = [
    "NEGATIVE_EXIT_STATUS",
    "NEGATIVE_TERMINAL_REFUSAL",
    "PROBE_ROW_CODES",
    "PROBE_SCHEMA",
    "PROBE_SKEW_SECONDS",
    "PROOF_SCHEMA",
    "ROW_CODE",
    "ROW_EVIDENCE_PATTERN",
    "ROW_EVIDENCE_PREFIX",
    "NegativeProof",
    "PostCompensationProbe",
    "ProofRefused",
    "ProofTerminal",
    "ProbeRow",
    "build_probe",
    "build_proof",
    "compensation_row",
    "parse_probe",
    "parse_proof",
    "require_compensation",
    "require_probe",
    "require_row_shape",
    "row_evidence",
]

PROOF_SCHEMA: Final = "lane3.negative-compensation-proof.v1"
PROBE_SCHEMA: Final = "lane3.post-compensation-probe.v1"

#: Row 8 of the positive receipt (Foundation's item code).
ROW_CODE: Final = "provoked_rollback"
ROW_EVIDENCE_PREFIX: Final = "lane3-negative-compensation-proof:sha256:"
ROW_EVIDENCE_PATTERN: Final = re.compile(
    r"lane3-negative-compensation-proof:sha256:[0-9a-f]{64}"
)
_PASSED: Final = "executed_passed"

#: The negative target's terminal report. Foundation's closed `TerminalRefusal`
#: has no dedicated exposure-verification member: the Executor's refusal arrives
#: below the Lane 3 classes while the lease is in hand, which
#: `classify_refusal` maps to `host_state_uncertified`. One constant, so a later
#: ruling that names another member changes this line and its tests only.
NEGATIVE_TERMINAL_REFUSAL: Final = "host_state_uncertified"
NEGATIVE_EXIT_STATUS: Final = 1

#: The two external rows measured after the compensation: both wanted
#: UNREACHABLE, each with its own tcp/22 positive control and a running service.
#: `positive_v6` is a different row and is not used here.
PROBE_ROW_CODES: Final = ("external_negative_v6", "external_v4")

#: Mirror of `lane3_lease_store.MAX_RELEASE_SKEW_SECONDS` (a test pins equality;
#: the offline verifier does not import the lease store).
PROBE_SKEW_SECONDS: Final = 60

MAX_PROBE_BYTES: Final = 65536
MAX_CALLS_BYTES: Final = 16 * 1024 * 1024
_MAX_DETAIL: Final = 4000
_MAX_EVIDENCE_ITEMS: Final = 16
_MAX_EVIDENCE_TEXT: Final = 500
_TOKEN: Final = re.compile(r"[\x21-\x7e]{1,256}")
_TIME: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ROW_KEYS: Final = frozenset({"code", "status", "detail", "evidence"})
_STATUSES: Final = frozenset(
    {"executed_passed", "executed_failed", "blocked", "not_applicable"}
)


class ProofRefused(ValueError):
    """A proof or probe is unusable. `code` is stable; no supplied text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(code: str) -> None:
    raise ProofRefused(code)


def _canonical(document: Any) -> bytes:
    """THE canonical encoding of both documents: sorted, compact, one newline."""
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _load(data: object, limit: int, code: str) -> dict[str, Any]:
    if type(data) is not bytes or not 0 < len(data) <= limit:
        _refuse(code)
    assert isinstance(data, bytes)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        _refuse(code)
    if type(document) is not dict:
        _refuse(code)
    return document


def _closed(value: object, keys: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != keys:
        _refuse(code)
    assert isinstance(value, dict)
    return value


def _token(value: object, code: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        _refuse(code)
    assert isinstance(value, str)
    return value


def _count(value: object, code: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        _refuse(code)
    assert isinstance(value, int)
    return value


def _b64(value: object, limit: int, code: str) -> bytes:
    if type(value) is not str or len(value) > limit * 2:
        _refuse(code)
    assert isinstance(value, str)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        _refuse(code)
    if not raw or len(raw) > limit or base64.b64encode(raw).decode("ascii") != value:
        _refuse(code)
    return raw


def _instant(value: object, code: str) -> datetime:
    if type(value) is not str or not _TIME.fullmatch(value):
        _refuse(code)
    assert isinstance(value, str)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _refuse(code)
    raise AssertionError  # pragma: no cover


# --- the post-compensation probe ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ProbeRow:
    code: str
    status: str
    detail: str
    evidence: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class PostCompensationProbe:
    dispatch_id: str
    host_id: str
    measured_at: datetime
    rows: tuple[ProbeRow, ...]


def _probe_row(value: object) -> ProbeRow:
    row = _closed(value, _ROW_KEYS, "probe-row")
    code, status, detail, evidence = (
        row["code"],
        row["status"],
        row["detail"],
        row["evidence"],
    )
    if (
        type(code) is not str
        or type(status) is not str
        or status not in _STATUSES
        or type(detail) is not str
        or not detail.strip()
        or len(detail) > _MAX_DETAIL
        or type(evidence) is not list
        or len(evidence) > _MAX_EVIDENCE_ITEMS
        or any(
            type(item) is not str or len(item) > _MAX_EVIDENCE_TEXT for item in evidence
        )
    ):
        _refuse("probe-row")
    return ProbeRow(code, status, detail, tuple(evidence))


def _probe_document(
    dispatch_id: str, host_id: str, measured_at: datetime, rows: Sequence[Mapping]
) -> dict[str, Any]:
    return {
        "schema": PROBE_SCHEMA,
        "dispatch_id": dispatch_id,
        "host_id": host_id,
        "measured_at": measured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": [
            {
                "code": row["code"],
                "status": row["status"],
                "detail": row["detail"],
                "evidence": list(row["evidence"]),
            }
            for row in rows
        ],
    }


def parse_probe(data: bytes) -> PostCompensationProbe:
    """The closed probe document, or a refusal. Exactly the two rows, in order."""
    document = _closed(
        _load(data, MAX_PROBE_BYTES, "probe-invalid"),
        frozenset({"schema", "dispatch_id", "host_id", "measured_at", "rows"}),
        "probe-invalid",
    )
    if document["schema"] != PROBE_SCHEMA:
        _refuse("probe-schema")
    rows_raw = document["rows"]
    if type(rows_raw) is not list or len(rows_raw) != len(PROBE_ROW_CODES):
        _refuse("probe-rows")
    rows = tuple(_probe_row(item) for item in rows_raw)
    if tuple(row.code for row in rows) != PROBE_ROW_CODES:
        _refuse("probe-rows")
    probe = PostCompensationProbe(
        dispatch_id=_token(document["dispatch_id"], "probe-invalid"),
        host_id=_token(document["host_id"], "probe-invalid"),
        measured_at=_instant(document["measured_at"], "probe-invalid"),
        rows=rows,
    )
    again = _canonical(
        _probe_document(
            probe.dispatch_id,
            probe.host_id,
            probe.measured_at,
            [dataclasses.asdict(row) for row in rows],
        )
    )
    if again != data:
        _refuse("probe-not-canonical")
    return probe


def build_probe(
    *,
    dispatch_id: str,
    host_id: str,
    measured_at: datetime,
    rows: Sequence[Mapping[str, Any]],
) -> bytes:
    """The canonical probe bytes, re-read by `parse_probe` before they leave."""
    if type(measured_at) is not datetime or measured_at.tzinfo is None:
        _refuse("probe-invalid")
    data = _canonical(_probe_document(dispatch_id, host_id, measured_at, rows))
    parse_probe(data)
    return data


# --- the proof ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ProofTerminal:
    refusal: str
    host_mutated: bool
    exit_status: int
    #: The target's terminal report acknowledged that the compensation window
    #: closed with `compensation_probe_done` (never with a timeout or an abort).
    window_done: bool


@dataclasses.dataclass(frozen=True, slots=True)
class NegativeProof:
    """The parsed proof. Everything but the two byte fields is a CLAIM."""

    positive_dispatch_id: str
    dispatch_id: str
    target_id: str
    host_id: str
    execution_sequence: int
    attempt_no: int
    lease_authorization_run: str
    terminal: ProofTerminal
    exposure_calls: bytes = dataclasses.field(repr=False)
    records: tuple[ExposureCallRecord, ...] = dataclasses.field(repr=False)
    probe_bytes: bytes = dataclasses.field(repr=False)
    probe: PostCompensationProbe = dataclasses.field(repr=False)
    member_bytes: bytes = dataclasses.field(repr=False)

    @property
    def member_digest(self) -> str:
        return hashlib.sha256(self.member_bytes).hexdigest()


def _proof_document(
    *,
    positive_dispatch_id: str,
    dispatch_id: str,
    target_id: str,
    host_id: str,
    execution_sequence: int,
    attempt_no: int,
    lease_authorization_run: str,
    refusal: str,
    host_mutated: bool,
    exit_status: int,
    window_done: bool,
    exposure_calls: bytes,
    probe_bytes: bytes,
) -> dict[str, Any]:
    return {
        "schema": PROOF_SCHEMA,
        "positive_dispatch_id": positive_dispatch_id,
        "negative": {
            "dispatch_id": dispatch_id,
            "target_id": target_id,
            "host_id": host_id,
            "execution_sequence": execution_sequence,
            "attempt_no": attempt_no,
            "lease_authorization_run": lease_authorization_run,
            "terminal": {
                "refusal": refusal,
                "host_mutated": host_mutated,
                "exit_status": exit_status,
                "window_done": window_done,
            },
            "exposure_calls_b64": base64.b64encode(exposure_calls).decode("ascii"),
            "post_compensation_probe_b64": base64.b64encode(probe_bytes).decode(
                "ascii"
            ),
        },
    }


def parse_proof(data: bytes) -> NegativeProof:
    """The closed, canonical proof, with both byte fields parsed by their owners."""
    document = _closed(
        _load(data, MAX_CALLS_BYTES, "proof-invalid"),
        frozenset({"schema", "positive_dispatch_id", "negative"}),
        "proof-invalid",
    )
    if document["schema"] != PROOF_SCHEMA:
        _refuse("proof-schema")
    negative = _closed(
        document["negative"],
        frozenset(
            {
                "dispatch_id",
                "target_id",
                "host_id",
                "execution_sequence",
                "attempt_no",
                "lease_authorization_run",
                "terminal",
                "exposure_calls_b64",
                "post_compensation_probe_b64",
            }
        ),
        "proof-invalid",
    )
    terminal = _closed(
        negative["terminal"],
        frozenset({"refusal", "host_mutated", "exit_status", "window_done"}),
        "proof-invalid",
    )
    if (
        type(terminal["refusal"]) is not str
        or len(terminal["refusal"]) > 64
        or type(terminal["host_mutated"]) is not bool
        or type(terminal["window_done"]) is not bool
        or type(terminal["exit_status"]) is not int
        or not 0 <= terminal["exit_status"] <= 255
    ):
        _refuse("proof-invalid")
    calls = _b64(negative["exposure_calls_b64"], MAX_CALLS_BYTES, "proof-calls")
    probe_bytes = _b64(
        negative["post_compensation_probe_b64"], MAX_PROBE_BYTES, "proof-probe"
    )
    try:
        records = parse_exposure_calls(calls)
    except ExposureEvidenceRefused:
        _refuse("proof-calls")
    probe = parse_probe(probe_bytes)
    proof = NegativeProof(
        positive_dispatch_id=_token(document["positive_dispatch_id"], "proof-invalid"),
        dispatch_id=_token(negative["dispatch_id"], "proof-invalid"),
        target_id=_token(negative["target_id"], "proof-invalid"),
        host_id=_token(negative["host_id"], "proof-invalid"),
        execution_sequence=_count(negative["execution_sequence"], "proof-invalid"),
        attempt_no=_count(negative["attempt_no"], "proof-invalid"),
        lease_authorization_run=_token(
            negative["lease_authorization_run"], "proof-invalid"
        ),
        terminal=ProofTerminal(
            terminal["refusal"],
            terminal["host_mutated"],
            terminal["exit_status"],
            terminal["window_done"],
        ),
        exposure_calls=calls,
        records=records,
        probe_bytes=probe_bytes,
        probe=probe,
        member_bytes=data,
    )
    again = _canonical(
        _proof_document(
            positive_dispatch_id=proof.positive_dispatch_id,
            dispatch_id=proof.dispatch_id,
            target_id=proof.target_id,
            host_id=proof.host_id,
            execution_sequence=proof.execution_sequence,
            attempt_no=proof.attempt_no,
            lease_authorization_run=proof.lease_authorization_run,
            refusal=proof.terminal.refusal,
            host_mutated=proof.terminal.host_mutated,
            exit_status=proof.terminal.exit_status,
            window_done=proof.terminal.window_done,
            exposure_calls=calls,
            probe_bytes=probe_bytes,
        )
    )
    if again != data:
        _refuse("proof-not-canonical")
    return proof


def build_proof(
    *,
    positive_dispatch_id: str,
    dispatch_id: str,
    target_id: str,
    host_id: str,
    execution_sequence: int,
    attempt_no: int,
    lease_authorization_run: str,
    terminal_refusal: str,
    host_mutated: bool,
    exit_status: int,
    window_done: bool,
    exposure_calls: bytes,
    probe_bytes: bytes,
) -> bytes:
    """The canonical proof bytes, re-read by `parse_proof` before they leave.

    The launcher calls this with bytes it holds; it asserts nothing the offline
    verifier will not re-derive.
    """
    data = _canonical(
        _proof_document(
            positive_dispatch_id=positive_dispatch_id,
            dispatch_id=dispatch_id,
            target_id=target_id,
            host_id=host_id,
            execution_sequence=execution_sequence,
            attempt_no=attempt_no,
            lease_authorization_run=lease_authorization_run,
            refusal=terminal_refusal,
            host_mutated=host_mutated,
            exit_status=exit_status,
            window_done=window_done,
            exposure_calls=exposure_calls,
            probe_bytes=probe_bytes,
        )
    )
    parse_proof(data)
    return data


# --- row 8 --------------------------------------------------------------------


def row_evidence(member_bytes: bytes) -> str:
    """The row's evidence string: the digest of the exact member bytes."""
    return ROW_EVIDENCE_PREFIX + hashlib.sha256(member_bytes).hexdigest()


def compensation_row(member_bytes: bytes) -> dict[str, Any]:
    """The positive receipt's row 8, as the launcher asserts it in the T2 frame.

    The launcher refuses to assert a proof it cannot itself parse. The assertion
    is not a measurement: publication still needs the authenticated offline
    result, which E9 refuses unless the proof is re-derived (N1-N7).
    """
    parse_proof(member_bytes)
    return {
        "code": ROW_CODE,
        "status": _PASSED,
        "detail": (
            "the launcher bound the unsigned negative-target compensation proof "
            "into this bundle; the compensation and the post-compensation probe "
            "are re-derived offline by E9, not by the launcher or this target"
        ),
        "evidence": [row_evidence(member_bytes)],
    }


def require_row_shape(
    code: str, status: str, detail: str, evidence: Sequence[str]
) -> None:
    """The target's SHAPE-only check of row 8 (it holds no proof bytes)."""
    if (
        code != ROW_CODE
        or status != _PASSED
        or not detail.strip()
        or len(evidence) != 1
        or not ROW_EVIDENCE_PATTERN.fullmatch(evidence[0])
    ):
        _refuse("row-shape")


# --- N6 / N7: re-derived offline ----------------------------------------------


def require_compensation(
    proof: NegativeProof, *, execution_plan: Any, descriptor: Any
) -> None:
    """N6: re-derive the compensation from the recorded observations.

    Nothing here reads a claim of the target. The records must be exactly the
    Executor's sequence for a verification that genuinely failed and was
    compensated: `observe`, one `replace_rules` per reconciliation, `observe`,
    `restore_chains`, `observe`, none of them failed.
    """
    try:
        records = require_exposure_records(
            proof.exposure_calls, execution_plan=execution_plan, descriptor=descriptor
        )
    except BundleVerificationRefused:
        _refuse("compensation-records")
    reconciliations = tuple(execution_plan.exposure_reconciliations)
    count = len(reconciliations)
    methods = (
        ("observe",)
        + ("replace_rules",) * count
        + ("observe", "restore_chains", "observe")
    )
    if (
        tuple(record.method for record in records) != methods
        or any(record.exception_type for record in records)
        or any(
            (record.family, record.chain) != (item.family, item.chain)
            for record, item in zip(
                records[1 : 1 + count], reconciliations, strict=True
            )
        )
    ):
        _refuse("compensation-sequence")
    snapshot = records[0].observation
    applied = records[count + 1].observation
    restore = records[count + 2]
    final = records[count + 3].observation
    if snapshot is None or applied is None or final is None:
        _refuse("compensation-sequence")
    owner = ownership_comment(descriptor.product)
    managed = managed_ports(descriptor)
    try:
        port = private_port(descriptor)
    except ProvocationError:
        _refuse("compensation-baseline")
    # The first observation proves the baseline, however it was prepared.
    inert = snapshot.chain("ipv6", "DOCKER-USER")
    if inert is None or not inert.rules_for(port):
        _refuse("compensation-baseline")
    families = {
        rule.family
        for rule in foreign_rules(snapshot, owner=owner, managed_ports=managed)
    }
    if not {"ipv4", "ipv6"} <= families:
        _refuse("compensation-baseline")
    try:
        require_preserved_foreign_rules(
            snapshot, applied, owner=owner, managed=managed, phase="apply"
        )
        report = verify_exposure(descriptor, applied)
    except Exception:
        _refuse("compensation-verification")
    if report.ok or "inert_chain" not in {finding.code for finding in report.refusals}:
        _refuse("compensation-verification")
    if restore.chains != snapshot.chains:
        _refuse("compensation-restore")
    try:
        require_preserved_foreign_rules(
            snapshot, final, owner=owner, managed=managed, phase="restore"
        )
    except Exception:
        _refuse("compensation-restore")
    terminal = proof.terminal
    if (
        terminal.refusal != NEGATIVE_TERMINAL_REFUSAL
        or terminal.host_mutated is not True
        or terminal.exit_status != NEGATIVE_EXIT_STATUS
    ):
        _refuse("compensation-terminal")


def require_probe(
    proof: NegativeProof, *, ledger_created_at: datetime, released_at: datetime
) -> None:
    """N7: the probe is this target's, passed, and taken inside its lease window.

    A timeout, an abort or a malformed frame yields no probe, so no proof can
    carry one and this join (or the bundle join before it) refuses.
    """
    if proof.terminal.window_done is not True:
        # The target never acknowledged that the window closed with `done`: the
        # probe may have been taken after its cleanup began.
        _refuse("probe-window-unacknowledged")
    probe = proof.probe
    if probe.dispatch_id != proof.dispatch_id or probe.host_id != proof.host_id:
        _refuse("probe-identity")
    if any(row.status != _PASSED for row in probe.rows):
        _refuse("probe-not-passed")
    skew = timedelta(seconds=PROBE_SKEW_SECONDS)
    if not ledger_created_at - skew <= probe.measured_at <= released_at + skew:
        _refuse("probe-outside-window")
