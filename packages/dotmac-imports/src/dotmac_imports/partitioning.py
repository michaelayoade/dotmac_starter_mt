"""Bounded CSV partitioning and durable parallel-worker claims.

`dotmac-files` continues to own bytes.  This module emits bounded partition
payloads for that owner to store, records only immutable file identities and
checksums, and lets workers claim one partition at a time.  No database
transaction spans creation or retrieval of a stored file.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from dotmac_imports.contracts import (
    ColumnMapping,
    FieldSet,
    ImportIssue,
    InvalidRunState,
    MalformedSource,
    RowApplier,
    RowRejected,
    RowStatus,
    RowValidator,
    RunStatus,
    SourceDocument,
    SourceLayout,
    SourceMismatch,
    UnmappedRequiredField,
    normalize_column,
)
from dotmac_imports.models import ImportPartition, ImportRun
from dotmac_imports.service import _record, _validation_detail
from dotmac_imports.tabular import apply_mapping

DEFAULT_PARTITION_ROWS = 200
DEFAULT_PARTITION_BYTES = 8 * 1024 * 1024
DEFAULT_LEASE_SECONDS = 300
_DIGEST_CHUNK_BYTES = 1024 * 1024
_BOM_TOLERANT = {"utf-8": "utf-8-sig", "utf8": "utf-8-sig"}

SourceOpener = Callable[[], BinaryIO]
PartitionOpener = Callable[[UUID], BinaryIO]


class PartitionError(RuntimeError):
    """A large-import partition cannot be used safely."""


class PartitionPlanInvalid(PartitionError):
    """Partition descriptors are not a complete immutable source plan."""


class PartitionClaimLost(PartitionError):
    """A worker no longer owns the lease it presented."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True, repr=False)
class PartitionPayload:
    """One bounded derived CSV object for `dotmac-files` to store."""

    ordinal: int
    start_row: int
    row_count: int
    checksum_sha256: str
    data: bytes


@dataclass(frozen=True, slots=True)
class PartitionDescriptor:
    """The immutable identity retained after a payload is stored."""

    ordinal: int
    start_row: int
    row_count: int
    file_id: UUID
    checksum_sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.start_row < 0 or self.row_count <= 0:
            raise PartitionPlanInvalid("partition ranges must be positive and ordered")
        if self.byte_size <= 0:
            raise PartitionPlanInvalid("partition byte_size must be positive")
        if len(self.checksum_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.checksum_sha256
        ):
            raise PartitionPlanInvalid("partition checksum must be lowercase SHA-256")


@dataclass(frozen=True, slots=True, repr=False)
class PartitionClaim:
    """A detached, opaque lease safe to carry outside a transaction."""

    partition_id: UUID
    run_id: UUID
    tenant_id: UUID
    lease_token: UUID
    file_id: UUID
    ordinal: int
    start_row: int
    row_count: int
    checksum_sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class PartitionProgress:
    run_id: UUID
    partition_id: UUID
    processed: int
    ok: int
    failed: int
    run_complete: bool


def _verify_source(source: SourceDocument, open_source: SourceOpener) -> None:
    digest = hashlib.sha256()
    try:
        with open_source() as stream:
            while chunk := stream.read(_DIGEST_CHUNK_BYTES):
                digest.update(chunk)
    except (OSError, ValueError):
        raise SourceMismatch(
            "stored source could not be read for verification"
        ) from None
    if digest.hexdigest() != source.checksum_sha256:
        raise SourceMismatch("stored source does not match its recorded SHA-256")


def _columns(reader: Iterator[list[str]]) -> tuple[str, ...]:
    try:
        headings = next(reader)
    except StopIteration:
        raise MalformedSource("source has no header row") from None
    except csv.Error:
        raise MalformedSource("source cannot be read as CSV") from None
    columns = tuple(heading.strip() for heading in headings)
    if not any(columns):
        raise MalformedSource("source header row is empty")
    normalized = [normalize_column(column) for column in columns if column]
    if len(normalized) != len(set(normalized)):
        raise MalformedSource("source has duplicate normalized headings")
    return columns


