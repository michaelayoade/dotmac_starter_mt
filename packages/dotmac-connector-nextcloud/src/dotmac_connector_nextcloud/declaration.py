"""Exact product-owned contract declarations for the Nextcloud connector."""

from __future__ import annotations

from typing import Final

from dotmac_integration.spi import (
    CapabilityContractSnapshot,
    CapabilityDeclaration,
    CapabilitySchemaDocument,
    ConnectorManifest,
    SpiRange,
)
from dotmac_managed_collaboration_contracts import (
    CAPABILITY_CONTRACTS,
    CAPABILITY_SCHEMAS,
)

CONNECTOR_KEY: Final = "nextcloud"
VERSION: Final = "0.1.0a1"
SPI_RANGE: Final = SpiRange.parse(">=1.2,<2.0")


def _capability_id(contract: CapabilityContractSnapshot) -> str:
    return f"{contract.capability_code}.v{contract.schema_version}"


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
    if {(document.schema_ref, document.digest) for document in documents} != expected:
        raise RuntimeError("managed-collaboration catalogue omitted a held schema")
    return documents


CAPABILITY_IDS: Final = tuple(
    sorted(_capability_id(contract) for contract in CAPABILITY_CONTRACTS)
)

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SPI_RANGE,
    capabilities=tuple(
        CapabilityDeclaration(
            capability_id=_capability_id(contract),
            contract_snapshot=contract,
            schema_documents=_schemas_for(contract),
        )
        for contract in sorted(
            CAPABILITY_CONTRACTS,
            key=lambda item: (item.capability_code, item.schema_version),
        )
    ),
)

__all__ = ["CAPABILITY_IDS", "CONNECTOR_KEY", "MANIFEST", "SPI_RANGE", "VERSION"]
