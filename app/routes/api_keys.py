from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_db
from app.middleware.auth import require_auth
from app.models.api_key_plan import APIKeyPlan
from app.models.api_key import APIKey
from app.models.plan import Plan
from app.models.plan_rate_limit import PlanRateLimit
from app.models.plan_quota import PlanQuota
from app.models.quota_counter import QuotaCounter
from app.models.rate_limit_counter import RateLimitCounter
from app.models.traffic_log import TrafficLog
from app.services.rate_limit_service import window_start
from app.services.api_key_service import generate_api_key, get_owned_api_key, list_user_api_keys, revoke_api_key


api_keys_bp = Blueprint("api_keys", __name__)


def _error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


def _timestamp(value):
    return value.isoformat() if value else None


def _key_response(api_key, include_plaintext: str | None = None) -> dict:
    response = {
        "id": str(api_key.id),
        "name": api_key.name,
        "key_prefix": api_key.key_prefix,
        "is_active": api_key.is_active,
        "expires_at": _timestamp(api_key.expires_at),
        "last_used_at": _timestamp(api_key.last_used_at),
        "created_at": _timestamp(api_key.created_at),
        "revoked_at": _timestamp(api_key.revoked_at),
    }
    if include_plaintext is not None:
        response["key"] = include_plaintext
    return response


