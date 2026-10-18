"""Academy _load_curriculum graph reconciliation with caller transactions.

The source validation/order and ordinary SELECT autoflush are preserved. Caller
pending changes can flush on those SELECTs; this command stages its own mutations
only after graph validation. It never flushes or owns transaction boundaries.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from .models import Course
from .participation_models import CoursePrerequisite
from .prerequisite_contracts import (
    PrerequisiteReconciliationError,
    PrerequisiteReconciliationResult,
    ReconcilePrerequisitesCommand,
)


def reconcile_prerequisites(
    db: Session, command: ReconcilePrerequisitesCommand
) -> PrerequisiteReconciliationResult:
    """Project wanted edges; remove stale edges only for scoped dependents.

    Validation concerns the wanted graph alone. Additions may cross disciplines;
    the discipline limits removals only. Empty requires does not check its slug.
    """
    courses = {
        c.slug: c
        for c in db.query(Course).filter(Course.tenant_id == command.tenant_id).all()
    }

    wanted: set[tuple[UUID, UUID]] = set()
    missing: list[str] = []
    for entry in command.declarations:
        slug = entry.course
        for req in entry.requires:
            for name in (slug, req):
                if name not in courses:
                    missing.append(name)
            if slug in courses and req in courses:
                if slug == req:
                    raise PrerequisiteReconciliationError(
                        f"{slug} cannot require itself"
                    )
                wanted.add((courses[slug].id, courses[req].id))

    if missing:
        raise PrerequisiteReconciliationError(
            "These courses are declared in the file but not imported: "
            + ", ".join(sorted(set(missing)))
        )

    edges: dict[UUID, set[UUID]] = {}
    for course_id, requires_id in wanted:
        edges.setdefault(course_id, set()).add(requires_id)
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
    existing = {(r.course_id, r.requires_course_id): r for r in existing_rows}

    added = removed = 0
    for pair in wanted - set(existing):
        db.add(
            CoursePrerequisite(
                tenant_id=command.tenant_id,
                course_id=pair[0],
                requires_course_id=pair[1],
            )
        )
        added += 1
    for pair, row in existing.items():
        if pair not in wanted and pair[0] in scoped:
            db.delete(row)
            removed += 1

    return PrerequisiteReconciliationResult(
        added=added, removed=removed, declared=len(wanted)
    )
