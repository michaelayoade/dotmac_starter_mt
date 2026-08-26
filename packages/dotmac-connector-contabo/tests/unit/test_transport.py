from __future__ import annotations

import json

import pytest
from dotmac_connector_contabo import (
    ContaboRequest,
    ContaboResponse,
    ContaboTransportError,
    FailureKind,
    normalize_api_endpoint,
)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.contabo.com",
        "https://api.contabo.com.evil.example",
        "https://user@api.contabo.com",
        "https://api.contabo.com/v1",
        "https://api.contabo.com?next=https://internal.example",
        "https://127.0.0.1",
        "https://[::1]",
    ],
)
def test_endpoint_ssrf_variants_are_refused(endpoint: str) -> None:
    with pytest.raises(ValueError, match="api_endpoint_invalid"):
        normalize_api_endpoint(endpoint)


def test_only_official_origin_is_canonical() -> None:
    assert normalize_api_endpoint("https://api.contabo.com") == (
        "https://api.contabo.com"
    )
    assert normalize_api_endpoint("https://api.contabo.com:443/") == (
        "https://api.contabo.com"
    )


def test_request_and_response_repr_never_render_material() -> None:
    request = ContaboRequest(
        method="POST",
        base_endpoint="https://api.contabo.com",
        path="/v1/firewalls",
        held_material=json.dumps(
            {
                "client_id": "client-material",
                "client_secret": "secret-material",
                "password": "password-material",
                "username": "user@example.test",
            }
        ),
        request_id="b6f359c2-880d-5dd7-8591-6392d03cc9d7",
        document={"description": "body-material"},
        mutating=True,
    )
    response = ContaboResponse(200, b"response-material")

    rendered = repr(request) + repr(response)
    for material in (
        "client-material",
        "secret-material",
        "password-material",
        "body-material",
        "response-material",
    ):
        assert material not in rendered


def test_transport_error_contains_only_stable_code() -> None:
    error = ContaboTransportError("provider_unavailable", FailureKind.RETRYABLE)

    assert str(error) == "provider_unavailable"
    assert repr(error.args) == "('provider_unavailable',)"
