"""Provider-free conformance kit for normalized observation producers.

A real connector runs Integration's SPI conformance separately. This kit checks
only the domain handoff: deterministic typed declarations and observations that
replay without changing facts. It deliberately imports no connector engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from dotmac_media_observations.contracts import (
    EntityObservation,
    HierarchyObservation,
    InvalidObservation,
    MetricDefinitionDeclaration,
    MetricObservation,
    NodeTypeDeclaration,
    RecordOutcome,
    RecordStatus,
)
from dotmac_media_observations.service import (
    declare_metric,
    declare_node_type,
    record_entity,
    record_hierarchy,
    record_metric,
)


@dataclass(frozen=True, slots=True)
class NormalizedObservationCase:
    node_declarations: tuple[NodeTypeDeclaration, ...]
    metric_declarations: tuple[MetricDefinitionDeclaration, ...]
    observations: tuple[
        EntityObservation | HierarchyObservation | MetricObservation, ...
    ]


class NormalizedObservationProducer(Protocol):
    """A connector-side fake or adapter that emits one stable fixture case."""

    def normalized_case(self) -> NormalizedObservationCase: ...


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    observation_count: int
    replay_count: int
    installation_ref: str
    source_system: str


def run_normalized_conformance(
    db: Session, producer: NormalizedObservationProducer
) -> ConformanceReport:
    """Persist the same fixture twice and require exact domain replay."""
    first = producer.normalized_case()
    second = producer.normalized_case()
    if first != second:
        raise InvalidObservation(
            "normalized producer is non-deterministic for the same fixture"
        )
    if not first.observations:
        raise InvalidObservation("conformance case must contain an observation")
    sources = [observation.source for observation in first.observations]
    tenant_ids = {source.tenant_id for source in sources}
    installations = {source.installation_ref for source in sources}
    systems = {source.source_system for source in sources}
    if len(tenant_ids) != 1 or len(installations) != 1 or len(systems) != 1:
        raise InvalidObservation(
            "one conformance case must target one tenant, installation and "
            "source system"
        )
    for declaration in first.node_declarations:
        if declaration.tenant_id not in tenant_ids:
            raise InvalidObservation(
                "node declaration tenant differs from observations"
            )
        declare_node_type(db, declaration)
    for declaration in first.metric_declarations:
        if declaration.tenant_id not in tenant_ids:
            raise InvalidObservation(
                "metric declaration tenant differs from observations"
            )
        declare_metric(db, declaration)

    for observation in first.observations:
        _record(db, observation)
    replays = tuple(
        _record(db, observation).status for observation in second.observations
    )
    if any(status is not RecordStatus.REPLAYED for status in replays):
        raise InvalidObservation(
            "normalized producer replay created new facts for an identical fixture"
        )
    return ConformanceReport(
        observation_count=len(first.observations),
        replay_count=len(replays),
        installation_ref=next(iter(installations)),
        source_system=next(iter(systems)),
    )


def _record(
    db: Session,
    observation: EntityObservation | HierarchyObservation | MetricObservation,
) -> RecordOutcome:
    if isinstance(observation, EntityObservation):
        return record_entity(db, observation)
    if isinstance(observation, HierarchyObservation):
        return record_hierarchy(db, observation)
    return record_metric(db, observation)


__all__ = [
    "ConformanceReport",
    "NormalizedObservationCase",
    "NormalizedObservationProducer",
    "run_normalized_conformance",
]
