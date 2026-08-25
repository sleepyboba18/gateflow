from datetime import datetime, timezone

from flask import Flask, request

from app.services.analytics_service import parse_period
from app.services.gateway_service import request_id_from


def test_analytics_periods_are_utc():
    assert parse_period("2026-08-25T00:00:00Z") == datetime(2026, 8, 25, tzinfo=timezone.utc)


def test_request_id_rejects_newlines():
    app = Flask(__name__)
    with app.test_request_context(headers={"X-Request-ID": "bad\nvalue"}):
        assert request_id_from(request) != "bad\nvalue"
