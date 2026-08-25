import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class RateLimit(Base):
    __tablename__ = "rate_limits"
    __table_args__ = (
        UniqueConstraint("api_id", "route_id", name="uq_rate_limits_api_route"),
        Index("uq_rate_limits_api_wide", "api_id", unique=True, postgresql_where=text("route_id IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("apis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("api_routes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    api: Mapped["API"] = relationship("API", back_populates="rate_limits")
    route: Mapped["APIRoute | None"] = relationship("APIRoute", back_populates="rate_limits")
    counters: Mapped[list["RateLimitCounter"]] = relationship(
        "RateLimitCounter", back_populates="rate_limit", cascade="all, delete-orphan", passive_deletes=True
    )
