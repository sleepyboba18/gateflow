from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def create_access_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=Config.JWT_EXPIRATION_MINUTES),
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            Config.JWT_SECRET_KEY,
            algorithms=[Config.JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
        UUID(str(payload["sub"]))
        return payload
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Invalid or expired token") from error
