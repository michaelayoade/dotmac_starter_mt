"""Behavioural contract for streamed files on both security planes."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from typing import IO
from uuid import uuid4

import pytest
from dotmac_files.contracts import (
    FilePolicy,
    FileState,
    ForeignScopeFile,
    IntegrityMismatch,
    InvalidFileRef,
    InvalidFileState,
    PreparedFileConflict,
    ProviderMismatch,
    ReadLimitExceeded,
    ReadTypeNotAllowed,
    StoredFileNotFound,
    UnsafeFile,
)
from dotmac_files.physical import (
    delete_object,
    delete_orphans,
    list_objects,
    observe_object,
    open_object,
    parse_file_ref,
    prepare_upload,
    read_verified,
    recheck_and_delete_orphan,
)
from dotmac_files.providers import ObjectInfo, StorageBoundaryViolation, StorageProvider
from dotmac_files.service import (
    deletion_target,
    download_target,
    finalize_purge,
    find_orphan_keys,
    reconciliation_target,
    record_presence,
    request_deletion,
    stage_file,
    verified_read_target,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


class MemoryProvider(StorageProvider):
    code = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(
        self,
        key: str,
        content: IO[bytes],
        *,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> None:
        del content_type, checksum_sha256
        payload = content.read()
        assert len(payload) == size_bytes
        self.objects[key] = payload

    def open(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[key])

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list(self, prefix: str) -> tuple[ObjectInfo, ...]:
        now = datetime.now(UTC)
        return tuple(
            ObjectInfo(key=key, size_bytes=len(value), last_modified=now)
            for key, value in self.objects.items()
            if key.startswith(prefix)
        )


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_files": None}},
    )
    from dotmac_files.models import PlatformStoredFile, TenantStoredFile

    TenantStoredFile.__table__.create(engine)
    PlatformStoredFile.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _policy() -> FilePolicy:
    return FilePolicy(
        max_bytes=1_024,
        allowed_extensions=frozenset({".pdf", ".csv", ".xls", ".xlsx"}),
        allowed_media_types=frozenset(
            {
                "application/pdf",
                "text/csv",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ),
    )


def _xlsx_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
    return output.getvalue()


def _docx_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return output.getvalue()


def _zip_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("readme.txt", "hello")
    return output.getvalue()


def _document_policy() -> FilePolicy:
    return FilePolicy(
        max_bytes=1_048_576,
        allowed_extensions=frozenset({".doc", ".docx", ".txt", ".zip", ".rtf"}),
        allowed_media_types=frozenset(
            {
                "application/msword",
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document",
                "text/plain",
                "application/zip",
                "application/rtf",
            }
        ),
    )


def _bmff_payload(brand: bytes) -> bytes:
    box_size = (24).to_bytes(4, "big")
    return box_size + b"ftyp" + brand + b"\x00\x00\x00\x00" + brand + b"mif1"


def _image_policy() -> FilePolicy:
    return FilePolicy(
        max_bytes=1_048_576,
        allowed_extensions=frozenset(
            {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif"}
        ),
        allowed_media_types=frozenset(
            {
                "image/png",
                "image/jpeg",
                "image/gif",
                "image/webp",
                "image/heic",
                "image/heif",
            }
        ),
    )


def test_upload_uses_only_trusted_scope_and_generated_identity_in_the_key() -> None:
    tenant = uuid4()
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=TenantScope(tenant),
        policy=_policy(),
        original_filename="August Invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )

    assert prepared.storage_key == f"tenants/{tenant}/files/{prepared.id}"
    assert "August" not in prepared.storage_key
    assert prepared.detected_media_type == "application/pdf"
    assert prepared.checksum_sha256.startswith("sha256:")
    assert provider.objects[prepared.storage_key] == b"%PDF-1.7\nbody"


def test_platform_upload_uses_a_distinct_trusted_prefix() -> None:
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=PlatformScope(),
        policy=_policy(),
        original_filename="licence-bundle.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nplatform",),
    )

    assert prepared.scope == PlatformScope()
    assert prepared.storage_key == f"platform/files/{prepared.id}"
    assert provider.objects[prepared.storage_key] == b"%PDF-1.7\nplatform"


@pytest.mark.parametrize("name", ["../secret.pdf", "/etc/secret.pdf", "", "a\x00.pdf"])
def test_upload_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(UnsafeFile):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=_policy(),
            original_filename=name,
            declared_media_type="application/pdf",
            chunks=(b"%PDF-1.7\n",),
        )


def test_upload_rejects_content_type_spoofing() -> None:
    with pytest.raises(UnsafeFile, match="does not match"):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=_policy(),
            original_filename="invoice.pdf",
            declared_media_type="application/pdf",
            chunks=(b"name,email\nAda,ada@example.net\n",),
        )


@pytest.mark.parametrize(
    ("name", "media_type", "payload"),
    [
        (
            "legacy.xls",
            "application/vnd.ms-excel",
            bytes.fromhex("D0CF11E0A1B11AE1") + b"workbook",
        ),
        (
            "modern.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx_payload(),
        ),
    ],
)
def test_excel_formats_are_recognised_by_content_signature(
    name: str, media_type: str, payload: bytes
) -> None:
    prepared = prepare_upload(
        MemoryProvider(),
        scope=TenantScope(uuid4()),
        policy=_policy(),
        original_filename=name,
        declared_media_type=media_type,
        chunks=(payload,),
    )
    assert prepared.detected_media_type == media_type


@pytest.mark.parametrize(
    ("name", "media_type", "payload"),
    [
        ("avatar.png", "image/png", b"\x89PNG\r\n\x1a\nimage"),
        ("avatar.jpg", "image/jpeg", b"\xff\xd8\xff\xe0image"),
        ("avatar.jpeg", "image/jpeg", b"\xff\xd8\xff\xe1image"),
        ("avatar.gif", "image/gif", b"GIF89aimage"),
        ("avatar.webp", "image/webp", b"RIFF\x05\x00\x00\x00WEBPimage"),
        ("photo.heic", "image/heic", _bmff_payload(b"heic")),
        ("photo.heif", "image/heif", _bmff_payload(b"mif1")),
    ],
)
def test_academy_avatar_formats_are_recognised_by_content_signature(
    name: str, media_type: str, payload: bytes
) -> None:
    prepared = prepare_upload(
        MemoryProvider(),
        scope=TenantScope(uuid4()),
        policy=_image_policy(),
        original_filename=name,
        declared_media_type=media_type,
        chunks=(payload,),
    )
    assert prepared.detected_media_type == media_type


@pytest.mark.parametrize(
    ("name", "media_type", "payload"),
    [
        (
            "letter.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_payload(),
        ),
        (
            "letter.doc",
            "application/msword",
            bytes.fromhex("D0CF11E0A1B11AE1")
            + b"\x00" * 512
            + "WordDocument".encode("utf-16-le"),
        ),
        ("notes.txt", "text/plain", b"plain meeting notes\nsecond line\n"),
        ("bundle.zip", "application/zip", _zip_payload()),
        (
            "policy.rtf",
            "application/rtf",
            b"{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Arial;}} Policy body}",
        ),
    ],
)
def test_document_formats_are_recognised_by_content_signature(
    name: str, media_type: str, payload: bytes
) -> None:
    prepared = prepare_upload(
        MemoryProvider(),
        scope=TenantScope(uuid4()),
        policy=_document_policy(),
        original_filename=name,
        declared_media_type=media_type,
        chunks=(payload,),
    )
    assert prepared.detected_media_type == media_type


def test_an_mp4_bmff_brand_declared_as_heic_is_rejected() -> None:
    with pytest.raises(UnsafeFile, match="no supported signature"):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=_image_policy(),
            original_filename="clip.heic",
            declared_media_type="image/heic",
            chunks=(_bmff_payload(b"isom"),),
        )


def test_an_rtf_declared_as_plain_text_is_rejected() -> None:
    with pytest.raises(UnsafeFile, match="does not match"):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=_document_policy(),
            original_filename="notes.txt",
            declared_media_type="text/plain",
            chunks=(b"{\\rtf1\\ansi disguised rich text}",),
        )


def test_a_docx_declared_as_a_plain_zip_is_rejected() -> None:
    with pytest.raises(UnsafeFile, match="does not match"):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=_document_policy(),
            original_filename="bundle.zip",
            declared_media_type="application/zip",
            chunks=(_docx_payload(),),
        )


def test_upload_stops_at_the_configured_size_limit() -> None:
    policy = FilePolicy(
        max_bytes=4,
        allowed_extensions=frozenset({".pdf"}),
        allowed_media_types=frozenset({"application/pdf"}),
    )
    with pytest.raises(UnsafeFile, match="exceeds"):
        prepare_upload(
            MemoryProvider(),
            scope=TenantScope(uuid4()),
            policy=policy,
            original_filename="x.pdf",
            declared_media_type="application/pdf",
            chunks=(b"%PDF", b"more-data-that-must-not-be-accepted"),
        )


def test_signature_detection_does_not_read_the_entire_file_back_into_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission may spool to disk but must not defeat that by `read()`ing all."""
    import tempfile

    original = tempfile.SpooledTemporaryFile

    class BoundedReadSpool:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._wrapped = original(*args, **kwargs)

        def __enter__(self) -> BoundedReadSpool:
            return self

        def __exit__(self, *args: object) -> None:
            self._wrapped.close()

        def write(self, payload: bytes) -> int:
            return self._wrapped.write(payload)

        def seek(self, offset: int, whence: int = 0) -> int:
            return self._wrapped.seek(offset, whence)

        def tell(self) -> int:
            return self._wrapped.tell()

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                raise AssertionError("admission attempted an unbounded read")
            return self._wrapped.read(size)

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", BoundedReadSpool)

    class ChunkedProvider(MemoryProvider):
        def put(
            self,
            key: str,
            content: IO[bytes],
            *,
            content_type: str,
            size_bytes: int,
            checksum_sha256: str,
        ) -> None:
            del content_type, checksum_sha256
            received = bytearray()
            while chunk := content.read(8):
                received.extend(chunk)
            assert len(received) == size_bytes
            self.objects[key] = bytes(received)

    prepare_upload(
        ChunkedProvider(),
        scope=TenantScope(uuid4()),
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\n", b"body" * 100),
    )


