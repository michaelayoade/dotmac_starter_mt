"""The import run ledger: validate, promote, apply, and resume.

Each processing call owns exactly one chunk inside the caller's transaction.
It locks and reloads the durable run, verifies the raw bytes against the
recorded SHA-256, resumes after the last committed row, and returns one
checkpoint. The caller commits that checkpoint through `dotmac_kernel.db` and
calls again. No generator spans transactions, and no function here commits or
rolls back.

The dry-run/apply split remains structural: `validate_next_chunk` has no
applier parameter, so no domain writer exists anywhere on that call path.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_imports.contracts import (
    MAX_SAFE_ERROR_MESSAGE,
    ColumnMapping,
    FieldSet,
    ImportIssue,
    ImportRunNotFound,
    InvalidRunState,
    PromotionRefused,
    RowApplier,
    RowRejected,
    RowStatus,
    RowValidator,
    RunProgress,
    RunStatus,
    SourceDocument,
    SourceLayout,
    SourceMismatch,
)
from dotmac_imports.models import ImportRun, ImportRunRow
from dotmac_imports.tabular import decode, mapped_rows

# Ported from Sub's `_CHUNK_COMMIT`. One call processes at most this many rows;
# the caller then commits the domain effects and their ledger outcomes together.
DEFAULT_CHUNK_SIZE: int = 200


def create_dry_run(
    db: Session,
    *,
    tenant_id: UUID,
    kind: str,
    source: SourceDocument,
    mapping: ColumnMapping,
    created_by: str | None = None,
) -> ImportRun:
    """Record a pending validation run over a stored document.

    There is deliberately no `dry_run=False` parameter. An apply run can only
    be created by promoting a completed validation (ADR-0025 section 2).
    """
    run = ImportRun(
        tenant_id=tenant_id,
        kind=kind,
        status=RunStatus.PENDING,
        dry_run=True,
        source_run_id=None,
        source_file_id=source.file_id,
        source_checksum_sha256=source.checksum_sha256,
        source_layout=source.layout,
        source_delimiter=source.delimiter,
        source_encoding=source.encoding,
        column_mapping=[list(pair) for pair in mapping.pairs],
        created_by=created_by,
    )
    db.add(run)
    db.flush()
    return run


def get_run(db: Session, *, tenant_id: UUID, run_id: UUID) -> ImportRun:
    run = db.execute(
        select(ImportRun).where(
            ImportRun.id == run_id, ImportRun.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    if run is None:
        raise ImportRunNotFound(f"no import run {run_id} in this tenant")
    return run


def validate_next_chunk(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    data: bytes,
    fields: FieldSet,
    validator: RowValidator,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> RunProgress:
    """Validate one durable chunk without holding a domain writer.

    Re-delivery of a completed validation is idempotent and returns its final
    checkpoint. Re-delivery of a running validation resumes after
    `total_rows`, which is committed atomically with its row outcomes.
    """
    run = _locked_run(db, tenant_id=tenant_id, run_id=run_id)
    _require_processable(run, dry_run=True)
    rows = _rows_from_verified_bytes(run, data=data, fields=fields)
    return _process_next_chunk(
        db,
        run,
        rows=rows,
        validator=validator,
        chunk_size=chunk_size,
    )


def promote(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    source: SourceDocument,
    created_by: str | None = None,
) -> ImportRun:
    """Create the one apply run for a completed, error-free validation."""
    validated = _locked_run(db, tenant_id=tenant_id, run_id=run_id)
    if not validated.dry_run or validated.status != RunStatus.DRY_RUN_READY:
        raise PromotionRefused(
            f"run {run_id} is {validated.status!r}; only a validated dry run "
            f"({RunStatus.DRY_RUN_READY}) can be applied"
        )
    if validated.failed_rows:
        raise PromotionRefused(
            f"run {run_id} has {validated.failed_rows} failed row(s); correct "
            "the source and validate again rather than applying a known-bad "
            "document"
        )
    _require_same_source(validated, source)

    already = db.execute(
        select(ImportRun.id).where(
            ImportRun.tenant_id == tenant_id, ImportRun.source_run_id == run_id
        )
    ).scalar_one_or_none()
    if already is not None:
        raise PromotionRefused(f"run {run_id} was already applied as {already}")

    applied = ImportRun(
        tenant_id=tenant_id,
        kind=validated.kind,
        status=RunStatus.PENDING,
        dry_run=False,
        source_run_id=validated.id,
        source_file_id=validated.source_file_id,
        source_checksum_sha256=validated.source_checksum_sha256,
        source_layout=validated.source_layout,
        source_delimiter=validated.source_delimiter,
        source_encoding=validated.source_encoding,
        column_mapping=validated.column_mapping,
        created_by=created_by,
    )
    db.add(applied)
    db.flush()
    return applied


def apply_next_chunk(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    data: bytes,
    fields: FieldSet,
    validator: RowValidator,
    applier: RowApplier,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> RunProgress:
    """Revalidate and apply one durable chunk from the verified source bytes.

    Each domain effect and its row outcome share the caller's transaction.
    `RowRejected` is an expected, safe row outcome. Every other exception
    escapes and rolls back this chunk instead of being persisted as error text.
    """
    run = _locked_run(db, tenant_id=tenant_id, run_id=run_id)
    _require_processable(run, dry_run=False)
    if run.source_run_id is None:
        raise InvalidRunState(
            f"apply run {run.id} has no source run — an apply run exists only "
            "as the promotion of a validated dry run"
        )
    rows = _rows_from_verified_bytes(run, data=data, fields=fields)
    return _process_next_chunk(
        db,
        run,
        rows=rows,
        validator=validator,
        applier=applier,
        chunk_size=chunk_size,
    )


def mark_failed(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    issue: ImportIssue,
) -> ImportRun:
    """Record a classified whole-run failure without raw exception text."""
    run = _locked_run(db, tenant_id=tenant_id, run_id=run_id)
    if run.status in {RunStatus.DRY_RUN_READY, RunStatus.COMPLETED}:
        raise InvalidRunState(f"completed run {run.id} cannot be marked failed")
    run.status = RunStatus.FAILED
    run.error_code = issue.code
    run.error_message = issue.message
    run.completed_at = _now()
    db.flush()
    return run


# -- internals -------------------------------------------------------------


def _locked_run(db: Session, *, tenant_id: UUID, run_id: UUID) -> ImportRun:
    run = db.execute(
        select(ImportRun)
        .where(ImportRun.id == run_id, ImportRun.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise ImportRunNotFound(f"no import run {run_id} in this tenant")
    return run


def _process_next_chunk(
    db: Session,
    run: ImportRun,
    *,
    rows: Sequence[Mapping[str, str]],
    validator: RowValidator,
    chunk_size: int,
    applier: RowApplier | None = None,
) -> RunProgress:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    final_status = RunStatus.DRY_RUN_READY if applier is None else RunStatus.COMPLETED
    processed = run.total_rows
    if processed > len(rows):
        raise InvalidRunState(
            f"run {run.id} checkpoint {processed} exceeds its {len(rows)} source rows"
        )
    if run.status == final_status:
        if processed != len(rows):
            raise InvalidRunState(
                f"completed run {run.id} records {processed} of {len(rows)} rows"
            )
        return _progress(run)

    if run.status == RunStatus.PENDING:
        run.status = RunStatus.RUNNING
        run.started_at = _now()

    stop = min(processed + chunk_size, len(rows))
    ok = run.ok_rows
    failed = run.failed_rows
    skipped = run.skipped_rows

    # An unexpected exception rolls back every domain effect and ledger row in
    # this chunk. The previously committed checkpoint remains resumable.
    with db.begin_nested():
        for index in range(processed, stop):
            number = index + 1
            row = rows[index]
            issues = tuple(validator.validate(row))
            if any(not isinstance(issue, ImportIssue) for issue in issues):
                raise TypeError(
                    "RowValidator.validate() must return ImportIssue values; "
                    "raw strings are not persistence-safe"
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
                outcome = _apply_one(db, run, number, row, applier)
                if outcome is RowStatus.OK:
                    ok += 1
                else:
                    failed += 1

        _count(run, processed=stop, ok=ok, failed=failed, skipped=skipped)
        if stop == len(rows):
            run.status = final_status
            run.completed_at = _now()
        db.flush()

    return _progress(run)


def _rows_from_verified_bytes(
    run: ImportRun, *, data: bytes, fields: FieldSet
) -> tuple[dict[str, str], ...]:
    actual_digest = hashlib.sha256(data).hexdigest()
    if actual_digest != run.source_checksum_sha256:
        raise SourceMismatch(
            f"run {run.id} expects bytes digest "
            f"{run.source_checksum_sha256[:12]}…; supplied bytes digest is "
            f"{actual_digest[:12]}…"
        )

    source = SourceDocument(
        file_id=run.source_file_id,
        checksum_sha256=run.source_checksum_sha256,
        layout=SourceLayout(run.source_layout),
        delimiter=run.source_delimiter,
        encoding=run.source_encoding,
    )
    columns, raw_rows = decode(data, source=source)
    mapping = ColumnMapping(
        tuple((str(pair[0]), str(pair[1])) for pair in (run.column_mapping or []))
    )
    # `columns` is decoded deliberately even though mapping uses the raw row
    # keys; it keeps malformed/duplicate headings on the verified path.
    del columns
    return tuple(mapped_rows(raw_rows, mapping, fields))


def _apply_one(
    db: Session,
    run: ImportRun,
    number: int,
    row: Mapping[str, str],
    applier: RowApplier,
) -> RowStatus:
    """Apply one row inside its own SAVEPOINT.

    Expected domain refusals roll back only this row. Unexpected exceptions
    escape to the chunk SAVEPOINT and therefore roll back the whole attempt.
    """
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
        return RowStatus.ERROR
    _record(db, run, number, row, RowStatus.OK, result=result)
    return RowStatus.OK


def _record(
    db: Session,
    run: ImportRun,
    number: int,
    row: Mapping[str, str],
    status: RowStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    result: dict[str, object] | None = None,
) -> None:
    db.add(
        ImportRunRow(
            tenant_id=run.tenant_id,
            run_id=run.id,
            row_number=number,
            row_fingerprint_sha256=_fingerprint(row),
            status=status,
            error_code=error_code,
            error_message=error_message,
            result=result,
        )
    )


def _fingerprint(row: Mapping[str, str]) -> str:
    canonical = json.dumps(
        {str(key): str(value) for key, value in row.items()},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validation_detail(issues: Sequence[ImportIssue]) -> tuple[str, str]:
    if len(issues) == 1:
        return issues[0].code, issues[0].message
    message = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
    return "multiple_validation_errors", message[:MAX_SAFE_ERROR_MESSAGE]


def _count(
    run: ImportRun, *, processed: int, ok: int, failed: int, skipped: int
) -> None:
    run.total_rows = processed
    run.ok_rows = ok
    run.failed_rows = failed
    run.skipped_rows = skipped


def _progress(run: ImportRun) -> RunProgress:
    return RunProgress(
        run.id,
        run.total_rows,
        run.ok_rows,
        run.failed_rows,
        run.skipped_rows,
        RunStatus(run.status),
    )


def _require_processable(run: ImportRun, *, dry_run: bool) -> None:
    if run.dry_run is not dry_run:
        raise InvalidRunState(
            f"run {run.id} has dry_run={run.dry_run} and cannot be processed by "
            "this entry point"
        )
    permitted = (
        {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.DRY_RUN_READY}
        if dry_run
        else {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.COMPLETED}
    )
    if run.status not in permitted:
        raise InvalidRunState(
            f"run {run.id} is {run.status!r} and cannot be processed or resumed"
        )


def _require_same_source(run: ImportRun, source: SourceDocument) -> None:
    if (
        run.source_file_id != source.file_id
        or run.source_checksum_sha256 != source.checksum_sha256
    ):
        raise SourceMismatch(
            f"run {run.id} validated file {run.source_file_id} with digest "
            f"{run.source_checksum_sha256[:12]}…; promotion offered "
            f"{source.file_id} with {source.checksum_sha256[:12]}…"
        )


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "apply_next_chunk",
    "create_dry_run",
    "get_run",
    "mark_failed",
    "promote",
    "validate_next_chunk",
]
