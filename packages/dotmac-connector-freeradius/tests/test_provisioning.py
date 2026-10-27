"""Handler behaviour over fake ports: fetch discipline, drift, CoA gates."""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_connector_freeradius.ports import (
    CoaOutcome,
    SecretFetchRefused,
)
from dotmac_connector_freeradius.provisioning import FreeRadiusProvisioningHandler
from dotmac_connector_freeradius.render import RenderedProjection
from dotmac_integration.spi import (
    ProvisionApplyRequest,
    ProvisionObserveRequest,
    ProvisionResultStatus,
    ProvisionStep,
)

STEP_INPUT = {
    "subject_ref": "svc-1",
    "username": "user@dotmac",
    "desired_state": "enabled",
    "policy_code": "active",
    "policy_version": "v1",
    "attributes": [{"key": "rate_limit", "value": "50M/50M"}],
    "credential_id": "cred-1",
    "secret_version": 3,
    "projection_request_ref": "proj-1",
    "expected_fingerprint": "a" * 64,
    "previous_username": None,
    "valid_until": None,
}


class FakeDb:
    def __init__(self) -> None:
        self.store: dict[str, tuple] = {}
        self.applies: list[tuple[list[str], RenderedProjection]] = []

    def ping(self) -> None:
        return None

    def apply_projection(self, *, usernames, rendered, lock_key) -> int:
        self.applies.append((list(usernames), rendered))
        for name in usernames:
            self.store.pop(name, None)
        self.store[usernames[0]] = (
            rendered.check_rows,
            rendered.reply_rows,
            rendered.group_rows,
        )
        return rendered.rows_written

    def read_projection(self, username):
        return self.store.get(username, ((), (), ()))

    def poll_postauth(self, *, after_id, limit):
        return ()

    def poll_accounting(self, *, after_id, limit):
        return ()

    def nas_secret(self, *, nas_ref, nas_address):
        return "nas-held-value"


HELD = "held-test-material"


class FakeFetch:
    def __init__(self, value: str = HELD, refuse: bool = False) -> None:
        self.secret = value
        self.refuse = refuse
        self.calls: list[tuple[str, int, str]] = []

    def fetch_secret(
        self, *, credential_id, secret_version, projection_request_ref, operation_ref
    ):
        self.calls.append((credential_id, secret_version, projection_request_ref))
        if self.refuse:
            raise SecretFetchRefused("consumed")
        return self.secret


class FakeCoa:
    def __init__(self, outcome: str = "acknowledged") -> None:
        self.outcome = outcome
        self.calls: list[Mapping[str, str]] = []

    def disconnect(self, *, address, port, secret, attributes, timeout_seconds):
        self.calls.append(dict(attributes))
        return CoaOutcome(outcome=self.outcome)


def _handler(db=None, fetch=None, coa=None):
    fake_db = db or FakeDb()
    handler = FreeRadiusProvisioningHandler(
        db_factory=lambda config, secrets: fake_db,
        secret_fetch=fetch or FakeFetch(),
        coa_port=coa or FakeCoa(),
    )
    return handler, fake_db


def _apply(capability, step_input, config=None):
    return ProvisionApplyRequest(
        capability_id=capability,
        command_id="cmd-1",
        operation_ref="op-1",
        plan_hash="b" * 64,
        step=ProvisionStep(step_key="s1", endpoint_code="x", input=step_input),
        config=config or {},
        secrets={},
        idempotency_key="cmd-1:s1",
    )


def test_apply_fetches_once_and_verifies_readback() -> None:
    fetch = FakeFetch()
    handler, db = _handler(fetch=fetch)
    result = handler.apply(_apply("network.access_projection.v1", STEP_INPUT))
    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert fetch.calls == [("cred-1", 3, "proj-1")]
    assert result.evidence["observed_fingerprint"] == "a" * 64
    assert result.evidence["rows_written"] > 0
    # The cleartext reached exactly the password row and nowhere else.
    _names, rendered = db.applies[0]
    values = [row["value"] for row in rendered.check_rows + rendered.reply_rows]
    assert values.count(HELD) == 1


def test_refused_fetch_is_terminal_with_closed_code() -> None:
    handler, db = _handler(fetch=FakeFetch(refuse=True))
    result = handler.apply(_apply("network.access_projection.v1", STEP_INPUT))
    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "secret_fetch_refused"
    assert db.applies == []


def test_observe_reports_drift_as_terminal() -> None:
    handler, _db = _handler()
    result = handler.observe(
        ProvisionObserveRequest(
            capability_id="network.access_projection.v1",
            command_id="cmd-1",
            operation_ref="op-1",
            plan_hash="b" * 64,
            step_key="s1",
            provider_operation_ref="user@dotmac",
            target=STEP_INPUT,
            config={},
            secrets={},
        )
    )
    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "projection_drift"


def test_disconnect_requires_a_bound_network_target() -> None:
    coa = FakeCoa()
    handler, _db = _handler(coa=coa)
    step_input = {
        "subject_ref": "svc-1",
        "username": "user@dotmac",
        "nas_ref": "nas-1",
        "nas_address": "10.99.0.1",
        "coa_port": 3799,
        "session_ref": None,
        "framed_ipv4_address": None,
        "reason_code": "credential_rotated",
    }
    refused = handler.apply(_apply("network.session_disconnect.v1", step_input))
    assert refused.status is ProvisionResultStatus.TERMINAL
    assert refused.error_code == "network_target_refused"
    assert coa.calls == []

    config = {
        "_network_targets": [
            {
                "cidr": "10.99.0.0/16",
                "protocol": "udp",
                "first_port": 3799,
                "last_port": 3799,
            }
        ]
    }
    allowed = handler.apply(
        _apply("network.session_disconnect.v1", step_input, config=config)
    )
    assert allowed.status is ProvisionResultStatus.SUCCEEDED
    assert allowed.evidence["disconnect_outcome"] == "acknowledged"
    assert coa.calls == [{"User-Name": "user@dotmac"}]


def test_fetch_resolves_from_config_when_no_port_injected() -> None:
    """Production path: endpoint from config, token from secret bindings."""
    import httpx
    from dotmac_connector_freeradius.fetch import HttpSecretFetch

    seen = {}

    def respond(request):
        seen["auth"] = request.headers.get("X-Api-Key")
        seen["body"] = request.read()
        return httpx.Response(200, json={"secret": HELD})

    fetch = HttpSecretFetch(
        base_url="https://sub.invalid",
        token=HELD,
        transport=httpx.MockTransport(respond),
    )
    assert (
        fetch.fetch_secret(
            credential_id="cred-1",
            secret_version=3,
            projection_request_ref="proj-1",
            operation_ref="op-1",
        )
        == HELD
    )
    assert seen["auth"] == HELD


def test_fetch_refusal_statuses_are_final() -> None:
    import httpx
    import pytest as _pytest
    from dotmac_connector_freeradius.fetch import HttpSecretFetch
    from dotmac_connector_freeradius.ports import SecretFetchRefused

    fetch = HttpSecretFetch(
        base_url="https://sub.invalid",
        token=None,
        transport=httpx.MockTransport(lambda request: httpx.Response(409)),
    )
    with _pytest.raises(SecretFetchRefused):
        fetch.fetch_secret(
            credential_id="cred-1",
            secret_version=3,
            projection_request_ref="proj-1",
            operation_ref="op-1",
        )
