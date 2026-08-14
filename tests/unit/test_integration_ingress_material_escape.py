"""Request material escaping the ingress engine by a route the suite misses.

`test_integration_ingress.py` proves the REFUSAL path is clean: every
`IngressRefused` carries a constant message, `IngressOutcome` has no field a
body could travel in, and `headers_json` is never stored. All of that holds.

This file covers the path that is NOT a refusal — an exception the engine does
not convert, which therefore leaves `receive()` as itself and reaches whatever
the edge does with a 500. Two mechanisms carry material out along it:

* **the exception's own message.** SQLAlchemy's `StatementError.__str__`
  appends ``[SQL: ...] [parameters: (...)]`` unless the Engine was built with
  ``hide_parameters=True``, which nothing in this fleet sets. The parameters are
  the normalized payload and the provider event id, verbatim, in a string a
  plain ``logging.exception`` writes to an ERROR line.
* **the traceback's frame locals.** `verify_and_normalize`'s frame holds the
  MATERIALIZED SECRET. It is reachable from any escaping exception via
  ``exc.__traceback__.tb_frame.f_locals`` — which is exactly what an error
  reporter that captures locals uploads. A `BaseException` (an ordinary
  `CancelledError` from a disconnecting client) walks past `except Exception`
  and out, carrying that frame with it.

The module's own hygiene test asserts it installs no logger. It does not need
one: an escaping exception carries the material into the EDGE's logger instead.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    EventSubscription,
    InboxReceipt,
    IngressCode,
    IngressOutcome,
    PollingCheckpoint,
    add_binding,
    create_draft,
    enable,
    mint_ingress_endpoint,
    prepare_ingress,
    put_config_revision,
    receive,
    set_binding_enabled,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_plugin, fake_registry
from dotmac_integration.spi import InboundEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

BODY_SENTINEL = b'{"leak":"SENTINEL-RAW-BODY-8f2a"}'
PAYLOAD_SENTINEL = "SENTINEL-NORMALIZED-PAYLOAD-3d0c"
HEADER_SENTINEL = "SENTINEL-SIGNATURE-9c1d"
SECRET_SENTINEL = "SENTINEL-MATERIALIZED-SECRET-1a5f"

ALL_MODELS = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    EventSubscription,
    InboxReceipt,
    DeliveryAttempt,
    PollingCheckpoint,
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in ALL_MODELS:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


class Uow:
    """Commits on a clean exit, unwinds on an exception — as a deployment's."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __call__(self):  # type: ignore[no-untyped-def]
        return self._unit()

    @contextmanager
    def _unit(self) -> Iterator[Session]:
        try:
            yield self.session
        except BaseException:
            self.session.rollback()
            raise
        else:
            self.session.commit()


def _resolver():  # type: ignore[no-untyped-def]
    def resolve(refs):  # type: ignore[no-untyped-def]
        return {"signing": SECRET_SENTINEL}

    return resolve


def _headers() -> dict[str, str]:
    return {"signature": HEADER_SENTINEL, "content-type": "application/json"}


def _endpoint(db: Session, registry) -> tuple[Any, Any, str]:  # type: ignore[no-untyped-def]
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(
        db,
        installation,
        config={"variant": "a"},
        secret_refs={"signing": "bao://kv/signing"},
    )
    enable(db, installation, registry=registry)
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    key = mint_ingress_endpoint(db, binding, registry=registry)
    db.flush()
    return installation, binding, key


def _deliver(registry, db: Session, key: str):  # type: ignore[no-untyped-def]
    """Drive `receive`, returning the outcome or whatever it failed to convert.

    Only the TYPE NAME of an escaping exception is ever reported by the
    assertions below — a test that proves nothing leaks must not itself print
    the leak into a CI log.
    """
    try:
        return receive(
            Uow(db),
            endpoint_id=key,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )
    except BaseException as exc:
        return exc


def _typed(result: Any) -> IngressOutcome:
    assert isinstance(
        result, IngressOutcome
    ), f"{type(result).__name__} escaped receive() instead of a typed outcome"
    return result


def _locals_render(exc: BaseException) -> str:
    """The rendering an error reporter that captures frame locals produces."""
    return "".join(
        traceback.TracebackException(
            type(exc), exc, exc.__traceback__, capture_locals=True
        ).format()
    )


#: Locals the CALLER injected, excluded from the rendering below. `registry`
#: reaches the connector's whole object graph and `resolve_secrets` is the
#: caller's closure — what either of them chooses to retain is not this module's
#: to clear. (The conformance fake deliberately retains what it was handed, so a
#: test can assert what crossed the boundary; that is the kit, not the engine.)
_INJECTED = frozenset({"registry", "resolve_secrets"})


