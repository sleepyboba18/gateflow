import ipaddress
from datetime import datetime, timezone
from uuid import UUID
from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database.connection import SessionLocal
from app.middleware.auth import require_auth
from app.models.api_key import APIKey
from app.models.api_key_ip_rule import APIKeyIPRule
from app.models.api_key_origin import APIKeyOrigin
from app.models.api_key_plan import APIKeyPlan
from app.models.api_key_scope import APIKeyScope
from app.models.security_audit_log import SecurityAuditLog
from app.models.scope import Scope
from app.services.api_key_service import MAX_GRACE_SECONDS, get_key_status, rotate_api_key, revoke_api_key, suspend_api_key, unsuspend_api_key
from app.services.security_audit_service import record_security_event

security_bp = Blueprint("security", __name__)


def error(message, status):
    return jsonify({"error": message, "status": status}), status


def uid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def authorized(key):
    return key is not None and (key.user_id == g.current_user.id or g.current_user.is_admin)


def time_value(value):
    return value.isoformat() if value else None


def key_metadata(key, session=None):
    result = {"id": str(key.id), "name": key.name, "key_prefix": key.key_prefix, "status": get_key_status(key), "expires_at": time_value(key.expires_at), "last_used_at": time_value(key.last_used_at), "last_used_ip": key.last_used_ip, "created_at": time_value(key.created_at), "revoked_at": time_value(key.revoked_at), "suspended_at": time_value(key.suspended_at), "rotation_parent_id": str(key.rotation_parent_id) if key.rotation_parent_id else None}
    if session is not None:
        result["scopes"] = list(session.scalars(select(Scope.name).join(APIKeyScope, APIKeyScope.scope_id == Scope.id).where(APIKeyScope.api_key_id == key.id)).all())
    return result


def key_from_session(session, key_id):
    return session.get(APIKey, uid(key_id)) if uid(key_id) else None


@security_bp.get("/api-keys/<key_id>")
@require_auth
def key_detail(key_id):
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        key = key_from_session(session, key_id)
        if not authorized(key): return error("API key not found", 404)
        return jsonify({"api_key": key_metadata(key, session)}), 200
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/revoke")
@require_auth
def revoke(key_id):
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        key = key_from_session(session, key_id)
        if not authorized(key): return error("API key not found", 404)
        key.revoked_reason = (request.get_json(silent=True) or {}).get("reason", "revoked")[:512]
        revoke_api_key(session, key)
        record_security_event(session, "api_key_revoked", user_id=key.user_id, api_key_id=key.id, metadata={"reason": key.revoked_reason})
        return jsonify({"success": True, "status": "revoked"}), 200
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/suspend")
@require_auth
def suspend(key_id):
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        key = key_from_session(session, key_id)
        if not authorized(key): return error("API key not found", 404)
        reason = (request.get_json(silent=True) or {}).get("reason", "suspended")
        if not isinstance(reason, str) or not reason.strip(): return error("Invalid request", 400)
        suspend_api_key(session, key, reason[:512]); record_security_event(session, "api_key_suspended", user_id=key.user_id, api_key_id=key.id)
        return jsonify({"success": True, "status": "suspended"}), 200
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/unsuspend")
@require_auth
def unsuspend(key_id):
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        key = key_from_session(session, key_id)
        if not authorized(key): return error("API key not found", 404)
        try: unsuspend_api_key(session, key)
        except ValueError: return error("Revoked keys cannot be unsuspended", 409)
        record_security_event(session, "api_key_unsuspended", user_id=key.user_id, api_key_id=key.id)
        return jsonify({"success": True, "status": get_key_status(key)}), 200
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/rotate")
@require_auth
def rotate(key_id):
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        key = key_from_session(session, key_id)
        if not authorized(key): return error("API key not found", 404)
        data = request.get_json(silent=True) or {}
        grace = data.get("grace_period_seconds", 0)
        if not isinstance(grace, int) or grace < 0 or grace > MAX_GRACE_SECONDS: return error("Invalid grace period", 400)
        expiration = data.get("expires_at")
        if expiration:
            try:
                expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00")); expiration = expiration if expiration.tzinfo else expiration.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError): return error("Invalid expiration", 400)
            if expiration <= datetime.now(timezone.utc): return error("Invalid expiration", 400)
        new_key, plaintext = rotate_api_key(session, key, grace, expiration)
        return jsonify({"success": True, "new_key": plaintext, "key_id": str(new_key.id), "key_prefix": new_key.key_prefix, "warning": "Store this key securely. It will not be shown again.", "old_key": {"id": str(key.id), "grace_until": time_value(key.rotation_grace_until)}}), 201
    except SQLAlchemyError:
        session.rollback(); return error("Unable to rotate API key", 500)
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/ip-rules")
@require_auth
def add_ip_rule(key_id):
    session = SessionLocal() if SessionLocal else None; key_uuid = uid(key_id); data = request.get_json(silent=True) or {}
    if session is None: return error("Database is not configured", 503)
    try:
        key = session.get(APIKey, key_uuid) if key_uuid else None
        if not authorized(key): return error("API key not found", 404)
        try: ipaddress.ip_network(data.get("cidr"), strict=False)
        except (ValueError, TypeError): return error("Invalid CIDR", 400)
        if data.get("rule_type") not in {"allow", "deny"}: return error("Invalid rule type", 400)
        item = APIKeyIPRule(api_key_id=key.id, cidr=data["cidr"], rule_type=data["rule_type"]); session.add(item); session.commit(); session.refresh(item)
        return jsonify({"ip_rule": {"id": str(item.id), "cidr": item.cidr, "rule_type": item.rule_type, "is_active": item.is_active}}), 201
    except IntegrityError: session.rollback(); return error("IP rule already exists", 409)
    finally: session.close()


