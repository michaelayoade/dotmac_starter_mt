"""Provider-free conformance kit for normalized observation producers.

A real connector runs Integration's SPI conformance separately. This kit checks
only the domain handoff: deterministic typed declarations and observations that
replay without changing facts. It deliberately imports no connector engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_media_observations.contracts import (
    EntityObservation,
    HierarchyObservation,
    InvalidObservation,
    MetricDefinitionDeclaration,
    MetricObservation,
    NodeTypeDeclaration,
    NormalizedMediaFact,
    ObservationKind,
    ObservationSource,
    RecordOutcome,
    RecordStatus,
    UnsupportedObservation,
)
from dotmac_media_observations.service import (
    declare_metric,
    declare_node_type,
    emit_analytics_fact,
    record_entity,
    record_hierarchy,
    record_metric,
)

CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class NormalizedObservationCase:
    node_declarations: tuple[NodeTypeDeclaration, ...]
    metric_declarations: tuple[MetricDefinitionDeclaration, ...]
    observations: tuple[
        EntityObservation | HierarchyObservation | MetricObservation, ...
    ]


class NormalizedObservationProducer(Protocol):
    """A connector-side fake or adapter that emits one stable fixture case."""

    @property
    def normalized_observation_spi_version(self) -> int: ...

    def normalized_case(self) -> NormalizedObservationCase: ...


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    spi_version: int
    observation_count: int
    replay_count: int
    installation_ref: str
    source_system: str
    node_declarations: tuple[NodeTypeDeclaration, ...]
    metric_declarations: tuple[MetricDefinitionDeclaration, ...]
    observation_kinds: tuple[ObservationKind, ...]
    observation_ids: tuple[UUID, ...]
    content_fingerprints: tuple[str, ...]
    facts: tuple[NormalizedMediaFact, ...]


def run_normalized_conformance(
    db: Session, producer: NormalizedObservationProducer
) -> ConformanceReport:
    """Persist one SPI-v1 fixture twice and prove exact replay provenance."""
    spi_version = _require_spi_version(producer)
    first = _normalized_case(producer)
    second = _normalized_case(producer)
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
    for node_declaration in first.node_declarations:
        if node_declaration.tenant_id not in tenant_ids:
            raise InvalidObservation(
                "node declaration tenant differs from observations"
            )
        declare_node_type(db, node_declaration)
    for metric_declaration in first.metric_declarations:
        if metric_declaration.tenant_id not in tenant_ids:
            raise InvalidObservation(
                "metric declaration tenant differs from observations"
            )
        declare_metric(db, metric_declaration)

    first_outcomes = tuple(
        _record(db, observation) for observation in first.observations
    )
    replay_outcomes = tuple(
        _record(db, observation) for observation in second.observations
    )
    if any(outcome.status is not RecordStatus.REPLAYED for outcome in replay_outcomes):
        raise InvalidObservation(
            "normalized producer replay created new facts for an identical fixture"
        )
    for first_outcome, replay_outcome in zip(
        first_outcomes, replay_outcomes, strict=True
    ):
        if (
            first_outcome.observation_id != replay_outcome.observation_id
            or first_outcome.fingerprint != replay_outcome.fingerprint
        ):
            raise InvalidObservation(
                "normalized producer replay changed observation identity or fingerprint"
            )
    facts = tuple(
        emit_analytics_fact(
            db,
            tenant_id=source.tenant_id,
            observation_id=outcome.observation_id,
        )
        for source, outcome in zip(sources, first_outcomes, strict=True)
    )
    for source, outcome, fact in zip(sources, first_outcomes, facts, strict=True):
        if (
            fact.observation_id != outcome.observation_id
            or fact.content_fingerprint != outcome.fingerprint
            or fact.installation_ref != source.installation_ref
            or fact.source_system != source.source_system
            or fact.source_observation_id != source.source_observation_id
            or fact.source_observed_at != source.observed_at
            or fact.normalization_version != source.normalization_version
        ):
            raise InvalidObservation(
                "normalized conformance fact lost observation provenance"
            )
        if not any(
            receipt.transport_receipt_ref == source.transport_receipt_ref
            and receipt.received_at == source.received_at
            for receipt in fact.transport_receipts
        ):
            raise InvalidObservation(
                "normalized conformance fact lost transport receipt provenance"
            )
    return ConformanceReport(
        spi_version=spi_version,
        observation_count=len(first.observations),
        replay_count=len(replay_outcomes),
        installation_ref=next(iter(installations)),
        source_system=next(iter(systems)),
        node_declarations=first.node_declarations,
        metric_declarations=first.metric_declarations,
        observation_kinds=tuple(
            _observation_kind(observation) for observation in first.observations
        ),
        observation_ids=tuple(outcome.observation_id for outcome in first_outcomes),
        content_fingerprints=tuple(outcome.fingerprint for outcome in first_outcomes),
        facts=facts,
    )


def _require_spi_version(producer: NormalizedObservationProducer) -> int:
    try:
        version = producer.normalized_observation_spi_version
    except AttributeError:
        raise UnsupportedObservation(
            "normalized producer does not declare an observation SPI version"
        ) from None
    if type(version) is not int or version < 1:
        raise InvalidObservation(
            "normalized observation SPI version must be a positive integer"
        )
    if version != CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION:
        raise UnsupportedObservation(
            f"normalized observation SPI version {version} is unsupported; "
            f"this module implements {CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION}"
        )
    return version


def _normalized_case(
    producer: NormalizedObservationProducer,
) -> NormalizedObservationCase:
    factory = getattr(producer, "normalized_case", None)
    if not callable(factory):
        raise UnsupportedObservation(
            "normalized producer does not expose a normalized case factory"
        )
    case = factory()
    if not isinstance(case, NormalizedObservationCase):
        raise InvalidObservation(
            "normalized producer must return a NormalizedObservationCase"
        )
    if type(case.node_declarations) is not tuple:
        raise InvalidObservation("conformance node declarations must be a tuple")
    if type(case.metric_declarations) is not tuple:
        raise InvalidObservation("conformance metric declarations must be a tuple")
    if type(case.observations) is not tuple:
        raise InvalidObservation("conformance observations must be a tuple")
    if any(
        not isinstance(declaration, NodeTypeDeclaration)
        for declaration in case.node_declarations
    ):
        raise InvalidObservation(
            "every conformance node declaration must be a NodeTypeDeclaration"
        )
    if any(
        not isinstance(declaration, MetricDefinitionDeclaration)
        for declaration in case.metric_declarations
    ):
        raise InvalidObservation(
            "every conformance metric declaration must be a "
            "MetricDefinitionDeclaration"
        )
    if any(
        not isinstance(
            observation,
            EntityObservation | HierarchyObservation | MetricObservation,
        )
        for observation in case.observations
    ):
        raise InvalidObservation(
            "every conformance observation command must be an EntityObservation, "
            "HierarchyObservation or MetricObservation"
        )
    if any(
        not isinstance(observation.source, ObservationSource)
        for observation in case.observations
    ):
        raise InvalidObservation(
            "every conformance observation command must carry an ObservationSource"
        )
    return case


def _record(
    db: Session,
    observation: EntityObservation | HierarchyObservation | MetricObservation,
) -> RecordOutcome:
    if isinstance(observation, EntityObservation):
        return record_entity(db, observation)
    if isinstance(observation, HierarchyObservation):
        return record_hierarchy(db, observation)
    if isinstance(observation, MetricObservation):
        return record_metric(db, observation)
    raise InvalidObservation("unsupported normalized observation command")


def _observation_kind(
    observation: EntityObservation | HierarchyObservation | MetricObservation,
) -> ObservationKind:
    if isinstance(observation, EntityObservation):
        return ObservationKind.ENTITY
    if isinstance(observation, HierarchyObservation):
        return ObservationKind.HIERARCHY
    return ObservationKind.METRIC


__all__ = [
    "CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION",
    "ConformanceReport",
    "NormalizedObservationCase",
    "NormalizedObservationProducer",
    "run_normalized_conformance",
]
