"""Pure-contract canaries for ``dotmac-analytics`` (ADR-0042)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_analytics.contracts import (
    DimensionKind,
    DimensionSpec,
    DimensionValue,
    DuplicateMetricDeclaration,
    InvalidAnalyticsContract,
    MetricDeclaration,
    MetricDeclarationRegistry,
    MetricGranularity,
    MetricPointInput,
    MetricSelector,
    MetricValueKind,
    RecordMetricBatchCommand,
    SourceProvenance,
    UnknownMetricDeclaration,
)


def _declaration() -> MetricDeclaration:
    return MetricDeclaration(
        owner_code="billing",
        metric_code="billing.revenue.collected",
        schema_version=1,
        display_name="Collected revenue",
        value_kind=MetricValueKind.MONEY,
        unit_code="money",
        granularities=(MetricGranularity.DAY, MetricGranularity.MONTH),
        dimensions=(
            DimensionSpec("channel", DimensionKind.ENUM, ("bank", "cash")),
            DimensionSpec("region_ref", DimensionKind.OPAQUE_REFERENCE),
        ),
    )


def _point(**changes: object) -> MetricPointInput:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    values: dict[str, object] = {
        "metric_code": "billing.revenue.collected",
        "schema_version": 1,
        "period_start": start,
        "period_end": start + timedelta(days=1),
        "granularity": MetricGranularity.DAY,
        "value": Decimal("1234.560000000001"),
        "currency_code": "NGN",
        "dimensions": (DimensionValue("channel", "bank"),),
    }
    values.update(changes)
    return MetricPointInput(**values)  # type: ignore[arg-type]


def test_registry_is_immutable_and_rejects_duplicate_metric_versions() -> None:
    declaration = _declaration()
    registry = MetricDeclarationRegistry((declaration,))
    assert registry.require(declaration.metric_code, 1) is declaration

    with pytest.raises(DuplicateMetricDeclaration):
        MetricDeclarationRegistry((declaration, declaration))
    with pytest.raises(UnknownMetricDeclaration):
        registry.require(declaration.metric_code, 2)


def test_metric_code_is_namespaced_by_its_decision_owner() -> None:
    with pytest.raises(InvalidAnalyticsContract, match="owner namespace"):
        MetricDeclaration(
            owner_code="billing",
            metric_code="sales.revenue",
            schema_version=1,
            display_name="Revenue",
            value_kind=MetricValueKind.NUMBER,
            unit_code="naira",
            granularities=(MetricGranularity.DAY,),
        )


def test_dimensions_are_declared_bounded_and_never_free_form_identity() -> None:
    declaration = _declaration()
    normalized = declaration.normalize_dimensions(
        (
            DimensionValue("region_ref", "region-019"),
            DimensionValue("channel", "bank"),
        )
    )
    assert normalized == (("channel", "bank"), ("region_ref", "region-019"))

    with pytest.raises(InvalidAnalyticsContract, match="undeclared"):
        declaration.normalize_dimensions((DimensionValue("customer_id", "c-1"),))
    with pytest.raises(InvalidAnalyticsContract, match="not an allowed"):
        declaration.normalize_dimensions((DimensionValue("channel", "crypto"),))
    with pytest.raises(InvalidAnalyticsContract, match="opaque reference"):
        declaration.normalize_dimensions(
            (DimensionValue("region_ref", "Michael Ayoade"),)
        )


def test_money_requires_currency_and_non_money_refuses_it() -> None:
    registry = MetricDeclarationRegistry((_declaration(),))
    with pytest.raises(InvalidAnalyticsContract, match="currency_code"):
        registry.validate_point(_point(currency_code=None))
    with pytest.raises(InvalidAnalyticsContract, match="selector requires"):
        registry.validate_selector(
            MetricSelector(
                metric_code="billing.revenue.collected",
                schema_version=1,
                granularity=MetricGranularity.DAY,
            )
        )

    count = MetricDeclaration(
        owner_code="ticketing",
        metric_code="ticketing.open.count",
        schema_version=1,
        display_name="Open tickets",
        value_kind=MetricValueKind.COUNT,
        unit_code="tickets",
        granularities=(MetricGranularity.DAY,),
    )
    with pytest.raises(InvalidAnalyticsContract, match="only money"):
        MetricDeclarationRegistry((count,)).validate_point(
            MetricPointInput(
                metric_code=count.metric_code,
                schema_version=1,
                period_start=_point().period_start,
                period_end=_point().period_end,
                granularity=MetricGranularity.DAY,
                value=Decimal("2"),
                currency_code="NGN",
            )
        )
    with pytest.raises(InvalidAnalyticsContract, match="only money"):
        MetricDeclarationRegistry((count,)).validate_selector(
            MetricSelector(
                metric_code=count.metric_code,
                schema_version=1,
                granularity=MetricGranularity.DAY,
                currency_code="NGN",
            )
        )


def test_numeric_values_fit_the_exact_database_precision_and_scale() -> None:
    with pytest.raises(InvalidAnalyticsContract, match=r"NUMERIC\(38,12\)"):
        _point(value=Decimal("1E+26"))
    with pytest.raises(InvalidAnalyticsContract, match=r"NUMERIC\(38,12\)"):
        _point(value=Decimal("0.0000000000001"))


def test_batch_identity_is_owned_once_and_duplicate_coordinates_are_refused() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    provenance = SourceProvenance(
        source_owner="billing",
        source_event_id="invoice-day-2026-08-01",
        source_schema_version=1,
        source_reference="billing-run-88",
        adapter_code="billing.analytics.v1",
    )
    command = RecordMetricBatchCommand(
        tenant_id=uuid4(),
        provenance=provenance,
        observed_at=now,
        received_at=now,
        points=(_point(),),
    )
    MetricDeclarationRegistry((_declaration(),)).validate_batch(command)

    with pytest.raises(InvalidAnalyticsContract, match="duplicate metric coordinate"):
        RecordMetricBatchCommand(
            tenant_id=command.tenant_id,
            provenance=provenance,
            observed_at=now,
            received_at=now,
            points=(_point(), _point()),
        )


def test_a_source_cannot_publish_another_owners_metric() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    command = RecordMetricBatchCommand(
        tenant_id=uuid4(),
        provenance=SourceProvenance(
            source_owner="sales",
            source_event_id="sale-1",
            source_schema_version=1,
            source_reference="sale-1",
            adapter_code="sales.analytics.v1",
        ),
        observed_at=now,
        received_at=now,
        points=(_point(),),
    )
    with pytest.raises(InvalidAnalyticsContract, match="owned by"):
        MetricDeclarationRegistry((_declaration(),)).validate_batch(command)


def test_source_provenance_refuses_free_text_and_subject_identity() -> None:
    with pytest.raises(InvalidAnalyticsContract, match="opaque reference"):
        SourceProvenance(
            source_owner="billing",
            source_event_id="customer@example.com",
            source_schema_version=1,
            source_reference="billing-run-1",
            adapter_code="billing.analytics.v1",
        )
    with pytest.raises(InvalidAnalyticsContract, match="opaque reference"):
        SourceProvenance(
            source_owner="billing",
            source_event_id="event-1",
            source_schema_version=1,
            source_reference="billing run for Michael",
            adapter_code="billing.analytics.v1",
        )


@pytest.mark.parametrize(
    "field",
    ("period_start", "period_end", "observed_at", "received_at"),
)
def test_every_business_or_evidence_instant_is_timezone_aware(field: str) -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    if field in {"period_start", "period_end"}:
        changes = {field: getattr(_point(), field).replace(tzinfo=None)}
        with pytest.raises(InvalidAnalyticsContract, match="timezone-aware"):
            _point(**changes)
        return

    values = {
        "tenant_id": uuid4(),
        "provenance": SourceProvenance(
            source_owner="billing",
            source_event_id="event-1",
            source_schema_version=1,
            source_reference="run-1",
            adapter_code="billing.analytics.v1",
        ),
        "observed_at": now,
        "received_at": now,
        "points": (_point(),),
    }
    values[field] = now.replace(tzinfo=None)
    with pytest.raises(InvalidAnalyticsContract, match="timezone-aware"):
        RecordMetricBatchCommand(**values)  # type: ignore[arg-type]
