from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from dotmac_connector_nextcloud import (
    FailureKind,
    HttpxNextcloudTransport,
    ManagementRequest,
    NextcloudTransportError,
    management_route,
    normalize_management_endpoint,
)

_FAKE_HELD_CLIENT_MATERIAL = "fixture-client-material"


def _public_dns(host: str, port: int) -> list[tuple[object, ...]]:
    del host, port
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def _request(*, mutating: bool = False) -> ManagementRequest:
    return ManagementRequest(
        capability_id="collaboration.application.lifecycle.v1",
        operation="apply" if mutating else "observe",
        body={"target": {"application_ref": "collaboration-primary"}},
        mutating=mutating,
    )


@pytest.mark.parametrize(
    ("endpoint", "code"),
    [
        ("http://cloud.example.test", "management_endpoint_https_required"),
        ("https://localhost", "management_endpoint_local_host_forbidden"),
        (
            "https://user@cloud.example.test",
            "management_endpoint_credentials_forbidden",
        ),
        (
            "https://cloud.example.test?next=https://elsewhere.test",
            "management_endpoint_query_or_fragment_forbidden",
        ),
    ],
)
def test_management_endpoint_refuses_unsafe_shapes(endpoint: str, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        normalize_management_endpoint(endpoint, resolver=_public_dns)


def test_management_endpoint_refuses_any_non_global_dns_answer() -> None:
    def mixed_dns(host: str, port: int) -> list[tuple[object, ...]]:
        del host, port
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.9", 443)),
        ]

    with pytest.raises(ValueError, match="management_endpoint_unsafe_address"):
        normalize_management_endpoint("https://cloud.example.test", resolver=mixed_dns)


def test_closed_route_preserves_exact_capability_identity() -> None:
    assert management_route(
        "collaboration.user-oidc.configuration.lifecycle.v1", "apply"
    ) == (
        "/ocs/v2.php/apps/dotmac_managed/api/v1/"
        "user-oidc-configuration-lifecycle/apply"
    )
    with pytest.raises(ValueError, match="management_operation_unsupported"):
        management_route("collaboration.application.lifecycle.v1", "arbitrary")


def test_ocs_envelope_is_required_and_redirect_is_never_followed() -> None:
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            307,
            headers={"location": "https://other.example.test/steal"},
            request=request,
        )

    transport = HttpxNextcloudTransport(
        resolver=_public_dns,
        http_transport=httpx.MockTransport(redirect),
    )
    with pytest.raises(NextcloudTransportError) as raised:
        transport.invoke(
            management_endpoint="https://cloud.example.test/base",
            management_authorization="held-authorization-material",
            client_secret=None,
            request=_request(),
        )
    assert raised.value.code == "provider_redirect_rejected"
    assert raised.value.kind is FailureKind.TERMINAL
    assert len(seen) == 1
    assert seen[0].url.path.startswith("/base/ocs/v2.php/")


def test_mutating_timeout_is_ambiguous_but_observe_timeout_is_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    transport = HttpxNextcloudTransport(
        resolver=_public_dns,
        http_transport=httpx.MockTransport(timeout),
    )
    expected = (
        (_request(mutating=True), FailureKind.AMBIGUOUS),
        (_request(mutating=False), FailureKind.RETRYABLE),
    )
    for request, kind in expected:
        with pytest.raises(NextcloudTransportError) as raised:
            transport.invoke(
                management_endpoint="https://cloud.example.test",
                management_authorization="held-authorization-material",
                client_secret=None,
                request=request,
            )
        assert raised.value.kind is kind


def test_valid_ocs_data_is_returned_without_meta_or_secret_headers() -> None:
    observed_headers: Mapping[str, str] = {}

    def success(request: httpx.Request) -> httpx.Response:
        nonlocal observed_headers
        observed_headers = request.headers
        return httpx.Response(
            200,
            json={
                "ocs": {
                    "meta": {"status": "ok", "statuscode": 100},
                    "data": {"evidence": {"cancelled": True}},
                }
            },
            request=request,
        )

    transport = HttpxNextcloudTransport(
        resolver=_public_dns,
        http_transport=httpx.MockTransport(success),
    )
    result = transport.invoke(
        management_endpoint="https://cloud.example.test",
        management_authorization="held-authorization-material",
        client_secret=_FAKE_HELD_CLIENT_MATERIAL,
        request=_request(),
    )
    assert result == {"evidence": {"cancelled": True}}
    assert observed_headers["ocs-apirequest"] == "true"
    assert observed_headers["authorization"] == "held-authorization-material"
    assert observed_headers["x-dotmac-held-client-secret"] == _FAKE_HELD_CLIENT_MATERIAL
    assert _FAKE_HELD_CLIENT_MATERIAL not in repr(_request())


def test_non_ocs_success_body_is_refused() -> None:
    def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"}, request=request)

    transport = HttpxNextcloudTransport(
        resolver=_public_dns,
        http_transport=httpx.MockTransport(invalid),
    )
    with pytest.raises(NextcloudTransportError) as raised:
        transport.invoke(
            management_endpoint="https://cloud.example.test",
            management_authorization="held-authorization-material",
            client_secret=None,
            request=_request(),
        )
    assert raised.value.code == "provider_response_invalid"
