"""Flush-only topology decision and projection owner."""

from __future__ import annotations

from datetime import UTC, datetime
from heapq import heappop, heappush
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_topology.contracts import (
    CoverageGap,
    CoverageQuery,
    DeclareLink,
    LinkKind,
    LinkLookup,
    LinkSnapshot,
    LinkState,
    PathQuery,
    PathSnapshot,
    ReachabilityQuery,
    ReachabilitySnapshot,
    ReachabilityState,
    RebuildTopology,
    RecordObservedLink,
    ResolveForwarding,
    TopologyRebuildReport,
    WithdrawLink,
)
from dotmac_network_topology.models import (
    CoverageGapRow,
    Link,
    PathProjection,
    ReachabilityProjection,
    TopologyEvent,
)


class TopologyError(ValueError):
    pass


class TopologyNotFound(TopologyError):
    pass


class TopologyConflict(TopologyError):
    pass


def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise TopologyError(f"{label} must not be blank")
    return result


def _event(
    db: Session, tenant_id: UUID, ref: str, kind: str, payload: dict[str, str]
) -> None:
    db.add(
        TopologyEvent(
            tenant_id=tenant_id,
            aggregate_ref=ref,
            event_type=kind,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
    )


def _link_snapshot(row: Link) -> LinkSnapshot:
    return LinkSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        left_ref=row.left_ref,
        right_ref=row.right_ref,
        kind=LinkKind(row.kind),
        state=LinkState(row.state),
        direction=row.direction,
        cost=row.cost,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        created_at=row.created_at,
        withdrawn_at=row.withdrawn_at,
    )


def _path_snapshot(row: PathProjection) -> PathSnapshot:
    return PathSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        source_ref=row.source_ref,
        destination_ref=row.destination_ref,
        hop_refs=tuple(row.hop_refs),
        link_ids=tuple(UUID(value) for value in row.link_ids),
        total_cost=row.total_cost,
        reachable=row.reachable,
        as_of=row.as_of,
        rebuilt_at=row.rebuilt_at,
    )


def declare_link(db: Session, *, tenant_id: UUID, command: DeclareLink) -> LinkSnapshot:
    if command.left_ref == command.right_ref:
        raise TopologyError("link endpoints must differ")
    direction = _clean(command.direction, "direction")
    if direction not in {"directed", "bidirectional"}:
        raise TopologyError("direction must be directed or bidirectional")
    row = Link(
        tenant_id=tenant_id,
        left_ref=_clean(command.left_ref, "left endpoint"),
        right_ref=_clean(command.right_ref, "right endpoint"),
        kind=command.kind.value,
        state=LinkState.DECLARED.value,
        direction=direction,
        cost=command.cost,
        source_ref=_clean(command.source_ref, "source reference"),
        created_at=datetime.now(UTC),
    )
    if row.cost < 0:
        raise TopologyError("link cost cannot be negative")
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"link:{row.id}",
                "link_declared",
                {"source_ref": row.source_ref},
            )
            db.flush()
    except IntegrityError as exc:
        raise TopologyConflict("link identity already exists") from exc
    return _link_snapshot(row)


def record_observed_link(
    db: Session, *, tenant_id: UUID, command: RecordObservedLink
) -> LinkSnapshot:
    existing = db.scalar(
        select(Link).where(
            Link.tenant_id == tenant_id,
            Link.fingerprint == command.fingerprint,
            Link.source_ref == command.source_ref,
        )
    )
    if existing is not None:
        return _link_snapshot(existing)
    row = Link(
        tenant_id=tenant_id,
        left_ref=_clean(command.left_ref, "left endpoint"),
        right_ref=_clean(command.right_ref, "right endpoint"),
        kind=command.kind.value,
        state=LinkState.OBSERVED.value,
        direction="bidirectional",
        cost=1,
        source_ref=_clean(command.source_ref, "source reference"),
        fingerprint=_clean(command.fingerprint, "fingerprint"),
        observed_at=command.observed_at,
        created_at=command.observed_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"link:{row.id}",
                "observed_link_recorded",
                {"fingerprint": row.fingerprint or ""},
            )
            db.flush()
    except IntegrityError as exc:
        raise TopologyConflict("observed-link fingerprint already exists") from exc
    return _link_snapshot(row)


def withdraw_link(
    db: Session, *, tenant_id: UUID, command: WithdrawLink
) -> LinkSnapshot:
    row = db.scalar(
        select(Link)
        .where(Link.tenant_id == tenant_id, Link.id == command.link_id)
        .with_for_update()
    )
    if row is None:
        raise TopologyNotFound("link not found")
    current = LinkState(row.state)
    if current is not command.expected:
        raise TopologyConflict("link state changed")
    row.state = LinkState.WITHDRAWN.value
    row.withdrawn_at = datetime.now(UTC)
    _event(
        db,
        tenant_id,
        f"link:{row.id}",
        "link_withdrawn",
        {"reason": _clean(command.reason, "withdraw reason")},
    )
    db.flush()
    return _link_snapshot(row)


