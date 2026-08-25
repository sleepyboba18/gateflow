from datetime import datetime, timezone

from app.services.policy_service import quota_period_start
from app.services.rate_limit_service import window_start


def test_daily_and_monthly_periods_are_utc():
    current = datetime(2026, 8, 25, 18, 42, tzinfo=timezone.utc)
    assert quota_period_start(current, "daily") == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert quota_period_start(current, "monthly") == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_policy_window_and_burst_boundaries():
    current = datetime(2026, 8, 25, 10, 31, 42, tzinfo=timezone.utc)
    assert window_start(current, 1).second == 42
    assert window_start(current, 60).minute == 31
