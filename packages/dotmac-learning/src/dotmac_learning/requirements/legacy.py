"""Today's course-access rule expressed as a requirement expression.

``compile_course_access`` builds ``all(any(routes), all(prerequisites))`` and
``to_course_access_state`` maps an evaluation back onto the legacy
``CourseAccessState`` with the legacy precedence, so slice 4 can run the engine
in shadow against ``entitlements.course_access_states``:

1. If no route is open, the **last** route in iteration order explains the lock
   (the legacy loop overwrites a locked state with each later locked route).
2. Otherwise the **first** unmet explicit prerequisite, in the given order.
3. Messages come from ``entitlements._course_lock_reason`` with its fallbacks.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from uuid import UUID

from dotmac_learning.entitlements import CourseAccessState, _course_lock_reason

from .evaluate import EvaluationResult
from .expression import All, Any, Cond, Expr
from .facts import Route

PREVIOUS_COURSE_FALLBACK = "Complete the previous course to unlock this course."
PREREQUISITE_FALLBACK = "Complete the prerequisite course to unlock this course."


def _route_expr(route: Route) -> Expr:
    if route.track_id is None or route.predecessor_course_id is None:
        return All(())
    return Cond("track_position", {"track_id": str(route.track_id)})


def compile_course_access(
    course_id: UUID, routes: Sequence[Route], prerequisite_ids: Sequence[UUID]
) -> Expr:
    """Compile the legacy rule for one entitled course."""
    if not routes:
        raise ValueError(f"course {course_id} has no route; it is not entitled")
    return All(
        (
            Any(tuple(_route_expr(route) for route in routes)),
            All(
                tuple(
                    Cond("course_completed", {"course_id": str(required)})
                    for required in prerequisite_ids
                )
            ),
        )
    )


def to_course_access_state(
    course_id: UUID,
    result: EvaluationResult,
    routes: Sequence[Route],
    prerequisite_ids: Sequence[UUID],
    titles: Mapping[UUID, str | None],
) -> CourseAccessState:
    """Map an evaluation of ``compile_course_access`` to the legacy state."""
    if result.passed:
        return CourseAccessState(course_id=course_id, locked=False)
    route_group, prerequisite_group = result.root.children
    if not route_group.passed:
        previous = routes[-1].predecessor_course_id
        if previous is None:
            raise ValueError("a failing route must have a predecessor")
        return CourseAccessState(
            course_id=course_id,
            locked=True,
            locked_reason=_course_lock_reason(
                titles.get(previous), PREVIOUS_COURSE_FALLBACK
            ),
            locked_until_course_id=previous,
        )
    for required, node in zip(
        prerequisite_ids, prerequisite_group.children, strict=True
    ):
        if not node.passed:
            return CourseAccessState(
                course_id=course_id,
                locked=True,
                locked_reason=_course_lock_reason(
                    titles.get(required), PREREQUISITE_FALLBACK
                ),
                locked_until_course_id=required,
            )
    raise ValueError("evaluation failed but no prerequisite is unmet")


def legacy_unmet_prerequisites(
    state: CourseAccessState,
    prerequisite_ids: Sequence[UUID],
    completed_course_ids: Collection[UUID],
) -> list[UUID]:
    """Reproduce ``entitlements.unmet_prerequisites`` from a mapped state.

    Explicit prerequisites first (duplicates preserved), then the course the
    state is locked on, exactly as the legacy function appends it.
    """
    required = list(prerequisite_ids)
    if state.locked and state.locked_until_course_id is not None:
        required.append(state.locked_until_course_id)
    return [course for course in required if course not in completed_course_ids]
