import secrets
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.api_key import APIKey
from app.models.api_key_ip_rule import APIKeyIPRule
from app.models.api_key_origin import APIKeyOrigin
from app.models.api_key_plan import APIKeyPlan
from app.models.api_key_scope import APIKeyScope
from app.services.security_audit_service import record_security_event
from app.sockets.events import emit_security_event
from config import Config

KEY_PREFIX = "gf_live_"
RANDOM_PART_LENGTH = 32
IDENTIFIER_LENGTH = len(KEY_PREFIX) + 8
MAX_GRACE_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class APIKeyAuthResult:
    api_key: APIKey | None
    reason: str = "invalid"


def hash_api_key(api_key: str) -> str:
    return generate_password_hash(api_key)


def generate_api_key(
    session: Session, user_id: UUID, name: str, expires_at: datetime | None = None
) -> tuple[APIKey, str]:
    plaintext_key = KEY_PREFIX + secrets.token_urlsafe(RANDOM_PART_LENGTH)
    api_key = APIKey(
        user_id=user_id,
        name=name,
        key_prefix=plaintext_key[:IDENTIFIER_LENGTH],
        key_hash=hash_api_key(plaintext_key),
        expires_at=expires_at,
    )
    session.add(api_key)
    record_security_event(session, "api_key_created", user_id=user_id, metadata={"key_prefix": api_key.key_prefix})
    return api_key, plaintext_key


def validate_api_key(session: Session, plaintext_key: str) -> APIKey | None:
    return authenticate_api_key(session, plaintext_key).api_key


