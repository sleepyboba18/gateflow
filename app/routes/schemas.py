from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api import API
from app.models.api_route import APIRoute
from app.models.api_version import APIVersion
from app.models.schema import GatewaySchema
from app.services.schema_service import validate_schema_definition

schemas_bp = Blueprint("schemas", __name__)


def error(message, status):
    return jsonify({"error": message, "status": status}), status


def uid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def owns(api):
    return api and (api.owner_id == g.current_user.id or g.current_user.is_admin)


def output(item):
    return {"id": str(item.id), "api_id": str(item.api_id), "version_id": str(item.version_id), "route_id": str(item.route_id) if item.route_id else None, "name": item.name, "schema_type": item.schema_type, "schema_definition": item.schema_definition, "is_active": item.is_active, "created_at": item.created_at.isoformat() if item.created_at else None, "updated_at": item.updated_at.isoformat() if item.updated_at else None}


@schemas_bp.post("/apis/<api_id>/schemas")
@require_auth
def create_schema(api_id):
    session = SessionLocal() if SessionLocal else None; api_uuid = uid(api_id); data = request.get_json(silent=True) or {}
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; version_uuid = uid(data.get("version_id")); route_uuid = uid(data.get("route_id")) if data.get("route_id") else None
        if not owns(api): return error("API not found", 404)
        version, route = session.get(APIVersion, version_uuid), session.get(APIRoute, route_uuid) if route_uuid else None
        if version is None or version.api_id != api.id or (route and (route.api_id != api.id or route.version_id != version.id)): return error("Invalid version or route", 400)
        if not isinstance(data.get("name"), str) or not data["name"].strip() or data.get("schema_type") not in {"request", "response"}: return error("Invalid request", 400)
        validate_schema_definition(data.get("schema_definition"))
        item = GatewaySchema(api_id=api.id, version_id=version.id, route_id=route.id if route else None, name=data["name"].strip(), schema_type=data["schema_type"], schema_definition=data["schema_definition"], is_active=data.get("is_active", True))
        session.add(item); session.commit(); session.refresh(item)
        return jsonify({"schema": output(item)}), 201
    except (ValueError, IntegrityError):
        session.rollback(); return error("Invalid or duplicate schema", 400)
    finally: session.close()


@schemas_bp.get("/apis/<api_id>/schemas")
@require_auth
def list_schemas(api_id):
    session = SessionLocal() if SessionLocal else None; parsed = uid(api_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, parsed) if parsed else None
        if not owns(api): return error("API not found", 404)
        return jsonify({"schemas": [output(item) for item in session.scalars(select(GatewaySchema).where(GatewaySchema.api_id == api.id).order_by(GatewaySchema.created_at)).all()]}), 200
    finally: session.close()


@schemas_bp.get("/apis/<api_id>/schemas/<schema_id>")
@require_auth
def get_schema(api_id, schema_id):
    return schema_operation(api_id, schema_id, None)


@schemas_bp.put("/apis/<api_id>/schemas/<schema_id>")
@require_auth
def update_schema(api_id, schema_id):
    return schema_operation(api_id, schema_id, request.get_json(silent=True) or {})


def schema_operation(api_id, schema_id, data):
    session = SessionLocal() if SessionLocal else None; api_uuid, schema_uuid = uid(api_id), uid(schema_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; item = session.get(GatewaySchema, schema_uuid) if schema_uuid else None
        if not owns(api) or item is None or item.api_id != api.id: return error("Schema not found", 404)
        if data is None: return jsonify({"schema": output(item)}), 200
        for field in ("name", "schema_type", "schema_definition", "is_active"):
            if field in data:
                if field == "schema_definition": validate_schema_definition(data[field])
                if field == "schema_type" and data[field] not in {"request", "response"}: return error("Invalid request", 400)
                setattr(item, field, data[field].strip() if field == "name" else data[field])
        session.commit(); session.refresh(item); return jsonify({"schema": output(item)}), 200
    except (ValueError, SQLAlchemyError):
        session.rollback(); return error("Unable to update schema", 400)
    finally: session.close()


@schemas_bp.delete("/apis/<api_id>/schemas/<schema_id>")
@require_auth
def delete_schema(api_id, schema_id):
    session = SessionLocal() if SessionLocal else None; api_uuid, schema_uuid = uid(api_id), uid(schema_id)
    if session is None: return error("Database is not configured", 503)
    try:
        api = session.get(API, api_uuid) if api_uuid else None; item = session.get(GatewaySchema, schema_uuid) if schema_uuid else None
        if not owns(api) or item is None or item.api_id != api.id: return error("Schema not found", 404)
        session.delete(item); session.commit(); return jsonify({"message": "Schema deleted successfully"}), 200
    finally: session.close()
