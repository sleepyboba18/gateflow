from datetime import datetime, timedelta, timezone

from app.gateway.reliability import RETRYABLE_METHODS, request_with_retries
from app.services.upstream_health_service import classify_health


def test_health_classification():
    assert classify_health(100, 0) == "healthy"
    assert classify_health(96, 4) == "degraded"
    assert classify_health(90, 10) == "unhealthy"
    assert classify_health(0, 0) == "unknown"


def test_only_safe_methods_are_retryable():
    assert RETRYABLE_METHODS == {"GET", "HEAD", "OPTIONS"}
    assert "POST" not in RETRYABLE_METHODS


def test_retry_executor_stops_after_success():
    calls = []

    class Response:
        status_code = 200

    response, attempts, retries = request_with_retries(
        lambda **kwargs: (calls.append(kwargs) or Response()), "GET", 2, 0, __import__("time").monotonic() + 1
    )
    assert response.status_code == 200
    assert attempts == 1
    assert retries == 0
