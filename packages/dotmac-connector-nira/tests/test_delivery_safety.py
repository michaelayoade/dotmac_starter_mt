"""Safety canaries for NiRA egress, sessions and ambiguous EPP writes."""

from __future__ import annotations

from collections.abc import Mapping

import dotmac_connector_nira.delivery as delivery
import dotmac_connector_nira.plugin as plugin
from dotmac_connector_nira import frames
from dotmac_connector_nira.epp import EppResult, EppTransportError
from dotmac_integration.retry import OutcomeStatus
from dotmac_integration.spi import DispatchRequest


def _request(
    *,
    operation: str,
    capability_id: str = delivery.DOMAIN_REGISTER_CAPABILITY,
    host: str = delivery.OTE_HOST,
    secrets: Mapping[str, str] | None = None,
    payload: Mapping[str, object] | None = None,
) -> DispatchRequest:
    body: dict[str, object] = {
        "operation": operation,
        "name": "example.ng",
        "period_years": 1,
        "registrant": "contact-1",
        "auth_pw": "domain-secret",
    }
    body.update(payload or {})
    return DispatchRequest(
        capability_id=capability_id,
        event_type=capability_id,
        payload=body,
        config={
            "host": host,
            "port": 700,
            "clid": "dotmac",
            "connect_timeout": 5,
            "read_timeout": 5,
        },
        secrets=secrets or {delivery.EPP_PASSWORD: "registry-secret"},
        idempotency_key="dispatch-1",
    )


def test_manifest_and_config_allow_only_reviewed_ote_host() -> None:
    assert plugin.MANIFEST.egress is not None
    assert plugin.MANIFEST.egress.hosts == (delivery.OTE_HOST,)
    host_schema = delivery.OUTBOUND_CONFIG_SCHEMA["properties"]
    assert isinstance(host_schema, dict)
    assert host_schema["host"] == {"type": "string", "enum": [delivery.OTE_HOST]}


def test_handler_refuses_unreviewed_host_before_session(monkeypatch) -> None:
    def forbidden_session(*args: object, **kwargs: object) -> object:
        raise AssertionError("a refused host must not construct a session")

    monkeypatch.setattr(delivery, "EppSession", forbidden_session)
    result = delivery.NiraDeliveryHandler(delivery.DOMAIN_REGISTER_CAPABILITY)(
        _request(operation="domain_create", host="attacker.example")
    )
    assert result.status is OutcomeStatus.TERMINAL
    assert result.error_code == "egress_host_not_allowed"