def _parse_expiration(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@api_keys_bp.post("")
@require_auth
def create_key():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error("Invalid request", 400)
    try:
        expires_at = _parse_expiration(data.get("expires_at"))
    except (TypeError, ValueError):
        return _error("Invalid request", 400)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return _error("Invalid request", 400)

    session = next(get_db())
    try:
        api_key, plaintext_key = generate_api_key(session, g.current_user.id, name.strip(), expires_at)
        session.commit()
        session.refresh(api_key)
        return jsonify({"message": "API key created successfully", "api_key": _key_response(api_key, plaintext_key)}), 201
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to create API key", 500)
    finally:
        session.close()


@api_keys_bp.get("")
@require_auth
def list_keys():
    session = next(get_db())
    try:
        keys = list_user_api_keys(session, g.current_user.id)
        return jsonify({"api_keys": [_key_response(api_key) for api_key in keys]}), 200
    finally:
        session.close()


@api_keys_bp.delete("/<api_key_id>")
@require_auth
def delete_key(api_key_id: str):
    try:
        parsed_id = UUID(api_key_id)
    except ValueError:
        return _error("API key not found", 404)
    session = next(get_db())
    try:
        api_key = get_owned_api_key(session, g.current_user.id, parsed_id)
        if api_key is None:
            return _error("API key not found", 404)
        revoke_api_key(session, api_key)
        return jsonify({"message": "API key revoked successfully"}), 200
    finally:
        session.close()


def _owned_or_admin(api_key, user):
    return api_key is not None and (api_key.user_id == user.id or user.is_admin)


@api_keys_bp.post("/<api_key_id>/plan")
@require_auth
def assign_plan(api_key_id: str):
    try:
        parsed_id = UUID(api_key_id)
    except ValueError:
        return _error("API key not found", 404)
    data = request.get_json(silent=True) or {}
    try:
        plan_id = UUID(data.get("plan_id"))
    except (ValueError, TypeError):
        return _error("Invalid request", 400)
    session = next(get_db())
    try:
        api_key = session.get(APIKey, parsed_id)
        plan = session.get(Plan, plan_id)
        if not _owned_or_admin(api_key, g.current_user):
            return _error("API key not found", 404)
        if plan is None or not plan.is_active:
            return _error("Plan not found", 404)
        session.query(APIKeyPlan).filter(APIKeyPlan.api_key_id == api_key.id, APIKeyPlan.is_active.is_(True)).update({APIKeyPlan.is_active: False}, synchronize_session=False)
        assignment = APIKeyPlan(api_key_id=api_key.id, plan_id=plan.id)
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return jsonify({"plan": {"id": str(plan.id), "name": plan.name, "slug": plan.slug, "started_at": _timestamp(assignment.started_at), "expires_at": _timestamp(assignment.expires_at)}}), 201
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to assign plan", 500)
    finally:
        session.close()


@api_keys_bp.get("/<api_key_id>/plan")
@require_auth
def current_plan(api_key_id: str):
    try:
        parsed_id = UUID(api_key_id)
    except ValueError:
        return _error("API key not found", 404)
    session = next(get_db())
    try:
        api_key = session.get(APIKey, parsed_id)
        if not _owned_or_admin(api_key, g.current_user):
            return _error("API key not found", 404)
        now = datetime.now(timezone.utc)
        assignment = session.scalar(select(APIKeyPlan).where(APIKeyPlan.api_key_id == parsed_id, APIKeyPlan.is_active.is_(True), APIKeyPlan.started_at <= now, (APIKeyPlan.expires_at.is_(None) | (APIKeyPlan.expires_at > now))).order_by(APIKeyPlan.started_at.desc()))
        plan = assignment.plan if assignment and assignment.plan.is_active else session.scalar(select(Plan).where(Plan.is_active.is_(True), Plan.is_default.is_(True)))
        if plan is None:
            return _error("No active plan configured", 503)
        return jsonify({"plan": {"id": str(plan.id), "name": plan.name, "slug": plan.slug, "started_at": _timestamp(assignment.started_at) if assignment else None, "expires_at": _timestamp(assignment.expires_at) if assignment else None}}), 200
    finally:
        session.close()


@api_keys_bp.get("/<api_key_id>/usage")
@require_auth
def key_usage(api_key_id: str):
    try:
        parsed_id = UUID(api_key_id)
    except ValueError:
        return _error("API key not found", 404)
    session = next(get_db())
    try:
        api_key = session.get(APIKey, parsed_id)
        if not _owned_or_admin(api_key, g.current_user):
            return _error("API key not found", 404)
        now = datetime.now(timezone.utc)
        assignment = session.scalar(select(APIKeyPlan).where(APIKeyPlan.api_key_id == parsed_id, APIKeyPlan.is_active.is_(True), APIKeyPlan.started_at <= now, (APIKeyPlan.expires_at.is_(None) | (APIKeyPlan.expires_at > now))).order_by(APIKeyPlan.started_at.desc()))
        plan = assignment.plan if assignment and assignment.plan.is_active else session.scalar(select(Plan).where(Plan.is_active.is_(True), Plan.is_default.is_(True)))
        if plan is None:
            return _error("No active plan configured", 503)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        today_count = session.scalar(select(func.count(TrafficLog.id)).where(TrafficLog.api_key_id == parsed_id, TrafficLog.created_at >= today)) or 0
        month_count = session.scalar(select(func.count(TrafficLog.id)).where(TrafficLog.api_key_id == parsed_id, TrafficLog.created_at >= month)) or 0
        rate_limited_today = session.scalar(select(func.count(TrafficLog.id)).where(TrafficLog.api_key_id == parsed_id, TrafficLog.created_at >= today, TrafficLog.rate_limit_allowed.is_(False))) or 0
        average_latency = session.scalar(select(func.coalesce(func.avg(TrafficLog.duration_ms), 0)).where(TrafficLog.api_key_id == parsed_id, TrafficLog.created_at >= today)) or 0
        short = session.scalars(select(PlanRateLimit).where(PlanRateLimit.plan_id == plan.id, PlanRateLimit.is_active.is_(True)).order_by(PlanRateLimit.window_seconds)).first()
        short_count = session.scalar(select(RateLimitCounter.request_count).where(RateLimitCounter.plan_rate_limit_id == short.id, RateLimitCounter.api_key_id == parsed_id, RateLimitCounter.window_start == window_start(now, short.window_seconds))) if short else 0
        quotas = {item.period: item for item in session.scalars(select(PlanQuota).where(PlanQuota.plan_id == plan.id, PlanQuota.is_active.is_(True))).all()}
        return jsonify({"api_key_id": str(parsed_id), "plan": plan.name, "requests_today": int(today_count), "requests_this_month": int(month_count), "rate_limited_today": int(rate_limited_today), "average_latency_ms": round(float(average_latency), 2), "rate_limit": {"limit": short.requests if short else None, "window_seconds": short.window_seconds if short else None, "remaining": max(short.requests - (short_count or 0), 0) if short else None}, "quota": {"daily_limit": quotas["daily"].limit if "daily" in quotas else None, "daily_remaining": max(quotas["daily"].limit - today_count, 0) if "daily" in quotas else None, "monthly_limit": quotas["monthly"].limit if "monthly" in quotas else None, "monthly_remaining": max(quotas["monthly"].limit - month_count, 0) if "monthly" in quotas else None}}), 200
    finally:
        session.close()
