import re
from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_route import APIRoute
from app.models.api_version import APIVersion
from app.services.api_service import SUPPORTED_METHODS, validate_route_path

versions_bp = Blueprint("versions", __name__)
STATUSES = {"development", "active", "deprecated", "sunset", "disabled"}
VERSION_RE = re.compile(r"^v[1-9][0-9]*$")


def error(message, status):
    return jsonify({"error": message, "status": status}), status


def uid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def time_value(value):
    return value.isoformat() if value else None


def can_manage(api):
    return api is not None and (api.owner_id == g.current_user.id or g.current_user.is_admin)


def version_json(item):
    return {"id": str(item.id), "api_id": str(item.api_id), "version": item.version, "name": item.name, "description": item.description, "status": item.status, "is_default": item.is_default, "is_active": item.is_active, "deprecated_at": time_value(item.deprecated_at), "sunset_at": time_value(item.sunset_at), "created_at": time_value(item.created_at), "updated_at": time_value(item.updated_at)}


@versions_bp.post("/apis/<api_id>/versions")
@require_auth
def create_version(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = uid(api_id); data = request.get_json(silent=True) or {}
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, parsed) if parsed else None
        if not can_manage(api): return error("API not found", 404)
        if not isinstance(data.get("version"), str) or not VERSION_RE.fullmatch(data["version"]) or not isinstance(data.get("name"), str) or not data["name"].strip() or data.get("status", "development") not in STATUSES: return error("Invalid request", 400)
        item = APIVersion(api_id=api.id, version=data["version"], name=data["name"].strip(), description=data.get("description"), status=data.get("status", "development"), is_default=bool(data.get("is_default", False)), is_active=data.get("status", "development") != "disabled")
        if item.is_default: session.query(APIVersion).filter(APIVersion.api_id == api.id).update({APIVersion.is_default: False}, synchronize_session=False)
        session.add(item); session.commit(); session.refresh(item)
        return jsonify({"version": version_json(item)}), 201
    except IntegrityError:
        session.rollback(); return error("Version already exists", 409)
    finally: session.close()


@versions_bp.get("/apis/<api_id>/versions")
@require_auth
def list_versions(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = uid(api_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, parsed) if parsed else None
        if not can_manage(api): return error("API not found", 404)
        return jsonify({"versions": [version_json(item) for item in session.scalars(select(APIVersion).where(APIVersion.api_id == api.id).order_by(APIVersion.version)).all()]}), 200
    finally: session.close()


@versions_bp.get("/apis/<api_id>/versions/<version_id>")
@require_auth
def get_version(api_id, version_id):
    return _version_operation(api_id, version_id, None)


@versions_bp.put("/apis/<api_id>/versions/<version_id>")
@require_auth
def update_version(api_id, version_id):
    return _version_operation(api_id, version_id, request.get_json(silent=True) or {})


def _version_operation(api_id, version_id, data):
    session = SessionLocal() if SessionLocal else None; api_uuid, version_uuid = uid(api_id), uid(version_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; item = session.get(APIVersion, version_uuid) if version_uuid else None
        if api is None or item is None or item.api_id != api.id or not can_manage(api): return error("Version not found", 404)
        if data is None: return jsonify({"version": version_json(item)}), 200
        if "version" in data and (not isinstance(data["version"], str) or not VERSION_RE.fullmatch(data["version"])): return error("Invalid request", 400)
        if "status" in data and data["status"] not in STATUSES: return error("Invalid request", 400)
        for field in ("version", "name", "description", "status", "is_active", "is_default"):
            if field in data:
                if field == "name" and (not isinstance(data[field], str) or not data[field].strip()): return error("Invalid request", 400)
                if field in {"is_active", "is_default"} and not isinstance(data[field], bool): return error("Invalid request", 400)
                setattr(item, field, data[field].strip() if field == "name" else data[field])
        if item.status == "deprecated" and item.deprecated_at is None: item.deprecated_at = datetime.now(timezone.utc)
        if item.status == "sunset" and item.sunset_at is None: item.sunset_at = datetime.now(timezone.utc)
        if item.status == "disabled": item.is_active = False
        if item.is_default: session.query(APIVersion).filter(APIVersion.api_id == api.id, APIVersion.id != item.id).update({APIVersion.is_default: False}, synchronize_session=False)
        session.commit(); return jsonify({"version": version_json(item)}), 200
    except IntegrityError:
        session.rollback(); return error("Version already exists", 409)
    finally: session.close()


@versions_bp.delete("/apis/<api_id>/versions/<version_id>")
@require_auth
def delete_version(api_id, version_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, version_uuid = uid(api_id), uid(version_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; item = session.get(APIVersion, version_uuid) if version_uuid else None
        if api is None or item is None or item.api_id != api.id or not can_manage(api): return error("Version not found", 404)
        session.delete(item); session.commit(); return jsonify({"message": "Version deleted successfully"}), 200
    finally: session.close()


@versions_bp.post("/apis/<api_id>/versions/<version_id>/routes")
@require_auth
def create_version_route(api_id, version_id):
    return _route_operation(api_id, version_id, None, False)


@versions_bp.get("/apis/<api_id>/versions/<version_id>/routes")
@require_auth
def list_version_routes(api_id, version_id):
    return _route_operation(api_id, version_id, None, True)


def _route_operation(api_id, version_id, data, listing):
    session = SessionLocal() if SessionLocal else None; api_uuid, version_uuid = uid(api_id), uid(version_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; version = session.get(APIVersion, version_uuid) if version_uuid else None
        if api is None or version is None or version.api_id != api.id or not can_manage(api): return error("Version not found", 404)
        if listing: return jsonify({"routes": [{"id": str(item.id), "version_id": str(item.version_id), "path": item.path, "method": item.method, "target_path": item.target_path, "is_active": item.is_active} for item in session.scalars(select(APIRoute).where(APIRoute.version_id == version.id)).all()]}), 200
        data = request.get_json(silent=True) or {}
        if not validate_route_path(data.get("path")) or not isinstance(data.get("method"), str) or data["method"].upper() not in SUPPORTED_METHODS or not validate_route_path(data.get("target_path"), True): return error("Invalid request", 400)
        route = APIRoute(api_id=api.id, version_id=version.id, path=data["path"], method=data["method"].upper(), target_path=data.get("target_path"))
        session.add(route); session.commit(); session.refresh(route)
        return jsonify({"route": {"id": str(route.id), "version_id": str(route.version_id), "path": route.path, "method": route.method, "target_path": route.target_path, "is_active": route.is_active}}), 201
    except IntegrityError:
        session.rollback(); return error("Route already exists", 409)
    finally: session.close()