def test_delivery_materializes_client_pem(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Session:
        def __init__(self, host: str, port: int, **kwargs: object) -> None:
            observed.update(host=host, port=port, **kwargs)

        def connect(self) -> str:
            return "<greeting/>"

        def request(self, xml: str) -> EppResult:
            return EppResult(1000, "ok", "<epp/>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(delivery, "EppSession", Session)
    result = delivery.NiraDeliveryHandler(delivery.DOMAIN_REGISTER_CAPABILITY)(
        _request(
            operation="domain_create",
            secrets={
                delivery.EPP_PASSWORD: "registry-secret",
                delivery.CLIENT_PEM: "certificate-and-key",
            },
        )
    )
    assert result.status is OutcomeStatus.SUCCEEDED
    assert observed["client_pem"] == "certificate-and-key"


def test_failure_after_business_command_is_not_retried(monkeypatch) -> None:
    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return "<greeting/>"

        def request(self, xml: str) -> EppResult:
            if "<login>" in xml or "<logout/>" in xml:
                return EppResult(1000, "ok", "<epp/>")
            raise EppTransportError("connection closed after command bytes")

        def close(self) -> None:
            pass

    monkeypatch.setattr(delivery, "EppSession", Session)
    result = delivery.NiraDeliveryHandler(delivery.DOMAIN_REGISTER_CAPABILITY)(
        _request(operation="domain_create")
    )
    assert result.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert result.error_code == "epp_command_outcome_unknown"


def test_successful_read_does_not_drop_required_domain_result(monkeypatch) -> None:
    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return "<greeting/>"

        def request(self, xml: str) -> EppResult:
            return EppResult(1000, "ok", "<epp/>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(delivery, "EppSession", Session)
    result = delivery.NiraDeliveryHandler(delivery.AVAILABILITY_CAPABILITY)(
        _request(
            operation="domain_check",
            capability_id=delivery.AVAILABILITY_CAPABILITY,
            payload={"names": ["example.ng"]},
        )
    )
    assert result.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert result.error_code == "domain_result_contract_unavailable"


def test_successful_transfer_query_does_not_drop_required_state(monkeypatch) -> None:
    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return "<greeting/>"

        def request(self, xml: str) -> EppResult:
            return EppResult(1000, "ok", "<epp><trnData/></epp>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(delivery, "EppSession", Session)
    result = delivery.NiraDeliveryHandler(delivery.DOMAIN_TRANSFER_CAPABILITY)(
        _request(
            operation="domain_transfer",
            capability_id=delivery.DOMAIN_TRANSFER_CAPABILITY,
            payload={"transfer_op": "query", "auth_pw": "domain-secret"},
        )
    )
    assert result.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert result.error_code == "domain_result_contract_unavailable"


def test_object_exists_is_not_assumed_to_be_our_prior_create(monkeypatch) -> None:
    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return "<greeting/>"

        def request(self, xml: str) -> EppResult:
            code = 1000 if "<login>" in xml or "<logout/>" in xml else 2302
            return EppResult(code, "exists", "<epp/>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(delivery, "EppSession", Session)
    result = delivery.NiraDeliveryHandler(delivery.DOMAIN_REGISTER_CAPABILITY)(
        _request(operation="domain_create")
    )
    assert result.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert result.error_code == "object_exists"


def test_login_negotiates_fee_extension_and_attributes_are_quoted() -> None:
    login = frames.login("dotmac", "pw", cltrid="login-1")
    assert f"<extURI>{frames.FEE_EXTENSION_URI}</extURI>" in login
    transfer = frames.domain_transfer(
        "example.ng",
        op='request" onmouseover="bad',
        auth_pw="pw",
        cltrid="transfer-1",
    )
    assert 'onmouseover="bad"' not in transfer
    assert "&quot;" in transfer


def test_health_refuses_login_failure(monkeypatch) -> None:
    greeting = (
        '<epp xmlns="urn:ietf:params:xml:ns:epp-1.0"><greeting><svcMenu>'
        "<objURI>urn:ietf:params:xml:ns:domain-1.0</objURI>"
        "<objURI>urn:ietf:params:xml:ns:host-1.0</objURI>"
        "<objURI>urn:ietf:params:xml:ns:contact-1.0</objURI>"
        "<svcExtension><extURI>urn:ietf:params:xml:ns:epp:fee-1.0</extURI>"
        "</svcExtension></svcMenu></greeting></epp>"
    )

    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return greeting

        def request(self, xml: str) -> EppResult:
            return EppResult(2202, "source IP prohibited", "<epp/>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(plugin, "EppSession", Session)
    diagnostics = plugin.PLUGIN.validate_connection(
        config={
            "host": delivery.OTE_HOST,
            "port": 700,
            "clid": "dotmac",
            "connect_timeout": 5,
            "read_timeout": 5,
        },
        secrets={delivery.EPP_PASSWORD: "registry-secret"},
    )
    assert [diagnostic.code for diagnostic in diagnostics] == ["registry_login_refused"]


def test_health_accepts_standard_successful_logout_code(monkeypatch) -> None:
    greeting = (
        '<epp xmlns="urn:ietf:params:xml:ns:epp-1.0"><greeting><svcMenu>'
        "<objURI>urn:ietf:params:xml:ns:domain-1.0</objURI>"
        "<objURI>urn:ietf:params:xml:ns:host-1.0</objURI>"
        "<objURI>urn:ietf:params:xml:ns:contact-1.0</objURI>"
        "<svcExtension><extURI>urn:ietf:params:xml:ns:epp:fee-1.0</extURI>"
        "</svcExtension></svcMenu></greeting></epp>"
    )

    class Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def connect(self) -> str:
            return greeting

        def request(self, xml: str) -> EppResult:
            code = 1500 if "<logout/>" in xml else 1000
            return EppResult(code, "ok", "<epp/>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(plugin, "EppSession", Session)
    diagnostics = plugin.PLUGIN.validate_connection(
        config={
            "host": delivery.OTE_HOST,
            "port": 700,
            "clid": "dotmac",
            "connect_timeout": 5,
            "read_timeout": 5,
        },
        secrets={delivery.EPP_PASSWORD: "registry-secret"},
    )
    assert diagnostics == ()
