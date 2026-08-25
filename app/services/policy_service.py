from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.api import API
from app.models.api_key import APIKey
from app.models.api_key_plan import APIKeyPlan
from app.models.api_route import APIRoute
from app.models.plan import Plan
from app.models.plan_quota import PlanQuota
from app.models.plan_rate_limit import PlanRateLimit
from app.models.rate_limit import RateLimit
from app.services.rate_limit_service import RateLimitDecision, check_counter, check_quota_counter, window_start


class PolicyConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    retry_after: int | None = None
    policy_type: str | None = None
    plan_id: UUID | None = None
    quota: bool = False


@dataclass(frozen=True)
class EffectivePolicy:
    plan: Plan
    rate_limits: list[PlanRateLimit | RateLimit]
    quotas: list[PlanQuota]


def ensure_default_plan(session: Session) -> Plan:
    plan = session.scalar(select(Plan).where(Plan.slug == "free"))
    if plan is None:
        plan = Plan(name="Free", slug="free", description="Default plan", is_active=True, is_default=True)
        session.add(plan)
        session.flush()
        session.add(PlanRateLimit(plan_id=plan.id, name="Per minute", requests=60, window_seconds=60, scope="api"))
        session.add(PlanRateLimit(plan_id=plan.id, name="Burst", requests=10, window_seconds=1, scope="api"))
        session.add(PlanQuota(plan_id=plan.id, name="Daily requests", limit=10_000, period="daily"))
        session.add(PlanQuota(plan_id=plan.id, name="Monthly requests", limit=100_000, period="monthly"))
        session.commit()
    return plan


def resolve_effective_policy(session: Session, api_key: APIKey, api: API, route: APIRoute, now: datetime | None = None) -> EffectivePolicy:
    now = now or datetime.now(timezone.utc)
    assignment = session.scalar(
        select(APIKeyPlan).options(selectinload(APIKeyPlan.plan)).where(
            APIKeyPlan.api_key_id == api_key.id, APIKeyPlan.is_active.is_(True),
            APIKeyPlan.started_at <= now,
            (APIKeyPlan.expires_at.is_(None) | (APIKeyPlan.expires_at > now)),
        ).order_by(APIKeyPlan.started_at.desc())
    )
    plan = assignment.plan if assignment and assignment.plan.is_active else session.scalar(
        select(Plan).where(Plan.is_active.is_(True), Plan.is_default.is_(True))
    )
    if plan is None:
        raise PolicyConfigurationError("No active default plan is configured")
    route_custom = session.scalars(select(RateLimit).where(RateLimit.api_id == api.id, RateLimit.route_id == route.id, RateLimit.is_active.is_(True))).all()
    api_custom = session.scalars(select(RateLimit).where(RateLimit.api_id == api.id, RateLimit.route_id.is_(None), RateLimit.is_active.is_(True))).all()
    if len(route_custom) > 1 or len(api_custom) > 1:
        raise PolicyConfigurationError("Ambiguous custom rate-limit configuration")
    custom = route_custom or api_custom
    plan_limits = session.scalars(select(PlanRateLimit).where(PlanRateLimit.plan_id == plan.id, PlanRateLimit.is_active.is_(True))).all()
    quotas = session.scalars(select(PlanQuota).where(PlanQuota.plan_id == plan.id, PlanQuota.is_active.is_(True))).all()
    custom_windows = {item.window_seconds for item in custom}
    effective_plan_limits = [item for item in plan_limits if item.window_seconds not in custom_windows]
    return EffectivePolicy(plan=plan, rate_limits=custom + effective_plan_limits, quotas=quotas)


def quota_period_start(now: datetime, period: str) -> datetime:
    current = now.astimezone(timezone.utc)
    if period == "daily":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def evaluate_policy(session: Session, api_key_id: UUID, policy: EffectivePolicy, now: datetime | None = None) -> PolicyDecision:
    now = now or datetime.now(timezone.utc)
    restrictive: PolicyDecision | None = None
    rejected: PolicyDecision | None = None
    for item in policy.rate_limits:
        decision = check_counter(session, api_key_id, item.requests, item.window_seconds, now, rate_limit_id=item.id if isinstance(item, RateLimit) else None, plan_rate_limit_id=item.id if isinstance(item, PlanRateLimit) else None)
        candidate = PolicyDecision(decision.allowed, decision.limit, decision.remaining, decision.reset_at, decision.retry_after, _policy_type(item.window_seconds), policy.plan.id)
        if restrictive is None or (candidate.remaining is not None and candidate.remaining < (restrictive.remaining if restrictive.remaining is not None else candidate.remaining)):
            restrictive = candidate
        if not candidate.allowed and rejected is None:
            rejected = candidate
    for quota in policy.quotas:
        period_start = quota_period_start(now, quota.period)
        if quota.period == "daily":
            reset_at = period_start + timedelta(days=1)
        else:
            reset_at = (period_start + timedelta(days=32)).replace(day=1)
        decision = check_quota_counter(session, api_key_id, quota.id, quota.limit, period_start, reset_at, now)
        candidate = PolicyDecision(decision.allowed, decision.limit, decision.remaining, decision.reset_at, decision.retry_after, quota.period, policy.plan.id, quota=True)
        if not candidate.allowed and rejected is None:
            rejected = candidate
    return rejected or restrictive or PolicyDecision(True, plan_id=policy.plan.id)


def _policy_type(window_seconds: int) -> str:
    return {1: "burst", 60: "minute", 3600: "hour", 86400: "day"}.get(window_seconds, f"window_{window_seconds}")