def test_database_stage_and_deletion_lifecycle_are_caller_transactional(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )
    stored = stage_file(db, prepared=prepared)
    assert db.in_transaction()
    assert stored.state == FileState.AVAILABLE

    request_deletion(db, scope=scope, file_id=stored.id, now=datetime.now(UTC))
    assert stored.state == FileState.DELETION_PENDING
    target = deletion_target(db, scope=scope, file_id=stored.id)
    delete_object(provider, target=target)
    finalize_purge(db, target=target, now=datetime.now(UTC))
    assert stored.state == FileState.PURGED
    assert prepared.storage_key not in provider.objects


def test_platform_database_lifecycle_uses_the_same_engine(db: Session) -> None:
    scope = PlatformScope()
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="licence-bundle.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nplatform",),
    )
    stored = stage_file(db, prepared=prepared)

    from dotmac_files.models import PlatformStoredFile

    assert isinstance(stored, PlatformStoredFile)
    assert "tenant_id" not in stored.__table__.c
    target = download_target(db, scope=scope, file_id=stored.id)
    assert target.scope == scope
    assert open_object(provider, target=target).read() == b"%PDF-1.7\nplatform"

    request_deletion(db, scope=scope, file_id=stored.id, now=datetime.now(UTC))
    target = deletion_target(db, scope=scope, file_id=stored.id)
    delete_object(provider, target=target)
    finalized = finalize_purge(db, target=target, now=datetime.now(UTC))
    assert finalized.state == FileState.PURGED


