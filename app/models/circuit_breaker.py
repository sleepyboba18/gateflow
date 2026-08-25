import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class CircuitBreaker(Base):
    __tablename__ = "circuit_breakers"
    __table_args__ = (
        UniqueConstraint("api_id", "route_id", name="uq_circuit_breakers_api_route"),
        Index("uq_circuit_breakers_api_wide", "api_id", unique=True, postgresql_where=text("route_id IS NULL")),
        Index("ix_circuit_breakers_active", "api_id", "route_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="CASCADE"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="closed", nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    recovery_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    half_open_max_requests: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
