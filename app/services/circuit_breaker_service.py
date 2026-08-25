from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.circuit_breaker import CircuitBreaker

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    state: str | None
    retry_after: int | None = None
    changed: bool = False
    previous_state: str | None = None


def get_effective_breaker(session: Session, api_id: UUID, route_id: UUID) -> CircuitBreaker | None:
    route = session.scalar(select(CircuitBreaker).where(CircuitBreaker.api_id == api_id, CircuitBreaker.route_id == route_id, CircuitBreaker.is_active.is_(True)).with_for_update())
    if route is not None:
        return route
    return session.scalar(select(CircuitBreaker).where(CircuitBreaker.api_id == api_id, CircuitBreaker.route_id.is_(None), CircuitBreaker.is_active.is_(True)).with_for_update())


def can_request(session: Session, api_id: UUID, route_id: UUID, now: datetime | None = None) -> CircuitDecision:
    breaker = get_effective_breaker(session, api_id, route_id)
    if breaker is None:
        return CircuitDecision(True, None)
    now = now or datetime.now(timezone.utc)
    if breaker.state == OPEN:
        opened_at = breaker.opened_at or now
        elapsed = (now - opened_at).total_seconds()
        if elapsed < breaker.recovery_timeout_seconds:
            return CircuitDecision(False, OPEN, max(int(breaker.recovery_timeout_seconds - elapsed), 1))
        previous = breaker.state
        breaker.state = HALF_OPEN
        breaker.success_count = 1
        session.flush()
        return CircuitDecision(True, HALF_OPEN, changed=True, previous_state=previous)
    if breaker.state == HALF_OPEN and breaker.success_count >= breaker.half_open_max_requests:
        return CircuitDecision(False, HALF_OPEN, 1)
    return CircuitDecision(True, breaker.state)


def record_success(session: Session, breaker: CircuitBreaker | None, now: datetime | None = None) -> CircuitDecision:
    if breaker is None:
        return CircuitDecision(True, None)
    now = now or datetime.now(timezone.utc)
    previous = breaker.state
    breaker.last_success_at = now
    breaker.success_count += 1
    breaker.failure_count = 0
    if breaker.state == HALF_OPEN:
        breaker.state = CLOSED
        breaker.success_count = 0
        breaker.opened_at = None
    session.commit()
    return CircuitDecision(True, breaker.state, changed=previous != breaker.state, previous_state=previous)


def record_failure(session: Session, breaker: CircuitBreaker | None, now: datetime | None = None) -> CircuitDecision:
    if breaker is None:
        return CircuitDecision(False, None)
    now = now or datetime.now(timezone.utc)
    previous = breaker.state
    breaker.last_failure_at = now
    breaker.failure_count += 1
    breaker.success_count = 0
    if breaker.state == HALF_OPEN or breaker.failure_count >= breaker.failure_threshold:
        breaker.state = OPEN
        breaker.opened_at = now
    session.commit()
    return CircuitDecision(False, breaker.state, changed=previous != breaker.state, previous_state=previous)
