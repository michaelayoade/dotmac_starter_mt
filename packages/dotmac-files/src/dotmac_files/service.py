"""One metadata writer shared by explicit tenant and platform file planes.

Provider I/O lives in :mod:`dotmac_files.physical`, which is persistence-free.
This module performs only database phases: stage immutable metadata, prepare an
authorized object target, and record deletion or reconciliation outcomes.  The
required kernel ``Scope`` selects one of two structurally separate tables;
there is no nullable tenant and no sentinel value.

No function commits or rolls back. ``dotmac_kernel.db`` remains the one
transaction authority.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_files.contracts import (
    FileState,
    InvalidFileState,
    PreparedFile,
    PreparedFileConflict,
    StaleObjectRef,
    StoredFileNotFound,
    StoredObjectRef,
)
from dotmac_files.models import PlatformStoredFile, TenantStoredFile
from dotmac_files.physical import scope_prefix, validate_provider_code
from dotmac_files.providers import ObjectInfo, StorageBoundaryViolation

StoredFileRecord = TenantStoredFile | PlatformStoredFile


def stage_file(db: Session, *, prepared: PreparedFile) -> StoredFileRecord:
    """Idempotently stage metadata in the caller's existing transaction."""
    # Lazy by design. Importing `dotmac_kernel.db` constructs the configured
    # engines; pure file/provider contracts remain importable without a DB URL.
    from dotmac_kernel.db import conflict_savepoint

    existing = _find_prepared(db, prepared=prepared)
    if existing is not None:
        return _require_same_prepared(existing, prepared=prepared)

    stored = _new_stored_file(prepared)
    try:
        with conflict_savepoint(db):
            db.add(stored)
            db.flush()
    except IntegrityError as exc:
        existing = _find_prepared(db, prepared=prepared)
        if existing is None:
            raise PreparedFileConflict(
                f"file identity {prepared.id} conflicts outside its declared scope"
            ) from exc
        return _require_same_prepared(existing, prepared=prepared)
    return stored


def _new_stored_file(prepared: PreparedFile) -> StoredFileRecord:
    values = {
        "id": prepared.id,
        "provider_code": prepared.provider_code,
        "storage_key": prepared.storage_key,
        "original_filename": prepared.original_filename,
        "size_bytes": prepared.size_bytes,
        "declared_media_type": prepared.declared_media_type,
        "detected_media_type": prepared.detected_media_type,
        "checksum_sha256": prepared.checksum_sha256,
        "state": str(FileState.AVAILABLE),
        "missing_observed_at": None,
        "deletion_requested_at": None,
        "purged_at": None,
    }
    if isinstance(prepared.scope, TenantScope):
        return TenantStoredFile(tenant_id=prepared.scope.tenant_id, **values)
    if isinstance(prepared.scope, PlatformScope):
        return PlatformStoredFile(**values)
    raise TypeError(f"unsupported file scope {type(prepared.scope).__name__}")


def _find_prepared(db: Session, *, prepared: PreparedFile) -> StoredFileRecord | None:
    if isinstance(prepared.scope, TenantScope):
        return db.scalars(
            select(TenantStoredFile).where(
                TenantStoredFile.tenant_id == prepared.scope.tenant_id,
                TenantStoredFile.id == prepared.id,
            )
        ).one_or_none()
    if isinstance(prepared.scope, PlatformScope):
        return db.scalars(
            select(PlatformStoredFile).where(PlatformStoredFile.id == prepared.id)
        ).one_or_none()
    raise TypeError(f"unsupported file scope {type(prepared.scope).__name__}")


def _require_same_prepared(
    stored: StoredFileRecord, *, prepared: PreparedFile
) -> StoredFileRecord:
    immutable = (
        "provider_code",
        "storage_key",
        "original_filename",
        "size_bytes",
        "declared_media_type",
        "detected_media_type",
        "checksum_sha256",
    )
    mismatches = [
        name for name in immutable if getattr(stored, name) != getattr(prepared, name)
    ]
    if mismatches:
        raise PreparedFileConflict(
            f"file identity {prepared.id} was reused with different "
            f"immutable metadata: {', '.join(mismatches)}"
        )
    if stored.state != FileState.AVAILABLE:
        raise PreparedFileConflict(
            f"file identity {prepared.id} cannot be staged from lifecycle "
            f"state {stored.state!r}"
        )
    return stored


def _load(db: Session, *, scope: Scope, file_id: UUID, lock: bool) -> StoredFileRecord:
    stored: StoredFileRecord | None
    if isinstance(scope, TenantScope):
        tenant_query = select(TenantStoredFile).where(
            TenantStoredFile.tenant_id == scope.tenant_id,
            TenantStoredFile.id == file_id,
        )
        if lock:
            tenant_query = tenant_query.with_for_update()
        stored = db.scalars(tenant_query).one_or_none()
        scope_name = f"tenant {scope.tenant_id}"
    elif isinstance(scope, PlatformScope):
        platform_query = select(PlatformStoredFile).where(
            PlatformStoredFile.id == file_id
        )
        if lock:
            platform_query = platform_query.with_for_update()
        stored = db.scalars(platform_query).one_or_none()
        scope_name = "platform"
    else:
        raise TypeError(f"unsupported file scope {type(scope).__name__}")
    if stored is None:
        raise StoredFileNotFound(f"no file {file_id} in {scope_name} scope")
    return stored


def get_file(db: Session, *, scope: Scope, file_id: UUID) -> StoredFileRecord:
    """Return metadata from the explicitly named security plane."""
    return _load(db, scope=scope, file_id=file_id, lock=False)


