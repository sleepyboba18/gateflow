from datetime import datetime, timezone
from uuid import UUID

import requests
from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_route import APIRoute
from app.models.circuit_breaker import CircuitBreaker
from app.models.upstream_health import UpstreamHealth
from app.gateway.proxy import build_upstream_url
from app.services.circuit_breaker_service import CLOSED, get_effective_breaker
from app.services.upstream_health_service import get_health, record_result

reliability_bp = Blueprint("reliability", __name__)


def _error(message, status):
    return jsonify({"error": message, "status": status}), status


def _uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def _api(session, api_id):
    api = session.get(API, api_id)
    return api if api and (api.owner_id == g.current_user.id or g.current_user.is_admin) else None


def _time(value):
    return value.isoformat() if value else None


def _breaker(item):
    return {"id": str(item.id), "api_id": str(item.api_id), "route_id": str(item.route_id) if item.route_id else None, "state": item.state, "failure_count": item.failure_count, "success_count": item.success_count, "failure_threshold": item.failure_threshold, "recovery_timeout_seconds": item.recovery_timeout_seconds, "half_open_max_requests": item.half_open_max_requests, "last_failure_at": _time(item.last_failure_at), "opened_at": _time(item.opened_at), "last_success_at": _time(item.last_success_at), "is_active": item.is_active}


def _health(item, circuit=None):
    return {"api_id": str(item.api_id), "route_id": str(item.route_id) if item.route_id else None, "state": item.state, "circuit_breaker": circuit.state if circuit else "closed", "last_success_at": _time(item.last_success_at), "last_failure_at": _time(item.last_failure_at), "consecutive_failures": item.consecutive_failures, "average_latency_ms": item.average_latency_ms}


@reliability_bp.post("/apis/<api_id>/circuit-breakers")
@require_auth
def create_breaker(api_id):
    session = SessionLocal() if SessionLocal else None; api_uuid = _uuid(api_id); data = request.get_json(silent=True) or {}
    if session is None: return _error("Database is not configured", 503)
    try:
        api = _api(session, api_uuid) if api_uuid else None
        route_id = _uuid(data.get("route_id")) if data.get("route_id") else None
        if api is None or (route_id and (session.get(APIRoute, route_id) is None or session.get(APIRoute, route_id).api_id != api.id)): return _error("API not found", 404)
        values = {"failure_threshold": data.get("failure_threshold", 5), "recovery_timeout_seconds": data.get("recovery_timeout_seconds", 30), "half_open_max_requests": data.get("half_open_max_requests", 1)}
        if any(not isinstance(value, int) or value <= 0 for value in values.values()) or values["failure_threshold"] > 10000 or values["recovery_timeout_seconds"] > 86400 or values["half_open_max_requests"] > 100: return _error("Invalid request", 400)
        item = CircuitBreaker(api_id=api.id, route_id=route_id, **values, is_active=bool(data.get("is_active", True)))
        session.add(item); session.commit(); session.refresh(item)
        return jsonify({"circuit_breaker": _breaker(item)}), 201
    except IntegrityError:
        session.rollback(); return _error("Circuit breaker already exists", 409)
    finally: session.close()


