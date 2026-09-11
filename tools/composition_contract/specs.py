"""Starter-owned fixed observation specifications for Kernel cutover evidence."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from tools.composition_contract.observations import (
    ObservationSpec,
    PoetryInstallCommandLocator,
    ProductObservationSpec,
    PythonAssignmentKeywordLocator,
    PythonStringAssignmentLocator,
    WholeFileLocator,
)


def _whole(observation_id: str, source_path: str) -> ObservationSpec:
    limit = 1024 * 1024 if source_path == "poetry.lock" else 256 * 1024
    return ObservationSpec(
        observation_id,
        source_path,
        WholeFileLocator(),
        max_source_bytes=limit,
    )


ERP_OBSERVATION_SPEC: Final = ProductObservationSpec(
    repository="dotmac_erp",
    product_id="dotmac-erp",
    subject="erp-kernel-successor-composition",
    observations=(
        _whole("dependency-manifest", "pyproject.toml"),
        _whole("dependency-lock", "poetry.lock"),
        ObservationSpec(
            "production-install",
            "Dockerfile",
            PoetryInstallCommandLocator("dockerfile"),
        ),
        _whole("product-assembly-source", "app/product_assembly.py"),
        ObservationSpec(
            "product-identity",
            "app/product_assembly.py",
            PythonStringAssignmentLocator("PRODUCT_CODE"),
        ),
        ObservationSpec(
            "module-registration",
            "app/product_assembly.py",
            PythonAssignmentKeywordLocator(
                "ERP_PRODUCT_ASSEMBLY", "ProductAssemblySpec", "modules"
            ),
        ),
        _whole("boot-entry-point", "app/main.py"),
        _whole("migration-config", "alembic.ini"),
        _whole("migration-bindings", "app/migration_bindings.py"),
    ),
)

SUB_OBSERVATION_SPEC: Final = ProductObservationSpec(
    repository="dotmac_sub",
    product_id="dotmac-sub",
    subject="sub-kernel-successor-composition",
    observations=(
        _whole("dependency-manifest", "pyproject.toml"),
        _whole("dependency-lock", "poetry.lock"),
        ObservationSpec(
            "production-install",
            "Dockerfile",
            PoetryInstallCommandLocator("dockerfile"),
        ),
        _whole("product-assembly-source", "app/composition.py"),
        ObservationSpec(
            "product-identity",
            "app/composition.py",
            PythonStringAssignmentLocator("PRODUCT_NAME"),
        ),
        ObservationSpec(
            "module-registration",
            "app/composition.py",
            PythonAssignmentKeywordLocator(
                "SUB_ASSEMBLY", "ProductAssemblySpec", "modules"
            ),
        ),
        _whole("boot-entry-point", "app/main.py"),
        _whole("migration-config", "alembic.ini"),
        _whole("migration-bindings", "app/migration_bindings.py"),
        _whole(
            "collections-operator-entry-point",
            "scripts/migration/collections_module_shadow_parity.py",
        ),
    ),
)

ACADEMY_OBSERVATION_SPEC: Final = ProductObservationSpec(
    repository="dotmac_academy_app",
    product_id="dotmac-academy",
    subject="academy-kernel-successor-composition",
    observations=(
        _whole("dependency-manifest", "pyproject.toml"),
        _whole("dependency-lock", "poetry.lock"),
        ObservationSpec(
            "production-install",
            "deploy/install.sh",
            PoetryInstallCommandLocator("shell"),
        ),
        _whole("product-assembly-source", "app/assembly.py"),
        ObservationSpec(
            "product-identity",
            "app/assembly.py",
            PythonStringAssignmentLocator("PRODUCT_ID"),
        ),
        ObservationSpec(
            "module-registration",
            "app/assembly.py",
            PythonAssignmentKeywordLocator(
                "assembly", "ProductAssemblySpec", "modules"
            ),
        ),
        _whole("boot-entry-point", "app/main.py"),
        _whole("kernel-runtime-entry-point", "app/kernel_runtime.py"),
        _whole("migration-config", "alembic.ini"),
    ),
)

PRODUCT_OBSERVATION_SPECS: Final = MappingProxyType(
    {
        "academy": ACADEMY_OBSERVATION_SPEC,
        "erp": ERP_OBSERVATION_SPEC,
        "sub": SUB_OBSERVATION_SPEC,
    }
)

__all__ = [
    "ACADEMY_OBSERVATION_SPEC",
    "ERP_OBSERVATION_SPEC",
    "PRODUCT_OBSERVATION_SPECS",
    "SUB_OBSERVATION_SPEC",
]
