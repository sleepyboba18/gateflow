from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_route import APIRoute
from app.models.rate_limit import RateLimit
from app.services.rate_limit_service import MAX_REQUESTS, MAX_WINDOW_SECONDS
from app.services.traffic_service import traffic_history, traffic_summary

rate_limits_bp = Blueprint("rate_limits", __name__)


def _error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


def _time(value):
    return value.isoformat() if value else None


def _owned_api(session, api_id: UUID):
    api = session.get(API, api_id)
    if api and (api.owner_id == g.current_user.id or g.current_user.is_admin):
        return api
    return None


def _rate_response(limit: RateLimit):
    return {"id": str(limit.id), "name": limit.name, "requests": limit.requests, "window_seconds": limit.window_seconds, "route_id": str(limit.route_id) if limit.route_id else None, "is_active": limit.is_active, "created_at": _time(limit.created_at), "updated_at": _time(limit.updated_at)}


def _parse_positive(value, maximum):
    return isinstance(value, int) and 0 < value <= maximum


def _parse_uuid(value):
    try:
        return UUID(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _parse_date(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@rate_limits_bp.post("/<api_id>/rate-limits")
@require_auth
def create_rate_limit(api_id: str):
    parsed_api_id = _parse_uuid(api_id)
    if parsed_api_id is None or SessionLocal is None:
        return _error("API not found" if parsed_api_id is None else "Database is not configured", 404 if parsed_api_id is None else 503)
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("name"), str) or not data["name"].strip() or not _parse_positive(data.get("requests"), MAX_REQUESTS) or not _parse_positive(data.get("window_seconds"), MAX_WINDOW_SECONDS):
        return _error("Invalid request", 400)
    route_id = _parse_uuid(data.get("route_id"))
    if data.get("route_id") is not None and route_id is None:
        return _error("Invalid request", 400)
    session = SessionLocal()
    try:
        api = _owned_api(session, parsed_api_id)
        if api is None:
            return _error("API not found", 404)
        if route_id is not None and (session.get(APIRoute, route_id) is None or session.get(APIRoute, route_id).api_id != api.id):
            return _error("Invalid route", 400)
        limit = RateLimit(api_id=api.id, route_id=route_id, name=data["name"].strip(), requests=data["requests"], window_seconds=data["window_seconds"])
        session.add(limit)
        session.commit()
        session.refresh(limit)
        return jsonify({"rate_limit": _rate_response(limit)}), 201
    except SQLAlchemyError:
        session.rollback()
        return _error("Rate limit configuration already exists or could not be created", 409)
    finally:
        session.close()


@rate_limits_bp.get("/<api_id>/rate-limits")
@require_auth
def list_rate_limits(api_id: str):
    parsed_api_id = _parse_uuid(api_id)
    if parsed_api_id is None or SessionLocal is None:
        return _error("API not found" if parsed_api_id is None else "Database is not configured", 404 if parsed_api_id is None else 503)
    session = SessionLocal()
    try:
        if _owned_api(session, parsed_api_id) is None:
            return _error("API not found", 404)
        limits = session.scalars(select(RateLimit).where(RateLimit.api_id == parsed_api_id).order_by(RateLimit.created_at)).all()
        return jsonify({"rate_limits": [_rate_response(limit) for limit in limits]}), 200
    finally:
        session.close()


@rate_limits_bp.put("/<api_id>/rate-limits/<rate_limit_id>")
@require_auth
def update_rate_limit(api_id: str, rate_limit_id: str):
    parsed_api_id, parsed_limit_id = _parse_uuid(api_id), _parse_uuid(rate_limit_id)
    if parsed_api_id is None or parsed_limit_id is None or SessionLocal is None:
        return _error("Rate limit not found" if parsed_limit_id else "Invalid request", 404 if parsed_limit_id else 400)
    session = SessionLocal()
    try:
        if _owned_api(session, parsed_api_id) is None:
            return _error("Rate limit not found", 404)
        limit = session.scalar(select(RateLimit).where(RateLimit.id == parsed_limit_id, RateLimit.api_id == parsed_api_id))
        if limit is None:
            return _error("Rate limit not found", 404)
        data = request.get_json(silent=True) or {}
        if "name" in data and (not isinstance(data["name"], str) or not data["name"].strip()):
            return _error("Invalid request", 400)
        if "requests" in data and not _parse_positive(data["requests"], MAX_REQUESTS):
            return _error("Invalid request", 400)
        if "window_seconds" in data and not _parse_positive(data["window_seconds"], MAX_WINDOW_SECONDS):
            return _error("Invalid request", 400)
        if "is_active" in data and not isinstance(data["is_active"], bool):
            return _error("Invalid request", 400)
        route_id = _parse_uuid(data["route_id"]) if "route_id" in data else limit.route_id
        if "route_id" in data and data["route_id"] is not None and route_id is None:
            return _error("Invalid request", 400)
        if route_id is not None and (session.get(APIRoute, route_id) is None or session.get(APIRoute, route_id).api_id != parsed_api_id):
            return _error("Invalid route", 400)
        for field in ("name", "requests", "window_seconds", "is_active"):
            if field in data:
                setattr(limit, field, data[field].strip() if field == "name" else data[field])
        if "route_id" in data:
            limit.route_id = route_id
        session.commit()
        session.refresh(limit)
        return jsonify({"rate_limit": _rate_response(limit)}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Rate limit configuration conflicts with an existing configuration", 409)
    finally:
        session.close()


@rate_limits_bp.delete("/<api_id>/rate-limits/<rate_limit_id>")
@require_auth
def delete_rate_limit(api_id: str, rate_limit_id: str):
    parsed_api_id, parsed_limit_id = _parse_uuid(api_id), _parse_uuid(rate_limit_id)
    if parsed_api_id is None or parsed_limit_id is None or SessionLocal is None:
        return _error("Rate limit not found", 404)
    session = SessionLocal()
    try:
        if _owned_api(session, parsed_api_id) is None:
            return _error("Rate limit not found", 404)
        limit = session.scalar(select(RateLimit).where(RateLimit.id == parsed_limit_id, RateLimit.api_id == parsed_api_id))
        if limit is None:
            return _error("Rate limit not found", 404)
        session.delete(limit)
        session.commit()
        return jsonify({"message": "Rate limit deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to delete rate limit", 500)
    finally:
        session.close()


@rate_limits_bp.get("/<api_id>/traffic/summary")
@require_auth
def traffic_summary_route(api_id: str):
    return _traffic_route(api_id, summary=True)


@rate_limits_bp.get("/<api_id>/traffic")
@require_auth
def traffic_history_route(api_id: str):
    return _traffic_route(api_id, summary=False)


def _traffic_route(api_id: str, summary: bool):
    parsed_api_id = _parse_uuid(api_id)
    if parsed_api_id is None or SessionLocal is None:
        return _error("API not found" if parsed_api_id is None else "Database is not configured", 404 if parsed_api_id is None else 503)
    session = SessionLocal()
    try:
        if _owned_api(session, parsed_api_id) is None:
            return _error("API not found", 404)
        if summary:
            return jsonify(traffic_summary(session, parsed_api_id)), 200
        try:
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 50))
        except ValueError:
            return _error("Invalid pagination", 400)
        if page < 1 or per_page < 1 or per_page > 100:
            return _error("Invalid pagination", 400)
        filters = {}
        for field in ("method", "route_id", "api_key_id"):
            if request.args.get(field):
                filters[field] = _parse_uuid(request.args[field]) if field.endswith("_id") else request.args[field].upper()
                if filters[field] is None:
                    return _error("Invalid filter", 400)
        if request.args.get("status_code"):
            try:
                filters["status_code"] = int(request.args["status_code"])
            except ValueError:
                return _error("Invalid filter", 400)
        try:
            filters["from"] = _parse_date(request.args.get("from"))
            filters["to"] = _parse_date(request.args.get("to"))
        except (TypeError, ValueError):
            return _error("Invalid filter", 400)
        items, total = traffic_history(session, parsed_api_id, page, per_page, filters)
        return jsonify({"page": page, "per_page": per_page, "total": total, "items": [{"id": str(item.id), "request_id": item.request_id, "route_id": str(item.route_id) if item.route_id else None, "method": item.method, "path": item.path, "status_code": item.status_code, "duration_ms": item.duration_ms, "request_size": item.request_size, "response_size": item.response_size, "rate_limit_allowed": item.rate_limit_allowed, "rate_limit_remaining": item.rate_limit_remaining, "error_type": item.error_type, "created_at": _time(item.created_at)} for item in items]}), 200
    finally:
        session.close()
