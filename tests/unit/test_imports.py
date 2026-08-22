"""Behavioural contract for the import run ledger (ADR-0025).

The properties worth naming: a dry run cannot mutate, an apply is provably the
same document its dry run validated, one bad row does not take the batch with
it, and the ledger never learns what a row meant.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator, Mapping, Sequence

import pytest
from dotmac_imports.contracts import (
    ColumnMapping,
    FieldDeclarationError,
    FieldSet,
    FieldSpec,
    ImportIssue,
    ImportRunNotFound,
    MalformedSource,
    PromotionRefused,
    RowRejected,
    RowStatus,
    RunStatus,
    SourceDocument,
    SourceLayout,
    SourceMismatch,
    UnmappedRequiredField,
    normalize_column,
)
from dotmac_imports.models import ImportRun, ImportRunRow
from dotmac_imports.service import (
    apply_next_chunk,
    create_dry_run,
    get_run,
    mark_failed,
    promote,
    validate_next_chunk,
)
from dotmac_imports.tabular import (
    apply_mapping,
    auto_map,
    decode,
    decode_csv,
    mapped_rows,
    preview,
)
from sqlalchemy import Column, Integer, String, Table, create_engine, event, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
DEFAULT_DATA = b"Ref No,Amount Paid\nR-1,10\n"


def _source(
    data: bytes = DEFAULT_DATA, *, file_id: uuid.UUID | None = None
) -> SourceDocument:
    return SourceDocument(
        file_id=file_id or uuid.uuid4(),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
    )


def _fields() -> FieldSet:
    return FieldSet(
        (
            FieldSpec("reference", required=True, aliases=frozenset({"Ref No"})),
            FieldSpec("amount", required=True, aliases=frozenset({"Amount Paid"})),
            FieldSpec("note"),
        )
    )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_imports": None}},
    )

    # pysqlite does not emit BEGIN until it sees DML, which leaves a SAVEPOINT
    # issued outside any transaction. `_apply_one` depends on savepoints, so the
    # documented SQLAlchemy recipe is required for this fixture to exercise the
    # real code path rather than a degraded one.
    @event.listens_for(engine, "connect")
    def _no_implicit_begin(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):  # type: ignore[no-untyped-def]
        conn.exec_driver_sql("BEGIN")

    ImportRun.__table__.create(engine)
    ImportRunRow.__table__.create(engine)
    _DOMAIN.create(engine)
    with Session(engine) as session:
        yield session


# A stand-in for an importing domain's own table. It exists to prove the module
# never writes one during a dry run.
_DOMAIN = Table(
    "domain_receipts",
    ImportRun.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reference", String(40), nullable=False, unique=True),
)


class Validator:
    """Rejects a row with a non-numeric amount, as a domain would."""

    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
        errors: list[ImportIssue] = []
        if not row.get("reference"):
            errors.append(ImportIssue("reference_required", "reference is required"))
        try:
            float(row.get("amount", ""))
        except ValueError:
            errors.append(ImportIssue("invalid_amount", "amount is not a number"))
        return errors


class Applier:
    """Inserts one domain row, and refuses a reference it has already seen."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.calls = 0

    def apply(self, row: Mapping[str, str]) -> Mapping[str, object]:
        self.calls += 1
        exists = self.db.execute(
            select(_DOMAIN.c.id).where(_DOMAIN.c.reference == row["reference"])
        ).scalar_one_or_none()
        if exists is not None:
            raise RowRejected(
                "duplicate_reference", "a receipt with this reference already exists"
            )
        self.db.execute(_DOMAIN.insert().values(reference=row["reference"]))
        return {"reference": row["reference"]}


def _domain_rows(db: Session) -> list[str]:
    return list(db.execute(select(_DOMAIN.c.reference)).scalars())


def _rows(db: Session, run: ImportRun) -> list[ImportRunRow]:
    return list(
        db.execute(
            select(ImportRunRow)
            .where(ImportRunRow.run_id == run.id)
            .order_by(ImportRunRow.row_number)
        ).scalars()
    )


