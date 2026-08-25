from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.upstream_health import UpstreamHealth

HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"


def classify_health(successes: int, failures: int) -> str:
    total = successes + failures
    if total == 0:
        return UNKNOWN
    success_rate = successes / total
    if success_rate >= 0.99:
        return HEALTHY
    if success_rate >= 0.95:
        return DEGRADED
    return UNHEALTHY


def get_health(session: Session, api_id: UUID, route_id: UUID | None = None) -> UpstreamHealth | None:
    return session.scalar(select(UpstreamHealth).where(UpstreamHealth.api_id == api_id, UpstreamHealth.route_id == route_id))


def record_result(session: Session, api_id: UUID, route_id: UUID, success: bool, latency_ms: float, status_code: int | None) -> UpstreamHealth:
    now = datetime.now(timezone.utc)
    health = get_health(session, api_id, route_id)
    if health is None:
        health = UpstreamHealth(api_id=api_id, route_id=route_id)
        session.add(health)
    health.last_checked_at = now
    health.last_status_code = status_code
    health.average_latency_ms = latency_ms if health.average_latency_ms is None else (health.average_latency_ms * 0.8) + (latency_ms * 0.2)
    if success:
        health.last_success_at = now
        health.consecutive_successes += 1
        health.consecutive_failures = 0
    else:
        health.last_failure_at = now
        health.consecutive_failures += 1
        health.consecutive_successes = 0
    health.state = classify_health(health.consecutive_successes, health.consecutive_failures)
    session.commit()
    return health
