"""Contract canaries for the Party-person catalogue consumed by dotmac-people."""

from __future__ import annotations

from dotmac_kernel.migrations.verify import registered_verifiers
from dotmac_kernel.namespaces import KERNEL_MIGRATION_OWNER
from dotmac_kernel.prerequisites import (
    KERNEL_PREREQUISITES,
    PARTY_PERSON_CATALOG_V1,
)

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS


def test_party_person_catalogue_is_named_provided_bound_and_provable() -> None:
    """Every declaration layer is required; deleting any one must fail here."""
    name = PARTY_PERSON_CATALOG_V1.name

    assert name == "party_person_catalog.v1"
    assert name in {spec.name for spec in KERNEL_PREREQUISITES}
    assert name in KERNEL_MIGRATION_OWNER.provides
    assert name in registered_verifiers()

    matches = [
        binding
        for binding in ASSEMBLY_PREREQUISITE_BINDINGS
        if binding.prerequisite == name
    ]
    assert len(matches) == 1
    assert matches[0].provider_owner == "kernel"
    assert matches[0].provider_revision == "0003_party_identity"


def test_party_person_catalogue_summary_names_the_observable_boundary() -> None:
    summary = PARTY_PERSON_CATALOG_V1.summary
    for observable in (
        "public.parties",
        "public.party_persons",
        "tenant_id",
        "party_type",
        "person",
        "row-level security",
        "app_user",
    ):
        assert observable in summary
