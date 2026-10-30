"""Historical Billing row shape for real pre-bi12 migration canaries only.

The old physical column population is explicit. No new column or table is added
before the migration under test. Real database constraints, scoped sessions,
financial operation bodies, fingerprints and immutable facts remain exercised.
"""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from copy import copy
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from dotmac_billing import service
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.orm import registry


@contextmanager
def predecessor_obligation_columns() -> Iterator[None]:
    """Bind the predecessor obligation column set during historical seeding.

    This is a test-only legacy mapper over an explicit Core table. It rejects
    successor evidence and is disposed before current-schema source is used.
    """
    mappings = registry()
    try:
        with ExitStack() as stack:
            for name in ("_TENANT", "_PLATFORM"):
                plane = getattr(service, name)
                source = plane.obligation.__table__
                predecessor = Table(
                    source.name,
                    MetaData(),
                    *(
                        Column(
                            column.name,
                            column.type,
                            primary_key=column.primary_key,
                            nullable=column.nullable,
                            default=copy(column.default),
                            server_default=copy(column.server_default),
                        )
                        for column in source.columns
                        if column.name != "tax_evidence"
                    ),
                    schema=source.schema,
                )

                class HistoricalObligation:
                    tax_evidence = None

                    def __init__(self, **values: Any) -> None:
                        if values.pop("tax_evidence", None) is not None:
                            raise ValueError(
                                "predecessor cannot contain V2 Tax evidence"
                            )
                        for key, value in values.items():
                            setattr(self, key, value)

                mappings.map_imperatively(HistoricalObligation, predecessor)
                stack.enter_context(
                    patch.object(
                        service, name, replace(plane, obligation=HistoricalObligation)
                    )
                )
            yield
    finally:
        mappings.dispose()