def test_download_stream_opens_after_the_database_phase_has_ended(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )
    stage_file(db, prepared=prepared)
    target = download_target(db, scope=scope, file_id=prepared.id)
    db.rollback()

    stream = open_object(provider, target=target)
    try:
        assert stream.read() == b"%PDF-1.7\nbody"
    finally:
        stream.close()


def test_reconciler_records_a_missing_object_without_deleting_metadata(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )
    stored = stage_file(db, prepared=prepared)
    target = reconciliation_target(db, scope=scope, file_id=stored.id)
    provider.delete(prepared.storage_key)

    exists = observe_object(provider, target=target)
    record_presence(
        db,
        target=target,
        exists=exists,
        now=datetime.now(UTC),
    )
    assert stored.state == FileState.MISSING
    assert stored.missing_observed_at is not None


def test_staging_the_same_prepared_file_is_idempotent_and_conflicts_on_drift(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    prepared = prepare_upload(
        MemoryProvider(),
        scope=scope,
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )
    first = stage_file(db, prepared=prepared)
    replay = stage_file(db, prepared=prepared)
    assert replay.id == first.id

    with pytest.raises(PreparedFileConflict):
        stage_file(db, prepared=replace(prepared, original_filename="other.pdf"))

    request_deletion(db, scope=scope, file_id=first.id, now=datetime.now(UTC))
    with pytest.raises(PreparedFileConflict, match="lifecycle"):
        stage_file(db, prepared=prepared)


def test_a_storage_key_collision_raises_without_discarding_the_outer_transaction(
    db: Session,
) -> None:
    """Exercises `conflict_savepoint` reached through its new engine-free
    import (`dotmac_kernel.transactions`, not `dotmac_kernel.db`) on the real
    `stage_file` IntegrityError path -- distinct from the idempotency test
    above, which never reaches that path: there, `_find_prepared`'s id lookup
    finds the existing row first and `_require_same_prepared` raises before
    `conflict_savepoint` is ever entered.

    Here two prepared files carry DIFFERENT ids (so `_find_prepared`'s id
    lookup misses both times) but the same tenant, provider and storage key,
    which collides on the DB-level `uq_stored_files_tenant_provider_key`
    unique constraint instead -- a genuine `IntegrityError` raised inside
    `conflict_savepoint`'s `with db.begin_nested()` block.

    `conflict_savepoint` isolates that failure to a SAVEPOINT rather than the
    caller's outer transaction (see its docstring in
    `dotmac_kernel/_transactions.py`). If the import instead reached a
    variant that called a bare `db.rollback()`, the outer transaction --
    including `first`, staged earlier in this same session -- would be
    discarded too. Asserting `first` is still visible and the session is
    still mid-transaction after the collision proves the SAVEPOINT held.
    """
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    first_prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="first.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nfirst",),
    )
    first = stage_file(db, prepared=first_prepared)

    second_prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="second.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nsecond",),
    )
    assert second_prepared.id != first_prepared.id
    colliding = replace(second_prepared, storage_key=first_prepared.storage_key)

    with pytest.raises(
        PreparedFileConflict, match="conflicts outside its declared scope"
    ):
        stage_file(db, prepared=colliding)

    assert db.in_transaction()
    assert db.get(type(first), first.id) is not None


