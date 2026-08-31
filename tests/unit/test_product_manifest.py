"""Product manifest snapshots are canonical facts emitted by product builds."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest
from dotmac_kernel import (
    FeatureManifest,
    ProductAssemblySpec,
    ProductManifestDigestMismatchError,
    ProductManifestError,
    ProductManifestSnapshot,
)
from dotmac_kernel.api_documentation import api_documentation_policy

#: Test assemblies declare the development policy explicitly: the kernel
#: refuses to build without one, and a fallback would be the inherited
#: exposure `api_documentation` exists to end.
_DOCS_POLICY = api_documentation_policy("development")


def _assembly(
    *,
    name: str = "dotmac-sub",
    manifests: tuple[FeatureManifest, ...] | None = None,
) -> ProductAssemblySpec:
    return ProductAssemblySpec(
        api_documentation=_DOCS_POLICY,
        name=name,
        modules=manifests
        or (
            FeatureManifest(
                name="sub.network",
                capabilities=("network.radius", "network.provisioning"),
            ),
            FeatureManifest(
                name="sub.billing",
                capabilities=("billing.export",),
            ),
        ),
    )


def test_snapshot_is_the_canonical_product_and_capability_document() -> None:
    snapshot = ProductManifestSnapshot.from_assembly(
        _assembly(), product_version="7.173.6"
    )

    assert snapshot.product_code == "dotmac-sub"
    assert snapshot.product_version == "7.173.6"
    assert snapshot.capability_codes == (
        "billing.export",
        "network.provisioning",
        "network.radius",
    )
    assert snapshot.schema == "dotmac.product-manifest/v1"
    assert snapshot.digest.startswith("sha256:")
    assert len(snapshot.digest) == len("sha256:") + 64

    assert snapshot.to_json_bytes() == (
        b'{"capability_codes":["billing.export","network.provisioning",'
        b'"network.radius"],"product_code":"dotmac-sub",'
        b'"product_version":"7.173.6","schema":'
        b'"dotmac.product-manifest/v1"}'
    )
    snapshot.require_capability("network.radius")
    with pytest.raises(KeyError, match="not declare"):
        snapshot.require_capability("network.unknown")


def test_snapshot_is_deterministic_across_manifest_and_capability_order() -> None:
    first = ProductManifestSnapshot.from_assembly(
        _assembly(), product_version="7.173.6"
    )
    second = ProductManifestSnapshot.from_assembly(
        _assembly(
            manifests=(
                FeatureManifest(name="sub.billing", capabilities=("billing.export",)),
                FeatureManifest(
                    name="sub.network",
                    capabilities=("network.provisioning", "network.radius"),
                ),
            )
        ),
        product_version="7.173.6",
    )

    assert second == first
    assert second.to_json_bytes() == first.to_json_bytes()
    assert second.digest == first.digest


@pytest.mark.parametrize(
    ("changed", "expected_different_field"),
    [
        (
            ProductManifestSnapshot.from_assembly(
                _assembly(name="dotmac-erp"), product_version="7.173.6"
            ),
            "product_code",
        ),
        (
            ProductManifestSnapshot.from_assembly(
                _assembly(), product_version="7.173.7"
            ),
            "product_version",
        ),
        (
            ProductManifestSnapshot.from_assembly(
                _assembly(
                    manifests=(
                        FeatureManifest(
                            name="sub.network", capabilities=("network.radius",)
                        ),
                    )
                ),
                product_version="7.173.6",
            ),
            "capability_codes",
        ),
    ],
)
def test_every_owned_fact_is_digest_bound(
    changed: ProductManifestSnapshot, expected_different_field: str
) -> None:
    baseline = ProductManifestSnapshot.from_assembly(
        _assembly(), product_version="7.173.6"
    )

    assert getattr(changed, expected_different_field) != getattr(
        baseline, expected_different_field
    )
    assert changed.digest != baseline.digest


def test_parse_requires_the_exact_canonical_document_and_digest() -> None:
    original = ProductManifestSnapshot.from_assembly(
        _assembly(), product_version="7.173.6"
    )

    parsed = ProductManifestSnapshot.from_json_bytes(
        original.to_json_bytes(), expected_digest=original.digest
    )

    assert parsed == original

    pretty = original.to_json_bytes().replace(b',"product_code"', b', "product_code"')
    with pytest.raises(ProductManifestError, match="canonical"):
        ProductManifestSnapshot.from_json_bytes(pretty)

    with pytest.raises(ProductManifestDigestMismatchError, match="does not match"):
        ProductManifestSnapshot.from_json_bytes(
            original.to_json_bytes(), expected_digest="sha256:" + "0" * 64
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"capability_codes":[],"product_code":"dotmac-sub",'
        b'"product_version":"1","schema":"wrong"}',
        b'{"capability_codes":["b","a"],"product_code":"dotmac-sub",'
        b'"product_version":"1","schema":"dotmac.product-manifest/v1"}',
        b'{"capability_codes":["a","a"],"product_code":"dotmac-sub",'
        b'"product_version":"1","schema":"dotmac.product-manifest/v1"}',
        b'{"capability_codes":[],"extra":true,"product_code":"dotmac-sub",'
        b'"product_version":"1","schema":"dotmac.product-manifest/v1"}',
    ],
)
def test_parse_refuses_incomplete_unknown_or_noncanonical_shapes(
    payload: bytes,
) -> None:
    with pytest.raises(ProductManifestError):
        ProductManifestSnapshot.from_json_bytes(payload)


@pytest.mark.parametrize(
    ("product_code", "product_version"),
    [
        ("", "1"),
        (" dotmac-sub", "1"),
        ("dotmac/sub", "1"),
        ("dotmac-sub", ""),
        ("dotmac-sub", " 1"),
    ],
)
def test_invalid_product_identity_is_refused(
    product_code: str, product_version: str
) -> None:
    with pytest.raises(ProductManifestError):
        ProductManifestSnapshot(
            product_code=product_code,
            product_version=product_version,
            capability_codes=(),
        )


def test_snapshot_is_frozen_slotted_and_has_no_mutable_collection_fields() -> None:
    snapshot = ProductManifestSnapshot.from_assembly(
        _assembly(), product_version="7.173.6"
    )

    assert hasattr(ProductManifestSnapshot, "__slots__")
    assert [field.name for field in fields(snapshot)] == [
        "product_code",
        "product_version",
        "capability_codes",
    ]
    assert isinstance(snapshot.capability_codes, tuple)
    with pytest.raises(FrozenInstanceError):
        snapshot.product_code = "dotmac-erp"  # type: ignore[misc]


def test_snapshot_build_validates_the_assembly_before_publishing() -> None:
    duplicate = _assembly(
        manifests=(
            FeatureManifest(name="owner.one", capabilities=("shared.use",)),
            FeatureManifest(name="owner.two", capabilities=("shared.use",)),
        )
    )

    with pytest.raises(ValueError, match="declared by both"):
        ProductManifestSnapshot.from_assembly(duplicate, product_version="1")
