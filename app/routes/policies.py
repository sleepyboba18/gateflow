import re
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth, require_admin
from app.models.api import API
from app.models.api_key import APIKey
from app.models.api_key_scope import APIKeyScope
from app.models.api_scope import APIScope
from app.models.api_route import APIRoute
from app.models.gateway_policy import GatewayPolicy
from app.models.header_policy import HeaderPolicy
from app.models.route_scope import RouteScope
from app.models.scope import Scope
from app.services.scope_service import valid_scope_name

policies_bp = Blueprint("policies", __name__)
FORBIDDEN_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie", "host", "content-length", "connection", "transfer-encoding"}


def error(message, status):
    return jsonify({"error": message, "status": status}), status


def uid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def authorized(owner_id):
    return owner_id == g.current_user.id or g.current_user.is_admin


def scope_value(scope):
    return {"id": str(scope.id), "name": scope.name, "description": scope.description}


def _session():
    return SessionLocal() if SessionLocal else None


@policies_bp.post("/scopes")
@require_admin
def create_scope():
    data = request.get_json(silent=True) or {}
    if not valid_scope_name(data.get("name")):
        return error("Invalid request", 400)
    session = _session()
    if session is None:
        return error("Database is not configured", 503)
    try:
        scope = Scope(name=data["name"], description=data.get("description"))
        session.add(scope)
        session.commit()
        session.refresh(scope)
        return jsonify({"scope": scope_value(scope)}), 201
    except IntegrityError:
        session.rollback()
        return error("Scope already exists", 409)
    finally:
        session.close()


@policies_bp.get("/scopes")
@require_auth
def list_scopes():
    session = _session()
    if session is None:
        return error("Database is not configured", 503)
    try:
        return jsonify({"scopes": [scope_value(scope) for scope in session.scalars(select(Scope).order_by(Scope.name)).all()]}), 200
    finally:
        session.close()


@policies_bp.put("/scopes/<scope_id>")
@require_admin
def update_scope(scope_id):
    session = _session()
    parsed = uid(scope_id)
    if session is None or parsed is None:
        return error("Scope not found", 404)
    try:
        scope = session.get(Scope, parsed)
        data = request.get_json(silent=True) or {}
        if scope is None:
            return error("Scope not found", 404)
        if "name" in data and not valid_scope_name(data["name"]):
            return error("Invalid request", 400)
        if "name" in data:
            scope.name = data["name"]
        if "description" in data:
            scope.description = data["description"]
        session.commit()
        return jsonify({"scope": scope_value(scope)}), 200
    except IntegrityError:
        session.rollback()
        return error("Scope already exists", 409)
    finally:
        session.close()


@policies_bp.delete("/scopes/<scope_id>")
@require_admin
def delete_scope(scope_id):
    session = _session()
    parsed = uid(scope_id)
    if session is None or parsed is None:
        return error("Scope not found", 404)
    try:
        scope = session.get(Scope, parsed)
        if scope is None:
            return error("Scope not found", 404)
        session.delete(scope)
        session.commit()
        return jsonify({"message": "Scope deleted successfully"}), 200
    except SQLAlchemyError:
        session.rollback()
        return error("Unable to delete scope", 500)
    finally:
        session.close()


def _scope_list(model, field, parent_id, session):
    return session.scalars(select(Scope).join(model, model.scope_id == Scope.id).where(field == parent_id).order_by(Scope.name)).all()