def test_orphan_reaper_deletes_only_old_unreferenced_keys_in_the_tenant_prefix(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_policy(),
        original_filename="invoice.pdf",
        declared_media_type="application/pdf",
        chunks=(b"%PDF-1.7\nbody",),
    )
    stage_file(db, prepared=prepared)
    orphan = f"tenants/{scope.tenant_id}/files/{uuid4()}"
    provider.objects[orphan] = b"orphan"

    observations = list_objects(provider, scope=scope)
    orphan_keys = find_orphan_keys(
        db,
        scope=scope,
        provider_code=provider.code,
        observations=observations,
        older_than=datetime.max.replace(tzinfo=UTC),
    )
    delete_orphans(provider, scope=scope, keys=orphan_keys)
    assert orphan_keys == (orphan,)
    assert prepared.storage_key in provider.objects


def test_reviewed_orphan_recheck_never_deletes_referenced_fresh_or_absent_key() -> None:
    scope = TenantScope(uuid4())
    key = f"tenants/{scope.tenant_id}/files/{uuid4()}"
    provider = MemoryProvider()
    provider.objects[key] = b"retained"
    reads: list[str] = []

    def referenced(candidate: str) -> bool:
        reads.append(candidate)
        return True

    result = recheck_and_delete_orphan(
        provider,
        scope=scope,
        key=key,
        expected_provider_code=provider.code,
        older_than=datetime.max.replace(tzinfo=UTC),
        is_referenced=referenced,
    )
    assert result.outcome == "referenced"
    assert reads == [key]
    assert key in provider.objects

    result = recheck_and_delete_orphan(
        provider,
        scope=scope,
        key=key,
        expected_provider_code=provider.code,
        older_than=datetime.min.replace(tzinfo=UTC),
        is_referenced=lambda _: False,
    )
    assert result.outcome == "too_new"
    assert result.observed_last_modified is not None
    assert key in provider.objects

    provider.objects.clear()
    result = recheck_and_delete_orphan(
        provider,
        scope=scope,
        key=key,
        expected_provider_code=provider.code,
        older_than=datetime.max.replace(tzinfo=UTC),
        is_referenced=lambda _: False,
    )
    assert result.outcome == "absent"


