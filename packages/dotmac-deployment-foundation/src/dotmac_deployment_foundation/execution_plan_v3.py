"""Execution plan binding the installed Foundation and one enrolled host.

The document schema is V3; its digest is still ``ExecutionPlanDigestV1``.
Control signs that value and does not parse or re-canonicalize this document.
Fleet owns the opaque host-id grammar.  Foundation only compares the exact
Control/Fleet-resolved identity and immutable enrolment coordinate.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Final
from uuid import UUID

from .canonical_plan import canonical_plan_bytes
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .execution_plan import EXECUTION_PLAN_DIGEST_SCHEMA
from .execution_plan_v2 import FoundationExecutionPlanV2
from .provenance import normalize_digest
from .secrets_guard import require_no_secrets

__all__ = [
    "EXECUTION_PLAN_V3_SCHEMA",
    "FoundationExecutionPlanV3",
    "canonical_execution_plan_v3_bytes",
    "render_execution_plan_v3",
    "require_execution_plan_v3_digest",
]

EXECUTION_PLAN_V3_SCHEMA: Final = "FoundationExecutionPlanV3"


@dataclasses.dataclass(frozen=True, slots=True)
class FoundationExecutionPlanV3:
    """V2's exact acts, plus the authority subject of their execution."""

    base: FoundationExecutionPlanV2
    candidate_wheel_digest: str
    target_id: str
    controller_ssh_fingerprint: str
    host_id: str
    host_incarnation: str
    host_enrolment_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.base, FoundationExecutionPlanV2):
            raise SpecError("FoundationExecutionPlanV3.base must be a V2 plan")
        normalize_digest(self.candidate_wheel_digest, where="candidate_wheel_digest")
        for name in (
            "target_id",
            "controller_ssh_fingerprint",
            "host_id",
            "host_incarnation",
            "host_enrolment_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SpecError(f"FoundationExecutionPlanV3.{name} is empty")
        try:
            canonical_enrolment = str(UUID(self.host_enrolment_ref))
        except ValueError as exc:
            raise SpecError(
                "FoundationExecutionPlanV3.host_enrolment_ref must be a UUID"
            ) from exc
        if canonical_enrolment != self.host_enrolment_ref:
            raise SpecError(
                "FoundationExecutionPlanV3.host_enrolment_ref must be canonical"
            )

    @property
    def operation(self) -> str:
        return self.base.operation

    @property
    def target(self) -> str:
        return self.base.target

    @property
    def descriptor_digest(self) -> str:
        return self.base.descriptor_digest

    @property
    def host_prestate(self):
        return self.base.host_prestate

    @property
    def principal_bootstraps(self):
        return self.base.principal_bootstraps

    @property
    def exposure_reconciliations(self):
        return self.base.exposure_reconciliations

    def __getattr__(self, name: str) -> Any:
        """Expose unchanged V2 act fields as read-only successor projections.

        Authority still hashes ``base.as_document()`` inside the V3 document;
        this delegates only named dataclass fields, never an arbitrary method
        or a second source of plan facts.
        """
        if name in {
            field.name for field in dataclasses.fields(FoundationExecutionPlanV2)
        }:
            return getattr(self.base, name)
        raise AttributeError(name)

    def as_document(self) -> dict[str, Any]:
        return {
            **self.base.as_document(),
            "schema": EXECUTION_PLAN_V3_SCHEMA,
            "candidate_wheel_digest": normalize_digest(
                self.candidate_wheel_digest, where="candidate_wheel_digest"
            ),
            "target_id": self.target_id,
            "controller_ssh_fingerprint": self.controller_ssh_fingerprint,
            "host_id": self.host_id,
            "host_incarnation": self.host_incarnation,
            "host_enrolment_ref": self.host_enrolment_ref,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_execution_plan_v3_bytes(self.as_document())

    def digest(self) -> str:
        """Return the existing ``ExecutionPlanDigestV1`` value schema."""
        return str(Digest.of(self.canonical_bytes()))


def canonical_execution_plan_v3_bytes(document: Any) -> bytes:
    return canonical_plan_bytes(
        document, schema=EXECUTION_PLAN_V3_SCHEMA, path="execution_plan_v3"
    )


def render_execution_plan_v3(
    base: FoundationExecutionPlanV2,
    *,
    candidate_wheel_digest: str,
    target_id: str,
    controller_ssh_fingerprint: str,
    host_id: str,
    host_incarnation: str,
    host_enrolment_ref: str,
) -> FoundationExecutionPlanV3:
    plan = FoundationExecutionPlanV3(
        base=base,
        candidate_wheel_digest=candidate_wheel_digest,
        target_id=target_id,
        controller_ssh_fingerprint=controller_ssh_fingerprint,
        host_id=host_id,
        host_incarnation=host_incarnation,
        host_enrolment_ref=host_enrolment_ref,
    )
    require_no_secrets(plan.as_document(), source="execution plan v3")
    return plan


def require_execution_plan_v3_digest(
    plan: FoundationExecutionPlanV3, *, authorized: str
) -> str:
    if not isinstance(plan, FoundationExecutionPlanV3):
        raise PreconditionFailed(
            "execution authority requires FoundationExecutionPlanV3; "
            "V1/V2 documents are historical and non-authorizing"
        )
    actual = plan.digest()
    if actual != normalize_digest(authorized, where="authorized execution plan"):
        raise PreconditionFailed(
            f"the authorized {EXECUTION_PLAN_DIGEST_SCHEMA} disagrees with "
            "the V3 plan in hand"
        )
    return actual