def _validate_all(
    db: Session,
    run: ImportRun,
    data: bytes,
    *,
    chunk_size: int = 200,
) -> ImportRun:
    while True:
        progress = validate_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            chunk_size=chunk_size,
        )
        db.commit()
        if progress.is_complete:
            return get_run(db, tenant_id=TENANT, run_id=run.id)


def _apply_all(
    db: Session,
    run: ImportRun,
    data: bytes,
    applier: Applier,
    *,
    chunk_size: int = 200,
) -> ImportRun:
    while True:
        progress = apply_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            applier=applier,
            chunk_size=chunk_size,
        )
        db.commit()
        if progress.is_complete:
            return get_run(db, tenant_id=TENANT, run_id=run.id)


# ── field declarations are the domain's vocabulary ──────────────────────────


def test_a_field_set_refuses_an_alias_two_fields_both_claim() -> None:
    """An ambiguous alias would map a column to whichever field was declared
    first — a silent, order-dependent answer."""
    with pytest.raises(FieldDeclarationError, match="claimed by both"):
        FieldSet(
            (
                FieldSpec("amount", aliases=frozenset({"Total"})),
                FieldSpec("gross", aliases=frozenset({"total"})),
            )
        )


def test_a_field_set_refuses_duplicate_names() -> None:
    with pytest.raises(FieldDeclarationError, match="duplicate field name"):
        FieldSet((FieldSpec("amount"), FieldSpec("amount")))


def test_headings_are_compared_in_normalised_form() -> None:
    assert normalize_column(" GL Account Name ") == "gl_account_name"
    assert normalize_column("Ref-No.") == "ref_no"


def test_a_column_mapping_refuses_to_map_one_field_twice() -> None:
    with pytest.raises(FieldDeclarationError, match="mapped twice"):
        ColumnMapping((("amount", "A"), ("amount", "B")))


# ── decoding ────────────────────────────────────────────────────────────────


def test_csv_decodes_to_strings_and_skips_blank_lines() -> None:
    columns, rows = decode_csv(b"Ref No,Amount Paid\nR-1,1200\n\nR-2,0900\n")
    assert columns == ("Ref No", "Amount Paid")
    assert rows == (
        {"Ref No": "R-1", "Amount Paid": "1200"},
        {"Ref No": "R-2", "Amount Paid": "0900"},
    )
    # Not coerced: "0900" is an account code to one domain and a number to
    # another, and only the domain can tell which.
    assert rows[1]["Amount Paid"] == "0900"


def test_a_utf8_bom_does_not_become_part_of_the_first_heading() -> None:
    """Excel writes one. Left in place it makes column one match nothing, and
    the failure looks like a mapping mistake rather than an encoding one."""
    columns, _ = decode_csv("﻿Ref No,Amount Paid\nR-1,5\n".encode())
    assert columns[0] == "Ref No"


def test_two_headings_that_normalise_alike_are_refused() -> None:
    with pytest.raises(MalformedSource, match="duplicate heading"):
        decode_csv(b"Ref No,ref_no\nR-1,R-1\n")


def test_a_short_row_pads_rather_than_shifting_later_columns() -> None:
    _, rows = decode_csv(b"a,b,c\n1,2\n")
    assert rows == ({"a": "1", "b": "2", "c": ""},)


def test_a_row_wider_than_its_header_is_refused_not_truncated() -> None:
    """An extra cell is source data. Silently dropping it would validate and
    apply a different document from the one the operator uploaded."""
    with pytest.raises(MalformedSource, match=r"row 2 has 3 cells.*2 headings"):
        decode_csv(b"a,b\n1,2,3\n")


def test_a_document_with_no_header_is_refused() -> None:
    with pytest.raises(MalformedSource, match="no header row"):
        decode_csv(b"")


