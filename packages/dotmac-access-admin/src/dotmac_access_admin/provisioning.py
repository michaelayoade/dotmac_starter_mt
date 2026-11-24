"""Product ports for `CredentialLifecycle.provision`, bound to the caller's
transaction.

Provision-only. The engine's other operations (verify, reset, force-reset) are
not this module's, so every port method they would need FAILS CLOSED rather than
approximating. Nothing here ever sees, stores, logs or returns password material:
the engine generates it, hashes it and discards it; the store only receives the
hash, and the audit/recovery ports receive references and counters.

The Starter `user_credentials` table has no reset flag or version column. The
stored hash is of material nobody was told, so the credential cannot be presented
until the subject recovers through the intent enqueued below; the snapshot the
store returns reports `reset_required=True` and version 0 accordingly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.credential_lifecycle import (
    CredentialAuditEntryV1,
    CredentialLifecycle,
    CredentialRecoveryIntentV1,
    CredentialSessionRevocationIntentV1,
    CredentialSnapshot,
    CredentialSubjectRef,
)
from dotmac_kernel.messaging.outbox import enqueue_event
from dotmac_kernel.models import UserCredential
from sqlalchemy import select
from sqlalchemy.orm import Session

APPLICATION = "dotmac_access_admin"
PRINCIPAL_KIND = "person"
RECOVERY_EVENT_TYPE = "access_admin.recovery_requested.v1"
PROVISIONED_AUDIT_ACTION = "access_admin.credential.provisioned"


class UnsupportedPortOperation(RuntimeError):
    """The provision-only adapter refuses everything outside provisioning."""


def _unsupported(name: str) -> UnsupportedPortOperation:
    return UnsupportedPortOperation(f"access-admin does not support {name}")


class _Store:
    def __init__(self, db: Session, tenant_id: UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    def load(self, subject: CredentialSubjectRef) -> CredentialSnapshot | None:
        row = self._db.scalars(
            select(UserCredential).where(
                UserCredential.tenant_id == self._tenant_id,
                UserCredential.id == UUID(subject.credential_ref),
            )
        ).first()
        if row is None:
            return None
        return CredentialSnapshot(
            subject=subject,
            password_hash=row.password_hash,
            credential_version=0,
            reset_required=True,
        )

    def lock_for_update(self, subject: CredentialSubjectRef) -> CredentialSnapshot:
        raise _unsupported("lock_for_update")

    def create(
        self,
        subject: CredentialSubjectRef,
        *,
        password_hash: str,
        reset_required: bool,
    ) -> CredentialSnapshot:
        if not reset_required:
            raise _unsupported("a standing (non reset-required) credential")
        self._db.add(
            UserCredential(
                id=UUID(subject.credential_ref),
                tenant_id=self._tenant_id,
                party_id=UUID(subject.principal_ref),
                password_hash=password_hash,
            )
        )
        self._db.flush()
        return CredentialSnapshot(
            subject=subject,
            password_hash=password_hash,
            credential_version=0,
            reset_required=True,
        )

    def write_hash(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("write_hash")


class _Sessions:
    def request_revocation(
        self, *args: object, **kwargs: object
    ) -> CredentialSessionRevocationIntentV1 | None:
        raise _unsupported("request_revocation")


class _Recovery:
    def __init__(self, db: Session, tenant_id: UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    def emit(self, intent: CredentialRecoveryIntentV1) -> str | None:
        """A durable, refs-only outbox row committed with the user."""
        event = enqueue_event(
            self._db,
            tenant_id=self._tenant_id,
            event_type=RECOVERY_EVENT_TYPE,
            payload={
                "party_id": intent.subject.principal_ref,
                "reason_code": intent.reason_code,
                "credential_version": intent.credential_version,
                "correlation_ref": intent.correlation_ref,
            },
            correlation_id=intent.correlation_ref,
        )
        return str(event.id)

    def consume(self, *args: object, **kwargs: object) -> bool:
        return False


class _Audit:
    def __init__(self, db: Session, tenant_id: UUID, actor_party_id: UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._actor = actor_party_id

    def record_event(self, entry: CredentialAuditEntryV1) -> None:
        write_audit_event(
            self._db,
            tenant_id=self._tenant_id,
            actor_party_id=self._actor,
            actor_type="user",
            actor_id=str(self._actor),
            action=PROVISIONED_AUDIT_ACTION,
            entity_type="credential",
            entity_id=entry.subject.credential_ref,
            details={
                "party_id": entry.subject.principal_ref,
                "reason_code": entry.reason_code,
                "credential_version": entry.credential_version,
                "correlation_ref": entry.correlation_ref,
            },
        )

    def record_receipt(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("record_receipt")

    def find_receipt(self, *args: object, **kwargs: object) -> None:
        raise _unsupported("find_receipt")


def build_lifecycle(
    db: Session,
    tenant_id: UUID,
    actor_party_id: UUID,
    *,
    clock: Callable[[], datetime] | None = None,
) -> CredentialLifecycle:
    return CredentialLifecycle(
        store=_Store(db, tenant_id),
        sessions=_Sessions(),
        recovery=_Recovery(db, tenant_id),
        audit=_Audit(db, tenant_id, actor_party_id),
        clock=clock if clock is not None else (lambda: datetime.now(UTC)),
    )