def test_reviewed_orphan_recheck_deletes_only_exact_old_key_after_reference_read() -> (
    None
):
    scope = TenantScope(uuid4())
    key = f"tenants/{scope.tenant_id}/files/{uuid4()}"
    lookalike = f"{key}-still-referenced"
    provider = MemoryProvider()
    provider.objects[key] = b"orphan"
    provider.objects[lookalike] = b"keep"
    events: list[str] = []

    def reference_read(candidate: str) -> bool:
        assert candidate == key
        events.append("reference-read-finished")
        return False

    original_delete = provider.delete

    def delete(candidate: str) -> None:
        assert events == ["reference-read-finished"]
        original_delete(candidate)

    provider.delete = delete  # type: ignore[method-assign]
    result = recheck_and_delete_orphan(
        provider,
        scope=scope,
        key=key,
        expected_provider_code=provider.code,
        older_than=datetime.max.replace(tzinfo=UTC),
        is_referenced=reference_read,
    )
    assert result.outcome == "deleted"
    assert result.observed_last_modified is not None
    assert key not in provider.objects
    assert lookalike in provider.objects


def test_reviewed_orphan_recheck_refuses_wrong_provider_or_scope() -> None:
    from dotmac_files import ProviderMismatch

    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    key = f"tenants/{scope.tenant_id}/files/{uuid4()}"
    provider.objects[key] = b"retain"

    with pytest.raises(ProviderMismatch):
        recheck_and_delete_orphan(
            provider,
            scope=scope,
            key=key,
            expected_provider_code="other",
            older_than=datetime.max.replace(tzinfo=UTC),
            is_referenced=lambda _: False,
        )
    with pytest.raises(StorageBoundaryViolation):
        recheck_and_delete_orphan(
            provider,
            scope=scope,
            key=f"tenants/{uuid4()}/files/{uuid4()}",
            expected_provider_code=provider.code,
            older_than=datetime.max.replace(tzinfo=UTC),
            is_referenced=lambda _: False,
        )
    assert key in provider.objects