def test_the_row_ceiling_refuses_rather_than_truncating() -> None:
    payload = b"a\n" + b"x\n" * 5
    with pytest.raises(MalformedSource, match="row ceiling"):
        decode_csv(payload, max_rows=3)


def test_only_csv_has_a_decoder_in_this_release() -> None:
    """XLSX is declarable so a run records its true layout; decoding it ships
    with the adopter that brings the library and its parity tests."""
    source = SourceDocument(
        file_id=uuid.uuid4(),
        checksum_sha256=hashlib.sha256(b"PK\x03\x04").hexdigest(),
        layout=SourceLayout.XLSX,
    )
    with pytest.raises(MalformedSource, match="no decoder"):
        decode(b"PK\x03\x04", source=source)


def test_a_source_digest_must_be_a_sha256() -> None:
    with pytest.raises(MalformedSource, match="64 lowercase hex"):
        SourceDocument(file_id=uuid.uuid4(), checksum_sha256="deadbeef")


# ── mapping and preview ─────────────────────────────────────────────────────


def test_auto_map_resolves_aliases_and_leaves_extra_columns_alone() -> None:
    columns = ("Ref No", "Amount Paid", "Posted By")
    mapping = auto_map(columns, _fields())
    assert mapping.as_dict() == {"reference": "Ref No", "amount": "Amount Paid"}


def test_preview_reports_what_is_missing_instead_of_refusing() -> None:
    """The operator's next action is to fix the mapping, and a bare refusal
    makes that guesswork."""
    columns, rows = decode_csv(b"Ref No,Posted By\nR-1,ada\n")
    plan = preview(columns, rows, _fields())
    assert plan.missing_required_fields == ("amount",)
    assert plan.unmapped_columns == ("Posted By",)
    assert not plan.is_mappable


def test_preview_refuses_a_mapping_naming_a_column_the_document_lacks() -> None:
    columns, rows = decode_csv(b"Ref No,Amount Paid\nR-1,5\n")
    with pytest.raises(UnmappedRequiredField, match="does not have"):
        preview(
            columns,
            rows,
            _fields(),
            mapping=ColumnMapping((("reference", "Nope"),)),
        )


def test_a_missing_required_field_is_reported_once_not_once_per_row() -> None:
    columns, rows = decode_csv(b"Ref No\nR-1\nR-2\nR-3\n")
    with pytest.raises(UnmappedRequiredField, match=r"\['amount'\]"):
        list(mapped_rows(rows, auto_map(columns, _fields()), _fields()))


def test_apply_mapping_projects_onto_declared_field_names() -> None:
    assert apply_mapping(
        {"Ref No": "R-1"}, ColumnMapping((("reference", "Ref No"),))
    ) == {"reference": "R-1"}


# ── the dry run ─────────────────────────────────────────────────────────────


def _dry_run(db: Session, source: SourceDocument | None = None) -> ImportRun:
    return create_dry_run(
        db,
        tenant_id=TENANT,
        kind="receipts",
        source=source or _source(),
        mapping=ColumnMapping((("amount", "Amount Paid"), ("reference", "Ref No"))),
    )


def test_a_dry_run_records_every_row_and_changes_no_domain_state(
    db: Session,
) -> None:
    data = b"Ref No,Amount Paid\nR-1,10\nR-2,twelve\n"
    run = _dry_run(db, _source(data))
    run = _validate_all(db, run, data)

    assert run.status == RunStatus.DRY_RUN_READY
    assert (run.total_rows, run.ok_rows, run.failed_rows) == (2, 1, 1)
    outcomes = [
        (row.status, row.error_code, row.error_message) for row in _rows(db, run)
    ]
    assert outcomes[0] == (RowStatus.OK, None, None)
    assert outcomes[1] == (RowStatus.ERROR, "invalid_amount", "amount is not a number")
    assert _domain_rows(db) == []


