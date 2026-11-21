"""Lane 3, the LAUNCHER half of the evidence hand-back (E7 / T3).

If the target encrypted the evidence it could rewrite the probe bytes first (E3
audit S5). So the launcher does: it holds the probe bytes the target never saw,
encrypts them together with the exposure calls the target streamed, and returns
only ciphertext. The target assembles the receipt around that ciphertext, in the
process holding the live grant. Afterwards the launcher checks the receipt it
gets back against the ciphertext it made, so a receipt that names some other
bundle cannot be uploaded as this run's.

Two functions, and nothing else:

* :func:`finalize` takes the target's exposure calls, refuses anything that is
  not the exact canonical serialization (the same strictness the offline
  verifier applies), and encrypts with `lane3_evidence_finalization`, which owns
  the recipient, the tool pin and the create-only output.
* :func:`verify_receipt` re-reads the returned receipt with the closed-key-set
  reader the oracle uses and refuses unless every binding the launcher knows
  independently matches.

Refusals carry a stable code and never a value.
"""

from __future__ import annotations

import base64
import binascii
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import lane3_evidence_finalization as evidence_finalization
import lane3_negative_proof as negative_proof
import lane3_receipt_v2 as receipt_v2
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
    require_execution_run,
)
from lane3_exposure_evidence import ExposureEvidenceRefused, parse_exposure_calls

__all__ = ["EvidenceHandBackRefused", "finalize", "verify_receipt"]

MAX_CALLS_BYTES: Final = evidence_finalization.MAX_MEMBER_BYTES
MAX_RECEIPT_BYTES: Final = 4 * 1024 * 1024


class EvidenceHandBackRefused(Exception):
    """The hand-back is unacceptable. Carries a CODE, never evidence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _decode(value: object, *, limit: int, code: str) -> bytes:
    if not isinstance(value, str) or len(value) > limit * 2:
        raise EvidenceHandBackRefused(code)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise EvidenceHandBackRefused(code) from None
    if not raw or len(raw) > limit:
        raise EvidenceHandBackRefused(code)
    return raw


def finalize(
    *,
    probe_bytes: bytes,
    exposure_calls_b64: object,
    negative_compensation: bytes | None,
    encryption: evidence_finalization.EncryptionContext,
    output: pathlib.Path,
) -> bytes:
    """Encrypt the launcher's probe bytes with the target's exposure calls and the
    negative-target compensation proof (the v2 bundle, S8b). `None` is refused.

    Size: the proof member is capped at 16 MiB as a whole and carries the exposure
    calls as base64, so the effective cap on the raw calls is about 12 MiB.

    Returns the ciphertext exactly as written to `output`. `output` is
    create-only: `finalize_evidence` refuses an existing name.
    """
    calls = _decode(
        exposure_calls_b64, limit=MAX_CALLS_BYTES, code="exposure-calls-invalid"
    )
    try:
        parse_exposure_calls(calls)
    except ExposureEvidenceRefused:
        raise EvidenceHandBackRefused("exposure-calls-not-canonical") from None
    try:
        negative_proof.parse_proof(negative_compensation)
    except negative_proof.ProofRefused:
        raise EvidenceHandBackRefused("negative-compensation-invalid") from None
    return evidence_finalization.finalize_evidence_v2(
        probe_evidence=probe_bytes,
        exposure_calls=calls,
        negative_compensation=negative_compensation,
        context=encryption,
        output=output,
    )


def _require_rows_exactly(
    receipt: RehearsalReceiptV2, sent_rows: Sequence[Mapping[str, Any]]
) -> None:
    """The six launcher-measured rows in the receipt are EXACTLY the rows sent.

    The target assembles the receipt, so it could change a status or a detail
    between receiving a row and writing it. Items 11-16 are the ones only the
    launcher can measure, so equality is checked row by row on code, status,
    detail and evidence, and the receipt may carry each sent code exactly once.
    """
    if not sent_rows:
        raise EvidenceHandBackRefused("external-rows-not-supplied")
    sent: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for row in sent_rows:
        code = row["code"]
        if code in sent:
            raise EvidenceHandBackRefused("external-rows-duplicate")
        sent[code] = (row["status"], row["detail"], tuple(row["evidence"]))
    received = [r for r in receipt.results if r.code in sent]
    if len(received) != len(sent):
        raise EvidenceHandBackRefused("external-rows-mismatch")
    for result in received:
        if (result.status.value, result.detail, result.evidence) != sent[result.code]:
            raise EvidenceHandBackRefused("external-rows-mismatch")


def verify_receipt(
    *,
    receipt_b64: object,
    receipt_digest: object,
    ciphertext: bytes,
    execution_run: ExecutionRunBindingV1,
    controller_identity: str,
    lease_id: str,
    host_id: str,
    sent_rows: Sequence[Mapping[str, Any]],
    foundation_revision: str,
    foundation_artifact: str,
    fixture_digest: str,
    probe_vantage_ref: str,
    plan_digest: str,
) -> RehearsalReceiptV2:
    """The receipt the target returned, or a refusal naming the first mismatch.

    Every comparison is against a value the launcher holds independently of the
    target: its own ciphertext, its own run coordinates, its own launch-key
    fingerprint, its own lease, the host it dispatched, the rows it measured, the
    revisions and artifact it passed, the fixture it hashed, the vantage
    reference it bound and the plan digest the target reported before it was
    allowed to proceed. Any mismatch is a refusal; there is no partial pass.
    """
    raw = _decode(receipt_b64, limit=MAX_RECEIPT_BYTES, code="receipt-invalid")
    try:
        receipt = RehearsalReceiptV2.from_json(raw)
    except SpecError:
        raise EvidenceHandBackRefused("receipt-unreadable") from None
    if receipt.canonical_bytes() != raw:
        raise EvidenceHandBackRefused("receipt-not-canonical")
    if not isinstance(receipt_digest, str) or receipt.sha256_digest() != receipt_digest:
        raise EvidenceHandBackRefused("receipt-digest-mismatch")
    try:
        ciphertext_digest = receipt_v2.evidence_bundle_digest(ciphertext)
    except receipt_v2.ReceiptRefused:
        raise EvidenceHandBackRefused("ciphertext-not-age") from None
    if receipt.content.get("evidence_bundle_digest") != ciphertext_digest:
        raise EvidenceHandBackRefused("evidence-bundle-digest-mismatch")
    try:
        require_execution_run(receipt, run=execution_run)
    except SpecError:
        raise EvidenceHandBackRefused("execution-run-mismatch") from None
    for key, expected in (
        ("controller_identity", controller_identity),
        ("lease_id", lease_id),
        ("host_id", host_id),
        ("foundation_revision", foundation_revision),
        ("foundation_artifact_digest", foundation_artifact),
        ("fixture_digest", fixture_digest),
        ("probe_vantage_ref", probe_vantage_ref),
        ("execution_plan_digest", plan_digest),
    ):
        if not expected or receipt.content.get(key) != expected:
            raise EvidenceHandBackRefused(f"{key}-mismatch")
    _require_rows_exactly(receipt, sent_rows)
    return receipt
