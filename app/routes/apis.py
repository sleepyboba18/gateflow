from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_route import APIRoute
from app.services.api_service import (
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    SUPPORTED_METHODS,
    commit_or_raise_conflict,
    create_api,
    create_route,
    get_owned_api,
    get_route,
    validate_base_url,
    validate_route_path,
)

apis_bp = Blueprint("apis", __name__)


def _error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


def _timestamp(value):
    return value.isoformat() if value else None


def _route_response(route: APIRoute) -> dict:
    return {
        "id": str(route.id), "api_id": str(route.api_id), "path": route.path,
        "method": route.method, "target_path": route.target_path,
        "is_active": route.is_active, "created_at": _timestamp(route.created_at),
        "updated_at": _timestamp(route.updated_at),
    }


def _api_response(api: API, include_routes: bool = False) -> dict:
    result = {
        "id": str(api.id), "name": api.name, "slug": api.slug, "base_url": api.base_url,
        "description": api.description, "is_active": api.is_active,
        "timeout_seconds": api.timeout_seconds, "created_at": _timestamp(api.created_at),
        "updated_at": _timestamp(api.updated_at),
        "upstream_auth": {"type": api.upstream_auth_type, "configured": bool(api.upstream_auth_value)},
    }
    if include_routes:
        result["routes"] = [_route_response(route) for route in api.routes]
    return result


def _session_or_error():
    if SessionLocal is None:
        return None, _error("Database is not configured", 503)
    return SessionLocal(), None


def _valid_timeout(value) -> bool:
    return isinstance(value, int) and MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS


def _valid_slug(value) -> bool:
    return isinstance(value, str) and bool(__import__("re").fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))


def _authorized(api: API) -> bool:
    return api.owner_id == g.current_user.id or bool(g.current_user.is_admin)


@apis_bp.post("")
@require_auth
def create_api_route():
    data = request.get_json(silent=True) or {}
    name, slug, base_url = data.get("name"), data.get("slug"), data.get("base_url")
    timeout = data.get("timeout_seconds", 10)
    if not all(isinstance(value, str) and value.strip() for value in (name, slug, base_url)):
        return _error("Invalid request", 400)
    if not _valid_slug(slug) or not validate_base_url(base_url.strip()) or not _valid_timeout(timeout):
        return _error("Invalid request", 400)
    session, error = _session_or_error()
    if error:
        return error
    try:
        auth_type = data.get("upstream_auth_type", "none")
        if auth_type not in {"none", "bearer", "api_key", "basic"} or (auth_type == "api_key" and data.get("upstream_auth_header") and not isinstance(data["upstream_auth_header"], str)):
            return _error("Invalid request", 400)
        api = create_api(session, owner_id=g.current_user.id, name=name.strip(), slug=slug, base_url=base_url.strip(), description=data.get("description"), timeout_seconds=timeout, upstream_auth_type=auth_type, upstream_auth_value=data.get("upstream_auth_value"), upstream_auth_header=data.get("upstream_auth_header"))
        commit_or_raise_conflict(session)
        session.refresh(api)
        return jsonify({"api": _api_response(api)}), 201
    except ValueError:
        return _error("API slug is already registered", 409)
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to create API", 500)
    finally:
        session.close()


@apis_bp.get("")
@require_auth
def list_apis():
    session, error = _session_or_error()
    if error:
        return error
    try:
        apis = session.scalars(select(API).where(API.owner_id == g.current_user.id).order_by(API.created_at.desc())).all()
        return jsonify({"apis": [_api_response(api) for api in apis]}), 200
    finally:
        session.close()


