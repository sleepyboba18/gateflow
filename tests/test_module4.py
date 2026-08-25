from datetime import datetime, timezone

from app.services.rate_limit_service import MAX_REQUESTS, MAX_WINDOW_SECONDS, window_start


def test_fixed_window_uses_utc_boundaries():
    current = datetime(2026, 8, 25, 10, 31, 42, tzinfo=timezone.utc)
    assert window_start(current, 60) == datetime(2026, 8, 25, 10, 31, tzinfo=timezone.utc)
    assert window_start(current, 3600) == datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def test_rate_limit_configuration_bounds_are_defined():
    assert MAX_REQUESTS == 1_000_000
    assert MAX_WINDOW_SECONDS == 31_536_000
