"""Trusted product account relations composed into Learning reads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, Uuid


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


@dataclass(frozen=True, slots=True)
class AccountPresenceSelection:
    """Tenant-bound, nonexecuting account-presence UUID relation.

    The trusted product account owner supplies one non-null UUID column with
    its real profile/account-presence predicate. Inactive accounts may qualify.
    This is neither authorization nor ActiveAccountSelection, request SQL or a
    cached UUID population. Learning validates tenant scope before SQL and
    composes the relation into each email lookup on the caller Session. Public
    SQLAlchemy Select shape and UUID type are checked without executing SQL;
    the trusted product still owns the actual presence predicate.
    """

    tenant_id: UUID
    person_ids: Select[tuple[UUID]]

    def __post_init__(self) -> None:
        if not isinstance(self.person_ids, Select):
            raise TypeError("person_ids must be a SQLAlchemy Select")
        columns = tuple(self.person_ids.selected_columns)
        # selected_columns intentionally omits textual projections. The public
        # descriptions also cover those, preventing an extra raw SQL projection.
        if len(columns) != 1 or len(self.person_ids.column_descriptions) != 1:
            raise ValueError("person_ids must select exactly one column")
        if not isinstance(columns[0].type, Uuid):
            raise TypeError("person_ids must select a SQLAlchemy Uuid column")
