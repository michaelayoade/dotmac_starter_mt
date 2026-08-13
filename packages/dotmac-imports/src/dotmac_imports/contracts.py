"""Pure contracts for the bulk-import run ledger (ADR-0025).

Nothing here touches a database, a file, or a domain. The two ports at the
bottom are the whole extension surface: a domain says what a row must satisfy
(`RowValidator`) and what to do with a valid one (`RowApplier`), and the module
says nothing about either.

The split is not stylistic. A dry run is handed the validator and *never* the
applier — see `dotmac_imports.service` — so the "a dry run must not write"
property is a fact about which object exists on the call path, not a guard on a
branch that could be written backwards.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


class ImportsError(Exception):
    """Base for refusals made by the imports module.

    Deliberately not named `ImportError`: both source products defined a row
    error class with that name, shadowing the builtin that Python raises when a
    module fails to load, in files that also do conditional imports.
    """


class ImportRunNotFound(ImportsError):
    """No run with this id exists in the requested tenant scope."""


class InvalidRunState(ImportsError):
    """The requested transition is not valid from the run's current status."""


class PromotionRefused(ImportsError):
    """This validated run cannot be promoted into an apply run."""


class SourceMismatch(ImportsError):
    """An apply run's input is not the input its dry run validated."""


class MalformedSource(ImportsError):
    """The supplied bytes are not decodable as the declared layout."""


class FieldDeclarationError(ImportsError):
    """A field set is self-contradictory and cannot map any document."""


class UnmappedRequiredField(ImportsError):
    """A required field has no source column in the supplied mapping."""


class RunStatus(enum.StrEnum):
    """The run lifecycle, ported from Sub's `ImportRunStatus`.

    `DRY_RUN_READY` is the state that makes the two-phase contract real: it is
    reached only by a run that validated every row and wrote nothing, and it is
    the only state `promote` accepts.
    """

    PENDING = "pending"
    RUNNING = "running"
    DRY_RUN_READY = "dry_run_ready"
    COMPLETED = "completed"
    FAILED = "failed"


class RowStatus(enum.StrEnum):
    """The outcome of one input line."""

    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class SourceLayout(enum.StrEnum):
    """How the stored bytes are decoded into rows.

    Only `CSV` is decodable by this release. `XLSX` is declarable so a run
    recorded against a spreadsheet keeps its true layout, and so the column is
    not migrated when the decoder arrives with the first adopter that needs it
    (see the module README).
    """

    CSV = "csv"
    XLSX = "xlsx"


_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")

# A persisted error is an operator-facing classification, never a dump of a
# source row or an exception. Keeping the bound in the public contract makes a
# diagnostic adapter prove that it is safe before the ledger can retain it.
MAX_SAFE_ERROR_MESSAGE: int = 500

# Sub's loader stopped at 10_000 rows with a "Row limit exceeded" error; ERP
# has no row ceiling at all and relies on the operator. A default that is
# generous but finite keeps a malformed 2 GB upload from becoming a run.
DEFAULT_MAX_ROWS: int = 50_000


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """A bounded, persistence-safe error supplied deliberately by a domain.

    `message` must be suitable for an operator and must not quote imported
    values, credentials, SQL, or a raw exception. The module can enforce the
    shape and size; the domain adapter remains responsible for the semantic
    redaction because only it knows what its values mean.
    """

    code: str
    message: str

    def __post_init__(self) -> None:
        if not _ERROR_CODE_RE.fullmatch(self.code):
            raise ValueError(
                f"error code {self.code!r} must match {_ERROR_CODE_RE.pattern}"
            )
        if not self.message.strip():
            raise ValueError("a safe error message cannot be blank")
        if len(self.message) > MAX_SAFE_ERROR_MESSAGE:
            raise ValueError(
                "a safe error message cannot exceed "
                f"{MAX_SAFE_ERROR_MESSAGE} characters"
            )


class RowRejected(ImportsError):
    """An expected domain refusal that is safe to persist as a row outcome.

    Any other exception from an applier is unexpected and escapes the chunk;
    it is never converted to a row error or copied into the ledger.
    """

    def __init__(self, code: str, message: str) -> None:
        self.issue = ImportIssue(code, message)
        super().__init__(f"{self.issue.code}: {self.issue.message}")


