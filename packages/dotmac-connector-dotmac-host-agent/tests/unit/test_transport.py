from __future__ import annotations

import json

import pytest
from dotmac_connector_dotmac_host_agent import (
    FailureKind,
    HostAgentRequest,
    HostAgentResponse,
    HostAgentTransportError,
    normalize_agent_endpoint,
)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://agent.example.test",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://user@agent.example.test",
        "https://agent.example.test/v1",
        "https://agent.example.test?next=https://internal.example",
        "https://agent",
    ],
)
def test_unsafe_agent_origins_are_refused(endpoint: str) -> None:
    with pytest.raises(ValueError, match="agent_endpoint_invalid"):
        normalize_agent_endpoint(endpoint)


def test_explicit_private_target_is_allowed_but_still_identity_bound() -> None:
    assert normalize_agent_endpoint("https://10.20.30.40:8443") == (
        "https://10.20.30.40:8443"
    )


def test_request_and_response_repr_hide_identity_material_and_bodies() -> None:
    request = HostAgentRequest(
        method="POST",
        base_endpoint="https://agent.example.test",
        path="/v1/provision/host.health-probe.lifecycle.v1/apply",
        identity_ref="host-agent/example",
        held_material=json.dumps(
            {
                "authorization_token": "token-material",
                "ca_certificate_file": "/run/secrets/ca.pem",
                "client_certificate_file": "/run/secrets/client.pem",
                "client_private_key_file": "/run/secrets/client.key",
                "expected_origin": "https://agent.example.test",
                "identity_ref": "host-agent/example",
            }
        ),
        request_id="b6f359c2-880d-5dd7-8591-6392d03cc9d7",
        document={"target": "body-material"},
        mutating=True,
    )
    response = HostAgentResponse(200, b"response-material")

    rendered = repr(request) + repr(response)
    for material in (
        "token-material",
        "client.key",
        "body-material",
        "response-material",
    ):
        assert material not in rendered


def test_transport_error_contains_only_stable_code() -> None:
    error = HostAgentTransportError("agent_unavailable", FailureKind.RETRYABLE)

    assert str(error) == "agent_unavailable"
    assert error.args == ("agent_unavailable",)