def test_orphan_reaper_fails_closed_on_a_provider_key_outside_the_tenant_prefix(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    foreign_key = f"tenants/{uuid4()}/files/{uuid4()}"

    class MisbehavingProvider(MemoryProvider):
        def list(self, prefix: str) -> tuple[ObjectInfo, ...]:
            del prefix
            return (
                ObjectInfo(
                    key=foreign_key,
                    size_bytes=1,
                    last_modified=datetime.min.replace(tzinfo=UTC),
                ),
            )

    provider = MisbehavingProvider()
    provider.objects[foreign_key] = b"x"
    with pytest.raises(StorageBoundaryViolation):
        list_objects(provider, scope=scope)
    assert foreign_key in provider.objects


def test_platform_orphan_reaper_cannot_cross_into_a_tenant_prefix(db: Session) -> None:
    scope = PlatformScope()
    foreign_key = f"tenants/{uuid4()}/files/{uuid4()}"

    class MisbehavingProvider(MemoryProvider):
        def list(self, prefix: str) -> tuple[ObjectInfo, ...]:
            del prefix
            return (
                ObjectInfo(
                    key=foreign_key,
                    size_bytes=1,
                    last_modified=datetime.min.replace(tzinfo=UTC),
                ),
            )

    provider = MisbehavingProvider()
    provider.objects[foreign_key] = b"x"
    with pytest.raises(StorageBoundaryViolation):
        list_objects(provider, scope=scope)
    assert foreign_key in provider.objects


# --- Scoped verified read ----------------------------------------------------

_ALLOWED = frozenset({"image/png"})
_PNG = b"\x89PNG\r\n\x1a\n" + b"verified-read-body" * 4


def _verified_policy() -> FilePolicy:
    return FilePolicy(
        max_bytes=1_048_576,
        allowed_extensions=frozenset({".png", ".pdf"}),
        allowed_media_types=frozenset({"image/png", "application/pdf"}),
    )


def _stage_png(db: Session, provider: MemoryProvider, scope, payload: bytes = _PNG):
    prepared = prepare_upload(
        provider,
        scope=scope,
        policy=_verified_policy(),
        original_filename="logo.png",
        declared_media_type="image/png",
        chunks=(payload,),
    )
    stage_file(db, prepared=prepared)
    return prepared


def test_verified_read_returns_checked_bytes_within_the_same_scope(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=str(prepared.id))
    db.rollback()

    result = read_verified(
        provider,
        scope=scope,
        target=target,
        limit=len(_PNG),
        allowed_media_types=_ALLOWED,
    )
    assert result.content == _PNG
    assert result.media_type == "image/png"
    assert result.size_bytes == len(_PNG)
    assert result.checksum_sha256 == prepared.checksum_sha256
    assert result.id == prepared.id and result.scope == scope


def test_verified_read_works_on_the_platform_plane(db: Session) -> None:
    scope = PlatformScope()
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    assert (
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        ).content
        == _PNG
    )


def test_verified_read_refuses_a_foreign_scope_file(db: Session) -> None:
    owner, other = TenantScope(uuid4()), TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, owner)
    target = verified_read_target(db, scope=owner, file_ref=prepared.id)

    # Database phase: another tenant cannot even load the id.
    with pytest.raises(StoredFileNotFound):
        verified_read_target(db, scope=other, file_ref=prepared.id)
    with pytest.raises(StoredFileNotFound):
        verified_read_target(db, scope=PlatformScope(), file_ref=prepared.id)
    # Provider phase: a target presented under a different scope is refused.
    for wrong in (other, PlatformScope()):
        with pytest.raises(ForeignScopeFile):
            read_verified(
                provider,
                scope=wrong,
                target=target,
                limit=1_000,
                allowed_media_types=_ALLOWED,
            )
    # A forged key (right scope, someone else's object) is refused before any read.
    forged = replace(target, storage_key=f"tenants/{other.tenant_id}/files/{target.id}")
    with pytest.raises(ForeignScopeFile):
        read_verified(
            provider,
            scope=owner,
            target=forged,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )
    traversal = replace(target, storage_key=f"{target.storage_key}/../{uuid4()}")
    with pytest.raises(ForeignScopeFile):
        read_verified(
            provider,
            scope=owner,
            target=traversal,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )


