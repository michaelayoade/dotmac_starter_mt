"""V3 keeps the stateless boundary: brand arrives as frozen snapshots, data-only."""

from dataclasses import dataclass, fields

from tests.architecture.test_document_rendering_module import (
    SOURCE_ROOT,
    _forbidden_import_findings,
)


def _locator_fields(cls):
    return [
        item.name
        for item in fields(cls)
        if any(token in item.name for token in ("url", "path", "uri", "href"))
    ]


def _v3_sources():
    return sorted(
        (*SOURCE_ROOT.glob("*_v3.py"), *SOURCE_ROOT.glob("*_statement_v1.py"))
    )


def test_v3_sources_exist_and_import_no_brand_files_or_storage_owner():
    sources = _v3_sources()
    assert {path.name for path in sources} >= {
        "contracts_v3.py",
        "projection_v3.py",
        "renderers_v3.py",
        "service_v3.py",
        "output_validation_v3.py",
        "conformance_v3.py",
        "refusal_kit_v3.py",
        "contracts_statement_v1.py",
        "projection_statement_v1.py",
        "templates_statement_v1.py",
        "renderers_statement_v1.py",
    }
    findings = []
    for path in sources:
        findings.extend(
            _forbidden_import_findings(
                path.read_text(encoding="utf-8"), label=path.name
            )
        )
    assert not findings, "\n".join(findings)
    for planted in (
        "import dotmac_brand_profiles\n",
        "from dotmac_brand_profiles import resolve_brand\n",
        "import dotmac_files\n",
    ):
        assert _forbidden_import_findings(
            planted, label="planted_v3.py"
        ), "sensitivity: brand/files import is no longer detected"


def test_logo_asset_is_a_frozen_snapshot_with_no_locator_field():
    from dotmac_document_rendering import (
        LOGO_MAX_BYTES,
        LOGO_MEDIA_TYPES,
        EmbeddedLogoAssetV3,
        InvoiceDocumentFactV3,
    )

    assert [item.name for item in fields(EmbeddedLogoAssetV3)] == [
        "file_id",
        "media_type",
        "sha256",
        "byte_length",
    ]
    assert LOGO_MAX_BYTES == 256 * 1024
    assert LOGO_MEDIA_TYPES == {"image/png", "image/jpeg"}
    names = {item.name for item in fields(InvoiceDocumentFactV3)}
    assert "brand_logo" in names and "brand_asset" not in names
    assert not _locator_fields(InvoiceDocumentFactV3)
    assert not _locator_fields(EmbeddedLogoAssetV3)

    @dataclass(frozen=True)
    class PlantedLogo:
        file_id: str
        logo_url: str

    assert _locator_fields(PlantedLogo) == [
        "logo_url"
    ], "sensitivity: a locator-bearing logo shape is no longer detected"


def test_pdf_engine_protocol_declares_the_data_only_policy_and_v1_is_unchanged():
    from dotmac_document_rendering import (
        DATA_ONLY_RESOURCE_POLICY,
        PdfEngine,
        PdfEngineV3,
    )

    assert DATA_ONLY_RESOURCE_POLICY == "data-only"
    assert "resource_policy" in PdfEngineV3.__annotations__
    assert "resource_policy" not in getattr(PdfEngine, "__annotations__", {})


def test_v3_html_is_only_built_where_the_data_only_check_runs():
    renderer_source = (SOURCE_ROOT / "renderers_v3.py").read_text(encoding="utf-8")
    assert "html_data_only_violations_v3(" in renderer_source
    assert "data_uri_for_logo_v3(" in renderer_source
    for scheme in ("http://", "https://", "file://"):
        assert scheme not in renderer_source
    # Sensitivity: the scheme detector bites on a planted literal.
    assert "http://" in 'src = "http://planted.invalid/"'