@policies_bp.post("/api-keys/<key_id>/scopes")
@require_auth
def assign_key_scope(key_id):
    session = _session()
    key_uuid, data = uid(key_id), request.get_json(silent=True) or {}
    scope_uuid = uid(data.get("scope_id"))
    if session is None or key_uuid is None or scope_uuid is None:
        return error("Invalid request", 400)
    try:
        key = session.get(APIKey, key_uuid)
        scope = session.get(Scope, scope_uuid)
        if key is None or not authorized(key.user_id) or scope is None:
            return error("API key not found", 404)
        supported = session.scalar(select(APIScope.id).join(API, API.id == APIScope.api_id).where(APIScope.scope_id == scope.id, (API.owner_id == key.user_id) | (g.current_user.is_admin)))
        if supported is None:
            return error("Scope is not supported by an accessible API", 400)
        if session.scalar(select(APIKeyScope).where(APIKeyScope.api_key_id == key.id, APIKeyScope.scope_id == scope.id)):
            return error("Scope already assigned", 409)
        session.add(APIKeyScope(api_key_id=key.id, scope_id=scope.id))
        session.commit()
        return jsonify({"scope": scope_value(scope)}), 201
    except SQLAlchemyError:
        session.rollback()
        return error("Unable to assign scope", 500)
    finally:
        session.close()


@policies_bp.get("/api-keys/<key_id>/scopes")
@require_auth
def list_key_scopes(key_id):
    return _list_associations("key", key_id)


@policies_bp.delete("/api-keys/<key_id>/scopes/<scope_id>")
@require_auth
def remove_key_scope(key_id, scope_id):
    return _remove_association("key", key_id, scope_id)


def _list_associations(kind, parent_id):
    session = _session()
    parsed = uid(parent_id)
    if session is None or parsed is None:
        return error("Resource not found", 404)
    try:
        if kind == "key":
            parent = session.get(APIKey, parsed); owner = parent.user_id if parent else None; model, field = APIKeyScope, APIKeyScope.api_key_id
        elif kind == "api":
            parent = session.get(API, parsed); owner = parent.owner_id if parent else None; model, field = APIScope, APIScope.api_id
        else:
            parent = session.get(APIRoute, parsed); api = session.get(API, parent.api_id) if parent else None; owner = api.owner_id if api else None; model, field = RouteScope, RouteScope.route_id
        if parent is None or not authorized(owner):
            return error("Resource not found", 404)
        return jsonify({"scopes": [scope_value(item) for item in _scope_list(model, field, parsed, session)]}), 200
    finally:
        session.close()


def _remove_association(kind, parent_id, scope_id):
    session = _session(); parent_uuid, scope_uuid = uid(parent_id), uid(scope_id)
    if session is None or parent_uuid is None or scope_uuid is None:
        return error("Resource not found", 404)
    try:
        if kind == "key":
            parent = session.get(APIKey, parent_uuid); owner = parent.user_id if parent else None; model, conditions = APIKeyScope, (APIKeyScope.api_key_id == parent_uuid, APIKeyScope.scope_id == scope_uuid)
        elif kind == "api":
            parent = session.get(API, parent_uuid); owner = parent.owner_id if parent else None; model, conditions = APIScope, (APIScope.api_id == parent_uuid, APIScope.scope_id == scope_uuid)
        else:
            parent = session.get(APIRoute, parent_uuid); api = session.get(API, parent.api_id) if parent else None; owner = api.owner_id if api else None; model, conditions = RouteScope, (RouteScope.route_id == parent_uuid, RouteScope.scope_id == scope_uuid)
        association = session.scalar(select(model).where(*conditions))
        if parent is None or not authorized(owner) or association is None:
            return error("Scope association not found", 404)
        session.delete(association); session.commit()
        return jsonify({"message": "Scope removed successfully"}), 200
    finally:
        session.close()


@policies_bp.post("/apis/<api_id>/scopes")
@require_auth
def assign_api_scope(api_id):
    session = _session(); api_uuid = uid(api_id); scope_uuid = uid((request.get_json(silent=True) or {}).get("scope_id"))
    if session is None or api_uuid is None or scope_uuid is None: return error("Invalid request", 400)
    try:
        api, scope = session.get(API, api_uuid), session.get(Scope, scope_uuid)
        if api is None or scope is None or not authorized(api.owner_id): return error("API not found", 404)
        if session.scalar(select(APIScope).where(APIScope.api_id == api.id, APIScope.scope_id == scope.id)): return error("Scope already assigned", 409)
        session.add(APIScope(api_id=api.id, scope_id=scope.id)); session.commit()
        return jsonify({"scope": scope_value(scope)}), 201
    finally: session.close()


