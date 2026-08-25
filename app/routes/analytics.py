from datetime import datetime, timedelta, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.services.analytics_service import (
    error_stats,
    key_stats,
    latency_stats,
    overview,
    parse_period,
    route_stats,
    status_stats,
    timeseries,
)

analytics_bp = Blueprint("analytics", __name__)


def _error(message, status):
    return jsonify({"error": message, "status": status}), status


def _period():
    try:
        end = parse_period(request.args.get("to")) if request.args.get("to") else datetime.now(timezone.utc)
        start = parse_period(request.args.get("from")) if request.args.get("from") else end - timedelta(hours=24)
        if start > end:
            raise ValueError
        return start, end
    except (TypeError, ValueError):
        raise ValueError("Invalid time range")


def _api_context(api_id):
    try:
        parsed_id = UUID(api_id)
    except (ValueError, TypeError):
        return None, _error("API not found", 404)
    if SessionLocal is None:
        return None, _error("Database is not configured", 503)
    session = SessionLocal()
    api = session.get(API, parsed_id)
    if api is None or (api.owner_id != g.current_user.id and not g.current_user.is_admin):
        session.close()
        return None, _error("API not found", 404)
    return (session, parsed_id), None


@analytics_bp.get("/<api_id>/analytics")
@require_auth
def analytics_overview(api_id):
    context, error = _api_context(api_id)
    if error:
        return error
    session, parsed_id = context
    try:
        start, end = _period()
        return jsonify({"api_id": str(parsed_id), "period": {"from": start.isoformat(), "to": end.isoformat()}, "data": overview(session, parsed_id, start, end)}), 200
    except ValueError as exception:
        return _error(str(exception), 400)
    finally:
        session.close()


def _analytics_collection(api_id, function, **kwargs):
    context, error = _api_context(api_id)
    if error:
        return error
    session, parsed_id = context
    try:
        start, end = _period()
        return jsonify({"api_id": str(parsed_id), "period": {"from": start.isoformat(), "to": end.isoformat()}, "data": function(session, parsed_id, start, end, **kwargs)}), 200
    except ValueError as exception:
        return _error(str(exception), 400)
    finally:
        session.close()


@analytics_bp.get("/<api_id>/analytics/timeseries")
@require_auth
def analytics_timeseries(api_id):
    return _analytics_collection(api_id, timeseries, granularity=request.args.get("granularity", "hour"))


@analytics_bp.get("/<api_id>/analytics/routes")
@require_auth
def analytics_routes(api_id):
    return _analytics_collection(api_id, route_stats)


@analytics_bp.get("/<api_id>/analytics/api-keys")
@require_auth
def analytics_keys(api_id):
    return _analytics_collection(api_id, key_stats)


@analytics_bp.get("/<api_id>/analytics/status-codes")
@require_auth
def analytics_status(api_id):
    return _analytics_collection(api_id, status_stats)


@analytics_bp.get("/<api_id>/analytics/latency")
@require_auth
def analytics_latency(api_id):
    return _analytics_collection(api_id, latency_stats)


@analytics_bp.get("/<api_id>/analytics/errors")
@require_auth
def analytics_errors(api_id):
    return _analytics_collection(api_id, error_stats)
