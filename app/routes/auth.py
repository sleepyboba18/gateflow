from flask import Blueprint, jsonify, request, g
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.connection import get_db
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password, verify_password
from app.middleware.auth import require_auth


auth_bp = Blueprint("auth", __name__)


def _error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


def _user_response(user: User) -> dict:
    return {"id": str(user.id), "username": user.username, "email": user.email}


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    if not all(isinstance(value, str) and value.strip() for value in (username, email, password)):
        return _error("Invalid request", 400)

    normalized_email = email.strip().lower()
    session = next(get_db())
    try:
        if session.scalar(select(User).where((User.email == normalized_email) | (User.username == username.strip()))):
            return _error("Username or email already registered", 409)
        user = User(username=username.strip(), email=normalized_email, password_hash=hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return jsonify({"message": "User registered successfully", "user": _user_response(user)}), 201
    except IntegrityError:
        session.rollback()
        return _error("Username or email already registered", 409)
    finally:
        session.close()


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return _error("Invalid email or password", 401)

    session = next(get_db())
    try:
        user = session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            return _error("Invalid email or password", 401)
        return jsonify({"access_token": create_access_token(user.id), "token_type": "Bearer"}), 200
    finally:
        session.close()


@auth_bp.get("/me")
@require_auth
def me():
    user = g.current_user
    return jsonify({
        "user": {
            **_user_response(user),
            "is_admin": user.is_admin,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
    }), 200