@policies_bp.get("/apis/<api_id>/scopes")
@require_auth
def list_api_scopes(api_id): return _list_associations("api", api_id)


@policies_bp.delete("/apis/<api_id>/scopes/<scope_id>")
@require_auth
def remove_api_scope(api_id, scope_id): return _remove_association("api", api_id, scope_id)


@policies_bp.post("/apis/<api_id>/routes/<route_id>/scopes")
@require_auth
def assign_route_scope(api_id, route_id):
    session = _session(); api_uuid, route_uuid = uid(api_id), uid(route_id); scope_uuid = uid((request.get_json(silent=True) or {}).get("scope_id"))
    if session is None or api_uuid is None or route_uuid is None or scope_uuid is None: return error("Invalid request", 400)
    try:
        api, route, scope = session.get(API, api_uuid), session.get(APIRoute, route_uuid), session.get(Scope, scope_uuid)
        if api is None or route is None or route.api_id != api.id or scope is None or not authorized(api.owner_id): return error("Route not found", 404)
        if not session.scalar(select(APIScope).where(APIScope.api_id == api.id, APIScope.scope_id == scope.id)): return error("Scope is not supported by this API", 400)
        if session.scalar(select(RouteScope).where(RouteScope.route_id == route.id, RouteScope.scope_id == scope.id)): return error("Scope already assigned", 409)
        session.add(RouteScope(route_id=route.id, scope_id=scope.id)); session.commit()
        return jsonify({"scope": scope_value(scope)}), 201
    finally: session.close()


@policies_bp.get("/apis/<api_id>/routes/<route_id>/scopes")
@require_auth
def list_route_scopes(api_id, route_id): return _list_associations("route", route_id)


@policies_bp.delete("/apis/<api_id>/routes/<route_id>/scopes/<scope_id>")
@require_auth
def remove_route_scope(api_id, route_id, scope_id): return _remove_association("route", route_id, scope_id)


def policy_json(policy):
    return {"id": str(policy.id) if policy else None, "api_id": str(policy.api_id) if policy else None, "route_id": str(policy.route_id) if policy and policy.route_id else None, "require_api_key": policy.require_api_key if policy else True, "require_scope": policy.require_scope if policy else True, "allow_query_parameters": policy.allow_query_parameters if policy else True, "allow_request_body": policy.allow_request_body if policy else True, "allow_file_upload": policy.allow_file_upload if policy else False, "max_request_size": policy.max_request_size if policy else 1_048_576, "is_active": policy.is_active if policy else True}


def save_policy(api_id, route_id=None):
    session = _session(); api_uuid = uid(api_id)
    if session is None or api_uuid is None: return error("API not found", 404)
    try:
        api = session.get(API, api_uuid); route = session.get(APIRoute, route_id) if route_id else None
        if api is None or (route_id and (route is None or route.api_id != api.id)) or not authorized(api.owner_id): return error("API not found", 404)
        policy = session.scalar(select(GatewayPolicy).where(GatewayPolicy.api_id == api.id, GatewayPolicy.route_id == route_id))
        if policy is None: policy = GatewayPolicy(api_id=api.id, route_id=route_id); session.add(policy)
        data = request.get_json(silent=True) or {}
        fields = ("require_api_key", "require_scope", "allow_query_parameters", "allow_request_body", "allow_file_upload", "is_active", "max_request_size")
        for field in fields:
            if field not in data: continue
            value = data[field]
            if field == "max_request_size" and (not isinstance(value, int) or not 0 <= value <= 100 * 1024 * 1024): return error("Invalid request", 400)
            if field != "max_request_size" and not isinstance(value, bool): return error("Invalid request", 400)
            setattr(policy, field, value)
        session.commit(); session.refresh(policy)
        return jsonify({"policy": policy_json(policy)}), 200
    except SQLAlchemyError:
        session.rollback(); return error("Unable to update policy", 500)
    finally: session.close()