def download_target(db: Session, *, scope: Scope, file_id: UUID) -> StoredObjectRef:
    """Read an immutable target to hand off after the DB transaction ends."""
    stored = get_file(db, scope=scope, file_id=file_id)
    if stored.state != FileState.AVAILABLE:
        raise InvalidFileState(f"file {file_id} is {stored.state}, not available")
    return _object_ref(stored, scope=scope)


def request_deletion(
    db: Session, *, scope: Scope, file_id: UUID, now: datetime
) -> StoredFileRecord:
    """Record deletion intent; physical deletion happens after this transaction."""
    stored = _load(db, scope=scope, file_id=file_id, lock=True)
    if stored.state == FileState.PURGED:
        return stored
    if stored.state != FileState.DELETION_PENDING:
        stored.state = str(FileState.DELETION_PENDING)
        stored.deletion_requested_at = now
        db.flush()
    return stored


def deletion_target(db: Session, *, scope: Scope, file_id: UUID) -> StoredObjectRef:
    """Read a committed deletion target for an external worker."""
    stored = _load(db, scope=scope, file_id=file_id, lock=False)
    if stored.state == FileState.PURGED:
        return _object_ref(stored, scope=scope)
    if stored.state != FileState.DELETION_PENDING:
        raise InvalidFileState(
            f"file {file_id} must be deletion_pending before physical purge"
        )
    return _object_ref(stored, scope=scope)


def finalize_purge(
    db: Session, *, target: StoredObjectRef, now: datetime
) -> StoredFileRecord:
    """Record a successful provider deletion in a new DB transaction."""
    stored = _load(db, scope=target.scope, file_id=target.id, lock=True)
    _require_current_target(stored, target=target)
    if stored.state == FileState.PURGED:
        return stored
    if stored.state != FileState.DELETION_PENDING:
        raise InvalidFileState(
            f"file {target.id} is no longer pending physical deletion"
        )
    stored.state = str(FileState.PURGED)
    stored.purged_at = now
    stored.missing_observed_at = None
    db.flush()
    return stored


def reconciliation_target(
    db: Session, *, scope: Scope, file_id: UUID
) -> StoredObjectRef:
    """Read a target whose presence may be observed out of transaction."""
    stored = _load(db, scope=scope, file_id=file_id, lock=False)
    current = FileState(stored.state)
    if current in {FileState.DELETION_PENDING, FileState.PURGED}:
        raise InvalidFileState(f"file {file_id} is {current}, not reconcilable")
    return _object_ref(stored, scope=scope)


def record_presence(
    db: Session, *, target: StoredObjectRef, exists: bool, now: datetime
) -> FileState:
    """Record an observation without overriding a concurrent deletion."""
    stored = _load(db, scope=target.scope, file_id=target.id, lock=True)
    _require_current_target(stored, target=target)
    current = FileState(stored.state)
    if current in {FileState.DELETION_PENDING, FileState.PURGED}:
        return current
    if exists:
        stored.state = str(FileState.AVAILABLE)
        stored.missing_observed_at = None
    else:
        stored.state = str(FileState.MISSING)
        if stored.missing_observed_at is None:
            stored.missing_observed_at = now
    db.flush()
    return FileState(stored.state)


def find_orphan_keys(
    db: Session,
    *,
    scope: Scope,
    provider_code: str,
    observations: tuple[ObjectInfo, ...],
    older_than: datetime,
) -> tuple[str, ...]:
    """Decide which observed keys are old and unreferenced; perform no I/O."""
    validate_provider_code(provider_code)
    prefix = scope_prefix(scope)
    if isinstance(scope, TenantScope):
        query = select(TenantStoredFile.storage_key).where(
            TenantStoredFile.tenant_id == scope.tenant_id,
            TenantStoredFile.provider_code == provider_code,
        )
    elif isinstance(scope, PlatformScope):
        query = select(PlatformStoredFile.storage_key).where(
            PlatformStoredFile.provider_code == provider_code
        )
    else:
        raise TypeError(f"unsupported file scope {type(scope).__name__}")
    known = set(db.scalars(query))
    orphans: list[str] = []
    for observed in observations:
        if not observed.key.startswith(prefix):
            raise StorageBoundaryViolation(
                f"provider {provider_code!r} returned an object outside "
                "the requested scope prefix"
            )
        if observed.key not in known and observed.last_modified < older_than:
            orphans.append(observed.key)
    return tuple(orphans)


def _object_ref(stored: StoredFileRecord, *, scope: Scope) -> StoredObjectRef:
    return StoredObjectRef(
        id=stored.id,
        scope=scope,
        provider_code=stored.provider_code,
        storage_key=stored.storage_key,
        state=FileState(stored.state),
        original_filename=stored.original_filename,
        size_bytes=stored.size_bytes,
        detected_media_type=stored.detected_media_type,
        checksum_sha256=stored.checksum_sha256,
    )


def _require_current_target(
    stored: StoredFileRecord, *, target: StoredObjectRef
) -> None:
    if (
        stored.provider_code != target.provider_code
        or stored.storage_key != target.storage_key
    ):
        raise StaleObjectRef(
            f"stored object identity changed after target {target.id} was prepared"
        )


__all__ = [
    "StoredFileRecord",
    "deletion_target",
    "download_target",
    "finalize_purge",
    "find_orphan_keys",
    "get_file",
    "reconciliation_target",
    "record_presence",
    "request_deletion",
    "stage_file",
]
