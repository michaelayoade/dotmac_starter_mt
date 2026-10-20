"""Typed condition registry and the built-in conditions.

A ``ConditionSpec`` declares one condition kind: its pydantic params model
(``extra="forbid"``), the targets it may be used on, the references it makes
(for missing-reference and cycle checks), whether it is monotone (once
satisfied it stays satisfied, which is what permits ``not`` over it) and its
evaluator. Registries refuse duplicate kinds at construction, like the kernel
registries. Product kinds are named ``<product>.<name>`` and are registered by
the product alongside the built-ins.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .facts import LearnerFacts, Target, TargetKind

ALL_TARGETS: frozenset[TargetKind] = frozenset({"course_access", "activity_attempt"})


class DuplicateConditionKindError(ValueError):
    """Two specs declared the same condition kind."""


class UnknownConditionKindError(KeyError):
    """A condition kind is not in the registry."""


@dataclass(frozen=True)
class Reference:
    """A learning object a condition points at."""

    kind: Literal["course", "activity"]
    id: UUID


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    id: str


@dataclass(frozen=True)
class LeafContext:
    facts: LearnerFacts
    target: Target
    path: str


@dataclass(frozen=True)
class LeafResult:
    """Outcome of one leaf. ``code`` is the condition kind when satisfied."""

    passed: bool
    code: str
    params: Mapping[str, object]
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class ConditionSpec:
    kind: str
    params_model: type[BaseModel]
    targets: frozenset[TargetKind]
    references: Callable[[BaseModel], Iterable[Reference]]
    monotone: bool
    evaluate: Callable[[BaseModel, LeafContext], LeafResult]


class ConditionRegistry:
    def __init__(self, specs: Mapping[str, ConditionSpec]) -> None:
        self._specs = dict(specs)

    @classmethod
    def from_specs(cls, specs: Iterable[ConditionSpec]) -> ConditionRegistry:
        by_kind: dict[str, ConditionSpec] = {}
        for spec in specs:
            if spec.kind in by_kind:
                raise DuplicateConditionKindError(spec.kind)
            by_kind[spec.kind] = spec
        return cls(by_kind)

    def get(self, kind: str) -> ConditionSpec | None:
        return self._specs.get(kind)

    def require(self, kind: str) -> ConditionSpec:
        spec = self._specs.get(kind)
        if spec is None:
            raise UnknownConditionKindError(kind)
        return spec

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def __contains__(self, kind: object) -> bool:
        return kind in self._specs


# --------------------------------------------------------------------------
# Params models


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CourseParams(_Params):
    course_id: UUID


class MinimumScoreParams(_Params):
    subject: Literal["activity", "course_final", "course_grade"]
    ref_id: UUID
    min_pct: Annotated[int, Field(strict=True, ge=1, le=100)]


class AttemptsAtLeastParams(_Params):
    activity_id: UUID
    n: Annotated[int, Field(strict=True, ge=1)]


class EnrolledBeforeParams(_Params):
    at: AwareDatetime


class ManualApprovalParams(_Params):
    approver_permission: Annotated[str, Field(min_length=1)]


class TrackPositionParams(_Params):
    track_id: UUID


P = TypeVar("P", bound=BaseModel)


def _typed(model: type[P], value: BaseModel) -> P:
    if not isinstance(value, model):
        raise TypeError(f"expected {model.__name__}, got {type(value).__name__}")
    return value


def _pass(
    kind: str, params: Mapping[str, object], *evidence: EvidenceRef
) -> LeafResult:
    return LeafResult(True, kind, params, evidence)


def _fail(code: str, params: Mapping[str, object]) -> LeafResult:
    return LeafResult(False, code, params)


def _missing(fact: str, **params: object) -> LeafResult:
    return _fail("fact_missing", {"fact": fact, **params})


# --------------------------------------------------------------------------
# Built-in conditions


def _course_completed(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(CourseParams, params)
    detail: dict[str, object] = {"course_id": str(p.course_id)}
    if p.course_id in ctx.facts.completed_course_ids:
        return _pass(
            "course_completed",
            detail,
            EvidenceRef("course_completion", str(p.course_id)),
        )
    return _fail("course_not_completed", detail)


def _certificate_held(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(CourseParams, params)
    detail: dict[str, object] = {"course_id": str(p.course_id)}
    if p.course_id in ctx.facts.certificate_course_ids:
        return _pass(
            "certificate_held", detail, EvidenceRef("certificate", str(p.course_id))
        )
    return _fail("certificate_missing", detail)


def _minimum_score(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(MinimumScoreParams, params)
    facts = ctx.facts
    if p.subject == "activity":
        source, fact = facts.best_fraction_by_activity, "best_fraction_by_activity"
        evidence = EvidenceRef("score", str(p.ref_id))
    elif p.subject == "course_final":
        source, fact = facts.course_final_fraction, "course_final_fraction"
        evidence = EvidenceRef("gradebook", str(p.ref_id))
    else:
        source, fact = facts.course_grade_fraction, "course_grade_fraction"
        evidence = EvidenceRef("gradebook", str(p.ref_id))
    detail: dict[str, object] = {
        "subject": p.subject,
        "ref_id": str(p.ref_id),
        "min_pct": p.min_pct,
    }
    fraction = source.get(p.ref_id)
    if fraction is None:
        return _missing(fact, **detail)
    if fraction >= p.min_pct / 100:
        return _pass("minimum_score", detail, evidence)
    return _fail("score_below_minimum", {**detail, "actual_fraction": fraction})


def _attempts_at_least(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(AttemptsAtLeastParams, params)
    detail: dict[str, object] = {"activity_id": str(p.activity_id), "n": p.n}
    used = ctx.facts.attempts_by_activity.get(p.activity_id)
    if used is None:
        return _missing("attempts_by_activity", **detail)
    if used >= p.n:
        return _pass(
            "attempts_at_least", detail, EvidenceRef("attempt", str(p.activity_id))
        )
    return _fail("attempts_below_minimum", {**detail, "actual": used})


def _enrolled_before(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(EnrolledBeforeParams, params)
    detail: dict[str, object] = {"at": p.at.isoformat()}
    enrolled = ctx.facts.enrolled_at_by_cohort
    if not enrolled:
        return _missing("enrolled_at_by_cohort", **detail)
    # Earliest enrollment in the facts; the loader scopes which cohorts count.
    cohort_id, first = min(enrolled.items(), key=lambda item: (item[1], str(item[0])))
    if first < p.at:
        return _pass(
            "enrolled_before", detail, EvidenceRef("enrollment", str(cohort_id))
        )
    return _fail("enrolled_after_cutoff", detail)


def _manual_approval(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(ManualApprovalParams, params)
    detail: dict[str, object] = {"approver_permission": p.approver_permission}
    key = (ctx.target.kind, ctx.target.id, ctx.path)
    if key in ctx.facts.approvals:
        return _pass(
            "manual_approval",
            detail,
            EvidenceRef("approval", f"{ctx.target.id}:{ctx.path}"),
        )
    return _fail("approval_missing", detail)


def _track_position(params: BaseModel, ctx: LeafContext) -> LeafResult:
    p = _typed(TrackPositionParams, params)
    detail: dict[str, object] = {"track_id": str(p.track_id)}
    routes = ctx.facts.routes_by_course.get(ctx.target.id)
    if routes is None:
        return _missing("routes_by_course", **detail)
    on_track = [route for route in routes if route.track_id == p.track_id]
    if not on_track:
        return _missing("routes_by_course", **detail)
    blocking: UUID | None = None
    for route in on_track:
        predecessor = route.predecessor_course_id
        if predecessor is None:
            return _pass("track_position", detail)
        if predecessor in ctx.facts.completed_course_ids:
            return _pass(
                "track_position",
                {**detail, "predecessor_course_id": str(predecessor)},
                EvidenceRef("course_completion", str(predecessor)),
            )
        blocking = predecessor
    return _fail(
        "track_predecessor_incomplete",
        {**detail, "predecessor_course_id": str(blocking)},
    )


def _course_refs(params: BaseModel) -> Iterable[Reference]:
    return (Reference("course", _typed(CourseParams, params).course_id),)


def _score_refs(params: BaseModel) -> Iterable[Reference]:
    p = _typed(MinimumScoreParams, params)
    kind: Literal["course", "activity"] = (
        "activity" if p.subject == "activity" else "course"
    )
    return (Reference(kind, p.ref_id),)


def _attempt_refs(params: BaseModel) -> Iterable[Reference]:
    return (Reference("activity", _typed(AttemptsAtLeastParams, params).activity_id),)


def _no_refs(params: BaseModel) -> Iterable[Reference]:
    return ()


BUILTIN_CONDITIONS: tuple[ConditionSpec, ...] = (
    ConditionSpec(
        "course_completed",
        CourseParams,
        ALL_TARGETS,
        _course_refs,
        True,
        _course_completed,
    ),
    # Monotone while certificate expiry (HM-04) is not adopted; revisit then.
    ConditionSpec(
        "certificate_held",
        CourseParams,
        ALL_TARGETS,
        _course_refs,
        True,
        _certificate_held,
    ),
    ConditionSpec(
        "minimum_score",
        MinimumScoreParams,
        ALL_TARGETS,
        _score_refs,
        True,
        _minimum_score,
    ),
    ConditionSpec(
        "attempts_at_least",
        AttemptsAtLeastParams,
        frozenset({"activity_attempt"}),
        _attempt_refs,
        True,
        _attempts_at_least,
    ),
    ConditionSpec(
        "enrolled_before",
        EnrolledBeforeParams,
        ALL_TARGETS,
        _no_refs,
        True,
        _enrolled_before,
    ),
    # Approvals can be revoked or expire, so they are not monotone.
    ConditionSpec(
        "manual_approval",
        ManualApprovalParams,
        ALL_TARGETS,
        _no_refs,
        False,
        _manual_approval,
    ),
    ConditionSpec(
        "track_position",
        TrackPositionParams,
        frozenset({"course_access"}),
        _no_refs,
        True,
        _track_position,
    ),
)


def default_registry() -> ConditionRegistry:
    return ConditionRegistry.from_specs(BUILTIN_CONDITIONS)