@security_bp.get("/api-keys/<key_id>/ip-rules")
@require_auth
def list_ip_rules(key_id): return _list_rules(key_id, APIKeyIPRule, "ip_rules")


@security_bp.delete("/api-keys/<key_id>/ip-rules/<rule_id>")
@require_auth
def delete_ip_rule(key_id, rule_id): return _delete_rule(key_id, rule_id, APIKeyIPRule)


@security_bp.put("/api-keys/<key_id>/ip-rules/<rule_id>")
@require_auth
def update_ip_rule(key_id, rule_id):
    session = SessionLocal() if SessionLocal else None
    key = key_from_session(session, key_id) if session else None
    rule_uuid = uid(rule_id)
    if session is None: return error("Database is not configured", 503)
    try:
        item = session.get(APIKeyIPRule, rule_uuid) if rule_uuid else None
        data = request.get_json(silent=True) or {}
        if not authorized(key) or item is None or item.api_key_id != key.id: return error("Rule not found", 404)
        if "cidr" in data:
            try: ipaddress.ip_network(data["cidr"], strict=False)
            except (ValueError, TypeError): return error("Invalid CIDR", 400)
            item.cidr = data["cidr"]
        if "rule_type" in data:
            if data["rule_type"] not in {"allow", "deny"}: return error("Invalid rule type", 400)
            item.rule_type = data["rule_type"]
        if "is_active" in data:
            if not isinstance(data["is_active"], bool): return error("Invalid request", 400)
            item.is_active = data["is_active"]
        session.commit()
        return jsonify({"ip_rule": {"id": str(item.id), "cidr": item.cidr, "rule_type": item.rule_type, "is_active": item.is_active}}), 200
    except (IntegrityError, SQLAlchemyError):
        session.rollback(); return error("Unable to update IP rule", 409)
    finally: session.close()


@security_bp.post("/api-keys/<key_id>/origins")
@require_auth
def add_origin(key_id):
    session = SessionLocal() if SessionLocal else None; key_uuid = uid(key_id); data = request.get_json(silent=True) or {}; origin = data.get("origin")
    if session is None: return error("Database is not configured", 503)
    try:
        key = session.get(APIKey, key_uuid) if key_uuid else None; parsed = urlparse(origin or "")
        if not authorized(key): return error("API key not found", 404)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment: return error("Invalid origin", 400)
        item = APIKeyOrigin(api_key_id=key.id, origin=origin); session.add(item); session.commit(); session.refresh(item)
        return jsonify({"origin": {"id": str(item.id), "origin": item.origin, "is_active": item.is_active}}), 201
    except IntegrityError: session.rollback(); return error("Origin already exists", 409)
    finally: session.close()


