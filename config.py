import os

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


class Config:
    APP_NAME = os.getenv("APP_NAME", "GateForge")
    APP_ENV = os.getenv("APP_ENV", "development")
    HOST = os.getenv("APP_HOST", "0.0.0.0")
    PORT = _as_int(os.getenv("APP_PORT"), 5000)
    DEBUG = _as_bool(os.getenv("APP_DEBUG"), APP_ENV == "development")

    DATABASE_URL = os.getenv("DATABASE_URL", "")

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change_this_secret")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_MINUTES = _as_int(os.getenv("JWT_EXPIRATION_MINUTES"), 60)

    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
    CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", CORS_ORIGINS)
    TRUSTED_PROXY_CIDRS = os.getenv("TRUSTED_PROXY_CIDRS", "")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_JSON = _as_bool(os.getenv("LOG_JSON"), True)
    ANALYTICS_MAX_DAYS = _as_int(os.getenv("ANALYTICS_MAX_DAYS"), 90)
    HEALTH_DB_TIMEOUT_SECONDS = _as_int(os.getenv("HEALTH_DB_TIMEOUT_SECONDS"), 2)
    TRAFFIC_LOG_RETENTION_DAYS = _as_int(os.getenv("TRAFFIC_LOG_RETENTION_DAYS"), 90)
    SECURITY_LOG_RETENTION_DAYS = _as_int(os.getenv("SECURITY_LOG_RETENTION_DAYS"), 365)