def _engine_frames(exc: BaseException) -> str:
    """The rendering an error reporter that captures frame locals produces,
    restricted to the bindings THIS MODULE makes in its OWN frames.

    The connector's own frame necessarily holds the secret — it is that
    function's argument, and a module that hands a plugin a materialized
    credential cannot then un-hand it. What the engine can be held to is what it
    binds itself: `verify_and_normalize` and `challenge_response` put the
    materialized dict in a local, and that local is pinned by every exception
    leaving them.
    """
    engine = "dotmac_integration/ingress.py"
    rendered: list[str] = []
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if engine in frame.f_code.co_filename.replace("\\", "/"):
            owned = {
                name: value
                for name, value in frame.f_locals.items()
                if name not in _INJECTED
            }
            rendered.append(f"{frame.f_code.co_name}: {owned!r}")
        tb = tb.tb_next
    assert rendered, "no frame of the ingress engine is in this traceback"
    assert any(
        "secrets" in line for line in rendered
    ), "no engine frame binds a `secrets` local — this proves nothing"
    return "\n".join(rendered)


def _registry_yielding(event: InboundEvent):  # type: ignore[no-untyped-def]
    return fake_registry(plugins=[fake_plugin(inbound=(event,))])


# ── The exception message ───────────────────────────────────────────────────


def test_a_database_error_becomes_a_typed_refusal_not_a_driver_message(
    db: Session,
) -> None:
    """A connector whose `normalize` omits an `event_type`.

    `record_batch` used to catch `IntegrityError` only to test it for the
    receipt uniqueness index, then bare-`raise` anything else — so a NOT NULL
    violation left the engine as a `sqlalchemy` error whose `str` embeds the
    whole bound parameter list, `payload_json` and `provider_event_id` included.
    """
    event = InboundEvent(
        provider_event_id="evt-1",
        event_type=None,  # type: ignore[arg-type]
        payload={"leak": PAYLOAD_SENTINEL},
    )
    registry = _registry_yielding(event)
    _, _, key = _endpoint(db, registry)

    outcome = _typed(_deliver(registry, db, key))

    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.RECEIPT_WRITE_FAILED,
    )
    assert PAYLOAD_SENTINEL not in repr(outcome)
    assert outcome.receipt_ids == ()


def test_a_serializer_error_is_not_even_an_integrity_error(db: Session) -> None:
    """A connector that normalizes a `datetime` into the payload.

    `payload_digest` survives it (`json.dumps(..., default=str)`); the JSON
    column serializer does not. The result is a bare `StatementError` — not a
    `DBAPIError`, not an `IntegrityError` — so widening the old catch to
    `IntegrityError` subclasses would still have missed it, and its `str`
    carries the same `[parameters: ...]`.
    """
    event = InboundEvent(
        provider_event_id="evt-1",
        event_type="thing.happened",
        payload={"leak": PAYLOAD_SENTINEL, "at": datetime.datetime(2026, 1, 1)},
    )
    registry = _registry_yielding(event)
    _, _, key = _endpoint(db, registry)

    outcome = _typed(_deliver(registry, db, key))

    assert outcome.code is IngressCode.RECEIPT_WRITE_FAILED
    assert PAYLOAD_SENTINEL not in repr(outcome)


def test_a_blank_provider_event_id_is_a_typed_refusal(db: Session) -> None:
    """`receive_verified` raises `ExecutionError` for a blank event id.

    Its MESSAGE is a constant, so nothing leaks along this one — but it is
    untyped to the edge, which is both a breach of "typed outcomes and error
    codes" and what makes the family above reachable at all. Same catch, same
    conversion.
    """
    event = InboundEvent(
        provider_event_id="   ", event_type="thing.happened", payload={"a": 1}
    )
    registry = _registry_yielding(event)
    _, _, key = _endpoint(db, registry)

    outcome = _typed(_deliver(registry, db, key))
    assert outcome.code is IngressCode.RECEIPT_WRITE_FAILED


