"""Strict inputs for source cohort writes; product context supplies the tenant."""

from uuid import UUID

from .participation_contracts import ParticipationCommand


class CreateCohortCommand(ParticipationCommand):
    name: str
    discipline: str


class RemoveFacilitatorCommand(ParticipationCommand):
    cohort_id: UUID
    person_id: UUID
