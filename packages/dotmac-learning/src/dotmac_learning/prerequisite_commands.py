"""Academy _load_curriculum graph reconciliation with caller transactions.

The source validation/order and ordinary SELECT autoflush are preserved. Caller
pending changes can flush on those SELECTs; this command stages its own mutations
only after graph validation. It never flushes or owns transaction boundaries.

HM-01 widens an edge to a typed condition row. Legacy ``requires`` entries
become ``course_completed`` conditions in group ``g0`` and manage only those
rows on removal; a declaration carrying ``conditions`` or ``composition``
manages all rows of its course. This guard keeps a legacy curriculum file
from silently deleting authored non-default conditions.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from .models import Activity, Course
from .participation_models import CoursePrerequisite
from .prerequisite_contracts import (
    ACTIVITY_SCOPED_KINDS,
    PrerequisiteReconciliationError,
    PrerequisiteReconciliationResult,
    ReconcilePrerequisitesCommand,
)


@dataclass(frozen=True, slots=True)
class _Wanted:
    course_id: UUID
    requires_course_id: UUID
    condition_kind: str
    group_key: str
    params_json: str

    @property
    def key(self) -> tuple[UUID, UUID, str, str]:
        return (
            self.course_id,
            self.requires_course_id,
            self.condition_kind,
            self.group_key,
        )

    @property
    def identity(self) -> tuple[UUID, UUID, str, str, str]:
        return (*self.key, self.params_json)


def _params_json(params: dict[str, object]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _stored_params_json(params: object) -> str | None:
    # A stored row whose params are not a JSON object can never equal a
    # typed declaration; report it as unmatched rather than guessing.
    if not isinstance(params, dict):
        return None
    return _params_json(params)


def reconcile_prerequisites(
    db: Session, command: ReconcilePrerequisitesCommand
) -> PrerequisiteReconciliationResult:
    """Project wanted conditions; remove stale rows for scoped dependents.

    Validation concerns the wanted graph alone. Additions may cross disciplines;
    the discipline limits removals only. An entry with neither requires nor
    conditions nor composition does not check its slug (source behavior).
    """
    courses = {
        c.slug: c
        for c in db.query(Course).filter(Course.tenant_id == command.tenant_id).all()
    }
    activity_ids = {
        condition.activity_id
        for entry in command.declarations
        for condition in entry.conditions
        if condition.activity_id is not None
    }
    activities: dict[UUID, Activity] = {}
    if activity_ids:
        activities = {
            a.id: a
            for a in db.query(Activity)
            .filter(Activity.tenant_id == command.tenant_id)
            .filter(Activity.id.in_(activity_ids))
            .all()
        }

    wanted: dict[tuple[UUID, UUID, str, str, str], _Wanted] = {}
    managed_all: set[UUID] = set()
    compositions: dict[UUID, str] = {}
    missing: list[str] = []
    for entry in command.declarations:
        slug = entry.course
        manages_all = bool(entry.conditions) or entry.composition is not None
        if manages_all:
            if slug in courses:
                managed_all.add(courses[slug].id)
                if entry.composition is not None:
                    compositions[courses[slug].id] = entry.composition
            else:
                missing.append(slug)
        for req in entry.requires:
            for name in (slug, req):
                if name not in courses:
                    missing.append(name)
            if slug in courses and req in courses:
                if slug == req:
                    raise PrerequisiteReconciliationError(
                        f"{slug} cannot require itself"
                    )
                row = _Wanted(
                    course_id=courses[slug].id,
                    requires_course_id=courses[req].id,
                    condition_kind="course_completed",
                    group_key="g0",
                    params_json=_params_json({}),
                )
                wanted[row.identity] = row
        for condition in entry.conditions:
            if condition.kind not in ACTIVITY_SCOPED_KINDS:
                assert condition.requires is not None
                req = condition.requires
                if req not in courses:
                    missing.append(req)
                if slug in courses and req in courses:
                    if slug == req:
                        raise PrerequisiteReconciliationError(
                            f"{slug} cannot require itself"
                        )
                    params: dict[str, object] = {}
                    if condition.kind == "course_grade_at_least":
                        assert condition.min_fraction is not None
                        params = {"min_fraction": condition.min_fraction}
                    row = _Wanted(
                        course_id=courses[slug].id,
                        requires_course_id=courses[req].id,
                        condition_kind=condition.kind,
                        group_key=condition.group,
                        params_json=_params_json(params),
                    )
                    wanted[row.identity] = row
            else:
                assert condition.activity_id is not None
                activity = activities.get(condition.activity_id)
                if activity is None:
                    raise PrerequisiteReconciliationError(
                        f"unknown activity in prerequisite condition: "
                        f"{condition.activity_id}"
                    )
                params = {"activity_id": str(condition.activity_id)}
                if condition.kind == "attempt_number_at_least":
                    assert condition.min_attempts is not None
                    params["min_attempts"] = condition.min_attempts
                if slug in courses:
                    row = _Wanted(
                        course_id=courses[slug].id,
                        requires_course_id=activity.course_id,
                        condition_kind=condition.kind,
                        group_key=condition.group,
                        params_json=_params_json(params),
                    )
                    wanted[row.identity] = row

    seen_keys: dict[tuple[UUID, UUID, str, str], str] = {}
    for row in wanted.values():
        previous = seen_keys.setdefault(row.key, row.params_json)
        if previous != row.params_json:
            raise PrerequisiteReconciliationError(
                "conflicting params for one prerequisite condition"
            )

    if missing:
        raise PrerequisiteReconciliationError(
            "These courses are declared in the file but not imported: "
            + ", ".join(sorted(set(missing)))
        )

    edges: dict[UUID, set[UUID]] = {}
    for row in wanted.values():
        # Same-course activity conditions are intra-course progress gates,
        # not course-graph edges; everything else joins the cycle walk.
        if (
            row.condition_kind in ACTIVITY_SCOPED_KINDS
            and row.requires_course_id == row.course_id
        ):
            continue
        edges.setdefault(row.course_id, set()).add(row.requires_course_id)
    state: dict[UUID, int] = {}

    def _walk(node: UUID) -> None:
        state[node] = 1
        for nxt in edges.get(node, ()):
            if state.get(nxt) == 1:
                raise PrerequisiteReconciliationError(
                    "prerequisite cycle detected; refusing to write"
                )
            if not state.get(nxt):
                _walk(nxt)
        state[node] = 2

    for node in list(edges):
        if not state.get(node):
            _walk(node)

    existing_rows = (
        db.query(CoursePrerequisite)
        .filter(CoursePrerequisite.tenant_id == command.tenant_id)
        .all()
    )
    scoped = {c.id for c in courses.values() if c.discipline == command.discipline}
    existing: dict[tuple[UUID, UUID, str, str], CoursePrerequisite] = {
        (r.course_id, r.requires_course_id, r.condition_kind, r.group_key): r
        for r in existing_rows
    }
    wanted_keys = {row.key for row in wanted.values()}

    added = removed = 0
    for row in wanted.values():
        current = existing.get(row.key)
        if current is None:
            db.add(
                CoursePrerequisite(
                    tenant_id=command.tenant_id,
                    course_id=row.course_id,
                    requires_course_id=row.requires_course_id,
                    condition_kind=row.condition_kind,
                    params=json.loads(row.params_json),
                    group_key=row.group_key,
                )
            )
            added += 1
        elif _stored_params_json(current.params) != row.params_json:
            current.params = json.loads(row.params_json)
    for key, existing_row in existing.items():
        if key in wanted_keys:
            continue
        legacy_only = key[2] == "course_completed" and key[3] == "g0"
        if key[0] in managed_all or (legacy_only and key[0] in scoped):
            db.delete(existing_row)
            removed += 1

    for course_id, composition in compositions.items():
        course = next(c for c in courses.values() if c.id == course_id)
        if course.prerequisite_composition != composition:
            course.prerequisite_composition = composition

    return PrerequisiteReconciliationResult(
        added=added, removed=removed, declared=len(wanted)
    )
