"""Fail-closed legacy shim for the retired bank+Activity import.

The predecessor loaded a bank destructively and unconditionally
reassigned an existing Activity's title and pass_threshold, silently
resetting admin-set pass marks on every re-import. Its successor is
:func:`dotmac_learning.import_bank_document_as_draft`, which lands as a
draft, returns YAML policy as unapplied proposals, and never writes an
Activity row. This shim performs no reads or writes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .bank_activity_contracts import (
    BankActivityImportResult,
    ImportBankWithActivityCommand,
)
from .bank_lifecycle_contracts import LegacyBankWriterRetired


def import_bank_with_activity(
    db: Session, command: ImportBankWithActivityCommand
) -> BankActivityImportResult | None:
    raise LegacyBankWriterRetired(
        "import_bank_with_activity is retired; import the document as a "
        "draft, publish a bank version, and repoint activities explicitly"
    )


__all__ = ["import_bank_with_activity"]