def _resolve_path(
    source: str, destination: str, links: tuple[Link, ...]
) -> tuple[tuple[str, ...], tuple[UUID, ...], int]:
    graph: dict[str, list[tuple[str, UUID, int]]] = {}
    for link in links:
        graph.setdefault(link.left_ref, []).append((link.right_ref, link.id, link.cost))
        if link.direction == "bidirectional":
            graph.setdefault(link.right_ref, []).append(
                (link.left_ref, link.id, link.cost)
            )
    queue: list[tuple[int, str, tuple[str, ...], tuple[UUID, ...]]] = [
        (0, source, (source,), ())
    ]
    best_cost = {source: 0}
    while queue:
        cost, node, hops, link_ids = heappop(queue)
        if cost != best_cost.get(node):
            continue
        if node == destination:
            return hops, link_ids, cost
        for next_node, link_id, edge_cost in graph.get(node, []):
            next_cost = cost + edge_cost
            if next_cost < best_cost.get(next_node, next_cost + 1):
                best_cost[next_node] = next_cost
                heappush(
                    queue,
                    (
                        next_cost,
                        next_node,
                        (*hops, next_node),
                        (*link_ids, link_id),
                    ),
                )
    return (), (), 0


def resolve_forwarding(
    db: Session, *, tenant_id: UUID, command: ResolveForwarding
) -> PathSnapshot:
    selected_ids = {*command.declared_link_ids, *command.observed_link_ids}
    links = (
        tuple(
            db.scalars(
                select(Link).where(
                    Link.tenant_id == tenant_id,
                    Link.id.in_(selected_ids),
                    Link.state != LinkState.WITHDRAWN.value,
                )
            )
        )
        if selected_ids
        else ()
    )
    if len(links) != len(selected_ids):
        raise TopologyNotFound("one or more path links were not found")
    hops, link_ids, cost = _resolve_path(
        command.source_ref, command.destination_ref, links
    )
    now = datetime.now(UTC)
    row = db.scalar(
        select(PathProjection)
        .where(
            PathProjection.tenant_id == tenant_id,
            PathProjection.source_ref == command.source_ref,
            PathProjection.destination_ref == command.destination_ref,
        )
        .with_for_update()
    )
    if row is None:
        row = PathProjection(
            tenant_id=tenant_id,
            source_ref=_clean(command.source_ref, "source reference"),
            destination_ref=_clean(command.destination_ref, "destination reference"),
            hop_refs=list(hops),
            link_ids=[str(value) for value in link_ids],
            total_cost=cost,
            reachable=bool(hops),
            as_of=command.as_of,
            rebuilt_at=now,
        )
        db.add(row)
    else:
        row.hop_refs = list(hops)
        row.link_ids = [str(value) for value in link_ids]
        row.total_cost = cost
        row.reachable = bool(hops)
        row.as_of = command.as_of
        row.rebuilt_at = now
    db.flush()
    return _path_snapshot(row)


