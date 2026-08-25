import json
import logging

from app import create_app
from app.observability.logger import JsonFormatter, redact_headers, redact_query
from app.observability.request_logging import valid_request_id


def test_request_id_validation_and_redaction():
    assert valid_request_id("request-123")
    assert not valid_request_id("bad\nvalue")
    assert redact_headers({"X-API-Key": "secret", "Accept": "application/json"})["X-API-Key"] == "[REDACTED]"
    assert redact_query("city=Kolkata&token=secret") == "city=Kolkata&token=[REDACTED]"


def test_health_endpoints_and_error_correlation():
    client = create_app().test_client()
    live = client.get("/health/live")
    missing = client.get("/does-not-exist")
    assert live.status_code == 200
    assert live.json == {"status": "ok"}
    assert missing.status_code == 404
    assert missing.json["request_id"]
    assert missing.headers["X-GateForge-Request-ID"] == missing.json["request_id"]


def test_json_formatter_emits_structured_event():
    record = logging.LogRecord("gateforge", logging.INFO, __file__, 1, "ignored", (), None)
    record.event = "test.event"
    record.fields = {"secret": "[REDACTED]"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "test.event"
    assert payload["secret"] == "[REDACTED]"