from uuid import UUID

from flask import request
from flask_socketio import emit, join_room, leave_room
from sqlalchemy import select

from app import socketio
from app.database.connection import SessionLocal
from app.models.user import User
from app.models.api import API
from app.models.api_key import APIKey
from app.sockets.rooms import api_key_room, api_room, join_user, leave_api, leave_api_key
from app.services.auth_service import decode_access_token
from app.services.analytics_service import snapshot


def _token(auth):
    if isinstance(auth, dict):
        return auth.get("token", "")
    return ""


@socketio.on("connect")
def on_connect(auth):
    try:
        payload = decode_access_token(_token(auth))
        user_id = UUID(str(payload["sub"]))
        if SessionLocal is None:
            return False
        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.id == user_id))
            if user is None or not user.is_active:
                return False
        from flask import session as flask_session
        flask_session["monitor_user_id"] = str(user_id)
        flask_session["monitor_is_admin"] = bool(user.is_admin)
        join_user(user_id)
        return True
    except (ValueError, TypeError, KeyError):
        return False


@socketio.on("disconnect")
def on_disconnect():
    return None


def _context():
    from flask import session as flask_session
    return flask_session.get("monitor_user_id"), bool(flask_session.get("monitor_is_admin"))


def _id(data):
    try:
        return UUID((data or {}).get("api_id"))
    except (ValueError, TypeError, AttributeError):
        return None


@socketio.on("join_api")
def on_join_api(data):
    user_id, is_admin = _context()
    api_id = _id(data)
    if not user_id or not api_id or SessionLocal is None:
        return {"success": False, "error": "Not authorized to monitor this API"}
    with SessionLocal() as session:
        api = session.get(API, api_id)
        if api is None or (str(api.owner_id) != user_id and not is_admin):
            return {"success": False, "error": "Not authorized to monitor this API"}
        join_room(api_room(api_id))
        emit("monitoring:snapshot", snapshot(session, api_id))
    return {"success": True, "room": api_room(api_id)}


@socketio.on("leave_api")
def on_leave_api(data):
    api_id = _id(data)
    if api_id:
        leave_api(api_id)
        return {"success": True, "room": api_room(api_id)}
    return {"success": False, "error": "Invalid API"}


@socketio.on("join_api_key")
def on_join_api_key(data):
    user_id, is_admin = _context()
    try:
        key_id = UUID((data or {}).get("api_key_id"))
    except (ValueError, TypeError, AttributeError):
        key_id = None
    if not user_id or not key_id or SessionLocal is None:
        return {"success": False, "error": "Not authorized to monitor this API key"}
    with SessionLocal() as session:
        api_key = session.get(APIKey, key_id)
        if api_key is None or (str(api_key.user_id) != user_id and not is_admin):
            return {"success": False, "error": "Not authorized to monitor this API key"}
    join_room(api_key_room(key_id))
    return {"success": True, "room": api_key_room(key_id)}


@socketio.on("leave_api_key")
def on_leave_api_key(data):
    try:
        key_id = UUID((data or {}).get("api_key_id"))
    except (ValueError, TypeError, AttributeError):
        return {"success": False, "error": "Invalid API key"}
    leave_api_key(key_id)
    return {"success": True, "room": api_key_room(key_id)}


@socketio.on("join_user")
def on_join_user(data):
    user_id, _ = _context()
    if user_id and str((data or {}).get("user_id")) == user_id:
        from app.sockets.rooms import join_user
        join_user(user_id)
        return {"success": True, "room": f"user:{user_id}"}
    return {"success": False, "error": "Not authorized to monitor this user"}


@socketio.on("leave_user")
def on_leave_user(data):
    user_id, _ = _context()
    if user_id and str((data or {}).get("user_id")) == user_id:
        from flask_socketio import leave_room
        leave_room(f"user:{user_id}")
        return {"success": True, "room": f"user:{user_id}"}
    return {"success": False, "error": "Not authorized to monitor this user"}