def _raw_rows(
    stream: BinaryIO, source: SourceDocument
) -> tuple[tuple[str, ...], Iterator[dict[str, str]]]:
    if source.layout is not SourceLayout.CSV:
        raise MalformedSource("large-import partitioning currently requires CSV")
    encoding = _BOM_TOLERANT.get(source.encoding.lower(), source.encoding)
    try:
        text = io.TextIOWrapper(stream, encoding=encoding, errors="strict", newline="")
    except LookupError:
        raise MalformedSource("source encoding is unknown") from None
    reader = csv.reader(text, delimiter=source.delimiter)
    columns = _columns(reader)

    def rows() -> Iterator[dict[str, str]]:
        try:
            for values in reader:
                if len(values) > len(columns):
                    raise MalformedSource("source row has more cells than headings")
                if not any(value.strip() for value in values):
                    continue
                yield {
                    column: (values[index].strip() if index < len(values) else "")
                    for index, column in enumerate(columns)
                    if column
                }
        except (UnicodeDecodeError, csv.Error):
            raise MalformedSource("source cannot be decoded as declared CSV") from None

    return columns, rows()


def iter_csv_partitions(
    source: SourceDocument,
    *,
    open_source: SourceOpener,
    partition_rows: int = DEFAULT_PARTITION_ROWS,
    max_partition_bytes: int = DEFAULT_PARTITION_BYTES,
) -> Iterator[PartitionPayload]:
    """Verify once, then stream bounded CSV artifacts without loading the file."""
    if partition_rows <= 0 or max_partition_bytes <= 0:
        raise PartitionPlanInvalid("partition bounds must be positive")
    _verify_source(source, open_source)

    with open_source() as stream:
        columns, rows = _raw_rows(stream, source)
        ordinal = 0
        start_row = 0
        pending: list[Mapping[str, str]] = []

        def encode(values: Sequence[Mapping[str, str]]) -> bytes:
            target = io.StringIO(newline="")
            writer = csv.DictWriter(
                target,
                fieldnames=list(columns),
                delimiter=source.delimiter,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(values)
            try:
                payload = target.getvalue().encode(source.encoding)
            except (LookupError, UnicodeEncodeError):
                raise MalformedSource("partition cannot use source encoding") from None
            return payload

        for row in rows:
            candidate = [*pending, row]
            payload = encode(candidate)
            if pending and (
                len(candidate) > partition_rows or len(payload) > max_partition_bytes
            ):
                payload = encode(pending)
                yield PartitionPayload(
                    ordinal=ordinal,
                    start_row=start_row,
                    row_count=len(pending),
                    checksum_sha256=hashlib.sha256(payload).hexdigest(),
                    data=payload,
                )
                ordinal += 1
                start_row += len(pending)
                pending = [row]
                if len(encode(pending)) > max_partition_bytes:
                    raise PartitionPlanInvalid(
                        "one source row exceeds max_partition_bytes"
                    )
            else:
                pending = candidate
        if pending:
            payload = encode(pending)
            if len(payload) > max_partition_bytes:
                raise PartitionPlanInvalid("one source row exceeds max_partition_bytes")
            yield PartitionPayload(
                ordinal=ordinal,
                start_row=start_row,
                row_count=len(pending),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                data=payload,
            )


def _validate_descriptors(
    descriptors: Sequence[PartitionDescriptor],
) -> tuple[PartitionDescriptor, ...]:
    ordered = tuple(sorted(descriptors, key=lambda item: item.ordinal))
    if not ordered:
        raise PartitionPlanInvalid("a partition plan must contain at least one row")
    expected_start = 0
    seen_files: set[UUID] = set()
    for ordinal, item in enumerate(ordered):
        if item.ordinal != ordinal or item.start_row != expected_start:
            raise PartitionPlanInvalid(
                "partition ordinals and row ranges must be contiguous"
            )
        if item.file_id in seen_files:
            raise PartitionPlanInvalid("one immutable file cannot back two partitions")
        seen_files.add(item.file_id)
        expected_start += item.row_count
    return ordered


def register_partition_plan(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    source: SourceDocument,
    descriptors: Sequence[PartitionDescriptor],
) -> tuple[ImportPartition, ...]:
    """Persist one complete immutable plan; identical redelivery is a no-op."""
    ordered = _validate_descriptors(descriptors)
    run = db.execute(
        select(ImportRun)
        .where(ImportRun.id == run_id, ImportRun.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise InvalidRunState("partition run does not exist")
    if not run.dry_run or run.status != RunStatus.PENDING:
        raise InvalidRunState("partitions can only be registered on a pending dry run")
    if (
        run.source_file_id != source.file_id
        or run.source_checksum_sha256 != source.checksum_sha256
    ):
        raise SourceMismatch("partition plan belongs to another source")
    existing = tuple(
        db.scalars(
            select(ImportPartition)
            .where(
                ImportPartition.tenant_id == tenant_id,
                ImportPartition.run_id == run_id,
            )
            .order_by(ImportPartition.ordinal)
        )
    )
    if existing:
        identity = tuple(
            (
                item.ordinal,
                item.start_row,
                item.row_count,
                item.partition_file_id,
                item.partition_checksum_sha256,
                item.byte_size,
            )
            for item in existing
        )
        offered = tuple(
            (
                item.ordinal,
                item.start_row,
                item.row_count,
                item.file_id,
                item.checksum_sha256,
                item.byte_size,
            )
            for item in ordered
        )
        if identity != offered:
            raise PartitionPlanInvalid("a run's partition plan is immutable")
        return existing
    for item in ordered:
        db.add(
            ImportPartition(
                tenant_id=tenant_id,
                run_id=run_id,
                ordinal=item.ordinal,
                start_row=item.start_row,
                row_count=item.row_count,
                partition_file_id=item.file_id,
                partition_checksum_sha256=item.checksum_sha256,
                byte_size=item.byte_size,
                status="pending",
                processed_rows=0,
                attempt_count=0,
            )
        )
    db.flush()
    return tuple(
        db.scalars(
            select(ImportPartition)
            .where(
                ImportPartition.tenant_id == tenant_id,
                ImportPartition.run_id == run_id,
            )
            .order_by(ImportPartition.ordinal)
        )
    )


def claim_partition(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> PartitionClaim | None:
    """Atomically lease the first pending or expired partition."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    moment = now or datetime.now(UTC)
    token = uuid4()
    candidate = (
        select(ImportPartition.id)
        .where(
            ImportPartition.tenant_id == tenant_id,
            ImportPartition.run_id == run_id,
            ImportPartition.status != "completed",
            or_(
                ImportPartition.status == "pending",
                ImportPartition.leased_until < moment,
            ),
        )
        .order_by(ImportPartition.ordinal)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed_id = db.execute(
        update(ImportPartition)
        .where(ImportPartition.id == candidate)
        .values(
            status="claimed",
            lease_token=token,
            leased_until=moment + timedelta(seconds=lease_seconds),
            attempt_count=ImportPartition.attempt_count + 1,
        )
        .returning(ImportPartition.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if claimed_id is None:
        finalize_partitioned_run(db, tenant_id=tenant_id, run_id=run_id, now=moment)
        return None
    partition = db.get(ImportPartition, claimed_id)
    if partition is None:  # pragma: no cover - returned id is the row
        raise PartitionPlanInvalid("claimed partition disappeared")
    db.refresh(partition)
    return PartitionClaim(
        partition_id=partition.id,
        run_id=partition.run_id,
        tenant_id=partition.tenant_id,
        lease_token=token,
        file_id=partition.partition_file_id,
        ordinal=partition.ordinal,
        start_row=partition.start_row,
        row_count=partition.row_count,
        checksum_sha256=partition.partition_checksum_sha256,
        byte_size=partition.byte_size,
    )


def finalize_partitioned_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Repair/finalize a run after every worker checkpoint has committed."""
    run = db.execute(
        select(ImportRun)
        .where(ImportRun.id == run_id, ImportRun.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise InvalidRunState("partition run does not exist")
    aggregates = db.execute(
        select(
            func.count(ImportPartition.id),
            func.sum(ImportPartition.row_count),
            func.sum(case((ImportPartition.status != "completed", 1), else_=0)),
        ).where(
            ImportPartition.tenant_id == tenant_id,
            ImportPartition.run_id == run_id,
        )
    ).one()
    partition_count = int(aggregates[0] or 0)
    planned_rows = int(aggregates[1] or 0)
    incomplete = int(aggregates[2] or 0)
    if not partition_count or incomplete:
        return False
    if run.total_rows != planned_rows:
        raise InvalidRunState(
            "partition checkpoints do not equal the immutable planned row count"
        )
    final = RunStatus.DRY_RUN_READY if run.dry_run else RunStatus.COMPLETED
    run.status = final
    run.completed_at = now or datetime.now(UTC)
    db.flush()
    return True


def _claimed_rows(
    claim: PartitionClaim,
    *,
    open_partition: PartitionOpener,
    source: SourceDocument,
    mapping: ColumnMapping,
    fields: FieldSet,
) -> tuple[dict[str, str], ...]:
    missing = fields.required_names - mapping.fields
    if missing:
        raise UnmappedRequiredField(
            f"required field(s) {sorted(missing)} have no source column"
        )
    digest = hashlib.sha256()
    read_bytes = 0

    try:
        with open_partition(claim.file_id) as raw:
            while chunk := raw.read(_DIGEST_CHUNK_BYTES):
                read_bytes += len(chunk)
                if read_bytes > claim.byte_size:
                    raise SourceMismatch("partition exceeds its immutable byte_size")
                digest.update(chunk)
        if read_bytes != claim.byte_size or digest.hexdigest() != claim.checksum_sha256:
            raise SourceMismatch(
                "stored partition differs from its immutable descriptor"
            )
        with open_partition(claim.file_id) as raw:
            columns, rows = _raw_rows(raw, source)
            del columns
            mapped = tuple(apply_mapping(row, mapping) for row in rows)
    except (OSError, ValueError):
        raise SourceMismatch("stored partition could not be read") from None
    if len(mapped) != claim.row_count:
        raise SourceMismatch("stored partition row count differs from its descriptor")
    return mapped


def validate_claimed_partition(
    db: Session,
    claim: PartitionClaim,
    *,
    source: SourceDocument,
    open_partition: PartitionOpener,
    fields: FieldSet,
    validator: RowValidator,
    now: datetime | None = None,
) -> PartitionProgress:
    """Validate one claimed partition with no domain writer in scope."""
    return _process_claimed_partition(
        db,
        claim,
        source=source,
        open_partition=open_partition,
        fields=fields,
        validator=validator,
        now=now,
    )


def apply_claimed_partition(
    db: Session,
    claim: PartitionClaim,
    *,
    source: SourceDocument,
    open_partition: PartitionOpener,
    fields: FieldSet,
    validator: RowValidator,
    applier: RowApplier,
    now: datetime | None = None,
) -> PartitionProgress:
    """Apply one claimed partition with its row outcomes in one transaction."""
    return _process_claimed_partition(
        db,
        claim,
        source=source,
        open_partition=open_partition,
        fields=fields,
        validator=validator,
        applier=applier,
        now=now,
    )


def _process_claimed_partition(
    db: Session,
    claim: PartitionClaim,
    *,
    source: SourceDocument,
    open_partition: PartitionOpener,
    fields: FieldSet,
    validator: RowValidator,
    applier: RowApplier | None = None,
    now: datetime | None = None,
) -> PartitionProgress:
    """Verify and settle one bounded partition in the caller's transaction."""
    moment = now or datetime.now(UTC)
    partition = db.execute(
        select(ImportPartition)
        .where(
            ImportPartition.id == claim.partition_id,
            ImportPartition.tenant_id == claim.tenant_id,
            ImportPartition.run_id == claim.run_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        partition is None
        or partition.status != "claimed"
        or partition.lease_token != claim.lease_token
        or partition.leased_until is None
        or _utc(partition.leased_until) < _utc(moment)
    ):
        raise PartitionClaimLost("partition lease is absent, expired or replaced")
    run = db.get(ImportRun, claim.run_id)
    if run is None or run.tenant_id != claim.tenant_id:
        raise InvalidRunState("partition run does not exist")
    if (
        run.source_file_id != source.file_id
        or run.source_checksum_sha256 != source.checksum_sha256
    ):
        raise SourceMismatch("partition worker was given another source identity")
    if run.dry_run is (applier is not None):
        raise InvalidRunState("dry-run and apply partition entry points are distinct")
    source_for_partition = SourceDocument(
        file_id=claim.file_id,
        checksum_sha256=claim.checksum_sha256,
        layout=SourceLayout(run.source_layout),
        delimiter=run.source_delimiter,
        encoding=run.source_encoding,
    )
    mapping = ColumnMapping(
        tuple((str(pair[0]), str(pair[1])) for pair in (run.column_mapping or []))
    )
    rows = _claimed_rows(
        claim,
        open_partition=open_partition,
        source=source_for_partition,
        mapping=mapping,
        fields=fields,
    )
    ok = 0
    failed = 0
    with db.begin_nested():
        for offset, row in enumerate(rows):
            number = claim.start_row + offset + 1
            issues = tuple(validator.validate(row))
            if any(not isinstance(issue, ImportIssue) for issue in issues):
                raise TypeError(
                    "RowValidator.validate() must return ImportIssue values"
                )
            if issues:
                code, message = _validation_detail(issues)
                _record(
                    db,
                    run,
                    number,
                    row,
                    RowStatus.ERROR,
                    error_code=code,
                    error_message=message,
                )
                failed += 1
            elif applier is None:
                _record(db, run, number, row, RowStatus.OK)
                ok += 1
            else:
                try:
                    with db.begin_nested():
                        result = dict(applier.apply(row))
                        db.flush()
                except RowRejected as exc:
                    _record(
                        db,
                        run,
                        number,
                        row,
                        RowStatus.ERROR,
                        error_code=exc.issue.code,
                        error_message=exc.issue.message,
                    )
                    failed += 1
                else:
                    _record(
                        db,
                        run,
                        number,
                        row,
                        RowStatus.OK,
                        result=result,
                    )
                    ok += 1
        settled = db.execute(
            update(ImportPartition)
            .where(
                ImportPartition.id == claim.partition_id,
                ImportPartition.lease_token == claim.lease_token,
                ImportPartition.status == "claimed",
            )
            .values(
                status="completed",
                processed_rows=claim.row_count,
                lease_token=None,
                leased_until=None,
                completed_at=moment,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(settled, "rowcount", 0) != 1:
            raise PartitionClaimLost("partition lease was lost before settlement")
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == claim.run_id)
            .values(
                status=RunStatus.RUNNING,
                started_at=func.coalesce(ImportRun.started_at, moment),
                total_rows=ImportRun.total_rows + claim.row_count,
                ok_rows=ImportRun.ok_rows + ok,
                failed_rows=ImportRun.failed_rows + failed,
            )
            .execution_options(synchronize_session=False)
        )
        db.flush()

    db.refresh(run)
    complete = finalize_partitioned_run(
        db,
        tenant_id=claim.tenant_id,
        run_id=claim.run_id,
        now=moment,
    )
    return PartitionProgress(
        run.id, partition.id, claim.row_count, ok, failed, complete
    )


def clone_partition_plan(
    db: Session, *, source_run: ImportRun, target_run: ImportRun
) -> None:
    """Give a promoted apply run the exact immutable partition plan."""
    partitions = tuple(
        db.scalars(
            select(ImportPartition)
            .where(
                ImportPartition.tenant_id == source_run.tenant_id,
                ImportPartition.run_id == source_run.id,
            )
            .order_by(ImportPartition.ordinal)
        )
    )
    for partition in partitions:
        db.add(
            ImportPartition(
                tenant_id=target_run.tenant_id,
                run_id=target_run.id,
                ordinal=partition.ordinal,
                start_row=partition.start_row,
                row_count=partition.row_count,
                partition_file_id=partition.partition_file_id,
                partition_checksum_sha256=partition.partition_checksum_sha256,
                byte_size=partition.byte_size,
                status="pending",
                processed_rows=0,
                attempt_count=0,
            )
        )
    db.flush()


def require_unpartitioned(db: Session, run: ImportRun) -> None:
    count = db.scalar(
        select(func.count())
        .select_from(ImportPartition)
        .where(
            ImportPartition.tenant_id == run.tenant_id,
            ImportPartition.run_id == run.id,
        )
    )
    if count:
        raise InvalidRunState(
            "partitioned runs must use the claimed-partition entry points"
        )


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_PARTITION_BYTES",
    "DEFAULT_PARTITION_ROWS",
    "PartitionClaim",
    "PartitionClaimLost",
    "PartitionDescriptor",
    "PartitionError",
    "PartitionPayload",
    "PartitionPlanInvalid",
    "PartitionProgress",
    "apply_claimed_partition",
    "claim_partition",
    "clone_partition_plan",
    "finalize_partitioned_run",
    "iter_csv_partitions",
    "register_partition_plan",
    "require_unpartitioned",
    "validate_claimed_partition",
]
