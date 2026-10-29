"""Strict inputs for source cohort writes; product context supplies the tenant."""

from uuid import UUID

from .participation_contracts import ParticipationCommand


class CreateCohortCommand(ParticipationCommand):
    name: str
    discipline: str


class RemoveFacilitatorCommand(ParticipationCommand):
    cohort_id: UUID
    person_id: UUID


class CohortIntakePatch(ParticipationCommand):
    """Stage only selected nullable intake fields after trusted product checks.

    Omission preserves the stored value; explicit None clears it. Operator
    authorization, bank suitability and numeric policy belong to the product.
    The owner checks selected nonnull bank references within the tenant.
    """

    cohort_id: UUID
    entrance_bank_id: UUID | None = None
    entrance_time_limit_minutes: int | None = None
    auto_accept_threshold: float | None = None
