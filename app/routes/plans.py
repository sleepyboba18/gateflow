import re
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.database.connection import SessionLocal
from app.middleware.auth import require_admin, require_auth
from app.models.plan import Plan
from app.models.plan_quota import PlanQuota
from app.models.plan_rate_limit import PlanRateLimit
from app.services.rate_limit_service import MAX_REQUESTS, MAX_WINDOW_SECONDS

plans_bp = Blueprint("plans", __name__)
SCOPES = {"api", "route"}
PERIODS = {"daily", "monthly"}


def _error(message, status):
    return jsonify({"error": message, "status": status}), status


def _time(value):
    return value.isoformat() if value else None


def _plan(plan, detail=False):
    result = {"id": str(plan.id), "name": plan.name, "slug": plan.slug, "description": plan.description, "is_active": plan.is_active, "is_default": plan.is_default, "created_at": _time(plan.created_at), "updated_at": _time(plan.updated_at)}
    if detail:
        result["rate_limits"] = [{"id": str(item.id), "name": item.name, "requests": item.requests, "window_seconds": item.window_seconds, "scope": item.scope, "is_active": item.is_active} for item in plan.rate_limits]
        result["quotas"] = [{"id": str(item.id), "name": item.name, "limit": item.limit, "period": item.period, "is_active": item.is_active} for item in plan.quotas]
    return result


def _uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def _get_plan(session, value, detail=False):
    plan_id = _uuid(value)
    if plan_id is None:
        return None
    options = [selectinload(Plan.rate_limits), selectinload(Plan.quotas)] if detail else []
    return session.scalar(select(Plan).options(*options).where(Plan.id == plan_id))


def _slug(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))


def _admin_plan(plan_id):
    if SessionLocal is None:
        return None, _error("Database is not configured", 503)
    session = SessionLocal()
    plan = _get_plan(session, plan_id, True)
    if plan is None:
        session.close()
        return None, _error("Plan not found", 404)
    return session, plan


@plans_bp.post("")
@require_admin
def create_plan():
    data = request.get_json(silent=True) or {}
    if not all(isinstance(data.get(field), str) and data[field].strip() for field in ("name", "slug")) or not _slug(data["slug"]):
        return _error("Invalid request", 400)
    if SessionLocal is None:
        return _error("Database is not configured", 503)
    session = SessionLocal()
    try:
        plan = Plan(name=data["name"].strip(), slug=data["slug"], description=data.get("description"), is_default=bool(data.get("is_default", False)))
        if plan.is_default:
            session.query(Plan).filter(Plan.is_default.is_(True)).update({Plan.is_default: False}, synchronize_session=False)
        session.add(plan)
        session.commit()
        session.refresh(plan)
        return jsonify({"plan": _plan(plan)}), 201
    except IntegrityError:
        session.rollback()
        return _error("Plan slug already exists", 409)
    finally:
        session.close()


@plans_bp.get("")
@require_auth
def list_plans():
    if SessionLocal is None:
        return _error("Database is not configured", 503)
    session = SessionLocal()
    try:
        query = select(Plan).order_by(Plan.name)
        if not g.current_user.is_admin:
            query = query.where(Plan.is_active.is_(True))
        return jsonify({"plans": [_plan(plan) for plan in session.scalars(query).all()]}), 200
    finally:
        session.close()


@plans_bp.get("/<plan_id>")
@require_auth
def get_plan(plan_id):
    session, result = _admin_plan(plan_id)
    if session is None:
        return result
    try:
        if not g.current_user.is_admin and not result.is_active:
            return _error("Plan not found", 404)
        return jsonify({"plan": _plan(result, True)}), 200
    finally:
        session.close()


