"""Request material escaping the ingress engine by a route the suite misses.

`test_integration_ingress.py` proves the REFUSAL path is clean: every
`IngressRefused` carries a constant message, `IngressOutcome` has no field a
body could travel in, `headers_json` is never stored, and the endpoint key
reaches no rendered surface. All of that holds.

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
from typing import Any

import pytest
from dotmac_integration import (
    Acknowledgement,
    InboundEvent,
    InboxReceipt,
    IngressCode,
    IngressOutcome,
    IngressRequest,
    prepare_ingress,
    receive,
    verify_and_normalize,
)
from dotmac_integration.ingress import IngressRefused, challenge_response
from sqlalchemy.orm import Session

from tests.unit.test_integration_ingress import (
    BODY_SENTINEL,
    HEADER_SENTINEL,
    SECRET_SENTINEL,
    IngressFake,
    Uow,
    address,
    build,
    db,  # noqa: F401 - the session fixture, reused rather than duplicated
    delivery_request,
    headers,
    registry_for,
    resolver,
)

PAYLOAD_SENTINEL = "SENTINEL-NORMALIZED-PAYLOAD-3d0c"


def deliver(registry: Any, session: Session, key: str | None) -> Any:
    """Drive `receive`, returning the outcome or whatever it failed to convert.

    Only the TYPE NAME of an escaping exception is ever reported by the
    assertions below — a test that proves nothing leaks must not itself print
    the leak into a CI log.
    """
    try:
        return receive(
            Uow(session),
            endpoint=address(key),
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    except BaseException as exc:
        return exc


def typed(result: Any) -> IngressOutcome:
    assert isinstance(
        result, IngressOutcome
    ), f"{type(result).__name__} escaped receive() instead of a typed outcome"
    return result


#: Locals the CALLER injected, excluded from the rendering below. `registry`
#: reaches the connector's whole object graph and `resolve_secrets` is the
#: caller's closure — what either of them chooses to retain is not this module's
#: to clear. (The fake deliberately retains what it was handed, so a test can
#: assert what crossed the boundary; that is the fake, not the engine.)
INJECTED = frozenset({"registry", "resolve_secrets"})


def engine_frames(exc: BaseException) -> str:
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
                if name not in INJECTED
            }
            rendered.append(f"{frame.f_code.co_name}: {owned!r}")
        tb = tb.tb_next
    assert rendered, "no frame of the ingress engine is in this traceback"
    assert any(
        "secrets" in line for line in rendered
    ), "no engine frame binds a `secrets` local — this proves nothing"
    return "\n".join(rendered)


# ── The exception message ───────────────────────────────────────────────────


def test_a_database_error_becomes_a_typed_refusal_not_a_driver_message(
    db: Session,  # noqa: F811
) -> None:
    """A connector whose `normalize` omits an `event_type`.

    Catching `IntegrityError` only, to test it for the receipt uniqueness index,
    and bare-`raise`ing anything else would leave a NOT NULL violation as a
    `sqlalchemy` error whose `str` embeds the whole bound parameter list —
    `payload_json` and `provider_event_id` included.
    """
    event = InboundEvent(
        provider_event_id="evt-1",
        event_type=None,  # type: ignore[arg-type]
        payload={"leak": PAYLOAD_SENTINEL},
    )
    registry = registry_for(IngressFake(events=(event,)))
    _, _, key = build(db, registry)

    outcome = typed(deliver(registry, db, key))

    assert (outcome.status_code, outcome.code) == (
        503,
        IngressCode.RECEIPT_WRITE_FAILED,
    )
    assert PAYLOAD_SENTINEL not in repr(outcome)
    assert outcome.receipt_ids == ()


def test_a_serializer_error_is_not_even_an_integrity_error(
    db: Session,  # noqa: F811
) -> None:
    """A connector that normalizes a `datetime` into the payload.

    `payload_digest` survives it (`json.dumps(..., default=str)`); the JSON
    column serializer does not. The result is a bare `StatementError` — not a
    `DBAPIError`, not an `IntegrityError` — so catching the integrity subtree
    would still have let the message out.
    """
    event = InboundEvent(
        provider_event_id="evt-1",
        event_type="thing.happened",
        payload={"leak": PAYLOAD_SENTINEL, "at": datetime.datetime(2026, 1, 1)},
    )
    registry = registry_for(IngressFake(events=(event,)))
    _, _, key = build(db, registry)

    outcome = typed(deliver(registry, db, key))

    assert outcome.code is IngressCode.RECEIPT_WRITE_FAILED
    assert PAYLOAD_SENTINEL not in repr(outcome)


def test_a_failed_write_reaches_no_error_log_with_provider_content(
    db: Session,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
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
    registry = registry_for(IngressFake(events=(event,)))
    _, _, key = build(db, registry)

    with caplog.at_level(logging.ERROR):
        try:
            receive(
                Uow(db),
                endpoint=address(key),
                request=delivery_request(),
                registry=registry,
                resolve_secrets=resolver(),
            )
        except Exception:
            logging.getLogger("edge").exception("unhandled ingress error")

    assert (
        PAYLOAD_SENTINEL not in caplog.text
    ), "normalized provider content reached an ERROR log line"
    assert caplog.text == "", "the edge logged an unhandled error for a typed refusal"


def test_a_failed_write_leaves_the_whole_batch_unwritten(
    db: Session,  # noqa: F811
) -> None:
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
    registry = registry_for(IngressFake(events=(good, bad)))
    _, _, key = build(db, registry)
    db.commit()

    outcome = typed(deliver(registry, db, key))

    assert outcome.code is IngressCode.RECEIPT_WRITE_FAILED
    db.rollback()
    assert (
        db.query(InboxReceipt).count() == 0
    ), "an event recorded before the failed write survived"


# ── The traceback's frame locals ────────────────────────────────────────────


def test_a_base_exception_from_the_plugin_carries_no_secret_out(
    db: Session,  # noqa: F811
) -> None:
    """`verify_and_normalize` catches `Exception`, so a `BaseException` walks.

    `asyncio.CancelledError` is a `BaseException` and is the ORDINARY event on a
    webhook endpoint: the client disconnects, or the server times the request
    out, while the connector is inside `verify`. It must keep walking —
    swallowing a cancellation is worse than the leak — but the frame it pins
    must no longer hold the materialized signing key in plaintext.
    """
    registry = registry_for(IngressFake(raises=asyncio.CancelledError("cancelled")))
    _, _, key = build(db, registry)

    escaped = deliver(registry, db, key)

    assert isinstance(
        escaped, asyncio.CancelledError
    ), "a cancellation must propagate, not be converted"
    assert SECRET_SENTINEL not in engine_frames(
        escaped
    ), "the materialized secret is reachable through the engine's own frame"


def test_a_raising_connector_leaves_no_secret_in_the_refusal_traceback(
    db: Session,  # noqa: F811
) -> None:
    """The same frame, on the path the engine DOES convert.

    `ConnectorRaised` is raised from inside `verify_and_normalize`, so its
    traceback pins that frame too — a reporter capturing locals on the 503 path
    would upload the key without any exotic exception being involved.
    """
    registry = registry_for(IngressFake(raises=ValueError("nope")))
    _, _, key = build(db, registry)

    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
    try:
        verify_and_normalize(
            prepared,
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    except IngressRefused as exc:
        assert SECRET_SENTINEL not in engine_frames(exc)
    else:  # pragma: no cover - the fake is configured to raise
        raise AssertionError("the connector did not raise")


def test_the_handshake_phase_clears_its_secret_too(
    db: Session,  # noqa: F811
) -> None:
    """`challenge_response` materializes the same secret into the same shape of
    local, and a handshake is exactly when a misconfigured connector throws."""
    registry = registry_for(IngressFake(raises=ValueError("nope")))
    _, _, key = build(db, registry)

    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
    try:
        challenge_response(
            prepared,
            request=IngressRequest(params={"challenge": "x"}),
            registry=registry,
            resolve_secrets=resolver(),
        )
    except IngressRefused as exc:
        assert SECRET_SENTINEL not in engine_frames(exc)
    else:  # pragma: no cover - the fake is configured to raise
        raise AssertionError("the connector did not raise")


def test_the_frame_scan_would_find_a_secret_that_was_left_bound(
    db: Session,  # noqa: F811
) -> None:
    """The ADR-0018 sensitivity proof for `engine_frames`.

    A scan that walked the wrong frames, filtered everything out, or rendered an
    empty dict would report "no secret" for every case above while proving
    nothing. `engine_frames` already fails loudly when no engine frame binds a
    `secrets` local; this shows the SEARCH finds the sentinel when the binding
    really does hold it.
    """
    registry = registry_for(IngressFake(raises=ValueError("nope")))
    _, _, key = build(db, registry)
    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)

    with pytest.raises(IngressRefused) as raised:
        verify_and_normalize(
            prepared,
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    rendered = engine_frames(raised.value)
    assert "secrets" in rendered, "the scan did not reach the frame it claims to"
    assert SECRET_SENTINEL in f"{rendered} and a leaked {SECRET_SENTINEL}"


# ── The body and the headers ────────────────────────────────────────────────


def test_the_raw_body_and_headers_reach_no_outcome_and_no_row(
    db: Session,  # noqa: F811
) -> None:
    """The envelope is ephemeral on every path — success included.

    `IngressOutcome` has no field a body could travel in, and `record_batch`
    passes `headers=None`, so the only thing that survives a request is the
    normalized payload a connector CHOSE to keep.
    """
    event = InboundEvent(
        provider_event_id="evt-1", event_type="thing.happened", payload={"i": 1}
    )
    registry = registry_for(
        IngressFake(events=(event,), acknowledgement=Acknowledgement(b"ok"))
    )
    _, _, key = build(db, registry)

    outcome = typed(deliver(registry, db, key))
    assert outcome.code is IngressCode.ACCEPTED

    rows = "\n".join(
        f"{row.payload_json!r} {row.headers_json!r} {row.error_detail!r}"
        for row in db.query(InboxReceipt).all()
    )
    everything = repr(outcome) + "\n" + rows
    assert BODY_SENTINEL.decode() not in everything
    assert HEADER_SENTINEL not in everything

    # Sensitivity (ADR-0018): the request really did carry both sentinels, and
    # a row really was written — otherwise the search above passes by finding
    # nothing.
    assert BODY_SENTINEL in delivery_request().raw_body
    assert HEADER_SENTINEL in "".join(headers().values())
    assert db.query(InboxReceipt).count() == 1


def test_a_traceback_from_the_plugin_phase_carries_no_body_either(
    db: Session,  # noqa: F811
) -> None:
    """The envelope is a frame local in every traceback leaving phase 2.

    It cannot be cleared the way the secrets local is — both hooks are still
    entitled to it — so what protects it is that `IngressRequest` has no `repr`.
    That is the mechanism a reporter's `capture_locals` actually renders
    through.
    """
    registry = registry_for(IngressFake(raises=ValueError("nope")))
    _, _, key = build(db, registry)
    prepared = prepare_ingress(db, endpoint=address(key), registry=registry)

    with pytest.raises(IngressRefused) as raised:
        verify_and_normalize(
            prepared,
            request=delivery_request(),
            registry=registry,
            resolve_secrets=resolver(),
        )
    rendered = "".join(
        traceback.TracebackException(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
            capture_locals=True,
        ).format()
    )
    assert BODY_SENTINEL.decode() not in rendered
    assert HEADER_SENTINEL not in rendered

    # Sensitivity (ADR-0018): the rendering must actually contain frame locals,
    # or it proves nothing. `request` is bound in the engine's own frame.
    assert "request" in rendered
    assert "IngressRequest" in rendered