@security_bp.get("/api-keys/<key_id>/origins")
@require_auth
def list_origins(key_id): return _list_rules(key_id, APIKeyOrigin, "origins")


@security_bp.delete("/api-keys/<key_id>/origins/<rule_id>")
@require_auth
def delete_origin(key_id, rule_id): return _delete_rule(key_id, rule_id, APIKeyOrigin)


@security_bp.get("/api-keys/<key_id>/security-summary")
@require_auth
def security_summary(key_id):
    session = SessionLocal() if SessionLocal else None; key = key_from_session(session, key_id) if session else None
    if session is None: return error("Database is not configured", 503)
    try:
        if not authorized(key): return error("API key not found", 404)
        counts = dict(session.execute(select(SecurityAuditLog.event_type, func.count(SecurityAuditLog.id)).where(SecurityAuditLog.api_key_id == key.id).group_by(SecurityAuditLog.event_type)).all())
        rotations = counts.get("api_key_rotated", 0)
        return jsonify({"status": get_key_status(key), "last_used_at": time_value(key.last_used_at), "last_used_ip": key.last_used_ip, "authentication_failures": counts.get("api_key_authentication_failed", 0), "ip_denials": counts.get("api_key_ip_denied", 0), "origin_denials": counts.get("api_key_origin_denied", 0), "rotations": rotations, "created_at": time_value(key.created_at), "expires_at": time_value(key.expires_at)}), 200
    finally: session.close()


def _list_rules(key_id, model, name):
    session = SessionLocal() if SessionLocal else None; key = key_from_session(session, key_id) if session else None
    if session is None: return error("Database is not configured", 503)
    try:
        if not authorized(key): return error("API key not found", 404)
        items = session.scalars(select(model).where(model.api_key_id == key.id)).all()
        return jsonify({name: [{"id": str(item.id), "cidr": getattr(item, "cidr", None), "rule_type": getattr(item, "rule_type", None), "origin": getattr(item, "origin", None), "is_active": item.is_active} for item in items]}), 200
    finally: session.close()


def _delete_rule(key_id, rule_id, model):
    session = SessionLocal() if SessionLocal else None; key = key_from_session(session, key_id) if session else None; rule_uuid = uid(rule_id)
    if session is None: return error("Database is not configured", 503)
    try:
        item = session.get(model, rule_uuid) if rule_uuid else None
        if not authorized(key) or item is None or item.api_key_id != key.id: return error("Rule not found", 404)
        session.delete(item); session.commit(); return jsonify({"message": "Rule deleted successfully"}), 200
    finally: session.close()


@security_bp.get("/security/events")
@require_auth
def security_events():
    if not g.current_user.is_admin: return error("Insufficient permissions", 403)
    session = SessionLocal() if SessionLocal else None
    if session is None: return error("Database is not configured", 503)
    try:
        page, per_page = max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("per_page", 50)), 1), 100)
        query = select(SecurityAuditLog).order_by(SecurityAuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        if request.args.get("event_type"): query = query.where(SecurityAuditLog.event_type == request.args["event_type"])
        return jsonify({"page": page, "per_page": per_page, "events": [{"id": str(item.id), "event_type": item.event_type, "api_key_id": str(item.api_key_id) if item.api_key_id else None, "api_id": str(item.api_id) if item.api_id else None, "ip_address": item.ip_address, "request_id": item.request_id, "created_at": time_value(item.created_at)} for item in session.scalars(query).all()]}), 200
    finally: session.close()


@security_bp.get("/api-keys/<key_id>/security-events")
@require_auth
def key_security_events(key_id):
    session = SessionLocal() if SessionLocal else None; key = key_from_session(session, key_id) if session else None
    if session is None: return error("Database is not configured", 503)
    try:
        if not authorized(key): return error("API key not found", 404)
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 50)), 1), 100)
        query = select(SecurityAuditLog).where(SecurityAuditLog.api_key_id == key.id).order_by(SecurityAuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        return jsonify({"page": page, "per_page": per_page, "events": [{"id": str(item.id), "event_type": item.event_type, "ip_address": item.ip_address, "request_id": item.request_id, "created_at": time_value(item.created_at)} for item in session.scalars(query).all()]}), 200
    except ValueError:
        return error("Invalid pagination", 400)
    finally: session.close()