@pytest.mark.parametrize(
    "ref",
    [
        "../etc/passwd",
        "/etc/passwd",
        "tenants/x/files/abc",
        "C:\\windows\\win.ini",
        "file:///etc/hostname",
        "https://resource.invalid/x",
        "urn:uuid:12345678-1234-5678-1234-567812345678",
        "{12345678-1234-5678-1234-567812345678}",
        "12345678123456781234567812345678",
        "12345678-1234-5678-1234-567812345678/..",
        "12345678-1234-5678-1234-567812345678\n",
        "12345678-1234-5678-1234-56781234567",
        "",
        "not-a-uuid",
        "\x00",
    ],
)
def test_non_uuid_and_path_like_references_are_refused_before_any_query(
    ref: str, db: Session
) -> None:
    with pytest.raises(InvalidFileRef) as raised:
        verified_read_target(db, scope=TenantScope(uuid4()), file_ref=ref)
    assert ref.strip() == "" or ref not in str(raised.value)
    assert not db.in_transaction()


@pytest.mark.parametrize(
    "ref", [None, 7, b"12345678-1234-5678-1234-567812345678", object()]
)
def test_non_text_references_are_refused(ref) -> None:
    with pytest.raises(InvalidFileRef):
        parse_file_ref(ref)


def test_canonical_uppercase_uuid_text_is_accepted_and_normalised() -> None:
    identity = uuid4()
    assert parse_file_ref(str(identity).upper()) == identity
    assert parse_file_ref(identity) == identity


class CountingStream(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.requested: list[int] = []
        self.closed_by_reader = False

    def read(self, size: int | None = -1) -> bytes:
        self.requested.append(-1 if size is None else size)
        return super().read(size)

    def close(self) -> None:
        self.closed_by_reader = True
        super().close()


class StreamingProvider(MemoryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.streams: list[CountingStream] = []

    def open(self, key: str) -> CountingStream:  # type: ignore[override]
        stream = CountingStream(self.objects[key])
        self.streams.append(stream)
        return stream


def test_verified_read_requests_at_most_limit_plus_one_bytes(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = StreamingProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)

    # The row under-reports its size: the object is bigger than the limit.
    provider.objects[prepared.storage_key] = _PNG + b"x" * 5_000
    small = replace(target, size_bytes=len(_PNG))
    with pytest.raises(ReadLimitExceeded):
        read_verified(
            provider,
            scope=scope,
            target=small,
            limit=len(_PNG),
            allowed_media_types=_ALLOWED,
        )
    stream = provider.streams[-1]
    assert stream.closed_by_reader
    assert sum(stream.requested) <= len(_PNG) + 1
    assert all(size > 0 for size in stream.requested)


def test_verified_read_refuses_by_recorded_size_without_opening_the_object(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = StreamingProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    with pytest.raises(ReadLimitExceeded):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=len(_PNG) - 1,
            allowed_media_types=_ALLOWED,
        )
    assert provider.streams == []


def test_verified_read_accepts_a_file_exactly_at_the_limit(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    assert read_verified(
        provider,
        scope=scope,
        target=target,
        limit=len(_PNG),
        allowed_media_types=_ALLOWED,
    ).content


def test_verified_read_refuses_bytes_that_differ_from_the_row(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)

    same_length = _PNG[:-1] + b"Z"
    provider.objects[prepared.storage_key] = same_length
    with pytest.raises(IntegrityMismatch, match="sha256"):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )

    provider.objects[prepared.storage_key] = _PNG + b"extra"
    with pytest.raises(IntegrityMismatch, match="length"):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )

    provider.objects[prepared.storage_key] = _PNG
    bad_checksum = replace(target, checksum_sha256="sha256:" + "0" * 64)
    with pytest.raises(IntegrityMismatch, match="sha256"):
        read_verified(
            provider,
            scope=scope,
            target=bad_checksum,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )
    malformed = replace(target, checksum_sha256="md5:abc")
    with pytest.raises(IntegrityMismatch):
        read_verified(
            provider,
            scope=scope,
            target=malformed,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )


def test_verified_read_checks_the_content_detected_type(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)

    # The row claims a PDF but the (checksum-consistent) bytes are a PNG.
    lying = replace(target, detected_media_type="application/pdf")
    with pytest.raises(IntegrityMismatch, match="type"):
        read_verified(
            provider,
            scope=scope,
            target=lying,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )

    # Bytes and checksum replaced together by something with no signature.
    opaque = b"\x00\x01\x02\x03" * 8
    provider.objects[prepared.storage_key] = opaque
    forged = replace(
        target,
        size_bytes=len(opaque),
        checksum_sha256=f"sha256:{hashlib.sha256(opaque).hexdigest()}",
    )
    with pytest.raises(IntegrityMismatch, match="signature"):
        read_verified(
            provider,
            scope=scope,
            target=forged,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )


def test_verified_read_enforces_an_allowed_media_type_set(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    assert (
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=1_000,
            allowed_media_types=frozenset({"image/png", "image/jpeg"}),
        ).media_type
        == "image/png"
    )
    with pytest.raises(ReadTypeNotAllowed):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=1_000,
            allowed_media_types=frozenset({"application/pdf"}),
        )


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10", None, 64 * 1024 * 1024 + 1])
def test_verified_read_refuses_an_invalid_limit(limit, db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    with pytest.raises(ValueError, match="limit"):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=limit,
            allowed_media_types=_ALLOWED,
        )


