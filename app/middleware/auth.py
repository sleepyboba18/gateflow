from functools import wraps
from typing import Any, Callable
from uuid import UUID

from flask import g, jsonify, request
from sqlalchemy import select

from app.database.connection import get_db
from app.models.user import User
from app.services.api_key_service import validate_api_key
from app.services.auth_service import decode_access_token


def _json_error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


def require_auth(route: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(route)
    def wrapped(*args: Any, **kwargs: Any):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _json_error("Authentication required", 401)
        try:
            payload = decode_access_token(token)
            user_id = UUID(str(payload["sub"]))
        except ValueError:
            return _json_error("Authentication required", 401)

        if get_db is None:
            return _json_error("Authentication required", 401)
        try:
            session = next(get_db())
        except RuntimeError:
            return _json_error("Authentication required", 401)
        try:
            user = session.scalar(select(User).where(User.id == user_id))
            if user is None or not user.is_active:
                return _json_error("Authentication required", 401)
            g.current_user = user
            return route(*args, **kwargs)
        finally:
            session.close()

    return wrapped


def require_admin(route: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(route)
    @require_auth
    def wrapped(*args: Any, **kwargs: Any):
        if not g.current_user.is_admin:
            return _json_error("Insufficient permissions", 403)
        return route(*args, **kwargs)

    return wrapped


def require_api_key(route: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(route)
    def wrapped(*args: Any, **kwargs: Any):
        plaintext_key = request.headers.get("X-API-Key", "")
        if not plaintext_key:
            return _json_error("API key authentication required", 401)
        try:
            session = next(get_db())
        except RuntimeError:
            return _json_error("API key authentication required", 401)
        try:
            api_key = validate_api_key(session, plaintext_key)
            if api_key is None:
                return _json_error("Invalid API key", 401)
            g.current_api_key = api_key
            g.current_user = api_key.user
            return route(*args, **kwargs)
        finally:
            session.close()

    return wrapped
