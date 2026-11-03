"""Versioned partition-manifest loader: closed parsing and idempotent plans."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from dotmac_imports.contracts import (
    ColumnMapping,
    FieldSet,
    FieldSpec,
    ImportIssue,
    InvalidRunState,
    RowApplier,
    RunStatus,
    SourceDocument,
    SourceMismatch,
)
from dotmac_imports.models import ImportPartition, ImportRun, ImportRunRow
from dotmac_imports.partition_manifest import (
    MANIFEST_FORMAT,
    MAX_MANIFEST_BYTES,
    ManifestDigestMismatch,
    ManifestError,
    ManifestVersionUnsupported,
    load_partition_manifest,
    parse_partition_manifest,
    register_manifest_plan,
    render_partition_manifest,
    verify_manifest_partitions,
)
from dotmac_imports.partitioning import (
    PartitionDescriptor,
    PartitionPlanInvalid,
    apply_claimed_partition,
    claim_partition,
    iter_csv_partitions,
    read_claimed_partition,
    validate_claimed_partition,
)
from dotmac_imports.service import create_dry_run, promote
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
DATA = b"Ref No,Amount Paid\nR-1,10\nR-2,20\nR-3,30\n"


def _source(data: bytes = DATA) -> SourceDocument:
    return SourceDocument(
        file_id=uuid.uuid4(), checksum_sha256=hashlib.sha256(data).hexdigest()
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
        return ()


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


def _plan(
    source: SourceDocument, data: bytes = DATA, partition_rows: int = 2
) -> tuple[list[PartitionDescriptor], dict[uuid.UUID, bytes]]:
    stored: dict[uuid.UUID, bytes] = {}
    descriptors: list[PartitionDescriptor] = []
    for payload in iter_csv_partitions(
        source,
        open_source=lambda: io.BytesIO(data),
        partition_rows=partition_rows,
    ):
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
    return descriptors, stored


def _document(manifest: bytes) -> dict:  # type: ignore[type-arg]
    return json.loads(manifest)


def _reseal(document: dict) -> bytes:  # type: ignore[type-arg]
    """Recompute the self-digest the way a producer would."""
    body = {k: v for k, v in document.items() if k != "manifest_sha256"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    document["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return json.dumps(document).encode()


def test_render_and_parse_round_trip_the_plan() -> None:
    source = _source()
    descriptors, _ = _plan(source)
    rendered = render_partition_manifest(source, descriptors)
    assert render_partition_manifest(source, descriptors) == rendered
    manifest = parse_partition_manifest(rendered)
    assert manifest.version == 1
    assert manifest.source_file_id == source.file_id
    assert manifest.partitions == tuple(descriptors)
    assert manifest.total_rows == 3
    assert _document(rendered)["format"] == MANIFEST_FORMAT


def test_the_file_loader_reads_a_bounded_file(tmp_path: Path) -> None:
    source = _source()
    descriptors, _ = _plan(source)
    path = tmp_path / "opening.manifest.json"
    path.write_bytes(render_partition_manifest(source, descriptors))
    assert load_partition_manifest(path).partitions == tuple(descriptors)
    with pytest.raises(ManifestError) as missing:
        load_partition_manifest(tmp_path / "absent.json")
    assert str(tmp_path) not in str(missing.value)
    path.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ManifestError, match="size ceiling"):
        load_partition_manifest(path)


@pytest.mark.parametrize("version", [0, 2, "1", True, None, 1.5])
def test_an_unknown_version_is_refused_even_if_resealed(version: object) -> None:
    source = _source()
    descriptors, _ = _plan(source)
    document = _document(render_partition_manifest(source, descriptors))
    document["version"] = version
    with pytest.raises(ManifestVersionUnsupported):
        parse_partition_manifest(_reseal(document))


def test_an_unknown_format_is_refused() -> None:
    source = _source()
    descriptors, _ = _plan(source)
    document = _document(render_partition_manifest(source, descriptors))
    document["format"] = "something.else"
    with pytest.raises(ManifestVersionUnsupported):
        parse_partition_manifest(_reseal(document))


def test_a_digest_mismatch_is_refused() -> None:
    source = _source()
    descriptors, _ = _plan(source)
    document = _document(render_partition_manifest(source, descriptors))
    document["partitions"][0]["row_count"] += 1  # edited, digest NOT recomputed
    with pytest.raises(ManifestDigestMismatch):
        parse_partition_manifest(json.dumps(document).encode())


def test_an_out_of_band_digest_catches_a_recomputed_forgery() -> None:
    source = _source()
    descriptors, _ = _plan(source)
    rendered = render_partition_manifest(source, descriptors)
    pinned = _document(rendered)["manifest_sha256"]
    assert parse_partition_manifest(rendered, expected_sha256=pinned)
    document = _document(rendered)
    document["partitions"][1]["checksum_sha256"] = "0" * 64
    forged = _reseal(document)
    assert parse_partition_manifest(forged)  # self-digest alone cannot tell
    with pytest.raises(ManifestDigestMismatch):
        parse_partition_manifest(forged, expected_sha256=pinned)
    with pytest.raises(ValueError):
        parse_partition_manifest(rendered, expected_sha256="NOT-A-DIGEST")


def test_duplicate_partitions_are_refused() -> None:
    source = _source()
    descriptors, _ = _plan(source)
    base = _document(render_partition_manifest(source, descriptors))

    same_ordinal = json.loads(json.dumps(base))
    same_ordinal["partitions"][1]["ordinal"] = 0
    with pytest.raises(ManifestError, match="twice"):
        parse_partition_manifest(_reseal(same_ordinal))

    same_file = json.loads(json.dumps(base))
    same_file["partitions"][1]["file_id"] = same_file["partitions"][0]["file_id"]
    with pytest.raises(ManifestError, match="twice"):
        parse_partition_manifest(_reseal(same_file))

    repeated_key = b'{"format":"%s","format":"%s","version":1}' % (
        MANIFEST_FORMAT.encode(),
        MANIFEST_FORMAT.encode(),
    )
    with pytest.raises(ManifestError, match="repeats"):
        parse_partition_manifest(repeated_key)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d["source"].update(extra=1),
        lambda d: d["partitions"][0].update(extra=1),
        lambda d: d["partitions"][0].pop("byte_size"),
        lambda d: d["partitions"][0].update(
            file_id=d["partitions"][0]["file_id"].upper()
        ),
        lambda d: d["partitions"][0].update(checksum_sha256="A" * 64),
        lambda d: d["partitions"][0].update(row_count=True),
        lambda d: d["partitions"][0].update(row_count=-1),
        lambda d: d["partitions"][0].update(byte_size=0),
        lambda d: d["partitions"][1].update(start_row=5),
        lambda d: d["partitions"].reverse(),
        lambda d: d.update(partitions=[]),
        lambda d: d["source"].update(checksum_sha256="short"),
    ],
)
def test_malformed_members_are_refused_even_if_resealed(mutate) -> None:  # type: ignore[no-untyped-def]
    source = _source()
    descriptors, _ = _plan(source)
    document = _document(render_partition_manifest(source, descriptors))
    mutate(document)
    with pytest.raises(PartitionPlanInvalid):
        parse_partition_manifest(_reseal(document))


@pytest.mark.parametrize(
    "raw", [b"", b"[]", b"not json", b"\xff\xfe", b'{"a": NaN}', b"null"]
)
def test_non_manifests_are_refused(raw: bytes) -> None:
    with pytest.raises(ManifestError):
        parse_partition_manifest(raw)


def test_verification_streams_each_object_against_its_digest() -> None:
    source = _source()
    descriptors, stored = _plan(source)
    manifest = parse_partition_manifest(render_partition_manifest(source, descriptors))
    verify_manifest_partitions(
        manifest, open_partition=lambda file_id: io.BytesIO(stored[file_id])
    )
    first = descriptors[0].file_id
    tampered = {**stored, first: stored[first].replace(b"R-1", b"R-9")}
    with pytest.raises(ManifestDigestMismatch):
        verify_manifest_partitions(
            manifest, open_partition=lambda file_id: io.BytesIO(tampered[file_id])
        )
    grown = {**stored, first: stored[first] + b"x"}
    with pytest.raises(ManifestDigestMismatch):
        verify_manifest_partitions(
            manifest, open_partition=lambda file_id: io.BytesIO(grown[file_id])
        )

    def missing(_file_id: uuid.UUID) -> io.BytesIO:
        raise FileNotFoundError("secret/path")

    with pytest.raises(ManifestDigestMismatch) as err:
        verify_manifest_partitions(manifest, open_partition=missing)
    assert "secret" not in str(err.value)


def _run(db: Session, source: SourceDocument) -> ImportRun:
    return create_dry_run(
        db,
        tenant_id=TENANT,
        kind="opening",
        source=source,
        mapping=ColumnMapping((("amount", "Amount Paid"), ("reference", "Ref No"))),
    )


def test_registration_is_idempotent_and_a_different_plan_is_refused(
    db: Session,
) -> None:
    source = _source()
    descriptors, _ = _plan(source)
    manifest = parse_partition_manifest(render_partition_manifest(source, descriptors))
    run = _run(db, source)
    first = register_manifest_plan(
        db, tenant_id=TENANT, run_id=run.id, source=source, manifest=manifest
    )
    again = register_manifest_plan(
        db, tenant_id=TENANT, run_id=run.id, source=source, manifest=manifest
    )
    assert [p.id for p in first] == [p.id for p in again]
    assert len(db.scalars(select(ImportPartition)).all()) == len(descriptors)

    other, _ = _plan(source, partition_rows=1)
    other_manifest = parse_partition_manifest(render_partition_manifest(source, other))
    with pytest.raises(PartitionPlanInvalid, match="immutable"):
        register_manifest_plan(
            db,
            tenant_id=TENANT,
            run_id=run.id,
            source=source,
            manifest=other_manifest,
        )
    assert len(db.scalars(select(ImportPartition)).all()) == len(descriptors)


def test_a_manifest_for_another_source_or_run_state_is_refused(db: Session) -> None:
    source = _source()
    descriptors, _ = _plan(source)
    manifest = parse_partition_manifest(render_partition_manifest(source, descriptors))
    other_source = SourceDocument(
        file_id=uuid.uuid4(), checksum_sha256=source.checksum_sha256
    )
    run = _run(db, other_source)
    with pytest.raises(SourceMismatch):
        register_manifest_plan(
            db, tenant_id=TENANT, run_id=run.id, source=other_source, manifest=manifest
        )
    with pytest.raises(InvalidRunState):
        register_manifest_plan(
            db,
            tenant_id=TENANT,
            run_id=uuid.uuid4(),
            source=source,
            manifest=manifest,
        )
    assert db.scalars(select(ImportPartition)).all() == []


def test_a_manifest_plan_drives_the_existing_lease_checkpoint_and_applier_port(
    db: Session,
) -> None:
    source = _source()
    descriptors, stored = _plan(source)
    manifest = parse_partition_manifest(render_partition_manifest(source, descriptors))
    run = _run(db, source)
    register_manifest_plan(
        db, tenant_id=TENANT, run_id=run.id, source=source, manifest=manifest
    )
    while (claim := claim_partition(db, tenant_id=TENANT, run_id=run.id)) is not None:
        prepared = read_claimed_partition(
            claim, open_partition=lambda file_id: io.BytesIO(stored[file_id])
        )
        validate_claimed_partition(
            db, prepared, fields=_fields(), validator=Validator()
        )
    db.refresh(run)
    assert run.status == RunStatus.DRY_RUN_READY

    applied = promote(db, tenant_id=TENANT, run_id=run.id, source=source)
    applier = Applier()
    assert isinstance(applier, RowApplier)
    while (
        claim := claim_partition(db, tenant_id=TENANT, run_id=applied.id)
    ) is not None:
        prepared = read_claimed_partition(
            claim, open_partition=lambda file_id: io.BytesIO(stored[file_id])
        )
        apply_claimed_partition(
            db,
            prepared,
            fields=_fields(),
            validator=Validator(),
            applier=applier,
        )
    db.refresh(applied)
    assert applied.status == RunStatus.COMPLETED
    assert sorted(applier.calls) == ["R-1", "R-2", "R-3"]
