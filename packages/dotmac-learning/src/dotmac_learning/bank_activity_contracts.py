"""Typed bank/Activity import intent and uncommitted source observations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import InstanceOf

from .bank_validation import BankDoc
from .curriculum_contracts import BankDocument, CurriculumInput


class ImportBankWithActivityCommand(CurriculumInput):
    """Product has parsed and linted the document before invoking this intent."""

    tenant_id: UUID
    doc: InstanceOf[BankDoc] | BankDocument


@dataclass(frozen=True, slots=True)
class BankActivityImportResult:
    """No Activity identity or defaults are read before the caller's flush."""

    bank_id: UUID
    title: str
    created: bool
    applied_policy: tuple[tuple[str, object], ...]


__all__ = ["BankActivityImportResult", "ImportBankWithActivityCommand"]
