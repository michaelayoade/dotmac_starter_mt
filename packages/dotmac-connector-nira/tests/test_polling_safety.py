"""Durability canaries for the NiRA EPP message-queue adapter.

An EPP poll acknowledgement deletes the queue head.  The connector may send
that destructive command only when the cursor handed back by Integration
proves that the same head's observation and cursor committed on an earlier
call.  These fakes make queue deletion visible so the ordering cannot pass by
asserting only on returned values.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from dotmac_connector_nira import polling
from dotmac_connector_nira.epp import (
    EPP_NS,
    EppProtocolError,
    EppResult,
    EppTransportError,
)
from dotmac_integration.spi import InboundEvent

_CONFIG: dict[str, object] = {
    "host": "ote.registry.ng",
    "port": 700,
    "clid": "registrar",
    "connect_timeout": 1,
    "read_timeout": 1,
}
_SECRETS = {"epp_password": "held-material"}


@dataclass(frozen=True, slots=True)
class _Message:
    message_id: str
    text: str
    res_data: str = ""


class _QueueRegistry:
    """Shared queue state behind a new fake EPP session for every poll call."""

    def __init__(self, *messages: _Message) -> None:
        self.messages = list(messages)
        self.acked: list[str] = []
        self.fail_ack_response_once = False
        self.login_code = 1000
        self.session_kwargs: list[dict[str, object]] = []

    def session(self, *_args: object, **kwargs: object) -> _QueueSession:
        self.session_kwargs.append(dict(kwargs))
        return _QueueSession(self)


class _QueueSession:
    def __init__(self, registry: _QueueRegistry) -> None:
        self.registry = registry

    def connect(self) -> str:
        return "<greeting/>"

    def request(self, frame: str) -> EppResult:
        if "<login" in frame:
            return _result(self.registry.login_code)
        if "<logout" in frame:
            return _result(1500)
        if '<poll op="req"' in frame:
            if not self.registry.messages:
                return _result(1300)
            message = self.registry.messages[0]
            body = (
                f'<msgQ count="{len(self.registry.messages)}" '
                f'id="{message.message_id}"><msg>{message.text}</msg></msgQ>'
            )
            return _result(1301, body + message.res_data)
        if '<poll op="ack"' in frame:
            head = self.registry.messages[0]
            assert f'msgID="{head.message_id}"' in frame
            self.registry.messages.pop(0)
            self.registry.acked.append(head.message_id)
            if self.registry.fail_ack_response_once:
                self.registry.fail_ack_response_once = False
                raise EppTransportError("connection lost after registry consumed ack")
            return _result(1301)
        raise AssertionError(f"unexpected frame: {frame}")

    def close(self) -> None:
        return None


def _result(code: int, response_body: str = "") -> EppResult:
    raw = (
        f'<epp xmlns="{EPP_NS}"><response>'
        f'<result code="{code}"><msg>fake</msg></result>'
        f"{response_body}</response></epp>"
    )
    return EppResult(code=code, message="fake", raw=raw)


def _poll(
    registry: _QueueRegistry,
    cursor: str | None,
    *,
    config: dict[str, object] | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[tuple[InboundEvent, ...], str | None]:
    with patch.object(polling, "EppSession", registry.session):
        return polling.NiraPollHandler().poll(
            cursor,
            config=_CONFIG if config is None else config,
            secrets=_SECRETS if secrets is None else secrets,
        )


def test_first_poll_returns_head_without_acknowledging_it() -> None:
    registry = _QueueRegistry(_Message("m-1", "transfer requested"))

    events, next_cursor = _poll(registry, None)

    assert len(events) == 1
    assert events[0].provider_event_id == "nira-msg:m-1"
    assert next_cursor == "m-1"
    assert registry.acked == []
    assert [item.message_id for item in registry.messages] == ["m-1"]


def test_poll_preserves_structured_result_before_later_ack_deletes_it() -> None:
    structured = (
        '<resData><domain:trnData xmlns:domain="urn:ietf:params:xml:ns:domain-1.0">'
        "<domain:name>example.ng</domain:name>"
        "<domain:trStatus>pending</domain:trStatus>"
        "</domain:trnData></resData>"
    )
    registry = _QueueRegistry(_Message("m-1", "transfer", structured))

    events, next_cursor = _poll(registry, None)

    evidence = events[0].payload["transport_evidence"]
    assert isinstance(evidence, dict)
    serialized = evidence["res_data_xml"]
    assert isinstance(serialized, str)
    assert "example.ng" in serialized
    assert "pending" in serialized
    assert next_cursor == "m-1"
    assert registry.acked == []


def test_poll_refuses_an_unapproved_egress_host_before_session_creation() -> None:
    registry = _QueueRegistry(_Message("m-1", "must not be reached"))
    config = {**_CONFIG, "host": "attacker.invalid"}

    try:
        _poll(registry, "durable", config=config)
    except EppProtocolError:
        pass
    else:  # pragma: no cover - this is the canary's failure path
        raise AssertionError("invalid egress configuration must fail the poll")
    assert registry.session_kwargs == []


def test_poll_login_refusal_is_a_failed_attempt_not_an_empty_success() -> None:
    registry = _QueueRegistry(_Message("m-1", "must remain queued"))
    registry.login_code = 2202

    try:
        _poll(registry, "durable")
    except EppProtocolError:
        pass
    else:  # pragma: no cover - this is the canary's failure path
        raise AssertionError("login refusal must enter Integration failure handling")

    assert registry.acked == []
    assert [item.message_id for item in registry.messages] == ["m-1"]


def test_poll_passes_optional_client_pem_to_the_transport() -> None:
    registry = _QueueRegistry()

    events, next_cursor = _poll(
        registry,
        None,
        secrets={**_SECRETS, "client_pem": "test-only-pem"},
    )

    assert events == ()
    assert next_cursor is None
    assert registry.session_kwargs == [
        {
            "client_pem": "test-only-pem",
            "connect_timeout": 1.0,
            "read_timeout": 1.0,
        }
    ]


def test_next_call_acks_only_the_durable_cursor_then_returns_one_new_head() -> None:
    registry = _QueueRegistry(
        _Message("m-1", "first"),
        _Message("m-2", "second"),
        _Message("m-3", "third"),
    )
    first_events, durable_cursor = _poll(registry, None)
    assert len(first_events) == 1

    events, next_cursor = _poll(registry, durable_cursor)

    assert registry.acked == ["m-1"]
    assert [item.message_id for item in registry.messages] == ["m-2", "m-3"]
    assert len(events) == 1
    assert events[0].provider_event_id == "nira-msg:m-2"
    assert next_cursor == "m-2"


def test_crash_between_return_and_record_redelivers_without_ack() -> None:
    registry = _QueueRegistry(_Message("m-1", "must survive"))

    first_events, uncommitted_cursor = _poll(registry, None)
    assert uncommitted_cursor == "m-1"
    # Simulate a process crash before Integration records the returned batch:
    # the retry receives the old durable cursor, not `uncommitted_cursor`.
    retried_events, retried_cursor = _poll(registry, None)

    assert first_events[0].provider_event_id == retried_events[0].provider_event_id
    assert retried_cursor == "m-1"
    assert registry.acked == []
    assert [item.message_id for item in registry.messages] == ["m-1"]


def test_ambiguous_ack_response_is_recovered_by_reading_the_new_head() -> None:
    registry = _QueueRegistry(
        _Message("m-1", "durable"),
        _Message("m-2", "not durable yet"),
    )
    _, durable_cursor = _poll(registry, None)
    registry.fail_ack_response_once = True

    try:
        _poll(registry, durable_cursor)
    except EppTransportError:
        pass
    else:  # pragma: no cover - this is the canary's failure path
        raise AssertionError("ambiguous acknowledgement must remain a failed attempt")
    assert registry.acked == ["m-1"]

    # Integration did not advance the cursor for the failed call. The retry
    # reads m-2, sees that m-1 is already absent, and does not ack m-2 early.
    events, next_cursor = _poll(registry, durable_cursor)
    assert len(events) == 1
    assert events[0].provider_event_id == "nira-msg:m-2"
    assert next_cursor == "m-2"
    assert registry.acked == ["m-1"]
