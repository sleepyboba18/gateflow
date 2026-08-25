from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.rate_limit import RateLimit
from app.models.rate_limit_counter import RateLimitCounter
from app.models.quota_counter import QuotaCounter

MAX_REQUESTS = 1_000_000
MAX_WINDOW_SECONDS = 31_536_000


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    retry_after: int | None = None
    rate_limit_id: UUID | None = None


class RateLimitConfigurationError(Exception):
    pass


def window_start(now: datetime, window_seconds: int) -> datetime:
    utc_now = now.astimezone(timezone.utc)
    epoch = int(utc_now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=timezone.utc)


def _applicable_limit(session: Session, api_id: UUID, route_id: UUID) -> RateLimit | None:
    route_limits = session.scalars(
        select(RateLimit).where(RateLimit.api_id == api_id, RateLimit.route_id == route_id, RateLimit.is_active.is_(True))
    ).all()
    if len(route_limits) > 1:
        raise RateLimitConfigurationError("Multiple active route rate limits configured")
    if route_limits:
        return route_limits[0]
    api_limits = session.scalars(
        select(RateLimit).where(RateLimit.api_id == api_id, RateLimit.route_id.is_(None), RateLimit.is_active.is_(True))
    ).all()
    if len(api_limits) > 1:
        raise RateLimitConfigurationError("Multiple active API rate limits configured")
    return api_limits[0] if api_limits else None


def check_rate_limit(session: Session, api_key_id: UUID, api_id: UUID, route_id: UUID, now: datetime | None = None) -> RateLimitDecision:
    now = now or datetime.now(timezone.utc)
    limit = _applicable_limit(session, api_id, route_id)
    if limit is None:
        return RateLimitDecision(allowed=True)

    return check_counter(session, api_key_id, limit.requests, limit.window_seconds, now, rate_limit_id=limit.id)


def check_counter(
    session: Session, api_key_id: UUID, maximum: int, window_seconds: int, now: datetime,
    rate_limit_id: UUID | None = None, plan_rate_limit_id: UUID | None = None,
) -> RateLimitDecision:
    if rate_limit_id is None and plan_rate_limit_id is None:
        raise ValueError("A rate-limit counter identity is required")
    start = window_start(now, window_seconds)
    reset_epoch = int(start.timestamp()) + window_seconds
    counter_insert = insert(RateLimitCounter).values(
        rate_limit_id=rate_limit_id, plan_rate_limit_id=plan_rate_limit_id,
        api_key_id=api_key_id, window_start=start, request_count=1
    )
    incremented_count = case(
        (RateLimitCounter.request_count < maximum, RateLimitCounter.request_count + 1),
        else_=RateLimitCounter.request_count,
    )
    statement = counter_insert.on_conflict_do_update(
        index_elements=["rate_limit_id", "api_key_id", "window_start"],
        set_={"request_count": incremented_count, "updated_at": func.now()},
    ).returning(RateLimitCounter.request_count)
    count = session.execute(statement).scalar_one()
    session.commit()
    allowed = count <= maximum
    remaining = max(maximum - count, 0)
    retry_after = max(reset_epoch - int(now.timestamp()), 1)
    return RateLimitDecision(
        allowed=allowed,
        limit=maximum,
        remaining=remaining,
        reset_at=reset_epoch,
        retry_after=None if allowed else retry_after,
        rate_limit_id=rate_limit_id or plan_rate_limit_id,
    )


def check_quota_counter(session: Session, api_key_id: UUID, quota_id: UUID, maximum: int, period_start: datetime, reset_at: datetime, now: datetime) -> RateLimitDecision:
    reset_epoch = int(reset_at.timestamp())
    counter_insert = insert(QuotaCounter).values(plan_quota_id=quota_id, api_key_id=api_key_id, period_start=period_start, request_count=1)
    incremented_count = case(
        (QuotaCounter.request_count < maximum, QuotaCounter.request_count + 1),
        else_=QuotaCounter.request_count,
    )
    statement = counter_insert.on_conflict_do_update(
        index_elements=["plan_quota_id", "api_key_id", "period_start"],
        set_={"request_count": incremented_count, "updated_at": func.now()},
    ).returning(QuotaCounter.request_count)
    count = session.execute(statement).scalar_one()
    session.commit()
    return RateLimitDecision(
        allowed=count <= maximum, limit=maximum, remaining=max(maximum - count, 0),
        reset_at=reset_epoch, retry_after=None if count <= maximum else max(reset_epoch - int(now.timestamp()), 1),
        rate_limit_id=quota_id,
    )