@plans_bp.put("/<plan_id>")
@require_admin
def update_plan(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        data = request.get_json(silent=True) or {}
        if "name" in data and (not isinstance(data["name"], str) or not data["name"].strip()):
            return _error("Invalid request", 400)
        if "slug" in data and not _slug(data["slug"]):
            return _error("Invalid request", 400)
        for field in ("name", "slug", "description", "is_active", "is_default"):
            if field in data:
                if field in {"is_active", "is_default"} and not isinstance(data[field], bool):
                    return _error("Invalid request", 400)
                setattr(plan, field, data[field].strip() if field in {"name", "slug"} else data[field])
        if plan.is_default:
            session.query(Plan).filter(Plan.id != plan.id).update({Plan.is_default: False}, synchronize_session=False)
        session.commit()
        session.refresh(plan)
        return jsonify({"plan": _plan(plan)}), 200
    except IntegrityError:
        session.rollback()
        return _error("Plan slug already exists", 409)
    finally:
        session.close()


@plans_bp.delete("/<plan_id>")
@require_admin
def delete_plan(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        if plan.is_default and plan.is_active:
            return _error("Select another default plan first", 409)
        session.delete(plan)
        session.commit()
        return jsonify({"message": "Plan deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to delete plan", 500)
    finally:
        session.close()


@plans_bp.post("/<plan_id>/rate-limits")
@require_admin
def create_plan_rate_limit(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data.get("name"), str) or not data["name"].strip() or not isinstance(data.get("requests"), int) or not 0 < data["requests"] <= MAX_REQUESTS or not isinstance(data.get("window_seconds"), int) or not 0 < data["window_seconds"] <= MAX_WINDOW_SECONDS or data.get("scope") not in SCOPES:
            return _error("Invalid request", 400)
        item = PlanRateLimit(plan_id=plan.id, name=data["name"].strip(), requests=data["requests"], window_seconds=data["window_seconds"], scope=data["scope"])
        session.add(item)
        session.commit()
        session.refresh(item)
        return jsonify({"rate_limit": {"id": str(item.id), "name": item.name, "requests": item.requests, "window_seconds": item.window_seconds, "scope": item.scope, "is_active": item.is_active}}), 201
    except IntegrityError:
        session.rollback()
        return _error("Plan rate limit already exists", 409)
    finally:
        session.close()


@plans_bp.get("/<plan_id>/rate-limits")
@require_auth
def list_plan_rate_limits(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        if not g.current_user.is_admin and not plan.is_active:
            return _error("Plan not found", 404)
        return jsonify({"rate_limits": [{"id": str(item.id), "name": item.name, "requests": item.requests, "window_seconds": item.window_seconds, "scope": item.scope, "is_active": item.is_active} for item in plan.rate_limits]}), 200
    finally:
        session.close()


@plans_bp.put("/<plan_id>/rate-limits/<rate_limit_id>")
@require_admin
def update_plan_rate_limit(plan_id, rate_limit_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        item = session.scalar(select(PlanRateLimit).where(PlanRateLimit.id == _uuid(rate_limit_id), PlanRateLimit.plan_id == plan.id))
        if item is None:
            return _error("Plan rate limit not found", 404)
        data = request.get_json(silent=True) or {}
        for field in ("name", "requests", "window_seconds", "scope", "is_active"):
            if field not in data:
                continue
            value = data[field]
            if field == "name" and (not isinstance(value, str) or not value.strip()):
                return _error("Invalid request", 400)
            if field == "requests" and (not isinstance(value, int) or not 0 < value <= MAX_REQUESTS):
                return _error("Invalid request", 400)
            if field == "window_seconds" and (not isinstance(value, int) or not 0 < value <= MAX_WINDOW_SECONDS):
                return _error("Invalid request", 400)
            if field == "scope" and value not in SCOPES:
                return _error("Invalid request", 400)
            if field == "is_active" and not isinstance(value, bool):
                return _error("Invalid request", 400)
            setattr(item, field, value.strip() if field == "name" else value)
        session.commit()
        return jsonify({"rate_limit": {"id": str(item.id), "name": item.name, "requests": item.requests, "window_seconds": item.window_seconds, "scope": item.scope, "is_active": item.is_active}}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to update plan rate limit", 500)
    finally:
        session.close()


@plans_bp.delete("/<plan_id>/rate-limits/<rate_limit_id>")
@require_admin
def delete_plan_rate_limit(plan_id, rate_limit_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        item = session.scalar(select(PlanRateLimit).where(PlanRateLimit.id == _uuid(rate_limit_id), PlanRateLimit.plan_id == plan.id))
        if item is None:
            return _error("Plan rate limit not found", 404)
        session.delete(item)
        session.commit()
        return jsonify({"message": "Plan rate limit deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to delete plan rate limit", 500)
    finally:
        session.close()


@plans_bp.post("/<plan_id>/quotas")
@require_admin
def create_quota(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data.get("name"), str) or not data["name"].strip() or not isinstance(data.get("limit"), int) or not 0 < data["limit"] <= MAX_REQUESTS or data.get("period") not in {"daily", "monthly"}:
            return _error("Invalid request", 400)
        item = PlanQuota(plan_id=plan.id, name=data["name"].strip(), limit=data["limit"], period=data["period"])
        session.add(item)
        session.commit()
        session.refresh(item)
        return jsonify({"quota": {"id": str(item.id), "name": item.name, "limit": item.limit, "period": item.period, "is_active": item.is_active}}), 201
    except IntegrityError:
        session.rollback()
        return _error("Plan quota for this period already exists", 409)
    finally:
        session.close()


@plans_bp.get("/<plan_id>/quotas")
@require_auth
def list_quotas(plan_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        if not g.current_user.is_admin and not plan.is_active:
            return _error("Plan not found", 404)
        return jsonify({"quotas": [{"id": str(item.id), "name": item.name, "limit": item.limit, "period": item.period, "is_active": item.is_active} for item in plan.quotas]}), 200
    finally:
        session.close()


@plans_bp.put("/<plan_id>/quotas/<quota_id>")
@require_admin
def update_quota(plan_id, quota_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        item = session.scalar(select(PlanQuota).where(PlanQuota.id == _uuid(quota_id), PlanQuota.plan_id == plan.id))
        if item is None:
            return _error("Plan quota not found", 404)
        data = request.get_json(silent=True) or {}
        for field in ("name", "limit", "period", "is_active"):
            if field not in data:
                continue
            value = data[field]
            if field == "name" and (not isinstance(value, str) or not value.strip()):
                return _error("Invalid request", 400)
            if field == "limit" and (not isinstance(value, int) or not 0 < value <= MAX_REQUESTS):
                return _error("Invalid request", 400)
            if field == "period" and value not in {"daily", "monthly"}:
                return _error("Invalid request", 400)
            if field == "is_active" and not isinstance(value, bool):
                return _error("Invalid request", 400)
            setattr(item, field, value.strip() if field == "name" else value)
        session.commit()
        return jsonify({"quota": {"id": str(item.id), "name": item.name, "limit": item.limit, "period": item.period, "is_active": item.is_active}}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to update plan quota", 500)
    finally:
        session.close()


@plans_bp.delete("/<plan_id>/quotas/<quota_id>")
@require_admin
def delete_quota(plan_id, quota_id):
    session, plan = _admin_plan(plan_id)
    if session is None:
        return plan
    try:
        item = session.scalar(select(PlanQuota).where(PlanQuota.id == _uuid(quota_id), PlanQuota.plan_id == plan.id))
        if item is None:
            return _error("Plan quota not found", 404)
        session.delete(item)
        session.commit()
        return jsonify({"message": "Plan quota deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to delete plan quota", 500)
    finally:
        session.close()
