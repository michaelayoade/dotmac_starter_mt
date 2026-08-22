"""Large-import partition integrity, claims and bounded settlement."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_imports.contracts import (
    ColumnMapping,
    FieldSet,
    FieldSpec,
    ImportIssue,
    InvalidRunState,
    RowApplier,
    RowSkipped,
    RunStatus,
    SourceDocument,
    SourceMismatch,
)
from dotmac_imports.models import ImportPartition, ImportRun, ImportRunRow
from dotmac_imports.partitioning import (
    PartitionClaimLost,
    PartitionDescriptor,
    PartitionPlanInvalid,
    apply_claimed_partition,
    claim_partition,
    iter_csv_partitions,
    read_claimed_partition,
    register_partition_plan,
    validate_claimed_partition,
)
from dotmac_imports.service import create_dry_run, promote, validate_next_chunk
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
DATA = b"Ref No,Amount Paid\nR-1,10\nR-2,20\nR-3,thirty\n"


def _source(data: bytes = DATA) -> SourceDocument:
    return SourceDocument(
        file_id=uuid.uuid4(),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
    )


def _fields() -> FieldSet:
    return FieldSet(
        (
            FieldSpec("reference", required=True, aliases=frozenset({"Ref No"})),
            FieldSpec("amount", required=True, aliases=frozenset({"Amount Paid"})),
        )
    )


class Validator:
    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
        return (
            ()
            if row["amount"].isdigit()
            else (ImportIssue("invalid_amount", "amount is not numeric"),)
        )


class Applier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def apply(self, row: Mapping[str, str]) -> Mapping[str, object]:
        self.calls.append(row["reference"])
        return {"reference": row["reference"]}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_imports": None}},
    )

    @event.listens_for(engine, "connect")
    def _no_implicit_begin(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):  # type: ignore[no-untyped-def]
        conn.exec_driver_sql("BEGIN")

    ImportRun.__table__.create(engine)
    ImportRunRow.__table__.create(engine)
    ImportPartition.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _run(db: Session, source: SourceDocument) -> ImportRun:
    return create_dry_run(
        db,
        tenant_id=TENANT,
        kind="receipts",
        source=source,
        mapping=ColumnMapping((("amount", "Amount Paid"), ("reference", "Ref No"))),
    )


def _plan(
    db: Session,
    source: SourceDocument,
    *,
    data: bytes = DATA,
    partition_rows: int = 2,
) -> tuple[ImportRun, dict[uuid.UUID, bytes]]:
    payloads = tuple(
        iter_csv_partitions(
            source,
            open_source=lambda: io.BytesIO(data),
            partition_rows=partition_rows,
        )
    )
    stored: dict[uuid.UUID, bytes] = {}
    descriptors: list[PartitionDescriptor] = []
    for payload in payloads:
        file_id = uuid.uuid4()
        stored[file_id] = payload.data
        descriptors.append(
            PartitionDescriptor(
                ordinal=payload.ordinal,
                start_row=payload.start_row,
                row_count=payload.row_count,
                file_id=file_id,
                checksum_sha256=payload.checksum_sha256,
                byte_size=len(payload.data),
            )
        )
    run = _run(db, source)
    register_partition_plan(
        db,
        tenant_id=TENANT,
        run_id=run.id,
        source=source,
        descriptors=descriptors,
    )
    return run, stored


def test_partitioning_streams_a_verified_source_into_bounded_artifacts() -> None:
    source = _source()
    payloads = tuple(
        iter_csv_partitions(
            source,
            open_source=lambda: io.BytesIO(DATA),
            partition_rows=2,
        )
    )

    assert [(item.ordinal, item.start_row, item.row_count) for item in payloads] == [
        (0, 0, 2),
        (1, 2, 1),
    ]
    assert all(
        hashlib.sha256(item.data).hexdigest() == item.checksum_sha256
        for item in payloads
    )
    assert "R-1" not in repr(payloads[0])


def test_partitioning_refuses_source_bytes_that_do_not_match_the_file_record() -> None:
    source = _source()
    with pytest.raises(SourceMismatch):
        tuple(
            iter_csv_partitions(
                source,
                open_source=lambda: io.BytesIO(DATA + b"R-4,40\n"),
            )
        )


def test_a_partition_plan_must_be_contiguous_and_is_immutable(db: Session) -> None:
    source = _source()
    run = _run(db, source)
    descriptor = PartitionDescriptor(1, 0, 1, uuid.uuid4(), "a" * 64, 10)
    with pytest.raises(PartitionPlanInvalid):
        register_partition_plan(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            source=source,
            descriptors=(descriptor,),
        )


def test_workers_claim_distinct_bounded_partitions(db: Session) -> None:
    source = _source()
    run, _ = _plan(db, source)

    first = claim_partition(db, tenant_id=TENANT, run_id=run.id)
    second = claim_partition(db, tenant_id=TENANT, run_id=run.id)

    assert first is not None and second is not None
    assert (first.ordinal, second.ordinal) == (0, 1)
    assert first.partition_id != second.partition_id
    assert "lease_token" not in repr(first)


def test_checksum_is_verified_before_database_settlement(db: Session) -> None:
    source = _source()
    run, stored = _plan(db, source, partition_rows=3)
    claim = claim_partition(db, tenant_id=TENANT, run_id=run.id)
    assert claim is not None
    stored[claim.file_id] += b"tampered"
    with pytest.raises(SourceMismatch):
        read_claimed_partition(
            claim,
            open_partition=lambda file_id: io.BytesIO(stored[file_id]),
        )

    assert db.scalars(select(ImportRunRow)).all() == []


def test_validation_settles_each_partition_and_finalizes_the_run(db: Session) -> None:
    source = _source()
    run, stored = _plan(db, source)

    results = []
    while claim := claim_partition(db, tenant_id=TENANT, run_id=run.id):
        prepared = read_claimed_partition(
            claim,
            open_partition=lambda file_id: io.BytesIO(stored[file_id]),
        )
        results.append(
            validate_claimed_partition(
                db,
                prepared,
                fields=_fields(),
                validator=Validator(),
            )
        )

    db.refresh(run)
    assert run.status == RunStatus.DRY_RUN_READY
    assert (run.total_rows, run.ok_rows, run.failed_rows) == (3, 2, 1)
    assert [result.processed for result in results] == [2, 1]
    assert [
        row.row_number
        for row in db.scalars(select(ImportRunRow).order_by(ImportRunRow.row_number))
    ] == [1, 2, 3]


def test_partition_settlement_counts_a_typed_skip_without_calling_the_applier(
    db: Session,
) -> None:
    data = b"Ref No,Amount Paid\nR-1,10\nR-2,20\n"
    source = _source(data)
    run, stored = _plan(db, source, data=data, partition_rows=2)
    claim = claim_partition(db, tenant_id=TENANT, run_id=run.id)
    assert claim is not None
    prepared = read_claimed_partition(
        claim,
        open_partition=lambda file_id: io.BytesIO(stored[file_id]),
    )

    class SkipsSecond(Validator):
        def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
            if row["reference"] == "R-2":
                raise RowSkipped(
                    "duplicate_reference",
                    "an existing domain record already owns this reference",
                )
            return super().validate(row)

    result = validate_claimed_partition(
        db,
        prepared,
        fields=_fields(),
        validator=SkipsSecond(),
    )

    db.refresh(run)
    assert result.skipped == 1
    assert (run.ok_rows, run.failed_rows, run.skipped_rows) == (1, 0, 1)


def test_an_expired_or_replaced_claim_cannot_apply(db: Session) -> None:
    source = _source()
    run, stored = _plan(db, source, partition_rows=3)
    now = datetime(2026, 8, 21, tzinfo=UTC)
    stale = claim_partition(
        db,
        tenant_id=TENANT,
        run_id=run.id,
        lease_seconds=1,
        now=now,
    )
    assert stale is not None
    replacement = claim_partition(
        db,
        tenant_id=TENANT,
        run_id=run.id,
        now=now + timedelta(seconds=2),
    )
    assert replacement is not None
    applier = Applier()
    prepared = read_claimed_partition(
        stale,
        open_partition=lambda file_id: io.BytesIO(stored[file_id]),
    )

    with pytest.raises(PartitionClaimLost):
        apply_claimed_partition(
            db,
            prepared,
            fields=_fields(),
            validator=Validator(),
            applier=applier,
            now=now + timedelta(seconds=2),
        )
    assert applier.calls == []


def test_promotion_clones_the_verified_plan_and_apply_uses_it(db: Session) -> None:
    source = _source(b"Ref No,Amount Paid\nR-1,10\nR-2,20\n")
    data = b"Ref No,Amount Paid\nR-1,10\nR-2,20\n"
    run, stored = _plan(db, source, data=data, partition_rows=2)
    claim = claim_partition(db, tenant_id=TENANT, run_id=run.id)
    assert claim is not None
    prepared = read_claimed_partition(
        claim,
        open_partition=lambda file_id: io.BytesIO(stored[file_id]),
    )
    validate_claimed_partition(
        db,
        prepared,
        fields=_fields(),
        validator=Validator(),
    )
    applied = promote(db, tenant_id=TENANT, run_id=run.id, source=source)
    apply_claim = claim_partition(db, tenant_id=TENANT, run_id=applied.id)
    assert apply_claim is not None
    applier: RowApplier = Applier()
    apply_prepared = read_claimed_partition(
        apply_claim,
        open_partition=lambda file_id: io.BytesIO(stored[file_id]),
    )

    result = apply_claimed_partition(
        db,
        apply_prepared,
        fields=_fields(),
        validator=Validator(),
        applier=applier,
    )

    assert result.run_complete
    assert isinstance(applier, Applier) and applier.calls == ["R-1", "R-2"]


def test_the_legacy_whole_file_lane_refuses_a_partitioned_run(db: Session) -> None:
    source = _source()
    run, _ = _plan(db, source)
    with pytest.raises(InvalidRunState, match="partitioned runs"):
        validate_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=DATA,
            fields=_fields(),
            validator=Validator(),
        )