@apis_bp.get("/<api_id>")
@require_auth
def get_api(api_id: str):
    try:
        parsed_id = UUID(api_id)
    except ValueError:
        return _error("API not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = get_owned_api(session, g.current_user.id, parsed_id, g.current_user.is_admin)
        return (jsonify({"api": _api_response(api, True)}), 200) if api else _error("API not found", 404)
    finally:
        session.close()


@apis_bp.put("/<api_id>")
@require_auth
def update_api(api_id: str):
    try:
        parsed_id = UUID(api_id)
    except ValueError:
        return _error("API not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = get_owned_api(session, g.current_user.id, parsed_id, g.current_user.is_admin)
        if api is None:
            return _error("API not found", 404)
        data = request.get_json(silent=True) or {}
        for field in ("name", "slug", "base_url", "description", "timeout_seconds", "is_active"):
            if field not in data:
                continue
            value = data[field]
            if field == "name" and (not isinstance(value, str) or not value.strip()):
                return _error("Invalid request", 400)
            if field == "slug" and not _valid_slug(value):
                return _error("Invalid request", 400)
            if field == "base_url" and (not isinstance(value, str) or not validate_base_url(value.strip())):
                return _error("Invalid request", 400)
            if field == "timeout_seconds" and not _valid_timeout(value):
                return _error("Invalid request", 400)
            if field in {"is_active"} and not isinstance(value, bool):
                return _error("Invalid request", 400)
            setattr(api, field, value.strip() if field in {"name", "base_url"} else value)
        if "upstream_auth_type" in data:
            if data["upstream_auth_type"] not in {"none", "bearer", "api_key", "basic"}:
                return _error("Invalid request", 400)
            api.upstream_auth_type = data["upstream_auth_type"]
        if "upstream_auth_value" in data:
            if not isinstance(data["upstream_auth_value"], str) or len(data["upstream_auth_value"]) > 2048:
                return _error("Invalid request", 400)
            api.upstream_auth_value = data["upstream_auth_value"]
        if "upstream_auth_header" in data:
            if not isinstance(data["upstream_auth_header"], str) or "\r" in data["upstream_auth_header"] or "\n" in data["upstream_auth_header"]:
                return _error("Invalid request", 400)
            api.upstream_auth_header = data["upstream_auth_header"]
        commit_or_raise_conflict(session)
        session.refresh(api)
        return jsonify({"api": _api_response(api)}), 200
    except ValueError:
        return _error("API slug is already registered", 409)
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to update API", 500)
    finally:
        session.close()


@apis_bp.delete("/<api_id>")
@require_auth
def delete_api(api_id: str):
    try:
        parsed_id = UUID(api_id)
    except ValueError:
        return _error("API not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = get_owned_api(session, g.current_user.id, parsed_id, g.current_user.is_admin)
        if api is None:
            return _error("API not found", 404)
        session.delete(api)
        session.commit()
        return jsonify({"message": "API deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to delete API", 500)
    finally:
        session.close()


@apis_bp.post("/<api_id>/routes")
@require_auth
def create_api_route_entry(api_id: str):
    try:
        parsed_id = UUID(api_id)
    except ValueError:
        return _error("API not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = session.get(API, parsed_id)
        if api is None:
            return _error("API not found", 404)
        if not _authorized(api):
            return _error("Insufficient permissions", 403)
        data = request.get_json(silent=True) or {}
        path, method, target_path = data.get("path"), data.get("method"), data.get("target_path")
        if not validate_route_path(path) or not isinstance(method, str) or method.upper() not in SUPPORTED_METHODS or not validate_route_path(target_path, True):
            return _error("Invalid request", 400)
        route = create_route(session, api_id=api.id, path=path, method=method.upper(), target_path=target_path)
        commit_or_raise_conflict(session)
        session.refresh(route)
        return jsonify({"route": _route_response(route)}), 201
    except ValueError:
        return _error("Route already exists", 409)
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to create route", 500)
    finally:
        session.close()


@apis_bp.get("/<api_id>/routes")
@require_auth
def list_api_routes(api_id: str):
    return _route_collection(api_id)


def _route_collection(api_id: str):
    try:
        parsed_id = UUID(api_id)
    except ValueError:
        return _error("API not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = session.get(API, parsed_id)
        if api is None:
            return _error("API not found", 404)
        if not _authorized(api):
            return _error("Insufficient permissions", 403)
        routes = session.scalars(select(APIRoute).where(APIRoute.api_id == api.id).order_by(APIRoute.created_at)).all()
        return jsonify({"routes": [_route_response(route) for route in routes]}), 200
    finally:
        session.close()


@apis_bp.put("/<api_id>/routes/<route_id>")
@require_auth
def update_api_route(api_id: str, route_id: str):
    return _update_or_delete_route(api_id, route_id, delete=False)


@apis_bp.delete("/<api_id>/routes/<route_id>")
@require_auth
def delete_api_route(api_id: str, route_id: str):
    return _update_or_delete_route(api_id, route_id, delete=True)


def _update_or_delete_route(api_id: str, route_id: str, delete: bool):
    try:
        parsed_api_id, parsed_route_id = UUID(api_id), UUID(route_id)
    except ValueError:
        return _error("Route not found", 404)
    session, error = _session_or_error()
    if error:
        return error
    try:
        api = session.get(API, parsed_api_id)
        route = get_route(session, parsed_api_id, parsed_route_id)
        if api is None or route is None:
            return _error("Route not found", 404)
        if not _authorized(api):
            return _error("Insufficient permissions", 403)
        if delete:
            session.delete(route)
            session.commit()
            return jsonify({"message": "Route deleted successfully"}), 200
        data = request.get_json(silent=True) or {}
        if "path" in data and not validate_route_path(data["path"]):
            return _error("Invalid request", 400)
        if "method" in data and (not isinstance(data["method"], str) or data["method"].upper() not in SUPPORTED_METHODS):
            return _error("Invalid request", 400)
        if "target_path" in data and not validate_route_path(data["target_path"], True):
            return _error("Invalid request", 400)
        if "is_active" in data and not isinstance(data["is_active"], bool):
            return _error("Invalid request", 400)
        if "path" in data:
            route.path = data["path"]
        if "method" in data:
            route.method = data["method"].upper()
        for field in ("target_path", "is_active"):
            if field in data:
                setattr(route, field, data[field])
        commit_or_raise_conflict(session)
        session.refresh(route)
        return jsonify({"route": _route_response(route)}), 200
    except ValueError:
        return _error("Route already exists", 409)
    except SQLAlchemyError:
        session.rollback()
        return _error("Unable to update route", 500)
    finally:
        session.close()