def test_the_ledger_fingerprints_a_row_without_persisting_its_values(
    db: Session,
) -> None:
    data = b"Ref No,Amount Paid,note\nPRIVATE-REF,10,customer secret\n"
    run = _dry_run(db, _source(data))
    run = _validate_all(db, run, data)

    outcome = _rows(db, run)[0]
    assert len(outcome.row_fingerprint_sha256) == 64
    assert "PRIVATE-REF" not in repr(outcome)
    assert "customer secret" not in repr(outcome)


def test_validation_rejects_untyped_error_text_instead_of_persisting_it(
    db: Session,
) -> None:
    class UnsafeValidator:
        def validate(self, row: Mapping[str, str]) -> Sequence[str]:
            return [f"bad secret value {row['reference']}"]

    data = b"Ref No,Amount Paid\nPRIVATE-REF,10\n"
    run = _dry_run(db, _source(data))
    with pytest.raises(TypeError, match="ImportIssue"):
        validate_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=data,
            fields=_fields(),
            validator=UnsafeValidator(),  # type: ignore[arg-type]
        )

    assert _rows(db, run) == []


def test_validate_next_chunk_cannot_be_handed_an_applier(db: Session) -> None:
    """The structural half of ADR-0025 § 2, asserted behaviourally."""
    run = _dry_run(db)
    with pytest.raises(TypeError, match="applier"):
        validate_next_chunk(  # type: ignore[call-arg]
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=DEFAULT_DATA,
            fields=_fields(),
            validator=Validator(),
            applier=Applier(db),
        )


def test_a_completed_validation_redelivery_is_idempotent(db: Session) -> None:
    run = _dry_run(db)
    completed = _validate_all(db, run, DEFAULT_DATA)
    before = [(row.id, row.row_fingerprint_sha256) for row in _rows(db, completed)]

    progress = validate_next_chunk(
        db,
        tenant_id=TENANT,
        run_id=completed.id,
        data=DEFAULT_DATA,
        fields=_fields(),
        validator=Validator(),
    )

    assert progress.is_complete
    replayed = [(row.id, row.row_fingerprint_sha256) for row in _rows(db, completed)]
    assert replayed == before


def test_validation_resumes_from_the_committed_checkpoint(
    db: Session,
) -> None:
    data = b"Ref No,Amount Paid\n" b"R-0,1\nR-1,1\nR-2,1\nR-3,1\nR-4,1\n"
    run = _dry_run(db, _source(data))
    first = validate_next_chunk(
        db,
        tenant_id=TENANT,
        run_id=run.id,
        data=data,
        fields=_fields(),
        validator=Validator(),
        chunk_size=2,
    )
    assert (first.processed, first.status, first.is_complete) == (
        2,
        RunStatus.RUNNING,
        False,
    )
    run_id = run.id
    db.commit()

    with Session(db.get_bind()) as resumed:
        second = validate_next_chunk(
            resumed,
            tenant_id=TENANT,
            run_id=run_id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            chunk_size=2,
        )
        assert second.processed == 4
        resumed.commit()
        final = validate_next_chunk(
            resumed,
            tenant_id=TENANT,
            run_id=run_id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            chunk_size=2,
        )
        assert (final.processed, final.status, final.is_complete) == (
            5,
            RunStatus.DRY_RUN_READY,
            True,
        )
        resumed.commit()

    restored = get_run(db, tenant_id=TENANT, run_id=run_id)
    assert (restored.total_rows, restored.ok_rows) == (5, 5)
    assert [row.row_number for row in _rows(db, restored)] == [1, 2, 3, 4, 5]


# ── promotion ───────────────────────────────────────────────────────────────


def _validated(
    db: Session, source: SourceDocument, data: bytes = DEFAULT_DATA
) -> ImportRun:
    run = _dry_run(db, source)
    return _validate_all(db, run, data)


def test_promotion_creates_an_apply_run_bound_to_its_source(db: Session) -> None:
    source = _source()
    validated = _validated(db, source)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)

    assert applied.dry_run is False
    assert applied.source_run_id == validated.id
    assert applied.status == RunStatus.PENDING
    assert applied.source_checksum_sha256 == validated.source_checksum_sha256