def rebuild_topology(
    db: Session, *, tenant_id: UUID, command: RebuildTopology
) -> TopologyRebuildReport:
    requested = {*command.declared_link_ids, *command.observed_link_ids}
    links = (
        tuple(
            db.scalars(
                select(Link).where(
                    Link.tenant_id == tenant_id,
                    Link.id.in_(requested),
                    Link.state != LinkState.WITHDRAWN.value,
                )
            )
        )
        if requested
        else ()
    )
    if len(links) != len(requested):
        raise TopologyNotFound("topology rebuild references unknown links")
    projection_ref = _clean(command.projection_ref, "projection reference")
    paths = tuple(
        db.scalars(
            select(PathProjection)
            .where(PathProjection.tenant_id == tenant_id)
            .with_for_update()
        )
    )
    changed = False
    expected_gaps: dict[str, str] = {}
    now = datetime.now(UTC)
    for path in paths:
        hops, link_ids, total_cost = _resolve_path(
            path.source_ref, path.destination_ref, links
        )
        reachable = bool(hops)
        next_link_ids = [str(value) for value in link_ids]
        if (
            path.hop_refs != list(hops)
            or path.link_ids != next_link_ids
            or path.total_cost != total_cost
            or path.reachable != reachable
        ):
            changed = True
            _event(
                db,
                tenant_id,
                f"path:{path.id}",
                "path_changed",
                {"projection_ref": projection_ref},
            )
        path.hop_refs = list(hops)
        path.link_ids = next_link_ids
        path.total_cost = total_cost
        path.reachable = reachable
        path.as_of = command.as_of
        path.rebuilt_at = now

        reachability = db.scalar(
            select(ReachabilityProjection)
            .where(
                ReachabilityProjection.tenant_id == tenant_id,
                ReachabilityProjection.subject_ref == path.destination_ref,
                ReachabilityProjection.from_ref == path.source_ref,
            )
            .with_for_update()
        )
        state = (
            ReachabilityState.REACHABLE if reachable else ReachabilityState.UNREACHABLE
        )
        reason = None if reachable else "no-active-path"
        if reachability is None:
            reachability = ReachabilityProjection(
                tenant_id=tenant_id,
                subject_ref=path.destination_ref,
                from_ref=path.source_ref,
                state=state.value,
                path_id=path.id,
                reason_code=reason,
                as_of=command.as_of,
            )
            db.add(reachability)
            changed = True
        else:
            if (
                reachability.state != state.value
                or reachability.path_id != path.id
                or reachability.reason_code != reason
            ):
                changed = True
            reachability.state = state.value
            reachability.path_id = path.id
            reachability.reason_code = reason
            reachability.as_of = command.as_of
        if not reachable:
            expected_gaps[path.destination_ref] = "no-active-path"

    existing_gaps = {
        row.missing_ref: row
        for row in db.scalars(
            select(CoverageGapRow)
            .where(
                CoverageGapRow.tenant_id == tenant_id,
                CoverageGapRow.scope_ref == projection_ref,
            )
            .with_for_update()
        )
    }
    for missing_ref, row in existing_gaps.items():
        if missing_ref not in expected_gaps:
            db.delete(row)
            changed = True
    for missing_ref, reason_code in expected_gaps.items():
        gap_row = existing_gaps.get(missing_ref)
        if gap_row is None:
            db.add(
                CoverageGapRow(
                    tenant_id=tenant_id,
                    scope_ref=projection_ref,
                    missing_ref=missing_ref,
                    reason_code=reason_code,
                    as_of=command.as_of,
                )
            )
            changed = True
        else:
            gap_row.reason_code = reason_code
            gap_row.as_of = command.as_of
    db.flush()
    return TopologyRebuildReport(
        projection_ref=projection_ref,
        link_count=len(links),
        path_count=len(paths),
        gap_count=len(expected_gaps),
        changed=changed,
        rebuilt_at=now,
    )


def lookup_links(
    db: Session, *, tenant_id: UUID, query: LinkLookup
) -> tuple[LinkSnapshot, ...]:
    statement = select(Link).where(Link.tenant_id == tenant_id)
    if query.link_id is not None:
        statement = statement.where(Link.id == query.link_id)
    if query.endpoint_ref is not None:
        statement = statement.where(
            or_(
                Link.left_ref == query.endpoint_ref,
                Link.right_ref == query.endpoint_ref,
            )
        )
    if not query.include_withdrawn:
        statement = statement.where(Link.state != LinkState.WITHDRAWN.value)
    return tuple(_link_snapshot(row) for row in db.scalars(statement))


def query_paths(
    db: Session, *, tenant_id: UUID, query: PathQuery
) -> tuple[PathSnapshot, ...]:
    statement = select(PathProjection).where(
        PathProjection.tenant_id == tenant_id,
        PathProjection.source_ref == query.source_ref,
        PathProjection.destination_ref == query.destination_ref,
    )
    if query.as_of is not None:
        statement = statement.where(PathProjection.as_of <= query.as_of)
    return tuple(_path_snapshot(row) for row in db.scalars(statement))


def query_reachability(
    db: Session, *, tenant_id: UUID, query: ReachabilityQuery
) -> ReachabilitySnapshot | None:
    statement = select(ReachabilityProjection).where(
        ReachabilityProjection.tenant_id == tenant_id,
        ReachabilityProjection.subject_ref == query.subject_ref,
    )
    if query.from_ref is not None:
        statement = statement.where(ReachabilityProjection.from_ref == query.from_ref)
    row = db.scalar(statement)
    if row is None:
        return None
    return ReachabilitySnapshot(
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        from_ref=row.from_ref,
        state=ReachabilityState(row.state),
        path_id=row.path_id,
        reason_code=row.reason_code,
        as_of=row.as_of,
    )


def query_coverage(
    db: Session, *, tenant_id: UUID, query: CoverageQuery
) -> tuple[CoverageGap, ...]:
    statement = select(CoverageGapRow).where(
        CoverageGapRow.tenant_id == tenant_id,
        CoverageGapRow.scope_ref == query.scope_ref,
    )
    if query.as_of is not None:
        statement = statement.where(CoverageGapRow.as_of <= query.as_of)
    return tuple(
        CoverageGap(
            tenant_id=row.tenant_id,
            scope_ref=row.scope_ref,
            missing_ref=row.missing_ref,
            reason_code=row.reason_code,
            as_of=row.as_of,
        )
        for row in db.scalars(statement)
    )


__all__ = [
    "TopologyConflict",
    "TopologyError",
    "TopologyNotFound",
    "declare_link",
    "lookup_links",
    "query_coverage",
    "query_paths",
    "query_reachability",
    "rebuild_topology",
    "record_observed_link",
    "resolve_forwarding",
    "withdraw_link",
]