@reliability_bp.get("/apis/<api_id>/circuit-breakers")
@require_auth
def list_breakers(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = _uuid(api_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        if parsed is None or _api(session, parsed) is None: return _error("API not found", 404)
        return jsonify({"circuit_breakers": [_breaker(item) for item in session.scalars(select(CircuitBreaker).where(CircuitBreaker.api_id == parsed)).all()]}), 200
    finally: session.close()


@reliability_bp.put("/apis/<api_id>/circuit-breakers/<breaker_id>")
@require_auth
def update_breaker(api_id, breaker_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, breaker_uuid = _uuid(api_id), _uuid(breaker_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        if api_uuid is None or breaker_uuid is None or _api(session, api_uuid) is None: return _error("Circuit breaker not found", 404)
        item = session.scalar(select(CircuitBreaker).where(CircuitBreaker.id == breaker_uuid, CircuitBreaker.api_id == api_uuid))
        if item is None: return _error("Circuit breaker not found", 404)
        data = request.get_json(silent=True) or {}
        for field, maximum in (("failure_threshold", 10000), ("recovery_timeout_seconds", 86400), ("half_open_max_requests", 100)):
            if field in data and (not isinstance(data[field], int) or not 0 < data[field] <= maximum): return _error("Invalid request", 400)
        if "is_active" in data and not isinstance(data["is_active"], bool): return _error("Invalid request", 400)
        for field in ("failure_threshold", "recovery_timeout_seconds", "half_open_max_requests", "is_active"):
            if field in data: setattr(item, field, data[field])
        session.commit(); return jsonify({"circuit_breaker": _breaker(item)}), 200
    except SQLAlchemyError:
        session.rollback(); return _error("Unable to update circuit breaker", 500)
    finally: session.close()


@reliability_bp.delete("/apis/<api_id>/circuit-breakers/<breaker_id>")
@require_auth
def delete_breaker(api_id, breaker_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, breaker_uuid = _uuid(api_id), _uuid(breaker_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        item = session.scalar(select(CircuitBreaker).where(CircuitBreaker.id == breaker_uuid, CircuitBreaker.api_id == api_uuid)) if api_uuid and breaker_uuid else None
        if item is None or _api(session, api_uuid) is None: return _error("Circuit breaker not found", 404)
        session.delete(item); session.commit(); return jsonify({"message": "Circuit breaker deleted successfully"}), 200
    finally: session.close()


@reliability_bp.post("/apis/<api_id>/circuit-breakers/<breaker_id>/reset")
@require_auth
def reset_breaker(api_id, breaker_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, breaker_uuid = _uuid(api_id), _uuid(breaker_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        item = session.scalar(select(CircuitBreaker).where(CircuitBreaker.id == breaker_uuid, CircuitBreaker.api_id == api_uuid)) if api_uuid and breaker_uuid else None
        if item is None or _api(session, api_uuid) is None: return _error("Circuit breaker not found", 404)
        item.state, item.failure_count, item.success_count, item.opened_at = CLOSED, 0, 0, None
        session.commit(); return jsonify({"circuit_breaker": _breaker(item)}), 200
    finally: session.close()


@reliability_bp.get("/apis/<api_id>/health")
@require_auth
def api_health(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = _uuid(api_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        api = _api(session, parsed) if parsed else None
        if api is None: return _error("API not found", 404)
        item = get_health(session, api.id); circuit = get_effective_breaker(session, api.id, None)
        if item is None: return jsonify({"api_id": str(api.id), "state": "unknown", "circuit_breaker": circuit.state if circuit else "closed"}), 200
        return jsonify(_health(item, circuit)), 200
    finally: session.close()


@reliability_bp.get("/apis/<api_id>/routes/<route_id>/health")
@require_auth
def route_health(api_id, route_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, route_uuid = _uuid(api_id), _uuid(route_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        api, route = _api(session, api_uuid) if api_uuid else None, session.get(APIRoute, route_uuid) if route_uuid else None
        if api is None or route is None or route.api_id != api.id: return _error("Route not found", 404)
        item = get_health(session, api.id, route.id); circuit = get_effective_breaker(session, api.id, route.id)
        return jsonify(_health(item, circuit) if item else {"api_id": str(api.id), "route_id": str(route.id), "state": "unknown", "circuit_breaker": circuit.state if circuit else "closed"}), 200
    finally: session.close()


@reliability_bp.get("/health/upstreams")
@require_auth
def upstream_health():
    session = SessionLocal() if SessionLocal else None
    if session is None: return _error("Database is not configured", 503)
    try:
        query = select(API, UpstreamHealth).join(UpstreamHealth, UpstreamHealth.api_id == API.id, isouter=True)
        if not g.current_user.is_admin: query = query.where(API.owner_id == g.current_user.id)
        items = []
        for api, health in session.execute(query).all():
                breaker = get_effective_breaker(session, api.id, None)
                items.append({"api_id": str(api.id), "api": api.slug, "state": health.state if health else "unknown", "circuit": breaker.state if breaker else "closed", "latency_ms": health.average_latency_ms if health else None})
        return jsonify({"upstreams": items}), 200
    finally: session.close()


@reliability_bp.post("/apis/<api_id>/health/check")
@require_auth
def check_health(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = _uuid(api_id)
    if session is None: return _error("Database is not configured", 503)
    try:
        api = _api(session, parsed) if parsed else None
        if api is None: return _error("API not found", 404)
        started = __import__("time").perf_counter()
        try:
            response = requests.head(build_upstream_url(api.base_url, "/"), timeout=api.timeout_seconds, allow_redirects=False)
            success = response.status_code < 500
            health = record_result(session, api.id, None, success, (__import__("time").perf_counter() - started) * 1000, response.status_code)
            return jsonify(_health(health)), 200 if success else 503
        except requests.RequestException:
            health = record_result(session, api.id, None, False, (__import__("time").perf_counter() - started) * 1000, None)
            return jsonify(_health(health)), 503
    finally: session.close()
