from datetime import datetime, timedelta, timezone
from uuid import UUID

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_key import APIKey
from app.services.analytics_service import (
    error_stats,
    key_stats,
    latency_stats,
    overview,
    parse_period,
    reliability,
    reliability_timeseries,
    version_stats,
    route_stats,
    status_stats,
    timeseries,
    get_error_metrics,
    get_latency_metrics,
    get_request_metrics,
    get_security_metrics,
    get_upstream_metrics,
)

analytics_bp = Blueprint("analytics", __name__)
observability_bp = Blueprint("observability_analytics", __name__)


def _error(message, status):
    return jsonify({"error": message, "status": status}), status


def _period():
    try:
        end = parse_period(request.args.get("to")) if request.args.get("to") else datetime.now(timezone.utc)
        start = parse_period(request.args.get("from")) if request.args.get("from") else end - timedelta(hours=24)
        if start > end:
            raise ValueError
        if end - start > timedelta(days=current_app.config.get("ANALYTICS_MAX_DAYS", 90)):
            raise ValueError("Analytics time range exceeds maximum window")
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


@analytics_bp.get("/<api_id>/analytics/reliability")
@require_auth
def analytics_reliability(api_id):
    return _analytics_collection(api_id, reliability)


@analytics_bp.get("/<api_id>/analytics/reliability/timeseries")
@require_auth
def analytics_reliability_timeseries(api_id):
    return _analytics_collection(api_id, reliability_timeseries, granularity=request.args.get("granularity", "hour"))


@analytics_bp.get("/<api_id>/analytics/versions")
@require_auth
def analytics_versions(api_id):
    return _analytics_collection(api_id, version_stats)


def _global_period():
    try:
        return _period()
    except ValueError as exception:
        return None, _error(str(exception), 400)


@observability_bp.get("/analytics/overview")
@require_auth
def global_overview():
    if not g.current_user.is_admin:
        return _error("Insufficient permissions", 403)
    if SessionLocal is None:
        return _error("Database is not configured", 503)
    period, error = _global_period()
    if error:
        return error
    session = SessionLocal()
    try:
        start, end = period
        requests = get_request_metrics(session, start, end)
        latency = get_latency_metrics(session, start, end)
        return jsonify({"period": {"from": start.isoformat(), "to": end.isoformat()}, "requests": {"total": requests["requests"], "successful": requests["successful_requests"], "failed": requests["failed_requests"]}, "latency": {"average_ms": latency["average_ms"], "p95_ms": latency["p95_ms"], "p99_ms": latency["p99_ms"]}, "upstream": get_upstream_metrics(session, start, end), "security": get_security_metrics(session, start, end)}), 200
    finally:
        session.close()


@observability_bp.get("/apis/<api_id>/analytics/overview")
@require_auth
def api_observability_overview(api_id):
    context, error = _api_context(api_id)
    if error:
        return error
    session, parsed_id = context
    try:
        start, end = _period()
        requests = get_request_metrics(session, start, end, api_id=parsed_id)
        return jsonify({"api_id": str(parsed_id), "period": {"from": start.isoformat(), "to": end.isoformat()}, "requests": requests, "latency": get_latency_metrics(session, start, end, api_id=parsed_id), "upstream": get_upstream_metrics(session, start, end, api_id=parsed_id), "errors": get_error_metrics(session, start, end, api_id=parsed_id)}), 200
    except ValueError as exception:
        return _error(str(exception), 400)
    finally:
        session.close()


@observability_bp.get("/api-keys/<key_id>/analytics")
@require_auth
def api_key_observability(key_id):
    try:
        key_id = UUID(key_id)
    except (TypeError, ValueError):
        return _error("API key not found", 404)
    if SessionLocal is None:
        return _error("Database is not configured", 503)
    session = SessionLocal()
    try:
        key = session.get(APIKey, key_id)
        if key is None or (key.user_id != g.current_user.id and not g.current_user.is_admin):
            return _error("API key not found", 404)
        start, end = _period()
        metrics = get_request_metrics(session, start, end, api_key_id=key_id)
        security = get_security_metrics(session, start, end)
        return jsonify({"api_key_id": str(key_id), "period": {"from": start.isoformat(), "to": end.isoformat()}, "requests": metrics["requests"], "successful_requests": metrics["successful_requests"], "failed_requests": metrics["failed_requests"], "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None, "last_used_ip": key.last_used_ip, "rate_limit_hits": metrics["rate_limited"], "quota_hits": metrics["quota_rejected"], "authentication_failures": security["authentication_failures"]}), 200
    except ValueError as exception:
        return _error(str(exception), 400)
    finally:
        session.close()


@observability_bp.get("/security/analytics")
@require_auth
def security_observability():
    if not g.current_user.is_admin:
        return _error("Insufficient permissions", 403)
    if SessionLocal is None:
        return _error("Database is not configured", 503)
    period, error = _global_period()
    if error:
        return error
    session = SessionLocal()
    try:
        start, end = period
        return jsonify({"period": {"from": start.isoformat(), "to": end.isoformat()}, **get_security_metrics(session, start, end)}), 200
    finally:
        session.close()