def test_verified_read_refuses_files_that_are_not_available(db: Session) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    request_deletion(db, scope=scope, file_id=prepared.id, now=datetime.now(UTC))
    with pytest.raises(InvalidFileState):
        verified_read_target(db, scope=scope, file_ref=prepared.id)
    pending = replace(target, state=FileState.DELETION_PENDING)
    with pytest.raises(InvalidFileState):
        read_verified(
            provider,
            scope=scope,
            target=pending,
            limit=1_000,
            allowed_media_types=_ALLOWED,
        )


def test_verified_read_refuses_a_provider_other_than_the_one_that_stores_it(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    other = MemoryProvider()
    other.code = "other"
    with pytest.raises(ProviderMismatch):
        read_verified(
            other, scope=scope, target=target, limit=1_000, allowed_media_types=_ALLOWED
        )


def test_allowed_media_types_is_required_and_must_be_a_non_empty_frozenset(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = MemoryProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    with pytest.raises(TypeError):
        read_verified(provider, scope=scope, target=target, limit=1_000)  # type: ignore[call-arg]
    for bad in (None, frozenset(), {"image/png"}, ["image/png"], "image/png"):
        with pytest.raises(ValueError, match="allowed_media_types"):
            read_verified(
                provider,
                scope=scope,
                target=target,
                limit=1_000,
                allowed_media_types=bad,  # type: ignore[arg-type]
            )


class OverReadingStream(io.BytesIO):
    """Ignores the requested size and appends extra bytes."""

    def read(self, size: int | None = -1) -> bytes:
        return super().read() + b"\x00\x00"


class OverReadingProvider(MemoryProvider):
    def open(self, key: str) -> OverReadingStream:  # type: ignore[override]
        return OverReadingStream(self.objects[key])


def test_verified_read_refuses_a_provider_that_returns_more_than_requested(
    db: Session,
) -> None:
    scope = TenantScope(uuid4())
    provider = OverReadingProvider()
    prepared = _stage_png(db, provider, scope)
    target = verified_read_target(db, scope=scope, file_ref=prepared.id)
    with pytest.raises(IntegrityMismatch, match="more bytes"):
        read_verified(
            provider,
            scope=scope,
            target=target,
            limit=len(_PNG),
            allowed_media_types=_ALLOWED,
        )