def test_a_failed_write_reaches_no_error_log_with_provider_content(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """The finding in its most concrete form: not a hypothetical reporter, but
    `logging.exception` in the 500 handler every edge has.

    A typed outcome is RETURNED, so the edge's `except` never runs and there is
    no ERROR line at all — which is the only durable way a module that installs
    no logger can keep provider content out of one.
    """
    event = InboundEvent(
        provider_event_id="evt-1",
        event_type=None,  # type: ignore[arg-type]
        payload={"leak": PAYLOAD_SENTINEL},
    )
    registry = _registry_yielding(event)
    _, _, key = _endpoint(db, registry)

    with caplog.at_level(logging.ERROR):
        try:
            receive(
                Uow(db),
                endpoint_id=key,
                raw_body=BODY_SENTINEL,
                headers=_headers(),
                registry=registry,
                resolve_secrets=_resolver(),
            )
        except Exception:
            logging.getLogger("edge").exception("unhandled ingress error")

    assert (
        PAYLOAD_SENTINEL not in caplog.text
    ), "normalized provider content reached an ERROR log line"
    assert caplog.text == "", "the edge logged an unhandled error for a typed refusal"


def test_a_failed_write_leaves_the_whole_batch_unwritten(db: Session) -> None:
    """The conversion must not cost the atomicity it sits inside.

    `record_batch` raises the refusal, `receive` catches it OUTSIDE the `with`,
    and the deployment's unit of work unwinds — so a batch whose second event
    fails to write leaves nothing behind, exactly as a collision does.
    """
    good = InboundEvent(
        provider_event_id="evt-good", event_type="thing.happened", payload={"i": 1}
    )
    bad = InboundEvent(
        provider_event_id="evt-bad",
        event_type=None,  # type: ignore[arg-type]
        payload={"i": 2},
    )
    registry = fake_registry(plugins=[fake_plugin(inbound=(good, bad))])
    _, _, key = _endpoint(db, registry)
    db.commit()

    outcome = _typed(_deliver(registry, db, key))

    assert outcome.code is IngressCode.RECEIPT_WRITE_FAILED
    db.rollback()
    stored = db.query(InboxReceipt).count()
    assert stored == 0, "an event recorded before the failed write survived"


# ── The traceback's frame locals ────────────────────────────────────────────


def test_a_base_exception_from_the_plugin_carries_no_secret_out(
    db: Session,
) -> None:
    """`verify_and_normalize` catches `Exception`, so a `BaseException` walks.

    `asyncio.CancelledError` is a `BaseException` and is the ORDINARY event on a
    webhook endpoint: the client disconnects, or the server times the request
    out, while the connector is inside `verify`. It must keep walking — swallowing
    a cancellation is worse than the leak — but the frame it pins must no longer
    hold the materialized signing key in plaintext.
    """
    registry = fake_registry(
        plugins=[fake_plugin(ingress_raises=asyncio.CancelledError("cancelled"))]
    )
    _, _, key = _endpoint(db, registry)

    escaped = _deliver(registry, db, key)

    assert isinstance(
        escaped, asyncio.CancelledError
    ), "a cancellation must propagate, not be converted"
    assert SECRET_SENTINEL not in _engine_frames(
        escaped
    ), "the materialized secret is reachable through the engine's own frame"


def test_a_raising_connector_leaves_no_secret_in_the_refusal_traceback(
    db: Session,
) -> None:
    """The same frame, on the path the engine DOES convert.

    `ConnectorRaised` is raised from inside `verify_and_normalize`, so its
    traceback pins that frame too — a reporter capturing locals on the 503 path
    would upload the key without any exotic exception being involved.
    """
    registry = fake_registry(plugins=[fake_plugin(ingress_raises=ValueError("nope"))])
    _, _, key = _endpoint(db, registry)

    from dotmac_integration import verify_and_normalize
    from dotmac_integration.ingress import IngressRefused

    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)
    try:
        verify_and_normalize(
            prepared,
            raw_body=BODY_SENTINEL,
            headers=_headers(),
            registry=registry,
            resolve_secrets=_resolver(),
        )
    except IngressRefused as exc:
        assert SECRET_SENTINEL not in _engine_frames(exc)
    else:  # pragma: no cover - the fake is configured to raise
        raise AssertionError("the connector did not raise")


def test_the_handshake_phase_clears_its_secret_too(db: Session) -> None:
    """`challenge_response` materializes the same secret into the same shape of
    local, and a handshake is exactly when a misconfigured connector throws."""
    registry = fake_registry(plugins=[fake_plugin(ingress_raises=ValueError("nope"))])
    _, _, key = _endpoint(db, registry)

    from dotmac_integration.ingress import IngressRefused, challenge_response

    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)
    try:
        challenge_response(
            prepared,
            params={"challenge": "x"},
            registry=registry,
            resolve_secrets=_resolver(),
        )
    except IngressRefused as exc:
        assert SECRET_SENTINEL not in _engine_frames(exc)
    else:  # pragma: no cover - the fake is configured to raise
        raise AssertionError("the connector did not raise")


def test_the_endpoint_key_is_not_rendered_by_the_carrier(db: Session) -> None:
    """`IngressOutcome` deliberately omits the endpoint key — "whoever holds it
    can drive the connector's verify". `PreparedIngress` holds it and rendered it
    in full through the default dataclass `repr`, so it landed in any log line or
    traceback frame that touched the carrier — and the carrier is a frame local
    in every escaping traceback above.
    """
    registry = fake_registry()
    _, _, key = _endpoint(db, registry)
    prepared = prepare_ingress(db, endpoint_id=key, registry=registry)

    assert key not in repr(prepared), "the carrier's repr prints the endpoint key"
    # The key is still THERE — this is a rendering rule, not a removal.
    assert prepared.endpoint_key == key
