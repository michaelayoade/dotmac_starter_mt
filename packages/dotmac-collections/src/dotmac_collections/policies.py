"""Immutable versioned collection policies and one ladder evaluator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal, TypeAlias
from uuid import UUID

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.contracts import CollectionTiming

AnchorKind = Literal["exposure_at", "request_at", "accepted_notice_receipt_at"]
RequestKind = Literal["notice", "action"]


@dataclass(frozen=True, slots=True)
class GraceRuleV1:
    duration: timedelta
    anchor: AnchorKind

    def __post_init__(self) -> None:
        if self.duration < timedelta(0):
            raise ValueError("grace duration must be non-negative")
        if self.anchor not in (
            "exposure_at",
            "request_at",
            "accepted_notice_receipt_at",
        ):
            raise ValueError("grace anchor is unsupported")


@dataclass(frozen=True, slots=True)
class PolicyStepDraftV1:
    code: str
    ordinal: int
    offset: timedelta
    offset_anchor: AnchorKind
    request_kind: RequestKind
    action_code: str | None
    receipt_required: bool

    def __post_init__(self) -> None:
        require_text("code", self.code)
        if self.ordinal < 1:
            raise ValueError("ordinal must be positive")
        if self.offset < timedelta(0):
            raise ValueError("offset must be non-negative")
        if self.offset_anchor not in (
            "exposure_at",
            "request_at",
            "accepted_notice_receipt_at",
        ):
            raise ValueError("offset anchor is unsupported")
        if self.request_kind not in ("notice", "action"):
            raise ValueError("request_kind is unsupported")
        if self.request_kind == "action":
            if self.action_code is None:
                raise ValueError("action step requires action_code")
            require_text("action_code", self.action_code)
        elif self.action_code is not None:
            raise ValueError("notice step cannot carry action_code")


@dataclass(frozen=True, slots=True)
class PolicyVersionDraftV1:
    policy_code: str
    reason_code: str
    collection_timing: CollectionTiming
    grace: GraceRuleV1 | None
    steps: tuple[PolicyStepDraftV1, ...]

    def __post_init__(self) -> None:
        require_text("policy_code", self.policy_code)
        require_text("reason_code", self.reason_code)
        if self.collection_timing not in ("advance", "arrears"):
            raise ValueError("collection_timing is unsupported")
        if not self.steps:
            raise ValueError("policy requires at least one step")
        expected = tuple(range(1, len(self.steps) + 1))
        if tuple(step.ordinal for step in self.steps) != expected:
            raise ValueError("policy step ordinals must be contiguous")
        if len({step.code for step in self.steps}) != len(self.steps):
            raise ValueError("policy step codes must be unique")
        for previous, current in zip(self.steps, self.steps[1:], strict=False):
            if current.offset < previous.offset:
                raise ValueError("policy step offsets must be nondecreasing")


@dataclass(frozen=True, slots=True)
class PolicyPublicationV1:
    policy_version_id: UUID
    version: int
    effective_from: datetime
    actor_ref: str
    reason: str
    published_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be positive")
        require_text("actor_ref", self.actor_ref)
        require_text("reason", self.reason)
        require_aware("effective_from", self.effective_from)
        require_aware("published_at", self.published_at)


@dataclass(frozen=True, slots=True)
class PublishedPolicyVersionV1:
    policy_version_id: UUID
    policy_code: str
    reason_code: str
    collection_timing: CollectionTiming
    grace: GraceRuleV1 | None
    steps: tuple[PolicyStepDraftV1, ...]
    version: int
    effective_from: datetime
    actor_ref: str
    reason: str
    published_at: datetime
    version_fingerprint: str


def _duration_value(value: timedelta) -> tuple[int, int, int]:
    return value.days, value.seconds, value.microseconds


def _version_fingerprint(
    draft: PolicyVersionDraftV1, publication: PolicyPublicationV1
) -> str:
    payload = {
        "draft": {
            "policy_code": draft.policy_code,
            "reason_code": draft.reason_code,
            "collection_timing": draft.collection_timing,
            "grace": None
            if draft.grace is None
            else {
                "duration": _duration_value(draft.grace.duration),
                "anchor": draft.grace.anchor,
            },
            "steps": [
                {
                    **asdict(step),
                    "offset": _duration_value(step.offset),
                }
                for step in draft.steps
            ],
        },
        "publication": {
            "policy_version_id": str(publication.policy_version_id),
            "version": publication.version,
            "effective_from": publication.effective_from.isoformat(),
            "actor_ref": publication.actor_ref,
            "reason": publication.reason,
            "published_at": publication.published_at.isoformat(),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def publish_policy_version(
    draft: PolicyVersionDraftV1,
    publication: PolicyPublicationV1,
) -> PublishedPolicyVersionV1:
    return PublishedPolicyVersionV1(
        policy_version_id=publication.policy_version_id,
        policy_code=draft.policy_code,
        reason_code=draft.reason_code,
        collection_timing=draft.collection_timing,
        grace=draft.grace,
        steps=draft.steps,
        version=publication.version,
        effective_from=publication.effective_from,
        actor_ref=publication.actor_ref,
        reason=publication.reason,
        published_at=publication.published_at,
        version_fingerprint=_version_fingerprint(draft, publication),
    )


@dataclass(frozen=True, slots=True)
class PolicyAnchorSetV1:
    exposure_at: datetime
    request_at: datetime | None
    accepted_notice_receipt_at: datetime | None

    def __post_init__(self) -> None:
        require_aware("exposure_at", self.exposure_at)
        if self.request_at is not None:
            require_aware("request_at", self.request_at)
        if self.accepted_notice_receipt_at is not None:
            require_aware("accepted_notice_receipt_at", self.accepted_notice_receipt_at)

    def get(self, anchor: AnchorKind) -> datetime | None:
        if anchor == "exposure_at":
            return self.exposure_at
        if anchor == "request_at":
            return self.request_at
        return self.accepted_notice_receipt_at


@dataclass(frozen=True, slots=True)
class StepDue:
    step_code: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class StepWaiting:
    step_code: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class AnchorUnavailable:
    anchor_kind: AnchorKind
    step_code: str


@dataclass(frozen=True, slots=True)
class LadderComplete:
    completed_step_codes: tuple[str, ...]


PolicyDecision: TypeAlias = StepDue | StepWaiting | AnchorUnavailable | LadderComplete


def evaluate_policy_version(
    policy: PublishedPolicyVersionV1,
    *,
    anchors: PolicyAnchorSetV1,
    completed_step_codes: tuple[str, ...],
    as_of: datetime,
) -> PolicyDecision:
    require_aware("as_of", as_of)
    all_codes = tuple(step.code for step in policy.steps)
    if completed_step_codes != all_codes[: len(completed_step_codes)]:
        raise ValueError("completed steps must be an exact policy prefix")
    if len(completed_step_codes) == len(policy.steps):
        return LadderComplete(completed_step_codes)
    step = policy.steps[len(completed_step_codes)]
    anchor = anchors.get(step.offset_anchor)
    if anchor is None:
        return AnchorUnavailable(step.offset_anchor, step.code)
    due_at = anchor + step.offset
    if as_of >= due_at:
        return StepDue(step.code, due_at)
    return StepWaiting(step.code, due_at)


__all__ = [
    "AnchorUnavailable",
    "GraceRuleV1",
    "LadderComplete",
    "PolicyAnchorSetV1",
    "PolicyDecision",
    "PolicyPublicationV1",
    "PolicyStepDraftV1",
    "PolicyVersionDraftV1",
    "PublishedPolicyVersionV1",
    "StepDue",
    "StepWaiting",
    "evaluate_policy_version",
    "publish_policy_version",
]
