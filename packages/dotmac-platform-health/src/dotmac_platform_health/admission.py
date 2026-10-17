"""Owner admission values, with no transport or evidence authentication implementation.

Only trusted service composition supplies the authority and authenticated source.
Neither value is a request-document schema. An accepting production authority is
intentionally absent: independently measured runtime and credential assignment
must be supplied by the Control owner, never inferred from self-reported labels.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from dotmac_platform_health.contracts import HealthObservationInput
    from dotmac_platform_health.models import HealthGrant, HealthGrantClosure
    from dotmac_platform_health.service import ObservationReceipt


class HealthAdmissionError(ValueError):
    """No authoritative enrollment permits this operation."""


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise HealthAdmissionError("bounded nonempty authority references required")


@dataclass(frozen=True, slots=True)
class AuthenticatedHealthSource:
    """Immutable result from the existing transport authentication owner."""

    installation_ref: str
    source_ref: str
    account_ref: str
    transport_ref: str
    credential_ref: str
    configuration_ref: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            _text(value)


@dataclass(frozen=True, slots=True)
class EnrollmentProjection:
    """Control's immutable public projection; no deployment coordinates on wire."""

    enrollment_ref: str
    installation_ref: str
    configuration_ref: str
    candidate_ref: str
    source_ref: str
    account_ref: str
    transport_ref: str
    credential_ref: str
    component_codes: tuple[str, ...]
    runtime_evidence_ref: str
    runtime_authority_ref: str
    credential_evidence_ref: str
    credential_authority_ref: str
    eligible_from: datetime
    eligible_until: datetime
    standing: str

    def __post_init__(self) -> None:
        if self.standing != "eligible":
            raise HealthAdmissionError(
                "authoritative enrollment standing is ineligible"
            )
        for name, value in asdict(self).items():
            if name not in {"component_codes", "eligible_from", "eligible_until"}:
                _text(value)
        if (
            not isinstance(self.component_codes, tuple)
            or not 1 <= len(self.component_codes) <= 100
            or len(set(self.component_codes)) != len(self.component_codes)
        ):
            raise HealthAdmissionError("exact unique bounded component roster required")
        for code in self.component_codes:
            _text(code)
            if len(code) > 120:
                raise HealthAdmissionError("component code exceeds bound")
        if (
            self.eligible_from.utcoffset() is None
            or self.eligible_until.utcoffset() is None
            or self.eligible_from >= self.eligible_until
        ):
            raise HealthAdmissionError("bounded aware eligibility interval required")

    def document(self) -> dict[str, object]:
        document = asdict(self)
        document["component_codes"] = sorted(self.component_codes)
        document["eligible_from"] = self.eligible_from.isoformat()
        document["eligible_until"] = self.eligible_until.isoformat()
        return document

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.document(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class EnrollmentAuthority(Protocol):
    """Required startup authority; current standing must refuse when unresolved.

    Implementations must authenticate Control evidence and independently measured
    runtime/credential assignment, including candidate/configuration joins. PH
    does not perform or fake that verification. Revoked/ineligible standing must
    raise HealthAdmissionError, not return a formerly eligible projection.
    """

    def require_ready(self) -> None: ...

    def enrollment(self, enrollment_ref: str) -> EnrollmentProjection: ...


class HealthAdmission:
    """Startup-injected enrollment authority. Services remain flush-only."""

    def __init__(self, authority: EnrollmentAuthority | None) -> None:
        if authority is None:
            raise HealthAdmissionError("an enrollment authority is required at startup")
        authority.require_ready()
        self._authority = authority

    def current(self, enrollment_ref: str, *, at: datetime) -> EnrollmentProjection:
        if at.utcoffset() is None:
            raise HealthAdmissionError("authority evaluation time must be aware")
        self._authority.require_ready()
        projection = self._authority.enrollment(enrollment_ref)
        if (
            not isinstance(projection, EnrollmentProjection)
            or projection.enrollment_ref != enrollment_ref
        ):
            raise HealthAdmissionError("authority returned contradictory enrollment")
        if not projection.eligible_from <= at < projection.eligible_until:
            raise HealthAdmissionError("enrollment is not currently eligible")
        return projection

    def create_grant(
        self, db: Session, *, enrollment_ref: str, at: datetime
    ) -> HealthGrant:
        from dotmac_platform_health.grants import create_grant

        return create_grant(db, self, enrollment_ref=enrollment_ref, at=at)

    def close_grant(
        self, db: Session, *, grant_id: UUID, closed_at: datetime, reason: str
    ) -> HealthGrantClosure:
        from dotmac_platform_health.grants import close_grant

        return close_grant(db, grant_id=grant_id, closed_at=closed_at, reason=reason)

    def record(
        self,
        db: Session,
        *,
        grant_id: UUID,
        source: AuthenticatedHealthSource,
        command: HealthObservationInput,
        at: datetime,
    ) -> ObservationReceipt:
        from dotmac_platform_health.grants import record_authenticated_observation

        return record_authenticated_observation(
            db, self, grant_id=grant_id, source=source, command=command, at=at
        )
