import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import Config
from app.database.base import Base

logger = logging.getLogger("gateforge.database")

engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True) if Config.DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False) if engine else None


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope():
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def initialize_database() -> None:
    if engine is None:
        logger.warning("DATABASE_URL is not configured; database initialization skipped")
        return
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized")
    except Exception:
        logger.exception("Database initialization failed")


def check_database_connection() -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False