def client_ip(request) -> str:
    direct = request.remote_addr or "0.0.0.0"
    try:
        source = ipaddress.ip_address(direct)
    except ValueError:
        return direct
    trusted = []
    for value in Config.TRUSTED_PROXY_CIDRS.split(","):
        try:
            if value.strip(): trusted.append(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            continue
    if any(source in network for network in trusted):
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        try:
            ipaddress.ip_address(forwarded)
            return forwarded
        except ValueError:
            pass
    return direct


def get_key_status(api_key: APIKey, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if api_key.revoked_at is not None or (api_key.rotation_grace_until is not None and api_key.rotation_grace_until <= now):
        return "revoked"
    if api_key.suspended_at is not None:
        return "suspended"
    if api_key.expires_at is not None and api_key.expires_at <= now:
        return "expired"
    if not api_key.is_active:
        return "revoked"
    return "active"


def _restriction_allowed(session: Session, api_key: APIKey, source_ip: str, origin: str | None) -> tuple[bool, str | None]:
    try: address = ipaddress.ip_address(source_ip)
    except ValueError: return False, "api_key_ip_denied"
    rules = session.scalars(select(APIKeyIPRule).where(APIKeyIPRule.api_key_id == api_key.id, APIKeyIPRule.is_active.is_(True))).all()
    networks = [(rule, ipaddress.ip_network(rule.cidr, strict=False)) for rule in rules]
    if any(rule.rule_type == "deny" and address in network for rule, network in networks): return False, "api_key_ip_denied"
    allows = [network for rule, network in networks if rule.rule_type == "allow"]
    if allows and not any(address in network for network in allows): return False, "api_key_ip_denied"
    origins = session.scalars(select(APIKeyOrigin).where(APIKeyOrigin.api_key_id == api_key.id, APIKeyOrigin.is_active.is_(True))).all()
    if origins and origin not in {item.origin for item in origins}: return False, "api_key_origin_denied"
    return True, None


def authenticate_api_key(session: Session, plaintext_key: str, *, source_ip: str | None = None, origin: str | None = None, request_id: str | None = None, user_agent: str | None = None) -> APIKeyAuthResult:
    if not plaintext_key or not plaintext_key.startswith(KEY_PREFIX):
        record_security_event(session, "api_key_authentication_failed", ip_address=source_ip, request_id=request_id, user_agent=user_agent, metadata={"key_prefix": plaintext_key[:IDENTIFIER_LENGTH] if plaintext_key else None})
        emit_security_event(event_type="api_key_authentication_failed", owner_id=None)
        return APIKeyAuthResult(None)
    key_prefix = plaintext_key[:IDENTIFIER_LENGTH]
    candidates = session.scalars(select(APIKey).where(APIKey.key_prefix == key_prefix)).all()
    now = datetime.now(timezone.utc)
    for api_key in candidates:
        if check_password_hash(api_key.key_hash, plaintext_key):
            status = get_key_status(api_key, now)
            if status != "active":
                record_security_event(session, "api_key_expired" if status == "expired" else "api_key_authentication_failed", api_key_id=api_key.id, user_id=api_key.user_id, request_id=request_id, metadata={"status": status})
                emit_security_event(event_type="api_key_expired" if status == "expired" else "api_key_authentication_failed", api_key_id=api_key.id, owner_id=api_key.user_id)
                return APIKeyAuthResult(None, status)
            allowed, restriction = _restriction_allowed(session, api_key, source_ip or "0.0.0.0", origin)
            if not allowed:
                record_security_event(session, restriction, api_key_id=api_key.id, user_id=api_key.user_id, ip_address=source_ip, request_id=request_id)
                emit_security_event(event_type=restriction or "api_key_restriction_denied", api_key_id=api_key.id, owner_id=api_key.user_id)
                return APIKeyAuthResult(None, restriction or "denied")
            api_key.last_used_at = now
            api_key.last_used_ip = source_ip
            session.commit()
            return APIKeyAuthResult(api_key, "ok")
    record_security_event(session, "api_key_authentication_failed", ip_address=source_ip, request_id=request_id, user_agent=user_agent, metadata={"key_prefix": key_prefix})
    emit_security_event(event_type="api_key_authentication_failed", owner_id=None)
    return APIKeyAuthResult(None)


def rotate_api_key(session: Session, old_key: APIKey, grace_seconds: int, expires_at: datetime | None = None) -> tuple[APIKey, str]:
    if grace_seconds < 0 or grace_seconds > MAX_GRACE_SECONDS: raise ValueError("Invalid grace period")
    now = datetime.now(timezone.utc)
    plaintext, new_key = KEY_PREFIX + secrets.token_urlsafe(RANDOM_PART_LENGTH), None
    new_key = APIKey(user_id=old_key.user_id, name=old_key.name, key_prefix=plaintext[:IDENTIFIER_LENGTH], key_hash=hash_api_key(plaintext), expires_at=expires_at if expires_at is not None else old_key.expires_at, rotation_parent_id=old_key.id)
    old_key.rotation_grace_until = now.replace(microsecond=0) + timedelta(seconds=grace_seconds)
    if grace_seconds == 0: old_key.is_active = False; old_key.revoked_at = now; old_key.revoked_reason = "rotated"
    for item in session.scalars(select(APIKeyScope).where(APIKeyScope.api_key_id == old_key.id)).all(): session.add(APIKeyScope(api_key=new_key, scope_id=item.scope_id))
    for item in session.scalars(select(APIKeyPlan).where(APIKeyPlan.api_key_id == old_key.id, APIKeyPlan.is_active.is_(True))).all(): session.add(APIKeyPlan(api_key=new_key, plan_id=item.plan_id, expires_at=item.expires_at))
    session.add(new_key); session.commit(); record_security_event(session, "api_key_rotated", user_id=old_key.user_id, api_key_id=old_key.id, metadata={"new_key_id": str(new_key.id)})
    emit_security_event(event_type="api_key_rotated", api_key_id=old_key.id, owner_id=old_key.user_id)
    return new_key, plaintext


def list_user_api_keys(session: Session, user_id: UUID) -> list[APIKey]:
    return list(session.scalars(select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())))


def get_owned_api_key(session: Session, user_id: UUID, api_key_id: UUID) -> APIKey | None:
    return session.scalar(select(APIKey).where(APIKey.id == api_key_id, APIKey.user_id == user_id))


def revoke_api_key(session: Session, api_key: APIKey) -> None:
    api_key.is_active = False
    api_key.revoked_at = datetime.now(timezone.utc)
    api_key.revoked_reason = api_key.revoked_reason or "revoked"
    session.commit()


def suspend_api_key(session: Session, api_key: APIKey, reason: str) -> None:
    api_key.suspended_at = datetime.now(timezone.utc)
    api_key.suspension_reason = reason
    session.commit()


def unsuspend_api_key(session: Session, api_key: APIKey) -> None:
    if api_key.revoked_at is not None:
        raise ValueError("Revoked keys cannot be unsuspended")
    api_key.suspended_at = None
    api_key.suspension_reason = None
    session.commit()
