from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.rate_limit import RateLimit
from app.models.rate_limit_counter import RateLimitCounter

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

    start = window_start(now, limit.window_seconds)
    reset_epoch = int(start.timestamp()) + limit.window_seconds
    counter_insert = insert(RateLimitCounter).values(
        rate_limit_id=limit.id, api_key_id=api_key_id, window_start=start, request_count=1
    )
    incremented_count = case(
        (RateLimitCounter.request_count < limit.requests, RateLimitCounter.request_count + 1),
        else_=RateLimitCounter.request_count,
    )
    statement = counter_insert.on_conflict_do_update(
        index_elements=["rate_limit_id", "api_key_id", "window_start"],
        set_={"request_count": incremented_count, "updated_at": func.now()},
    ).returning(RateLimitCounter.request_count)
    count = session.execute(statement).scalar_one()
    session.commit()
    allowed = count <= limit.requests
    remaining = max(limit.requests - count, 0)
    retry_after = max(reset_epoch - int(now.timestamp()), 1)
    return RateLimitDecision(
        allowed=allowed,
        limit=limit.requests,
        remaining=remaining,
        reset_at=reset_epoch,
        retry_after=None if allowed else retry_after,
        rate_limit_id=limit.id,
    )