def test_a_run_cannot_be_applied_twice(db: Session) -> None:
    source = _source()
    validated = _validated(db, source)
    promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    with pytest.raises(PromotionRefused, match="already applied"):
        promote(db, tenant_id=TENANT, run_id=validated.id, source=source)


def test_an_apply_must_read_the_bytes_its_dry_run_validated(db: Session) -> None:
    """Sub compared the whole stored payload for equality, which was only
    possible because it kept the payload in a column. The digest is the same
    guarantee against bytes `dotmac-files` already hashed."""
    source = _source()
    validated = _validated(db, source)
    other_source = _source(b"Ref No,Amount Paid\nR-9,900\n", file_id=source.file_id)
    with pytest.raises(SourceMismatch):
        promote(db, tenant_id=TENANT, run_id=validated.id, source=other_source)


def test_an_unvalidated_run_cannot_be_promoted(db: Session) -> None:
    source = _source()
    run = _dry_run(db, source)
    with pytest.raises(PromotionRefused, match="only a validated dry run"):
        promote(db, tenant_id=TENANT, run_id=run.id, source=source)


def test_a_run_with_failed_rows_cannot_be_promoted(db: Session) -> None:
    data = b"Ref No,Amount Paid\nR-1,nope\n"
    source = _source(data)
    run = _dry_run(db, source)
    run = _validate_all(db, run, data)
    with pytest.raises(PromotionRefused, match="failed row"):
        promote(db, tenant_id=TENANT, run_id=run.id, source=source)


def test_another_tenants_run_is_not_found(db: Session) -> None:
    validated = _validated(db, _source())
    with pytest.raises(ImportRunNotFound):
        get_run(db, tenant_id=uuid.uuid4(), run_id=validated.id)


# ── the apply ───────────────────────────────────────────────────────────────


def test_apply_mutates_the_domain_and_records_its_opaque_result(
    db: Session,
) -> None:
    source = _source()
    validated = _validated(db, source)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    applier = Applier(db)
    applied = _apply_all(db, applied, DEFAULT_DATA, applier)

    assert applied.status == RunStatus.COMPLETED
    assert _domain_rows(db) == ["R-1"]
    row = _rows(db, applied)[0]
    assert row.status == RowStatus.OK
    assert row.result == {"reference": "R-1"}


def test_one_failing_row_becomes_an_error_record_not_a_failed_batch(
    db: Session,
) -> None:
    """Sub's per-row SAVEPOINT, preserved. The duplicate reference raises inside
    the domain's applier; the rows on either side of it must still land."""
    data = b"Ref No,Amount Paid\n" b"R-1,1\nR-1,2\nR-2,3\n"
    source = _source(data)
    validated = _validated(db, source, data)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    applied = _apply_all(db, applied, data, Applier(db))

    assert (applied.ok_rows, applied.failed_rows) == (2, 1)
    assert _domain_rows(db) == ["R-1", "R-2"]
    assert [row.status for row in _rows(db, applied)] == [
        RowStatus.OK,
        RowStatus.ERROR,
        RowStatus.OK,
    ]
    duplicate = _rows(db, applied)[1]
    assert duplicate.error_code == "duplicate_reference"
    assert duplicate.error_message == "a receipt with this reference already exists"


def test_a_row_the_domain_now_rejects_is_an_error_not_a_silent_write(
    db: Session,
) -> None:
    """Apply re-validates. The dry run proved the document was acceptable then;
    a domain's answer can legitimately have changed since."""
    apply_data = b"Ref No,Amount Paid\nR-1,not-a-number\n"
    # The source digest cannot change between phases. The domain answer changes
    # instead, represented here by a validator whose policy changed after the
    # successful dry run.
    source = _source(apply_data)
    run = _dry_run(db, source)

    class InitiallyAccepts(Validator):
        def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
            return []

    while True:
        progress = validate_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            data=apply_data,
            fields=_fields(),
            validator=InitiallyAccepts(),
        )
        db.commit()
        if progress.is_complete:
            break
    validated = get_run(db, tenant_id=TENANT, run_id=run.id)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    applier = Applier(db)
    applied = _apply_all(db, applied, apply_data, applier)
    assert applier.calls == 0
    assert _domain_rows(db) == []
    assert applied.failed_rows == 1


