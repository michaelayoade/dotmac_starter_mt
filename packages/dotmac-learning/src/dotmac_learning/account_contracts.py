"""Trusted product account relations composed into Learning reads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select


@dataclass(frozen=True, slots=True)
class ActiveAccountSelection:
    """Tenant-bound, nonexecuting active-account UUID relation.

    The trusted in-process product account owner supplies a one-column,
    non-null UUID Select with its actual active-account predicate. This is not
    request SQL, authorization, identity provisioning or a cached population.
    Learning checks tenant scope before SQL and composes this relation into the
    enrollment statement on the caller Session. Identity/account correspondence
    and authoritative profile parity remain product adoption prerequisites.
    """

    tenant_id: UUID
    active_person_ids: Select[tuple[UUID]]
