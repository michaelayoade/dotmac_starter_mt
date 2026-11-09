"""Statement HTML with the V3 data-only policy; no service or PDF orchestration."""

from __future__ import annotations

from dotmac_document_rendering.contracts import FactShapeInvalid
from dotmac_document_rendering.contracts_statement_v1 import (
    AccountStatementFactV1,
    AccountStatementProjectionV1,
)
from dotmac_document_rendering.contracts_v2 import DocumentProjectionV2
from dotmac_document_rendering.output_validation_v3 import (
    html_data_only_violations_v3,
)
from dotmac_document_rendering.projection_statement_v1 import (
    project_statement_v1,
    statement_projection_digest_v1,
)
from dotmac_document_rendering.renderers_v2 import (
    _DEFAULT_STYLESHEET,
    html_from_projection_v2,
)
from dotmac_document_rendering.renderers_v3 import (
    check_logo_v3,
    embed_verified_logo_v3,
)
from dotmac_document_rendering.templates_statement_v1 import (
    StatementTemplateCatalogV1,
)

STATEMENT_HTML_RENDERER_CODE = "dotmac-statement-html"
STATEMENT_HTML_RENDERER_VERSION = "1"


def html_from_statement_projection_v1(
    projection: AccountStatementProjectionV1,
    *,
    logo_content: bytes | None = None,
    stylesheet: str = _DEFAULT_STYLESHEET,
) -> str:
    """Serialize a statement projection; its only resource is the data: logo."""
    if not isinstance(projection, AccountStatementProjectionV1):
        raise FactShapeInvalid("projection must use the statement V1 contract")
    if projection.digest != statement_projection_digest_v1(projection):
        raise FactShapeInvalid("statement projection digest is stale")
    check_logo_v3(projection.brand_logo, logo_content)
    # The V2 serializer is a pure function of sections; reuse it unchanged.
    view = DocumentProjectionV2(
        2,
        projection.statement_id,
        projection.fact_version,
        projection.source_fact_fingerprint,
        projection.statement_profile_code,
        projection.statement_profile_version,
        projection.template_code,
        projection.template_version,
        projection.renderer_code,
        projection.renderer_version,
        projection.media_type,
        projection.template_decisions,
        projection.sections,
        projection.digest,
    )
    html = html_from_projection_v2(view, stylesheet=stylesheet)
    html = embed_verified_logo_v3(html, projection.brand_logo, logo_content)
    problems = html_data_only_violations_v3(
        html, max_images=0 if projection.brand_logo is None else 1
    )
    if problems:
        raise FactShapeInvalid("; ".join(problems))
    return html


def statement_html_v1(
    fact: AccountStatementFactV1,
    *,
    catalog: StatementTemplateCatalogV1,
    logo_content: bytes | None = None,
    stylesheet: str = _DEFAULT_STYLESHEET,
) -> tuple[AccountStatementProjectionV1, str]:
    """Select, project and serialize one statement; raises typed refusals."""
    selected = catalog.select(
        statement_profile_code=fact.statement_profile_code,
        statement_profile_version=fact.statement_profile_version,
        media_type="text/html",
    )
    projection = project_statement_v1(
        fact,
        selected,
        renderer_code=STATEMENT_HTML_RENDERER_CODE,
        renderer_version=STATEMENT_HTML_RENDERER_VERSION,
        media_type="text/html",
    )
    return projection, html_from_statement_projection_v1(
        projection, logo_content=logo_content, stylesheet=stylesheet
    )


__all__ = [
    "STATEMENT_HTML_RENDERER_CODE",
    "STATEMENT_HTML_RENDERER_VERSION",
    "html_from_statement_projection_v1",
    "statement_html_v1",
]
