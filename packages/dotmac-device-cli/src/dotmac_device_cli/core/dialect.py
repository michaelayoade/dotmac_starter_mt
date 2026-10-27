"""The dialect interface: everything about a vendor that is not data.

A profile (data) says WHICH commands, prompts and quirks a model has. A dialect
(code) says how that vendor ADDRESSES things and how its replies are READ: ONT
address formatting and parsing, command composition per typed operation, and the
parsers. The engine and the typed operations depend only on this interface and
never name a vendor, so a new vendor is one dialect plus data plus transcripts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from dotmac_device_cli.core.profile import Profile


class RefError(ValueError):
    """Text is not a valid native address for this dialect."""


@dataclass(frozen=True, slots=True)
class OntRef:
    """Vendor-neutral address of a PON port, or of one ONT on it.

    ``shelf``/``slot``/``port`` are the neutral position; ``ont_id`` is None for
    a port reference. ``native`` keeps the vendor's own canonical string so a
    caller can show it and round-trip it; ``dialect`` names who owns that form.
    """

    dialect: str
    shelf: int
    slot: int
    port: int
    ont_id: int | None
    native: str

    @property
    def is_port(self) -> bool:
        return self.ont_id is None


@dataclass(frozen=True, slots=True)
class OntRow:
    """One ONT as the device listed it, in neutral terms."""

    ref: OntRef
    serial: str
    admin_state: str
    run_state: str
    config_state: str
    match_state: str
    description: str


@dataclass(frozen=True, slots=True)
class ParsedTable:
    """A parser's finding. Only the shared classifier may turn it into Ok/Unreadable."""

    rows: tuple[OntRow, ...]
    data_row_hints: int
    recognized: bool
    explicitly_empty: bool
    declared_total: int | None
    orphan_descriptions: int = 0  # description-table ids with no data row
    missing_descriptions: int = 0  # data rows with no description-table row


class Dialect(Protocol):
    name: str

    def format_ref(self, ref: OntRef) -> str:
        """The vendor-native canonical string for a neutral reference."""
        ...

    def parse_ref(self, text: str) -> OntRef:
        """Parse a native string (tolerating the device's own quirks)."""
        ...

    def compose(self, operation: str, profile: Profile, ref: OntRef | None) -> str:
        """The rendered command for a typed operation, or raise ``KeyError`` when
        the profile has no template for it. Never borrows another profile's."""
        ...

    def parse_ont_table(self, text: str) -> ParsedTable: ...

    def operations(self) -> frozenset[str]:
        """Operations this dialect can compose AND parse at all."""
        ...


_DIALECTS: dict[str, Dialect] = {}


def register_dialect(dialect: Dialect) -> None:
    if dialect.name in _DIALECTS:
        raise ValueError(f"dialect {dialect.name!r} already registered")
    _DIALECTS[dialect.name] = dialect


def dialect_for(name: str) -> Dialect | None:
    from dotmac_device_cli import dialects  # noqa: F401  (registers the shipped ones)

    return _DIALECTS.get(name)


def registered_dialects() -> Mapping[str, Dialect]:
    from dotmac_device_cli import dialects  # noqa: F401

    return dict(_DIALECTS)


__all__ = [
    "Dialect",
    "OntRef",
    "OntRow",
    "ParsedTable",
    "RefError",
    "dialect_for",
    "register_dialect",
    "registered_dialects",
]