def test_apply_reverifies_raw_bytes_at_its_own_entry_point(db: Session) -> None:
    """Promotion alone is insufficient: rows used by apply must come from the
    exact bytes that validation hashed, not an arbitrary caller sequence."""
    source = _source()
    validated = _validated(db, source)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    applier = Applier(db)

    with pytest.raises(SourceMismatch, match="bytes digest"):
        apply_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=applied.id,
            data=b"Ref No,Amount Paid\nR-OTHER,999\n",
            fields=_fields(),
            validator=Validator(),
            applier=applier,
        )

    assert applier.calls == 0
    assert _domain_rows(db) == []
    assert _rows(db, applied) == []


def test_apply_resumes_and_completed_redelivery_does_not_repeat_effects(
    db: Session,
) -> None:
    data = b"Ref No,Amount Paid\nR-1,1\nR-2,2\nR-3,3\n"
    source = _source(data)
    validated = _validated(db, source, data)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)
    applied_id = applied.id
    db.commit()

    applier = Applier(db)
    first = apply_next_chunk(
        db,
        tenant_id=TENANT,
        run_id=applied_id,
        data=data,
        fields=_fields(),
        validator=Validator(),
        applier=applier,
        chunk_size=1,
    )
    assert (first.processed, first.status) == (1, RunStatus.RUNNING)
    db.commit()

    with Session(db.get_bind()) as resumed:
        resumed_applier = Applier(resumed)
        second = apply_next_chunk(
            resumed,
            tenant_id=TENANT,
            run_id=applied_id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            applier=resumed_applier,
            chunk_size=2,
        )
        assert (second.processed, second.status) == (3, RunStatus.COMPLETED)
        resumed.commit()
        replay = apply_next_chunk(
            resumed,
            tenant_id=TENANT,
            run_id=applied_id,
            data=data,
            fields=_fields(),
            validator=Validator(),
            applier=resumed_applier,
            chunk_size=2,
        )
        assert replay.is_complete
        assert resumed_applier.calls == 2

    assert _domain_rows(db) == ["R-1", "R-2", "R-3"]


def test_an_unexpected_applier_exception_fails_the_attempt_without_leaking_it(
    db: Session,
) -> None:
    class BrokenApplier:
        def apply(self, row: Mapping[str, str]) -> Mapping[str, object]:
            raise RuntimeError(f"database password beside {row['reference']}")

    source = _source()
    validated = _validated(db, source)
    applied = promote(db, tenant_id=TENANT, run_id=validated.id, source=source)

    with pytest.raises(RuntimeError, match="database password"):
        apply_next_chunk(
            db,
            tenant_id=TENANT,
            run_id=applied.id,
            data=DEFAULT_DATA,
            fields=_fields(),
            validator=Validator(),
            applier=BrokenApplier(),
        )
    db.commit()

    restored = get_run(db, tenant_id=TENANT, run_id=applied.id)
    assert restored.status == RunStatus.RUNNING
    assert restored.error_message is None
    assert _rows(db, restored) == []
    assert _domain_rows(db) == []


def test_a_whole_run_failure_records_only_typed_safe_detail(db: Session) -> None:
    run = _dry_run(db)
    failed = mark_failed(
        db,
        tenant_id=TENANT,
        run_id=run.id,
        issue=ImportIssue("source_missing", "source object is missing"),
    )
    assert failed.status == RunStatus.FAILED
    assert failed.error_code == "source_missing"
    assert failed.error_message == "source object is missing"
    assert _rows(db, failed) == []
