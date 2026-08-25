from datetime import datetime, timezone
from uuid import UUID

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_db
from app.middleware.auth import require_auth
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