def normalize_column(column: str) -> str:
    """The comparison form of a source column heading.

    Spreadsheet headings differ from each other by case, punctuation and
    whitespace far more often than by meaning — ERP's alias table carries
    ``Account Name``, ``AccountName``, ``account_name`` and ``GL Account Name``
    as separate strings for exactly this reason. Normalising once here is what
    lets a field declare four aliases instead of forty.
    """
    return re.sub(r"[^a-z0-9]+", "_", column.strip().lower()).strip("_")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field a domain expects, and the source headings that mean it."""

    name: str
    required: bool = False
    aliases: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not _FIELD_NAME_RE.match(self.name):
            raise FieldDeclarationError(
                f"field name {self.name!r} must match {_FIELD_NAME_RE.pattern} — "
                "it is the key the validator and applier receive, not a label"
            )
        if any(not alias.strip() for alias in self.aliases):
            raise FieldDeclarationError(f"field {self.name!r} has a blank alias")

    def matches(self) -> frozenset[str]:
        """Every normalised heading that resolves to this field, its own name
        included."""
        return frozenset(
            {normalize_column(self.name), *(normalize_column(a) for a in self.aliases)}
        )


@dataclass(frozen=True, slots=True)
class FieldSet:
    """The domain's declaration of what a row of this kind contains.

    This is the vocabulary boundary. ERP's `COLUMN_ALIASES`, its six accounting
    vendor detectors and Sub's `ENTITY_CONFIG` are all values of this type held
    by their own products; the module ships the mechanism and none of the
    vocabulary (ADR-0025 § 5).
    """

    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        if not self.fields:
            raise FieldDeclarationError("a field set must declare at least one field")
        names = [field.name for field in self.fields]
        if len(set(names)) != len(names):
            raise FieldDeclarationError(f"duplicate field name in {sorted(names)}")
        claimed: dict[str, str] = {}
        for field in self.fields:
            for match in field.matches():
                owner = claimed.get(match)
                if owner is not None:
                    raise FieldDeclarationError(
                        f"heading {match!r} is claimed by both {owner!r} and "
                        f"{field.name!r} — an ambiguous alias would map a column "
                        "to whichever field happened to be declared first"
                    )
                claimed[match] = field.name

    @property
    def required_names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields if field.required)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields)

    def field_for(self, column: str) -> str | None:
        """The declared field a source heading resolves to, if any."""
        normalized = normalize_column(column)
        for field in self.fields:
            if normalized in field.matches():
                return field.name
        return None


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Declared field → source column, as chosen or confirmed by an operator.

    Stored as ordered pairs rather than a dict so the contract is deeply
    immutable and the persisted mapping is byte-stable across runs.
    """

    pairs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        fields = [field for field, _ in self.pairs]
        if len(set(fields)) != len(fields):
            raise FieldDeclarationError(
                f"a field is mapped twice in {sorted(fields)} — one field, one "
                "source column"
            )
        columns = [column for _, column in self.pairs]
        if len(set(columns)) != len(columns):
            raise FieldDeclarationError(
                f"a source column is mapped twice in {sorted(columns)}"
            )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> ColumnMapping:
        return cls(tuple(sorted(mapping.items())))

    def as_dict(self) -> dict[str, str]:
        return dict(self.pairs)

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(field for field, _ in self.pairs)


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """The stored bytes a run reads, owned by `dotmac-files` (ADR-0022).

    The module holds an opaque file id and the checksum that file module already
    computed. It never validates, opens, streams or deletes the object, and it
    never learns a provider or a storage key.
    """

    file_id: UUID
    checksum_sha256: str
    layout: SourceLayout = SourceLayout.CSV
    delimiter: str = ","
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum_sha256):
            raise MalformedSource(
                "checksum_sha256 must be 64 lowercase hex digits — it is what "
                "proves an apply run reads the bytes its dry run validated"
            )
        if len(self.delimiter) != 1:
            raise MalformedSource("delimiter must be exactly one character")


@dataclass(frozen=True, slots=True)
class Preview:
    """What an operator confirms before a run is created."""

    columns: tuple[str, ...]
    mapping: ColumnMapping
    unmapped_columns: tuple[str, ...]
    missing_required_fields: tuple[str, ...]
    sample: tuple[tuple[tuple[str, str], ...], ...] = ()

    @property
    def is_mappable(self) -> bool:
        return not self.missing_required_fields


@dataclass(frozen=True, slots=True)
class RunProgress:
    """The durable checkpoint after one caller-owned transaction."""

    run_id: UUID
    processed: int
    ok: int
    failed: int
    skipped: int
    status: RunStatus

    @property
    def is_complete(self) -> bool:
        return self.status in {RunStatus.DRY_RUN_READY, RunStatus.COMPLETED}


@runtime_checkable
class RowValidator(Protocol):
    """The domain's answer to "is this row acceptable?".

    Returns the reasons it is not; an empty sequence means acceptable. It must
    not write: a validator is the only object a dry run holds.
    """

    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]: ...


@runtime_checkable
class RowApplier(Protocol):
    """The domain's mutation for one validated row.

    Returns an opaque JSON-serialisable result — typically the created or
    matched entity's id — which the module records and never interprets. The
    module owns no foreign key into a domain table (ADR-0025 § 3); if the domain
    needs the reverse link it stores the run and row ids on its own row, as
    Sub's `Payment.import_run_id` already does.
    """

    def apply(self, row: Mapping[str, str]) -> Mapping[str, object]: ...


__all__ = [
    "DEFAULT_MAX_ROWS",
    "MAX_SAFE_ERROR_MESSAGE",
    "ColumnMapping",
    "FieldDeclarationError",
    "FieldSet",
    "FieldSpec",
    "ImportRunNotFound",
    "ImportIssue",
    "ImportsError",
    "InvalidRunState",
    "MalformedSource",
    "Preview",
    "PromotionRefused",
    "RowApplier",
    "RowRejected",
    "RowStatus",
    "RowValidator",
    "RunProgress",
    "RunStatus",
    "SourceDocument",
    "SourceLayout",
    "SourceMismatch",
    "UnmappedRequiredField",
    "normalize_column",
]