def get_policy(api_id, route_id=None):
    session = _session(); api_uuid = uid(api_id)
    if session is None or api_uuid is None: return error("API not found", 404)
    try:
        api = session.get(API, api_uuid); route = session.get(APIRoute, route_id) if route_id else None
        if api is None or (route_id and (route is None or route.api_id != api.id)) or not authorized(api.owner_id): return error("API not found", 404)
        policy = session.scalar(select(GatewayPolicy).where(GatewayPolicy.api_id == api.id, GatewayPolicy.route_id == route_id))
        return jsonify({"policy": policy_json(policy)}), 200
    finally: session.close()


@policies_bp.get("/apis/<api_id>/policy")
@require_auth
def api_policy(api_id): return get_policy(api_id)


@policies_bp.put("/apis/<api_id>/policy")
@require_auth
def update_api_policy(api_id): return save_policy(api_id)


@policies_bp.get("/apis/<api_id>/routes/<route_id>/policy")
@require_auth
def route_policy(api_id, route_id): return get_policy(api_id, uid(route_id))


@policies_bp.put("/apis/<api_id>/routes/<route_id>/policy")
@require_auth
def update_route_policy(api_id, route_id): return save_policy(api_id, uid(route_id))


@policies_bp.post("/apis/<api_id>/headers")
@require_auth
def add_header_policy(api_id):
    session = _session(); api_uuid = uid(api_id); data = request.get_json(silent=True) or {}
    if session is None or api_uuid is None: return error("API not found", 404)
    try:
        api = session.get(API, api_uuid); route_id = uid(data.get("route_id")) if data.get("route_id") else None
        if api is None or not authorized(api.owner_id) or (route_id and (session.get(APIRoute, route_id) is None or session.get(APIRoute, route_id).api_id != api.id)): return error("API not found", 404)
        name, value = data.get("header_name"), data.get("header_value")
        if data.get("direction") not in {"request", "response"} or data.get("action") not in {"add", "replace", "remove"} or not isinstance(name, str) or name.lower() in FORBIDDEN_HEADERS or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name) or "\r" in str(value or "") or "\n" in str(value or ""): return error("Invalid request", 400)
        item = HeaderPolicy(api_id=api.id, route_id=route_id, direction=data["direction"], action=data["action"], header_name=name, header_value=value)
        session.add(item); session.commit(); session.refresh(item)
        return jsonify({"header_policy": {"id": str(item.id), "route_id": str(item.route_id) if item.route_id else None, "direction": item.direction, "action": item.action, "header_name": item.header_name, "is_active": item.is_active}}), 201
    finally: session.close()


@policies_bp.get("/apis/<api_id>/headers")
@require_auth
def headers(api_id):
    session = _session(); parsed = uid(api_id)
    if session is None or parsed is None: return error("API not found", 404)
    try:
        api = session.get(API, parsed)
        if api is None or not authorized(api.owner_id): return error("API not found", 404)
        items = session.scalars(select(HeaderPolicy).where(HeaderPolicy.api_id == parsed)).all()
        return jsonify({"headers": [{"id": str(item.id), "route_id": str(item.route_id) if item.route_id else None, "direction": item.direction, "action": item.action, "header_name": item.header_name, "is_active": item.is_active} for item in items]}), 200
    finally: session.close()


@policies_bp.delete("/apis/<api_id>/headers/<header_id>")
@require_auth
def remove_header(api_id, header_id):
    session = _session(); api_uuid, header_uuid = uid(api_id), uid(header_id)
    if session is None or api_uuid is None or header_uuid is None: return error("Header policy not found", 404)
    try:
        api, item = session.get(API, api_uuid), session.get(HeaderPolicy, header_uuid)
        if api is None or item is None or item.api_id != api.id or not authorized(api.owner_id): return error("Header policy not found", 404)
        session.delete(item); session.commit(); return jsonify({"message": "Header policy deleted successfully"}), 200
    finally: session.close()
