import asyncio
import json
import logging

from app.core.logging import JsonLogFormatter, request_id_var
from app.core.middleware.observability import ObservabilityMiddleware


def test_json_formatter_emits_request_id_and_fields():
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        payload = json.loads(JsonLogFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "req-123"
    assert "timestamp" in payload


def test_json_formatter_without_request_id():
    record = logging.LogRecord(
        name="app.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="plain",
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["request_id"] is None


def test_observability_completion_log_has_request_id_not_null():
    """Regression test: the "request" completion log must be emitted while
    request_id_var still holds the request's id.

    Drives one request through ObservabilityMiddleware and captures the
    "request" completion log by formatting it with JsonLogFormatter inside
    the handler's emit(), i.e. synchronously at the moment
    logger.info("request", ...) runs in the middleware's finally block.
    That is the only point at which the contextvar ordering bug is
    observable: if request_id_var.reset(token) runs before the log call,
    the formatted payload's request_id is None; if it runs after, the
    payload carries the real request id.
    """

    captured: list[str] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.name == "app.requests" and record.getMessage() == "request":
                captured.append(self.format(record))

    requests_logger = logging.getLogger("app.requests")
    handler = _CapturingHandler()
    handler.setFormatter(JsonLogFormatter())
    requests_logger.addHandler(handler)
    previous_level = requests_logger.level
    requests_logger.setLevel(logging.INFO)

    async def _ok_app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    app = ObservabilityMiddleware(_ok_app, trust_inbound_request_id=False)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        pass

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/ping",
        "raw_path": b"/ping",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }

    try:
        asyncio.run(app(scope, receive, send))
    finally:
        requests_logger.removeHandler(handler)
        requests_logger.setLevel(previous_level)

    assert len(captured) == 1
    payload = json.loads(captured[0])
    assert isinstance(payload["request_id"], str)
    assert payload["request_id"]
