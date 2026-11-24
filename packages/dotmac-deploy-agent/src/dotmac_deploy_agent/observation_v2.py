"""The agent observation envelope (Control `contract/target_api_v2.json`, N-1).

Control's `record_observation` accepts only a statement signed by the target's PoP
key: `{"signature": <b64url>, "statement": {schema, kind, dispatch_id, target_ref,
key_id, outcome, reason, details, observed_at}}`, the signature over the canonical
JSON of the statement (sorted keys, compact, ASCII). The agent keeps its internal
`dotmac.deploy_observation/1` record in the outbox and converts at post time, so a
sealed record is never altered. Domain separation: the signed bytes start `{`, a
request message starts `dotmac.target_request.v1`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from .canon import canonical_json
from .wire import PopSigner

SCHEMA: Final = "dotmac.agent-observation/2"
MAX_DETAILS_BYTES: Final = 16_384
MAX_DETAILS_DEPTH: Final = 4
MAX_ENVELOPE_BYTES: Final = 65_536
KIND_OUTCOMES: Final = {
    "deploy": ("succeeded", "failed", "refused"),
    "job": ("succeeded", "failed", "refused"),
    "reconcile": ("reconciled", "failed"),
}
#: The agent's internal outcome words -> Control's, per kind.
_OUTCOME: Final = {
    "completed": "succeeded",
    "failed": "failed",
    "refused": "refused",
    "integrity_violation": "failed",  # the reason keeps `integrity_violation`
}
#: A reconcile reports facts only: `reconciled` means the observation is complete,
#: `failed` that it could not be made. The agent never sends `not_needed`; Control's
#: pure `reconcile_marker` decides.
_RECONCILE_OUTCOME: Final = {
    "reconciled": "reconciled",
    "failed": "failed",
    "refused": "failed",
}
#: Plan operation -> the one non-reconcile kind it may report.
_KIND_FOR_OPERATION: Final = {
    "deploy": "deploy",
    "rollback": "deploy",
    "run_job": "job",
}


def kind_for_operation(operation: str | None) -> str:
    """`job` <=> run_job; `deploy` <=> deploy/rollback. Anything else is reported
    as a deploy (Control records the mismatch rather than the agent guessing)."""
    return _KIND_FOR_OPERATION.get(operation or "", "deploy")


def _json_safe(value: object, depth: int) -> bool:
    # Control's rule: a scalar is fine at any level; only a CONTAINER nested deeper
    # than MAX_DETAILS_DEPTH is refused (a reconcile's repo_digests strings sit at 5).
    if value is None or isinstance(value, bool | int | str):
        return True
    if depth > MAX_DETAILS_DEPTH:
        return False
    if isinstance(value, list):
        return all(_json_safe(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _json_safe(v, depth + 1) for k, v in value.items()
        )
    return False  # floats and every other type


def shaped_details(details: Mapping[str, object]) -> dict[str, object]:
    """Details within Control's limits (depth, size, JSON values only). Evidence
    that does not fit is replaced by a marker, never silently reshaped."""
    plain = dict(details)
    if not _json_safe(plain, 1):
        return {"details_dropped": "shape"}
    if len(canonical_json(plain)) > MAX_DETAILS_BYTES:
        return {"details_dropped": "size"}
    return plain


def statement_from(
    observation: Mapping[str, object], *, key_id: str
) -> dict[str, object]:
    """The v2 statement for an internal observation record. Raises ValueError for
    a record that cannot be expressed (the caller dead-letters it)."""
    kind = observation.get("kind")
    outcome = observation.get("outcome")
    if not isinstance(kind, str) or kind not in KIND_OUTCOMES:
        raise ValueError("kind")
    mapping = _RECONCILE_OUTCOME if kind == "reconcile" else _OUTCOME
    mapped = mapping.get(str(outcome))
    if mapped is None or mapped not in KIND_OUTCOMES[kind]:
        raise ValueError("outcome")
    reason = observation.get("reason")
    if reason is None:
        reason = ""
    details = observation.get("details")
    if (
        not isinstance(reason, str)
        or len(reason) > 512
        or reason != reason.strip()
        or not isinstance(details, dict)
    ):
        raise ValueError("shape")
    shaped = shaped_details(details)
    if kind == "reconcile" and "details_dropped" in shaped:
        mapped = "failed"  # evidence that did not fit is never `reconciled`
    return {
        "schema": SCHEMA,
        "kind": kind,
        "dispatch_id": observation["dispatch_id"],
        "target_ref": observation["target_ref"],
        "key_id": key_id,
        "outcome": mapped,
        "reason": reason,
        "details": shaped,
        "observed_at": observation["observed_at"],
    }


def signed_bytes(statement: Mapping[str, object]) -> bytes:
    """What the PoP key signs: the canonical JSON of the statement."""
    return canonical_json(dict(statement))


def seal(statement: Mapping[str, object], signer: PopSigner) -> dict[str, object]:
    envelope: dict[str, object] = {
        "signature": signer.sign(signed_bytes(statement)),
        "statement": dict(statement),
    }
    if len(canonical_json(envelope)) > MAX_ENVELOPE_BYTES:
        raise ValueError("size")
    return envelope
