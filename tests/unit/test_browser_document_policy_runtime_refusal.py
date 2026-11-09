"""Metadata guard tests; patched versions are not compatibility qualification."""

from __future__ import annotations

import pytest
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.app_factory import _validate_browser_document_runtime, create_app
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.web_surfaces import (
    BrowserDocumentPolicyV1,
    InlineScriptHashV1,
    WebRouteRef,
)


@pytest.mark.parametrize(
    "fastapi_version,starlette_version",
    [
        ("0.111.0", "1.10.0"),
        ("0.115.14", "1.10.0"),
        ("0.117.0", "1.10.0"),
        ("0.118.0", "1.10.0"),
        ("0.140.0", "1.10.0"),
        ("0.140.13", "0.46.2"),
        ("malformed", "1.10.0"),
        ("0.140.13", "malformed"),
        (None, "1.10.0"),
        ("0.140.13", None),
    ],
)
def test_unqualified_runtime_metadata_refuses_active_policy(
    monkeypatch,
    fastapi_version,
    starlette_version,
):
    import fastapi
    import starlette

    monkeypatch.setattr(fastapi, "__version__", fastapi_version)
    monkeypatch.setattr(starlette, "__version__", starlette_version)
    with pytest.raises(ValueError, match="FastAPI 0.140.13 and Starlette 1.10.0"):
        _validate_browser_document_runtime(True)


@pytest.mark.parametrize("web_enabled", [True, False])
@pytest.mark.parametrize("version", ["0.115.14", "0.140.13", "malformed", None])
def test_inactive_policy_factory_does_not_restrict_runtime_metadata(
    monkeypatch,
    web_enabled,
    version,
):
    import fastapi
    import starlette

    monkeypatch.setattr(fastapi, "__version__", version)
    monkeypatch.setattr(starlette, "__version__", version)
    policy = BrowserDocumentPolicyV1(
        "learner",
        WebRouteRef("labs", "console", "document"),
        (InlineScriptHashV1("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),),
    )
    create_app(
        ProductAssemblySpec(
            name="inactive-document-policy",
            api_documentation=api_documentation_policy("development"),
            web_enabled=web_enabled,
            browser_document_policies=() if web_enabled else (policy,),
        )
    )


@pytest.mark.parametrize("web_enabled", [True, False])
def test_inactive_policies_preserve_disabled_kernel_header_writer(
    monkeypatch, web_enabled
):
    from dotmac_kernel.config import settings

    monkeypatch.setattr(settings, "security_headers_enabled", False)
    policy = BrowserDocumentPolicyV1(
        "learner",
        WebRouteRef("labs", "console", "document"),
        (InlineScriptHashV1("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),),
    )
    create_app(
        ProductAssemblySpec(
            name="inactive-disabled-header-writer",
            api_documentation=api_documentation_policy("development"),
            web_enabled=web_enabled,
            browser_document_policies=() if web_enabled else (policy,),
        )
    )
