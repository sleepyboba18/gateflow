import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.api_key import APIKey

KEY_PREFIX = "gf_live_"
RANDOM_PART_LENGTH = 32
IDENTIFIER_LENGTH = len(KEY_PREFIX) + 8


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
    return api_key, plaintext_key


def validate_api_key(session: Session, plaintext_key: str) -> APIKey | None:
    if not plaintext_key or not plaintext_key.startswith(KEY_PREFIX):
        return None
    key_prefix = plaintext_key[:IDENTIFIER_LENGTH]
    candidates = session.scalars(select(APIKey).where(APIKey.key_prefix == key_prefix)).all()
    now = datetime.now(timezone.utc)
    for api_key in candidates:
        if not api_key.is_active or api_key.revoked_at is not None:
            continue
        if api_key.expires_at is not None and api_key.expires_at <= now:
            continue
        if check_password_hash(api_key.key_hash, plaintext_key):
            api_key.last_used_at = now
            session.commit()
            return api_key
    return None


def list_user_api_keys(session: Session, user_id: UUID) -> list[APIKey]:
    return list(session.scalars(select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())))


def get_owned_api_key(session: Session, user_id: UUID, api_key_id: UUID) -> APIKey | None:
    return session.scalar(select(APIKey).where(APIKey.id == api_key_id, APIKey.user_id == user_id))


def revoke_api_key(session: Session, api_key: APIKey) -> None:
    api_key.is_active = False
    api_key.revoked_at = datetime.now(timezone.utc)
    session.commit()
