"""Exact product-owned declaration implemented by the Mailcow connector."""

from __future__ import annotations

from typing import Final

from dotmac_integration.spi import (
    CapabilityContractSnapshot,
    CapabilityDeclaration,
    CapabilitySchemaDocument,
    ConnectorManifest,
    SpiRange,
)
from dotmac_managed_email_contracts import (
    CAPABILITY_CONTRACTS,
    CAPABILITY_SCHEMAS,
    EMAIL_LIFECYCLE,
)

CONNECTOR_KEY: Final = "mailcow"
VERSION: Final = "0.1.0a1"
SPI_RANGE: Final = SpiRange.parse(">=1.2,<2.0")
CAPABILITY_ID: Final = EMAIL_LIFECYCLE.capability_id


def _schemas_for(
    contract: CapabilityContractSnapshot,
) -> tuple[CapabilitySchemaDocument, ...]:
    expected = {
        identity
        for operation in contract.operations
        for identity in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        )
    }
    documents = tuple(
        sorted(
            (
                document
                for document in CAPABILITY_SCHEMAS
                if (document.schema_ref, document.digest) in expected
            ),
            key=lambda document: document.schema_ref,
        )
    )
    if {(item.schema_ref, item.digest) for item in documents} != expected:
        raise RuntimeError("managed-email catalogue omitted a held schema")
    return documents


if CAPABILITY_CONTRACTS != (EMAIL_LIFECYCLE,):
    raise RuntimeError("Mailcow connector expects one managed-email capability")

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SPI_RANGE,
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            contract_snapshot=EMAIL_LIFECYCLE,
            schema_documents=_schemas_for(EMAIL_LIFECYCLE),
        ),
    ),
)

__all__ = ["CAPABILITY_ID", "CONNECTOR_KEY", "MANIFEST", "SPI_RANGE", "VERSION"]
