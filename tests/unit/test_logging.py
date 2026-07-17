import json
import logging

from app.core.logging import JsonLogFormatter, request_id_var


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
